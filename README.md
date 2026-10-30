# Multi-Agent AI Governance Platform (Version 3)

**The manager agent thinks. The policy engine decides. The human has the final say.**

A production-ready governance control tower that sits above a company's AI agents, orchestrating their work, enforcing policy rules through deterministic code, and maintaining a tamper-evident audit trail. Version 3 adds comprehensive safety invariants, pre-final output checks, knowledge provenance, and fail-closed enforcement.

---

## 1. What's Inside (Version 3 Architecture)

| Component | File | What It Does |
| --- | --- | --- |
| **Orchestrator** | `backend/orchestrator.py` | Plans tasks, validates plans, delegates to agents, handles compensation on failure |
| **Policy Engine** | `backend/policy.py` + `config/policies.yaml` | 11 deterministic rules (P-001 to P-011) that make allow/block/escalate decisions |
| **Gateway** | `backend/gateway.py` | The ONLY door for agents to tools/data. Logs everything, filters sensitive fields, enforces payload-hash approvals |
| **Knowledge Hub** | `backend/knowledge.py` | Shared facts with 4 trust levels, provenance tracking, Manager's trusted clock for expiry, conflict resolution |
| **Safety Invariants** | `backend/invariants.py` | Independent checker verifying 13 safety properties (I1-I13) against database state |
| **Audit System** | `backend/db.py` | SHA-256 hash-chained tamper-evident audit log with privacy-preserving payload references |
| **Agents** | `backend/agents.py` + `config/registry.yaml` | Inventory, Sales, Finance, Support. Zero credentials, only permitted tools |
| **Dashboard** | `dashboard/index.html` | Plain JavaScript UI with real-time invariants health badge, event stream, and demo scenarios |
| **Mock Data** | `data/company.json` | Demo products, orders, payments (not real data) |
| **Test Suite** | `tests/test_scenarios.py` | 39 automated tests covering 5 scenarios + 26 V3 attack/failure cases |

### Two Operating Modes

Set `AGENT_MODE` in `.env`:
- **`mock`** (default): Scripted agents. Zero dependencies on external APIs. Fully deterministic. **Use this for demos.**
- **`llm`**: Real AI model agents (requires API key). Same gateway, same rules, same safety guarantees.

---

## 2. Run It on Your Laptop (10 Minutes)

You need Python 3.10 or newer.

### Windows (PowerShell)
```powershell
# Navigate to the governance-mvp folder
cd governance-mvp

# Create virtual environment
py -m venv .venv

# Activate
.venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Create config file
copy .env.example .env

# Run tests (should see "39 passed")
python -m pytest -q

# Start server
uvicorn backend.main:app --reload
```

### Mac or Linux
```bash
cd governance-mvp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m pytest -q
uvicorn backend.main:app --reload
```

Open **http://127.0.0.1:8000** in your browser.

**Backup Plan (Venue Wi-Fi):** If `pip install` fails, run `python -m backend.fallback_server` which needs only `pyyaml`. Install packages at home beforehand.

---

## 3. The 3-Minute Live Demo (Original 5 Scenarios)

Click the scenario buttons in order. Each proves a key claim.

| # | Click | What Happens | What to Say |
| --- | --- | --- | --- |
| **1** | **1. Delegate a sale plan** | Plan: inventory → sales → finance. Sales gets stock from hub (no duplicate work). Green success. | "The manager splits the goal into steps. Sales didn't ask Inventory again—the manager answered from the shared knowledge hub." |
| **2** | **2. Block a 40% discount** | Sales proposes 40%, **red block P-001**, revises to 20%. Inventory step skipped (fresh fact reused). | "40% was blocked by rule P-001, plain code. Sales revised to 20%. The manager also skipped Inventory because the stock fact was still fresh." |
| **3** | **3. Repeat request** | Status `duplicate`, linked to task #1. Zero tool calls. | "Same request. No work repeated. The manager reused the earlier result via idempotency key." |
| **4** | **4. Support opens payment DB** | **Red block P-004/P-008** on `read_payment_db`. Order details shown without card/token. | "Support tried the payment database. The gateway blocked it and logged the attempt. Agents only see fields they're allowed to see." |
| **5** | **5. Rs 15,000 refund** | Yellow card "Waiting for a human". Click **Approve** → green success. | "Refunds above Rs 10,000 need human approval (P-005). Nothing runs until I approve. The gateway bound this approval to a payload hash—if parameters change, it blocks for tampering." |

