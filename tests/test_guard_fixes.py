"""Fixes for problems found in the 0.2.0 live comparison (customer spelling, site in a name)."""

from __future__ import annotations

from datetime import date

from conftest import Env, get
from opsapp.ai.guard import validate_output
from opsapp.ai.ports import ExtractionRun
from opsapp.ai.schema import CatalogHint, CustomerHint, ExtractionContext, ExtractionOutput
from opsapp.persistence.models import WorkflowInstance

CTX = ExtractionContext(
    today=date(2026, 10, 5),
    customers=[
        CustomerHint(name="Summit Bakery Co", aliases=["Summit Bakery"]),
        CustomerHint(name="Harbor Dental Group", aliases=["Harbor Dental"]),
    ],
    site_labels=["Bakery", "Warehouse", "Elm Street"],
    catalog=[CatalogHint(sku="NET-DROP", name="Network drop", keywords=["network drop"])],
)


def _out(**kw: object) -> ExtractionOutput:
    return ExtractionOutput.model_validate({"request_type": "service_request", **kw})


def test_full_listed_name_is_kept_when_the_text_uses_an_alias() -> None:
    text = "Summit Bakery here: 2 network drops at the bakery please."
    out, problems = validate_output(_out(customer_mentions=["Summit Bakery Co"]), text, CTX)
    assert out.customer_mentions == ["Summit Bakery"]
    assert problems == []


def test_customer_not_in_the_text_is_still_dropped() -> None:
    text = "Please add 2 network drops."
    out, problems = validate_output(_out(customer_mentions=["Summit Bakery Co"]), text, CTX)
    assert out.customer_mentions == []
    assert any("Summit Bakery Co" in p for p in problems)


def test_unknown_customer_is_dropped() -> None:
    out, _ = validate_output(_out(customer_mentions=["Acme"]), "Acme needs drops", CTX)
    assert out.customer_mentions == []


def test_site_label_inside_a_customer_name_is_not_a_site() -> None:
    text = "We need 2 laptops set up please.\n\nDana\nSummit Bakery Co"
    out, _ = validate_output(_out(site_mentions=["Bakery"]), text, CTX)
    assert out.site_mentions == []


def test_site_named_outside_the_customer_name_is_kept() -> None:
    text = "Summit Bakery here: 2 network drops at the bakery please."
    out, _ = validate_output(_out(site_mentions=["Bakery"]), text, CTX)
    assert out.site_mentions == ["Bakery"]


class FullNameReader:
    """A fake AI reader that names the customer by its full listed name."""

    provider = "fake"
    model_id = "full-name"

    def extract(self, text: str, sender: str, context: ExtractionContext) -> ExtractionRun:
        out = _out(
            customer_mentions=["Summit Bakery Co"],
            site_mentions=["Bakery"],
            items=[
                {
                    "sku": "NET-DROP",
                    "quantity": "2",
                    "quantity_text": "2",
                    "evidence": "2 network drops at the bakery",
                }
            ],
        )
        return ExtractionRun(
            output=out,
            provider=self.provider,
            model_id=self.model_id,
            latency_ms=1,
            input_chars=len(text),
            output_chars=0,
        )


def test_summit_bakery_case_reaches_a_draft_with_a_full_name_reader(env: Env) -> None:
    env.c.service.extractor = FullNameReader()
    wf_id = env.svc.submit_request(
        env.ids["priya"], "Summit Bakery here: 2 network drops at the bakery please.", "", "k1"
    ).workflow_id
    wf = get(env, WorkflowInstance, wf_id)
    assert wf.scope["customer_id"] == env.ids["customer_summit"]
    assert wf.state == "draft_ready"
