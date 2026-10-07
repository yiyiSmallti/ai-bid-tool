"""Runtime contracts for bounded org resource and export binding management."""

from datetime import date
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from app.schemas.certificate_contracts import CertificateRevision
from app.schemas.certificate_file_contracts import CertificateFileRevision
from app.schemas.contracts import Contract
from app.schemas.feature_contracts import FeatureRevision
from app.schemas.profile_contracts import OrgProfileRevision
from app.schemas.resource_contracts import ProductRevision
from app.schemas.template_contracts import TemplateRevision

PAGE_BYTE_LIMIT = 256 * 1024
DETAIL_BYTE_LIMIT = 1024 * 1024

type LifecycleState = Literal["active", "inactive"]
type Revision = Annotated[int, Field(strict=True, ge=1)]
type LifecycleRevision = Annotated[int, Field(strict=True, ge=0)]
type Cursor = Annotated[str, Field(min_length=1, max_length=2048)]
type SearchText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
type ReasonCode = Literal["obsolete", "duplicate", "unavailable", "restored", "other"]
type ActionCode = Literal[
    "create",
    "revise",
    "deactivate",
    "restore",
    "select",
    "upload",
    "download",
    "bind",
    "configure",
    "test",
    "propose",
    "approve",
    "reject",
    "disable",
    "set_value",
    "reveal",
]
type BlockerCode = Literal[
    "role_required",
    "human_required",
    "task_access_required",
    "task_archived",
    "resource_inactive",
    "not_candidate",
    "missing_file",
    "binding_required",
    "unsupported_capability",
    "provider_unavailable",
]


class ActionHint(Contract):
    action: ActionCode
    allowed: bool
    reason: BlockerCode | None = None

    @model_validator(mode="after")
    def denial_reason(self) -> Self:
        if self.allowed == (self.reason is not None):
            raise ValueError("only denied actions require a reason")
        return self


class PageQuery(Contract):
    cursor: Cursor | None = None
    limit: int = Field(default=25, strict=True, ge=1, le=100)


class PageData(Contract):
    """Live keyset page, not a snapshot or an authorization grant; no global counts."""

    org_id: UUID
    as_of: AwareDatetime
    returned: int = Field(strict=True, ge=0, le=100)
    next_cursor: Cursor | None = None
    has_more: bool

    @model_validator(mode="after")
    def continuation(self) -> Self:
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("has_more and next_cursor must agree")
        return self


class Page[T](Contract):
    """Internal aggregate. Serialize metadata to Result.data and rows to Result.items."""

    data: PageData
    items: list[T] = Field(max_length=100)

    @model_validator(mode="after")
    def bounded_page(self) -> Self:
        if self.data.returned != len(self.items):
            raise ValueError("returned must equal the page row count")
        if len(self.model_dump_json().encode("utf-8")) > PAGE_BYTE_LIMIT:
            raise ValueError("page payload exceeds its byte budget")
        if any(
            getattr(item, "org_id", self.data.org_id) != self.data.org_id for item in self.items
        ):
            raise ValueError("page rows must belong to the authenticated org")
        return self


class ResourceQuery(PageQuery):
    q: SearchText | None = None
    state: LifecycleState | Literal["all"] = "active"
    # Only features accepts product_id / implementation_status.
    product_id: UUID | None = None
    implementation_status: Literal["implemented", "developing", "planned"] | None = None


class ResourceRef(Contract):
    kind: Literal["products"]
    resource_id: UUID


class LifecycleView(Contract):
    state: LifecycleState
    revision: LifecycleRevision

    @model_validator(mode="after")
    def initial_state(self) -> Self:
        if self.revision == 0 and self.state != "active":
            raise ValueError("an initial resource is active")
        return self


class ResourceRow(Contract):
    org_id: UUID
    ref: ResourceRef
    name: str = Field(min_length=1, max_length=200)
    revision_id: UUID
    revision: Revision
    lifecycle: LifecycleView
    # A root-level simulated-resource marker applies to all its revisions.
    provenance: Literal["declared", "simulated"]
    created_at: AwareDatetime
    revised_at: AwareDatetime
    revised_by: UUID | None
    actions: list[ActionHint] = Field(max_length=16)