**After the demo:** Point to the **green invariants badge** in the header: "13/13 Invariants + Chain OK" means all safety properties verified in real time.

---

## 4. Version 3 Extended Demo (New Capabilities)

### 4A. Payload Tampering Detection
1. Click **"5. Rs 15,000 refund"** → approval requested
2. Explain: "Every approval is bound to a SHA-256 hash of the action parameters"
3. "If anyone modifies the amount or order ID after approval, the gateway detects tampering and blocks execution"
4. Click **Approve** → success with payload hash verified

### 4B. Safety Invariants Health Dashboard
1. Point to header badge: **"✓ 13/13 Invariants + Chain OK"**
2. Scroll to **V3: Safety Invariants** section
3. Show all 13 invariants listed: "These check:
   - I1: No unauthorized effects
   - I6: Idempotency enforced
   - I8: Separation of duties
   - I9: Audit chain verified
   - I11: No policy modifications by agents"

### 4C. Knowledge Hub with Trust Levels
1. Scroll to **Knowledge hub** table
2. Show **Trust** column with levels: Authoritative, Verified, Agent-reported
3. Explain: "High-risk decisions (payments, code merges) require Verified or Authoritative knowledge. Stale or Unsupported facts trigger refresh or block."

### 4D. Pre-Final Output Check
1. Run any scenario
2. In **Audit log**, find `precheck` entry
3. Explain: "Before any agent result is released, the manager scans for:
   - Credit card numbers (redacted)
   - Prompt injection attempts (flagged)
   - False status claims (verified against connector records)"

### 4E. Fail-Closed Enforcement
Run this in browser console or via API:
```javascript
fetch('/api/invariants').then(r => r.json()).then(console.log)
```
Show: "If the policy engine or audit log become unavailable, high-risk actions (Tier 3-4) fail closed immediately. We never fail open."

---

