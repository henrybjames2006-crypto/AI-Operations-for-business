"""0.5.0: setting up a firm from nothing, customers and sites, pricing rules, company settings."""

from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from conftest import START, Env, events, open_questions, state, submit
from opsapp.__main__ import main
from opsapp.clock import FixedClock
from opsapp.config import Settings
from opsapp.container import Container, build
from opsapp.domain.errors import (
    CatalogImportError,
    Conflict,
    NotFound,
    PermissionDenied,
    ValidationError,
)
from opsapp.persistence import migrate
from opsapp.persistence.models import (
    Customer,
    CustomerSite,
    PriceEntryRow,
    PricingVersion,
    Quote,
    QuoteVersion,
    Tenant,
    User,
)
from opsapp.workflow.audit import verify_chain
from opsapp.workflow.customer_import import parse_customers_csv
from opsapp.workflow.firm import setup_checklist

OWNER_PASSWORD = "birch harbor violet sky"  # noqa: S105 - test only


def empty_container(tmp_path: Path) -> Container:
    """A migrated database with no companies at all: no seed, no demo data."""
    settings = Settings(database_path=tmp_path / "firm.sqlite", session_secret="s" * 16)
    migrate.upgrade(settings.database_path)
    return build(settings, FixedClock(START))


def tenant_named(c: Container, name: str) -> Tenant:
    with c.read_sf() as s:
        t = s.scalars(select(Tenant).where(Tenant.name == name)).one()
        s.expunge_all()
        return t


def user_id(c: Container, email: str) -> str:
    with c.read_sf() as s:
        return s.scalars(select(User.id).where(User.email == email)).one()


PRICE_LIST = (
    b"sku,name,unit,unit_price,min_qty,max_qty,quantity_step,onsite,keywords,description\r\n"
    b"LAPTOP-SETUP,Laptop setup,laptop,120.00,1,200,1,yes,laptop;laptops,Set up a laptop\r\n"
    b"NET-AUDIT,Network audit,site,400.00,1,10,1,yes,network audit,Review a network\r\n"
)


# ---------------------------------------------------------------- the whole journey


