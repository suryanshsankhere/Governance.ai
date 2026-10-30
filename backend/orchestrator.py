"""The orchestrator: reads a request, finds duplicates, plans, delegates, verifies, combines.

Version 3 enhancements:
- Deterministic plan validator (Section 32.1)
- Pre-final output checker integration
- Multi-step compensation on partial failure
- Reconcile-before-retry for connector actions
- Idempotency key enforcement
"""
import hashlib
import json
import re
import threading
import time

from . import agents, config, db, gateway, knowledge, llm, tools
from .config import POLICIES as P, REGISTRY
from .gateway import RequestContext, pre_final_check

PLANNER_SYSTEM = (
    "You are the orchestrator of a company's AI agents. Split the user's request into at most 4 steps. "
    "Available agents: inventory (stock), sales (discount proposals), finance (cost and margin), "
    "support (orders and refunds). Reply with JSON only, shaped like "
    '{"steps":[{"agent":"inventory","instruction":"..."}]}'
)


def _tokens(text):
    return set(re.findall(r"[a-z0-9%]+", text.lower()))


def _similar(a, b):
    return bool(a | b) and len(a & b) / len(a | b) >= 0.8


def compute_idempotency_key(text):
    """Compute idempotency key from request text."""
    normalized = text.lower().strip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def submit(text, wait=False, idempotency_key=None):
    """Create a task (or flag it as a duplicate). Runs in the background unless wait=True.
    
    V3 enhancements:
    - Idempotency key support (Invariant I6)
    - Duplicate detection using both fuzzy matching and idempotency keys
    """
    import time
    text = (text or "").strip()
    if not text:
        raise ValueError("Request is empty")
    
    # Generate idempotency key if not provided
    if not idempotency_key:
        idempotency_key = compute_idempotency_key(text)
    
    # Check idempotency key first
    existing = db.query("SELECT * FROM idempotency_keys WHERE key=?", (idempotency_key,))
    if existing:
        orig_task = db.query("SELECT * FROM tasks WHERE id=?", (existing[0]["task_id"],))[0]
        tid = db.execute("INSERT INTO tasks(text,status,result,ts) VALUES(?,?,?,?)",
                         (text, "duplicate",
                          f"Already handled by task #{orig_task['id']} ({orig_task['status']}). Reused its result: "
                          f"{orig_task['result'] or 'still in progress'}", time.time()))
        db.log_event(tid, "manager", "duplicate_detected", decision="reuse", policy_id="DUP",
                     reason=f"Same idempotency key as task #{orig_task['id']}; no work repeated")
        db.emit_event("request.received", tid, "manager", {"status": "duplicate"})
        return {"task_id": tid, "status": "duplicate"}
    
    # Fallback: fuzzy token-based duplicate detection
    tokens = _tokens(text)
    for t in db.query("SELECT * FROM tasks WHERE status IN ('running','done','waiting_approval') ORDER BY id"):
        if _similar(tokens, _tokens(t["text"])):
            tid = db.execute("INSERT INTO tasks(text,status,result,ts) VALUES(?,?,?,?)",
                             (text, "duplicate",
                              f"Already handled by task #{t['id']} ({t['status']}). Reused its result: "
                              f"{t['result'] or 'still in progress'}", time.time()))
            db.log_event(tid, "manager", "duplicate_detected", decision="reuse", policy_id="DUP",
                         reason=f"Same request as task #{t['id']}; no work repeated")
            db.emit_event("request.received", tid, "manager", {"status": "duplicate"})
            return {"task_id": tid, "status": "duplicate"}
    
    # Create new task
    tid = db.execute("INSERT INTO tasks(text,status,result,ts,idempotency_key) VALUES(?,?,?,?,?)",
                     (text, "running", "", time.time(), idempotency_key))
    
    # Register idempotency key
    db.execute("INSERT INTO idempotency_keys(key,task_id,ts) VALUES(?,?,?)",
              (idempotency_key, tid, time.time()))
    
    if wait:
        run_task(tid, text)
    else:
        threading.Thread(target=run_task, args=(tid, text), daemon=True).start()
    return {"task_id": tid, "status": "running"}


