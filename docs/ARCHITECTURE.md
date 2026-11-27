# Proviso Architecture

**Terms. Spend. Proof.** Proviso is a local-first, evidence-gated
contract-to-spend audit prototype. Its primary result is not a generic chat
answer: it is a traceable decision showing the cited contractual term, the
read-only ledger calculation, and the conditions under which the system refuses
to decide.

## Default local profile

```text
Streamlit UI
    │ authenticated REST request
    ▼
FastAPI
    │
    ├── SQLite: vendors, invoices, users, feedback
    ├── local Chroma: contract chunks + page/vendor metadata
    └── LangGraph
          ├── Legal: retrieve / cite / evidence-gate
          └── Finance: allow-listed read-only SQL
```

The optional Docker profile swaps the ledger for PostgreSQL and can use a
Chroma service. Prefect remains available for repeatable ingestion, but it is
not required for the local demonstration.

## Evidence-gated cap decision

For a contract-cap question, the route is deliberately bounded:

1. The supervisor sends the question to Legal.
2. Legal requires an unambiguous vendor identity, retrieves only that vendor's
   indexed contract evidence, and cites the monthly cap and contract term.
3. Finance builds a read-only, dialect-aware monthly aggregation from that
   cited cap—not from an invoice average.
4. The final report exposes source citations, executed SQL, agent trace, and
   the monthly breach/excess values.

The system declines to decide when the vendor is ambiguous or absent from the
corpus, retrieval is weak, the cap scope is underspecified, or a material
amendment/side letter is not in the indexed evidence.

## Data and persistence

- **Contracts:** 50 synthetic PDFs, ingested into persistent local Chroma.
- **Ledger:** synthetic vendors and invoices in `data/enterprise_ledger.db`.
- **Application state:** users and feedback live in the same local SQLite
  database. Startup creates/migrates the small `users` and `feedback` tables
  and seeds the local demonstration account (`admin` / `admin123`).
- **Feedback:** chat ratings are submitted to the protected API and can be
  viewed in XAI Explorer. They are stored for review only; there is no
  automatic model training loop.

## Security boundary

The Finance agent accepts a single allow-listed read-only SQL statement after
AST validation. SQLite connections use `PRAGMA query_only`; the Docker profile
configures a PostgreSQL read-only role. JWT protects query and feedback routes,
and SlowAPI applies per-client request limits.

These controls are prototype safeguards, not a production security
certification. Proviso does not provide enterprise identity management,
immutable audit retention, legal advice, or calibrated production accuracy.

## Evaluation

`evaluation/cases.json` contains synthetic correctness, security, and
uncertainty fixtures. The local calibration report demonstrates why similarity
score alone is insufficient for unknown vendors; exact metadata entity gating
is required before a contract can be treated as evidence.
