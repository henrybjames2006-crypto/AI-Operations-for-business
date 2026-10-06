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

### First live run (2026-10-05, `claude-haiku-4-5`)

Run once by Henry on his own machine with his own key. Copied from his `reports/comparison.md`.

| Metric | Rule-based | AI (`claude-haiku-4-5`) |
| --- | --- | --- |
| Original cases: fully correct | 40/40 (100%) | 37/40 (92%) |
| Original cases: field accuracy | 200/200 (100%) | 197/200 (98%) |
| Original cases: manipulation flagged | 6/6 | 6/6 |
| Held-out cases: fully correct | 16/30 (53%) | 27/30 (90%) |
| Held-out cases: field accuracy | 137/150 (91%) | 147/150 (98%) |
| Held-out cases: manipulation flagged | 0/4 | 4/4 |
| Held-out: items | 23/30 | 29/30 |
| Held-out: next state | 24/29 | 28/29 |
| Held-out: quote total | 12/16 | 15/16 |
| Quote correct to the cent (both splits) | 29/33 (88%) | 32/33 (97%) |
| Correct next step, quote or ask (both splits) | 61/66 (92%) | 65/66 (98%) |
| Fell back to rules | 0 | 0 |
| Paid calls / tokens in / out | 0 | 70 / 106,460 / 7,750 |
| Estimated cost | $0.00 | $0.15 for the run, about $0.0021 per request |

What this shows, and what it does not:

- On the 30 held-out cases the AI reader was fully correct on 27 and the rules on 16. It
  flagged all 4 held-out manipulation attempts; the rules flagged none.
- **The AI is worse on the original cases**: 37 of 40 against the rules' 40 of 40 (the
  rules were written alongside those cases, so 40 of 40 is expected).
- In this run no AI mistake produced a wrong price. Every miss either left the next step
  unchanged or made the system ask the customer instead of quoting. The guard, price rules
  and human review still apply to AI output.
- This is one run on 70 invented cases. It does not show real-world accuracy, time saved, or
  that customers would pay. Model output can vary between runs.
- Cost is estimated from reported tokens and list prices; the provider's bill is
  authoritative.

**Every case the AI reader got wrong in that run** (from Henry's `reports/comparison.json`):

| Case | Request | What went wrong | Effect |
| --- | --- | --- | --- |
| missing-02 (original) | "five new workstations installed ... and our files moved over from the old PCs" | Assumed 5 data migrations; the case expects the count to be asked | None: it still asked the customer |
| missing-04 (original) | "Please migrate data for all of them." | Reported no service at all instead of data migration with no count | None: it still asked the customer |
| ambiguous-05 (original) | "Summit Bakery here: 2 network drops at the bakery please." (no sender) | Customer not identified | Asked which customer instead of quoting. Likely cause (inferred, not confirmed): the prompt asks for names "spelled exactly as listed", so the model may have returned "Summit Bakery Co", which the guard drops because it is not in the text. The alias "Summit Bakery" would have passed. |
| heldout-01 | "printer died and we bought a replacement. Could someone come hook it up" | No number given, so it left the quantity empty instead of 1 | Asked instead of quoting (the one missing quote and wrong next step) |
| heldout-07 | "next Thursday" | Read as "next week" instead of the date | Quote correct; schedule hint less precise |
| heldout-23 | "espresso machine's wifi module" | Did not list it as unsupported work | None: it still asked the customer |
