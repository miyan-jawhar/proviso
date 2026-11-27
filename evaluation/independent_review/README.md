# Blinded independent-review packet

This packet allows a reviewer who did **not** author Proviso's evaluation
manifest to independently assess a small, mixed subset of synthetic cases.
It is deliberately separated from `evaluation/cases.json` and prior result
files, which contain the project author's expected outcomes.

## What this can and cannot establish

The review provides an independent annotation check over the existing synthetic
fixture. It can reveal whether Proviso's authored expectation labels agree with
an outside reviewer who inspected the source materials. It does **not** make
the generated contracts or invoices an independent real-world dataset, and it
must not be presented as such.

## Reviewer procedure

1. Do not open `evaluation/cases.json`, `evaluation/results/`, or the original
   evaluation README until the worksheet is submitted.
2. Read [blinded_cases.json](blinded_cases.json). It intentionally contains no
   expected answer, routing, or source-vendor key.
3. Use only the source material stated in the packet:
   - contracts in `data/contracts/`;
   - vendor/term metadata in `data/contracts_metadata.json` when locating a
     contract; and
   - `data/enterprise_ledger.db` or `data/seed/invoices.csv` for ledger facts.
4. For each case, complete the standardized fields in
   [review_sheet.csv](review_sheet.csv). Record `answer`, `human_review`, or
   `blocked` as the expected behavior. Use `not_applicable` where a field is
   irrelevant.
5. Return the completed sheet to the project owner. The project owner may then
   compare it with the hidden manifest and publish the agreement calculation,
   reviewer role, review date, disagreements, and any adjudication notes.

Do not substitute an LLM answer for source review. A different LLM can be a
supplementary writing-quality check, but it is not independent factual ground
truth for a legal or financial decision.

## Required reporting if used

Report the number of completed cases, reviewer relationship/role, exact source
materials, field-level agreement, disagreements, and whether any manifest
expectations were changed after review. Do not imply that an unreviewed packet
is an independent evaluation result.
