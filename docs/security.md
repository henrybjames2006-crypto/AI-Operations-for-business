# Security and boundaries

## Hard boundaries in Checkpoint 1

- No real email, calendar, payment, contract, purchasing or customer-record system is
  connected. Adapters are simulations only (`src/opsapp/adapters/simulated.py`).
- This project is separate from any other project. It does not read from or write to any
  existing database; it creates its own SQLite file under `data/`.
- The server binds to `127.0.0.1` only. The setting is fixed in code
  (`src/opsapp/config.py`) and a test fails if it changes.
- The AI provider setting accepts only `mock`.

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

## Before any pilot with real data (not done)

- Real authentication (SSO or passwords with MFA) and session management.
- Hosting decision, TLS, and a managed database with encrypted, off-machine backups.
- Data retention and deletion policy; customer data processing terms.
- Review of the AI provider's data handling before sending any real text to it.
- Security review and dependency scanning in CI.
- Rate limiting and monitoring.
- Explicit approval from Henry for each external connection.
