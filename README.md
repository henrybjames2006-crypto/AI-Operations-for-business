# AI Operations for Business (version 0.6.0 prototype)

A local prototype of one workflow for small IT service firms:

> customer request → clarification → draft quote → manager approval → simulated quote
> delivery → simulated schedule proposal → completion or escalation

**Status: local prototype, fictional data only. Not production ready.** Nothing in this
repository sends real email, creates real calendar events, charges anyone, signs anything,
or connects to any other system or database. All "delivery" and "scheduling" happens in
simulated adapters that write rows to the local SQLite file. Requests are read by a
rule-based reader by default; version 0.2.0 adds an optional Claude model as the reader
(off unless you turn it on, paid per request). Either way the reader only proposes what a
request says, and fixed rules decide customers, prices and approvals. Version 0.3.0 adds
tools for learning from real firms without putting their data in the app: a discovery
kit, a local redaction tool for sample requests, price list import, and measurements.
Version 0.5.0 lets a new firm be set up from nothing: its company, prices, pricing rules,
customers and staff. Version 0.6.0 prepares for a small pilot: daily encrypted backups
to a folder you choose, a restore drill, a printable customer copy of an approved quote
that a person sends themselves, and data agreement and pilot plan templates.

Whether small IT service firms want this, or would pay for it, has not been tested. See
[docs/limitations.md](docs/limitations.md).

## What you can do in the demo

1. Sign in with a password and a code from an authenticator app, or, in demo mode, pick a
   fictional user with no password.
2. Paste a customer request, or use a built-in sample.
3. See what was understood, where each fact came from (Stated, Inferred, From records,
   Operator), and answer the clarification questions.
4. Review a quote where every number traces to a priced catalog version and a named rule.
5. Submit for approval. Approval is bound to the exact quote, recipient and actions; any
   edit voids it. The person who prepared the quote cannot approve it.
6. Run the simulated delivery and scheduling, with optional injected failures (failure,
   timeout, lost response, unknown status) to see retries, reconciliation and escalation.
7. Read the full history and verify the tamper-evident audit chain.
8. See time per step and how often people corrected the reader on **Measurements**.
9. As the owner, import a price list from a CSV file as a draft and approve it.
10. As the owner, add users, reset passwords and authenticator apps, disable users, and
    download the audit log.
11. Set up a new, invented firm from nothing: create the company, then add its price list,
    pricing rules, customers and sites, and staff, following the dashboard checklist. See
    [docs/setup-a-firm.md](docs/setup-a-firm.md).
12. Open the customer copy of an approved quote, print it or save it as a PDF, copy the
    email text into your own email program, and record that you sent it by hand.
13. As the owner, see the last good backup and the last passed restore drill on the
    dashboard.

## Run it on Windows (PowerShell)

Needs Python 3.14 (developed with 3.14.4). Install it from python.org or with
`winget install Python.Python.3.14`, then open a new PowerShell window.

```powershell
git clone https://github.com/henrybjames2006-crypto/AI-Operations-for-business.git
cd AI-Operations-for-business

py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
# If activation is blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

Copy-Item .env.example .env
# Put a random session secret into .env:
$secret = python -c "import secrets; print(secrets.token_urlsafe(32))"
(Get-Content .env) -replace '^OPSAPP_SESSION_SECRET=.*', "OPSAPP_SESSION_SECRET=$secret" | Set-Content .env

python -m opsapp db upgrade
python -m opsapp seed
```

Then choose how to sign in.

**Demo mode** (the quickest way to try it with the fictional users, no passwords):

```powershell
(Get-Content .env) -replace '^OPSAPP_DEMO_MODE=.*', 'OPSAPP_DEMO_MODE=true' | Set-Content .env
python -m opsapp serve
```

**Real sign-in** (password plus an authenticator app on your phone). Create your own owner
account in one of the demo companies; you set up the authenticator app at first sign-in:

```powershell
python -m opsapp user create --company "Brightline IT Services" --name "Your Name" --email you@example.com
python -m opsapp serve
```

As that owner, open **Users** to give the demo users passwords. To set up a new firm
instead of using the demo companies, follow [docs/setup-a-firm.md](docs/setup-a-firm.md). Demo mode can't be used on
a database where anyone has a password; use a separate database for it.

Open <http://127.0.0.1:8000>. The server only listens on 127.0.0.1.

Simulated actions run when you press **Run dispatcher once** on the Simulation page. To run
them automatically instead, open a second PowerShell window in the same folder:

```powershell
.\.venv\Scripts\Activate.ps1
python -m opsapp dispatch
```

Follow [docs/demo.md](docs/demo.md) for a five-minute walkthrough.

### Checks

```powershell
python -m pytest                 # 336 tests, no network needed
python -m ruff check .
python -m ruff format --check .
python -m mypy src
python -m opsapp eval run        # writes reports\evaluation.md and reports\evaluation.json
```

