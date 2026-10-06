# Security self-review, version 0.4.0

**Date:** 2026-10-06
**What was reviewed:** the local prototype at version 0.4.0. The source was read against
the OWASP Top 10 (2021) and checked with the automated tests, Bandit and pip-audit.
**Who:** written by Claude during the build. This is a **self-review, not an independent
security test**. The pilot checklist still asks for an independent review before real data.

Status words:
- **ok:** checked, nothing to fix.
- **fixed:** a problem found and fixed in 0.4.0.
- **accepted:** a known weakness, kept for now and written down.
- **open:** still to do before a pilot.

## A01 Broken access control

- **ok:** every page and form checks the signed-in user on the server, and every use case
  checks the role matrix (`domain/roles.py`). A test walks every route without a session,
  and with only a password (no code), and none of them returns a page.
- **ok:** records are looked up by company, so another company's records give "Not found".
  This includes users on the Users page.
- **ok:** owners can't disable or reset themselves.
- **accepted:** on a few forms, FastAPI rejects a request with missing fields (422) before
  the sign-in check runs. No data is shown either way.

## A02 Cryptographic failures

- **ok:** passwords are hashed with scrypt and a per-password salt.
- **ok:** session tokens and recovery codes are random and stored as SHA-256 only.
- **ok:** backups are encrypted with AES-256-GCM and a scrypt-derived key.
- **accepted:** authenticator secrets are stored unencrypted in the database. A key held
  outside the database would protect them, but losing that key would lock everyone out.
  The database file and its backups must be protected instead (encrypted backups, a
  locked computer).
- **accepted:** the cookie is not marked Secure, because the app is served over plain HTTP
  on 127.0.0.1 only. Any hosting must add HTTPS first.

## A03 Injection

- **ok:** all database access goes through SQLAlchemy with bound parameters.
- **ok:** templates are auto-escaped, and no template marks content as safe.
- **ok:** customer text is treated as data by both readers (0.1.0 and 0.2.0 tests).
- **ok:** CSV formula injection is refused on price list import (0.3.0). In the audit
  export it is neutralised with a leading `'` (fixed in 0.4.0).

## A04 Insecure design

- **ok:** approval is bound to the exact content, the person who prepared a quote can't
  approve it, and nothing real is sent.
- **accepted:** the lockout after 5 wrong tries lets someone at the computer lock another
  user out for 15 minutes on purpose. This is the usual trade-off; an owner can see
  locked accounts on the Users page.
- **accepted:** wrong passwords for unknown email addresses are not rate limited. Each try
  costs one scrypt run. This is acceptable while the app only listens on 127.0.0.1;
  hosting would need rate limiting.

## A05 Security misconfiguration

- **ok:** the server binds to 127.0.0.1 only, and a test fails if that changes.
- **ok:** the API docs pages are off.
- **fixed:** strict security headers on every response (content security policy with no
  scripts, no inline styles and no framing; nosniff; no-referrer; no-store). Inline styles
  were removed from the templates so the policy holds, and a test checks they stay out.
- **fixed:** the app refuses to start with real sign-in when `OPSAPP_SESSION_SECRET` is
  missing or the example value. It refuses demo mode when any user has a password.
- **fixed:** request bodies over about 300 KB are refused.

## A06 Vulnerable and outdated components

- **ok:** pip-audit found no known vulnerabilities in the installed packages on 2026-10-06.
  It now runs on every pull request.
- **accepted:** dependencies are pinned to version ranges, not exact hashes.

## A07 Identification and authentication failures

- **fixed:** the user picker was replaced by password plus authenticator code (it remains
  only as demo mode).
- **fixed:** passwords need 12+ characters, and common passwords, very simple ones and
  ones containing the user's name or email are refused.
- **fixed:** lockout after 5 wrong passwords or codes.
- **fixed:** the same message for every failure, and the same scrypt work for unknown
  emails.
- **fixed:** codes can't be replayed.
- **fixed:** sessions are stored on the server with idle and absolute limits, and end at
  once on sign-out, disable or reset. The session is replaced at sign-in, so no session
  fixation.
- **open:** no single sign-on, and no passkeys.

## A08 Software and data integrity failures

- **ok:** the audit log is hash-chained, and exports can be checked without the app.
- **ok:** encrypted backups detect any change.
- **accepted:** the GitHub Actions steps use version tags (`actions/checkout@v5`), not pinned
  commit hashes.

## A09 Security logging and monitoring failures

- **fixed:** failed sign-ins, lockouts, sign-ins, sign-outs, recovery code use and every
  user change are written to the audit log, without passwords or codes.
- **open:** nothing alerts anyone. Someone has to read the audit log.

## A10 Server-side request forgery

- **ok:** the app makes no outbound requests, except the optional reader calling the
  Anthropic API at a fixed address. No user-supplied URL is ever fetched.

## Automated results on this version

| Check | Result |
| --- | --- |
| Tests | 298 passed |
| Ruff, mypy | clean |
| Bandit (`bandit -r src`) | no issues. Of the 11 low findings, nine `assert` checks were replaced with explicit errors, an evaluation-only secret was replaced with a random one, and the `.env.example` placeholder in `config.py` is marked as not a secret. |
| pip-audit | no known vulnerabilities |
