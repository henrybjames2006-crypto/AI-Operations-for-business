# Security and boundaries

## Hard boundaries in Checkpoint 1

- No real email, calendar, payment, contract, purchasing or customer-record system is
  connected. Adapters are simulations only (`src/opsapp/adapters/simulated.py`).
- This project is separate from any other project. It does not read from or write to any
  existing database; it creates its own SQLite file under `data/`.
- The server binds to `127.0.0.1` only. The setting is fixed in code
  (`src/opsapp/config.py`) and a test fails if it changes.
- The request reader is the rule-based `mock` unless `OPSAPP_AI_PROVIDER=anthropic` and an
  API key are set. Only `mock` and `anthropic` are accepted.

## Optional AI reader (0.2.0)

- **What is sent:** the request text and sender address, catalog service names and
  keywords, customer names and aliases, site labels, and today's date.
- **Never sent:** prices, user names, other companies' data, or anything else from the
  database.
- **The AI only proposes.** Its answer must match a fixed JSON schema. The same guard as the
  rule-based reader then drops services not in the catalog, quotes not found word for word
  in the request, customer or site names not in the text, and bad quantities or timeframes.
  Customers, prices, approvals and actions are decided by fixed code.
- **Customer text is data.** The prompt marks the request as untrusted content and asks the
  model to report instruction-like text rather than follow it. Tests use a fake model that
  "obeys" such text and show it still cannot change price, customer or approval.
- **The key** comes only from `OPSAPP_AI_API_KEY`, is passed to the SDK explicitly (so no
  other credentials on the machine are picked up), is hidden from settings output, and is
  redacted from logs and exports.
- **Spending:** at most 1,500 output tokens per call, input checked against an 8,000-token
  limit before sending, one retry for temporary failures only, and an app budget
  (`OPSAPP_AI_BUDGET_USD`) after which no AI calls are made. The budget is estimated from
  token counts, so it is not a billing cap; set a spending limit in the Anthropic console
  too.
- **Data handling:** Anthropic's API terms and retention apply to anything sent. Only
  fictional requests have been sent during development. Real customer text should not be
  sent until the pre-pilot items below are done.

## Secrets

- Secrets come from environment variables or a local `.env` file, which is git-ignored.
  `.env.example` contains placeholders only.
- Secret-named values (`OPSAPP_SESSION_SECRET`, `OPSAPP_AI_API_KEY`) and secret-looking
  strings (API keys, bearer tokens, private keys) are redacted from logs and from JSON
  exports by `src/opsapp/redaction.py`.
- Tests plant a secret and check it never appears in logs or exports, and scan the
  repository for credential patterns.

## Application controls

- **Server-side permissions.** Every action checks the role matrix
  (`src/opsapp/domain/roles.py`); hiding a button is never the control.
- **Separation of duties.** The person who prepared or submitted a quote cannot approve or
  reject it, even as owner.
- **Approval binds to content.** The approval stores a hash of the quote version and every
  action payload. Any edit voids it; a stale approval is refused.
- **Tenant isolation.** Every lookup is scoped by tenant; another tenant's records return
  "Not found".
- **CSRF tokens** on every form, rotated at sign-in.
- **Untrusted text.** Customer text is never executed or treated as instructions.
  Instruction-like text is flagged and logged; the extractor only proposes, and prices,
  customers and approvals are decided by fixed rules.
- **Tamper-evident audit.** Each tenant's events form a SHA-256 hash chain;
  `python -m opsapp audit verify` detects edits or deletions.
- **Idempotent execution.** Each action has an idempotency key; uncertain outcomes are
  reconciled by lookup before any resend, and unresolved ones escalate to a person.

## Real data and imports (0.3.0)

- **Real samples stay out of the app.** `python -m opsapp redact` works on files on the
  user's computer, offline, without AI, and never changes the originals. It refuses an
  output folder inside the originals folder, renames output files, and its report never
  contains original values. Redaction is pattern-based and misses names not in the list
  and unusual formats, so a person reads every redacted file before it is shared. See
  [ADR 0007](adr/0007-real-data-stays-outside-the-app.md).
- **Sending samples to the AI** (`eval compare --samples`) needs `--yes` and the firm's
  agreement, and sends only redacted text.
- **Price list import** is owner-only (`MANAGE_CATALOG`), limited to 200 KB and 200 rows,
  all-or-nothing, and creates a draft that changes nothing until approved. Cells starting
  with `=`, `+`, `@`, tab or carriage return are refused (CSV formula injection). Each
  import is audited with the file's SHA-256.
- **Measurements** are read-only, tenant-scoped, and contain workflow ids, timings and
  outcome words, not request text.

## Before any pilot with real data (not done)

See also the [pilot checklist](pilot-checklist.md).


- Real authentication (SSO or passwords with MFA) and session management.
- Hosting decision, TLS, and a managed database with encrypted, off-machine backups.
- Data retention and deletion policy; customer data processing terms.
- Review of the AI provider's data handling before sending any real text to it.
- Security review and dependency scanning in CI.
- Rate limiting and monitoring.
- Explicit approval from Henry for each external connection.
