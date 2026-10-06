# Set up a firm

How to set up a new IT service firm in the app from nothing, without the fictional demo
companies. Use an invented firm for practice. Don't enter a real firm's data until the
[pilot checklist](pilot-checklist.md) says it is time.

## 1. Make a database and the company (PowerShell)

Use a database file of its own, so the firm never shares a database with the demo data.
In your project folder, with the virtual environment active:

```powershell
# A separate database file for this firm (replaces the OPSAPP_DATABASE_PATH line)
(Get-Content .env) | Where-Object { $_ -notmatch '^OPSAPP_DATABASE_PATH=' } | Set-Content .env
Add-Content .env "OPSAPP_DATABASE_PATH=data/oakfield.sqlite"
(Get-Content .env) -replace '^OPSAPP_DEMO_MODE=.*', 'OPSAPP_DEMO_MODE=false' | Set-Content .env

python -m opsapp db upgrade
python -m opsapp company create "Oakfield Computer Care" --timezone Europe/London --owner-name "Olive Owner" --owner-email olive@oakfield.example
```

- It asks for the owner's password twice (at least 12 characters).
- The time zone is used for proposed schedule times. Use a name such as `America/Chicago`,
  `America/New_York` or `Europe/London`.
- The company starts empty: no services, prices, customers or other users. The currency
  is USD.

Then start the app with `python -m opsapp serve`, open <http://127.0.0.1:8000> and sign in
as the owner. The first time, you scan a QR code with an authenticator app.

## 2. Follow the checklist on the dashboard

The dashboard shows **Set up your company** until three things are done.

### Price list (Catalog)

1. Make a CSV file with the columns `sku, name, unit, unit_price, min_qty, max_qty,
   quantity_step, onsite, keywords, description`. Separate several keywords with `;`.
   The keywords are how the request reader recognises a service.
2. Under **Import a price list**, choose the file and click **Import as draft**.
3. Check the draft and click **Approve this pricing version**.

### Pricing rules (Catalog, owner only)

Under **Pricing rules**, add any of:

- a **volume discount**: a percentage off one service from a quantity;
- a **minimum charge** for the items on a quote;
- a **trip fee** added once to any quote with on-site work.

Each change goes into a draft pricing version. Nothing changes for quotes until you
approve the draft, and quotes already made keep their prices.

### Customers (owner or operator)

Open **Customers** and either:

- add each customer by hand: name, other names it goes by, email domains and a contact
  email, then its sites (label and address); or
- import a CSV file with one row per site: `customer_name, other_names, email_domains,
  contact_email, site_label, site_address`. **Download an example file** shows the format.
  You see a preview before anything is saved, and a file with any problem saves nothing.

Requests from an address at a customer's email domain are matched to that customer
automatically. Shared mail services such as gmail.com can't be used as a domain.

To stop using a customer, **Deactivate** it. Removing a site works the same way: past
requests and quotes keep them.

### People (Users, owner only)

Add at least one more person, so one prepares quotes and another approves them. Nobody can
approve a quote they prepared.

## 3. Company settings (owner only)

**Company** changes the company name and time zone. Every change is in the audit log.

## Going back to the demo data

Point `OPSAPP_DATABASE_PATH` back at the demo database (the default is
`data/opsapp.sqlite`). Both databases stay as they are:

```powershell
(Get-Content .env) -replace '^OPSAPP_DATABASE_PATH=.*', 'OPSAPP_DATABASE_PATH=data/opsapp.sqlite' | Set-Content .env
```
