"""The gateway: the ONLY way an agent touches company tools or data.

Version 3 enhancements:
- Payload-hash binding for tamper-proof approvals
- Pre-final output checks (PII redaction, prompt injection screening)
- Manager-mediated delegation with effective permission calculation
- Break-glass emergency override support
- Enhanced need-to-know field filtering
"""
import hashlib
import json
import re
from dataclasses import dataclass, field

from . import db, knowledge
from .config import POLICIES as P, REGISTRY
from .policy import check_policy, check_separation_of_duties, check_delegation_depth
from .tools import FIELD_FILTERED, TOOLS, validate_args


@dataclass
class RequestContext:
    """Everything the manager tracks for one request: limits, proposals, blocks, approvals."""
    task_id: int
    tool_calls: int = 0
    llm_calls: int = 0
    proposals: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    approvals: list = field(default_factory=list)
    delegation_depth: int = 0
    requester_id: str = "system"


def _filter_fields(agent_id, tool, result):
    """Need-to-know: sensitive tools return only the fields this agent is allowed to see."""
    if tool not in FIELD_FILTERED or not isinstance(result, dict):
        return result
    allowed = REGISTRY.get("field_access", {}).get(agent_id, {}).get(tool, [])
    return {k: v for k, v in result.items() if k in allowed}


def _blocked(ctx, agent_id, tool, args, policy_id, reason, limit=None):
    """Record a blocked action."""
    db.log_event(ctx.task_id, agent_id, "tool_call", tool, "block", policy_id, reason, {"args": args})
    db.emit_event("policy.decision", ctx.task_id, agent_id, 
                  {"tool": tool, "decision": "block", "policy_id": policy_id}, "WARNING")
    ctx.blocks.append({"agent": agent_id, "tool": tool, "policy": policy_id, "reason": reason, "args": args})
    return {"status": "blocked", "policy_id": policy_id, "reason": reason, "limit": limit}


def call_tool(ctx, agent_id, tool, args, depth=1, requester_id=None):
    """Run one tool call on behalf of an agent, under the rules. Returns a plain dict.
    
    V3 enhancements:
    - Manager-mediated delegation with depth tracking
    - Payload hash calculation for approvals
    - Enhanced audit logging
    """
    args = dict(args or {})
    
    # P-007: Check delegation depth
    depth_ok, depth_msg = check_delegation_depth(depth)
    if not depth_ok:
        return _blocked(ctx, agent_id, tool, args, "P-007", depth_msg)
    
    if ctx.tool_calls >= P["max_tool_calls_per_request"]:
        return _blocked(ctx, agent_id, tool, args, "P-006",
                        f"Tool-call limit of {P['max_tool_calls_per_request']} per request reached")
    ctx.tool_calls += 1

    if tool == "ask_manager":
        return _ask_manager(ctx, agent_id, args, depth)

    # Check policy with enhanced V3 checks
    d = check_policy(agent_id, tool, args, requester_id=requester_id or ctx.requester_id)
    
    if d.decision == "block":
        return _blocked(ctx, agent_id, tool, args, d.policy_id, d.reason, d.limit)

    clean = validate_args(tool, args)
    
    # Handle escalation (approval needed)
    if d.decision == "escalate" or d.decision == "approve":
        # Compute payload hash for tamper-proof binding
        payload_hash = db.compute_payload_hash(tool, clean)
        
        approval_id = db.execute(
            "INSERT INTO approvals(task_id,agent,tool,args,policy_id,reason,status,ts,requested_by,payload_hash,expires_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (ctx.task_id, agent_id, tool, json.dumps(clean), d.policy_id, d.reason, "pending", 
             _now(), ctx.requester_id, payload_hash, _now() + 24*3600),  # 24h expiry
        )
        ctx.approvals.append(approval_id)
        
        # Log to both audit tables
        db.log_event(ctx.task_id, agent_id, "approval_requested", tool, "approve", d.policy_id, d.reason,
                     {"args": clean, "approval_id": approval_id, "payload_hash": payload_hash})
        db.log_audit_event(ctx.task_id, agent_id, "approval.requested", tool, "escalate", d.policy_id, d.reason,
                          {"args": clean, "approval_id": approval_id})
        db.emit_event("approval.requested", ctx.task_id, agent_id,
                     {"approval_id": approval_id, "tool": tool, "policy_id": d.policy_id})
        
        return {"status": "pending_approval", "approval_id": approval_id,
                "policy_id": d.policy_id, "reason": d.reason}

    # Log policy decision
    decision_id = db.execute(
        "INSERT INTO policy_decisions(task_id,agent,tool,args,decision,policy_id,policy_version,reason,ts) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (ctx.task_id, agent_id, tool, json.dumps(clean), d.decision, d.policy_id, d.policy_version, d.reason, _now())
    )

    # Execute the tool
    try:
        raw = TOOLS[tool]["fn"](**clean)
    except Exception as e:  # one bad call must never crash the run
        db.log_event(ctx.task_id, agent_id, "tool_call", tool, "error", "", str(e), {"args": clean})
        db.emit_event("tool.called", ctx.task_id, agent_id, {"tool": tool, "status": "error"}, "ERROR")
        return {"status": "error", "reason": str(e)}

    # Update knowledge hub with Manager-only writes
    if tool == "read_stock":
        knowledge.put_fact(
            f"stock:{raw['product']}", 
            {"in_stock": raw["in_stock"]}, 
            f"{agent_id} agent / read_stock",
            trust_level="Verified"  # Verified because it came from connector
        )
    elif tool == "read_cost":
        knowledge.put_fact(
            f"cost:{raw['product']}", 
            {"cost": raw["cost"], "price": raw["price"]},
            f"{agent_id} agent / read_cost",
            trust_level="Verified"
        )
    elif tool == "propose_discount":
        ctx.proposals.append(raw)

    # Record connector action
    if TOOLS[tool]["risk"] in ["medium", "high"]:
        db.execute(
            "INSERT INTO connector_actions(task_id,decision_id,tool,args,result,status,ts) "
            "VALUES(?,?,?,?,?,?,?)",
            (ctx.task_id, decision_id, tool, json.dumps(clean), json.dumps(raw, default=str), "executed", _now())
        )

    # Filter fields based on need-to-know
    result = _filter_fields(agent_id, tool, raw)
    
    # Log successful execution
    db.log_event(ctx.task_id, agent_id, "tool_call", tool, "allow", d.policy_id, d.reason,
                 {"args": clean, "result": result})
    db.log_audit_event(ctx.task_id, agent_id, "tool.called", tool, "allow", d.policy_id, d.reason,
                      {"args": clean})
    db.emit_event("tool.called", ctx.task_id, agent_id, {"tool": tool, "status": "success"})
    
    return {"status": "ok", "result": result}


