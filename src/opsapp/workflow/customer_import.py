"""Customer details: the checks shared by the Customers page and the customer CSV import.

The CSV import is strict and all-or-nothing, like the price list import: every problem is
reported with its line, and a file with any problem saves nothing. One row per site; rows
with the same customer name belong to one customer.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from .catalog_import import _FORMULA_START, _decode, _header

COLUMNS = (
    "customer_name",
    "other_names",
    "email_domains",
    "contact_email",
    "site_label",
    "site_address",
)
OPTIONAL = {"other_names"}
MAX_BYTES = 150_000  # the preview carries the file back, under the request limit
MAX_ROWS = 500
MAX_ALIASES = 20
MAX_DOMAINS = 10

_DOMAIN = re.compile(r"(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]{3,190}")
# Shared mail services: listing one would match every sender who uses it to this customer.
PUBLIC_MAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "msn.com",
        "yahoo.com",
        "ymail.com",
        "aol.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "proton.me",
        "protonmail.com",
        "gmx.com",
        "mail.com",
        "zoho.com",
        "yandex.com",
    }
)


@dataclass
class CustomerFields:
    name: str
    aliases: list[str]
    email_domains: list[str]
    contact_email: str


@dataclass
class SiteFields:
    label: str
    address: str


def split_list(text: str) -> list[str]:
    """Split on commas, semicolons or new lines; drop blanks and repeats, keep the order."""
    out: list[str] = []
    for part in re.split(r"[,;\n]", text):
        item = " ".join(part.split())
        if item and item.lower() not in {o.lower() for o in out}:
            out.append(item)
    return out


def clean_customer(
    name: str, other_names: str, email_domains: str, contact_email: str
) -> tuple[CustomerFields, list[str]]:
    """Normalise a customer's details. Returns the cleaned fields and any problems."""
    problems: list[str] = []
    clean_name = " ".join(name.split())
    if not 2 <= len(clean_name) <= 200:
        problems.append("The customer name must be 2 to 200 characters.")
    aliases = [a for a in split_list(other_names) if a.lower() != clean_name.lower()]
    if len(aliases) > MAX_ALIASES or any(len(a) > 100 for a in aliases):
        problems.append(f"Up to {MAX_ALIASES} other names of up to 100 characters each.")
    domains = [d.lower().lstrip("@") for d in split_list(email_domains)]
    if len(domains) > MAX_DOMAINS:
        problems.append(f"Up to {MAX_DOMAINS} email domains.")
    for d in domains:
        if not _DOMAIN.fullmatch(d):
            problems.append(f"{d!r} is not an email domain (for example: example.com).")
        elif d in PUBLIC_MAIL_DOMAINS:
            problems.append(
                f"{d} is a shared mail service, so it would match every sender who uses it. "
                "Leave it out and choose the customer when a request arrives."
            )
    contact = contact_email.strip().lower()
    if not _EMAIL.fullmatch(contact) or "." not in contact.rsplit("@", 1)[-1]:
        problems.append("Enter a valid contact email address.")
    return CustomerFields(clean_name, aliases, domains, contact), problems


def clean_site(label: str, address: str) -> tuple[SiteFields, list[str]]:
    problems: list[str] = []
    clean_label = " ".join(label.split())
    clean_address = " ".join(address.split())
    if not 1 <= len(clean_label) <= 100:
        problems.append("The site label must be 1 to 100 characters.")
    if not 5 <= len(clean_address) <= 400:
        problems.append("The site address must be 5 to 400 characters.")
    return SiteFields(clean_label, clean_address), problems


@dataclass
class ImportCustomer:
    fields: CustomerFields
    sites: list[SiteFields] = field(default_factory=list)


@dataclass(frozen=True)
class CustomerParseResult:
    customers: list[ImportCustomer]
    errors: list[str]

    @property
    def site_count(self) -> int:
        return sum(len(c.sites) for c in self.customers)


