# 90-Second Evidence-Gated Demo

This is a focused technical demonstration. It leads with a policy failure and
safe refusal before showing the successful legal-to-finance calculation.

## Before starting

- Start the FastAPI service and Streamlit application using the local path in
  the root README.
- Confirm `data/enterprise_ledger.db` and `data/chroma_db` are present.
- Log in with the local demonstration account, then open **Proof View**.

## Script

1. **Expose the policy mistake (15 seconds).** In **Proof View**, the same
   vendor and ledger appear side by side. For *Mcclain, Miller and Henderson*,
   the average invoice is below the monthly contractual cap, while multiple
   calendar-month totals exceed it. State the policy explicitly: a monthly cap
   is evaluated against a monthly sum, not an average or a single invoice.

2. **Show safety before the happy path (25 seconds).** In **Chat**, run the
   **Ambiguous vendor → review** preset. The alias *M. M. Henderson* must not
   be silently mapped to the known vendor; the response should request a legal
   vendor identifier or approved alias evidence. Then point to the **Unverified
   amendment → review** preset: an asserted email amendment is not enough to
   change a cap. These are deliberate outcomes, not error states.

3. **Show the auditable decision when evidence is solid (40 seconds).** In
   **Chat**, use the **Check a known monthly-cap breach** preset or ask:

   ```text
   Are we overpaying Mcclain Miller and Henderson based on their contract terms?
   ```

   The expected path is Legal → Finance. The response must show the cited
   contract page and INR 456,170 monthly cap, read-only aggregation SQL, and
   four contract-valid breach months. The fixture contains these excesses:

   | Month | Monthly total | Excess over cap |
   | --- | ---: | ---: |
   | 2024-04 | INR 715,616.37 | INR 259,446.37 |
   | 2024-07 | INR 667,771.34 | INR 211,601.34 |
   | 2025-12 | INR 631,719.39 | INR 175,549.39 |
   | 2026-03 | INR 759,523.58 | INR 303,353.58 |

4. **Close on the boundary (10 seconds).** State that the demonstration proves
   a controlled evidence workflow: unknown aliases, unsupported amendments,
   and multiple cited cap values are review handoffs; only a single cited cap
   over a verified term receives a deterministic payment-compliance result.

## Claims to make—and not make

Say that the synthetic fixture validates a deterministic policy path,
read-only SQL controls, traceability, and explicit uncertainty handoff. Do not
claim real-company savings, calibrated production accuracy, legal advice, or
production-grade security. The full synthetic evaluation and its limits are in
[`evaluation/README.md`](../evaluation/README.md).
