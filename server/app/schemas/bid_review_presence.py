"""Human clearance for exact, presence-only JPEG derivatives."""

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.bid_review import NonBlank
from app.schemas.contracts import Contract
from app.schemas.screenshot_contracts import Sha256


class PresencePrepareRequest(Contract):
    review_id: UUID


class PresenceImage(Contract):
    id: UUID
    page_id: UUID
    document_id: UUID
    page: int
    sha256: Sha256
    source_sha256: Sha256
    width_px: int
    height_px: int
    size_bytes: int
    blur: dict
    privacy_receipt_sha256: Sha256


class PresenceAuthorizationView(Contract):
    id: UUID
    revision: int
    manifest_sha256: Sha256
    image_ids: list[UUID]
    allow_external: bool
    purpose: Literal["bid_review_presence"] = "bid_review_presence"


class PresencePreview(Contract):
    submission_id: UUID
    source_review_id: UUID | None = None
    manifest_sha256: Sha256 | None = None
    expected_revision: int
    expected_authorization_id: UUID | None = None
    images: list[PresenceImage] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    authorization_id: UUID | None = None
    current: bool = False
    revocable_authorization: PresenceAuthorizationView | None = None
    purpose: Literal["bid_review_presence"] = "bid_review_presence"


class PresenceAuthorizationRequest(Contract):
    request_id: UUID
    expected_revision: int = Field(ge=1)
    expected_authorization_id: UUID | None = None
    manifest_sha256: Sha256
    image_ids: list[UUID] = Field(min_length=1, max_length=40)
    privacy_reviewed: Literal[True]
    allow_external: bool = True
    reason: NonBlank = Field(repr=False)

    @model_validator(mode="after")
    def unique_images(self):
        if len(set(self.image_ids)) != len(self.image_ids):
            raise ValueError("images must be unique")
        return self