## 5. API Endpoints (Version 3)

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/` | GET | Dashboard UI |
| `/api/health` | GET | Health check |
| `/api/state` | GET | Full system state (tasks, audit, facts, invariants) |
| `/api/invariants` | GET | Detailed 13-invariant check results |
| `/api/events` | GET | Recent event stream (23 event types) |
| `/api/reconcile` | GET | Reconcile connector actions vs. systems of record |
| `/api/requests` | POST | Submit new request |
| `/api/scenarios/{n}` | POST | Run demo scenario 1-5 |
| `/api/approvals/{id}` | POST | Approve/reject pending action |
| `/api/facts/age` | POST | Age facts (demo helper) |
| `/api/reset` | POST | Reset demo state |

---

## 6. Version 3 Policy Pack (11 Rules)

Expanded from 7 to 11 rules with backward-compatible aliases:

| ID | Alias | Rule | Enforcement | If Broken |
| --- | --- | --- | --- | --- |
| **P-001** | P1 | No discount above 20% | Policy engine (code) | Block and explain |
| **P-002** | P2 | Never promote more units than in stock | Policy engine (code) | Block and explain |
| **P-003** | — | Finance may prepare but not unilaterally execute payments | Policy engine (code) | Block and escalate |
| **P-004** | P4 | Agents may only use authorized tools and access current verified data | Gateway + Knowledge hub | Block and log |
| **P-005** | P3 | Refunds/payments > Rs 10,000 need human approval; facts > 24h refreshed | Policy engine + Hub | Escalate / Refresh |
| **P-006** | — | Separation of duties: requester ≠ approver | Gateway approval flow | Block |
| **P-007** | — | Delegation depth ≤ 3 | Gateway | Block |
| **P-008** | — | Support cannot access finance:* or employee-*:* | Policy engine | Block and log |
| **P-009** | — | Code merge with passing tests/scans escalates to code_reviewer | Policy engine | Escalate |
| **P-010** | — | Code merge with failing tests denied | Policy engine | Block |
| **P-011** | — | Agents cannot execute policy.modify, registry.modify, audit.modify | Policy + Plan validator | Block immediately |
| **V1** | — | Plans must keep ≥10% profit margin | Manager verification | Reject plan |

**Key Point:** Rules are plain Python code in `backend/policy.py` and `backend/orchestrator.py`. An AI cannot break a rule because the decision is deterministic code, not AI output.

---

## 7. Safety Invariants (13 Properties, Continuously Verified)

The platform maintains 13 independently-checked safety properties:

| Invariant | Property |
| --- | --- |
| **I1** | No Tier 3/4 external effect without matching ALLOW decision or valid approval |
| **I2** | Agents hold zero business-system or model-provider credentials |
| **I3** | Every gateway request has corresponding audit_events row (including denials) |
| **I4** | Every delegation has child scope ⊆ parent scope and depth ≤ 3 |
| **I5** | Every COMPLETED task passed through valid state transitions with evidence |
| **I6** | At most one external effect per idempotency_key |
| **I7** | No Tier 3/4 decision used expired knowledge or Unsupported trust level |
| **I8** | No approval has decided_by == requested_by |
| **I9** | The audit_events SHA-256 hash chain verifies from start to end |
| **I10** | High-risk actions fail closed when policy engine or audit log unavailable |
| **I11** | Zero agent modifications to policies, registry, or audit data |
| **I12** | Zero direct agent-to-agent calls bypassing the Manager |
| **I13** | Every executed connector_actions traces to valid task/decision/approval |

Check live: `GET /api/invariants`

---

## 8. How to Answer Judges (Honest Claims & Limitations)

| Question | Honest Answer |
| --- | --- |
| **"Is the manager just an AI deciding?"** | No. The AI orchestrator proposes. The policy engine—plain Python code—decides. A tricked or hallucinating AI still cannot break a rule because the decision is deterministic code, not AI output. |
| **"Can an agent bypass the manager?"** | No. Agents hold zero credentials. Only the gateway can run tools. Asking the gateway is the only path. This is enforced architecturally (Invariant I2, I12). |
| **"How does it handle confidential data?"** | Three layers: (1) Sensitive tools return only permitted fields per agent, (2) Pre-final check scans outputs for leaked secrets, (3) Audit log uses payload references instead of raw PII so data can be erased without breaking the hash chain. |
| **"Where is the human?"** | Humans have final authority on high-risk actions (Tier 3-4). Nothing executes until approved. Approvals are tamper-proof via payload hashing. Separation of duties enforced (P-006). |
| **"Is this real AI or scripted?"** | Demo mode uses scripted agents for predictability. The platform is the product: the same gateway and rules govern real AI agents in `llm` mode. We tested both. |
| **"What would you build next?"** | (1) Visual policy editor, (2) Real integrations (MCP, Agent2Agent protocol), (3) PostgreSQL + Redis for production scale, (4) Break-glass two-person approval UI, (5) Multi-tenancy with org-level policies. |
| **"How do you prevent prompt injection?"** | Treat all retrieved data (supplier notes, documents) strictly as data, never instructions. Pre-final check flags injection patterns. Policy decisions are code, not influenced by prompts. |
| **"Can you show the code for a rule?"** | Yes! Open `backend/policy.py` lines 45-50. P-001 is a simple if-statement checking `args["percent"] > P["max_discount_percent"]`. Show judges the code. |
| **"What if the model hallucinates an approval?"** | Approvals are stored in SQLite with payload hashes. At execution time, we verify (1) approval exists, (2) status is approved, (3) payload hash matches, (4) not expired, (5) not already used. An agent claiming approval without a real database row gets blocked. |
| **"What's the audit chain?"** | Every audit event includes a SHA-256 hash of `prev_hash + timestamp + agent + action + ...`. We verify the chain on every invariant check. Any tampering breaks the chain. You can't edit history without detection. |

---

## 9. Test Suite (39 Tests Covering 5 Scenarios + 26 V3 Cases)

Run the full test suite:
```bash
python -m pytest -v
```

### Test Coverage

**Original Demo Scenarios (13 tests):**
- ✓ Scenario 1: Delegation and knowledge hub
- ✓ Scenario 2: Blocked discount revision
- ✓ Scenario 3: Duplicate detection
- ✓ Scenario 4: Unauthorized access blocked
- ✓ Scenario 5a: Human approval flow
- ✓ Scenario 5b: Human rejection flow
- ✓ Small refund auto-executes
- ✓ Stock limit enforced
- ✓ Stale fact refresh
- ✓ Registry permissions
- ✓ Tool call limits
- ✓ Unknown request handling
- ✓ LLM mode obeys same rules

**V3 Attack & Failure Cases (26 tests):**
1. ✓ Payload tampering after approval (detects parameter changes)
2. ✓ Prompt injection in supplier notes (flags and escalates)
3. ✓ Credit card redaction in outputs
4. ✓ Separation of duties (self-approval blocked)
5. ✓ Delegation depth limit (P-007)
6. ✓ Plan validator rejects forbidden tools (P-011)
7. ✓ Idempotency key enforcement (no duplicate payments)
8. ✓ Audit chain integrity verification
9. ✓ Audit chain tamper detection
10. ✓ Knowledge trust levels (Unsupported rejected)
11. ✓ Stale supplier verification blocked
12. ✓ Manager's trusted clock used for expiry
13. ✓ Fail-closed on policy engine unavailable
14. ✓ Fail-closed on audit log unavailable
15. ✓ Approval expiry enforcement
16. ✓ Single-use approval (cannot reuse)
17. ✓ P-011 blocks policy.modify
18. ✓ P-008 blocks support-agent finance access
19. ✓ Compensation on partial failure
20. ✓ All invariants pass on clean execution
21. ✓ Connector traceability (I13)
22. ✓ No direct agent-to-agent calls (I12)
23. ✓ Event bus records events
24. ✓ Reconciliation detects discrepancies
25. ✓ Plan budget limits enforced
26. ✓ Knowledge conflict resolution with trust hierarchy

Expected: **39 passed** in < 5 seconds.

---

## 10. Quick Configuration Changes

| What to Change | Where | Example |
| --- | --- | --- |
| Discount limit | `config/policies.yaml` | `max_discount_percent: 25` |
| Approval threshold | `config/policies.yaml` | `refund_approval_threshold: 50000` |
| Fact freshness | `config/policies.yaml` | `fact_max_age_hours: 48` |
| Add a tool | `backend/tools.py` TOOLS dict | Add function + schema |
| Add an agent | `config/registry.yaml` | Add under `agents:` |
| Add a policy rule | `backend/policy.py` | Add check in `check_policy()` |
| Add a product | `data/company.json` | Add under `products` |

Changes take effect on server restart (or auto-reload if using `--reload`).

---

## 11. Troubleshooting

| Problem | Solution |
| --- | --- |
| `uvicorn` not found | Run: `python -m uvicorn backend.main:app --reload` |
| Port 8000 busy | Add `--port 8001` and open that port |
| Module backend not found | Ensure you're in the `governance-mvp` folder |
| Scenario says `duplicate` | Press **Reset demo** button |
| Dashboard not updating | Server crashed—check terminal for errors |
| Test failures | Ensure clean state: `rm governance.db` then `pytest` |
| `llm` mode errors | Check API key in `.env`, fall back to `AGENT_MODE=mock` |

---

## 12. Deploy Online (Required for Submission)

### Option 1: Render (Free Tier)
1. Push to GitHub (`.env` and `governance.db` are gitignored)
2. Render.com → New Web Service → Connect repo
3. Build: `pip install -r requirements.txt`
4. Start: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`
5. Environment: Add `AGENT_MODE=mock`
6. Deploy and test

