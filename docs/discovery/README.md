# Discovery kit

Tools for finding out whether small IT service firms (5 to 30 staff) actually want this,
before any real customer data goes into the app. Nothing here has been used yet: no
interviews have been run and no real samples have been collected.

| Step | What | File or command |
| --- | --- | --- |
| 1 | Decide what you are testing | [hypotheses.md](hypotheses.md) |
| 2 | Run interviews with firm owners and operations staff | [interview-guide.md](interview-guide.md), [notes-template.md](notes-template.md) |
| 3 | Keep track of who you spoke to and what you learned | [interview-tracker.csv](interview-tracker.csv) |
| 4 | If a firm agrees, collect a few real requests and strip personal details | `python -m opsapp redact` (below) |
| 5 | Label the redacted requests and score the readers on them | [labelling.md](labelling.md), `python -m opsapp eval run --samples` |
| 6 | Before any firm tries the app with real work | [../pilot-checklist.md](../pilot-checklist.md) |

## Rules for real data

- Real requests never go into the app's database in this version. They stay as files on
  your computer.
- Only collect samples a firm has agreed in writing to share, for this purpose.
- Redact first and read every redacted file before anyone else (including Claude) sees it.
- Sending samples to the AI reader (`eval compare --samples`) sends them to Anthropic's
  API. Only do that if the firm agreed to it specifically.
- Delete the originals when the agreement says to.

## Redacting samples (Windows PowerShell)

Put the original requests as `.txt`, `.eml` or `.md` files in one folder, for example
`C:\samples\originals`. Make a text file with the names to hide, one per line: people,
the firm, its customers, and any product or building names that identify them.

```powershell
cd C:\path\to\AI-Operations-for-business
.\.venv\Scripts\Activate.ps1
notepad C:\samples\names.txt
python -m opsapp redact C:\samples\originals --out C:\samples\redacted --names C:\samples\names.txt
```

This writes `sample-001.txt` and so on to `C:\samples\redacted`, plus:

- `redaction-report.csv`: every replacement made (file, line, kind, placeholder), without
  the original values;
- `labels.csv`: a blank labelling sheet (see [labelling.md](labelling.md)).

The originals are not changed. Emails, phone numbers, street addresses, UK postcodes, web
links, IP addresses, passwords and API keys are replaced by pattern. Names are replaced
only if they are in your list. **Patterns miss things**, so open every redacted file and
check it by eye before sharing it.