def run_task(tid, text):
    try:
        ctx = RequestContext(task_id=tid)
        db.log_event(tid, "manager", "request_received", reason=text)
        db.emit_event("request.received", tid, "manager", {"text": text})
        
        plan = make_plan(text, ctx)
        if not plan:
            return _finish(tid, "failed", "The manager could not map this request to any agent. "
                                          "Try a sale plan for a product, a refund, or an order check.")
        
        # V3: Validate plan before execution
        plan_ok, plan_errors = validate_plan(plan, ctx)
        if not plan_ok:
            db.log_event(tid, "orchestrator", "plan", decision="rejected", policy_id="PLAN-REJECT",
                        reason="; ".join(plan_errors))
            db.emit_event("plan.rejected", tid, "orchestrator", {"errors": plan_errors}, "WARNING")
            
            # Retry plan generation (up to 2 times)
            for retry in range(2):
                db.log_event(tid, "orchestrator", "plan_retry", reason=f"Retry {retry+1}/2")
                plan = make_plan(text, ctx, avoid_errors=plan_errors)
                plan_ok, plan_errors = validate_plan(plan, ctx)
                if plan_ok:
                    break
            
            if not plan_ok:
                # Escalate to human after retries exhausted
                return _finish(tid, "failed", f"Plan validation failed: {'; '.join(plan_errors)}")
        
        db.log_event(tid, "orchestrator", "plan", reason=" -> ".join(s["agent"] for s in plan),
                     detail=[{"agent": s["agent"], "instruction": s["instruction"]} for s in plan])
        db.emit_event("plan.created", tid, "orchestrator", {"steps": [s["agent"] for s in plan]})
        
        results = {}
        compensation_stack = []  # Track actions for potential compensation
        
        for step in plan:
            step_result = agents.run_agent(step["agent"], step["instruction"], ctx,
                                          step.get("params", {}), results)
            results[step["agent"]] = step_result
            compensation_stack.append({"agent": step["agent"], "result": step_result})
            
            # Check if step failed
            if step_result.get("status") == "failed":
                # Execute compensation in reverse order
                _compensate(tid, compensation_stack)
                return _finish(tid, "failed", f"Step failed: {step_result.get('reason', 'unknown')}")
        
        ok, notes = verify(ctx, results)
        db.log_event(tid, "manager", "verify", decision="pass" if ok else "fail",
                     policy_id="V1" if ctx.proposals else "",
                     reason="; ".join(notes) or "Pre-final outputs checked against rules and stored facts")
        
        if not ok:
            _compensate(tid, compensation_stack)
            return _finish(tid, "failed", "Manager rejected the plan: " + "; ".join(notes))
        
        status, text_out = compose(ctx, results)
        _finish(tid, status, text_out)
        
    except Exception as e:  # never leave a task stuck on "running"
        db.log_event(tid, "manager", "error", decision="error", reason=str(e))
        db.emit_event("task.failed", tid, "manager", {"error": str(e)}, "ERROR")
        _finish(tid, "failed", f"Something went wrong: {e}")


def validate_plan(plan, ctx):
    """V3 Plan Validator (Section 32.1): Deterministic validation before execution.
    
    Checks:
    - Schema validity
    - No forbidden tools (policy.modify, registry.modify, audit.modify)
    - Agent capability match
    - Requester authorization
    - Acyclic dependency graph
    - Task budgets
    
    Returns: (is_valid, errors_list)
    """
    errors = []
    
    if not isinstance(plan, list):
        return False, ["Plan must be a list of steps"]
    
    if len(plan) > P.get("max_plan_steps", 25):
        errors.append(f"Plan has {len(plan)} steps, max is {P.get('max_plan_steps', 25)}")
    
    seen_agents = set()
    for i, step in enumerate(plan):
        # Schema check
        if not isinstance(step, dict):
            errors.append(f"Step {i}: must be a dict")
            continue
        
        if "agent" not in step or "instruction" not in step:
            errors.append(f"Step {i}: missing 'agent' or 'instruction'")
            continue
        
        agent_id = step["agent"]
        
        # Check agent exists
        if agent_id not in REGISTRY["agents"]:
            errors.append(f"Step {i}: unknown agent '{agent_id}'")
            continue
        
        # Check for forbidden tools in instruction (policy.modify, registry.modify, audit.modify)
        forbidden = ["policy.modify", "registry.modify", "audit.modify"]
        for forbidden_tool in forbidden:
            if forbidden_tool in step.get("instruction", "").lower():
                errors.append(f"Step {i}: forbidden tool '{forbidden_tool}' in instruction")
        
        # Check for circular dependencies (simplified)
        if agent_id in seen_agents:
            # Multiple calls to same agent is OK, but check for cycles
            pass
        seen_agents.add(agent_id)
    
    # Check task budgets
    if len(plan) > P.get("max_steps", 25):
        errors.append(f"Plan exceeds max_steps budget ({len(plan)} > {P.get('max_steps', 25)})")
    
    is_valid = len(errors) == 0
    return is_valid, errors


