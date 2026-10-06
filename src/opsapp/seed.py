"""Fictional seed data. Every business, person, address and price here is invented.

Email domains use the reserved ``.example`` top-level domain so nothing can be delivered.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ids import new_id
from .persistence.models import (
    CatalogItem,
    Customer,
    CustomerSite,
    PriceEntryRow,
    PricingVersion,
    Tenant,
    User,
)

D = Decimal

BRIGHTLINE_CATALOG: list[dict[str, Any]] = [
    {
        "sku": "WS-INSTALL",
        "name": "Workstation setup and install",
        "unit": "workstation",
        "description": "Labor only: unbox, connect, image and test one desktop. Hardware supplied "
        "by the customer.",
        "keywords": [
            "workstation",
            "workstations",
            "desktop",
            "desktops",
            "new pc",
            "new pcs",
            "new computers",
            "new computer",
            "computer setup",
        ],
        "step": "1",
        "onsite": True,
        "prices": {2: "175.00", 3: "185.00", 4: "195.00"},
        "min": "1",
        "max": "50",
    },
    {
        "sku": "LAPTOP-SETUP",
        "name": "Laptop setup",
        "unit": "laptop",
        "description": "Labor only: configure and test one laptop. Hardware supplied by the "
        "customer.",
        "keywords": ["laptop", "laptops", "notebook", "notebooks"],
        "step": "1",
        "onsite": True,
        "prices": {2: "140.00", 3: "145.00", 4: "150.00"},
        "min": "1",
        "max": "50",
    },
    {
        "sku": "DATA-MIGR",
        "name": "User data migration per PC",
        "unit": "PC",
        "description": "Copy one user's files and settings from an old PC to a new one.",
        "keywords": [
            "data migration",
            "migrate",
            "migrate data",
            "files moved",
            "move files",
            "move data",
            "move our files",
            "transfer files",
            "copy files",
            "files over",
        ],
        "step": "1",
        "onsite": True,
        "prices": {2: "90.00", 3: "95.00", 4: "99.00"},
        "min": "1",
        "max": "50",
    },
    {
        "sku": "PRN-SETUP",
        "name": "Network printer setup",
        "unit": "printer",
        "description": "Install drivers and connect one network printer for all users.",
        "keywords": ["printer", "printers"],
        "step": "1",
        "onsite": True,
        "prices": {2: "115.00", 3: "120.00", 4: "125.00"},
        "min": "1",
        "max": "20",
    },
    {
        "sku": "WIFI-AP",
        "name": "Wireless access point install",
        "unit": "access point",
        "description": "Mount and configure one customer-supplied access point.",
        "keywords": ["access point", "access points", "wifi", "wi-fi", "wireless"],
        "step": "1",
        "onsite": True,
        "prices": {2: "200.00", 3: "210.00", 4: "220.00"},
        "min": "1",
        "max": "30",
    },
    {
        "sku": "NET-DROP",
        "name": "Network cable drop",
        "unit": "drop",
        "description": "Run and terminate one network cable to a wall jack.",
        "keywords": [
            "cable drop",
            "cable drops",
            "network drop",
            "network drops",
            "ethernet drop",
            "ethernet drops",
            "cabling",
        ],
        "step": "1",
        "onsite": True,
        "prices": {2: "150.00", 3: "160.00", 4: "170.00"},
        "min": "1",
        "max": "40",
    },
    {
        "sku": "EMAIL-ONBOARD",
        "name": "Email account onboarding",
        "unit": "user",
        "description": "Create one mailbox and set up sign-in, done remotely.",
        "keywords": [
            "email account",
            "email accounts",
            "mailbox",
            "mailboxes",
            "new user accounts",
        ],
        "step": "1",
        "onsite": False,
        "prices": {2: "40.00", 3: "40.00", 4: "45.00"},
        "min": "1",
        "max": "100",
    },
    {
        "sku": "ONSITE-HOUR",
        "name": "On-site technician time",
        "unit": "hour",
        "description": "General on-site work billed in half-hour steps.",
        "keywords": ["technician hours", "hours of on-site", "on-site hours", "onsite hours"],
        "step": "0.5",
        "onsite": True,
        "prices": {2: "120.00", 3: "125.00", 4: "130.00"},
        "min": "0.5",
        "max": "40",
    },
]

BRIGHTLINE_RULES = [
    {"kind": "volume_tier", "sku": "WS-INSTALL", "min_qty": "10", "percent_off": "5"},
    {"kind": "minimum_charge", "amount": "250.00"},
    {"kind": "trip_fee", "sku": "SITE-TRIP", "label": "Site visit trip fee", "amount": "75.00"},
]

NORTHGATE_CATALOG: list[dict[str, Any]] = [
    {
        "sku": "WS-INSTALL",
        "name": "Desktop deployment",
        "unit": "desktop",
        "description": "Labor only. Hardware supplied by the customer.",
        "keywords": ["workstation", "workstations", "desktop", "desktops"],
        "step": "1",
        "onsite": True,
        "prices": {1: "199.00"},
        "min": "1",
        "max": "40",
    },
    {
        "sku": "PRN-SETUP",
        "name": "Printer setup",
        "unit": "printer",
        "description": "Connect one printer.",
        "keywords": ["printer", "printers"],
        "step": "1",
        "onsite": True,
        "prices": {1: "99.00"},
        "min": "1",
        "max": "10",
    },
]


def _ts(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def _add_catalog(
    s: Session,
    tenant_id: str,
    catalog: list[dict[str, Any]],
    versions: list[dict[str, Any]],
    owner_id: str,
) -> None:
    items: dict[str, CatalogItem] = {}
    for c in catalog:
        item = CatalogItem(
            id=new_id("catalog_item"),
            tenant_id=tenant_id,
            sku=c["sku"],
            name=c["name"],
            unit=c["unit"],
            description=c["description"],
            keywords=c["keywords"],
            quantity_step=D(c["step"]),
            onsite=c["onsite"],
            active=True,
        )
        s.add(item)
        items[c["sku"]] = item
    s.flush()
    for v in versions:
        pv = PricingVersion(
            id=new_id("pricing_version"),
            tenant_id=tenant_id,
            version_no=v["no"],
            currency="USD",
            status=v["status"],
            effective_from=v["effective"],
            rules=v["rules"],
            approved_by=owner_id if v["status"] != "draft" else None,
            approved_at=v["effective"] if v["status"] != "draft" else None,
            notes=v["notes"],
        )
        s.add(pv)
        s.flush()
        for c in catalog:
            price = c["prices"].get(v["no"])
            if price is None:
                continue
            s.add(
                PriceEntryRow(
                    id=new_id("price_entry"),
                    tenant_id=tenant_id,
                    pricing_version_id=pv.id,
                    catalog_item_id=items[c["sku"]].id,
                    unit_price=D(price),
                    currency="USD",
                    min_qty=D(c["min"]),
                    max_qty=D(c["max"]),
                )
            )


def seed(s: Session, now: datetime | None = None) -> dict[str, str]:
    """Create the two fictional tenants. Refuses to run twice."""
    if s.scalars(select(Tenant)).first() is not None:
        raise RuntimeError("Database already has data; seed only an empty database.")
    now = now or datetime.now(UTC)
    ids: dict[str, str] = {}

    # --- Tenant 1: Brightline IT Services (fictional) ---------------------------------
    t1 = Tenant(
        id=new_id("tenant"),
        name="Brightline IT Services",
        currency="USD",
        timezone="America/Chicago",
        created_at=now,
    )
    s.add(t1)
    s.flush()
    users = [
        ("priya", "Priya Shah", "operator"),
        ("marcus", "Marcus Lee", "approver"),
        ("dana", "Dana Ortiz", "owner"),
        ("victor", "Victor Kim", "viewer"),
    ]
    for key, name, role in users:
        u = User(
            id=new_id("user"),
            tenant_id=t1.id,
            display_name=name,
            email=f"{key}@brightline-it.example",
            role=role,
            is_active=True,
        )
        s.add(u)
        ids[key] = u.id
    s.flush()

    customers = [
        (
            "harbor",
            "Harbor Dental Group",
            ["Harbor Dental", "HDG"],
            ["harbordental.example"],
            "office@harbordental.example",
            [
                ("Elm Street", "1200 Elm Street, Springfield"),
                ("Bay Road", "48 Bay Road, Springfield"),
            ],
        ),
        (
            "harborview",
            "Harborview Accounting",
            ["Harborview"],
            ["harborview-acct.example"],
            "admin@harborview-acct.example",
            [("Main office", "9 Lake Avenue, Springfield")],
        ),
        (
            "maple",
            "Maple Street Law",
            ["Maple Street Law Office"],
            ["maplestreetlaw.example"],
            "it@maplestreetlaw.example",
            [("Downtown", "300 Maple Street, Springfield")],
        ),
        (
            "summit",
            "Summit Bakery Co",
            ["Summit Bakery"],
            ["summitbakery.example"],
            "owner@summitbakery.example",
            [("Bakery", "77 Hill Road, Springfield"), ("Warehouse", "5 Depot Lane, Springfield")],
        ),
    ]
    for key, name, aliases, domains, contact, sites in customers:
        c = Customer(
            id=new_id("customer"),
            tenant_id=t1.id,
            name=name,
            aliases=aliases,
            email_domains=domains,
            contact_email=contact,
        )
        s.add(c)
        s.flush()
        ids[f"customer_{key}"] = c.id
        for label, address in sites:
            site = CustomerSite(
                id=new_id("site"), tenant_id=t1.id, customer_id=c.id, label=label, address=address
            )
            s.add(site)
            s.flush()
            ids[f"site_{key}_{label.lower().replace(' ', '_')}"] = site.id

    _add_catalog(
        s,
        t1.id,
        BRIGHTLINE_CATALOG,
        [
            {
                "no": 2,
                "status": "retired",
                "effective": now - timedelta(days=400),
                "rules": BRIGHTLINE_RULES,
                "notes": "Last year's prices.",
            },
            {
                "no": 3,
                "status": "approved",
                "effective": now - timedelta(days=30),
                "rules": BRIGHTLINE_RULES,
                "notes": "Current approved price list.",
            },
            {
                "no": 4,
                "status": "draft",
                "effective": now - timedelta(days=1),
                "rules": BRIGHTLINE_RULES,
                "notes": "Proposed increase; not approved.",
            },
        ],
        ids["dana"],
    )

    # --- Tenant 2: Northgate Tech (fictional), used to prove isolation ----------------
    t2 = Tenant(
        id=new_id("tenant"),
        name="Northgate Tech",
        currency="USD",
        timezone="America/New_York",
        created_at=now,
    )
    s.add(t2)
    s.flush()
    for key, name, role in [
        ("nia", "Nia Okafor", "operator"),
        ("omar", "Omar Haddad", "approver"),
        ("grace", "Grace Turner", "owner"),
    ]:
        u = User(
            id=new_id("user"),
            tenant_id=t2.id,
            display_name=name,
            email=f"{key}@northgate-tech.example",
            role=role,
            is_active=True,
        )
        s.add(u)
        ids[key] = u.id
    c2 = Customer(
        id=new_id("customer"),
        tenant_id=t2.id,
        name="Riverside Clinic",
        aliases=["Riverside"],
        email_domains=["riversideclinic.example"],
        contact_email="frontdesk@riversideclinic.example",
    )
    s.add(c2)
    s.flush()
    ids["customer_riverside"] = c2.id
    s.add(
        CustomerSite(
            id=new_id("site"),
            tenant_id=t2.id,
            customer_id=c2.id,
            label="Clinic",
            address="2 River Road, Fairview",
        )
    )
    s.flush()
    north_owner = ids["grace"]
    _add_catalog(
        s,
        t2.id,
        NORTHGATE_CATALOG,
        [
            {
                "no": 1,
                "status": "approved",
                "effective": now - timedelta(days=60),
                "rules": [
                    {
                        "kind": "trip_fee",
                        "sku": "SITE-TRIP",
                        "label": "Trip charge",
                        "amount": "60.00",
                    }
                ],
                "notes": "Northgate prices.",
            },
        ],
        north_owner,
    )
    ids["tenant_brightline"] = t1.id
    ids["tenant_northgate"] = t2.id
    s.flush()
    return ids