class ResourceDetailQuery(Contract):
    revision: Revision | None = None


class ResourceHistoryRow(Contract):
    org_id: UUID
    ref: ResourceRef
    revision_id: UUID
    revision: Revision
    name: str = Field(min_length=1, max_length=200)
    created_at: AwareDatetime
    created_by: UUID | None
    current: bool
    has_file: bool


class ProductDetail(Contract):
    kind: Literal["products"] = "products"
    revision: ProductRevision


class FeatureDetail(Contract):
    kind: Literal["features"] = "features"
    revision: FeatureRevision


class ResourceDetailData(Contract):
    org_id: UUID
    ref: ResourceRef
    current_revision: Revision
    lifecycle: LifecycleView
    provenance: Literal["declared", "simulated"]
    detail: ProductDetail
    revised_at: AwareDatetime
    revised_by: UUID | None
    actions: list[ActionHint] = Field(max_length=16)

    @model_validator(mode="after")
    def identity_binding(self) -> Self:
        revision = self.detail.revision
        resource_id = revision.product_id
        if (
            self.ref.kind != self.detail.kind
            or self.ref.resource_id != resource_id
            or self.org_id != revision.org_id
            or revision.revision > self.current_revision
        ):
            raise ValueError("resource, org and revision must match")
        if len(self.model_dump_json().encode("utf-8")) > DETAIL_BYTE_LIMIT:
            raise ValueError("detail exceeds its byte budget")
        return self


class ResourceLifecycleSet(Contract):
    expected_revision: Revision
    expected_lifecycle_revision: LifecycleRevision
    state: LifecycleState
    reason_code: ReasonCode

    @model_validator(mode="after")
    def transition_reason(self) -> Self:
        if (self.state == "active") != (self.reason_code == "restored"):
            raise ValueError("restored is the required reason for activation only")
        return self


class ResourceLifecycleEvent(Contract):
    id: UUID
    org_id: UUID
    ref: ResourceRef
    revision: Revision
    resource_revision: Revision
    before: LifecycleState
    after: LifecycleState
    reason_code: ReasonCode
    actor_user_id: UUID
    actor_kind: Literal["session"] = "session"
    created_at: AwareDatetime

    @model_validator(mode="after")
    def actual_transition(self) -> Self:
        if self.before == self.after:
            raise ValueError("a lifecycle event records a change")
        if (self.after == "active") != (self.reason_code == "restored"):
            raise ValueError("restored is the required reason for activation only")
        return self


class ResourceLifecycleData(Contract):
    event: ResourceLifecycleEvent
    lifecycle: LifecycleView
    existing_selections: Literal["preserved"] = "preserved"

    @model_validator(mode="after")
    def current_event(self) -> Self:
        if (self.event.revision, self.event.after) != (
            self.lifecycle.revision,
            self.lifecycle.state,
        ):
            raise ValueError("event must describe the returned lifecycle")
        return self


class FeatureRef(Contract):
    kind: Literal["features"]
    resource_id: UUID


class FeatureRow(Contract):
    org_id: UUID
    ref: FeatureRef
    name: str = Field(min_length=1, max_length=200)
    revision_id: UUID
    revision: Revision
    lifecycle: LifecycleView
    # A root-level simulated-resource marker applies to all its revisions.
    provenance: Literal["declared", "simulated"]
    created_at: AwareDatetime
    revised_at: AwareDatetime
    revised_by: UUID | None
    actions: list[ActionHint] = Field(max_length=16)


class FeatureHistoryRow(Contract):
    org_id: UUID
    ref: FeatureRef
    revision_id: UUID
    revision: Revision
    name: str = Field(min_length=1, max_length=200)
    created_at: AwareDatetime
    created_by: UUID | None
    current: bool
    has_file: bool


