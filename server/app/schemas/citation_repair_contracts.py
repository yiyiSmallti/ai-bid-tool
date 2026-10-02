"""Explicit, preview-bound repair of stored tender citations."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.contracts import Contract, Source


class CitationRepairRequest(Contract):
    extraction_job_id: UUID
    expected_preview: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=10000)


class CitationRepairItem(Contract):
    requirement_id: UUID
    source: Source
    model_quote: str | None
    proposed_quote: str | None
    status: Literal["repairable", "unchanged", "unlocatable"]
    reason: Literal["ambiguous_quote", "quote_not_at_position"] | None
    quote_changed: bool
