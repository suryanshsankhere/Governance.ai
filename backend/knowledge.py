"""The knowledge hub: verified facts with source, timestamp, trust level, and conflict resolution.

Version 3 enhancements:
- Four trust levels: Authoritative, Verified, Agent-reported, Unsupported
- Trusted clock for expiry (Manager's UTC clock, not agent-supplied)
- Conflict resolution with trust hierarchy
- Provenance tracking with evidence references
"""
import json
import time

from . import db
from .config import POLICIES as P

# kind of fact -> (agent that owns it, tool that produces it, tool argument name)
RESOLVERS = {
    "stock": ("inventory", "read_stock", "product"),
    "cost": ("finance", "read_cost", "product"),
}

# Trust level hierarchy (lowest to highest)
TRUST_LEVELS = ["Unsupported", "Agent-reported", "Verified", "Authoritative"]


def get_trust_level_rank(level):
    """Get numeric rank of trust level."""
    try:
        return TRUST_LEVELS.index(level)
    except ValueError:
        return 0  # Default to lowest


def put_fact(key, value, source, trust_level="Agent-reported", evidence=None):
    """Store a fact with trust level and provenance.
    
    Args:
        key: Fact identifier (e.g., "stock:earbuds")
        value: Fact value (JSON-serializable)
        source: Source description (e.g., "inventory agent / read_stock")
        trust_level: One of: Authoritative, Verified, Agent-reported, Unsupported
        evidence: Optional evidence/references supporting this fact
    """
    # Use Manager's trusted clock (Invariant: ignore agent-supplied timestamps)
    observed_at = time.time()
    max_age_s = P.get("fact_max_age_hours", 24) * 3600
    expires_at = observed_at + max_age_s
    
    db.execute(
        "INSERT OR REPLACE INTO facts(key,value,source,ts,max_age_s,trust_level,observed_at,expires_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (key, json.dumps(value), source, observed_at, max_age_s, trust_level, observed_at, expires_at),
    )


def put_knowledge_item(subject, field, value, trust_level="Agent-reported", source="", evidence=None):
    """Store structured knowledge item with full provenance.
    
    This is the V3 structured format that supports conflict resolution.
    """
    observed_at = time.time()
    max_age_s = P.get("fact_max_age_hours", 24) * 3600
    expires_at = observed_at + max_age_s
    
    evidence_str = json.dumps(evidence) if evidence else None
    
    db.execute(
        "INSERT INTO knowledge_items(subject,field,value,trust_level,source,evidence,observed_at,expires_at,ts) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (subject, field, json.dumps(value), trust_level, source, evidence_str, observed_at, expires_at, time.time()),
    )


def _row(r):
    """Convert database row to fact dict."""
    observed_at = r.get("observed_at", r["ts"])
    expires_at = r.get("expires_at", observed_at + r["max_age_s"])
    age = time.time() - observed_at
    
    return {
        "key": r["key"],
        "value": json.loads(r["value"]),
        "source": r["source"],
        "age_s": age,
        "fresh": time.time() <= expires_at,  # Use trusted Manager clock for expiry
        "trust_level": r.get("trust_level", "Agent-reported"),
        "observed_at": observed_at,
        "expires_at": expires_at
    }


def get_fact(key):
    """Retrieve a fact by key."""
    rows = db.query("SELECT * FROM facts WHERE key=?", (key,))
    return _row(rows[0]) if rows else None


def get_knowledge_item(subject, field):
    """Get the most authoritative unexpired knowledge item for subject+field.
    
    If multiple unexpired items exist, returns the one with highest trust level.
    If trust levels are equal, returns the most recent.
    """
    rows = db.query(
        "SELECT * FROM knowledge_items WHERE subject=? AND field=? AND expires_at > ? "
        "ORDER BY ts DESC",
        (subject, field, time.time())
    )
    
    if not rows:
        return None
    
    # Sort by trust level (highest first), then by timestamp (newest first)
    rows_sorted = sorted(rows, key=lambda r: (
        -get_trust_level_rank(r["trust_level"]),
        -r["ts"]
    ))
    
    best = rows_sorted[0]
    return {
        "subject": best["subject"],
        "field": best["field"],
        "value": json.loads(best["value"]),
        "trust_level": best["trust_level"],
        "source": best["source"],
        "evidence": json.loads(best["evidence"]) if best["evidence"] else None,
        "observed_at": best["observed_at"],
        "expires_at": best["expires_at"],
        "age_s": time.time() - best["observed_at"]
    }