class FeatureDetailData(Contract):
    org_id: UUID
    ref: FeatureRef
    current_revision: Revision
    lifecycle: LifecycleView
    provenance: Literal["declared", "simulated"]
    detail: FeatureDetail
    revised_at: AwareDatetime
    revised_by: UUID | None
    actions: list[ActionHint] = Field(max_length=16)

    @model_validator(mode="after")
    def identity_binding(self) -> Self:
        revision = self.detail.revision
        resource_id = revision.feature_id
        if (
            self.ref.kind != self.detail.kind
            or self.ref.resource_id != resource_id
            or self.org_id != revision.org_id
            or revision.revision > self.current_revision
        ):
            raise ValueError("resource, org and revision must match")
        if len(self.model_dump_json().encode("utf-8")) > DETAIL_BYTE_LIMIT:
            raise ValueError("detail exceeds its byte budget")
        return self


class FeatureLifecycleEvent(Contract):
    id: UUID
    org_id: UUID
    ref: FeatureRef
    revision: Revision
    resource_revision: Revision
    before: LifecycleState
    after: LifecycleState
    reason_code: ReasonCode
    actor_user_id: UUID
    actor_kind: Literal["session"] = "session"
    created_at: AwareDatetime

    @model_validator(mode="after")
    def actual_transition(self) -> Self:
        if self.before == self.after:
            raise ValueError("a lifecycle event records a change")
        if (self.after == "active") != (self.reason_code == "restored"):
            raise ValueError("restored is the required reason for activation only")
        return self


class FeatureLifecycleData(Contract):
    event: FeatureLifecycleEvent
    lifecycle: LifecycleView
    existing_selections: Literal["preserved"] = "preserved"

    @model_validator(mode="after")
    def current_event(self) -> Self:
        if (self.event.revision, self.event.after) != (
            self.lifecycle.revision,
            self.lifecycle.state,
        ):
            raise ValueError("event must describe the returned lifecycle")
        return self


class CertificateDetail(Contract):
    kind: Literal["certificates"] = "certificates"
    revision: CertificateRevision
    file: CertificateFileRevision | None = None

    @model_validator(mode="after")
    def exact_original(self) -> Self:
        if self.file is not None and (
            self.file.org_id != self.revision.org_id
            or self.file.certificate_id != self.revision.certificate_id
            or self.file.certificate_revision_id != self.revision.id
            or self.file.revision != self.revision.revision
            or self.file.data != self.revision.data
        ):
            raise ValueError("original and metadata must belong to the same exact revision")
        return self


class CertificateRef(Contract):
    kind: Literal["certificates"]
    resource_id: UUID


class CertificateRow(Contract):
    org_id: UUID
    ref: CertificateRef
    name: str = Field(min_length=1, max_length=200)
    revision_id: UUID
    revision: Revision
    lifecycle: LifecycleView
    provenance: Literal["declared"]
    created_at: AwareDatetime
    revised_at: AwareDatetime
    revised_by: UUID | None
    actions: list[ActionHint] = Field(max_length=16)


class CertificateHistoryRow(Contract):
    org_id: UUID
    ref: CertificateRef
    revision_id: UUID
    revision: Revision
    name: str = Field(min_length=1, max_length=200)
    created_at: AwareDatetime
    created_by: UUID | None
    current: bool
    has_file: bool


class CertificateDateAdvisory(Contract):
    """Declared-date classification; absent as_of never implies the current date."""

    as_of: date | None
    state: Literal["unknown", "expired", "not_yet_valid", "valid"]

    @model_validator(mode="after")
    def explicit_date(self) -> Self:
        if self.as_of is None and self.state != "unknown":
            raise ValueError("date classification requires explicit as_of")
        return self


class CertificateDetailQuery(ResourceDetailQuery):
    as_of: date | None = None