def test_new_firm_from_an_empty_database_to_an_approved_delivery(tmp_path: Path) -> None:
    c = empty_container(tmp_path)
    tenant_id = c.auth.create_company(
        "Oakfield Computer Care",
        "Europe/London",
        "Olive Owner",
        "olive@oakfield.example",
        OWNER_PASSWORD,
    )
    owner = user_id(c, "olive@oakfield.example")
    with c.read_sf() as s:
        assert [t.name for t in s.scalars(select(Tenant))] == ["Oakfield Computer Care"]
        assert [step["done"] for step in setup_checklist(s, tenant_id)] == [False] * 3

    # Price list: the first import works with no approved version to copy rules from.
    assert c.service.import_catalog(owner, PRICE_LIST, "oakfield.csv") == 1
    with c.read_sf() as s:
        pv1 = s.scalars(select(PricingVersion).where(PricingVersion.tenant_id == tenant_id)).one()
        assert pv1.rules == [] and "First price list" in pv1.notes
    c.service.approve_pricing_version(owner, pv1.id)

    # A rule through a draft: quotes keep using version 1 until the draft is approved.
    assert (
        c.firm.add_pricing_rule(
            owner, "volume_tier", {"sku": "laptop-setup", "min_qty": "10", "percent_off": "10"}
        )
        == 2
    )
    assert c.firm.add_pricing_rule(owner, "trip_fee", {"amount": "60", "label": ""}) == 2
    with c.read_sf() as s:
        draft = s.scalars(
            select(PricingVersion).where(
                PricingVersion.tenant_id == tenant_id, PricingVersion.version_no == 2
            )
        ).one()
        assert draft.status == "draft" and draft.catalog_changes is None
        assert [r["kind"] for r in draft.rules] == ["volume_tier", "trip_fee"]
        assert draft.rules[0] == {
            "kind": "volume_tier",
            "sku": "LAPTOP-SETUP",
            "min_qty": "10",
            "percent_off": "10",
        }
        copied = s.scalars(
            select(PriceEntryRow).where(PriceEntryRow.pricing_version_id == draft.id)
        ).all()
        assert sorted(str(e.unit_price) for e in copied) == ["120.00", "400.00"]
    c.service.approve_pricing_version(owner, draft.id)

    # A customer with two sites, and a second person to prepare quotes.
    customer = c.firm.save_customer(
        owner,
        None,
        "Willow Accountants",
        "Willow, Willow Accounts",
        "willowaccts.example",
        "office@willowaccts.example",
    )
    with c.read_sf() as s:  # a customer without a site doesn't count yet
        assert setup_checklist(s, tenant_id)[1]["done"] is False
    head = c.firm.save_site(owner, customer, None, "Head office", "1 Willow Lane, Oakfield")
    c.firm.save_site(owner, customer, None, "Branch", "9 Market Street, Oakfield")
    c.auth.add_user(owner, "Pat Preparer", "pat@oakfield.example", "operator")
    pat = user_id(c, "pat@oakfield.example")
    with c.read_sf() as s:
        assert all(step["done"] for step in setup_checklist(s, tenant_id))

    # One request through to an approved, simulated delivery.
    result = c.service.submit_request(
        pat,
        "Hi, please set up 12 laptops at our head office next week. Thanks, Willow",
        "manager@willowaccts.example",
        "oak-1",
    )
    wf = result.workflow_id
    env = Env(c, c.clock, {})  # type: ignore[arg-type]
    for q in open_questions(env, wf):
        value = {"choose_site": head, "quantity": "12", "timeframe": "next_week"}[q.kind]
        c.service.answer_question(pat, wf, q.id, value)
    assert state(env, wf) == "draft_ready", open_questions(env, wf)
    c.service.submit_for_approval(pat, wf)
    h = c.service.approval_subject_hash(owner, wf)
    c.service.approve(owner, wf, h)
    c.dispatcher().run_once()
    assert state(env, wf) == "completed"

    with c.read_sf() as s:
        quote = s.scalars(select(Quote).where(Quote.workflow_id == wf)).one()
        qv = s.get(QuoteVersion, quote.current_version_id)
        assert qv is not None and qv.customer_id == customer and qv.site_id == head
        # 12 x $120 = $1,440; 10% volume discount -$144; trip fee $60.
        assert qv.total == Decimal("1356.00")
        assert qv.pricing_version_id == draft.id
        ok, _bad, n = verify_chain(s, tenant_id)
        assert ok and n > 10
        assert s.scalars(select(Tenant)).all() == [s.get(Tenant, tenant_id)]


def test_cli_company_create_works_without_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "cli.sqlite"
    monkeypatch.setenv("OPSAPP_DATABASE_PATH", str(db))
    args = [
        "company",
        "create",
        "Oakfield Computer Care",
        "--timezone",
        "Europe/London",
        "--owner-name",
        "Olive Owner",
        "--owner-email",
        "Olive@Oakfield.example",
    ]
    assert main(args) == 1  # no database yet
    assert "db upgrade" in capsys.readouterr().out
    migrate.upgrade(db)
    answers = iter([OWNER_PASSWORD, OWNER_PASSWORD])
    monkeypatch.setattr("getpass.getpass", lambda _prompt="": next(answers))
    assert main(args) == 0
    assert "created" in capsys.readouterr().out
    answers = iter([OWNER_PASSWORD, OWNER_PASSWORD])
    assert main([*args[:-1], "second@oakfield.example"]) == 1  # same company name
    assert "already exists" in capsys.readouterr().out
    answers = iter([OWNER_PASSWORD, OWNER_PASSWORD])
    bad_zone = ["company", "create", "Other Firm", "--timezone", "Mars/Olympus", *args[5:-1]]
    assert main([*bad_zone, "x@other.example"]) == 1
    assert "Unknown time zone" in capsys.readouterr().out
    answers = iter(["short", "short"])
    assert main(["company", "create", "Third Firm", *args[3:-1], "x@third.example"]) == 1
    c = build(Settings(database_path=db, session_secret="s" * 16))
    with c.read_sf() as s:
        assert [t.name for t in s.scalars(select(Tenant))] == ["Oakfield Computer Care"]
        assert [u.email for u in s.scalars(select(User))] == ["olive@oakfield.example"]
    c.engine.dispose()


