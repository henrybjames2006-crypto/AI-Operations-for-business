"""Schema for extraction output. Every adapter (mock or real) must produce this shape.

The extractor only reports what the text says. Matching mentions to customer records,
prices and permissions happens afterwards in deterministic application code.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1"
MAX_LIST = 25
MAX_TEXT = 500


class ItemMention(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(max_length=40)
    quantity: str | None = Field(default=None, max_length=20, description="Decimal string")
    quantity_text: str | None = Field(default=None, max_length=60)
    evidence: str = Field(max_length=MAX_TEXT)


class UnsupportedMention(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=MAX_TEXT)


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1"] = "1"
    request_type: Literal["service_request", "unclear"]
    customer_mentions: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    site_mentions: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    items: list[ItemMention] = Field(default_factory=list, max_length=MAX_LIST)
    unsupported: list[UnsupportedMention] = Field(default_factory=list, max_length=MAX_LIST)
    timeframe: str | None = Field(default=None, max_length=20)
    timeframe_text: str | None = Field(default=None, max_length=MAX_TEXT)
    suspicious_instructions: list[str] = Field(default_factory=list, max_length=MAX_LIST)
    notes: list[str] = Field(default_factory=list, max_length=MAX_LIST)


class CatalogHint(BaseModel):
    sku: str
    name: str
    keywords: list[str]


class CustomerHint(BaseModel):
    name: str
    aliases: list[str]


class ExtractionContext(BaseModel):
    """What an extractor is allowed to see besides the request text.

    Names and catalog descriptions only: no prices, no user identities, no other tenants.
    """

    today: date
    customers: list[CustomerHint]
    site_labels: list[str]
    catalog: list[CatalogHint]
