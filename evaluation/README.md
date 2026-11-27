# Evaluation Suite

This directory makes the synthetic dataset an explicit **validation fixture**,
not evidence of business ROI. The 43 cases in `cases.json` are hand-authored
expectations over known contract and ledger facts, including adversarial and
security cases.

## What it measures

- **Case pass rate**: all declared checks for a case pass.
- **Routing accuracy**: the trace includes each expected specialist.
- **Citation recall@k**: the expected vendor occurs in the returned citations.
- **SQL trace coverage**: a successful finance response exposes SQL over the
  expected tables and reports no execution error.
- **Answer-assertion coverage**: required terms appear in the returned answer.
- **Safety rejection rate**: intentionally destructive user inputs return the
  expected HTTP rejection status.
- **Human-review handoff rate**: ambiguous, conflicting, or unsupported
  material claims produce a structured escalation instead of a compliance
  conclusion.
- **Unsafe uncertainty-conclusion rate**: an adversarial uncertainty case
  returns a confident substantive conclusion instead of a handoff.
- **Clear-case false-abstention rate**: a case with declared evidence is
  needlessly escalated. This is a useful counterweight to a system that simply
  hands everything off.

`sql_trace_coverage` is deliberately not called SQL semantic accuracy. The
current API does not return structured result sets, so a human review of the
saved per-case results is still required for wrong-but-plausible SQL. Add
canonical result assertions before making a SQL-correctness claim.

## Confidence-handoff finding

The legal agent does not treat a retrieved passage as enough evidence by
default. It escalates when the top retrieval score is below the configured
threshold, when no passage is found, or when a question relies on a document
outside the indexed corpus (for example, an alleged amendment, draft, waiver,
or side letter). The supervisor also declines an underspecified cap decision
before routing unrelated contracts and invoices.

Each escalation contains a machine-readable reason code and recommended action.
The 20 `adversarial_uncertainty` cases exercise unknown vendors, missing scope,
unverified amendments, conflicting contract versions, and ambiguous vendor
aliases. Unknown named vendors are explicitly gated by exact metadata entity
resolution: semantic similarity cannot be treated as proof that a different
vendor's contract applies. These are policy checks over a synthetic corpus;
they are not a calibrated probability-of-correctness claim.

## Financial ground truth

`data/seed/anomaly_manifest.json` is generated with the invoices. It defines a
monthly-cap breach as the sum of one vendor's invoices in a contract-valid
calendar month exceeding that contract's monthly cap. The `monthly-cap-v2`
fixture deliberately contains 30 observed breach periods and 15 exact duplicate
payment signatures. Do not use average invoice amount as a cap-compliance
metric; the finance agent rejects that SQL shape for cap questions.

### Whole-ledger cap-detection check

Run the deterministic audit against every contract-valid vendor-month in the
seeded SQLite ledger:

```powershell
python evaluation/evaluate_cap_detection.py
```

The script compares a fresh aggregation with the generator's manifest, emits a
timestamped JSON artifact under `evaluation/results/`, and reports the complete
confusion matrix plus precision, recall, F1, specificity, prevalence, and
exact cap/total/excess-detail matches. Use `--invoices-csv` to validate the CSV
fixture before database seeding, or `--dry-run` to print metrics without
writing an artifact.

This is deliberately called **synthetic fixture consistency**, not a
generalization benchmark: the data generator and the policy labels belong to
the same controlled testbed. It catches regressions in grouping, effective-date
filtering, cap comparisons, and seeded-ledger drift; it does not establish
real-world detection precision or recall.

The recorded [whole-ledger result](results/cap-detection-20260724T181359Z.json)
found all **30/30** manifest breaches with **0** false positives and **0** false
negatives across **1,145** eligible vendor-months (1,115 true negatives). Its
precision, recall, F1, specificity, and cap/total/excess-detail match rate are
all 1.0 for this controlled fixture.

The same report measures the deliberately invalid baseline shown in Proof
View: comparing a vendor's mean contract-valid **invoice** with its **monthly**
cap. It identifies **0** breaches, yielding 30 false negatives, 0 false
positives, and recall/F1 of 0.0. The baseline is included to make the units
error measurable, not to suggest it is an acceptable compliance policy.

### Vendor-identity boundary check

Run the metadata gate against explicit naming variants without using an LLM or
semantic retrieval:

```powershell
python evaluation/evaluate_vendor_identity_variants.py
```

The [recorded result](results/vendor-identity-20260724T181359Z.json) passes all
12 hand-authored boundary cases: 6/6 punctuation/conjunction or exact-name
forms bind to the intended legal vendor, and 6/6 typos, conflicting legal
suffixes, DBA/subsidiary references, partial names, or initials safely escalate.
Its unsafe-binding and unresolved rates are both 0.0 for this manifest.

This is not fuzzy entity resolution. Proviso deliberately prefers a review
handoff to guessing. A real deployment would need a governed, auditable alias
registry for DBAs, subsidiaries, renames, and approved master-data mappings.

### Effective-dated cap-change guard