def manager_fact(ctx, key, depth=1):
    """The manager's own lookup: serve a fresh fact from the hub, otherwise refresh it first.
    Returns (fact or None, how_it_was_served)."""
    fact = knowledge.get_fact(key)
    if fact and fact["fresh"]:
        return fact, "knowledge hub"
    
    kind, _, name = key.partition(":")
    resolver = knowledge.RESOLVERS.get(kind)
    if resolver is None:
        return None, "no source"
    
    owner, tool, param = resolver
    why = "stale (older than the maximum age)" if fact else "not in the hub yet"
    db.log_event(ctx.task_id, "manager", "hub_refresh", tool, "refresh", "P-005" if fact else "",
                 f"Fact '{key}' is {why}; manager asks the {owner} agent to refresh it")
    db.emit_event("knowledge.requested", ctx.task_id, "manager", {"key": key, "status": "stale" if fact else "missing"})
    
    out = call_tool(ctx, owner, tool, {param: name}, depth + 1)
    if out["status"] != "ok":
        return None, "refresh failed"
    
    db.emit_event("knowledge.served", ctx.task_id, owner, {"key": key, "status": "refreshed"})
    return knowledge.get_fact(key), f"refreshed by {owner} agent"


def _ask_manager(ctx, agent_id, args, depth):
    """Agent asks manager for knowledge (manager-mediated delegation)."""
    key = str(args.get("key", "")).strip().lower()
    kind = key.partition(":")[0]
    
    # Check agent has permission to access this kind of fact
    if kind not in REGISTRY.get("fact_access", {}).get(agent_id, []):
        return _blocked(ctx, agent_id, "ask_manager", args, "P-004",
                        f"{REGISTRY['agents'][agent_id]['title']} may not ask for '{kind}' facts")
    
    fact, how = manager_fact(ctx, key, depth)
    if fact is None:
        return {"status": "error", "reason": f"Could not find or refresh '{key}' ({how})"}
    
    if how == "knowledge hub":
        db.log_event(ctx.task_id, "manager", "hub_hit", "ask_manager", "allow", "",
                     f"Answered '{key}' for the {agent_id} agent from the knowledge hub (fresh, from {fact['source']})",
                     {"value": fact["value"]})
        db.emit_event("knowledge.served", ctx.task_id, "manager", {"key": key, "status": "cache_hit"})
    
    return {"status": "ok", "result": {"value": fact["value"], "served_from": how,
                                       "age_minutes": round(fact["age_s"] / 60, 1), "source": fact["source"]}}