### Option 2: Railway, Fly.io, or Heroku
Similar setup. Use the `Procfile` included:
```
web: uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

**Before Judging:** Open your live link 5 minutes early (free servers sleep when idle).

---

## 13. Architecture Diagrams (For Presentation)

### Hub-and-Spoke Enforcement

```
              ┌─────────────────┐
              │   Manager /     │
              │  Orchestrator   │◄─── Plans, Validates, Coordinates
              └────────┬────────┘
                       │
          ┌────────────┴────────────┐
          │   Gateway (PEP)         │◄─── ONLY door to tools/data
          │ + Policy Engine (PDP)   │     Logs, Filters, Enforces
          └──┬────┬──────┬──────┬──┘
             │    │      │      │
        ┌────▼┐ ┌─▼───┐ ┌▼────┐ ┌▼──────┐
        │Inv. │ │Sales│ │Fin. │ │Support│ ◄─── Zero credentials
        │Agent│ │Agent│ │Agent│ │ Agent │      Zero direct calls
        └─────┘ └─────┘ └─────┘ └───────┘
                       │
                ┌──────▼──────────┐
                │ Audit Log       │
                │ (Hash-Chained)  │◄─── Tamper-evident
                └─────────────────┘
```

### Decision Flow (Every Tool Call)

```
Agent → Gateway → Policy Check → Allow/Block/Escalate
                      ↓
                 [Decision]
                 /    |    \
            Allow   Block   Escalate
              ↓       ↓        ↓
           Execute  Return   Human
           Tool     Error    Approval
              ↓       ↓        ↓
           Audit   Audit    Audit
           Log     Log      Log
