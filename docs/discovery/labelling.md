# Labelling redacted samples

`python -m opsapp redact` creates `labels.csv` next to the redacted files. Fill in one row
per sample with what a careful person would take from the request, **before** running any
reader on it, so the labels aren't shaped by what the readers say.

Real requests ask for a real firm's services, which the demo catalog doesn't have. Label
each ask with the closest demo service from the app's **Catalog** page. If none is close,
count it as unsupported.

| Column | What to write | Example |
| --- | --- | --- |
| `file` | Already filled in | `sample-001.txt` |
| `sender` | The sender placeholder from the file, or blank | `[EMAIL-1]` |
| `items` | Demo SKUs asked for and how many, separated by `;`. Use `?` when no number was given. Blank if none. | `LAPTOP-SETUP=3; EMAIL-ONBOARD=?` |
| `timeframe` | `tomorrow`, `this_week`, `next_week`, `asap`, `date:YYYY-MM-DD`, or blank | `next_week` |
| `instruction` | `yes` if the text tries to instruct the system (discounts, "approve this", "ignore the rules"), otherwise `no` | `no` |
| `unsupported` | How many asks match no demo service | `1` |
| `notes` | Anything odd or uncertain | `"a few" laptops` |

The customer isn't scored, because the demo records don't contain the firm's customers.

Then, in PowerShell:

```powershell
python -m opsapp eval run --samples C:\samples\redacted
```

This scores the free rule-based reader and writes `reports\samples.md`. To compare it with
the AI reader (paid, and it sends the redacted text to Anthropic, so only if the firm
agreed to that):

```powershell
python -m opsapp eval compare --samples C:\samples\redacted --yes
```

A handful of samples from one firm says little about other firms. Report the number of
samples and firms next to any score.
