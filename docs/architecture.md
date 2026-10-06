# Architecture (version 0.6.0)

One Python package, `opsapp`, run as two local processes that share one SQLite file:

- **Web app** (`python -m opsapp serve`): FastAPI with server-rendered Jinja2 pages. Plain HTML
  forms, no JavaScript. Binds to 127.0.0.1 only. (The plan mentioned HTMX; it turned out to
  be unnecessary and was dropped.)
- **Dispatcher** (`python -m opsapp dispatch`, or the Simulation page button): executes
  approved actions from the outbox through simulated adapters.

```
src/opsapp/
  domain/       pure rules: money, pricing, states, roles, hashing, scheduling, errors
  ai/           extraction contract (schema, port), rule-based mock, optional Claude
                reader, fallback/budget wrapper, output guard
  adapters/     action adapter port + simulated email and calendar with fault injection
  persistence/  SQLAlchemy models, engine/sessions, Alembic migrations, backup/restore
  workflow/     WorkflowService (all use cases), audit chain, catalog snapshots,
                price list CSV import, measurements
  dispatch/     outbox dispatcher: leasing, retries, timeouts, reconciliation
  web/          routes, view models, templates, samples
  evals/        synthetic cases, held-out cases, labelled real samples, evaluation
                runner and reader comparison
  discovery/    offline redaction of real sample requests (files only, no database)
  auth/         passwords (scrypt), authenticator codes (TOTP), sessions, user management
  verify.py     stdlib-only independent quote recalculation
```

The web layer calls `WorkflowService` only; it never changes rows directly. Domain modules
import nothing from the database or web layers.

## Workflow states

Every change goes through `check_transition` (`domain/states.py`). Refused moves raise and
are written to the audit log as `action_refused`.

| From | Allowed next states |
| --- | --- |
| Received | Needs clarification, Draft ready, Escalated, Canceled |
| Needs clarification | Draft ready, Escalated, Canceled |
| Draft ready | Awaiting approval, Needs clarification, Canceled |
| Awaiting approval | Approved, Rejected, Draft ready (edited), Canceled |
| Approved | Executing, Draft ready (edited before execution), Canceled |
| Executing | Completed, Failed, Escalated |
| Failed | Executing (retry), Escalated, Canceled |
| Escalated | Needs clarification, Draft ready, Executing, Completed, Canceled |
| Rejected | Draft ready (revise), Canceled |
| Completed, Canceled | (terminal) |

Changes from the written plan, found while building: **Approved → Draft ready** (an edit after
approval but before execution voids the approval), **Failed → Canceled**, and
**Escalated → Completed** (a person confirms the action did happen).

## Request reading

`ai/mock.py` splits the text into clauses and matches catalog keywords and numbers (digits
and number words). It also flags instruction-like text ("ignore previous instructions",
"to the assistant", "skip the approval", discount or price demands). Its output is a
Pydantic `ExtractionOutput`. `ai/guard.py` then drops anything that is not a known SKU,
whose evidence quote is not in the original text, or whose quantity or timeframe is invalid.
The extractor sees customer names and the catalog, never prices. Customer and site
mentions must be known names that appear in the text.

Version 0.2.0 adds an optional AI reader, `ai/claude.py`, behind the same `Extractor`
interface. It calls a Claude model through the Anthropic Python SDK with structured output
(a fixed JSON schema), then the result goes through the same guard. `ai/fallback.py` wraps
it: if the app budget is spent the AI is not called; a temporary failure (timeout,
connection, rate limit, server error) is retried once; anything else (rejected key, refusal,
output cut off or not matching the schema) is not retried. In every failure case the
rule-based reader reads the request instead, the history records an `ai_fallback` event,
and each failed call is still recorded in `ai_usage` with its tokens and estimated cost.
`container.make_extractor` picks the reader from `OPSAPP_AI_PROVIDER`.

Customer matching is deterministic: exact sender domain, then exact name or alias in the
text. Partial matches only narrow the options in a "which customer?" question. Each fact in
the working scope records its source: Stated, Inferred, From records, or Operator.

## Pricing

`domain/pricing.py` is pure and uses `Decimal` throughout; floats are rejected. Order:

1. Line items: quantity × unit price from the approved pricing version, each line rounded
   to cents (half up).
2. Volume tier: percentage discount on a SKU at or above a quantity (5% off workstation
   installs at 10 or more).
3. Minimum charge: if items total less than the minimum ($250.00), a top-up line.
4. Trip fee: one per quote when any on-site service is present ($75.00).

The total is the sum of rounded lines. Tax is not calculated. Each quote version stores a
snapshot of the pricing version it used, the inputs, every rule result (applied or not, and
why), and a content hash. `verify.py` recomputes a stored quote using only the standard
library, as a cross-check.

## Approval

Submitting creates the proposed actions (simulated quote email, simulated schedule
proposal) with their exact payloads. The approval stores `subject_hash` = SHA-256 of the
quote version id, its content hash, and each action's payload hash. Approving with any
other hash is refused as stale. Any edit voids existing approvals. The preparer and
submitter cannot approve or reject. Optimistic locking (`state_version`) stops two people
acting on the same version at once.

