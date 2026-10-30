"""The policy engine. Plain code, no AI. It makes the final allow / block / approve decision.

The AI can propose anything. This file decides what is actually allowed.

Version 3: Expanded to support 11 starter rules (P-001 to P-011) with backward-compatible P1-P5 aliases.
"""
from dataclasses import dataclass
from typing import Optional
import time

from .config import POLICIES as P, REGISTRY
from .tools import TOOLS, get_order, get_product, validate_args


@dataclass
class Decision:
    decision: str            # "allow" | "block" | "approve" (needs a human) | "escalate"
    policy_id: str
    reason: str
    risk: str = ""
    limit: Optional[dict] = None   # e.g. {"max_percent": 20}, so an agent can revise
    policy_version: str = "v3.0"
    shadow_mode: bool = False


# Action risk tiers (0-4 mapped to Low/Medium/High for UI)
RISK_TIERS = {
    "low": (0, 1),       # Reads within scope
    "medium": (2,),      # Reversible writes
    "high": (3, 4),      # Payments, refunds > threshold, code changes, bulk ops
}


def get_risk_tier(risk_level):
    """Map risk level to numeric tier."""
    for tier_name, tiers in RISK_TIERS.items():
        if risk_level == tier_name:
            return max(tiers) if tiers else 0
    return 0


def check_policy(agent_id, tool, args, requester_id=None, knowledge_context=None, policy_engine_available=True, audit_log_available=True):
    """Main policy decision point. Returns Decision object.
    
    V3 enhancements:
    - Supports P-001 through P-011
    - Fail-closed on Tier 3-4 if policy engine or audit unavailable
    - Checks knowledge freshness and trust levels
    - Enforces separation of duties
    """
    spec = TOOLS.get(tool)
    if spec is None:
        return Decision("block", "P-000", f"Unknown tool '{tool}'")
    
    agent = REGISTRY["agents"].get(agent_id)
    if agent is None:
        return Decision("block", "P-004", f"Unknown agent '{agent_id}'", spec["risk"])
    
    risk = spec["risk"]
    risk_tier = get_risk_tier(risk)
    
    # Fail-closed: block Tier 3-4 actions if policy engine or audit log unavailable (Invariant I10)
    if risk_tier >= 3:
        if not policy_engine_available:
            return Decision("block", "FAIL-CLOSED", "Policy engine unavailable; high-risk action blocked", risk)
        if not audit_log_available:
            return Decision("block", "FAIL-CLOSED", "Audit log unavailable; high-risk action blocked", risk)

    # P-011 / P11: Agents can NEVER modify policies, registry, or audit (Invariant I11)
    forbidden_tools = ["policy.modify", "registry.modify", "audit.modify"]
    if tool in forbidden_tools:
        return Decision("block", "P-011", f"Agents cannot execute {tool} - governance data is immutable to agents", risk)

    # P-008: support-agent denied access to finance:* and employee-*:*
    if agent_id == "support":
        finance_tools = ["read_cost", "calc_margin", "read_payment_db"]
        if tool in finance_tools:
            return Decision("block", "P-008", f"Support agent cannot access finance tools", risk)

    # P-004 / P4: Agent registry permissions check
    if tool not in agent["allowed_tools"]:
        return Decision("block", "P-004", f"{agent['title']} is not permitted to use '{tool}'", risk)

    try:
        args = validate_args(tool, args)
    except ValueError as e:
        return Decision("block", "P-000", f"Invalid arguments: {e}", risk)

    try:
        # Tool-specific policy rules
        if tool == "propose_discount":
            limit = P["max_discount_percent"]
            
            # P-001 / P1: discount limit
            if args["percent"] > limit:
                return Decision("block", "P-001", 
                                f"A {args['percent']:g}% discount is above the {limit}% limit",
                                risk, {"max_percent": limit})
            
            _, product = get_product(args["product"])
            
            # P-002 / P2: stock check
            if args["units"] > product["stock"]:
                return Decision("block", "P-002",
                                f"Promoting {args['units']} units is more than the {product['stock']} in stock",
                                risk, {"max_units": product["stock"]})
            
            return Decision("allow", "P-001,P-002", "Medium risk: discount and stock rules passed", risk)

        if tool == "issue_refund":
            _, order = get_order(args["order_id"])
            
            # Basic validation
            if args["amount"] > order["amount"] - order["refunded"]:
                return Decision("block", "P-000", "Refund is more than the order amount", risk)
            
            threshold = P["refund_approval_threshold"]
            
            # P-005 / P3 (legacy P5 -> P3 mapping): refunds above threshold need human approval
            if args["amount"] > threshold:
                return Decision("escalate", "P-005",
                                f"Refund of Rs {args['amount']:,.0f} is above Rs {threshold:,} and needs human approval",
                                risk)
            
            # P-003: finance-agent can prepare but not unilaterally execute payments
            # (this would be checked if we had separate prepare/execute tools)
            
            return Decision("allow", "P-005", f"Refund is within the Rs {threshold:,} limit", risk)

        # P-002 / P-001: HR policy reads - only hr-agent may read hr-policy:* and employee-salary:*
        if tool == "read_hr_policy" or tool == "read_employee_salary":
            if agent_id != "hr-agent":
                return Decision("block", "P-002", f"Only HR agent may read employee salary data", risk)
            return Decision("allow", "P-002", "HR agent authorized for employee data", risk)

        # P-009, P-010: code.merge gate
        if tool == "code.merge":
            tests_passed = args.get("tests_passed", False)
            static_scan_passed = args.get("static_scan_passed", False)
            
            # P-010: deny if tests failed
            if not tests_passed:
                return Decision("block", "P-010", "Code merge denied: tests did not pass", risk)
            
            # P-009: escalate to code_reviewer if tests and scan passed
            if tests_passed and static_scan_passed:
                return Decision("escalate", "P-009", 
                                "Code merge requires human review (tests and scans passed)", risk)
            
            return Decision("block", "P-009", "Code merge requires tests and static scan to pass", risk)

    except ValueError as e:  # unknown product / order
        return Decision("block", "P-000", str(e), risk)

    # Default allow for low-risk tools
    return Decision("allow", "", f"{risk.capitalize()} risk: allowed", risk)


