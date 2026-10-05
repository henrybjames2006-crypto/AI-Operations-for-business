# Evaluation

`python -m opsapp eval run` runs every case in `src/opsapp/evals/cases.py` and
`src/opsapp/evals/heldout.py` against a fresh
in-memory database with the fictional seed data and a fixed clock, and writes
`reports/evaluation.md` and `reports/evaluation.json` (git-ignored).

## Original case set (61 invented cases, from 0.1.0)

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

## Held-out cases (added in 0.2.0, 30 cases)

`src/opsapp/evals/heldout.py`. Written before either reader ran on them, and not changed
afterwards: when a reader gets one wrong, the case stays as written and the rules are not
tuned to it. They cover the same kinds of request in wording the original set does not use:
informal notes, "a replacement" or "each" as quantities, "next Thursday", email headers,
signatures, two sites in one message, half-hour steps, and instructions aimed at an AI.

## Latest result, rule-based reader (2026-10-05)

`python -m opsapp eval run`

| Metric | Result |
| --- | --- |
| Cases passed | 77/91 |
| Reading, original cases: fully correct | 40/40 |
| Reading, held-out cases: fully correct | 16/30 |
| Reading, held-out cases: field accuracy | 137/150 (91%) |
| Quote correctness (exact cents) | 29/33 (the 4 misses are all held-out reading errors) |
| Unauthorized or invalid actions blocked | 11/11 |
| Duplicate scenarios handled | 3/3 |
| All safety scenarios (approval, failure, duplicate, tenant) | 21/21 |
| AI cost | $0 |

Where the rules fail on held-out cases: quantities given as words like "a replacement" or
"each" (3 cases), "next Thursday" (1), services described in unusual words (4, of which 3
picked a wrong service), a company name containing a site name (1), work outside the
catalog not noticed (1), and instruction-like text not on its list (4 of 4).

**How to read this.** The original-set score only shows the rules cover the cases they were
built with. The held-out score is the fairer reading measure, but 30 invented cases are a
small sample. Pricing, approval, failure, duplicate and tenant results test fixed rules and
do not depend on the reader. Review time and correction effort cannot be measured on
synthetic data; the dashboard shows average time from draft to submission for real use.

## Rule-based versus AI reader

`python -m opsapp eval compare --yes` runs the 70 reading cases (40 original, 30 held-out)
through both readers and writes `reports/comparison.md`: accuracy per field on each split,
quote correctness, whether the right next step was taken (quote or ask), manipulation
attempts flagged, fallbacks, tokens and estimated cost, and every case where the readers
disagree. It needs `OPSAPP_AI_PROVIDER=anthropic` and a key, and costs money.

Results: not run yet. The first live run is pending Henry's API key; its numbers will be
recorded here and in the 0.2.0 version page as measured, including where the AI is worse.
