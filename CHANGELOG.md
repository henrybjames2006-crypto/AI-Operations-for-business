# Changelog

All notable changes, one entry per version. Each version has a full page in
[docs/versions/](docs/versions/).

## [Unreleased]

Nothing yet. Next planned: version 0.2.0 (Checkpoint 2), a real AI model for request
reading compared against the rule-based baseline. Needs approval before starting because it
uses a paid service.

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
