# Limitations (Checkpoint 1)

This is a local prototype for demonstrating one workflow with fictional data. It is **not
production ready** and has not been used with real customers.

## Not evidence of anything commercial

- No customer interviews, pilots, pricing tests or market research have been done. Whether
  small IT service firms want this, or would pay for it, is an untested hypothesis.
- Evaluation scores come from 61 invented cases written alongside the rules they test. They
  do not measure accuracy on real requests, time saved or error rates in practice.

## Functional limits

- **No real AI.** Requests are read by deterministic keyword and number rules. They handle
  the phrasings in the samples and test cases; new phrasings will often be missed and turn
  into clarification questions. A real model adapter is planned for Checkpoint 2 and needs
  approval because it is a paid service.
- **No real actions.** Email and calendar adapters are simulations that write rows to the
  local database. Nothing is sent and no event is created.
- **Scheduling is a proposal only.** The three times are the next business days at 09:00 in
  the tenant's time zone. No calendar or technician availability is checked.
- **Tax is not calculated.** Quotes say so.
- **Single currency (USD)** and one pricing catalog per tenant.
- **Inbound email is not connected.** Requests are pasted into a form.
- **Customer records are seed data.** There is no customer or catalog editor beyond
  approving the draft pricing version.

## Technical limits

- **Local user switcher, no passwords.** Anyone who can reach the page can be any user.
  Safe only because the server binds to 127.0.0.1.
- **SQLite, single machine.** Fine for a demo; a pilot would need a managed database,
  backups off the machine, and real authentication.
- **Sessions use a local signed cookie.** If `OPSAPP_SESSION_SECRET` is unset a random one is
  generated at start, so restarting the server signs everyone out.
- **No accessibility audit, browser matrix or load testing** beyond a headless Chromium run.
- One third-party deprecation warning appears in the tests (Starlette's test client and
  `httpx`); it does not affect the app.
