# Limitations (version 0.2.0)

This is a local prototype for demonstrating one workflow with fictional data. It is **not
production ready** and has not been used with real customers.

## Not evidence of anything commercial

- No customer interviews, pilots, pricing tests or market research have been done. Whether
  small IT service firms want this, or would pay for it, is an untested hypothesis.
- Evaluation scores come from invented cases: 61 written alongside the rule-based reader and
  30 held-out cases added in 0.2.0. None of them measure accuracy on real requests, time
  saved or error rates in practice.

## Functional limits

- **Rule-based reading is weak on new wording.** On the 30 held-out cases the rules read
  16 fully correctly. They miss quantities written as "a replacement" or "each", dates such
  as "next Thursday", most instruction-like text not on their list, and some services
  described in unusual words. Misses usually become clarification questions, but some
  produce a wrong service or quantity that a person must catch before submitting.
- **The AI reader is optional and paid.** It is off by default. Its accuracy and cost are
  measured only on the fictional evaluation set (see docs/evaluation.md).
- **A site name inside a company name** can be taken as the site (for example "Bakery" in
  "Summit Bakery Co"), so a site question that should be asked is skipped. Found by
  held-out case 17; not fixed in 0.2.0.
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