### Optional: read requests with a Claude model (paid)

Off by default. Create an API key at <https://console.anthropic.com>, set a monthly spending
limit there, then put the key in your local `.env` (never in the repository or in chat):

```powershell
(Get-Content .env) -replace '^OPSAPP_AI_PROVIDER=.*', 'OPSAPP_AI_PROVIDER=anthropic' | Set-Content .env
$key = Read-Host "Paste your Anthropic API key"
(Get-Content .env) -replace '^OPSAPP_AI_API_KEY=.*', "OPSAPP_AI_API_KEY=$key" | Set-Content .env
Remove-Variable key
```

New requests are then read by `OPSAPP_AI_MODEL` (default `claude-haiku-4-5`). If the AI
fails, times out, returns something unusable, or the app's budget (`OPSAPP_AI_BUDGET_USD`,
default $5.00) is used up, the request is read by the rules instead and the page says so.
Each workflow page shows tokens and estimated cost.

Compare the two readers on the evaluation set (about 70 fictional requests; roughly $0.20
to $1 on Haiku 4.5, stopped by the budget):

```powershell
python -m opsapp eval compare --yes   # writes reports\comparison.md and reports\comparison.json
```

To switch back, set `OPSAPP_AI_PROVIDER=mock`.

### Discovery tools (no real data enters the app)

See [docs/discovery/](docs/discovery/README.md) for the interview kit and the rules for
real samples. To strip personal details from sample requests and score the readers on them:

```powershell
python -m opsapp redact C:\samples\originals --out C:\samples\redacted --names C:\samples\names.txt
python -m opsapp eval run --samples C:\samples\redacted     # after filling in labels.csv
```

### Operations commands

```powershell
python -m opsapp audit verify                       # check every tenant's audit hash chain
python -m opsapp audit verify-export audit-log.json # check a downloaded audit log
python -m opsapp backup --out backups\opsapp.opsbak --encrypt  # asks for a passphrase
python -m opsapp backup --verify backups\opsapp.opsbak         # test-restores into a temp copy
python -m opsapp restore --from backups\opsapp.opsbak --yes    # stop the server first
python -m opsapp backup make-key --key "$HOME\Documents\opsapp-backup.key"
python -m opsapp backup --to-folder "$HOME\OneDrive\opsapp-backups" --key "$HOME\Documents\opsapp-backup.key"
python -m opsapp backup drill --folder "$HOME\OneDrive\opsapp-backups" --key "$HOME\Documents\opsapp-backup.key"
python -m opsapp export <workflow id> --out export.json      # redacted workflow history
python -m opsapp recompute-quote <quote version id>          # independent recalculation
python -m opsapp db current
python -m opsapp company create "Firm name" --timezone America/Chicago --owner-name "Name" --owner-email you@example.com
python -m opsapp company export "Firm name" --out firm-export.json
python -m opsapp company delete "Firm name" --out firm-export.json --yes   # end of a pilot
```

Daily backups with Windows Task Scheduler: see [docs/backups.md](docs/backups.md).

### Start over

```powershell
Remove-Item -Recurse -Force data
python -m opsapp db upgrade
python -m opsapp seed
```

## Configuration

All settings are environment variables (or `.env`, which is git-ignored). See
[.env.example](.env.example). Secrets are never written to source, logs, fixtures, or
exports; see [docs/security.md](docs/security.md).

## Documentation

- [CHANGELOG.md](CHANGELOG.md) and [docs/versions/](docs/versions/): what each version
  contains (current: [0.6.0](docs/versions/v0.6.0.md), Checkpoint 6)
- [docs/setup-a-firm.md](docs/setup-a-firm.md): set up a new firm from nothing
- [docs/backups.md](docs/backups.md): daily encrypted backups, restore drill, restore
- [docs/architecture.md](docs/architecture.md): components, states, data, execution
- [docs/demo.md](docs/demo.md): walkthrough with the fictional demo data
- [docs/evaluation.md](docs/evaluation.md): synthetic evaluation set and latest results
- [docs/security.md](docs/security.md): boundaries, secrets, pre-pilot checklist
- [docs/limitations.md](docs/limitations.md): what this prototype does not do
- [docs/security-review-0.4.0.md](docs/security-review-0.4.0.md): self-review against the
  OWASP Top 10
- [docs/discovery/](docs/discovery/README.md): interview kit, redaction and labelling
- [docs/pilot-checklist.md](docs/pilot-checklist.md): what must be true before a pilot
- [docs/pilot/](docs/pilot/): data agreement template (not legal advice) and pilot plan
  template
- [docs/adr/](docs/adr/): design decisions

## Stack

Python 3.14, FastAPI with server-rendered Jinja2 pages (no JavaScript), SQLAlchemy 2.1,
Alembic, SQLite (WAL), Pydantic 2, Anthropic Python SDK (optional reader), pytest +
Hypothesis, Ruff, mypy.
