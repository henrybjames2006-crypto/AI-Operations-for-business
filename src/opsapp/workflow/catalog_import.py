"""Read a service catalog and price list from a CSV file (as saved by Excel).

Parsing is strict and all-or-nothing: every problem is reported with its row, and a file
with any problem saves nothing. A good file becomes a *draft* pricing version, which an
owner approves on the Catalog page before any quote uses it.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

COLUMNS = (
    "sku",
    "name",
    "unit",
    "unit_price",
    "min_qty",
    "max_qty",
    "quantity_step",
    "onsite",
    "keywords",
    "description",
)
OPTIONAL = {"description"}
MAX_BYTES = 200_000
MAX_ROWS = 200
MAX_PRICE = Decimal("1000000")
MAX_QTY = Decimal("10000")

_SKU = re.compile(r"[A-Z0-9][A-Z0-9-]{1,39}")
# Cells starting with these characters can run as formulas when the file is reopened in a
# spreadsheet, so they are refused rather than stored.
_FORMULA_START = ("=", "+", "@", "\t", "\r")
_YES = {"yes", "y", "true", "1"}
_NO = {"no", "n", "false", "0"}


@dataclass(frozen=True)
class ImportRow:
    sku: str
    name: str
    unit: str
    unit_price: Decimal
    min_qty: Decimal
    max_qty: Decimal
    quantity_step: Decimal
    onsite: bool
    keywords: tuple[str, ...]
    description: str

    def catalog_fields(self) -> dict[str, object]:
        """The catalog details applied to the service when the version is approved."""
        return {
            "sku": self.sku,
            "name": self.name,
            "unit": self.unit,
            "description": self.description,
            "keywords": list(self.keywords),
            "quantity_step": str(self.quantity_step),
            "onsite": self.onsite,
        }


@dataclass(frozen=True)
class ParseResult:
    rows: list[ImportRow]
    errors: list[str]


def _decode(data: bytes) -> str:
    # Excel saves "CSV UTF-8" with a byte-order mark and plain "CSV" in the Windows code page.
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _header(name: str) -> str:
    return re.sub(r"[\s-]+", "_", name.strip().lower())


def _number(raw: str, label: str, errors: list[str], where: str, *, money: bool = False) -> Decimal:
    text = raw.strip()
    if money:
        text = text.replace("$", "").replace(",", "")
    try:
        value = Decimal(text)
        if not value.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        errors.append(f"{where}: {label} {raw.strip()!r} is not a number.")
        return Decimal(0)
    if value <= 0:
        errors.append(f"{where}: {label} must be more than zero.")
    if money and value.as_tuple().exponent < -2:  # type: ignore[operator]
        errors.append(f"{where}: {label} has more than two decimal places.")
    limit = MAX_PRICE if money else MAX_QTY
    if value > limit:
        errors.append(f"{where}: {label} is above the limit of {limit}.")
    return value


def parse_catalog_csv(data: bytes) -> ParseResult:
    if len(data) > MAX_BYTES:
        return ParseResult([], [f"The file is larger than {MAX_BYTES // 1000} KB."])
    text = _decode(data)
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return ParseResult([], ["The file is empty."])
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
        return ParseResult([], errors)

    rows: list[ImportRow] = []
    seen: set[str] = set()
    for line_no, cells in enumerate(reader, start=2):
        if not any(c.strip() for c in cells):
            continue
        where = f"Line {line_no}"
        if len(rows) + 1 > MAX_ROWS:
            errors.append(f"More than {MAX_ROWS} services; split the file.")
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
        row_errors: list[str] = []
        sku = row["sku"].strip().upper()
        if not _SKU.fullmatch(sku):
            row_errors.append(
                f"{where}: SKU {row['sku'].strip()!r} must be 2 to 40 letters, digits or dashes."
            )
        elif sku in seen:
            row_errors.append(f"{where}: SKU {sku} appears more than once.")
        seen.add(sku)
        name = row["name"].strip()
        unit = row["unit"].strip()
        description = row.get("description", "").strip()
        if not name or len(name) > 200:
            row_errors.append(f"{where}: name is required (up to 200 characters).")
        if not unit or len(unit) > 40:
            row_errors.append(f"{where}: unit is required (up to 40 characters).")
        if len(description) > 1000:
            row_errors.append(f"{where}: description is longer than 1000 characters.")
        price = _number(row["unit_price"], "unit price", row_errors, where, money=True)
        min_qty = _number(row["min_qty"], "minimum quantity", row_errors, where)
        max_qty = _number(row["max_qty"], "maximum quantity", row_errors, where)
        step = _number(row["quantity_step"], "quantity step", row_errors, where)
        if min_qty > 0 and max_qty > 0 and max_qty < min_qty:
            row_errors.append(f"{where}: maximum quantity is below the minimum.")
        onsite_text = row["onsite"].strip().lower()
        if onsite_text not in _YES | _NO:
            row_errors.append(f"{where}: onsite must be yes or no.")
        keywords = tuple(k.strip().lower() for k in row["keywords"].split(";") if k.strip())
        if not keywords:
            row_errors.append(
                f"{where}: at least one keyword is needed (separate several with ';')."
            )
        elif len(keywords) > 20 or any(len(k) > 60 for k in keywords):
            row_errors.append(f"{where}: up to 20 keywords of up to 60 characters each.")
        if row_errors:
            errors.extend(row_errors)
            continue
        rows.append(
            ImportRow(
                sku=sku,
                name=name,
                unit=unit,
                unit_price=price.quantize(Decimal("0.01")),
                min_qty=min_qty,
                max_qty=max_qty,
                quantity_step=step,
                onsite=onsite_text in _YES,
                keywords=keywords,
                description=description,
            )
        )
    if not rows and not errors:
        errors.append("The file has a header but no services.")
    return ParseResult(rows if not errors else [], errors)


def to_csv(rows: list[dict[str, object]]) -> str:
    """Write services in the import format (used for the downloadable template)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    for r in rows:
        writer.writerow([r.get(c, "") for c in COLUMNS])
    return out.getvalue()