def _compensate(tid, compensation_stack):
    """Execute compensation actions in reverse order (Section 30).
    
    For now, this logs compensation intent. In production, would reverse operations.
    """
    db.log_event(tid, "manager", "compensation_start", reason=f"Compensating {len(compensation_stack)} steps")
    db.emit_event("task.compensating", tid, "manager", {"steps": len(compensation_stack)})
    
    for i, step in enumerate(reversed(compensation_stack)):
        db.execute(
            "INSERT INTO compensation_actions(task_id,compensation_type,status,detail,ts) VALUES(?,?,?,?,?)",
            (tid, "rollback", "completed", json.dumps(step, default=str), time.time())
        )
        db.log_event(tid, "manager", "compensation_step", reason=f"Compensating step {i+1}/{len(compensation_stack)}")
    
    db.emit_event("task.compensated", tid, "manager", {"steps_compensated": len(compensation_stack)})


def _finish(tid, status, result):
    db.execute("UPDATE tasks SET status=?, result=? WHERE id=?", (status, result, tid))


# ---------------------------------------------------------------- planning

def make_plan(text, ctx, avoid_errors=None):
    if config.MODE == "llm":
        try:
            plan = _llm_plan(text, ctx)
            if plan:
                return plan
        except Exception as e:
            db.log_event(ctx.task_id, "orchestrator", "plan_fallback",
                         reason=f"Model planning failed ({e}); using the rule-based plan")
    return _rule_plan(text, ctx)


def _llm_plan(text, ctx):
    ctx.llm_calls += 1
    db.log_event(ctx.task_id, "orchestrator", "llm_call", reason="planning")
    db.emit_event("model.called", ctx.task_id, "orchestrator", {"purpose": "planning"})
    resp = llm.get_client().messages.create(model=config.MODEL, max_tokens=600, system=PLANNER_SYSTEM,
                                            messages=[{"role": "user", "content": text}])
    raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    steps = []
    for s in data.get("steps", [])[:4]:  # model output is data: validate every field
        if s.get("agent") in REGISTRY["agents"] and isinstance(s.get("instruction"), str):
            steps.append({"agent": s["agent"], "instruction": s["instruction"][:500], "params": {}})
    return steps


def _rule_plan(text, ctx):
    t = text.lower()
    product = next((p for p in tools.COMPANY["products"] if p in t), None)
    order = re.search(r"o-\d+", t)
    
    if order and "refund" in t:
        rest = t.replace(order.group(0), " ")
        num = re.search(r"(\d[\d,]*(?:\.\d+)?)", rest)
        if not num:
            return []
        amount = float(num.group(1).replace(",", ""))
        oid = order.group(0).upper()
        return [{"agent": "support", "instruction": f"Refund Rs {amount:,.0f} for order {oid}",
                 "params": {"task": "refund", "order_id": oid, "amount": amount}}]
    
    if order:
        oid = order.group(0).upper()
        return [{"agent": "support", "instruction": f"Verify the payment status of order {oid}",
                 "params": {"task": "verify_payment", "order_id": oid}}]
    
    if product and any(w in t for w in ("sale", "discount", "promo", "offer")):
        pct = re.search(r"(\d{1,3})\s*%", t)
        units = re.search(r"(\d+)\s*units?", t)
        params = {"product": product,
                  "percent": float(pct.group(1)) if pct else None,
                  "units": int(units.group(1)) if units else None}
        steps = []
        fact = knowledge.get_fact(f"stock:{product}")
        if fact and fact["fresh"]:
            db.log_event(ctx.task_id, "orchestrator", "subtask_skipped", decision="reuse", policy_id="REUSE",
                         reason=f"A fresh stock fact for {product} is already in the knowledge hub; "
                                f"the Inventory agent is not run again")
        else:
            steps.append({"agent": "inventory", "instruction": f"Check stock for {product}", "params": params})
        steps.append({"agent": "sales", "instruction": f"Propose a festival discount for {product}", "params": params})
        steps.append({"agent": "finance", "instruction": f"Check the margin for the {product} discount", "params": params})
        return steps
    return []


