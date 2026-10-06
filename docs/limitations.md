# Limitations (version 0.6.0)

This is a local prototype for demonstrating one workflow with fictional data. It is **not
production ready** and has not been used with real customers.

## Not evidence of anything commercial

- No customer interviews, pilots, pricing tests or market research have been done. Whether
  small IT service firms want this, or would pay for it, is an untested hypothesis. Version
  0.3.0 adds the kit for running interviews ([docs/discovery/](discovery/README.md)); none
  have been run yet.
- Evaluation scores come from invented cases: 61 written alongside the rule-based reader and
  30 held-out cases added in 0.2.0. None of them measure accuracy on real requests, time
  saved or error rates in practice.

## Functional limits

- **Rule-based reading is weak on new wording.** On the 30 held-out cases the rules read
  17 fully correctly (16 in 0.2.0; the 0.3.0 site fix was prompted by a held-out case).
  They miss quantities written as "a replacement" or "each", dates such as "next Thursday", most instruction-like text not on their list, and some services
  described in unusual words. Misses usually become clarification questions, but some
  produce a wrong service or quantity that a person must catch before submitting.
- **The AI reader is optional and paid.** It is off by default. Its accuracy and cost are
  measured only on the fictional evaluation set (see docs/evaluation.md).
- **Redaction is pattern-based.** It replaces emails, phone numbers, street addresses, UK
  postcodes, links, IP addresses, secrets and the names you list. Other names, unusual
  formats and identifying details in the wording are missed, so every redacted file must
  be read by a person.
- **Samples are scored against the demo catalog**, and the customer is not scored.
- **Measurements** start with 0.3.0: workflows created earlier have no recorded proposal,
  so their corrections can't be measured. Timings include time spent waiting for answers.
- **No real actions.** Email and calendar adapters are simulations that write rows to the
  local database. Nothing is sent and no event is created.
- **Scheduling is a proposal only.** The three times are the next business days at 09:00 in
  the tenant's time zone. No calendar or technician availability is checked.
- **Tax is not calculated.** Quotes say so.
- **Single currency (USD)** and one pricing catalog per tenant.
- **Inbound email is not connected.** Requests are pasted into a form.
- **Catalog editing is by file.** Services and prices change by importing a CSV price
  list (one draft at a time); there is no editor for a single service. Pricing rules have
  an editor (0.5.0), limited to the three rule types: volume discount, minimum charge and
  trip fee. Imported versions copy the rules of the current version.
- **Quotes reach customers only by hand** (0.6.0): a person prints the customer copy or
  pastes the email text into their own email program. The app can't tell whether it
  really arrived; **Mark as sent by hand** records only what the person says.
- **Customers are never deleted**, only deactivated (0.5.0). The customer CSV import adds
  new customers only; it can't update existing ones.

## Technical limits

- **Sign-in is password plus authenticator app only.** No single sign-on (Microsoft 365,
  Google) and no emailed password reset: an owner resets passwords and authenticator apps.
  If the only owner loses both their phone and recovery codes, the fix is
  `python -m opsapp user create` for a new owner on the computer that holds the database.
- **The authenticator secrets are stored unencrypted in the database.** Anyone with the
  database file can read them. Keep backups encrypted and the computer locked.
- **The session cookie is not marked Secure** because the app is served over plain HTTP on
  127.0.0.1. Hosting it anywhere else needs HTTPS first.
- **Demo mode** has no passwords at all and is only for the fictional data. It refuses to
  start once any user has a password.
- **SQLite, single machine, not hosted.** Where the app runs for a pilot is undecided.
  Daily backups (0.6.0) leave the machine only if their folder is synced, for example by
  OneDrive; the app does not check that syncing works. The scheduled task runs only while
  you are signed in to Windows, and losing the key file means losing the backups.
- **The security review is a self-review** against the OWASP Top 10, not an independent
  test.
- **No accessibility audit, browser matrix or load testing** beyond a headless Chromium run.
- One third-party deprecation warning appears in the tests (Starlette's test client and
  `httpx`); it does not affect the app.
