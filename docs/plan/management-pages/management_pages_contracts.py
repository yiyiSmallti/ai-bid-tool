"""U01 management pages: pending approval, not implemented.

Only proposed interfaces and Pydantic v2 payloads live here. No handlers, database
writes, Provider calls or runtime registration occur on import. Existing mutation
schemas remain authoritative; these projections never authorize an action.
"""

from typing import Annotated, Literal, Protocol, Self
from uuid import UUID

from app.schemas.certificate_contracts import CertificateRevision
from app.schemas.certificate_file_contracts import CertificateFileRevision
from app.schemas.confidential_contracts import (
    ConfidentialFieldView,
    ConfidentialValueSet,
    ConfidentialValueView,
)
from app.schemas.contracts import CONTRACT_VERSION, Contract, Cost, Result
from app.schemas.export_contracts import ExportBindingView
from app.schemas.feature_contracts import FeatureRevision
from app.schemas.memory_contracts import MemoryKind, MemoryStatus, MemoryView, Tag
from app.schemas.profile_contracts import OrgProfileRevision
from app.schemas.provider_contracts import ProviderConfigInput
from app.schemas.resource_contracts import ProductRevision
from app.schemas.template_contracts import TemplateRevision
from app.services.auth import Identity
from pydantic import AwareDatetime, Field, StringConstraints, model_validator

RESULT_CONTRACT_VERSION = CONTRACT_VERSION
PAGE_BYTE_LIMIT = 256 * 1024
DETAIL_BYTE_LIMIT = 1024 * 1024

type ResourceKind = Literal["products", "features", "certificates", "profiles", "templates"]
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
    kind: ResourceKind
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


class CertificateDetail(Contract):
    kind: Literal["certificates"] = "certificates"
    revision: CertificateRevision
    file: CertificateFileRevision | None = None

    @model_validator(mode="after")
    def file_binding(self) -> Self:
        if self.file is not None and (
            self.file.org_id != self.revision.org_id
            or self.file.certificate_id != self.revision.certificate_id
            or self.file.certificate_revision_id != self.revision.id
            or self.file.revision != self.revision.revision
            or self.file.data != self.revision.data
            or len(self.file.parts) > 20
        ):
            raise ValueError("certificate file must belong to the exact metadata revision")
        return self


class ProfileDetail(Contract):
    kind: Literal["profiles"] = "profiles"
    revision: OrgProfileRevision


class TemplateDetail(Contract):
    kind: Literal["templates"] = "templates"
    revision: TemplateRevision


type ResourceDetail = Annotated[
    ProductDetail | FeatureDetail | CertificateDetail | ProfileDetail | TemplateDetail,
    Field(discriminator="kind"),
]


class ResourceDetailData(Contract):
    org_id: UUID
    ref: ResourceRef
    current_revision: Revision
    lifecycle: LifecycleView
    provenance: Literal["declared", "simulated"]
    detail: ResourceDetail
    actions: list[ActionHint] = Field(max_length=16)

    @model_validator(mode="after")
    def identity_binding(self) -> Self:
        revision = self.detail.revision
        match self.detail:
            case ProductDetail():
                resource_id = self.detail.revision.product_id
            case FeatureDetail():
                resource_id = self.detail.revision.feature_id
            case CertificateDetail():
                resource_id = self.detail.revision.certificate_id
            case ProfileDetail():
                resource_id = self.detail.revision.profile_id
            case TemplateDetail():
                resource_id = self.detail.revision.template_id
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


class BindingQuery(PageQuery):
    template_revision_id: UUID


class BindingDetailQuery(Contract):
    template_revision_id: UUID


class CatalogReasoningChoice(Contract):
    name: str = Field(min_length=1, max_length=20)
    label: str | None = Field(default=None, max_length=100)


class PlatformModelChoice(Contract):
    """Only the org-visible subset of provider_configs.catalog_view, plus catalog revision."""

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,39}$")
    revision: Revision
    model: str = Field(min_length=1, max_length=100)
    provider: Literal["anthropic", "openai"]
    sale_input_per_mtok: float = Field(ge=0, allow_inf_nan=False)
    sale_output_per_mtok: float = Field(ge=0, allow_inf_nan=False)
    default: bool
    reasoning: list[CatalogReasoningChoice] = Field(max_length=8)
    default_reasoning: str | None


