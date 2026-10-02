"""Approved immutable unconfirmed source archive and genuine page preview contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.certificate_file_contracts import CertificateScanFile
from app.schemas.contracts import Contract


class EvidenceSourceCreate(Contract):
    task_certificate_id: UUID
    page: int = Field(ge=1, le=200)


class EvidenceSourcePreview(Contract):
    name: str = Field(min_length=1, max_length=200)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0, le=40 * 1024 * 1024)
    media_type: Literal["image/png"] = "image/png"
    width_px: int = Field(gt=0, le=8192)
    height_px: int = Field(gt=0, le=8192)

    @model_validator(mode="after")
    def bounded_pixels(self):
        if self.width_px * self.height_px > 20_000_000:
            raise ValueError("preview pixel limit exceeded")
        return self


class EvidenceSourceArchive(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    task_certificate_id: UUID
    certificate_id: UUID
    certificate_revision_id: UUID
    certificate_file_id: UUID
    source_kind: Literal["user_supplied_certificate_pdf"] = "user_supplied_certificate_pdf"
    original: CertificateScanFile
    page: int = Field(ge=1, le=200)
    render_profile: Literal["pdf-page-preview-v1"] = "pdf-page-preview-v1"
    dpi: Literal[150] = 150
    preview: EvidenceSourcePreview
    rendered_at: datetime
    created_by: UUID
    active_selection: bool
    status: Literal["unconfirmed_source"] = "unconfirmed_source"
    confirmed_by: Literal[None] = None
    eligible_for_draft_export: Literal[False] = False

    @model_validator(mode="after")
    def source_page_exists(self):
        if self.page > self.original.page_count:
            raise ValueError("page is outside the fixed original")
        return self

    @field_validator("rendered_at")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("a timezone-aware render timestamp is required")
        return value


class EvidenceSourceCreateReceipt(Contract):
    source: EvidenceSourceArchive
    duplicate: bool


class EvidenceSourceListData(Contract):
    history: bool
    active_source_ids: list[UUID]


class EvidenceSourcePreviewLink(Contract):
    url: str
    expires_in: Literal[300] = 300


class EvidenceSourcePreviewReceipt(Contract):
    evidence_source_id: UUID
    output_path: str
    file: EvidenceSourcePreview