class CertificateDetailData(Contract):
    org_id: UUID
    ref: CertificateRef
    current_revision: Revision
    lifecycle: LifecycleView
    provenance: Literal["declared"]
    detail: CertificateDetail
    date_advisory: CertificateDateAdvisory
    revised_at: AwareDatetime
    revised_by: UUID | None
    actions: list[ActionHint] = Field(max_length=16)

    @model_validator(mode="after")
    def identity_binding(self) -> Self:
        revision = self.detail.revision
        resource_id = revision.certificate_id
        if (
            self.ref.kind != self.detail.kind
            or self.ref.resource_id != resource_id
            or self.org_id != revision.org_id
            or revision.revision > self.current_revision
        ):
            raise ValueError("resource, org and revision must match")
        if len(self.model_dump_json().encode("utf-8")) > DETAIL_BYTE_LIMIT:
            raise ValueError("detail exceeds its byte budget")
        return self


class CertificateLifecycleEvent(Contract):
    id: UUID
    org_id: UUID
    ref: CertificateRef
    revision: Revision
    resource_revision: Revision
    before: LifecycleState
    after: LifecycleState
    reason_code: ReasonCode
    actor_user_id: UUID
    actor_kind: Literal["session"] = "session"
    created_at: AwareDatetime

    @model_validator(mode="after")
    def actual_transition(self) -> Self:
        if self.before == self.after:
            raise ValueError("a lifecycle event records a change")
        if (self.after == "active") != (self.reason_code == "restored"):
            raise ValueError("restored is the required reason for activation only")
        return self


class CertificateLifecycleData(Contract):
    event: CertificateLifecycleEvent
    lifecycle: LifecycleView
    existing_selections: Literal["preserved"] = "preserved"

    @model_validator(mode="after")
    def current_event(self) -> Self:
        if (self.event.revision, self.event.after) != (
            self.lifecycle.revision,
            self.lifecycle.state,
        ):
            raise ValueError("event must describe the returned lifecycle")
        return self


class ProfileDetail(Contract):
    kind: Literal["profiles"] = "profiles"
    revision: OrgProfileRevision


class ProfileRef(Contract):
    kind: Literal["profiles"]
    resource_id: UUID


class ProfileRow(Contract):
    org_id: UUID
    ref: ProfileRef
    name: str = Field(min_length=1, max_length=200)
    revision_id: UUID
    revision: Revision
    lifecycle: LifecycleView
    provenance: Literal["declared"]
    created_at: AwareDatetime
    revised_at: AwareDatetime
    revised_by: UUID | None
    actions: list[ActionHint] = Field(max_length=16)


class ProfileHistoryRow(Contract):
    org_id: UUID
    ref: ProfileRef
    revision_id: UUID
    revision: Revision
    name: str = Field(min_length=1, max_length=200)
    created_at: AwareDatetime
    created_by: UUID | None
    current: bool
    has_file: bool


class ProfileDetailData(Contract):
    org_id: UUID
    ref: ProfileRef
    current_revision: Revision
    lifecycle: LifecycleView
    provenance: Literal["declared"]
    detail: ProfileDetail
    revised_at: AwareDatetime
    revised_by: UUID | None
    actions: list[ActionHint] = Field(max_length=16)

    @model_validator(mode="after")
    def identity_binding(self) -> Self:
        revision = self.detail.revision
        resource_id = revision.profile_id
        if (
            self.ref.kind != self.detail.kind
            or self.ref.resource_id != resource_id
            or self.org_id != revision.org_id
            or revision.revision > self.current_revision
        ):
            raise ValueError("resource, org and revision must match")
        if len(self.model_dump_json().encode("utf-8")) > DETAIL_BYTE_LIMIT:
            raise ValueError("detail exceeds its byte budget")
        return self


class ProfileLifecycleEvent(Contract):
    id: UUID
    org_id: UUID
    ref: ProfileRef
    revision: Revision
    resource_revision: Revision
    before: LifecycleState
    after: LifecycleState
    reason_code: ReasonCode
    actor_user_id: UUID
    actor_kind: Literal["session"] = "session"
    created_at: AwareDatetime

    @model_validator(mode="after")
    def actual_transition(self) -> Self:
        if self.before == self.after:
            raise ValueError("a lifecycle event records a change")
        if (self.after == "active") != (self.reason_code == "restored"):
            raise ValueError("restored is the required reason for activation only")
        return self