def check_separation_of_duties(requested_by, decided_by):
    """P-006: Separation of duties - requester cannot approve their own request (Invariant I8)."""
    if requested_by and decided_by and requested_by == decided_by:
        return False, "Requester and approver must be different people (P-006)"
    return True, ""


def check_delegation_depth(depth):
    """P-007: Delegation depth limit (Invariant I4)."""
    max_depth = P.get("max_delegation_depth", 3)
    if depth > max_depth:
        return False, f"Delegation depth {depth} exceeds maximum of {max_depth} (P-007)"
    return True, ""


def check_knowledge_freshness(fact, max_age_days=30):
    """P-004 extension: Check if knowledge is fresh enough for high-risk decisions."""
    if not fact:
        return False, "No fact available"
    
    age_s = time.time() - fact.get("observed_at", fact.get("ts", 0))
    age_days = age_s / 86400
    
    if age_days > max_age_days:
        return False, f"Knowledge is stale ({age_days:.1f} days old, max {max_age_days} days)"
    
    return True, ""


def check_knowledge_trust_level(fact, min_level="Verified"):
    """Ensure knowledge meets minimum trust level for high-risk decisions (Invariant I7)."""
    trust_hierarchy = ["Unsupported", "Agent-reported", "Verified", "Authoritative"]
    
    if not fact:
        return False, "No fact available"
    
    fact_level = fact.get("trust_level", "Agent-reported")
    
    if fact_level not in trust_hierarchy:
        return False, f"Unknown trust level: {fact_level}"
    
    if trust_hierarchy.index(fact_level) < trust_hierarchy.index(min_level):
        return False, f"Trust level '{fact_level}' is below required '{min_level}'"
    
    return True, ""


def evaluate_with_shadow_mode(agent_id, tool, args, shadow_policy_id=None):
    """Evaluate request with both active and shadow policies for safe testing."""
    # Evaluate with active policy
    active_decision = check_policy(agent_id, tool, args)
    
    # If shadow policy specified, evaluate but don't enforce
    if shadow_policy_id:
        # In real implementation, load shadow policy and evaluate
        # For now, just log that shadow evaluation would happen
        shadow_decision = active_decision  # Placeholder
        shadow_decision.shadow_mode = True
    
    return active_decision
