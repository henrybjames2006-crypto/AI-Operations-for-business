# ADR 0001: One Python package, SQLite, two local processes

Status: accepted (Checkpoint 1)

**Context.** A local demo for one person, needing transactions, migrations, and a separate
worker for actions, on Windows without extra installs.

**Decision.** One package with domain, workflow, persistence, dispatch and web modules.
SQLite (WAL, `BEGIN IMMEDIATE` for writes) through SQLAlchemy and Alembic. The web app and the
dispatcher are separate processes sharing the database.

**Consequences.** Nothing to install beyond Python. Moving to PostgreSQL later means a new
engine and migration check, not a rewrite. Not suitable for multiple machines.
