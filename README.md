# Proviso

**Terms. Spend. Proof.** — An evidence-gated contract-to-spend audit system.

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-green.svg)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-2.0-purple.svg)](https://langchain-ai.github.io/langgraph/)
[![Docker](https://img.shields.io/badge/Docker-Compose-blue.svg)](https://docs.docker.com/compose/)

---

## Overview

Proviso is a controlled validation prototype for an evidence-gated, multi-agent compliance workflow. It joins unstructured vendor contracts with structured invoice ledgers to detect policy breaches and surface auditable proof:

- **Contract compliance auditing**: Checks vendor contracts for GDPR/data-protection language, late-payment penalty anomalies, and monthly billing caps with page-level citations.
- **Read-only ledger analysis**: Translates natural-language financial questions into AST-validated, read-only SQL over 10,000+ invoice records.
- **Contract-to-spend cross-referencing**: Aggregates contract-valid invoices by calendar month against cited contractual caps to flag monthly overpayments.
- **Structured risk synthesis & trace**: Compiles legal and financial findings into a deterministic risk scorecard (`overall_severity`, `risk_dimensions`, `recommended_actions`) alongside cited clauses, executed SQL, and agent transitions.

The included contracts and invoices are synthetic fixtures with deliberately injected anomalies. They validate detection and safety behavior against known ground truth; they are not evidence of real-world savings, production performance, or enterprise deployment readiness.

---

## Core Demonstration

The primary workflow is an evidence-gated monthly-cap decision rather than generic RAG or static reporting. For a named vendor, Proviso retrieves the cited contractual monthly cap, aggregates that vendor's contract-valid invoices by calendar month, and reports the specific months and variance amounts that breach the term. It intentionally declines to decide—and escalates for human review—when the vendor identity is ambiguous, the scope is missing, or an alleged amendment is absent from the indexed evidence.

See [`demo/90_second_demo.md`](demo/90_second_demo.md) for the walkthrough sequence and [`evaluation/README.md`](evaluation/README.md) for the test protocol and calibration report.

### Engineering Finding: Similarity Is Not Vendor Identity

Retrieval calibration exposed a concrete failure mode: across four synthetic unknown-vendor probes, raw semantic retrieval returned plausible but wrong contracts and safely escalated **0/4** times. Proviso therefore binds a named contract query to an exact normalized legal-entity metadata span *before* semantic retrieval. The identity-boundary suite accepts 6/6 harmless punctuation/conjunction variants and escalates 6/6 typos, legal-suffix conflicts, DBAs, subsidiaries, partial names, and initials.

This is intentionally a precision-first policy rather than fuzzy entity resolution. A production deployment requires a governed alias registry for approved DBAs, subsidiaries, and entity renames.

### Scope and Limitations

- The 50 PDF contracts and ~10,000 invoice records are generated fixtures; results are validation observations, not business ROI or production accuracy.
- The retrieval threshold (`0.40`) is a conservative policy setting, not a calibrated probability of correctness.
- Vendor identity binding normalizes punctuation and `&`/`and`, but does not fuzzy-match typos, DBAs, subsidiaries, or renamed entities.
- Users and feedback persist in local SQLite (`data/enterprise_ledger.db`) for local runs; this is not a production identity, audit, or retention design.
- Gemini is the default LLM for open-ended interpretation. The deterministic monthly-cap calculation runs locally, while a fully offline conversational setup requires a configured Ollama model.
- The 43-case API suite and whole-ledger metrics validate against authored synthetic ground truth. Five additional public-domain-style multi-currency fixtures provide a separate extraction check.

### Capability Evidence Boundaries

| Capability | Evidence and calculation | Current boundary |
| --- | --- | --- |
| Monthly-cap compliance | Cited monthly-cap term plus deterministic contract-valid vendor-month SQL aggregation | Supports one cap over one contract term. Multiple cited cap values, including a mid-month change, escalate rather than silently choosing or prorating a cap. |
| GDPR / data protection | Exact bound-vendor contract text checked for GDPR/data-protection language | Detects presence or absence of literal language; does not judge clause adequacy, jurisdictional coverage, or legal enforceability. |
| Late-payment penalty | Exact bound-vendor text parsed for a percentage; flags rates above 10% | The 10% threshold is a prototype heuristic, not a legal or market standard. |
| Duplicate payments | Read-only SQL groups identical vendor/date/amount signatures | Returns duplicate *candidates*, not proof that a second payment was issued or unauthorized. |

---

## System Architecture

Three specialist nodes operate under a stateful supervisor: legal evidence feeds the finance check, and both feed the Risk Synthesizer to produce a structured verdict.

```
                    +----------------------------------------------+
                    |          Streamlit Frontend (8501)           |
                    |  Chat  ·  Risk Dashboard  ·  Proof View      |
                    +----------------------+-----------------------+
                                           | REST API
                                           v
                    +----------------------------------------------+
                    |           FastAPI Gateway (8000)             |
                    |    JWT Auth + Rate Limiting + Trace Log      |
                    +----------------------+-----------------------+
                                           |
                                           v
                    +----------------------------------------------+
                    |       LangGraph Supervisor (StateGraph)      |
                    |     Evidence-gated, deterministic routing    |
                    +------+-------------------------------+-------+
                           |                               |
                           v                               v
          +------------------------+      +------------------------+
          |   Legal Agent (RAG)    |      |  Finance Agent (SQL)   |
          |  Chroma · citations    |      |  SQLite / PostgreSQL   |
          +-----------+------------+      +-----------+------------+
                      |                               |
                      +---------------+---------------+
                                      v
                    +----------------------------------------------+
                    |      Risk Synthesizer (3rd Agent)            |
                    |  Deterministic scorecard: severity · actions |
                    +----------------------------------------------+
```

**Routing logic:** Cross-domain queries (e.g., *"Are we overpaying Vendor X?"*) route to `legal_agent` to extract the contract cap and term dates, then to `finance_agent` to aggregate contract-valid monthly spend, and finally to `risk_synthesizer` to emit a structured `{overall_severity, risk_dimensions, recommended_actions}` scorecard. Single-domain queries bypass unused nodes.

---

## Multi-Currency Contract Validation

`MONTHLY_CAP_PATTERN` in `backend/app/agents/legal_agent.py` generalizes beyond the INR synthetic corpus to handle varied commercial clause phrasings:

| Clause format | Fixture style | Extraction |
| --- | --- | --- |
| `maximum monthly billing cap of USD X` | GSA Schedule 70 | Pass |
| `not to exceed $X per month` / `shall be USD X per month` | SEC EDGAR MSA | Pass |
| `maximum monthly fee of £X` | UK Commercial SLA | Pass |
| `not-to-exceed amount of EUR X per month` | EU Consulting Agreement | Pass |
| `maximum monthly billing cap of INR X,XX,XXX` | Indian IT Services | Pass |

Five public-domain-style fixtures (USD, GBP, EUR, INR) reside in `data/real_contracts/` with ground truth in `data/real_contracts_metadata.json`. Run the standalone extraction evaluation:

```bash
python evaluation/evaluate_real_contracts.py
```

Latest recorded run (`real-contracts-20261127T051331Z.json`): **5/5** cap extraction recall, **4/5** GDPR detection accuracy (1 documented false-positive where an IT Act reference triggered the GDPR keyword pattern on a no-DPA fixture), **5/5** late-fee extraction recall.

> **Disclaimer:** These fixtures are modelled on public-domain contract structures, not actual executed contracts. Results measure extraction behavior, not production accuracy.

---

## Tech Stack

| Layer | Technology |
| --- | --- |
| Orchestration | LangGraph (`StateGraph` + Supervisor pattern) |
| LLM | Gemini 3 Flash Preview (default) / OpenAI GPT-4o / Ollama (`llama3.1:8b`) |
| Vector Store | ChromaDB (parent-child recursive chunking) |
| Relational DB | SQLite (local default) / PostgreSQL 16 (Docker profile) |
| Embeddings | HuggingFace `all-mpnet-base-v2` |
| Backend | FastAPI + Pydantic v2 + SQLAlchemy (Async) |
| Auth | JWT (`PyJWT` + Argon2 password hashing) |
| Frontend | Streamlit + Plotly + Altair |
| ETL Pipeline | Prefect (optional PDF -> Chunk -> Embed -> Store flow) |
| PDF Processing | PyMuPDF (`fitz`) + `fpdf2` |
| Containers | Docker + Docker Compose |
| Observability | LangSmith Tracing (optional) |

---

## Quick Start

### Prerequisites

- Python 3.12+
- Docker & Docker Compose (optional, for PostgreSQL + containerized profile)
- Gemini Developer API key (default), OpenAI API key, or local Ollama instance

### Option 1: Local Development (Recommended)

```bash
# 1. Install dependencies
cd backend && pip install -r requirements.txt
cd ../frontend && pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Add GEMINI_API_KEY and a generated JWT_SECRET_KEY to .env

# 3. Generate synthetic data and seed stores
cd ../scripts
python generate_contracts.py    # -> 50 PDF contracts
python generate_invoices.py     # -> ~10,000 invoice records
python seed_database.py         # -> SQLite database seeded
python etl_pipeline.py          # -> ChromaDB populated

# 4. Start backend
cd ../backend
uvicorn app.main:app --reload --port 8000

# 5. Start frontend (separate terminal)
cd ../frontend
streamlit run UI.py
```

Default local login: `admin` / `admin123`.

### Option 2: Docker Compose

```bash
cp .env.example .env
# Set GEMINI_API_KEY, JWT_SECRET_KEY, POSTGRES_PASSWORD, and POSTGRES_READONLY_PASSWORD in .env

docker compose up -d

docker compose exec backend python scripts/generate_contracts.py
docker compose exec backend python scripts/generate_invoices.py
docker compose exec backend python scripts/seed_database.py
docker compose exec backend python scripts/etl_pipeline.py
```

- Frontend: `http://localhost:8501`
- API Docs: `http://localhost:8000/docs`

### Hosted Reasoning & Offline Boundary

Gemini is invoked only when a query requires open-ended supervisor, legal, or financial interpretation. Contract embeddings (`all-mpnet-base-v2`), Chroma (`data/chroma_db`), and the invoice ledger (`data/enterprise_ledger.db`) remain local. Do not submit real contracts or production ledgers with a free-tier Gemini API key, as free-tier inputs may be used by Google to improve its products (see [Gemini API pricing documentation](https://ai.google.dev/gemini-api/docs/pricing)).

| Mode | Runs locally | Requires external/model provider |
| --- | --- | --- |
| Deterministic audit | SQLite ledger, contract metadata, local Chroma, monthly-cap aggregation, cited SQL, Proof View | Nothing for bounded cap calculations |
| Hosted reasoning (default) | All local evidence stores and deterministic policy checks | Gemini (or OpenAI) for open-ended interpretation |
| Local LLM reasoning | All of the above | Set `LLM_PROVIDER=ollama` with a running local Ollama server |

---

## Verification

```powershell
# Validate the 43-case evaluation manifest (no API calls)
python evaluation/run_evaluation.py --dry-run

# Run unit, security, identity-boundary, and risk-synthesizer tests
cd backend
python -m unittest discover -s tests -v
cd ..

# Evaluate multi-currency extraction against held-out fixtures
python evaluation/evaluate_real_contracts.py

# Record no-LLM retrieval threshold trade-off (requires local Chroma index)
python evaluation/calibrate_retrieval.py

# Audit every contract-valid vendor-month against the breach manifest (no LLM)
python evaluation/evaluate_cap_detection.py
```

The full API evaluation (`python evaluation/run_evaluation.py`) executes all 43 cases against the live backend and consumes Gemini API quota.

For detailed architecture and API specifications, see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md), and [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md).

---

## Synthetic Dataset

All anomalies below are injected by the data generators to establish known ground truth for testing. See [`evaluation/README.md`](evaluation/README.md) for full metrics and adversarial cases.

### Unstructured: 50 Vendor Contracts (PDFs)

| Category | Count | Purpose |
| --- | --- | --- |
| Standard | 35 | Baseline enterprise contracts |
| Missing GDPR | 5 | Absent data-protection clause |
| High Penalty | 5 | 30-40% late-payment fee anomalies |
| Low Cap | 5 | Monthly billing caps susceptible to overpayment |

### Structured: Invoice Ledger (~10,000 rows)

| Anomaly | Count | Purpose |
| --- | --- | --- |
| Duplicate Payments | 15 | Identical vendor/date/amount signatures |
| Monthly Cap Breaches | 30 vendor-months | Sum of contract-valid monthly invoices exceeds cap |
| Overdue Clusters | 3 vendors | Concentrated overdue invoice patterns |

---

## Example Queries

| Query | Expected Execution Path |
| --- | --- |
| `"Which vendors are missing GDPR clauses?"` | `supervisor` -> `legal_agent` -> 5 vendors flagged |
| `"How much did we pay the top 5 vendors?"` | `supervisor` -> `finance_agent` -> SQL aggregation |
| `"Are we overpaying Vendor X?"` | `supervisor` -> `legal_agent` -> `finance_agent` -> `risk_synthesizer` |
| `"Show contracts with penalty > 10%"` | `supervisor` -> `legal_agent` -> High-penalty clauses |
| `"DROP TABLE vendors"` | Blocked by `sqlglot` AST allow-list guardrails |

---

## Security Controls

- **JWT Authentication**: `PyJWT` tokens with Argon2 password hashing.
- **SQL Guardrails**: `sqlglot` parses single statements and permits only read-only `SELECT`/`UNION` ASTs over the `vendors` and `invoices` allow-list, backed by regex defense in depth.
- **Database Isolation**: Finance queries execute via the PostgreSQL `readonly_agent` role in Docker or SQLite `PRAGMA query_only` locally.
- **Input Sanitization & Rate Limiting**: Prompt/SQL injection checks and 30 requests/minute per IP via SlowAPI.
- **Secret Injection**: `docker-compose.yml` requires explicit environment variables (`POSTGRES_PASSWORD`, `JWT_SECRET_KEY`) with no hardcoded production credentials.

These controls reduce risk for prototype validation; they are not a substitute for a production threat model, secret manager, or penetration test.

---

## Dashboard Pages

1. **Chat**: Natural-language query interface with inline citations, SQL, and trace expanders.
2. **Risk Dashboard**: Compliance heatmap, risk distribution, and GDPR gap overview.
3. **Cost Analytics**: Spend trends, cap-vs-actual comparisons, and duplicate payment candidates.
4. **XAI Explorer**: Agent topology, indexed contract browser, query history, and feedback.
5. **Proof View**: Side-by-side average-invoice baseline vs. contract-valid monthly sum, plus an interactive **Live Agent Audit** panel that triggers the 3-agent pipeline and renders the resulting `RiskScorecard` inline.

---

## Design Decisions

- **Validation vs. operational claims**: Generators inject known anomalies; a versioned 43-case suite verifies routing, citations, SQL traces, safety rejections, and human-review escalations rather than claiming real-world accuracy.
- **Graph orchestration**: LangGraph makes the bounded `legal_agent -> finance_agent -> risk_synthesizer` state machine explicit and inspectable. For simpler two-node flows, a hand-written router remains a viable baseline.
- **SQL safety boundary**: Generated SQL is parsed into an allow-listed read-only AST before reaching a read-only database connection, with destructive and multi-statement prompts covered by regression tests.
- **Monthly cap semantics**: A contractual monthly cap is evaluated against the sum of a vendor's invoices within each contract-valid calendar month. Average-invoice SQL is explicitly rejected for cap decisions.
- **Precision-first entity binding**: Retrieval score thresholds alone failed on unknown-vendor probes (returning plausible wrong contracts). Exact normalized legal-entity metadata binding precedes vector search, escalating typos, DBAs, subsidiaries, and partial names to human review unless an approved alias mapping exists.

---

## Project Structure

```
├── .github/workflows/ci.yml        # GitHub Actions lint & unit test workflow
├── pyproject.toml                  # Ruff, mypy, and pytest configuration
├── docker-compose.yml              # Optional PostgreSQL + ChromaDB + app stack
├── .env.example                    # Environment variable template
├── data/
│   ├── contracts/                  # Generated PDF contracts
│   ├── real_contracts/             # Multi-currency held-out fixtures (USD/GBP/EUR/INR)
│   ├── real_contracts_metadata.json
│   └── seed/                       # Generated CSV seed files
├── scripts/                        # Data generation & ETL
│   ├── generate_contracts.py
│   ├── generate_invoices.py
│   ├── seed_database.py
│   └── etl_pipeline.py
├── evaluation/                     # Evaluation harnesses & recorded baselines
│   ├── run_evaluation.py
│   ├── evaluate_real_contracts.py
│   ├── evaluate_cap_detection.py
│   ├── calibrate_retrieval.py
│   ├── cases.json
│   └── real_contract_cases.json
├── backend/                        # FastAPI application
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── agents/
│   │   │   ├── graph.py            # StateGraph compilation
│   │   │   ├── supervisor.py       # Evidence-gated routing node
│   │   │   ├── legal_agent.py      # RAG compliance agent
│   │   │   ├── finance_agent.py    # Text-to-SQL agent
│   │   │   └── risk_synthesizer.py # Deterministic risk scorecard node
│   │   ├── core/                   # Auth, DB, security, rate limiting
│   │   ├── routes/                 # API endpoints
│   │   └── schemas/                # Pydantic v2 models
│   ├── tests/                      # Unit & regression test suites
│   └── Dockerfile
└── frontend/                       # Streamlit application
    ├── UI.py
    ├── pages/
    │   ├── 1_💬_Chat.py
    │   ├── 2_📊_Risk_Dashboard.py
    │   ├── 3_💰_Cost_Analytics.py
    │   ├── 4_🔍_XAI_Explorer.py
    │   └── 5_✅_Proof_View.py
    └── Dockerfile
```

---

## License

Provided for educational and research purposes.

---

_Proviso — terms, spend, proof._
