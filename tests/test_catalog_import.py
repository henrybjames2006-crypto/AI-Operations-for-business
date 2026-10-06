"""Price list import from CSV: strict parsing, draft first, nothing changes until approval."""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from sqlalchemy import select

from conftest import Env, answer_demo, events, get, make_env, submit
from opsapp.domain.errors import CatalogImportError, PermissionDenied, ValidationError
from opsapp.persistence.models import CatalogItem, PricingVersion, QuoteVersion, WorkflowInstance
from opsapp.workflow.catalog_import import COLUMNS, parse_catalog_csv

HEADER = ",".join(COLUMNS)


def csv_of(*rows: str, header: str = HEADER) -> bytes:
    return ("\r\n".join([header, *rows]) + "\r\n").encode("utf-8")


WS = (
    'WS-INSTALL,Workstation setup,device,"$200.00",1,50,1,yes,'
    "workstation; desktop; pc,Image and install"
)
PRN = "PRN-SETUP,Printer setup,printer,150,1,20,1,yes,printer; printers,"
NEW = "BACKUP-SETUP,Backup setup,site,400,1,5,1,no,backup; backups,Cloud backup for one site"


def test_good_file_parses() -> None:
    result = parse_catalog_csv(csv_of(WS, PRN, NEW))
    assert result.errors == []
    ws = result.rows[0]
    assert ws.unit_price == Decimal("200.00") and ws.keywords == ("workstation", "desktop", "pc")
    assert result.rows[2].onsite is False


def test_excel_byte_order_mark_and_windows_code_page_are_read() -> None:
    assert parse_catalog_csv(b"\xef\xbb\xbf" + csv_of(PRN)).errors == []
    caf = "CAFE-WIFI,Caf\xe9 wifi,site,90,1,5,1,yes,wifi,".encode("cp1252")
    result = parse_catalog_csv(HEADER.encode() + b"\r\n" + caf)
    assert result.errors == [] and result.rows[0].name == "Caf\xe9 wifi"


@pytest.mark.parametrize(
    ("row", "fragment"),
    [
        ("PRN-SETUP,Printer setup,printer,abc,1,20,1,yes,printer,", "is not a number"),
        ("PRN-SETUP,Printer setup,printer,-5,1,20,1,yes,printer,", "more than zero"),
        ("PRN-SETUP,Printer setup,printer,1.005,1,20,1,yes,printer,", "two decimal places"),
        ("PRN-SETUP,Printer setup,printer,150,5,2,1,yes,printer,", "below the minimum"),
        ("PRN-SETUP,Printer setup,printer,150,1,20,1,maybe,printer,", "yes or no"),
        ("PRN-SETUP,Printer setup,printer,150,1,20,1,yes,,", "keyword"),
        ("bad sku!,Printer setup,printer,150,1,20,1,yes,printer,", "SKU"),
        ("PRN-SETUP,,printer,150,1,20,1,yes,printer,", "name is required"),
        ("PRN-SETUP,=HYPERLINK(1),printer,150,1,20,1,yes,printer,", "formula"),
        ("PRN-SETUP,Printer,printer,150,1,20,1,yes,printer,desc,extra", "more cells"),
    ],
)
def test_bad_rows_are_reported_with_their_line(row: str, fragment: str) -> None:
    result = parse_catalog_csv(csv_of(WS, row))
    assert result.rows == []
    assert any(fragment in e and "Line 3" in e for e in result.errors), result.errors


def test_every_bad_row_is_reported_not_just_the_first() -> None:
    bad1 = "PRN-SETUP,Printer setup,printer,abc,1,20,1,yes,printer,"
    bad2 = "NET-DROP,Network drop,drop,150,1,20,1,perhaps,network,"
    result = parse_catalog_csv(csv_of(bad1, bad2))
    assert any("Line 2" in e for e in result.errors) and any("Line 3" in e for e in result.errors)


