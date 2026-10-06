"""Labelled real samples (redacted first) as evaluation cases.

A firm's real requests name its own customers and services, which the demo records don't
have. So samples are scored on reading only: which demo catalog services were asked for
and how many, the timeframe, instruction-like text and unsupported work. The labeller maps
each ask to the closest demo service (see the Catalog page) or counts it as unsupported.
The customer is not scored.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from ..discovery.redact import LABEL_COLUMNS
from .cases import Case

CATEGORY = "sample"
_TIMEFRAME = re.compile(r"tomorrow|this_week|next_week|asap|date:\d{4}-\d\d-\d\d")
_ITEM = re.compile(r"([A-Z0-9][A-Z0-9-]+)\s*=\s*(\?|\d+(?:\.\d+)?)")


def load_samples(folder: Path) -> tuple[list[Case], list[str]]:
    """Read ``labels.csv`` and the redacted files it names. Returns cases and problems."""
    labels = folder / "labels.csv"
    if not labels.is_file():
        return [], [f"No labels.csv in {folder}. Run `python -m opsapp redact` first."]
    cases: list[Case] = []
    problems: list[str] = []
    with labels.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if [c.strip().lower() for c in reader.fieldnames or []] != list(LABEL_COLUMNS):
            return [], ["labels.csv must have the columns: " + ", ".join(LABEL_COLUMNS)]
        for n, row in enumerate(reader, start=2):
            where = f"labels.csv line {n}"
            name = (row["file"] or "").strip()
            path = folder / name
            if not name or "/" in name or "\\" in name or not path.is_file():
                problems.append(f"{where}: file {name!r} not found in {folder}.")
                continue
            items: dict[str, str | None] = {}
            raw_items = (row["items"] or "").strip()
            for part in filter(None, (p.strip() for p in raw_items.split(";"))):
                m = _ITEM.fullmatch(part.upper())
                if not m:
                    problems.append(
                        f"{where}: item {part!r} should look like SKU=5, or SKU=? when no "
                        "number was given."
                    )
                    continue
                items[m.group(1)] = None if m.group(2) == "?" else m.group(2)
            timeframe = (row["timeframe"] or "").strip() or None
            if timeframe and not _TIMEFRAME.fullmatch(timeframe):
                problems.append(
                    f"{where}: timeframe must be blank, tomorrow, this_week, next_week, asap "
                    "or date:YYYY-MM-DD."
                )
            flag = (row["instruction"] or "no").strip().lower()
            if flag not in ("yes", "no"):
                problems.append(f"{where}: instruction must be yes or no.")
            unsupported = (row["unsupported"] or "0").strip()
            if not unsupported.isdigit():
                problems.append(f"{where}: unsupported must be a whole number.")
                unsupported = "0"
            cases.append(
                Case(
                    f"sample-{path.stem}",
                    CATEGORY,
                    path.read_text(encoding="utf-8"),
                    (row["sender"] or "").strip(),
                    customer=None,
                    items=items,
                    timeframe=timeframe,
                    flagged=flag == "yes",
                    unsupported=int(unsupported),
                )
            )
    if not cases and not problems:
        problems.append("labels.csv has no rows.")
    return (cases if not problems else []), problems
