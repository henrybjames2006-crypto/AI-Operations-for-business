# Evaluation

`python -m opsapp eval run` runs every case in `src/opsapp/evals/cases.py` against a fresh
in-memory database with the fictional seed data and a fixed clock, and writes
`reports/evaluation.md` and `reports/evaluation.json` (git-ignored).

## The case set (61 invented cases)

| Category | Cases | What is checked |
| --- | --- | --- |
| complete | 12 | customer, items, quantities, timeframe, final state, exact quote total |
| missing_info | 6 | the missing fact becomes a question; no quote yet |
| ambiguous_customer | 6 | the customer is asked for when it cannot be decided from records |
| invalid_quantity | 5 | zero, negative, fractional and out-of-range quantities are questioned |
| unsupported_service | 5 | non-catalog work is flagged, never priced |
| manipulation | 6 | instruction-like text is flagged and has no effect on the price |
| approval_edit | 7 | edits void approvals; stale, self and wrong-role approvals are refused |
| adapter_failure | 3 | retries, then Failed after the attempt limit |
| timeout_uncertain | 4 | timeouts and lost responses reconcile without duplicates; unknown escalates |
| duplicate | 3 | repeated submissions and double approvals create one workflow and one send |
| cross_tenant | 4 | another tenant's owner gets Not found for view, approve, cancel, answer |

Expected totals were worked out by hand from the pricing rules, independently of the code.

## Latest result (deterministic baseline, 2026-10-05)

| Metric | Result |
| --- | --- |
| Cases passed | 61/61 |
| Extraction field accuracy | 200/200 |
| Quote correctness (exact cents) | 17/17 |
| Unauthorized or invalid actions blocked | 11/11 |
| Duplicate scenarios handled | 3/3 |
| Escalation rate | 1 of 61 workflows (the status-unknown case, by design) |
| AI cost | $0 (mock only) |
| Runtime | about 4 s |

**How to read this.** The mock extractor's rules were written alongside these cases, so the
extraction score only shows that the rules cover the cases they were built for. It does not
predict performance on new wording or real requests. The pricing, approval, failure,
duplicate and tenant results test fixed rules and are the meaningful part. Review time and
correction effort cannot be measured on synthetic data. Event timestamps are in the audit
log, but the app does not yet report review time; that belongs with a pilot.

Checkpoint 2 would add a real model adapter (paid; needs approval) and report both columns
side by side on this set plus new held-out cases written without looking at the rules.
