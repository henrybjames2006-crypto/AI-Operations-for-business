# 7. Real samples stay outside the app until Checkpoint 4

**Status:** accepted (version 0.3.0)

## Context

Checkpoint 3 prepares for learning from real IT service firms: interviews, real sample
requests, and a real price list. The app still has a local user switcher with no
passwords, a single SQLite file and no agreed hosting, so it is not fit to hold a real
firm's customer data.

## Decision

- Real sample requests are handled as **files on the user's computer**, never in the app
  database. `python -m opsapp redact` copies them into a new folder with personal details
  replaced by placeholders, using fixed patterns and a names list the user supplies. It
  uses no AI and no network, and never changes the originals. Output files are renamed so
  original file names (which often hold names) don't leak, and the report lists what was
  replaced without the original values.
- Labelled samples are scored with `eval run --samples` against the **demo** tenant, so
  the firm's customers never need to be entered. The customer field is not scored.
- A price list may be imported into the app, because prices and service names are not
  personal data. Import is strict and all-or-nothing, produces a **draft** pricing
  version, and changes nothing until an owner approves it. Cells that start with a
  spreadsheet formula character are refused.
- Measurements (step timings, corrections) are computed from the audit log and the
  reader's recorded proposal, never estimated.

## Consequences

- No real customer data can enter the app through 0.3.0 features, but redaction is only
  as good as its patterns and names list, so a person must read every redacted file.
- Sending samples to the AI reader (`eval compare --samples`) sends redacted text to
  Anthropic. The command warns about this and needs `--yes`; doing it requires the firm's
  agreement.
- Moving real requests into the app waits for Checkpoint 4 (real sign-in, hosting,
  backups) and the [pilot checklist](../pilot-checklist.md).