def test_missing_unknown_and_duplicate_columns_and_skus() -> None:
    assert (
        "Missing column(s): unit_price"
        in parse_catalog_csv(csv_of(header=HEADER.replace("unit_price,", ""))).errors[0]
    )
    assert "Unknown column" in parse_catalog_csv(csv_of(header=HEADER + ",colour")).errors[0]
    dup = parse_catalog_csv(csv_of(PRN, PRN))
    assert any("more than once" in e for e in dup.errors)
    assert parse_catalog_csv(b"").errors == ["The file is empty."]
    assert parse_catalog_csv(csv_of()).errors == ["The file has a header but no services."]


def test_header_spelling_is_forgiving() -> None:
    header = "SKU,Name,Unit,Unit Price,Min Qty,Max Qty,Quantity Step,Onsite,Keywords,Description"
    assert parse_catalog_csv(csv_of(PRN, header=header)).errors == []


def test_oversized_file_is_refused() -> None:
    assert "larger than" in parse_catalog_csv(b"x" * 300_000).errors[0]


# --------------------------------------------------------------------- service


def _discard_seeded_draft(env: Env) -> None:
    with env.c.read_sf() as s:
        draft = s.scalars(select(PricingVersion).where(PricingVersion.status == "draft")).first()
    assert draft is not None
    env.svc.discard_pricing_version(env.ids["dana"], draft.id)


def _active_skus(env: Env) -> set[str]:
    with env.c.read_sf() as s:
        return set(
            s.scalars(
                select(CatalogItem.sku).where(
                    CatalogItem.active.is_(True),
                    CatalogItem.tenant_id == env.ids["tenant_brightline"],
                )
            )
        )


def test_import_is_a_draft_until_approved(env: Env) -> None:
    before = _active_skus(env)
    _discard_seeded_draft(env)
    number = env.svc.import_catalog(env.ids["dana"], csv_of(WS, PRN, NEW), "prices.csv")
    assert _active_skus(env) == before  # nothing changes yet
    wf = submit(env, "Please set up 2 printers.", "it@maplestreetlaw.example")
    assert get(env, WorkflowInstance, wf).state == "draft_ready"

    with env.c.read_sf() as s:
        pv = s.scalars(select(PricingVersion).where(PricingVersion.version_no == number)).one()
    env.svc.approve_pricing_version(env.ids["dana"], pv.id)
    assert _active_skus(env) == {"WS-INSTALL", "PRN-SETUP", "BACKUP-SETUP"}
    kinds = [e.event_type for e in events(env)]
    assert "catalog_imported" in kinds and "catalog_services_removed" in kinds

    # A quote made before keeps its prices; a new one uses the imported prices.
    with env.c.read_sf() as s:
        old_total = s.scalars(select(QuoteVersion.total)).one()
    assert old_total == Decimal("325.00")  # pricing version 3, unchanged
    new_wf = submit(env, "Could you set up 2 printers for us?", "it@maplestreetlaw.example")
    with env.c.read_sf() as s:
        totals = sorted(s.scalars(select(QuoteVersion.total)))
    assert get(env, WorkflowInstance, new_wf).state == "draft_ready"
    assert totals == [Decimal("325.00"), Decimal("375.00")]  # 2 x 150 + 75 trip fee


def test_new_service_is_read_only_after_approval(env: Env) -> None:
    _discard_seeded_draft(env)
    number = env.svc.import_catalog(env.ids["dana"], csv_of(WS, PRN, NEW), "prices.csv")
    wf = submit(env, "We need backup setup for 1 site.", "it@maplestreetlaw.example")
    scope = get(env, WorkflowInstance, wf).scope
    assert all(i["sku"] != "BACKUP-SETUP" for i in scope["items"])
    with env.c.read_sf() as s:
        pv = s.scalars(select(PricingVersion).where(PricingVersion.version_no == number)).one()
    env.svc.approve_pricing_version(env.ids["dana"], pv.id)
    wf2 = submit(env, "We need backup setup for 1 site, please.", "it@maplestreetlaw.example")
    assert any(i["sku"] == "BACKUP-SETUP" for i in get(env, WorkflowInstance, wf2).scope["items"])


