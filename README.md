# AI Operations for Business (version 0.3.0 prototype)

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

Whether small IT service firms want this, or would pay for it, has not been tested. See
[docs/limitations.md](docs/limitations.md).

## What you can do in the demo

1. Sign in as a fictional user (local user switcher, no passwords).
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
python -m opsapp serve
```

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
python -m pytest                 # 271 tests, no network needed
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
python -m opsapp backup --out backups\opsapp.sqlite # consistent copy + integrity check
python -m opsapp restore --from backups\opsapp.sqlite --yes   # stop the server first
python -m opsapp export <workflow id> --out export.json      # redacted workflow history
python -m opsapp recompute-quote <quote version id>          # independent recalculation
python -m opsapp db current
```

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
  contains (current: [0.3.0](docs/versions/v0.3.0.md), Checkpoint 3)
- [docs/architecture.md](docs/architecture.md): components, states, data, execution
- [docs/demo.md](docs/demo.md): walkthrough with the fictional demo data
- [docs/evaluation.md](docs/evaluation.md): synthetic evaluation set and latest results
- [docs/security.md](docs/security.md): boundaries, secrets, pre-pilot checklist
- [docs/limitations.md](docs/limitations.md): what this prototype does not do
- [docs/discovery/](docs/discovery/README.md): interview kit, redaction and labelling
- [docs/pilot-checklist.md](docs/pilot-checklist.md): what must be true before a pilot
- [docs/adr/](docs/adr/): design decisions

## Stack

Python 3.14, FastAPI with server-rendered Jinja2 pages (no JavaScript), SQLAlchemy 2.1,
Alembic, SQLite (WAL), Pydantic 2, Anthropic Python SDK (optional reader), pytest +
Hypothesis, Ruff, mypy.
