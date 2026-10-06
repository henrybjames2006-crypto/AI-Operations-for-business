# Limitations (version 0.3.0)

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
  17 fully correctly (16 in 0.2.0; the 0.3.0 site fix was prompted by a held-out case). They miss quantities written as "a replacement" or "each", dates such
  as "next Thursday", most instruction-like text not on their list, and some services
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
- **Customer records are seed data.** There is no customer editor. The catalog can be
  replaced by importing a CSV price list (one draft at a time); there is no editor for
  single services or pricing rules, and imported versions copy the rules of the current
  version.

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
