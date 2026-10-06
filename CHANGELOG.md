# Changelog

All notable changes, one entry per version. Each version has a full page in
[docs/versions/](docs/versions/).

## [Unreleased]

Nothing yet. Next planned: version 0.3.0 (Checkpoint 3), discovery kit and pilot readiness.
Needs approval before starting.

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
