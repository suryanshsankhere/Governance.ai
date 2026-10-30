"""SQLite layer: tasks, audit log, knowledge facts, approvals, delegation grants, events, and V3 extensions."""
import hashlib
import json
import sqlite3
import threading
import time

from .config import DB_PATH

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT,
  status TEXT,
  result TEXT,
  ts REAL,
  idempotency_key TEXT,
  parent_task_id INTEGER,
  max_steps INTEGER DEFAULT 25,
  max_model_calls INTEGER DEFAULT 30,
  max_wall_clock_minutes INTEGER DEFAULT 15,
  max_retries INTEGER DEFAULT 3,
  retry_count INTEGER DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_idempotency ON tasks(idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS task_dependencies(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER NOT NULL,
  depends_on_task_id INTEGER NOT NULL,
  FOREIGN KEY(task_id) REFERENCES tasks(id),
  FOREIGN KEY(depends_on_task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS audit_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL,
  task_id INTEGER,
  agent TEXT,
  type TEXT,
  tool TEXT,
  decision TEXT,
  policy_id TEXT,
  reason TEXT,
  detail TEXT,
  prev_hash TEXT,
  hash TEXT,
  payload_ref TEXT
);

CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL,
  task_id INTEGER,
  agent TEXT,
  type TEXT,
  tool TEXT,
  decision TEXT,
  policy_id TEXT,
  reason TEXT,
  detail TEXT
);

CREATE TABLE IF NOT EXISTS facts(
  key TEXT PRIMARY KEY,
  value TEXT,
  source TEXT,
  ts REAL,
  max_age_s REAL,
  trust_level TEXT DEFAULT 'Agent-reported',
  observed_at REAL,
  expires_at REAL
);

CREATE TABLE IF NOT EXISTS knowledge_items(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subject TEXT,
  field TEXT,
  value TEXT,
  trust_level TEXT,
  source TEXT,
  evidence TEXT,
  observed_at REAL,
  expires_at REAL,
  ts REAL
);

CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER,
  agent TEXT,
  tool TEXT,
  args TEXT,
  policy_id TEXT,
  reason TEXT,
  status TEXT,
  ts REAL,
  decided_ts REAL,
  decided_by TEXT,
  requested_by TEXT,
  payload_hash TEXT,
  expires_at REAL,
  used_at REAL
);

CREATE TABLE IF NOT EXISTS delegation_grants(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER,
  parent_agent TEXT,
  child_agent TEXT,
  parent_scope TEXT,
  child_scope TEXT,
  depth INTEGER,
  ts REAL,
  expires_at REAL
);

CREATE TABLE IF NOT EXISTS connector_actions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER,
  decision_id INTEGER,
  approval_id INTEGER,
  tool TEXT,
  args TEXT,
  result TEXT,
  status TEXT,
  ts REAL,
  reconciled BOOLEAN DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL,
  event_type TEXT,
  task_id INTEGER,
  agent TEXT,
  detail TEXT,
  severity TEXT
);

CREATE TABLE IF NOT EXISTS policy_decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER,
  agent TEXT,
  tool TEXT,
  args TEXT,
  decision TEXT,
  policy_id TEXT,
  policy_version TEXT,
  reason TEXT,
  shadow_mode BOOLEAN DEFAULT 0,
  ts REAL
);

CREATE TABLE IF NOT EXISTS compensation_actions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER,
  original_action_id INTEGER,
  compensation_type TEXT,
  status TEXT,
  detail TEXT,
  ts REAL
);

CREATE TABLE IF NOT EXISTS agent_versions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT,
  model_ref TEXT,
  prompt_hash TEXT,
  tool_manifest_hash TEXT,
  promotion_status TEXT,
  autonomy_level INTEGER DEFAULT 0,
  regression_passed BOOLEAN DEFAULT 0,
  ts REAL
);

CREATE TABLE IF NOT EXISTS dead_letter_tasks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  original_task_id INTEGER,
  reason TEXT,
  detail TEXT,
  ts REAL
);

CREATE TABLE IF NOT EXISTS break_glass_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id INTEGER,
  reason TEXT,
  scope TEXT,
  requester TEXT,
  approver1 TEXT,
  approver2 TEXT,
  status TEXT,
  expires_at REAL,
  ts REAL
);

CREATE TABLE IF NOT EXISTS idempotency_keys(
  key TEXT PRIMARY KEY,
  task_id INTEGER,
  ts REAL
);

CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE,
  role TEXT,
  ts REAL
);

CREATE TABLE IF NOT EXISTS agents(
  id TEXT PRIMARY KEY,
  title TEXT,
  role TEXT,
  status TEXT DEFAULT 'ACTIVE',
  suspended BOOLEAN DEFAULT 0,
  ts REAL
);

CREATE TABLE IF NOT EXISTS policies(
  id TEXT PRIMARY KEY,
  version TEXT,
  content TEXT,
  active BOOLEAN DEFAULT 1,
  ts REAL
);
"""


def conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
    return _conn


def reset():
    with _lock:
        c = conn()
        tables = [
            "tasks", "task_dependencies", "audit_events", "audit", "facts", "knowledge_items",
            "approvals", "delegation_grants", "connector_actions", "events", "policy_decisions",
            "compensation_actions", "agent_versions", "dead_letter_tasks", "break_glass_requests",
            "idempotency_keys", "users", "agents", "policies"
        ]
        for table in tables:
            c.execute(f"DROP TABLE IF EXISTS {table}")
        c.execute("DROP INDEX IF EXISTS idx_idempotency")
        c.executescript(SCHEMA)
        c.commit()


def query(sql, params=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, params).fetchall()]


def execute(sql, params=()):
    with _lock:
        c = conn()
        cur = c.execute(sql, params)
        c.commit()
        return cur.lastrowid


def log_event(task_id, agent, type_, tool="", decision="", policy_id="", reason="", detail=None):
    """Write one line to the audit log: who did what, under which rule, with what outcome."""
    return execute(
        "INSERT INTO audit(ts,task_id,agent,type,tool,decision,policy_id,reason,detail) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (time.time(), task_id, agent, type_, tool, decision, policy_id, reason,
         json.dumps(detail, default=str) if detail is not None else ""),
    )


def log_audit_event(task_id, agent, type_, tool="", decision="", policy_id="", reason="", detail=None, payload_ref=None):
    """Write to hash-chained audit_events table with tamper-evident chain."""
    with _lock:
        # Get previous hash
        prev = query("SELECT hash FROM audit_events ORDER BY id DESC LIMIT 1")
        prev_hash = prev[0]["hash"] if prev else "genesis"
        
        # Compute current hash
        ts = time.time()
        detail_str = json.dumps(detail, default=str) if detail is not None else ""
        hash_input = f"{prev_hash}|{ts}|{task_id}|{agent}|{type_}|{tool}|{decision}|{policy_id}|{reason}|{detail_str}"
        current_hash = hashlib.sha256(hash_input.encode()).hexdigest()
        
        return execute(
            "INSERT INTO audit_events(ts,task_id,agent,type,tool,decision,policy_id,reason,detail,prev_hash,hash,payload_ref) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, task_id, agent, type_, tool, decision, policy_id, reason, detail_str, prev_hash, current_hash, payload_ref),
        )


def emit_event(event_type, task_id=None, agent=None, detail=None, severity="INFO"):
    """Emit structured event to the event bus."""
    return execute(
        "INSERT INTO events(ts,event_type,task_id,agent,detail,severity) VALUES(?,?,?,?,?,?)",
        (time.time(), event_type, task_id, agent, json.dumps(detail, default=str) if detail else "", severity)
    )


def verify_audit_chain():
    """Verify the SHA-256 hash chain in audit_events. Returns (is_valid, error_message)."""
    with _lock:
        events = query("SELECT * FROM audit_events ORDER BY id")
        if not events:
            return True, "No events yet"
        
        prev_hash = "genesis"
        for i, event in enumerate(events):
            # Recompute hash
            hash_input = f"{prev_hash}|{event['ts']}|{event['task_id']}|{event['agent']}|{event['type']}|{event['tool']}|{event['decision']}|{event['policy_id']}|{event['reason']}|{event['detail']}"
            expected_hash = hashlib.sha256(hash_input.encode()).hexdigest()
            
            if event['prev_hash'] != prev_hash:
                return False, f"Event {i+1}: prev_hash mismatch (expected {prev_hash[:8]}..., got {event['prev_hash'][:8]}...)"
            
            if event['hash'] != expected_hash:
                return False, f"Event {i+1}: hash verification failed"
            
            prev_hash = event['hash']
        
        return True, f"Chain verified: {len(events)} events"


def compute_payload_hash(tool, args):
    """Compute SHA-256 hash of action payload for tamper-proof approval binding."""
    payload = json.dumps({"tool": tool, "args": args}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()
