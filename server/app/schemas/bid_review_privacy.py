"""Exact human-reviewed text disclosure; no image or price transmission grant."""

from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.bid_review import NonBlank, Revision
from app.schemas.contracts import Contract
from app.schemas.screenshot_contracts import Sha256


class SanitizedPageRef(Contract):
    page_id: UUID
    sanitized_text_sha256: Sha256


class BidSanitizedPage(SanitizedPageRef):
    document_id: UUID
    page: int
    role: Literal["tender", "bid"]
    sanitized_text: str
    price_page: bool
    price_classification: Literal["price", "non_price", "uncertain"]
    notes: list[str]
    redaction_counts: dict[str, int]
    outbound_eligible: bool


class BidRedactionPreview(Contract):
    submission_id: UUID
    expected_revision: Revision
    expected_authorization_id: UUID | None
    expected_submission_manifest_sha256: Sha256
    expected_preparation_input_hash: Sha256
    expected_redaction_manifest_sha256: Sha256
    provider_bindings_sha256: Sha256
    redaction_revision: Revision
    redaction_rule_version: str
    confidential_binding_sha256: Sha256
    derived_name_lists_sha256: Sha256
    human_name_revision: int = Field(strict=True, ge=0)
    pages: list[BidSanitizedPage]
    total: int
    next_cursor: int | None
    blockers: list[str]


class OutboundAuthorizationRequest(Contract):
    request_id: UUID
    expected_revision: Revision
    expected_authorization_id: UUID | None
    expected_submission_manifest_sha256: Sha256
    expected_preparation_input_hash: Sha256
    expected_redaction_manifest_sha256: Sha256
    authorized_sanitized_context_sha256: Sha256
    provider_bindings_sha256: Sha256
    pages: list[SanitizedPageRef] = Field(min_length=1, max_length=1000)
    allow_external: bool = True
    purposes: list[Literal["bid_review_text"]] = Field(
        default_factory=lambda: ["bid_review_text"], min_length=1, max_length=1
    )
    allowed_capabilities: list[Literal["llm"]] = Field(
        default_factory=lambda: ["llm"], min_length=1, max_length=1
    )
    privacy_reviewed: Literal[True]
    reason: NonBlank = Field(repr=False)

    @model_validator(mode="after")
    def unique_pages(self) -> Self:
        if len({item.page_id for item in self.pages}) != len(self.pages):
            raise ValueError("authorized pages must be unique")
        return self


class OutboundAuthorizationView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    submission_id: UUID
    submission_manifest_sha256: Sha256
    preparation_input_hash: Sha256
    redaction_manifest_sha256: Sha256
    authorized_sanitized_context_sha256: Sha256
    provider_bindings_sha256: Sha256
    revision: Revision
    prior_authorization_id: UUID | None
    allow_external: bool
    purposes: list[Literal["bid_review_text"]]
    allowed_capabilities: list[Literal["llm"]]
    pages: list[SanitizedPageRef]
    authorized_by: UUID
    authorized_at: AwareDatetime
    reason_sha256: Sha256
    actor_kind: Literal["session"] = "session"
    immutable: Literal[True] = True
    price_pages_included: Literal[False] = False
    current: bool


class OutboundRevokeRequest(Contract):
    request_id: UUID
    expected_revision: Revision
    expected_authorization_id: UUID
    reason: NonBlank = Field(repr=False)


class OutboundAuthorizationListData(Contract):
    total: int = Field(strict=True, ge=0)
    next_cursor: int | None = Field(default=None, ge=0)


class BidPrivacyListQuery(Contract):
    cursor: int = Field(default=0, strict=True, ge=0)
    limit: int = Field(default=25, strict=True, ge=1, le=100)


class BidNameListRequest(Contract):
    request_id: UUID
    expected_revision: int = Field(strict=True, ge=0)
    bidder_names: list[NonBlank] = Field(default_factory=list, max_length=500, repr=False)
    staff_names: list[NonBlank] = Field(default_factory=list, max_length=500, repr=False)


class BidNameListView(Contract):
    id: UUID
    revision: Revision
    names_sha256: Sha256
    bidder_name_count: int
    staff_name_count: int


def sanitized_context_sha256(pages: list[SanitizedPageRef]) -> str:
    """Stable scope digest shared with CLI and console; lexical page-ID ordering."""
    import hashlib
    import json

    value = [item.model_dump(mode="json") for item in sorted(pages, key=lambda p: str(p.page_id))]
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
