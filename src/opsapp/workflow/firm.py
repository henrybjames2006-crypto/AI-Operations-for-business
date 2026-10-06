"""Setting up a firm in the app: customers and sites, pricing rules, company settings.

Every change is permission-checked, scoped to the actor's company and written to its audit
log in the same transaction. Customers and sites are deactivated, never deleted, so quotes
already made keep their history.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth.service import check_timezone
from ..authz import get_scoped, require
from ..domain.errors import CatalogImportError, Conflict, PricingError, ValidationError
from ..domain.pricing import RULE_ORDER
from ..domain.roles import Permission, Role
from ..ids import new_id
from ..persistence.models import (
    CatalogItem,
    Customer,
    CustomerSite,
    PriceEntryRow,
    PricingVersion,
    Tenant,
    User,
)
from .catalog import current_pricing_version
from .customer_import import (
    CustomerFields,
    clean_customer,
    clean_site,
    parse_customers_csv,
)
from .service import WorkflowService

MAX_RULE_AMOUNT = Decimal("100000")
RULE_TEXT = {
    "volume_tier": "volume discount",
    "minimum_charge": "minimum charge",
    "trip_fee": "trip fee",
}


def describe_rule(rule: dict[str, Any]) -> str:
    kind = rule.get("kind")
    if kind == "volume_tier":
        return (
            f"Volume discount: {rule['percent_off']}% off {rule['sku']} "
            f"at {rule['min_qty']} or more"
        )
    if kind == "minimum_charge":
        return f"Minimum charge: ${rule['amount']} for items (after discounts)"
    if kind == "trip_fee":
        return (
            f"{rule.get('label', 'Trip fee')}: ${rule['amount']} once per quote with on-site work"
        )
    return f"Unknown rule {kind!r}"


def _amount(raw: str, label: str, *, cents: bool, limit: Decimal) -> Decimal:
    text = raw.strip().replace("$", "").replace(",", "").rstrip("%")
    try:
        value = Decimal(text)
        if not value.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        raise ValidationError(f"{label} {raw.strip()!r} is not a number.") from None
    if value <= 0:
        raise ValidationError(f"{label} must be more than zero.")
    if value > limit:
        raise ValidationError(f"{label} must be at most {limit}.")
    if cents and value.as_tuple().exponent < -2:  # type: ignore[operator]
        raise ValidationError(f"{label} has more than two decimal places.")
    return value


def _plain(value: Decimal) -> str:
    """10 rather than 1E+1 or 10.00, 2.5 rather than 2.50."""
    return format(value.normalize(), "f")


class FirmService:
    def __init__(self, workflows: WorkflowService) -> None:
        self.w = workflows
        self.clock = workflows.clock

    # ------------------------------------------------------------ company settings

    def update_company(self, actor_id: str, name: str, timezone: str) -> None:
        def run(s: Session, actor: User) -> None:
            require(actor, Permission.MANAGE_COMPANY)
            tenant = s.get(Tenant, actor.tenant_id)
            if tenant is None:  # a broken invariant, never a user error
                raise RuntimeError("tenant is missing")
            company = " ".join(name.split())
            if not 2 <= len(company) <= 100:
                raise ValidationError("The company name must be 2 to 100 characters.")
            zone = check_timezone(timezone)
            taken = s.scalars(
                select(Tenant).where(
                    func.lower(Tenant.name) == company.lower(), Tenant.id != tenant.id
                )
            ).first()
            if taken is not None:
                raise Conflict("Another company already uses that name.")
            changes = {}
            if company != tenant.name:
                changes["name"] = [tenant.name, company]
            if zone != tenant.timezone:
                changes["timezone"] = [tenant.timezone, zone]
            if not changes:
                raise ValidationError("Nothing was changed.")
            tenant.name = company
            tenant.timezone = zone
            said = "; ".join(f"{k} from {old!r} to {new!r}" for k, (old, new) in changes.items())
            self.w._audit(
                s,
                None,
                actor,
                "company_settings_changed",
                f"Company settings changed by {actor.display_name}: {said}.",
                {"changes": changes},
            )

        self.w._run(actor_id, run)

    # ------------------------------------------------------------ customers and sites

    def _check_customer_unique(
        self, s: Session, tenant_id: str, fields: CustomerFields, customer_id: str | None
    ) -> None:
        others = [
            c
            for c in s.scalars(select(Customer).where(Customer.tenant_id == tenant_id))
            if c.id != customer_id
        ]
        same = next((c for c in others if c.name.lower() == fields.name.lower()), None)
        if same is not None:
            hint = "" if same.active else " It is deactivated: reactivate it instead."
            raise Conflict(f"A customer called {same.name!r} already exists.{hint}")
        names = {n.lower() for n in [fields.name, *fields.aliases]}
        for c in others:
            if not c.active:
                continue
            clash = next((n for n in [c.name, *c.aliases] if n.lower() in names), None)
            if clash is not None:
                raise Conflict(
                    f"{clash!r} is already a name of {c.name}, so requests could not tell "
                    "them apart."
                )
        for d in fields.email_domains:
            owner = next((c for c in others if c.active and d in c.email_domains), None)
            if owner is not None:
                raise Conflict(f"Email domain {d} is already used by {owner.name}.")

    def save_customer(
        self,
        actor_id: str,
        customer_id: str | None,
        name: str,
        other_names: str,
        email_domains: str,
        contact_email: str,
    ) -> str:
        """Add a customer (customer_id None) or change one. Returns the customer id."""

        def run(s: Session, actor: User) -> str:
            require(actor, Permission.MANAGE_CUSTOMERS)
            fields, problems = clean_customer(name, other_names, email_domains, contact_email)
            if problems:
                raise ValidationError(" ".join(problems))
            self._check_customer_unique(s, actor.tenant_id, fields, customer_id)
            if customer_id is None:
                customer = Customer(
                    id=new_id("customer"),
                    tenant_id=actor.tenant_id,
                    name=fields.name,
                    aliases=fields.aliases,
                    email_domains=fields.email_domains,
                    contact_email=fields.contact_email,
                    active=True,
                )
                s.add(customer)
                s.flush()
                self.w._audit(
                    s,
                    None,
                    actor,
                    "customer_added",
                    f"Customer {customer.name} added by {actor.display_name}.",
                    {"customer_id": customer.id, **self._customer_data(customer)},
                )
                return customer.id
            customer = get_scoped(s, Customer, customer_id, actor.tenant_id)
            before = self._customer_data(customer)
            customer.name = fields.name
            customer.aliases = fields.aliases
            customer.email_domains = fields.email_domains
            customer.contact_email = fields.contact_email
            after = self._customer_data(customer)
            changed = sorted(k for k in after if after[k] != before[k])
            if not changed:
                raise ValidationError("Nothing was changed.")
            self.w._audit(
                s,
                None,
                actor,
                "customer_changed",
                f"Customer {customer.name} changed by {actor.display_name}: "
                f"{', '.join(c.replace('_', ' ') for c in changed)}.",
                {
                    "customer_id": customer.id,
                    "before": {k: before[k] for k in changed},
                    "after": {k: after[k] for k in changed},
                },
            )
            return customer.id

        return self.w._run(actor_id, run)

    @staticmethod
    def _customer_data(c: Customer) -> dict[str, Any]:
        return {
            "name": c.name,
            "other_names": list(c.aliases),
            "email_domains": list(c.email_domains),
            "contact_email": c.contact_email,
        }

    def set_customer_active(self, actor_id: str, customer_id: str, active: bool) -> None:
        def run(s: Session, actor: User) -> None:
            require(actor, Permission.MANAGE_CUSTOMERS)
            customer = get_scoped(s, Customer, customer_id, actor.tenant_id)
            if customer.active == active:
                raise ValidationError(
                    "That customer is already " + ("active." if active else "deactivated.")
                )
            if active:
                fields = CustomerFields(
                    customer.name,
                    list(customer.aliases),
                    list(customer.email_domains),
                    customer.contact_email,
                )
                self._check_customer_unique(s, actor.tenant_id, fields, customer.id)
            customer.active = active
            self.w._audit(
                s,
                None,
                actor,
                "customer_reactivated" if active else "customer_deactivated",
                f"Customer {customer.name} "
                + ("reactivated" if active else "deactivated")
                + f" by {actor.display_name}."
                + ("" if active else " Existing requests and quotes keep it; new ones can't."),
                {"customer_id": customer.id},
            )

        self.w._run(actor_id, run)

    def save_site(
        self, actor_id: str, customer_id: str, site_id: str | None, label: str, address: str
    ) -> str:
        def run(s: Session, actor: User) -> str:
            require(actor, Permission.MANAGE_CUSTOMERS)
            customer = get_scoped(s, Customer, customer_id, actor.tenant_id)
            if not customer.active:
                raise ValidationError("Reactivate the customer before changing its sites.")
            fields, problems = clean_site(label, address)
            if problems:
                raise ValidationError(" ".join(problems))
            clash = s.scalars(
                select(CustomerSite).where(
                    CustomerSite.customer_id == customer.id,
                    CustomerSite.active.is_(True),
                    func.lower(CustomerSite.label) == fields.label.lower(),
                    CustomerSite.id != (site_id or ""),
                )
            ).first()
            if clash is not None:
                raise Conflict(f"{customer.name} already has a site called {clash.label!r}.")
            if site_id is None:
                site = CustomerSite(
                    id=new_id("site"),
                    tenant_id=actor.tenant_id,
                    customer_id=customer.id,
                    label=fields.label,
                    address=fields.address,
                    active=True,
                )
                s.add(site)
                s.flush()
                self.w._audit(
                    s,
                    None,
                    actor,
                    "site_added",
                    f"Site {site.label} added to {customer.name} by {actor.display_name}.",
                    {"customer_id": customer.id, "site_id": site.id, "address": site.address},
                )
                return site.id
            site = get_scoped(s, CustomerSite, site_id, actor.tenant_id)
            if site.customer_id != customer.id or not site.active:
                raise ValidationError("That site can't be changed.")
            before = {"label": site.label, "address": site.address}
            site.label, site.address = fields.label, fields.address
            after = {"label": site.label, "address": site.address}
            if after == before:
                raise ValidationError("Nothing was changed.")
            self.w._audit(
                s,
                None,
                actor,
                "site_changed",
                f"Site {before['label']} of {customer.name} changed by {actor.display_name}.",
                {"customer_id": customer.id, "site_id": site.id, "before": before, "after": after},
            )
            return site.id

        return self.w._run(actor_id, run)

    def remove_site(self, actor_id: str, site_id: str) -> None:
        """Stop offering a site. It stays on requests and quotes that already use it."""

        def run(s: Session, actor: User) -> None:
            require(actor, Permission.MANAGE_CUSTOMERS)
            site = get_scoped(s, CustomerSite, site_id, actor.tenant_id)
            if not site.active:
                raise ValidationError("That site was already removed.")
            customer = get_scoped(s, Customer, site.customer_id, actor.tenant_id)
            site.active = False
            self.w._audit(
                s,
                None,
                actor,
                "site_removed",
                f"Site {site.label} of {customer.name} removed by {actor.display_name}. "
                "Existing requests and quotes keep it.",
                {"customer_id": customer.id, "site_id": site.id},
            )

        self.w._run(actor_id, run)

    def check_customer_import(self, actor_id: str, data: bytes) -> dict[str, Any]:
        """Check a customer CSV without saving anything. Raises CatalogImportError."""

        def run(s: Session, actor: User) -> dict[str, Any]:
            require(actor, Permission.MANAGE_CUSTOMERS)
            return self._checked_import(s, actor, data)

        return self.w._run(actor_id, run)

    def _checked_import(self, s: Session, actor: User, data: bytes) -> dict[str, Any]:
        parsed = parse_customers_csv(data)
        if parsed.errors:
            raise CatalogImportError(parsed.errors)
        problems: list[str] = []
        seen: dict[str, str] = {}
        for c in parsed.customers:
            try:
                self._check_customer_unique(s, actor.tenant_id, c.fields, None)
            except Conflict as exc:
                problems.append(f"{c.fields.name}: {exc}")
            for n in [c.fields.name, *c.fields.aliases]:
                other = seen.setdefault(n.lower(), c.fields.name)
                if other != c.fields.name:
                    problems.append(
                        f"{c.fields.name}: {n!r} is also a name of {other} in this file."
                    )
        if problems:
            raise CatalogImportError(problems)
        return {
            "customers": parsed.customers,
            "site_count": parsed.site_count,
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def import_customers(self, actor_id: str, data: bytes, expected_sha256: str) -> int:
        """Add every customer and site in a checked file, or nothing. Returns the count."""

        def run(s: Session, actor: User) -> int:
            require(actor, Permission.MANAGE_CUSTOMERS)
            checked = self._checked_import(s, actor, data)
            if checked["sha256"] != expected_sha256:
                raise ValidationError("The file changed after the preview. Upload it again.")
            for c in checked["customers"]:
                customer = Customer(
                    id=new_id("customer"),
                    tenant_id=actor.tenant_id,
                    name=c.fields.name,
                    aliases=c.fields.aliases,
                    email_domains=c.fields.email_domains,
                    contact_email=c.fields.contact_email,
                    active=True,
                )
                s.add(customer)
                s.flush()
                for site in c.sites:
                    s.add(
                        CustomerSite(
                            id=new_id("site"),
                            tenant_id=actor.tenant_id,
                            customer_id=customer.id,
                            label=site.label,
                            address=site.address,
                            active=True,
                        )
                    )
            count = len(checked["customers"])
            self.w._audit(
                s,
                None,
                actor,
                "customers_imported",
                f"{count} customers with {checked['site_count']} sites imported from a CSV "
                f"file by {actor.display_name}.",
                {
                    "customers": [c.fields.name for c in checked["customers"]],
                    "sites": checked["site_count"],
                    "sha256": checked["sha256"],
                },
            )
            return count

        return self.w._run(actor_id, run)

    # ------------------------------------------------------------ pricing rules

    def _rules_draft(self, s: Session, actor: User) -> PricingVersion:
        """The draft to put rule changes in: the open draft, or a new copy of the current."""
        draft = s.scalars(
            select(PricingVersion).where(
                PricingVersion.tenant_id == actor.tenant_id, PricingVersion.status == "draft"
            )
        ).first()
        if draft is not None:
            return draft
        now = self.clock.now()
        try:
            current = current_pricing_version(s, actor.tenant_id, now)
        except PricingError:
            raise ValidationError(
                "Import and approve a price list first; rules apply to its services."
            ) from None
        number = (
            s.scalar(
                select(func.max(PricingVersion.version_no)).where(
                    PricingVersion.tenant_id == actor.tenant_id
                )
            )
            or 0
        ) + 1
        draft = PricingVersion(
            id=new_id("pricing_version"),
            tenant_id=actor.tenant_id,
            version_no=number,
            currency=current.currency,
            status="draft",
            effective_from=now,
            rules=list(current.rules),
            notes=(
                f"Rule changes by {actor.display_name}. Prices copied from version "
                f"{current.version_no}."
            ),
            catalog_changes=None,
        )
        s.add(draft)
        s.flush()
        for e in s.scalars(
            select(PriceEntryRow).where(PriceEntryRow.pricing_version_id == current.id)
        ):
            s.add(
                PriceEntryRow(
                    id=new_id("price_entry"),
                    tenant_id=actor.tenant_id,
                    pricing_version_id=draft.id,
                    catalog_item_id=e.catalog_item_id,
                    unit_price=e.unit_price,
                    currency=e.currency,
                    min_qty=e.min_qty,
                    max_qty=e.max_qty,
                )
            )
        return draft

    def _draft_skus(self, s: Session, draft: PricingVersion) -> set[str]:
        return set(
            s.scalars(
                select(CatalogItem.sku)
                .join(PriceEntryRow, PriceEntryRow.catalog_item_id == CatalogItem.id)
                .where(PriceEntryRow.pricing_version_id == draft.id)
            )
        )

    def add_pricing_rule(self, actor_id: str, kind: str, form: dict[str, str]) -> int:
        """Add or replace a rule in a draft pricing version. Returns the draft's number.

        A minimum charge or trip fee replaces the one already there; a volume discount
        replaces one for the same service and quantity.
        """

        def run(s: Session, actor: User) -> int:
            require(actor, Permission.MANAGE_CATALOG)
            if kind not in RULE_ORDER:
                raise ValidationError("Unknown kind of pricing rule.")
            draft = self._rules_draft(s, actor)
            rule: dict[str, str]
            if kind == "volume_tier":
                sku = form.get("sku", "").strip().upper()
                if sku not in self._draft_skus(s, draft):
                    raise ValidationError(f"Choose a service from the price list (not {sku!r}).")
                qty = _amount(
                    form.get("min_qty", ""), "The quantity", cents=False, limit=Decimal(10000)
                )
                pct = _amount(
                    form.get("percent_off", ""), "The discount", cents=True, limit=Decimal(90)
                )
                rule = {
                    "kind": kind,
                    "sku": sku,
                    "min_qty": _plain(qty),
                    "percent_off": _plain(pct),
                }
                same = [
                    r
                    for r in draft.rules
                    if r.get("kind") == kind
                    and r.get("sku") == sku
                    and Decimal(str(r.get("min_qty"))) == qty
                ]
            else:
                money = _amount(
                    form.get("amount", ""), "The amount", cents=True, limit=MAX_RULE_AMOUNT
                )
                rule = {"kind": kind, "amount": str(money.quantize(Decimal("0.01")))}
                if kind == "trip_fee":
                    label = " ".join(form.get("label", "").split()) or "Site visit trip fee"
                    if len(label) > 80:
                        raise ValidationError("The trip fee label must be up to 80 characters.")
                    rule = {"kind": kind, "sku": "SITE-TRIP", "label": label, **rule}
                same = [r for r in draft.rules if r.get("kind") == kind]
            kept = [r for r in draft.rules if r not in same]
            draft.rules = sorted([*kept, rule], key=lambda r: RULE_ORDER.index(str(r.get("kind"))))
            verb = "changed" if same else "added"
            self.w._audit(
                s,
                None,
                actor,
                "pricing_rule_changed" if same else "pricing_rule_added",
                f"Pricing rule {verb} in draft pricing version {draft.version_no} by "
                f"{actor.display_name}: {describe_rule(rule)}. Nothing changes until the "
                "draft is approved.",
                {"pricing_version_id": draft.id, "rule": rule, "replaced": same},
            )
            return draft.version_no

        return self.w._run(actor_id, run)

    def remove_pricing_rule(self, actor_id: str, position: int) -> int:
        """Remove the rule at a position (from 1) of the draft's list. Returns its number."""

        def run(s: Session, actor: User) -> int:
            require(actor, Permission.MANAGE_CATALOG)
            draft = self._rules_draft(s, actor)
            if not 1 <= position <= len(draft.rules):
                raise ValidationError("That rule is not in the draft. Reload the page.")
            rule = draft.rules[position - 1]
            draft.rules = [r for i, r in enumerate(draft.rules) if i != position - 1]
            self.w._audit(
                s,
                None,
                actor,
                "pricing_rule_removed",
                f"Pricing rule removed from draft pricing version {draft.version_no} by "
                f"{actor.display_name}: {describe_rule(rule)}. Nothing changes until the "
                "draft is approved.",
                {"pricing_version_id": draft.id, "rule": rule},
            )
            return draft.version_no

        return self.w._run(actor_id, run)