def resolve_conflict(subject, field):
    """Resolve conflicts when multiple unexpired facts exist for same subject+field.
    
    Resolution procedure (Section 29.4):
    1. Compare trust levels - highest wins
    2. If trust equal, check source authority
    3. If still ambiguous, re-check connector or escalate to human
    
    Returns: (best_item, conflict_detected, resolution_method)
    """
    rows = db.query(
        "SELECT * FROM knowledge_items WHERE subject=? AND field=? AND expires_at > ?",
        (subject, field, time.time())
    )
    
    if len(rows) <= 1:
        return (rows[0] if rows else None, False, "no_conflict")
    
    # Multiple unexpired items - conflict detected
    rows_sorted = sorted(rows, key=lambda r: (
        -get_trust_level_rank(r["trust_level"]),
        -r["ts"]
    ))
    
    best = rows_sorted[0]
    second = rows_sorted[1]
    
    # If trust levels differ, trust hierarchy resolves it
    if get_trust_level_rank(best["trust_level"]) > get_trust_level_rank(second["trust_level"]):
        return (best, True, "trust_hierarchy")
    
    # If trust levels equal but sources differ, prefer system of record connectors
    authoritative_sources = ["connector:", "system_of_record:"]
    best_is_authoritative = any(best["source"].startswith(s) for s in authoritative_sources)
    second_is_authoritative = any(second["source"].startswith(s) for s in authoritative_sources)
    
    if best_is_authoritative and not second_is_authoritative:
        return (best, True, "source_authority")
    
    # Ambiguous - should re-check or escalate
    return (best, True, "ambiguous_needs_escalation")


def all_facts():
    """Get all stored facts."""
    return [_row(r) for r in db.query("SELECT * FROM facts ORDER BY key")]


def age_all(hours):
    """Demo helper: make every stored fact look `hours` older, to show the freshness rule."""
    db.execute("UPDATE facts SET ts = ts - ?, observed_at = observed_at - ?", (hours * 3600, hours * 3600))
    db.execute("UPDATE facts SET expires_at = expires_at - ?", (hours * 3600,))


def check_supplier_verification(supplier_id, max_age_days=30):
    """P-004 extension: Check if supplier verification is fresh and verified.
    
    Returns: (is_valid, reason)
    """
    # Query directly to include expired items
    rows = db.query(
        "SELECT * FROM knowledge_items WHERE subject=? AND field=? ORDER BY ts DESC LIMIT 1",
        (f"supplier:{supplier_id}", "verification_status")
    )
    
    if not rows:
        return False, "No supplier verification found"
    
    item = rows[0]
    value = json.loads(item["value"]) if item["value"] else None
    
    if value != "verified":
        return False, f"Supplier verification status is '{value}', not 'verified'"
    
    age_s = time.time() - item["observed_at"]
    age_days = age_s / 86400
    
    if age_days > max_age_days:
        return False, f"Supplier verification is stale ({age_days:.1f} days old, max {max_age_days} days)"
    
    return True, "Supplier verification valid"


def get_stale_facts():
    """Get all facts that have expired (for monitoring/alerts)."""
    current_time = time.time()
    rows = db.query("SELECT * FROM facts WHERE expires_at < ?", (current_time,))
    return [_row(r) for r in rows]


def get_unsupported_facts():
    """Get all facts with 'Unsupported' trust level (should not be used for Tier 3-4 decisions)."""
    rows = db.query("SELECT * FROM facts WHERE trust_level='Unsupported'")
    return [_row(r) for r in rows]