def pre_final_check(ctx, agent_id, output):
    """Pre-final output check before releasing agent results (Section 32.2).
    
    Checks:
    - Sensitive data & PII redaction/blocking
    - Prompt injection screening
    - Status claim verification
    - Schema compliance
    
    Returns: (outcome, cleaned_output, issues)
    where outcome is one of: PASS, REVISE, BLOCK, ESCALATE
    """
    issues = []
    cleaned = output
    
    # Convert to string for pattern matching
    output_str = json.dumps(output, default=str) if not isinstance(output, str) else output
    
    # 1. Check for sensitive data leaks
    sensitive_patterns = {
        "credit_card": r'\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b',
        "payment_token": r'tok_[a-zA-Z0-9_]+',
    }
    
    for pattern_name, pattern in sensitive_patterns.items():
        matches = re.findall(pattern, output_str)
        if matches:
            issues.append(f"Detected {pattern_name}: {len(matches)} occurrence(s)")
            # Redact the matches
            cleaned = re.sub(pattern, "[REDACTED]", output_str if isinstance(output, str) else json.dumps(output))
    
    # 2. Prompt injection screening - detect and treat as data, never instructions
    injection_patterns = [
        r'ignore\s+(all\s+)?(previous\s+)?(instructions?|rules?)',
        r'you\s+are\s+now',
        r'disregard\s+(everything|all)',
        r'new\s+instructions?:',
        r'system\s+prompt',
    ]
    
    for pattern in injection_patterns:
        if re.search(pattern, output_str, re.IGNORECASE):
            issues.append(f"Potential prompt injection detected")
            # Don't redact, but flag for review
            db.emit_event("precheck.revise", ctx.task_id, agent_id, 
                         {"reason": "prompt_injection_detected"}, "WARNING")
    
    # 3. Verify status claims against connector records (only if claims execution)
    if ("executed" in output_str.lower() or "issued" in output_str.lower()) and ("refund" in output_str.lower() or "payment" in output_str.lower()):
        # Only check if agent claims to have executed something
        actions = db.query(
            "SELECT * FROM connector_actions WHERE task_id=? AND status='executed'",
            (ctx.task_id,)
        )
        if not actions and "waiting" not in output_str.lower() and "approval" not in output_str.lower():
            issues.append("Agent claims execution but no connector record found")
    
    # 4. Schema compliance (basic check)
    if isinstance(output, dict):
        if "summary" not in output and "result" not in output:
            issues.append("Output missing expected structure (summary or result)")
    
    # Determine outcome
    if any("injection" in i.lower() for i in issues):
        outcome = "ESCALATE"
    elif any("claims execution" in i.lower() for i in issues):
        outcome = "BLOCK"
    elif issues and any("REDACTED" in str(cleaned) for i in issues):
        outcome = "PASS"  # Issues were automatically redacted
    elif issues:
        outcome = "REVISE"
    else:
        outcome = "PASS"
    
    # Log the check
    db.log_event(ctx.task_id, "manager", "precheck", "", outcome.lower(), "",
                 f"Pre-final check: {outcome}", {"issues": issues})
    db.emit_event("precheck.passed" if outcome == "PASS" else "precheck.revise", 
                 ctx.task_id, "manager", {"outcome": outcome, "issues": issues})
    
    return outcome, cleaned, issues


