"""Framework-independent API functions.

Both the FastAPI app (main.py) and the zero-install fallback server call these.

Version 3: Added invariants checking, expanded scenarios, event tracking.
"""
import json

from . import config, db, gateway, invariants, knowledge, orchestrator, tools
from .config import POLICIES, REGISTRY

SCENARIOS = [
    {"id": 1, "label": "1. Delegate a sale plan", "text": "Plan a festival sale for earbuds that does not lose money"},
    {"id": 2, "label": "2. Block a 40% discount", "text": "Plan a festival sale for earbuds with a 40% discount"},
    {"id": 3, "label": "3. Repeat request", "text": "Plan a festival sale for earbuds that does not lose money"},
    {"id": 4, "label": "4. Support opens payment DB", "text": "Verify the payment details of order O-1001"},
    {"id": 5, "label": "5. Rs 15,000 refund", "text": "Refund Rs 15,000 for order O-2002"},
]


def reset_all():
    db.reset()
    tools.load_company()
    return {"ok": True}


def get_state():
    """Get current system state including V3 additions."""
    approvals = db.query("SELECT * FROM approvals ORDER BY id DESC LIMIT 10")
    for a in approvals:
        a["args"] = json.loads(a["args"])
    
    counts = {r["type"]: r["n"] for r in db.query("SELECT type, COUNT(*) AS n FROM audit GROUP BY type")}
    blocks = db.query("SELECT COUNT(*) AS n FROM audit WHERE decision='block'")[0]["n"]
    
    # V3: Check invariants
    invariants_ok, violations = invariants.check_all_invariants()
    
    # V3: Verify audit chain
    chain_ok, chain_msg = db.verify_audit_chain()
    
    return {
        "mode": config.MODE,
        "tasks": db.query("SELECT * FROM tasks ORDER BY id DESC LIMIT 15"),
        "audit": db.query("SELECT * FROM audit ORDER BY id DESC LIMIT 80"),
        "approvals": approvals,
        "facts": knowledge.all_facts(),
        "agents": [{"id": k, **v, "facts": REGISTRY["fact_access"].get(k, [])}
                   for k, v in REGISTRY["agents"].items()],
        "rules": POLICIES["rules"],
        "scenarios": SCENARIOS,
        "counters": {
            "llm_calls": counts.get("llm_call", 0),
            "tool_calls": counts.get("tool_call", 0),
            "blocks": blocks
        },
        # V3 additions
        "invariants": {
            "passing": invariants_ok,
            "total": 13,
            "violations": violations
        },
        "audit_chain": {
            "valid": chain_ok,
            "message": chain_msg
        },
        "events": db.query("SELECT * FROM events ORDER BY id DESC LIMIT 50")
    }


def get_invariants_status():
    """Get detailed invariants check results."""
    invariants_ok, violations = invariants.check_all_invariants()
    summary = invariants.get_invariants_summary()
    
    return {
        "all_passed": invariants_ok,
        "total": 13,
        "passed": 13 - len(violations),
        "violations": violations,
        "invariants": summary
    }


def submit_request(text):
    return orchestrator.submit(text)


def run_scenario(n):
    for s in SCENARIOS:
        if s["id"] == n:
            return orchestrator.submit(s["text"])
    raise ValueError("Unknown scenario")


def decide(approval_id, approve, decided_by="human"):
    """V3: Enhanced with decided_by parameter for separation of duties."""
    return gateway.decide_approval(approval_id, bool(approve), decided_by)


def age_facts(hours=25):
    knowledge.age_all(hours)
    return {"ok": True, "aged_hours": hours}


def get_events(limit=50, event_type=None):
    """Get recent events from the event bus."""
    if event_type:
        events = db.query(
            "SELECT * FROM events WHERE event_type=? ORDER BY id DESC LIMIT ?",
            (event_type, limit)
        )
    else:
        events = db.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
    
    return {"events": events, "count": len(events)}


def reconcile_connector_actions():
    """Reconciliation job: check connector actions against company.json.
    
    Returns discrepancies between Manager records and systems of record.
    """
    discrepancies = []
    
    # Check refunds
    actions = db.query("SELECT * FROM connector_actions WHERE tool='issue_refund' AND status='executed'")
    for action in actions:
        args = json.loads(action["args"])
        order_id = args["order_id"]
        amount = args["amount"]
        
        # Check against company.json
        if order_id in tools.COMPANY["orders"]:
            actual_refunded = tools.COMPANY["orders"][order_id]["refunded"]
            # Sum all refunds for this order in our records
            our_total = sum(
                json.loads(a["args"])["amount"]
                for a in db.query(
                    "SELECT * FROM connector_actions WHERE tool='issue_refund' AND args LIKE ?",
                    (f'%{order_id}%',)
                ) if a["status"] == "executed"
            )
            
            if abs(actual_refunded - our_total) > 0.01:  # Allow for float precision
                discrepancies.append({
                    "type": "refund_mismatch",
                    "order_id": order_id,
                    "our_total": our_total,
                    "actual": actual_refunded,
                    "difference": actual_refunded - our_total
                })
    
    return {
        "ok": len(discrepancies) == 0,
        "discrepancies": discrepancies,
        "checked": len(actions)
    }
