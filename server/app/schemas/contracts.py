from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "2.1"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class Cost(Contract):
    llm_tokens: int = 0
    ocr_pages: int = 0
    usd: float | None = 0.0


class Result(Contract):
    ok: bool
    command: str
    data: dict[str, Any] = Field(default_factory=dict)
    items: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    cost: Cost = Field(default_factory=Cost)
    duration_ms: int = 0


class Login(Contract):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)
    org_id: UUID


class TaskCreate(Contract):
    name: str = Field(min_length=1, max_length=200)
    tender_number: str | None = Field(default=None, max_length=100)
    deadline: datetime | None = None
    budget_usd: float | None = Field(default=None, ge=0)

    @field_validator("deadline")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("deadline must include a timezone")
        return value

    @field_validator("name")
    @classmethod
    def meaningful_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must contain non-whitespace characters")
        return value.strip()


class TokenCreate(Contract):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(min_length=1)
    expires_at: datetime


class JobAction(Contract):
    dry_run: bool = False
    retry: bool = False
    # Extraction only: one of the model's official reasoning levels; omitted means its default.
    reasoning: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,20}$")


class Category(StrEnum):
    qualification = "qualification"
    technical = "technical"
    scoring = "scoring"
    substantive = "substantive"


class Location(Contract):
    """Where a quote sits in a Word document: a paragraph or a table cell."""

    block_id: str = Field(min_length=2, max_length=200)
    kind: Literal["paragraph", "cell"]
    section_path: list[str] = Field(default_factory=list)
    paragraph: int | None = Field(default=None, ge=1)
    table: int | None = Field(default=None, ge=1)
    row: int | None = Field(default=None, ge=1)
    column: int | None = Field(default=None, ge=1)
    label: str = Field(min_length=1)


class Source(Contract):
    document_id: UUID
    chunk_id: UUID
    # PDF sources cite a page; Word sources cite a structural location. Never both.
    page: int | None = Field(default=None, ge=1)
    location: Location | None = None
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def one_position(self):
        if (self.page is None) == (self.location is None):
            raise ValueError("a source needs exactly one of page or location")
        return self


class ExtractedRequirement(Contract):
    category: Category
    starred: bool = False
    text: str = Field(min_length=1)
    source: Source
    model_quote: str | None = None
    condition: dict[str, Any] = Field(default_factory=dict)


class Extraction(Contract):
    items: list[ExtractedRequirement]


class ProviderUsage(Contract):
    provider_config_id: UUID | None = None
    image_count: int = Field(default=0, ge=0, le=20)
    image_price_revision: str | None = None
    image_input_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    provider: str
    model: str
    version: str
    duration_ms: int = Field(ge=0)
    tokens: int = Field(default=0, ge=0)
    ocr_pages: int = Field(default=0, ge=0)
    usd: float | None = Field(default=None, ge=0)
    test_only: bool = False
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    # Set only for calls billed to the org through a platform catalog model.
    platform_model_id: str | None = None
    charge: float | None = Field(default=None, ge=0)


class PageText(Contract):
    page: int = Field(ge=1)
    text: str
    ocr: bool = False
    citation_verified: bool = True


class Block(Location):
    """One Word paragraph or table cell, addressed by Location fields."""

    text: str = Field(min_length=1)


class SectionText(Contract):
    """A run of Word blocks parsed into one chunk; cited by block, never by page."""

    seq: int = Field(ge=1)
    text: str
    blocks: list[Block] = Field(min_length=1)


class OCRText(Contract):
    text: str
    boxes: list[dict[str, Any]] = Field(default_factory=list)
    usage: ProviderUsage


class GapFill(Contract):
    """Internal provenance; the processor reports only newly saved requirements as added."""

    segments: int = Field(default=0, ge=0)
    calls: int = Field(default=0, ge=0)
    fingerprints: set[str] = Field(default_factory=set)


class LLMResult(Contract):
    extraction: Extraction
    usage: ProviderUsage
    # HTTP adapters retain each call as well as the aggregate used by existing consumers.
    # Providers making a single call can keep returning only usage.
    usages: list[ProviderUsage] | None = None
    gap_fill: GapFill = Field(default_factory=GapFill)
    # Items dropped before citation checks, such as ones with an empty quote.
    rejected: list[dict[str, str]] = Field(default_factory=list)