The deterministic cap policy supports one cited monthly cap over one verified
term. Unit regressions supply two cap values for the same vendor and verify
that Legal and Finance both return the structured
`ambiguous_effective_dated_cap_terms` handoff before any SQL or model call.
The current 43-case live API corpus intentionally contains one executed cap per
vendor, so this is a policy-guard regression rather than a claim of completed
mid-month proration support. A future version needs a versioned cap schedule,
invoice-period allocation rule, and a separate temporal fixture before it can
make that calculation.

## Run

Start the API with a populated Chroma collection and a configured LLM, then:

```powershell
python evaluation/run_evaluation.py
```

The runner spaces requests by 2.1 seconds by default so a full run stays below
Proviso's 30-requests-per-minute API limit. Override it with
`--delay-seconds` only when evaluating against a differently configured limit.

For a reproducible subset that does not need open-ended legal analysis, select
one or more categories:

```powershell
python evaluation/run_evaluation.py --category finance_ground_truth --category cross_agent_ground_truth
python evaluation/run_evaluation.py --category adversarial_uncertainty
```

Use `--dry-run` to validate the manifest without calling the API. Results are
written to `evaluation/results/` and should be committed or attached to a
release only with their model, embedding, and dataset version recorded.

To inspect the retrieval policy without calling an LLM or API, run the local
fixture calibration report after the Chroma index is populated:

```powershell
python evaluation/calibrate_retrieval.py
```

It measures the trade-off at several thresholds between retrieval coverage for
clear named-vendor fixtures and raw semantic retrieval for unknown vendors. It
also reports the evidence-gated result after exact entity resolution; this makes
visible why a score threshold alone is insufficient. Use `--dry-run` to check
the fixture wiring only. This is sensitivity analysis over hand-authored
synthetic cases, not statistical calibration on an independent test set.

### Recorded local fixture result

The latest local run at the configured `0.40` threshold is saved as
[`retrieval-calibration-20260720T181157Z.json`](results/retrieval-calibration-20260720T181157Z.json).
Across its 15 clear named-vendor and 4 unknown-vendor probes, clear-vendor
retrieval coverage was 100%. Raw semantic retrieval safely handed off 0% of
unknown vendors (all four retrieved a plausible but wrong contract); the exact
metadata entity gate handed off 100% of those four. This is a useful synthetic
failure-mode finding, not a claim of production accuracy.

### Blinded independent-review packet

[`independent_review/`](independent_review/) contains 12 mixed cases without
the authored expected-answer key and a standardized review sheet. An external
reviewer can independently inspect the supplied synthetic contracts and ledger,
then return their annotations for an agreement comparison. No independent
review has yet been completed or claimed. The packet improves independence of
expectation review; it does not convert the synthetic corpus into external
ground truth.

### Recorded API subset results

The following live API runs were completed after the deterministic finance,
cross-agent, and evidence-handoff routes were hardened. They use the local
SQLite ledger and Chroma collection; these categories do not depend on an
open-ended Gemini answer.

- [Finance + cross-agent fixtures: 10/10 pass](results/evaluation-20260720T190729Z.json)
  — 100% routing, citation recall, SQL-trace coverage, and answer assertions.
- [Adversarial uncertainty fixtures: 20/20 pass](results/evaluation-20260720T190735Z.json)
  — 100% structured handoff rate and 0% unsafe uncertainty conclusions.
- [Adversarial security fixtures: 3/3 pass](results/evaluation-20260720T190844Z.json)
  — 100% destructive-input rejection.

### Recorded full-run history

| Run | Result | What changed or affected it |
| --- | --- | --- |
| [Initial full baseline](results/evaluation-20260720T190116Z.json) | 30/43 (69.8%) | Before the later routing and deterministic-policy fixes; Gemini free-tier requests also became resource-exhausted. This is not an isolated A/B measurement of exact vendor identity gating. |
| [First post-fix full run](results/evaluation-20260721T174901Z.json) | 41/43 (95.3%) | Most fixes had landed; two late-payment-penalty routing failures remained. |
| [Initial post-fix full run](results/evaluation-20260721T175212Z.json) | 43/43 (100%) | Synthetic regression suite passed after the remaining routing correction. |
| [Post entity-gate run](results/evaluation-20260724T181747Z.json) | 43/43 (100%) | Fresh full API regression after legal-suffix/subsidiary-safe entity binding was added. |
| [Post temporal-cap-guard run](results/evaluation-20260724T183306Z.json) | 43/43 (100%) | Fresh full API regression after the multiple-cap escalation safeguard was added. |

### Current full API result

The [latest post-temporal-cap-guard full suite](results/evaluation-20260724T183306Z.json) completed
all **43/43** synthetic cases at a rate-safe 2.3-second request cadence:

- 100% case pass, routing, citation recall, SQL-trace coverage, and answer assertions.
- 100% security rejection and structured uncertainty handoff.
- 0% unsafe uncertainty conclusions and 0% false abstentions on clear cases.

This is a regression result over the generated fixture corpus, not independent
real-world validation or a production-accuracy claim.

## Limitation

This suite tests known synthetic facts. It demonstrates regression detection
and safety behavior; it does **not** demonstrate performance on independently
collected enterprise contracts. Do not convert its outcomes into ROI claims.
