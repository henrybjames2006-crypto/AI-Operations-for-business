"""0.5.0 pages over HTTP: customers, customer import, pricing rules, company settings."""

import re

from sqlalchemy import select

from opsapp.persistence.models import Customer, CustomerSite, PricingVersion, Tenant
from test_firm_setup import GOOD_CUSTOMERS
from test_web import client_as, web  # noqa: F401 - pytest fixture


def test_operator_adds_and_edits_a_customer_through_the_pages(web) -> None:  # noqa: F811
    env, app = web
    priya = client_as(app, env, "priya")
    page = priya.get("/customers")
    assert page.status_code == 200 and "Harbor Dental Group" in page.text
    assert "Add a customer" in page.text
    r = priya.post(
        "/customers",
        data={
            "name": "Cedar Vets",
            "other_names": "Cedar",
            "email_domains": "@CedarVets.example",
            "contact_email": "desk@cedarvets.example",
            "csrf": priya.csrf,
        },
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/customers/cus")
    url = r.headers["location"]
    r = priya.post(
        url + "/sites", data={"label": "Clinic", "address": "4 Cedar Road", "csrf": priya.csrf}
    )
    assert r.status_code == 303
    page = priya.get(url)
    assert "Site Clinic added to Cedar Vets" in page.text and "4 Cedar Road" in page.text
    site_id = re.search(r'/sites/(sit[^/"]+)/remove', page.text).group(1)  # type: ignore[union-attr]
    assert priya.post(f"{url}/sites/{site_id}/remove", data={"csrf": priya.csrf}).status_code == 303
    r = priya.post(
        "/customers",
        data={
            "name": "Gmail Co",
            "email_domains": "gmail.com",
            "contact_email": "a@b.example",
            "csrf": priya.csrf,
        },
        follow_redirects=True,
    )
    assert "shared mail service" in r.text and "Site removed" in r.text
    with env.c.read_sf() as s:
        cedar = s.scalars(select(Customer).where(Customer.name == "Cedar Vets")).one()
        assert cedar.email_domains == ["cedarvets.example"]
        assert s.get(CustomerSite, site_id).active is False  # type: ignore[union-attr]
        assert s.scalars(select(Customer).where(Customer.name == "Gmail Co")).first() is None
    assert priya.get("/company").status_code == 403


def test_viewer_sees_customers_but_cannot_change_them(web) -> None:  # noqa: F811
    env, app = web
    victor = client_as(app, env, "victor")
    page = victor.get("/customers")
    assert page.status_code == 200 and "Add a customer" not in page.text
    r = victor.post(
        "/customers",
        data={"name": "X Co", "contact_email": "a@x.example", "csrf": victor.csrf},
    )
    assert r.status_code == 403
    r = victor.post(
        f"/customers/{env.ids['customer_harbor']}/deactivate", data={"csrf": victor.csrf}
    )
    assert r.status_code == 403
    nia = client_as(app, env, "nia")
    assert nia.get(f"/customers/{env.ids['customer_harbor']}").status_code == 404


def test_customer_import_preview_then_confirm(web) -> None:  # noqa: F811
    env, app = web
    priya = client_as(app, env, "priya")
    r = priya.post(
        "/customers/import",
        files={"file": ("customers.csv", GOOD_CUSTOMERS, "text/csv")},
        data={"csrf": priya.csrf},
    )
    assert r.status_code == 200 and "Nothing has been saved yet" in r.text
    assert "2 customers with 3 sites" in r.text
    payload = re.search(r'name="payload" value="([^"]+)"', r.text).group(1)  # type: ignore[union-attr]
    sha = re.search(r'name="sha256" value="([^"]+)"', r.text).group(1)  # type: ignore[union-attr]
    with env.c.read_sf() as s:
        assert s.scalars(select(Customer).where(Customer.name == "Birch Law")).first() is None
    r = priya.post(
        "/customers/import/confirm",
        data={"payload": payload, "sha256": sha, "csrf": priya.csrf},
        follow_redirects=True,
    )
    assert "2 customers imported" in r.text and "Birch Law" in r.text
    bad = b"customer_name,email_domains,contact_email,site_label,site_address\r\n=1,a,b,c,d\r\n"
    r = priya.post(
        "/customers/import",
        files={"file": ("bad.csv", bad, "text/csv")},
        data={"csrf": priya.csrf},
    )
    assert r.status_code == 422 and "Nothing was saved" in r.text
    assert "/customers/template.csv" in r.text
    assert priya.get("/customers/template.csv").text.startswith("customer_name,")


def test_owner_edits_rules_and_company_settings(web) -> None:  # noqa: F811
    env, app = web
    dana = client_as(app, env, "dana")
    page = dana.get("/catalog")
    assert "Pricing rules" in page.text and "Volume discount" in page.text
    r = dana.post(
        "/catalog/rules",
        data={"kind": "trip_fee", "amount": "90", "label": "Call-out fee", "csrf": dana.csrf},
        follow_redirects=True,
    )
    assert "Rule saved in draft pricing version" in r.text
    assert "Call-out fee: $90.00 once per quote" in r.text
    with env.c.read_sf() as s:
        draft = s.scalars(
            select(PricingVersion).where(
                PricingVersion.tenant_id == env.ids["tenant_brightline"],
                PricingVersion.status == "draft",
            )
        ).one()
        position = [r["kind"] for r in draft.rules].index("trip_fee") + 1
    r = dana.post(f"/catalog/rules/{position}/remove", data={"csrf": dana.csrf})
    assert r.status_code == 303
    priya = client_as(app, env, "priya")
    assert "Pricing rules" not in priya.get("/catalog").text
    r = priya.post("/catalog/rules", data={"kind": "trip_fee", "amount": "1", "csrf": priya.csrf})
    assert r.status_code == 403

    page = dana.get("/company")
    assert page.status_code == 200 and "America/Chicago" in page.text
    r = dana.post(
        "/company",
        data={"name": "Brightline IT", "timezone": "America/Denver", "csrf": dana.csrf},
        follow_redirects=True,
    )
    assert "Company settings saved" in r.text and "Brightline IT" in r.text
    with env.c.read_sf() as s:
        t = s.get(Tenant, env.ids["tenant_brightline"])
        assert t is not None and t.timezone == "America/Denver"
    assert "Set up your company" not in dana.get("/").text
