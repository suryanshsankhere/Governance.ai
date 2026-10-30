# Quick Start Guide - Version 3 Multi-Agent AI Governance Platform

## Setup (5 Minutes)

### Windows PowerShell

```powershell
cd governance-mvp

# First time setup
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env

# Verify installation
python -m pytest -q

# Expected: 39 passed in ~20s
```

### Start the Server

```powershell
# Make sure venv is activated
.venv\Scripts\activate

# Start server (auto-reloads on code changes)
uvicorn backend.main:app --reload

# Or without reload
python -m uvicorn backend.main:app
```

Open your browser: **http://127.0.0.1:8000**

---

## The 3-Minute Demo Script

### Part 1: Original 5 Scenarios (Proves Core Claims)

Click these buttons in order, waiting ~2 seconds between each:

| Button | What Happens | Key Claim |
|--------|--------------|-----------|
| **1. Delegate a sale plan** | Green success. Sales reuses stock from knowledge hub. | Manager orchestrates. No duplicate work. |
| **2. Block a 40% discount** | Red block **P-001**. Sales revises to 20%. Inventory skipped (fresh fact). | Policy engine (code) decides. AI cannot break rules. |
| **3. Repeat request** | Blue duplicate status. Links to task #1. Zero new tool calls. | Idempotency prevents duplicate work. |
| **4. Support opens payment DB** | Red block **P-004/P-008**. Shows order details but no card/token. | Need-to-know filtering. Audit trail of attempt. |
| **5. Rs 15,000 refund** | Yellow "waiting for human". Click **Approve** → Green success. | Human has final say. Payload hash prevents tampering. |

**Point to header:** Green badge shows "✓ 13/13 Invariants + Chain OK"

---

### Part 2: Version 3 Features (Extended Demo)

Scroll down to show:

#### 1. Safety Invariants Section
- All 13 properties listed
- Real-time verification
- Explains I1 (no unauthorized effects), I6 (idempotency), I8 (separation of duties), I9 (audit chain), I11 (no policy mods)

#### 2. Knowledge Hub with Trust Levels
- Table shows **Trust** column
- Four levels: Authoritative, Verified, Agent-reported, Unsupported
- High-risk decisions require Verified or better

#### 3. Expanded Policy Rules
- 11 rules (P-001 to P-011) + V1
- Backward-compatible aliases (P1 = P-001, etc.)
- Each rule shows enforcement method and consequence

#### 4. Recent Events Stream
- 23 event types
- Shows request.received, plan.created, tool.called, etc.
- Real-time activity feed

#### 5. Audit Log
- Every action logged with timestamp, agent, decision, policy ID
- Pre-final checks visible (look for "precheck" entries)
- Human decisions traceable

---

## API Endpoints to Show

Open these URLs while server is running:

```
# Full system state (tasks, audit, facts, invariants)
http://127.0.0.1:8000/api/state

# Detailed invariants check
http://127.0.0.1:8000/api/invariants

# Recent events
http://127.0.0.1:8000/api/events

# Reconciliation check
http://127.0.0.1:8000/api/reconcile

# Health check
http://127.0.0.1:8000/api/health
```

---

## Judge Q&A - Quick Answers

### "Can agents bypass the manager?"
**No.** Agents hold zero credentials. Only the gateway can run tools. This is architecturally enforced (Invariants I2, I12). Show them `config/registry.yaml` - agents list allowed_tools but have no passwords.

### "What if an AI hallucinates an approval?"
Approvals are in SQLite with payload hashes. At execution:
1. Verify approval record exists
2. Check status = approved
3. **Recompute payload hash** - if parameters changed, BLOCK
4. Check not expired
5. Check not already used (single-use)

Agent claiming approval without real DB record gets blocked.

### "How do you prevent prompt injection?"
Three layers:
1. All retrieved data (supplier notes, documents) treated strictly as data, never instructions
2. Pre-final check flags injection patterns ("ignore all instructions")
3. Policy decisions are deterministic code, not influenced by prompts

### "Show me the code for a rule"
Open `backend/policy.py` lines 57-61:
```python
if args["percent"] > limit:  # P-001
    return Decision("block", "P-001",
                    f"A {args['percent']:g}% discount is above the {limit}% limit",
                    risk, {"max_percent": limit})
```

It's plain Python. AI output goes IN as data. Decision comes OUT as code execution.

### "What's the audit chain?"
Every audit event computes `SHA-256(prev_hash + timestamp + agent + action + ...)`.
Check it: Visit `/api/invariants` → see I9 status.
Any tampering breaks the chain. You can't edit history without detection.

### "Is this production-ready?"
**Demo mode yes. Production needs:**
- PostgreSQL (SQLite is fine for < 10K events/day)
- Redis for rate limiting
- User authentication
- Multi-tenancy
- Break-glass UI (data model ready)
- MCP/A2A integrations

**But the governance model IS production-ready:** Deterministic policy enforcement, tamper-evident audit, separation of duties, fail-closed on outages.

---

## Test Coverage

```bash
python -m pytest -v
```

**39 tests covering:**

✅ 13 original tests (5 demo scenarios + safety rules)  
✅ 26 V3 attack & failure tests:
- Payload tampering after approval
- Prompt injection detection
- Credit card redaction
- Separation of duties
- Delegation depth limits
- Plan validation rejects forbidden tools
- Idempotency enforcement
- Audit chain integrity
- Knowledge trust levels
- Stale knowledge blocking
- Fail-closed on outages
- Approval expiry
- Single-use approvals
- P-011 blocks policy modifications
- Compensation on partial failure
- All invariants pass
- Connector traceability
- Event bus recording
- Reconciliation
- Plan budget enforcement
- Knowledge conflict resolution

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Port busy | `uvicorn backend.main:app --port 8001` |
| Module not found | Ensure in `governance-mvp` folder with `backend/` subdirectory |
| Tests fail | `rm governance.db` then re-run tests |
| Scenario says duplicate | Click **Reset demo** button |
| Dashboard blank | Check terminal for server errors, restart server |

---

## Architecture Summary (30 Seconds)

```
Hub-and-Spoke Enforcement:

Manager (orchestrator) 
    ↓
Gateway (PEP) + Policy Engine (PDP) ← ONLY door to tools/data
    ↓
Inventory, Sales, Finance, Support agents ← Zero credentials, zero direct calls
    ↓
Hash-Chained Audit Log ← Tamper-evident
```

**Key insight:** AI proposes. Code decides. Human approves high-risk. Audit proves.

---

## Version 3 Highlights

🔒 **Safety First:**
- 13 safety invariants continuously verified
- SHA-256 hash-chained audit (tamper-evident)
- Payload-hash approvals (parameter tampering blocked)
- Fail-closed on policy/audit unavailability

🧠 **Knowledge Governance:**
- 4 trust levels (Authoritative → Unsupported)
- Manager's trusted clock (agents can't fake timestamps)
- Conflict resolution with trust hierarchy
- 30-day max age for high-risk decisions

📜 **Policy Expansion:**
- 11 rules (P-001 to P-011) from original 7
- Code merge gate (P-009, P-010)
- Governance immutability (P-011)
- Cross-department access control (P-008)

🛡️ **Pre-Final Checks:**
- PII redaction (credit cards, SSNs)
- Prompt injection screening
- Status claim verification
- Schema compliance

✅ **39 Automated Tests:**
- 5 demo scenarios
- 26 attack & failure cases
- 100% coverage of all invariants

---

**Ready to present. All 39 tests passing. All 13 invariants verified. Hash chain intact.** 🎯
