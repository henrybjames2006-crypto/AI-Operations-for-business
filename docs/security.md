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

## Sign-in and sessions (0.4.0)

- **Passwords** are stored only as scrypt hashes (standard library; N=2^15, r=8, p=1, 16-byte
  salt), never in plain text or logs. New passwords need at least 12 characters and are
  refused if common, too simple, or containing the user's name or email.
- **Second factor.** Every account needs a 6-digit code from an authenticator app (TOTP,
  RFC 6238), set up by QR code at first sign-in. A code is accepted one 30-second step
  early or late, and never twice. Ten one-time recovery codes are shown once and stored as
  SHA-256 hashes. The authenticator secret itself is stored in the database, so anyone
  with the database file could generate codes: keep backups encrypted.
- **Lockout.** Five wrong passwords or codes lock the account for 15 minutes. The message
  is the same whether the email, password or code was wrong, or the account is locked.
  Each failure, lockout, sign-in and sign-out is in the audit log, without the password.
- **Sessions** are stored on the server; the cookie holds only a random token whose SHA-256
  is kept. A session ends at sign-out, after 30 minutes without activity, after 8 hours,
  when the user is disabled, and when their password or authenticator is reset. The
  cookie is HttpOnly and SameSite=Strict. It is not marked Secure, because the app is only
  served over http://127.0.0.1; a hosted version must serve HTTPS and set it.
- **Owners manage users** (`MANAGE_USERS`): add, disable, enable, reset password (a
  16-character temporary password shown once, which must be changed at next sign-in) and
  reset the authenticator. An owner can't disable or reset themselves. Nothing is emailed.
- **Demo mode** (`OPSAPP_DEMO_MODE=true`) restores the no-password user picker for the
  fictional data, behind a banner. The app refuses to start in demo mode if any user has a
  password, and refuses to start with real sign-in unless `OPSAPP_SESSION_SECRET` is set.
- **Web hardening.** Every response carries a strict content security policy (no scripts,
  no inline styles, no framing), `X-Frame-Options: DENY`, `nosniff`, `no-referrer` and
  `Cache-Control: no-store`. Request bodies over about 300 KB are refused with 413.

## Backups and audit export (0.4.0)

- `backup --encrypt` encrypts the backup with AES-256-GCM, using a key derived from a
  passphrase by scrypt. The passphrase is typed in each time and stored nowhere; a lost
  passphrase means a lost backup. A changed or damaged file fails to decrypt rather than
  restoring bad data. The unencrypted copy exists only in a temporary folder while the
  command runs.
- `backup --verify` opens a backup in a temporary copy and checks SQLite integrity, the
  schema version and every company's audit chain, without touching the live database.
- Owners can download their company's audit log (`EXPORT_AUDIT`) as JSON, which states the
  hash rule, or CSV (cells starting with a formula character are prefixed with `'`).
  `audit verify-export` checks the JSON with a separately written, standard-library-only
  checker.

## Automatic checks (0.4.0)

GitHub Actions (`.github/workflows/checks.yml`) runs Ruff, mypy, the tests, Bandit and
pip-audit on every pull request and push to main, with read-only repository access and no
secrets. A self-review against the OWASP Top 10 is in
[security-review-0.4.0.md](security-review-0.4.0.md); it is not an independent audit.

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

## Scheduled backups and pilot data (0.6.0)

- **Key-file backups:** `backup --to-folder` encrypts with a 32-byte random key read from
  a file (AES-256-GCM), so a scheduled task needs no passphrase. The key file is created
  once with owner-only permissions where the system supports it, is never replaced, and
  is refused if it sits inside the backup folder. Anyone with both the key file and a
  backup can read the data, so the key stays out of synced folders. A changed backup or a
  different key fails to decrypt.
- **Logs** (`backup.log`, `backup-status.json`, drill reports) hold dates, file names and
  record counts, never data or keys.
- **Restore drill** works only in a temporary copy and never writes to the live database.
- **Customer copy** shows only the approved quote and email payload; viewers can't record
  a send, and every record is audited. The app sends nothing.
- **Company export** leaves out password hashes, authenticator secrets, sign-in sessions
  and recovery codes. **Company delete** is command-line only, needs `--yes`, exports
  first, deletes in one transaction, and logs the company id, row count and the export's
  SHA-256 without the name. The export file holds the firm's data and must be handed over
  and then removed as the agreement says.

## Firm setup (0.5.0)

- **Permissions:** customers and sites need `manage_customers` (owner, operator); pricing
  rules need `manage_catalog` and company settings need `manage_company` (owner only).
  Records are looked up within the user's company, so another company's records give "Not
  found". Refusals are audited.
- **Customer CSV import:** limited to 150 KB and 500 rows, all-or-nothing, with formula
  cells refused as for price lists. The preview saves nothing; the confirm step re-checks
  the whole file and its SHA-256, and the import is audited with that hash.
- **No silent mismatches:** active customers can't share a name, other name or email
  domain, and shared mail domains (gmail.com and similar) are refused, so a request can't
  be matched to the wrong customer by a domain anyone can use.
- **`company create`** checks the owner's password like every other password and stores
  only its hash.

## Before any pilot with real data (not done)

Done in 0.4.0: real sign-in with a second factor, encrypted backups with a restore check,
audit export, automatic dependency and security scanning. Still open:

See also the [pilot checklist](pilot-checklist.md).


- Hosting decision, TLS, and a managed database. (Encrypted, off-machine backups on this
  computer: 0.6.0.)
- A data agreement reviewed by someone qualified and signed (0.6.0 has a template).
- Review of the AI provider's data handling before sending any real text to it.
- An independent security review (0.4.0 has a self-review only).
- Rate limiting and monitoring.
- Explicit approval from Henry for each external connection.
