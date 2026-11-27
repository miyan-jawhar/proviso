# Proviso project plan

> **Terms. Spend. Proof.** — Evidence-gated contract-to-spend audit.

This is the maintained delivery plan for Proviso. The repository is an
evidence-gated prototype built around a narrow, defensible decision: whether a vendor's
contract-valid monthly invoices exceed the cap in its indexed contract.

## Product boundary

Proviso joins PDF contract evidence with a structured invoice ledger. For a
cap decision it must retrieve the vendor-specific term, verify the available
contract dates, group contract-valid invoices by calendar month, calculate the
variance, show citations and read-only SQL, and escalate when the evidence is
ambiguous or incomplete. It must not compare an invoice average with a monthly
cap or present an unsupported answer as a compliance conclusion.

The current corpus is synthetic and contains one indexed executed contract per
vendor. Proviso is not a contract-lifecycle, document-approval, or production
identity system.

## Technical blueprint

```text
Streamlit UI
  ├─ Chat, risk dashboard, cost analytics proof view, XAI/feedback
  ▼
FastAPI
  ├─ JWT authentication, rate limits, query validation, health/operating mode
  ▼
LangGraph state machine
  ├─ Legal: local Chroma retrieval + evidence/entity gates
  └─ Finance: read-only SQLite/PostgreSQL + constrained SQL
       ▼                         ▼
Local PDF/Chroma evidence     Local SQLite ledger
```

The default local profile uses SQLite, persistent local Chroma, and
`all-mpnet-base-v2` embeddings. Gemini is the default hosted provider only for
open-ended interpretation; OpenAI and a local Ollama model are optional.
Prefect remains the optional, reproducible PDF-ingestion orchestrator, while
Docker/PostgreSQL are optional deployment profiles.

## Engineering findings from the controlled fixture

### Retrieval needs an entity boundary

Similarity-only retrieval returned plausible but wrong contracts for all four
synthetic unknown-vendor probes, yielding 0/4 safe handoffs. Proviso now
requires an exact normalized legal-entity metadata span before a named
contract can be used as evidence. Its current 12-case boundary manifest
accepts harmless punctuation/conjunction normalization and escalates typos,
legal-suffix conflicts, DBAs, subsidiaries, partial names, and initials. This
is a precision-first safety policy; it is not a substitute for governed alias
master data in a real ledger.

### A monthly policy cannot use an invoice average

Across the complete synthetic ledger, the deliberately invalid vendor-average
invoice baseline finds 0 of 30 known breach months. The contract-month policy
finds 30/30. Proviso therefore treats the cap calculation as a constrained
evidence-and-SQL operation, not an LLM judgement or a generic anomaly score.

### Effective-dated cap changes are an evidence boundary

The current deterministic policy supports one monthly cap over one contract
term. If retrieval finds two cap values for the same vendor—such as a cap change
mid-month—it escalates for an executed effective-dated schedule and approved
proration/month-splitting policy. It never silently selects a value. Supporting
multiple effective-dated terms is a future policy-engine capability, not a
claim of the present prototype.

## Dataset and validation guide

- **Contracts:** 50 locally generated PDFs. They include standard terms plus
  missing-GDPR, high-penalty, and low-cap scenarios.
- **Ledger:** 50 vendors and approximately 10,000 generated invoices in SQLite
  and CSV form. The generator emits 30 known vendor-month cap breaches, 15
  duplicate-payment signatures, and overdue clusters.
- **Ground truth:** `data/seed/anomaly_manifest.json` records known synthetic
  breach months, cap values, totals, and excesses. It is a test oracle, not an
  operational finding.
- **Agent regression suite:** 43 hand-authored cases cover legal, finance,
  cross-agent, uncertainty, and security behavior. The recorded history is
  30/43 before fixes, 41/43 after most fixes, then 43/43 current synthetic
  regression pass, including a fresh 43/43 run after the stricter entity gate.
- **Whole-ledger check:** `evaluation/evaluate_cap_detection.py` evaluates all
  eligible vendor-months and writes precision/recall/F1 and a confusion matrix.
  The recorded local result is 30/30 breaches detected, 0 false positives, 0
  false negatives, and 1,115 true negatives across 1,145 eligible
  vendor-months. Its deliberately invalid average-invoice baseline detects 0
  of 30 breach months. This is fixture consistency, not independent model
  validation.
- **Entity binding:** `evaluation/evaluate_vendor_identity_variants.py` tests
  the boundary between safe normalization and mandatory review. The current
  12-case fixture accepts 6 harmless exact-name forms and safely escalates 6
  typo/alias/subsidiary/partial-name forms; it is not a real master-data study.

## Delivery roadmap

### Completed foundation

- Synthetic data generation, SQLite seeding, and Prefect-capable local Chroma
  ingestion.
- Legal/finance LangGraph workflow with exact-vendor evidence gates,
  structured uncertainty handoffs, AST-validated read-only SQL, and
  deterministic cap computation.
- FastAPI, persistent local users/feedback, Streamlit dashboards, citations,
  SQL traces, and agent traces.

### Current demonstration hardening

1. Publish the evaluation history and limitations alongside the 43/43 result.
2. Batch-audit the entire seeded ledger against generated ground truth.
3. Show the deliberately flawed average-invoice baseline beside the cited
   contract-month calculation in the Cost Analytics proof view.
4. Make hosted versus local reasoning mode visible without exposing secrets.
5. Prepare a blinded review packet for an external reviewer without claiming
   that the generated fixture is independently sourced.

### Next evidence milestone

Before adding agents, SSO, or broader deployment infrastructure, use a small
legally usable held-out contract set:

1. Acquire 5–10 public or de-identified contracts with clear usage rights.
2. Annotate vendor/entity, cap, currency, effective dates, amendments, source
   span, and ambiguity with a documented protocol.
3. Keep a held-out subset untouched while tuning retrieval and extraction.
4. Report contract-term extraction, evidence citation, escalation, and failure
   results separately from the synthetic suite.

If spend remains synthetic for that exercise, describe the result precisely as
contract-extraction generalization—not commercial anomaly detection.

### Later maturity work

- Contract-version registry and approval workflow.
- Provider cost/latency benchmark across hosted and local models.
- Threat model, external security review, real identity/authorization, and
  audited persistence/migrations.
- Load testing and explicit service-level objectives.

## Reproducible execution

```powershell
# Validate the 43-case manifest without API calls
.\.venv\Scripts\python.exe evaluation\run_evaluation.py --dry-run

# Run local deterministic tests
Push-Location backend
..\.venv\Scripts\python.exe -m unittest discover -s tests -q
Pop-Location

# Measure every eligible vendor-month against the synthetic manifest
.\.venv\Scripts\python.exe evaluation\evaluate_cap_detection.py
```

For the full agent demonstration, start FastAPI and Streamlit after local
Chroma ingestion. Configure Gemini for hosted reasoning or Ollama for fully
local open-ended reasoning. Without either, the deterministic audit remains
available but open-ended chat interpretation is intentionally unavailable.

## Claims discipline

Say: “Proviso is a reproducible synthetic prototype for evidence-gated
contract-to-spend auditing.” Do not claim independent real-world accuracy,
savings, or fully offline conversational operation unless the held-out corpus
and local Ollama configuration have actually been demonstrated.