def test_company_create_checks_before_saving(tmp_path: Path) -> None:
    c = empty_container(tmp_path)
    good = ("Oakfield", "America/Chicago", "Olive", "olive@oakfield.example", OWNER_PASSWORD)
    with pytest.raises(ValidationError):
        c.auth.create_company("X", *good[1:])
    with pytest.raises(ValidationError):
        c.auth.create_company(good[0], "Not/AZone", *good[2:])
    with pytest.raises(ValidationError):
        c.auth.create_company(*good[:4], "password")
    c.auth.create_company(*good)
    with pytest.raises(Conflict):
        c.auth.create_company("OAKFIELD", *good[1:3], "other@oakfield.example", OWNER_PASSWORD)
    with pytest.raises(Conflict):  # email already used, in any company
        c.auth.create_company("Second Firm", *good[1:])
    with c.read_sf() as s:
        assert len(s.scalars(select(Tenant)).all()) == 1


# ---------------------------------------------------------------- customers and sites


def test_operators_manage_customers_and_others_cannot(env: Env) -> None:
    ids = env.ids
    cid = env.c.firm.save_customer(
        ids["priya"], None, "Cedar Vets", "", "cedarvets.example", "desk@cedarvets.example"
    )
    env.c.firm.save_site(ids["priya"], cid, None, "Clinic", "4 Cedar Road")
    for who in ("marcus", "victor"):
        with pytest.raises(PermissionDenied):
            env.c.firm.save_customer(ids[who], None, "Elm Co", "", "", "a@elm.example")
        with pytest.raises(PermissionDenied):
            env.c.firm.set_customer_active(ids[who], cid, False)
    with pytest.raises(NotFound):  # another company's customer
        env.c.firm.save_customer(ids["grace"], cid, "Taken", "", "", "a@b.example")
    kinds = [e.event_type for e in events(env)]
    assert "customer_added" in kinds and "site_added" in kinds
    assert kinds.count("action_refused") >= 4


@pytest.mark.parametrize(
    ("domains", "fragment"),
    [
        ("gmail.com", "shared mail service"),
        ("not a domain", "is not an email domain"),
        ("harbordental.example", "already used by Harbor Dental Group"),
    ],
)
def test_customer_domains_are_checked(env: Env, domains: str, fragment: str) -> None:
    with pytest.raises((ValidationError, Conflict), match=fragment):
        env.c.firm.save_customer(env.ids["priya"], None, "New Co", "", domains, "a@new.example")


def test_customer_names_are_unique_and_edits_are_audited(env: Env) -> None:
    ids = env.ids
    with pytest.raises(Conflict, match="already exists"):
        env.c.firm.save_customer(ids["priya"], None, "harbor dental group", "", "", "a@b.example")
    with pytest.raises(Conflict, match="already a name of Harbor Dental Group"):
        env.c.firm.save_customer(ids["priya"], None, "New Co", "HDG", "", "a@b.example")
    harbor = ids["customer_harbor"]
    with env.c.read_sf() as s:
        before = s.get(Customer, harbor)
        assert before is not None
        args = (before.name, ", ".join(before.aliases), ", ".join(before.email_domains))
        contact = before.contact_email
    with pytest.raises(ValidationError, match="Nothing was changed"):
        env.c.firm.save_customer(ids["priya"], harbor, *args, contact)
    env.c.firm.save_customer(ids["priya"], harbor, *args, "frontdesk@harbordental.example")
    changed = [e for e in events(env) if e.event_type == "customer_changed"][-1]
    assert changed.data["after"] == {"contact_email": "frontdesk@harbordental.example"}


def test_deactivated_customers_and_removed_sites_are_not_offered(env: Env) -> None:
    ids = env.ids
    env.c.firm.set_customer_active(ids["priya"], ids["customer_harbor"], False)
    wf = submit(env)  # from office@harbordental.example
    qs = open_questions(env, wf)
    choose = next(q for q in qs if q.kind == "choose_customer")
    assert ids["customer_harbor"] not in [o["value"] for o in choose.options]
    with pytest.raises(ValidationError, match="listed options|no longer active"):
        env.svc.answer_question(ids["priya"], wf, choose.id, ids["customer_harbor"])
    env.c.firm.set_customer_active(ids["priya"], ids["customer_harbor"], True)

    env.c.firm.remove_site(ids["priya"], ids["site_harbor_elm_street"])
    wf2 = submit(env)
    site_q = [q for q in open_questions(env, wf2) if q.kind == "choose_site"]
    offered = [o["value"] for q in site_q for o in q.options]
    assert ids["site_harbor_elm_street"] not in offered
    with env.c.read_sf() as s:
        site = s.get(CustomerSite, ids["site_harbor_elm_street"])
        assert site is not None and site.active is False  # kept, not deleted