def parse_customers_csv(data: bytes) -> CustomerParseResult:
    if len(data) > MAX_BYTES:
        return CustomerParseResult([], [f"The file is larger than {MAX_BYTES // 1000} KB."])
    reader = csv.reader(io.StringIO(_decode(data)))
    try:
        header = next(reader)
    except StopIteration:
        return CustomerParseResult([], ["The file is empty."])
    names = [_header(h) for h in header]
    errors: list[str] = []
    missing = [c for c in COLUMNS if c not in names and c not in OPTIONAL]
    unknown = [h for h, n in zip(header, names, strict=True) if n and n not in COLUMNS]
    if missing:
        errors.append("Missing column(s): " + ", ".join(missing) + ".")
    if unknown:
        errors.append("Unknown column(s): " + ", ".join(repr(u) for u in unknown) + ".")
    if len(set(n for n in names if n)) != len([n for n in names if n]):
        errors.append("A column name appears more than once.")
    if errors:
        return CustomerParseResult([], errors)

    customers: dict[str, ImportCustomer] = {}
    domain_owner: dict[str, str] = {}
    rows = 0
    for line_no, cells in enumerate(reader, start=2):
        if not any(c.strip() for c in cells):
            continue
        where = f"Line {line_no}"
        rows += 1
        if rows > MAX_ROWS:
            errors.append(f"More than {MAX_ROWS} rows; split the file.")
            break
        if len(cells) > len(names):
            errors.append(f"{where}: more cells than columns (check for unquoted commas).")
            continue
        row = {n: (cells[i] if i < len(cells) else "") for i, n in enumerate(names) if n}
        bad = [k for k, v in row.items() if v.startswith(_FORMULA_START)]
        if bad:
            errors.append(
                f"{where}: {', '.join(bad)} starts with =, +, @ or a tab, which "
                "spreadsheets treat as a formula. Remove that first character."
            )
            continue
        fields, problems = clean_customer(
            row["customer_name"],
            row.get("other_names", ""),
            row["email_domains"],
            row["contact_email"],
        )
        site, site_problems = clean_site(row["site_label"], row["site_address"])
        row_problems = [f"{where}: {p}" for p in problems + site_problems]
        key = fields.name.lower()
        existing = customers.get(key)
        if existing is not None and not row_problems:
            if (
                [a.lower() for a in fields.aliases] != [a.lower() for a in existing.fields.aliases]
                or fields.email_domains != existing.fields.email_domains
                or fields.contact_email != existing.fields.contact_email
            ):
                row_problems.append(
                    f"{where}: {fields.name} appears on an earlier line with different other "
                    "names, email domains or contact email. Repeat them exactly on every row "
                    "for the same customer."
                )
            elif site.label.lower() in {s.label.lower() for s in existing.sites}:
                row_problems.append(f"{where}: {fields.name} already has a site {site.label!r}.")
        if existing is None and not row_problems:
            for d in fields.email_domains:
                owner = domain_owner.get(d)
                if owner is not None and owner != key:
                    other = customers[owner].fields.name
                    row_problems.append(f"{where}: email domain {d} is also given for {other}.")
        if row_problems:
            errors.extend(row_problems)
            continue
        if existing is None:
            existing = customers[key] = ImportCustomer(fields)
            for d in fields.email_domains:
                domain_owner[d] = key
        existing.sites.append(site)
    if not customers and not errors:
        errors.append("The file has a header but no customers.")
    return CustomerParseResult(list(customers.values()) if not errors else [], errors)


TEMPLATE = (
    ",".join(COLUMNS) + "\r\n"
    "Example Dental Group,Example Dental;EDG,exampledental.example,"
    "office@exampledental.example,Main office,1 Example Street Springfield\r\n"
    "Example Dental Group,Example Dental;EDG,exampledental.example,"
    "office@exampledental.example,North clinic,20 Sample Road Springfield\r\n"
)
