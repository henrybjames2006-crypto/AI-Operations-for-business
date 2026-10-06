# Changelog

All notable changes, one entry per version. Each version has a full page in
[docs/versions/](docs/versions/).

## [Unreleased]

Nothing yet. Next planned: Checkpoint 4, real sign-in and hardening. Needs approval before
starting.

## [0.3.0] - 2026-10-06 (Checkpoint 3)

Full details: [docs/versions/v0.3.0.md](docs/versions/v0.3.0.md)

### Added
- Discovery kit in `docs/discovery/`: hypotheses to test, interview guide with consent
  wording, notes template, interview tracker and labelling guide.
- `python -m opsapp redact`: copies sample requests into a new folder with emails, phone
  numbers, addresses, postcodes, links, IP addresses, secrets and listed names replaced.
  Offline, no AI, originals untouched.
- `eval run --samples` and `eval compare --samples` to score the readers on redacted,
  labelled real requests.
- Price list import from CSV on the Catalog page: strict checks with one message per bad
  row, saved as a draft pricing version, applied only on approval. Template download and
  a discard button for drafts.
- Measurements page and CSV: time per step from the audit log, and how often people kept,
  filled in or changed what the reader proposed.
- Pilot checklist (`docs/pilot-checklist.md`).

### Changed
- A reader that gives a customer's full listed name is accepted when the request uses one
  of that customer's aliases (the "Summit Bakery" case from the 0.2.0 comparison). The AI
  prompt now asks for the name as written in the request.
- A site label that is part of a customer's name is no longer taken as the site.
- The demo guide says that only the owner runs the dispatcher from the Simulation page.
- Database migration 0002 adds the pending catalog changes of imported pricing versions.

### Known limitations
- No real customer data in the app yet; real sign-in and hosting are Checkpoint 4.
- Redaction is pattern-based: names not in the list and unusual formats are missed.
- Rule-based reader: 17 of 30 held-out cases fully correct (the site fix was prompted by a
  held-out case, so this one is no longer an unseen result).
- The AI comparison was not re-run after the name fix.

## [0.2.0] - 2026-10-05 (Checkpoint 2)

Full details: [docs/versions/v0.2.0.md](docs/versions/v0.2.0.md)

### Added
- Optional Claude request reader (`OPSAPP_AI_PROVIDER=anthropic`), off by default, with a
  fixed JSON output schema and the same guard as the rule-based reader.
- App budget, token limits, one retry for temporary failures, and fallback to the
  rule-based reader with the reason shown on the page and in the history.
- AI calls, tokens and estimated cost on each workflow page; failed calls recorded too.
- 30 held-out evaluation cases; reports split original and held-out results.
- `python -m opsapp eval compare` to compare both readers on accuracy and cost.
- New settings: `OPSAPP_AI_API_KEY`, `OPSAPP_AI_MODEL`, `OPSAPP_AI_BUDGET_USD`,
  `OPSAPP_AI_TIMEOUT_SECONDS`.

### Changed
- Customer and site names from any reader must appear in the request text.
- `eval run` exits with an error only when a safety scenario fails; reading mistakes are
  reported.
- Anthropic Python SDK added as a dependency.

### Known limitations
- Live AI comparison, one run with `claude-haiku-4-5` on synthetic cases: held-out 27 of 30
  fully correct (rules 16 of 30), but original cases 37 of 40 (rules 40 of 40). About $0.15
  for 70 calls. No AI miss produced a wrong price; misses led to asking the customer.
- The AI reader may fail to name a customer when the prompt's "exactly as listed" name is
  not in the text (inferred from one case, ambiguous-05).
- Rule-based reader: 16 of 30 held-out cases fully correct.
- A site name inside a company name is taken as the site.

## [0.1.0] - 2026-10-05 (Checkpoint 1)

Full details: [docs/versions/v0.1.0.md](docs/versions/v0.1.0.md)

### Added
- Request intake by paste or built-in samples, with duplicate detection.
- Rule-based request reading (no real AI) showing where each fact came from; flags
  unsupported work and ignores instructions hidden in customer text.
- Customer matching by email domain or exact name; asks when unclear.
- Clarification questions for missing customer, site, quantity or unsupported work.
- Traceable quotes from an approved pricing version, with per-line calculations, named
  pricing rules, versioning, and tax shown as not calculated.
- Approval locked to the exact quote and actions; the preparer can never approve; any edit
  voids approval.
- Simulated quote email and schedule proposal run by a separate dispatcher, with retries,
  lost-response reconciliation, escalation, and fault injection.
- Exceptions page, cancellation summary, full history, tamper-evident audit chain, and
  redacted JSON export.
- Four server-enforced roles and two isolated fictional companies.
- Migrations, backup and restore, environment-based settings, local-only server.
- 202 automated tests and a 61-case synthetic evaluation.

### Known limitations
- No real AI, email, calendar, payments or external systems.
- No tax calculation; appointment times are not checked against any calendar.
- Local user picker with no passwords; for use on your own computer only.