def test_site_labels_are_unique_per_customer(env: Env) -> None:
    ids = env.ids
    with env.c.read_sf() as s:
        site = s.get(CustomerSite, ids["site_harbor_elm_street"])
        assert site is not None
        label = site.label
    with pytest.raises(Conflict):
        env.c.firm.save_site(ids["priya"], ids["customer_harbor"], None, label.upper(), "1 Rd X")
    with pytest.raises(ValidationError):
        env.c.firm.save_site(ids["priya"], ids["customer_harbor"], None, "New", "")


# ---------------------------------------------------------------- customer CSV import

GOOD_CUSTOMERS = (
    b"customer_name,other_names,email_domains,contact_email,site_label,site_address\r\n"
    b"Birch Law,Birch;BL,birchlaw.example,office@birchlaw.example,Main,1 Birch Road Town\r\n"
    b"Birch Law,Birch;BL,birchlaw.example,office@birchlaw.example,Annex,3 Birch Road Town\r\n"
    b"Fern Clinic,,fernclinic.example,desk@fernclinic.example,Clinic,8 Fern Way Springfield\r\n"
)


def test_customer_csv_preview_then_import(env: Env) -> None:
    priya = env.ids["priya"]
    checked = env.c.firm.check_customer_import(priya, GOOD_CUSTOMERS)
    assert [c.fields.name for c in checked["customers"]] == ["Birch Law", "Fern Clinic"]
    assert checked["site_count"] == 3
    with env.c.read_sf() as s:  # the preview saved nothing
        assert s.scalars(select(Customer).where(Customer.name == "Birch Law")).first() is None
    with pytest.raises(ValidationError, match="changed after the preview"):
        env.c.firm.import_customers(priya, GOOD_CUSTOMERS, "0" * 64)
    assert env.c.firm.import_customers(priya, GOOD_CUSTOMERS, checked["sha256"]) == 2
    with env.c.read_sf() as s:
        birch = s.scalars(select(Customer).where(Customer.name == "Birch Law")).one()
        assert birch.aliases == ["Birch", "BL"] and birch.tenant_id == env.ids["tenant_brightline"]
        sites = s.scalars(select(CustomerSite).where(CustomerSite.customer_id == birch.id)).all()
        assert sorted(x.label for x in sites) == ["Annex", "Main"]
    imported = [e for e in events(env) if e.event_type == "customers_imported"]
    assert imported[0].data["sha256"] == hashlib.sha256(GOOD_CUSTOMERS).hexdigest()
    with pytest.raises(CatalogImportError):  # importing the same file twice
        env.c.firm.check_customer_import(priya, GOOD_CUSTOMERS)
    with pytest.raises(PermissionDenied):
        env.c.firm.check_customer_import(env.ids["victor"], GOOD_CUSTOMERS)


def test_bad_customer_csv_lists_every_problem_and_saves_nothing(env: Env) -> None:
    bad = (
        b"customer_name,email_domains,contact_email,site_label,site_address\r\n"
        b"=HYPERLINK(1),a.example,a@a.example,Main,1 Road Town\r\n"
        b"Gmail Fans,gmail.com,a@b.example,Main,1 Road Town\r\n"
        b"Oak Co,oak.example,a@oak.example,Main,1 Road Town\r\n"
        b"Oak Co,oak.example,different@oak.example,Second,2 Road Town\r\n"
        b"Pine Co,oak.example,a@pine.example,Main,1 Road Town\r\n"
        b"Ash Co,ash.example,not-an-email,Main,1 Road Town\r\n"
        b"Elm Co,elm.example,a@elm.example,Main,\r\n"
    )
    parsed = parse_customers_csv(bad)
    assert parsed.customers == []
    text = "\n".join(parsed.errors)
    for fragment in (
        "Line 2: customer_name starts with =",
        "Line 3: gmail.com is a shared mail service",
        "Line 5: Oak Co appears on an earlier line with different",
        "Line 6: email domain oak.example is also given for Oak Co",
        "Line 7: Enter a valid contact email",
        "Line 8: The site address must be",
    ):
        assert fragment in text, text
    with pytest.raises(CatalogImportError) as info:
        env.c.firm.check_customer_import(env.ids["priya"], bad)
    assert len(info.value.problems) == 6
    assert parse_customers_csv(b"customer_name\r\n").errors[0].startswith("Missing column(s)")
    assert parse_customers_csv(b"").errors == ["The file is empty."]


