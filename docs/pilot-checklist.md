# Pilot checklist

What must be true before one real IT service firm uses this app with real work, what to
measure while it does, and when to stop. Version 0.4.0 does **not** meet this checklist
yet: hosting, off-machine backups, an independent review and the agreement with the firm
are still open.

## 1. Before any real data enters the app

**Security**

- [x] Real sign-in (passwords with a second factor) replaces the local user switcher.
      Built in 0.4.0; single sign-on is not.
- [ ] The app runs somewhere agreed with the firm: on their machine, or hosted with HTTPS,
      a managed database and access limited to their staff. Not decided.
- [ ] Backups are encrypted, stored off the machine, and a restore has been tested.
      0.4.0 encrypts and test-restores backups (`backup --encrypt`, `backup --verify`);
      storing them off the machine and running a restore drill are still to do.
- [x] The audit log can be exported for the firm (JSON and CSV, 0.4.0).
- [ ] A security review of the above has been done and its findings fixed. 0.4.0 has a
      self-review ([security-review-0.4.0.md](security-review-0.4.0.md)); an independent
      review is still needed.

**Agreement with the firm**

- [ ] A written data agreement: what data the app holds, where, who can see it, how long it
      is kept, and how it is deleted at the end.
- [ ] Whether the AI reader may be used. If yes, the firm has read Anthropic's data terms
      and agreed, and a spending limit is set in the Anthropic console.
- [ ] The firm's customers are not contacted by the app. Quote emails and schedules stay
      simulated; a person sends the real email from their own system.
- [ ] A named contact at the firm and a way to report problems.

**Setup**

- [ ] The firm's price list is imported (Catalog, Import a price list) and approved by
      the firm's owner.
- [ ] Their customers and sites are entered.
- [ ] Two people are set up at least: one who prepares quotes and one who approves.
- [ ] A short walkthrough with the people who will use it.

## 2. What to measure during the pilot

Agree these numbers with the firm before starting, and write down how they quote today
(from the interviews) as the comparison.

| Measure | Where it comes from |
| --- | --- |
| Requests handled in the app versus outside it | Count of workflows; the firm's own count |
| Time per step (received to draft, draft to submitted, submitted to decision) | Measurements page |
| How often people corrected the reader, per field | Measurements page |
| Quotes that were wrong after approval | Ask the firm weekly; check against the audit log |
| AI cost per request, if used | Workflow pages and dashboard |
| Problems and requests from users | A shared log kept during the pilot |

## 3. Stop the pilot if

- a wrong quote reaches a customer because of the app;
- any data appears where the data agreement says it must not;
- the firm stops using it for two weeks running;
- AI spending passes the agreed limit.

## 4. What would count as success

Decide this with the firm before starting, in numbers, for example: "at least 20 real
requests handled, median time from received to draft lower than today, no wrong quote
sent, and the owner wants to keep using it." A pilot that misses its target is still a
result: write down why.
