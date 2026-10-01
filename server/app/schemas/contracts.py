from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTRACT_VERSION = "1.0"


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


class Category(StrEnum):
    qualification = "qualification"
    technical = "technical"
    scoring = "scoring"
    substantive = "substantive"


class Source(Contract):
    document_id: UUID
    chunk_id: UUID
    page: int = Field(ge=1)
    quote: str = Field(min_length=1)


class ExtractedRequirement(Contract):
    category: Category
    starred: bool = False
    text: str = Field(min_length=1)
    source: Source
    condition: dict[str, Any] = Field(default_factory=dict)


class Extraction(Contract):
    items: list[ExtractedRequirement]


class ProviderUsage(Contract):
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
    charge_usd: float | None = Field(default=None, ge=0)


class PageText(Contract):
    page: int = Field(ge=1)
    text: str
    ocr: bool = False
    # Word pagination depends on layout. Unverified page numbers never become citations.
    citation_verified: bool = True


class OCRText(Contract):
    text: str
    boxes: list[dict[str, Any]] = Field(default_factory=list)
    usage: ProviderUsage


class LLMResult(Contract):
    extraction: Extraction
    usage: ProviderUsage