# ---------------------------------------------------------------- pricing rules


def test_rule_changes_need_an_owner_and_go_through_a_draft(env: Env) -> None:
    ids = env.ids
    with pytest.raises(PermissionDenied):
        env.c.firm.add_pricing_rule(ids["priya"], "minimum_charge", {"amount": "300"})
    n = env.c.firm.add_pricing_rule(ids["dana"], "minimum_charge", {"amount": "$300"})
    with env.c.read_sf() as s:
        draft = s.scalars(
            select(PricingVersion).where(
                PricingVersion.tenant_id == ids["tenant_brightline"],
                PricingVersion.status == "draft",
            )
        ).one()
        assert draft.version_no == n
        minimums = [r for r in draft.rules if r["kind"] == "minimum_charge"]
        assert minimums == [{"kind": "minimum_charge", "amount": "300.00"}]  # replaced 250
    # Quotes keep using the approved version until the draft is approved.
    wf = submit(env, "Please set up 3 printers next week.", "it@maplestreetlaw.example")
    with env.c.read_sf() as s:
        qv = s.scalars(select(QuoteVersion)).all()[-1]
        assert qv.pricing_version_id != draft.id
    assert state(env, wf) == "draft_ready"
    assert env.c.firm.remove_pricing_rule(ids["dana"], 1) == n
    with pytest.raises(ValidationError):
        env.c.firm.remove_pricing_rule(ids["dana"], 99)


@pytest.mark.parametrize(
    ("kind", "form", "fragment"),
    [
        ("volume_tier", {"sku": "NOPE", "min_qty": "5", "percent_off": "5"}, "Choose a service"),
        ("volume_tier", {"sku": "WS-INSTALL", "min_qty": "5", "percent_off": "95"}, "at most"),
        ("volume_tier", {"sku": "WS-INSTALL", "min_qty": "0", "percent_off": "5"}, "more than"),
        ("minimum_charge", {"amount": "12.345"}, "two decimal places"),
        ("trip_fee", {"amount": "abc"}, "not a number"),
        ("discount_everything", {}, "Unknown kind"),
    ],
)
def test_bad_rules_are_refused(env: Env, kind: str, form: dict[str, str], fragment: str) -> None:
    with pytest.raises(ValidationError, match=fragment):
        env.c.firm.add_pricing_rule(env.ids["dana"], kind, form)


def test_rules_need_a_price_list_first(tmp_path: Path) -> None:
    c = empty_container(tmp_path)
    c.auth.create_company("Oakfield", "Europe/London", "Olive", "o@oak.example", OWNER_PASSWORD)
    with pytest.raises(ValidationError, match="Import and approve a price list first"):
        c.firm.add_pricing_rule(user_id(c, "o@oak.example"), "trip_fee", {"amount": "50"})


# ---------------------------------------------------------------- company settings


def test_company_settings_are_owner_only_and_audited(env: Env) -> None:
    ids = env.ids
    with pytest.raises(PermissionDenied):
        env.c.firm.update_company(ids["priya"], "Brightline IT", "America/Chicago")
    with pytest.raises(Conflict):
        env.c.firm.update_company(ids["dana"], "northgate tech", "America/Chicago")
    with pytest.raises(ValidationError, match="Unknown time zone"):
        env.c.firm.update_company(ids["dana"], "Brightline IT", "Chicago")
    env.c.firm.update_company(ids["dana"], "Brightline IT", "America/Denver")
    with env.c.read_sf() as s:
        t = s.get(Tenant, ids["tenant_brightline"])
        assert t is not None and (t.name, t.timezone) == ("Brightline IT", "America/Denver")
    changed = [e for e in events(env) if e.event_type == "company_settings_changed"]
    assert changed[0].data["changes"]["timezone"] == ["America/Chicago", "America/Denver"]


def test_seeded_companies_show_no_setup_checklist(env: Env) -> None:
    with env.c.read_sf() as s:
        assert all(step["done"] for step in setup_checklist(s, env.ids["tenant_brightline"]))