class ProviderRevisionMetadata(Contract):
    """Explicit allowlist; no key, ciphertext, suffix, fingerprint or platform endpoint."""

    id: UUID
    org_id: UUID
    revision: Revision
    configuration: ProviderConfigInput
    provider: Literal["anthropic", "openai"]
    model: str = Field(min_length=1, max_length=100)
    catalog_state: Literal["enabled", "unavailable", "not_applicable"]
    credential_state: Literal["configured", "platform_managed"]
    updated_by: UUID
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def read_only_configuration(self) -> Self:
        if self.configuration.expected_revision is not None:
            raise ValueError("read projection cannot supply a write precondition")
        expected = "configured" if self.configuration.source == "org" else "platform_managed"
        if self.credential_state != expected:
            raise ValueError("credential state must match the selected source")
        if self.configuration.source == "org":
            if (
                self.catalog_state != "not_applicable"
                or self.provider != self.configuration.provider
                or self.model != self.configuration.model
            ):
                raise ValueError("BYOK metadata must match its stored configuration")
        elif self.catalog_state == "not_applicable":
            raise ValueError("a platform selection requires catalog availability")
        return self


class ProviderSettingsData(Contract):
    org_id: UUID
    capability: Literal["llm_extract"] = "llm_extract"
    effective_source: Literal["org", "platform", "unconfigured"]
    current: ProviderRevisionMetadata | None
    default_model: PlatformModelChoice | None
    billing_currency: str = Field(pattern=r"^[A-Z]{3}$")
    reasoning: list[CatalogReasoningChoice] = Field(max_length=8)
    actions: list[ActionHint] = Field(max_length=16)
    # This is configuration metadata, never a successful connection/decryption claim.
    connection_status: Literal["not_checked"] = "not_checked"

    @model_validator(mode="after")
    def effective_source_binding(self) -> Self:
        if self.current is not None and self.current.org_id != self.org_id:
            raise ValueError("configuration must belong to the authenticated org")
        expected = (
            self.current.configuration.source
            if self.current is not None
            else "platform"
            if self.default_model is not None
            else "unconfigured"
        )
        if self.effective_source != expected:
            raise ValueError("effective source must follow org revision then platform default")
        return self