def test_bad_file_saves_nothing(env: Env) -> None:
    _discard_seeded_draft(env)
    with env.c.read_sf() as s:
        count = len(list(s.scalars(select(PricingVersion))))
    with pytest.raises(CatalogImportError) as exc:
        env.svc.import_catalog(env.ids["dana"], csv_of(WS, "X,,,,,,,,,"), "bad.csv")
    assert len(exc.value.problems) > 1
    with env.c.read_sf() as s:
        assert len(list(s.scalars(select(PricingVersion)))) == count


def test_only_one_draft_at_a_time_and_only_owners_import(env: Env) -> None:
    with pytest.raises(ValidationError, match="still a draft"):
        env.svc.import_catalog(env.ids["dana"], csv_of(PRN), "p.csv")
    with pytest.raises(PermissionDenied):
        env.svc.import_catalog(env.ids["priya"], csv_of(PRN), "p.csv")
    with pytest.raises(PermissionDenied):
        env.svc.import_catalog(env.ids["marcus"], csv_of(PRN), "p.csv")


def test_import_stays_inside_the_tenant(env: Env) -> None:
    _discard_seeded_draft(env)
    env.svc.import_catalog(env.ids["dana"], csv_of(PRN), "p.csv")
    with env.c.read_sf() as s:
        north = s.scalars(
            select(CatalogItem).where(CatalogItem.tenant_id == env.ids["tenant_northgate"])
        ).all()
    assert all(i.tenant_id == env.ids["tenant_northgate"] for i in north)
    assert not any(i.sku == "PRN-SETUP" and not i.active for i in north)


# --------------------------------------------------------------------- web


def test_import_over_http(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from opsapp.web.app import create_app
    from test_web import client_as

    env = make_env(tmp_path)
    app = create_app(env.c.settings, container=env.c)
    try:
        dana = client_as(app, env, "dana")
        page = dana.get("/catalog")
        assert "Import a price list" in page.text
        template = dana.get("/catalog/template.csv")
        assert template.status_code == 200 and template.text.startswith(HEADER)
        assert parse_catalog_csv(template.content).errors == []

        draft_id = re.search(r"/catalog/pricing/([^/]+)/discard", page.text).group(1)
        assert (
            dana.post(f"/catalog/pricing/{draft_id}/discard", data={"csrf": dana.csrf}).status_code
            == 303
        )

        bad = dana.post(
            "/catalog/import",
            data={"csrf": dana.csrf},
            files={"file": ("bad.csv", csv_of("PRN-SETUP,P,printer,abc,1,2,1,yes,printer,"))},
        )
        assert bad.status_code == 422 and "Nothing was saved" in bad.text and "Line 2" in bad.text

        good = dana.post(
            "/catalog/import",
            data={"csrf": dana.csrf},
            files={"file": ("prices.csv", template.content)},
        )
        assert good.status_code == 303
        assert "Imported from prices.csv" in dana.get("/catalog").text

        priya = client_as(app, env, "priya")
        assert "Import a price list" not in priya.get("/catalog").text
        r = priya.post(
            "/catalog/import", data={"csrf": priya.csrf}, files={"file": ("p.csv", csv_of(PRN))}
        )
        assert r.status_code == 403
    finally:
        env.c.engine.dispose()


def test_seeded_flow_still_works_after_reimporting_the_template(env: Env) -> None:
    from opsapp.web.views import catalog_csv

    with env.c.read_sf() as s:
        dana = s.get(
            __import__("opsapp.persistence.models", fromlist=["User"]).User, env.ids["dana"]
        )
        body = catalog_csv(s, dana)
    _discard_seeded_draft(env)
    number = env.svc.import_catalog(env.ids["dana"], body.encode(), "same.csv")
    with env.c.read_sf() as s:
        pv = s.scalars(select(PricingVersion).where(PricingVersion.version_no == number)).one()
    env.svc.approve_pricing_version(env.ids["dana"], pv.id)
    wf = submit(env)
    answer_demo(env, wf)
    with env.c.read_sf() as s:
        assert s.scalars(select(QuoteVersion.total)).one() == Decimal("1475.00")
