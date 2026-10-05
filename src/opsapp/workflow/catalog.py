"""Load approved pricing as a domain snapshot."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.errors import PricingError
from ..domain.pricing import PriceEntry, PricingSnapshot
from ..persistence.models import CatalogItem, PriceEntryRow, PricingVersion


def current_pricing_version(s: Session, tenant_id: str, now: datetime) -> PricingVersion:
    pv = s.scalars(
        select(PricingVersion)
        .where(
            PricingVersion.tenant_id == tenant_id,
            PricingVersion.status == "approved",
            PricingVersion.effective_from <= now,
        )
        .order_by(PricingVersion.version_no.desc())
        .limit(1)
    ).first()
    if pv is None:
        raise PricingError("No approved pricing version is in effect for this business.")
    return pv


def snapshot_for(s: Session, pv: PricingVersion) -> PricingSnapshot:
    rows = s.execute(
        select(PriceEntryRow, CatalogItem)
        .join(CatalogItem, CatalogItem.id == PriceEntryRow.catalog_item_id)
        .where(PriceEntryRow.pricing_version_id == pv.id, CatalogItem.active.is_(True))
    ).all()
    entries = {
        item.sku: PriceEntry(
            sku=item.sku,
            name=item.name,
            unit=item.unit,
            unit_price=row.unit_price,
            currency=row.currency,
            min_qty=row.min_qty,
            max_qty=row.max_qty,
            quantity_step=item.quantity_step,
            onsite=item.onsite,
        )
        for row, item in rows
    }
    return PricingSnapshot(
        pricing_version_id=pv.id,
        version_no=pv.version_no,
        currency=pv.currency,
        entries=entries,
        rules=list(pv.rules),
    )


def snapshot_to_json(snap: PricingSnapshot, skus: list[str]) -> dict[str, Any]:
    """The subset of a snapshot a quote used, stored with the quote for reproduction."""
    return {
        "pricing_version_id": snap.pricing_version_id,
        "version_no": snap.version_no,
        "currency": snap.currency,
        "entries": {
            sku: {
                "sku": e.sku,
                "name": e.name,
                "unit": e.unit,
                "unit_price": str(e.unit_price),
                "currency": e.currency,
                "min_qty": str(e.min_qty),
                "max_qty": str(e.max_qty),
                "quantity_step": str(e.quantity_step),
                "onsite": e.onsite,
            }
            for sku, e in snap.entries.items()
            if sku in skus
        },
        "rules": list(snap.rules),
    }


def snapshot_from_json(data: dict[str, Any]) -> PricingSnapshot:
    from decimal import Decimal

    entries = {
        sku: PriceEntry(
            sku=e["sku"],
            name=e["name"],
            unit=e["unit"],
            unit_price=Decimal(e["unit_price"]),
            currency=e["currency"],
            min_qty=Decimal(e["min_qty"]),
            max_qty=Decimal(e["max_qty"]),
            quantity_step=Decimal(e["quantity_step"]),
            onsite=bool(e["onsite"]),
        )
        for sku, e in data["entries"].items()
    }
    return PricingSnapshot(
        pricing_version_id=data["pricing_version_id"],
        version_no=int(data["version_no"]),
        currency=data["currency"],
        entries=entries,
        rules=data["rules"],
    )
