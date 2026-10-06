# Versions

Each checkpoint is released as a version with its own page describing what it does, what
was verified, and what it does not do yet. A summary of every version is in
[CHANGELOG.md](../../CHANGELOG.md).

| Version | Checkpoint | Date | Summary |
| --- | --- | --- | --- |
| [0.1.0](v0.1.0.md) | 1 | 2026-10-05 | Request-to-quote workflow with approvals and simulated execution |
| [0.2.0](v0.2.0.md) | 2 | 2026-10-05 | Optional AI request reader with budget, fallback, held-out cases and comparison |

To add a version: copy the latest page to `vX.Y.Z.md`, update it, add a row here, add a
CHANGELOG entry, and bump `version` in `pyproject.toml`.