class ProfileLifecycleData(Contract):
    event: ProfileLifecycleEvent
    lifecycle: LifecycleView
    existing_selections: Literal["preserved"] = "preserved"

    @model_validator(mode="after")
    def current_event(self) -> Self:
        if (self.event.revision, self.event.after) != (
            self.lifecycle.revision,
            self.lifecycle.state,
        ):
            raise ValueError("event must describe the returned lifecycle")
        return self


class TemplateDetailQuery(ResourceDetailQuery):
    revision_id: UUID | None = None

    @model_validator(mode="after")
    def exact_revision(self) -> Self:
        if self.revision is not None and self.revision_id is not None:
            raise ValueError("choose revision or revision_id, not both")
        return self


class TemplateDetail(Contract):
    kind: Literal["templates"] = "templates"
    revision: TemplateRevision


class TemplateRef(Contract):
    kind: Literal["templates"]
    resource_id: UUID


class TemplateRow(Contract):
    org_id: UUID
    ref: TemplateRef
    name: str = Field(min_length=1, max_length=200)
    revision_id: UUID
    revision: Revision
    lifecycle: LifecycleView
    provenance: Literal["declared"]
    created_at: AwareDatetime
    revised_at: AwareDatetime
    revised_by: UUID | None
    actions: list[ActionHint] = Field(max_length=16)


class TemplateHistoryRow(Contract):
    org_id: UUID
    ref: TemplateRef
    revision_id: UUID
    revision: Revision
    name: str = Field(min_length=1, max_length=200)
    created_at: AwareDatetime
    created_by: UUID | None
    current: bool
    has_file: bool


class TemplateDetailData(Contract):
    org_id: UUID
    ref: TemplateRef
    current_revision: Revision
    lifecycle: LifecycleView
    provenance: Literal["declared"]
    detail: TemplateDetail
    revised_at: AwareDatetime
    revised_by: UUID | None
    actions: list[ActionHint] = Field(max_length=16)

    @model_validator(mode="after")
    def identity_binding(self) -> Self:
        revision = self.detail.revision
        resource_id = revision.template_id
        if (
            self.ref.kind != self.detail.kind
            or self.ref.resource_id != resource_id
            or self.org_id != revision.org_id
            or revision.revision > self.current_revision
        ):
            raise ValueError("resource, org and revision must match")
        if len(self.model_dump_json().encode("utf-8")) > DETAIL_BYTE_LIMIT:
            raise ValueError("detail exceeds its byte budget")
        return self


class TemplateLifecycleEvent(Contract):
    id: UUID
    org_id: UUID
    ref: TemplateRef
    revision: Revision
    resource_revision: Revision
    before: LifecycleState
    after: LifecycleState
    reason_code: ReasonCode
    actor_user_id: UUID
    actor_kind: Literal["session"] = "session"
    created_at: AwareDatetime

    @model_validator(mode="after")
    def actual_transition(self) -> Self:
        if self.before == self.after:
            raise ValueError("a lifecycle event records a change")
        if (self.after == "active") != (self.reason_code == "restored"):
            raise ValueError("restored is the required reason for activation only")
        return self


class TemplateLifecycleData(Contract):
    event: TemplateLifecycleEvent
    lifecycle: LifecycleView
    existing_selections: Literal["preserved"] = "preserved"

    @model_validator(mode="after")
    def current_event(self) -> Self:
        if (self.event.revision, self.event.after) != (
            self.lifecycle.revision,
            self.lifecycle.state,
        ):
            raise ValueError("event must describe the returned lifecycle")
        return self


class BindingQuery(PageQuery):
    template_revision_id: UUID


class BindingDetailQuery(Contract):
    template_revision_id: UUID
