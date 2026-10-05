"""Deterministic mock extractor: keyword and pattern rules, no network, no randomness.

This is the baseline that a real AI adapter must beat. It is intentionally simple and
documents its own blind spots (see docs/evaluation.md).
"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Literal

from .ports import ExtractionRun
from .schema import (
    ExtractionContext,
    ExtractionOutput,
    ItemMention,
    UnsupportedMention,
)

NUMBER_WORDS = {
    "zero": 0,
    "one": 1,
    "a single": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "a dozen": 12,
    "dozen": 12,
}
VAGUE_WORDS = (
    "a couple",
    "couple",
    "a few",
    "few",
    "several",
    "some",
    "many",
    "all of them",
    "all",
    "a bunch",
    "multiple",
)

WORK_VERBS = re.compile(
    r"\b(install|installed|installing|set up|setup|repair|repaired|fix|fixed|replace|"
    r"configure|upgrade|build|deploy|mount|wire|troubleshoot|recover|train|audit|design|"
    r"develop|paint|clean|service|maintain|need|want|require)\b"
)

SUSPICIOUS = [
    re.compile(p)
    for p in (
        r"ignore (all |any )?(previous|prior|above|the) (instructions|rules|policy|policies)",
        r"\b(apply|give|add|include)\b[^.]*\bdiscount\b",
        r"\bapprove (this|it|the quote)?\s*(automatically|now|immediately)\b",
        r"\bauto-?approve",
        r"\bsystem prompt\b",
        r"\bto the (assistant|ai|bot|system)\b",
        r"\byou are now\b",
        r"\b(set|change|make) (the )?price",
        r"\bfree of charge\b",
        r"\bwaive\b",
        r"\bskip (the )?approval\b",
        r"\bdeveloper mode\b",
    )
]

MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]

# Split on punctuation, but not inside numbers such as "2.5" or "1,200".
_CLAUSE_SPLIT = re.compile(r"[!?\n;]|\.(?!\d)|,(?!\d{3}\b)|\band\b|\balso\b|\bplus\b")
_SENTENCE_SPLIT = re.compile(r"(?<=[!?\n])|(?<=\.)(?!\d)")
_NUM_TOKEN = re.compile(r"^-?\d+(\.\d+)?$")


def _tokens(text: str) -> list[str]:
    return re.findall(r"-?\d+(?:\.\d+)?|[a-z][a-z'\-]*", text)


def _find_number(clause: str, keyword_pos: int) -> tuple[str | None, str | None]:
    """Nearest quantity before the keyword (within 5 words). Returns (decimal, raw text)."""
    before = clause[:keyword_pos]
    for phrase in sorted(NUMBER_WORDS, key=len, reverse=True):
        if " " in phrase and re.search(rf"\b{phrase}\b\W*(\w+\W+){{0,4}}$", before):
            return str(NUMBER_WORDS[phrase]), phrase
    toks = _tokens(before)[-5:]
    for tok in reversed(toks):
        if _NUM_TOKEN.match(tok):
            return tok, tok
        if tok in NUMBER_WORDS:
            return str(NUMBER_WORDS[tok]), tok
    for vague in VAGUE_WORDS:
        if re.search(rf"\b{vague}\b", clause):
            return None, vague
    after = clause[keyword_pos:]
    m = re.search(r"\b(?:x|qty|quantity|for)\s*:?\s*(-?\d+(?:\.\d+)?)\b", after)
    if m:
        return m.group(1), m.group(0)
    return None, None


def _timeframe(text: str, today: date) -> tuple[str | None, str | None]:
    checks = [
        (r"\bnext week\b", "next_week"),
        (r"\bthis week\b", "this_week"),
        (r"\btomorrow\b", "tomorrow"),
        (r"\b(asap|as soon as possible|urgent(ly)?)\b", "asap"),
    ]
    for pattern, code in checks:
        m = re.search(pattern, text)
        if m:
            return code, m.group(0)
    m = re.search(r"\b(20\d\d)-(\d\d)-(\d\d)\b", text)
    if m:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return f"date:{d.isoformat()}", m.group(0)
        except ValueError:
            return None, m.group(0)
    m = re.search(rf"\b({'|'.join(MONTHS)})\s+(\d{{1,2}})(st|nd|rd|th)?\b", text)
    if m:
        month = MONTHS.index(m.group(1)) + 1
        day = int(m.group(2))
        try:
            d = date(today.year, month, day)
            if d < today:
                d = date(today.year + 1, month, day)
            return f"date:{d.isoformat()}", m.group(0)
        except ValueError:
            return None, m.group(0)
    return None, None


class MockExtractor:
    provider = "mock"
    model_id = "deterministic-rules-v1"

    def extract(self, text: str, sender: str, context: ExtractionContext) -> ExtractionRun:
        started = time.perf_counter()
        output = self._extract(text, context)
        latency = int((time.perf_counter() - started) * 1000)
        body = output.model_dump_json()
        return ExtractionRun(
            output=output,
            provider=self.provider,
            model_id=self.model_id,
            latency_ms=latency,
            input_chars=len(text),
            output_chars=len(body),
        )

    def _extract(self, text: str, ctx: ExtractionContext) -> ExtractionOutput:
        lower = text.lower()
        # Drop e-mail header lines from item matching (they are metadata, not requests).
        body_lines = [
            ln
            for ln in lower.splitlines()
            if not re.match(r"^\s*(from|to|subject|date|cc)\s*:", ln)
        ]
        body = "\n".join(body_lines)

        suspicious: list[str] = []
        clean_sentences: list[str] = []
        for sentence in _SENTENCE_SPLIT.split(body):
            if any(p.search(sentence) for p in SUSPICIOUS):
                suspicious.append(sentence.strip()[:500])
            else:
                clean_sentences.append(sentence)
        clean = " ".join(clean_sentences)

        customer_mentions: list[str] = []
        for c in ctx.customers:
            for name in [c.name, *c.aliases]:
                if name and re.search(rf"\b{re.escape(name.lower())}\b", lower):
                    customer_mentions.append(name)
        site_mentions = [
            s for s in ctx.site_labels if re.search(rf"\b{re.escape(s.lower())}\b", lower)
        ]

        keyword_index: list[tuple[str, str]] = sorted(
            ((kw.lower(), item.sku) for item in ctx.catalog for kw in item.keywords),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )

        items: dict[str, ItemMention] = {}
        notes: list[str] = []
        unsupported: list[UnsupportedMention] = []
        for clause in _CLAUSE_SPLIT.split(clean):
            clause = clause.strip()
            if len(clause) < 3:
                continue
            match: tuple[str, str, int] | None = None
            for kw, sku in keyword_index:
                m = re.search(rf"\b{re.escape(kw)}\b", clause)
                if m:
                    match = (kw, sku, m.start())
                    break
            if match is None:
                if WORK_VERBS.search(clause) and not re.search(
                    r"\b(thanks|thank you|hi|hello)\b", clause
                ):
                    # Only report clauses with an object after the verb.
                    words = _tokens(clause)
                    if len(words) >= 3 and not _only_scheduling(clause):
                        unsupported.append(UnsupportedMention(text=clause[:500]))
                continue
            _kw, sku, pos = match
            qty, qty_text = _find_number(clause, pos)
            if sku in items:
                prev = items[sku]
                if prev.quantity is None:
                    items[sku] = ItemMention(
                        sku=sku, quantity=qty, quantity_text=qty_text, evidence=clause[:500]
                    )
                elif qty is not None and qty != prev.quantity:
                    notes.append(f"Conflicting quantities for {sku}: {prev.quantity} and {qty}.")
                    items[sku] = ItemMention(
                        sku=sku,
                        quantity=None,
                        quantity_text=f"{prev.quantity} or {qty}",
                        evidence=clause[:500],
                    )
                continue
            items[sku] = ItemMention(
                sku=sku, quantity=qty, quantity_text=qty_text, evidence=clause[:500]
            )

        timeframe, timeframe_text = _timeframe(clean, ctx.today)
        request_type: Literal["service_request", "unclear"] = (
            "service_request" if items or unsupported else "unclear"
        )
        return ExtractionOutput(
            request_type=request_type,
            customer_mentions=_dedupe(customer_mentions),
            site_mentions=_dedupe(site_mentions),
            items=list(items.values()),
            unsupported=unsupported[:25],
            timeframe=timeframe,
            timeframe_text=timeframe_text,
            suspicious_instructions=suspicious[:25],
            notes=notes,
        )


def _only_scheduling(clause: str) -> bool:
    return bool(
        re.search(
            r"\b(week|tomorrow|monday|tuesday|wednesday|thursday|friday|asap|"
            r"great|soon|time|date|schedule|quote|price|cost)\b",
            clause,
        )
    ) and not re.search(
        r"\b(install|repair|fix|replace|configure|upgrade|build|deploy|mount|wire)\b", clause
    )


def _dedupe(values: list[str]) -> list[str]:
    seen: list[str] = []
    for v in values:
        if v not in seen:
            seen.append(v)
    return seen