# ------------------------------------------------------------ setup checklist


def setup_checklist(s: Session, tenant_id: str) -> list[dict[str, Any]]:
    """What a new company still needs before it can run a request end to end."""
    approved = (
        s.scalars(
            select(PricingVersion.id).where(
                PricingVersion.tenant_id == tenant_id, PricingVersion.status == "approved"
            )
        ).first()
        is not None
    )
    customers = (
        s.scalars(
            select(CustomerSite.id)
            .join(Customer, Customer.id == CustomerSite.customer_id)
            .where(
                CustomerSite.tenant_id == tenant_id,
                CustomerSite.active.is_(True),
                Customer.active.is_(True),
            )
        ).first()
        is not None
    )
    approvers = s.scalar(
        select(func.count(User.id)).where(
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
            User.role.in_([Role.OWNER.value, Role.APPROVER.value]),
        )
    )
    preparers = s.scalar(
        select(func.count(User.id)).where(
            User.tenant_id == tenant_id,
            User.is_active.is_(True),
            User.role.in_([Role.OWNER.value, Role.OPERATOR.value, Role.APPROVER.value]),
        )
    )
    return [
        {
            "done": approved,
            "text": "Import your price list and approve it",
            "link": "/catalog",
        },
        {
            "done": customers,
            "text": "Add at least one customer with a site",
            "link": "/customers",
        },
        {
            "done": (approvers or 0) >= 1 and (preparers or 0) >= 2,
            "text": "Add a second person, so one prepares quotes and another approves them",
            "link": "/users",
        },
    ]