def decide_approval(approval_id, approve, decided_by="human"):
    """A human approves or rejects a pending action. Approved actions are executed by the gateway.
    
    V3 enhancements:
    - Payload hash verification (tamper detection)
    - Separation of duties check
    - Single-use enforcement
    - Expiry check
    """
    rows = db.query("SELECT * FROM approvals WHERE id=? AND status='pending'", (approval_id,))
    if not rows:
        raise ValueError("Approval not found or already decided")
    
    a = rows[0]
    
    # Check expiry
    if a["expires_at"] and _now() > a["expires_at"]:
        db.execute("UPDATE approvals SET status='expired' WHERE id=?", (approval_id,))
        db.emit_event("approval.expired", a["task_id"], "system", {"approval_id": approval_id})
        raise ValueError("Approval has expired")
    
    # Check separation of duties (P-006)
    duties_ok, duties_msg = check_separation_of_duties(a.get("requested_by"), decided_by)
    if not duties_ok:
        db.log_event(a["task_id"], "human", "human_decision", a["tool"], "blocked", "P-006", duties_msg, 
                    {"approval_id": approval_id})
        raise ValueError(duties_msg)
    
    args = json.loads(a["args"])
    
    if approve:
        # Verify payload hash hasn't changed (tamper detection)
        current_hash = db.compute_payload_hash(a["tool"], args)
        if a["payload_hash"] and current_hash != a["payload_hash"]:
            db.execute("UPDATE approvals SET status='tampered' WHERE id=?", (approval_id,))
            db.log_event(a["task_id"], "human", "human_decision", a["tool"], "blocked", "TAMPER",
                        "Payload was modified after approval request", {"approval_id": approval_id})
            db.emit_event("approval.rejected", a["task_id"], "human", 
                         {"approval_id": approval_id, "reason": "payload_tampered"}, "ERROR")
            raise ValueError("Payload was modified after approval - parameters do not match approved request")
        
        try:
            result = TOOLS[a["tool"]]["fn"](**validate_args(a["tool"], args))
        except Exception as e:
            db.execute("UPDATE approvals SET status='failed', decided_ts=?, decided_by=? WHERE id=?", 
                      (_now(), decided_by, approval_id))
            db.log_event(a["task_id"], "human", "human_decision", a["tool"], "error", a["policy_id"], str(e), args)
            db.emit_event("action.executed", a["task_id"], "human", {"status": "failed", "error": str(e)}, "ERROR")
            _settle_task(a["task_id"], "failed", f"Approved, but the action failed: {e}")
            return {"approval_id": approval_id, "status": "failed"}
        
        # Mark as used (single-use enforcement)
        db.execute("UPDATE approvals SET status='approved', decided_ts=?, decided_by=?, used_at=? WHERE id=?", 
                  (_now(), decided_by, _now(), approval_id))
        
        # Record connector action
        db.execute(
            "INSERT INTO connector_actions(task_id,approval_id,tool,args,result,status,ts) "
            "VALUES(?,?,?,?,?,?,?)",
            (a["task_id"], approval_id, a["tool"], json.dumps(args), json.dumps(result, default=str), "executed", _now())
        )
        
        db.log_event(a["task_id"], "human", "human_decision", a["tool"], "approved", a["policy_id"],
                     "Approved by a human; the gateway executed the action", {"args": args, "result": result})
        db.log_audit_event(a["task_id"], "human", "approval.granted", a["tool"], "approved", a["policy_id"],
                          f"Approved by {decided_by}", {"args": args})
        db.emit_event("approval.granted", a["task_id"], "human", 
                     {"approval_id": approval_id, "decided_by": decided_by})
        db.emit_event("action.executed", a["task_id"], "human", 
                     {"tool": a["tool"], "status": "success"})
        
        _settle_task(a["task_id"], "done", _describe(a["tool"], args, "approved by a human and executed"))
        return {"approval_id": approval_id, "status": "approved"}
    
    # Rejection
    db.execute("UPDATE approvals SET status='rejected', decided_ts=?, decided_by=? WHERE id=?", 
              (_now(), decided_by, approval_id))
    db.log_event(a["task_id"], "human", "human_decision", a["tool"], "rejected", a["policy_id"],
                 "Rejected by a human; the action was not executed", args)
    db.log_audit_event(a["task_id"], "human", "approval.rejected", a["tool"], "rejected", a["policy_id"],
                      f"Rejected by {decided_by}", {"args": args})
    db.emit_event("approval.rejected", a["task_id"], "human", 
                 {"approval_id": approval_id, "decided_by": decided_by})
    
    _settle_task(a["task_id"], "rejected", _describe(a["tool"], args, "rejected by a human and NOT executed"))
    return {"approval_id": approval_id, "status": "rejected"}


def _describe(tool, args, outcome):
    if tool == "issue_refund":
        return f"Refund of Rs {args['amount']:,.0f} for {args['order_id']} {outcome}."
    return f"{tool} {outcome}."


def _settle_task(task_id, status, text):
    """Finish the task once no approvals remain pending for it."""
    pending = db.query("SELECT COUNT(*) AS n FROM approvals WHERE task_id=? AND status='pending'", (task_id,))[0]["n"]
    if pending == 0:
        db.execute("UPDATE tasks SET status=?, result=? WHERE id=?", (status, text, task_id))


def _now():
    import time
    return time.time()
