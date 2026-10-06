"""Remove personal details from real sample requests before anyone else sees them.

Runs on files on the user's own computer, uses fixed patterns (no AI, no network) and never
changes the originals. Each kind of detail becomes a numbered placeholder; the same value
gets the same placeholder in every file, so "who wrote twice" stays visible.

Patterns miss things (names not in the supplied list, unusual phone formats, a customer's
own wording that identifies them), so a person must read every redacted file before it is
shared. The report lists what was replaced, but never the original values, so the output
folder can be shared as a whole once checked.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..redaction import _PATTERNS as SECRET_PATTERNS

TEXT_SUFFIXES = {".txt", ".eml", ".md"}
MAX_FILE_BYTES = 1_000_000

_STREET = (
    r"street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|way|court|ct|place|pl|"
    r"parkway|pkwy|highway|hwy|terrace|close|crescent"
)
# Order matters: earlier patterns win where matches overlap.
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")),
    ("LINK", re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>()\"']*[^\s<>()\"'.,;:!?]")),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    (
        "PHONE",
        re.compile(
            r"(?<![\w-])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)\s?|\d{2,4}[\s.-])\d{3,4}[\s.-]\d{3,4}"
            r"(?:\s?(?:x|ext\.?)\s?\d{1,5})?(?![\w-])"
        ),
    ),
    (
        "ADDRESS",
        re.compile(
            # Street words must start with a capital ("4410 Old Mill Road"), so phrases
            # like "4 laptops next week for Dr. Okafor" are left alone.
            rf"\b\d{{1,6}}(?:\s+[A-Z][\w'-]*){{1,4}}\s+(?i:{_STREET})\b\.?"
            r"(?:,?\s+(?i:suite|ste|unit|apt|floor|fl)\.?\s*#?\w+)?"
        ),
    ),
    ("POSTCODE", re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b")),
]


@dataclass
class Replacement:
    file: str
    line: int
    kind: str
    placeholder: str


@dataclass
class Redactor:
    names: list[str] = field(default_factory=list)
    _seen: dict[tuple[str, str], str] = field(default_factory=dict)
    _counts: dict[str, int] = field(default_factory=dict)

    def _placeholder(self, kind: str, value: str) -> str:
        key = (kind, value.strip().lower())
        if key not in self._seen:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            self._seen[key] = f"[{kind}-{self._counts[kind]}]"
        return self._seen[key]

    def _patterns(self) -> list[tuple[str, re.Pattern[str]]]:
        names = sorted({n.strip() for n in self.names if n.strip()}, key=len, reverse=True)
        out = [("SECRET", p) for p in SECRET_PATTERNS] + list(PATTERNS)
        if names:
            alternation = "|".join(re.escape(n) for n in names)
            out.append(("NAME", re.compile(rf"(?i)(?<!\w)(?:{alternation})(?!\w)")))
        return out

    def redact(self, text: str, file: str = "") -> tuple[str, list[Replacement]]:
        found: list[Replacement] = []
        for kind, pattern in self._patterns():
            parts: list[str] = []
            last = 0
            for m in pattern.finditer(text):
                ph = "[SECRET]" if kind == "SECRET" else self._placeholder(kind, m.group(0))
                # Placeholders never contain line breaks, so line numbers stay correct.
                found.append(Replacement(file, text.count("\n", 0, m.start()) + 1, kind, ph))
                parts += [text[last : m.start()], ph]
                last = m.end()
            text = "".join([*parts, text[last:]])
        found.sort(key=lambda r: (r.line, r.kind))
        return text, found


def read_names(path: Path | None) -> list[str]:
    if path is None:
        return []
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def redact_folder(source: Path, out: Path, names: list[str]) -> tuple[int, list[Replacement]]:
    """Redact every text file in ``source`` into ``out``. Returns files written, replacements."""
    source = source.resolve()
    out = out.resolve()
    if not source.is_dir():
        raise ValueError(f"{source} is not a folder.")
    if out == source or source in out.parents:
        raise ValueError("Choose an output folder outside the folder of originals.")
    files = sorted(p for p in source.iterdir() if p.suffix.lower() in TEXT_SUFFIXES)
    if not files:
        raise ValueError(f"No .txt, .eml or .md files found in {source}.")
    out.mkdir(parents=True, exist_ok=True)
    redactor = Redactor(names)
    report: list[Replacement] = []
    for i, path in enumerate(files, start=1):
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"{path.name} is larger than 1 MB; split it first.")
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
        # Output files are renamed too: original file names often contain names or dates.
        new_name = f"sample-{i:03d}.txt"
        redacted, found = redactor.redact(text, new_name)
        (out / new_name).write_text(redacted, encoding="utf-8")
        report.extend(found)
    with (out / "redaction-report.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "line", "kind", "placeholder"])
        for r in report:
            w.writerow([r.file, r.line, r.kind, r.placeholder])
    labels = out / "labels.csv"
    if not labels.exists():
        write_label_template(labels, [f"sample-{i:03d}.txt" for i in range(1, len(files) + 1)])
    return len(files), report


LABEL_COLUMNS = ("file", "sender", "items", "timeframe", "instruction", "unsupported", "notes")


def write_label_template(path: Path, files: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(LABEL_COLUMNS)
        for f in files:
            w.writerow([f, "", "", "", "no", "0", ""])
