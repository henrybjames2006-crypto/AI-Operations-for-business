# 8. Password and authenticator sign-in, server-side sessions

**Status:** accepted (version 0.4.0)

## Context

Until 0.3.0 anyone at the computer could pick any user. Before a real firm could pilot the
app, people need their own accounts, and the pilot checklist asks for a second factor.
There is no real email, no hosting and no outside identity provider yet, and Henry asked
for no paid services.

## Decision

- Email and password, hashed with scrypt from Python's standard library.
- A required second factor: TOTP codes from any authenticator app, implemented with the
  standard library (RFC 6238), set up by QR code at first sign-in, with ten one-time
  recovery codes. The QR code is drawn by `segno` (pure Python, no dependencies).
- Sessions stored in the database, so they can be ended at once (sign-out, disable,
  password or authenticator reset), with a 30-minute idle limit and an 8-hour maximum.
- Owners add users with a temporary password shown once, and reset passwords and
  authenticators. Nothing is emailed.
- The old picker stays as an explicit demo mode, refused once anyone has a password.

## Alternatives not chosen

- **Microsoft 365 or Google sign-in:** needs an app registration with an outside service
  and a hosted address. Possible later, once hosting is decided.
- **Passkeys (WebAuthn):** stronger, but needs JavaScript, HTTPS and browser support work;
  the app has no JavaScript today.
- **Emailed reset links:** the app sends no real email.

## Consequences

- The authenticator secrets are in the database unencrypted; protecting the database file
  and its backups matters more now.
- The cookie can't be marked Secure while the app is served over http://127.0.0.1.
- Two new dependencies: `cryptography` (backup encryption) and `segno` (QR code).