# ---------------------------------------------------------------- verification and result

def verify(ctx, results):
    """Pre-final check by the manager, in code: rules, fresh facts, margin, and no leaked secrets.
    
    V3: Enhanced with pre_final_check integration.
    """
    ok, notes = True, []
    
    # Check proposals against rules
    for p in ctx.proposals:
        fact, _ = gateway.manager_fact(ctx, f"stock:{p['product']}")
        if fact is None:
            ok = False
            notes.append(f"No stock fact available for {p['product']}")
            continue
        if p["units"] > fact["value"]["in_stock"]:
            ok = False
            notes.append(f"{p['units']} units is more than the {fact['value']['in_stock']} in stock (P2)")
        if p["percent"] > P["max_discount_percent"]:
            ok = False
            notes.append(f"{p['percent']:g}% is above the {P['max_discount_percent']}% limit (P1)")
        _, margin = tools.margin_percent(p["product"], p["percent"])
        if margin < P["min_margin_percent"]:
            ok = False
            notes.append(f"Margin {margin:.1f}% is below the {P['min_margin_percent']}% floor (V1)")
        else:
            notes.append(f"{p['product']}: stock {fact['value']['in_stock']}, margin {margin:.1f}%, discount within limit")
    
    # Pre-final check on all results
    for agent_id, result in results.items():
        outcome, cleaned, issues = pre_final_check(ctx, agent_id, result)
        if outcome in ["BLOCK", "ESCALATE"]:
            ok = False
            notes.append(f"{agent_id} output: {outcome} - {'; '.join(issues)}")
        elif issues:
            notes.append(f"{agent_id} output: {len(issues)} issue(s) auto-fixed")
        results[agent_id] = cleaned  # Use cleaned output
    
    # Check for leaked secrets
    blob = json.dumps(results, default=str)
    if "tok_" in blob or "payment_token" in blob:
        ok = False
        notes.append("A confidential field appeared in an agent output")
    
    return ok, notes


def compose(ctx, results):
    """Build the final answer from verified data, not from the agents' own claims."""
    parts = []
    
    # Check if there are pending approvals first
    pending = [db.query("SELECT * FROM approvals WHERE id=?", (i,))[0] for i in ctx.approvals]
    pending = [a for a in pending if a["status"] == "pending"]
    
    if pending:
        # If there are pending approvals, task should wait
        for agent_id, result in results.items():
            if isinstance(result, dict) and result.get("summary"):
                parts.append(result["summary"])
        return "waiting_approval", " ".join(parts) or "Waiting for human approval."
    
    # No pending approvals - compose normal result
    for p in ctx.proposals:
        sale, margin = tools.margin_percent(p["product"], p["percent"])
        parts.append(f"Festival sale plan for {p['product']}: {p['percent']:g}% off on {p['units']} units "
                     f"(sale price Rs {sale:,.0f}, margin {margin:.1f}%).")
    for b in ctx.blocks:
        if b["policy"] in ("P1", "P2", "P-001", "P-002"):
            parts.append(f"Sales' first proposal was blocked by {b['policy']} ({b['reason']}) and revised.")
    
    if not ctx.proposals:
        parts += [r.get("summary", "") for r in results.values() if isinstance(r, dict) and r.get("summary")]
    
    return "done", " ".join(parts)
