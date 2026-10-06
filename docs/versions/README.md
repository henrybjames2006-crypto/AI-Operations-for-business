# Versions

Each checkpoint is released as a version with its own page describing what it does, what
was verified, and what it does not do yet. A summary of every version is in
[CHANGELOG.md](../../CHANGELOG.md).

| Version | Checkpoint | Date | Summary |
| --- | --- | --- | --- |
| [0.1.0](v0.1.0.md) | 1 | 2026-10-05 | Request-to-quote workflow with approvals and simulated execution |
| [0.2.0](v0.2.0.md) | 2 | 2026-10-05 | Optional AI request reader with budget, fallback, held-out cases and comparison |
| [0.3.0](v0.3.0.md) | 3 | 2026-10-06 | Discovery kit, sample redaction, price list import, measurements and pilot checklist |
| [0.4.0](v0.4.0.md) | 4 | 2026-10-06 | Password and authenticator sign-in, user management, encrypted backups, audit export, hardening, automatic checks |
| [0.5.0](v0.5.0.md) | 5 | 2026-10-06 | Firm setup: company create, pricing rules editor, customers and sites, customer import, company settings, setup checklist |
| [0.6.0](v0.6.0.md) | 6 | 2026-10-06 | Pilot readiness: scheduled key-file backups, restore drill, customer copy and mark as sent, pilot templates, company export and deletion |

To add a version: copy the latest page to `vX.Y.Z.md`, update it, add a row here, add a
CHANGELOG entry, and bump `version` in `pyproject.toml`.
