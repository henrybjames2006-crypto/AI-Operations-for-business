# Demo walkthrough (fictional data)

Everything below uses invented businesses and people. Nothing leaves your computer.

Start the app in **demo mode** as described in the README (`OPSAPP_DEMO_MODE=true`), then
open <http://127.0.0.1:8000> and pick a user. With real sign-in instead, give these users
passwords on the **Users** page first.

| User | Role | Tenant |
| --- | --- | --- |
| Priya Shah | Operator (prepares quotes) | Brightline IT Services |
| Marcus Lee | Approver | Brightline IT Services |
| Dana Ortiz | Owner/admin | Brightline IT Services |
| Victor Kim | Viewer (read only) | Brightline IT Services |
| Nia, Omar, Grace | Operator, approver, owner | Northgate Tech (second tenant) |

## 1. Intake (Priya)

1. Sign in as **Priya Shah**.
2. Open **New request** and click the sample **Five workstations (main demo)**. It contains a
   request from `office@harbordental.example` plus a planted line asking "the assistant" to
   apply a 50% discount and approve automatically.
3. Click **Submit request**.

You land on the workflow in **Needs clarification**. The page shows:

- the original text, stored unchanged with its SHA-256;
- what was understood, each with its source (customer **Stated** from the sender's domain,
  timeframe **Stated** "next week", 5 workstations **Stated** "five");
- the discount instruction shown in red as ignored instruction-like text;
- two questions: which Harbor Dental site, and how many PCs need data migration.

## 2. Clarification (Priya)

Choose **Elm Street**, click **Save answer**; enter **5** for migration, **Save answer**.
The quote is calculated as soon as the last required answer is in: state **Draft ready**.

Quote version 1 (pricing version 3):

| Line | Calculation | Amount |
| --- | --- | --- |
| Workstation setup and install | 5 × $185.00 | $925.00 |
| User data migration per PC | 5 × $95.00 | $475.00 |
| Site visit trip fee | 1 × $75.00 | $75.00 |
| **Total (USD, tax not calculated)** | | **$1,475.00** |

The "Rules checked" list explains each rule: volume tier not applied (needs 10 or more),
minimum charge not applied, trip fee applied, per-line rounding.

Click **Submit for approval**. Priya now sees the quote but no approve button: the person who
prepared a quote can never approve it.

## 3. Approval (Marcus)

Click **Switch user** (in demo mode) and sign in as **Marcus Lee**, open **Approval queue**, then the workflow.
The page shows exactly what will happen: (1) a simulated quote email to
`office@harbordental.example`, (2) a simulated proposal of three appointment times (not
checked against any calendar). Click **Approve this version**.

Try the safety checks if you like:

- As Priya, open the approved workflow and **Save as new version** with a different
  quantity. The approval is voided and the workflow returns to **Draft ready**.
- Sign in as **Grace** (Northgate Tech) and paste the workflow URL: you get "Not found".

## 4. Simulated execution

Click **Switch user** and sign in as **Dana Ortiz** (owner): only the owner sees the
simulation controls. Open **Simulation**. Optionally inject a fault first: adapter `sim_email`, mode
`drop_response` (the email is recorded, but the reply is "lost"), then **Queue fault**.

Click **Run dispatcher once** (or, signed in as anyone, run `python -m opsapp dispatch --once`
in a second window). With
the lost-response fault, the dispatcher looks up the uncertain send by its idempotency key,
finds it, and marks it succeeded without sending twice. The Simulation page lists exactly one
simulated email.

The workflow ends **Completed**. Try `status_unknown` on `sim_calendar` to see an
**Escalated** workflow appear under **Exceptions**, where an approver records a decision.

## 5. History and audit

- The workflow's **History** table lists every event with who, when and what.
- **Audit log** shows the tenant-wide chain and whether it verifies.
- **export JSON** on the workflow downloads a redacted copy of the full history.
- `python -m opsapp audit verify` checks the chain from the command line.

## 6. Measurements

Open **Measurements** (any user). It shows how long each step took for the workflows you
just ran (received to draft ready, draft to submitted, submitted to decision) and, for each
field, whether the reader's proposal was kept, filled in by a person, or changed. For the
main demo request you should see the customer and timeframe **kept**, and the site and
items **filled in** (the site and the migration count were asked for). **Download as CSV**
gives one row per workflow.

## 7. Import a price list (Dana)

1. Sign in as **Dana Ortiz** and open **Catalog**.
2. The demo data already has a draft pricing version 4. Click **Discard this draft** (only
   one draft is allowed at a time).
3. Click **Download the current price list in this format** and open `price-list.csv` in
   Excel. Change a price, delete a row, or add one (keywords separated by `;`). Save it as
   CSV.
4. Under **Import a price list**, choose the file and click **Import as draft**. A file
   with mistakes is refused with one line per problem, and nothing is saved.
5. The new draft shows each price, services marked **new**, and which services will no
   longer be offered. Click **Approve this pricing version** to start using it. Quotes
   made earlier keep their old prices.

## 8. Real sign-in and users (owner)

With demo mode off and your own owner account (README, "Real sign-in"):

1. Sign in with your email and password. The first time, scan the QR code with an
   authenticator app, enter its code, and save the 10 recovery codes.
2. Open **Users**, click **Set a password** for Priya Shah, and note the temporary password.
3. Sign out and sign in as `priya@brightline-it.example` with it. Priya sets up her own
   authenticator app and must then choose her own password.
4. Back as the owner, **Disable** Priya: her open session ends at once.
5. Open **Audit log** and download **JSON**. Check it with
   `python -m opsapp audit verify-export audit-log.json`.