class MemoryQuery(PageQuery):
    scope: Literal["org"] = "org"
    q: SearchText | None = None
    kind: MemoryKind | None = None
    status: MemoryStatus | None = None
    tags: list[Tag] = Field(default_factory=list, max_length=20)
    expiry: Literal["all", "expired", "unexpired"] = "all"
    include_deleted: bool = False

    @model_validator(mode="after")
    def normalized_tags(self) -> Self:
        normalized = [tag.strip().casefold() for tag in self.tags]
        if any(not tag for tag in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("search tags must be nonblank and unique")
        self.tags = sorted(normalized)
        return self


class ConfidentialQuery(PageQuery):
    q: SearchText | None = None
    task_id: UUID | None = None
    archived: bool = False


class ConfidentialHistoryQuery(PageQuery):
    task_id: UUID | None = None


class ConfidentialValueRevisionSet(ConfidentialValueSet):
    """One-time value input; no generic serializer may echo the plaintext input."""

    value: str = Field(min_length=1, max_length=2000, exclude=True, repr=False)
    expected_field_revision: Revision
    # Required even when null: null asserts there is no current value for this owner.
    expected_value_id: UUID | None


class ManagementResultAdapter(Protocol):
    """Use shared Result 4.0, never a second wrapper or a seventh-to-eighth-key change.

    Reads must fit the complete envelope and generate the exact continuation before
    constructing Page. This adapter validates the final encoder's complete Result
    bytes, including warnings/cost/duration; it cannot remove rows or change cursors.
    Oversize details fail; never truncate content or hide integrity errors.
    """

    def page[T](self, command: str, page: Page[T], cost: Cost, duration_ms: int) -> Result: ...

    def detail(self, command: str, data: Contract, cost: Cost, duration_ms: int) -> Result: ...


class ManagementResourceReads(Protocol):
    """Authorize current Membership/scopes before any query; no outbound calls.

    SQL keyset LIMIT precedes materialization. Historical reads use exact IDs and
    do not fetch the entire library. No visible task IDs/counts are inferred from
    org resource ownership. Actions are hints and writes reauthorize live state.
    Reject feature-only filters on other kinds. Check every projected row's org,
    kind and history root against the request. Resolve authors through exact
    revision audit associations, with null for ambiguous or unavailable authors.
    Fit complete Result bytes and issue the retained-row continuation before Page
    validation; never reuse a cursor computed for subsequently removed rows.
    """

    async def query(
        self, actor: Identity, kind: ResourceKind, query: ResourceQuery
    ) -> Page[ResourceRow]: ...

    async def detail(
        self, actor: Identity, ref: ResourceRef, query: ResourceDetailQuery
    ) -> ResourceDetailData: ...

    async def history(
        self, actor: Identity, ref: ResourceRef, query: PageQuery
    ) -> Page[ResourceHistoryRow]: ...

    async def lifecycle_history(
        self, actor: Identity, ref: ResourceRef, query: PageQuery
    ) -> Page[ResourceLifecycleEvent]: ...


class ManagementResourceLifecycle(Protocol):
    """Human session plus the kind's existing write scope; no token authority.

    Lock root, compare both versions, append event and update head with audit in
    one transaction. Selection must check the same root under its lock. Existing
    selections remain usable; deactivation does not confirm or invalidate evidence.
    Lifecycle-only writes never lock tasks after the root. Task selection uses
    task/workflow then root locks; an exact existing active pin may return a no-op
    duplicate receipt for an inactive root, but no new/replacement pin is allowed.
    """

    async def set_state(
        self, actor: Identity, ref: ResourceRef, command: ResourceLifecycleSet
    ) -> ResourceLifecycleData: ...


class ManagementBindingReads(Protocol):
    """Reuse exports.human_access; each binding must match its exact template revision."""

    async def query(self, actor: Identity, query: BindingQuery) -> Page[ExportBindingView]: ...

    async def detail(
        self, actor: Identity, binding_id: UUID, query: BindingDetailQuery
    ) -> ExportBindingView: ...


class ManagementProviderReads(Protocol):
    """Reuse provider_configs.require_access; no balance query, decryption or vendor SDK.

    Platform catalog fields come only from the existing org-safe catalog view.
    History cannot run one usage aggregation per revision or resolve old keys.
    """

    async def settings(self, actor: Identity) -> ProviderSettingsData: ...

    async def revision(self, actor: Identity, config_id: UUID) -> ProviderRevisionMetadata: ...

    async def history(
        self, actor: Identity, query: PageQuery
    ) -> Page[ProviderRevisionMetadata]: ...

    async def catalog(self, actor: Identity, query: PageQuery) -> Page[PlatformModelChoice]: ...


class ManagementMemoryReads(Protocol):
    """Share memory access/visible_sources/safety; management search is not retrieval.

    Enforce org-only scope and deleted visibility before search. Apply the same
    provenance redaction as crud.visible_sources, including on historical links.
    """

    async def query(self, actor: Identity, query: MemoryQuery) -> Page[MemoryView]: ...


class ManagementConfidential(Protocol):
    """Reads never decrypt; task owners remain subject to task_workflow.access.

    CAS and existing confidential.set_value must share the field lock/transaction.
    Compare current value for this field and exact org/task owner. Dedicated wire
    construction sends the transient value once; never use generic model_dump.
    After CAS, explicitly construct ConfidentialValueSet(value=command.value,
    task_id=command.task_id); do not pass expected_* fields to the legacy setter.
    A stale field/value cannot append a replacement. Reveal remains exclusively
    the existing confidential.reveal route, outside list/detail projections.
    """

    async def fields(
        self, actor: Identity, query: ConfidentialQuery
    ) -> Page[ConfidentialFieldView]: ...

    async def values(
        self, actor: Identity, query: ConfidentialQuery
    ) -> Page[ConfidentialValueView]: ...

    async def history(
        self, actor: Identity, field_id: UUID, query: ConfidentialHistoryQuery
    ) -> Page[ConfidentialValueView]: ...

    async def set_value(
        self, actor: Identity, field_id: UUID, command: ConfidentialValueRevisionSet
    ) -> ConfidentialValueView: ...