## Execution

Approving writes outbox rows in the same transaction. The dispatcher:

- claims the next due row with a 120 s lease and records each attempt;
- calls the adapter inside a timeout;
- on failure, retries with backoff up to `OPSAPP_MAX_ATTEMPTS` (3), then marks the workflow
  Failed;
- on timeout or lost response, the outcome is **uncertain**: it looks the operation up by
  idempotency key. Found → succeeded; not found → send again with the same key; unknown →
  Escalated for a person to decide;
- on restart, any action left in progress is reconciled the same way before anything is
  resent.

Actions run in order; the schedule proposal runs only after the quote email succeeded.

## Data

SQLite in WAL mode with foreign keys on. Money is stored as decimal strings, times as UTC
ISO strings, IDs as sortable random strings with an entity prefix (`wf_`, `quo_`, ...).
Writes use `BEGIN IMMEDIATE` so concurrent writers wait instead of failing mid-transaction;
pages read through a separate read-only session factory. Schema changes go through Alembic
(`python -m opsapp db upgrade`); a test checks the migrated schema equals the models.

## Audit

Every event is appended to a per-tenant hash chain: each row stores the hash of the previous
row plus its own content. `python -m opsapp audit verify` and the Audit log page recompute it.

## Price list import and measurements (0.3.0)

`workflow/catalog_import.py` parses a CSV price list into rows or a list of line errors,
never both. `WorkflowService.import_catalog` turns a good file into a draft
`PricingVersion` with the current rules, its prices, and the catalog details to apply in
`catalog_changes` (migration 0002). New services are created inactive. On approval,
`_apply_catalog_changes` updates and activates the imported services and deactivates the
ones left out; drafts can instead be discarded.

When a request is read, the workflow's `scope["proposed"]` records the customer, site,
services and timeframe the reader proposed after the guard. `workflow/measures.py` compares
that with the current quote version and takes step timings from the first `state_changed`
event for each state.

`discovery/redact.py` and `evals/samples.py` work only on files. Labelled samples are scored
against the demo tenant's catalog, with the customer left unscored.

## Sign-in (0.4.0)

`auth/service.py` (`AuthService`, on the container as `c.auth`) owns sign-in and users.
The web layer keeps a signed cookie (Starlette's session middleware) holding only a random
session token, the form token and flash messages; `AuthService.session_user` looks the
token's SHA-256 up in `auth_sessions` on every request and ends idle, expired or revoked
sessions. Between the password and the code, the cookie holds the user id for at most five
minutes and nothing else is reachable. Migration 0003 adds the sign-in columns on `users`,
a unique index on email, and the `auth_sessions` and `recovery_codes` tables.

`web/hardening.py` adds security headers and a body size limit as plain ASGI middleware.
`persistence/backup.py` encrypts backups (AES-256-GCM) and checks them in a temporary copy.
`workflow/audit_export.py` writes the audit log export; `verify.verify_audit_export` checks
it independently.

## Firm setup (0.5.0)

`AuthService.create_company` makes a tenant and its first owner in one transaction, with a
`company_created` audit event as the first link of the new tenant's chain.
`workflow/firm.py` (`FirmService`, on the container as `c.firm`) holds the setup use
cases. It runs each one through `WorkflowService._run`, so permission checks, tenant
scoping and audit events work as for the workflow:

- customers and sites: `save_customer`, `set_customer_active`, `save_site`, `remove_site`;
- the customer CSV import: `check_customer_import` (no writes) and `import_customers`,
  which re-checks the file and its SHA-256 from the preview;
- pricing rules: `add_pricing_rule` and `remove_pricing_rule` edit the open draft, or a
  new draft that copies the approved version's price entries and rules
  (`catalog_changes` stays empty, so approval leaves the services as they are);
- company settings: `update_company`.

`workflow/customer_import.py` holds the checks shared by the form and the CSV import.
Migration 0004 adds `active` to `customers` and `customer_sites`. The reader's context,
customer matching, clarification options and quote edits only use active ones; a request
or quote that already points at a deactivated customer or removed site keeps it.
`firm.setup_checklist` drives the dashboard checklist.

## Pilot readiness (0.6.0)

`persistence/backup.py` adds a second encrypted format (`OPSAPP-ENCRYPTED-BACKUP-2`) keyed
by a key file instead of a passphrase. `backup_to_folder` writes, verifies and prunes
dated backups and records each result in `backup-status.json` and `backup.log` next to
the database; `drill` restores the newest one into a temporary folder and compares record
counts per tenant with the live database. `web/views.backup_summary` reads the status
file for the owner's dashboard.

`workflow/firm.approved_quote` rebuilds the customer copy from the approval's own records
(quote version, `send_quote` and `propose_schedule` payloads), so it shows only what was
approved. `FirmService.record_sent_by_hand` writes a `quote_sent_by_hand` audit event and
nothing else. `persistence/company_data.py` exports and deletes one tenant's rows across
every table with a `tenant_id`, children first.