```

---

## 14. File Structure Reference

```
governance-mvp/
├── backend/
│   ├── __init__.py
│   ├── main.py              # FastAPI entrypoint
│   ├── api.py               # API functions (V3: +invariants, +events)
│   ├── orchestrator.py      # Plans, validates, delegates (V3: +compensation)
│   ├── policy.py            # 11 policy rules (V3: P-001 to P-011)
│   ├── gateway.py           # Tool call enforcement (V3: +payload hash, +pre-check)
│   ├── knowledge.py         # Shared facts (V3: +trust levels, +conflict resolution)
│   ├── invariants.py        # [NEW V3] 13 safety invariant checker
│   ├── agents.py            # 4 departmental agents
│   ├── tools.py             # Company tool connectors
│   ├── db.py                # SQLite with hash-chained audit (V3: 19 tables)
│   ├── llm.py               # Model client wrapper
│   ├── config.py            # Settings loader
│   └── fallback_server.py   # Zero-dependency backup server
├── config/
│   ├── policies.yaml        # Policy thresholds (V3: 11 rules)
│   └── registry.yaml        # Agent capabilities
├── dashboard/
│   └── index.html           # Plain JS UI (V3: +invariants badge, +events)
├── data/
│   └── company.json         # Mock products, orders, payments
├── tests/
│   └── test_scenarios.py    # 39 tests (V3: +26 attack/failure cases)
├── .env.example             # Environment template
├── .gitignore
├── Procfile                 # Deployment config
├── README.md                # This file
└── requirements.txt         # Python dependencies
```

---

## 15. Credits & License

Built for the AI Governance Hackathon.

**Tech Stack:**
- Backend: Python, FastAPI, SQLite
- Frontend: Vanilla JavaScript (no build step)
- Testing: pytest
- Deployment: Any Python host

**License:** MIT (change as needed for your submission)

**Note:** All company data in `data/company.json` is fictional. No real customer information.

---

## 16. Version 3 Changelog

### What's New in V3

**Safety & Compliance:**
- ✅ 13 safety invariants with continuous verification
- ✅ SHA-256 hash-chained tamper-evident audit log
- ✅ Payload-hash binding for tamper-proof approvals
- ✅ Separation of duties enforcement (P-006)
- ✅ Fail-closed on policy/audit unavailability (I10)
- ✅ Pre-final output checks (PII redaction, prompt injection screening)

**Knowledge & Trust:**
- ✅ Four trust levels: Authoritative, Verified, Agent-reported, Unsupported
- ✅ Manager's trusted clock for expiry (agents can't fake timestamps)
- ✅ Conflict resolution with trust hierarchy
- ✅ Freshness checks with 30-day max age for high-risk decisions

**Policy Expansion:**
- ✅ Expanded from 7 to 11 rules (P-001 to P-011)
- ✅ Code merge gate (P-009, P-010)
- ✅ Governance immutability (P-011: agents cannot modify policies/registry/audit)
- ✅ Cross-department access control (P-008)

**Orchestration & Resilience:**
- ✅ Deterministic plan validator (rejects forbidden tools, checks budgets)
- ✅ Multi-step compensation on partial failure
- ✅ Reconcile-before-retry for connector actions
- ✅ Idempotency key enforcement (prevents duplicate payments)
- ✅ Break-glass emergency override support (two-person approval)

**Observability:**
- ✅ 23 structured event types on event bus
- ✅ Automated anomaly detectors (duplicate work, identical-input loops, data-volume spikes)
- ✅ Reconciliation job for connector action verification
- ✅ Real-time invariants health dashboard

**Testing:**
- ✅ Expanded from 13 to 39 tests
- ✅ 26 new attack & failure test cases
- ✅ Full coverage of all invariants and policy rules

---

## Need Help?

**Check the dashboard first:** Most issues show up in the audit log with clear error messages.

**Run tests:** `python -m pytest -v` will show exactly what passed and what failed.

**Check invariants:** Visit `http://127.0.0.1:8000/api/invariants` to see if any safety properties are violated.

**For demos:** Always use `AGENT_MODE=mock` for predictable, deterministic behavior.

---

**Ready to demonstrate production-grade AI governance. The manager thinks, the policy decides, the human has the final say.** 🎯
