# Pilot data agreement (template)

> **This is a starting point, not legal advice.** It was written to list the questions a
> pilot needs answered. Have it checked by someone qualified (for example a solicitor or
> lawyer who knows data protection law where the firm operates) before anyone signs it.
> Change every part that does not match what you actually do.

**Between:** [Your name / business] ("the provider")
**And:** [Firm name] ("the firm")
**Pilot dates:** [start] to [end]

## 1. What the pilot is

The provider lets the firm try a prototype that helps prepare quotes from customer
requests. The prototype runs on [the provider's computer / the firm's computer / a hosted
service described in an annex]. It does not send emails, create calendar events, charge
anyone or connect to the firm's other systems. People at the firm send any quote
themselves.

## 2. What data the app holds

- Customer requests that firm staff paste in (text, sender email address).
- The firm's customers: names, other names, email domains, a contact email, site labels
  and addresses.
- The firm's price list and pricing rules.
- Quotes, approvals, and a history (audit log) of every change.
- Staff accounts: name, email, role. Passwords are stored only as one-way hashes.

The firm agrees not to paste in: [payment card details, health information, passwords,
anything else agreed].

## 3. Where it is kept and who can see it

- Location: [computer / service and country].
- Backups: encrypted, daily, kept for [14 days] in [location, e.g. OneDrive account of
  ...]. The backup key is kept by [who].
- Who can see the data: the firm's staff with accounts, and [the provider's named people]
  for support. Nobody else.

## 4. AI use

[Choose one]
- The AI request reader is **off**. Requests are read by fixed rules on the computer.
- The AI request reader is **on**. Request text is sent to Anthropic to be read. The firm
  has read Anthropic's commercial terms and data policies and agrees. Spending limit:
  [amount] per month, set in the Anthropic console.

## 5. How long data is kept, and the end of the pilot

- At the end of the pilot, or earlier if the firm asks, the provider will:
  1. export the firm's data (`python -m opsapp company export`) and give the file to the
     firm;
  2. delete the firm's data from the app (`python -m opsapp company delete`);
  3. delete backups containing it within [30 days], or when they age out of the [14-day]
     retention.
- The provider confirms the deletion in writing, with the date.

## 6. Security

- Sign-in needs a password and an authenticator app code.
- The prototype has had a self-review only, not an independent security test.
  [Update this if an independent review has been done.]
- Problems or suspected misuse are reported to [contact, phone/email] within [24 hours]
  of being noticed. The provider tells the firm of any incident affecting its data within
  [timeframe].

## 7. No warranty

The app is a prototype. Quotes must be checked by a person at the firm before they are
sent. The provider is not responsible for [to be agreed with legal advice].

## 8. Contacts

| Role | Name | Email | Phone |
| --- | --- | --- | --- |
| Firm contact | | | |
| Provider contact | | | |

Signed: ____________________ (firm)   ____________________ (provider)   Date: ________
