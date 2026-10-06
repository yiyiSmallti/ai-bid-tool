"""Approved B05 server-job contract; never imported or registered by runtime.

No routes, jobs, persistence, grants, renderer execution or confirmation occurs here.
The schemas describe a candidate followed by a separately gated release rendition.
"""

import hashlib
import json
from typing import TYPE_CHECKING, Annotated, Literal, Protocol
from uuid import UUID

from app.schemas.budget_contracts import BudgetPreflightData
from app.schemas.contracts import CONTRACT_VERSION, Contract, Cost, Result
from app.schemas.evidence_source_contracts import EvidenceSourceArchive
from app.schemas.requirement_confirmation import ReviewState
from app.schemas.response_card_contracts import CardAction, ReviewDomain
from app.schemas.screenshot_contracts import (
    ContentMapping,
    PixelRect,
    PNGDescriptor,
    Sha256,
    VendorArchiveView,
)
from app.schemas.team_workflow import CoSignConfirm
from pydantic import AwareDatetime, Field, model_validator

if TYPE_CHECKING:
    from app.services.auth import Identity
    from sqlalchemy.ext.asyncio import AsyncSession

HTTP_INPUT_LIMIT = 128 * 1024
RENDERER_JSON_LIMIT = 64 * 1024
PNG_BYTE_LIMIT = 40 * 1024 * 1024
PNG_PIXEL_LIMIT = 20_000_000

Revision = Annotated[int, Field(strict=True, ge=1)]
AnnotationJobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]
AnnotationBlocker = Literal[
    "source_inactive",
    "source_withdrawn",
    "source_integrity_failure",
    "vendor_source_not_enabled",
    "card_revision_changed",
    "card_not_editable",
    "requirement_review_not_current",
    "privacy_lineage_invalid",
    "renderer_unavailable",
    "output_limit_exceeded",
    "approval_not_complete",
    "approval_stale",
    "release_binding_changed",
]


def _bounded_image(image: PNGDescriptor) -> None:
    if image.width_px * image.height_px > PNG_PIXEL_LIMIT:
        raise ValueError("PNG pixel limit exceeded")


class AnnotationPlan(Contract):
    """Coordinates belong to authorized source content; generated footers are excluded."""

    crop: PixelRect | None = None
    boxes: list[PixelRect] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def bounded_endpoints(self):
        for rect in ([self.crop] if self.crop else []) + self.boxes:
            if not rect.within(8192, 8192):
                raise ValueError("rectangle endpoint exceeds the profile limit")
        return self

    def canonical_sha256(self) -> str:
        encoded = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class CertificateAnnotationSource(Contract):
    kind: Literal["certificate_page"]
    evidence_source_id: UUID


class VendorAnnotationSource(Contract):
    kind: Literal["vendor_rendition"]
    asset_id: UUID
    rendition_id: UUID
    expected_image_sha256: Sha256


AnnotationSource = Annotated[
    CertificateAnnotationSource | VendorAnnotationSource, Field(discriminator="kind")
]


class AnnotationInput(Contract):
    """The human-authored file has no org, actor, storage key, path or gate flags."""

    extraction_job_id: UUID
    card_id: UUID
    expected_card_revision: Revision
    source: AnnotationSource
    plan: AnnotationPlan


class AnnotationSubmit(Contract):
    input: AnnotationInput
    dry_run: Literal[False] = False
    reviewed_source_png_sha256: Sha256
    expected_input_hash: Sha256
    request_id: UUID
    retry: bool = False


class AnnotationPreflightRequest(Contract):
    input: AnnotationInput
    dry_run: Literal[True] = True


class CertificatePageBinding(Contract):
    kind: Literal["certificate_page"] = "certificate_page"
    archive: EvidenceSourceArchive
    source_png: PNGDescriptor
    page_kind: Literal["pdf_page"] = "pdf_page"
    source_time_kind: Literal["server_page_rendered_at"] = "server_page_rendered_at"
    source_time: AwareDatetime
    original_sha256: Sha256
    selection_id: UUID
    resource_revision_id: UUID
    authorized_content: PixelRect

    @model_validator(mode="after")
    def fixed_archive(self):
        _bounded_image(self.source_png)
        preview = self.archive.preview
        if (
            self.source_png.model_dump()
            != PNGDescriptor(
                sha256=preview.sha256,
                size_bytes=preview.size_bytes,
                width_px=preview.width_px,
                height_px=preview.height_px,
            ).model_dump()
        ):
            raise ValueError("source PNG must be the archived certificate preview")
        if self.original_sha256 != self.archive.original.sha256:
            raise ValueError("original hash must name the fixed certificate PDF")
        if self.source_time != self.archive.rendered_at:
            raise ValueError("certificate source time must be server page-render time")
        if self.selection_id != self.archive.task_certificate_id:
            raise ValueError("certificate selection binding differs from archive")
        if self.resource_revision_id != self.archive.certificate_revision_id:
            raise ValueError("certificate revision binding differs from archive")
        if self.authorized_content != PixelRect(
            x=0, y=0, width=preview.width_px, height=preview.height_px
        ):
            raise ValueError("certificate content must preserve the full archived page")
        return self


class VendorWebPage(Contract):
    kind: Literal["web_page"] = "web_page"
    page: Literal[1] = 1


class VendorPDFPage(Contract):
    kind: Literal["pdf_page"] = "pdf_page"
    page: int = Field(strict=True, ge=1)


class VendorRenditionBinding(Contract):
    """Server-normalized lineage; no raw URLs, storage keys or local paths."""

    kind: Literal["vendor_rendition"] = "vendor_rendition"
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    asset_id: UUID
    parent_rendition_id: UUID
    archive: VendorArchiveView
    page: Annotated[VendorWebPage | VendorPDFPage, Field(discriminator="kind")]
    source_time_kind: Literal["vendor_captured_at"] = "vendor_captured_at"
    source_time: AwareDatetime
    original_sha256: Sha256
    source_png: PNGDescriptor
    root_source_png_sha256: Sha256
    selection_id: UUID
    resource_revision_id: UUID
    authorized_content: PixelRect
    source_crop_mapping: ContentMapping
    source_profile: Literal["screenshot-markup-v1"] = "screenshot-markup-v1"
    privacy_review_id: UUID
    privacy_lineage_sha256: Sha256
    root_mapping_sha256: Sha256

    @model_validator(mode="after")
    def genuine_vendor_source(self):
        _bounded_image(self.source_png)
        if not self.authorized_content.within(self.source_png.width_px, self.source_png.height_px):
            raise ValueError("authorized content exceeds the parent PNG")
        if self.source_time != self.archive.captured_at:
            raise ValueError("vendor source time differs from capture archive")
        if self.original_sha256 != self.archive.content_sha256:
            raise ValueError("original hash must name the captured archive content")
        if self.selection_id != self.archive.task_resource_id:
            raise ValueError("vendor selection binding differs from archive")
        if self.resource_revision_id != self.archive.product_revision_id:
            raise ValueError("vendor product revision differs from archive")
        if (self.page.kind == "web_page") != (self.archive.format == "web"):
            raise ValueError("web page numbering cannot represent a PDF page")
        mapping = self.source_crop_mapping
        if self.authorized_content != PixelRect(
            x=mapping.content_offset_x,
            y=mapping.content_offset_y,
            width=mapping.content_width,
            height=mapping.content_height,
        ):
            raise ValueError("authorized content must exclude parent padding and footer")
        return self


AnnotationSourceBinding = Annotated[
    CertificatePageBinding | VendorRenditionBinding, Field(discriminator="kind")
]


class AnnotationRequirementPin(Contract):
    requirement_id: UUID
    review_revision: Revision
    review_hash: Sha256
    state: ReviewState
    source_binding_sha256: Sha256 | None


class AnnotationRendererIdentity(Contract):
    protocol_version: Literal["annotation-render-v1"] = "annotation-render-v1"
    profile: Literal["annotation-candidate-v1", "annotation-release-v1"]
    version: str = Field(min_length=1, max_length=100)
    binary_sha256: Sha256
    font_bundle_sha256: Sha256


class AnnotationInputManifest(Contract):
    org_id: UUID
    task_id: UUID
    target: AnnotationInput
    card_content_sha256: Sha256
    requirement: AnnotationRequirementPin
    source: AnnotationSourceBinding
    plan_sha256: Sha256
    renderer: AnnotationRendererIdentity
    policy_version: Literal["annotation-binding-v1"] = "annotation-binding-v1"

    def canonical_sha256(self) -> str:
        encoded = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @model_validator(mode="after")
    def exact_binding(self):
        if self.plan_sha256 != self.target.plan.canonical_sha256():
            raise ValueError("plan hash differs from validated canonical defaults and box order")
        if self.renderer.profile != "annotation-candidate-v1":
            raise ValueError("candidate input requires the candidate renderer profile")
        source = self.source
        org_id, task_id = (
            (source.archive.org_id, source.archive.task_id)
            if isinstance(source, CertificatePageBinding)
            else (source.org_id, source.task_id)
        )
        if (org_id, task_id) != (self.org_id, self.task_id):
            raise ValueError("source must belong to the request org and task")
        supplied = self.target.source
        if isinstance(supplied, CertificateAnnotationSource):
            if not isinstance(source, CertificatePageBinding) or (
                supplied.evidence_source_id != source.archive.id
            ):
                raise ValueError("certificate source ID differs from normalized archive")
        elif not isinstance(source, VendorRenditionBinding) or (
            supplied.asset_id != source.asset_id
            or supplied.rendition_id != source.parent_rendition_id
            or supplied.expected_image_sha256 != source.source_png.sha256
            or self.target.extraction_job_id != source.extraction_job_id
        ):
            raise ValueError("vendor input differs from normalized parent rendition")
        crop = self.target.plan.crop or PixelRect(
            x=0,
            y=0,
            width=source.authorized_content.width,
            height=source.authorized_content.height,
        )
        if not crop.within(source.authorized_content.width, source.authorized_content.height):
            raise ValueError("crop exceeds authorized source content")
        for box in self.target.plan.boxes:
            if (
                box.x < crop.x
                or box.y < crop.y
                or not box.within(crop.x + crop.width, crop.y + crop.height)
            ):
                raise ValueError("box must be inside the crop in source-content coordinates")
        return self


class AnnotationCanvas(Contract):
    width_px: int = Field(strict=True, ge=1, le=8192)
    height_px: int = Field(strict=True, ge=1, le=8192)
    maximum_png_bytes: Literal[41943040] = PNG_BYTE_LIMIT
    mapping: ContentMapping

    @model_validator(mode="after")
    def bounded_canvas(self):
        mapping = self.mapping
        if self.width_px * self.height_px > PNG_PIXEL_LIMIT:
            raise ValueError("complete canvas pixel limit exceeded")
        if (
            mapping.content_offset_y != 0
            or mapping.content_width != mapping.crop.width
            or mapping.content_height != mapping.crop.height
            or mapping.content_offset_x != (self.width_px - mapping.content_width) // 2
            or self.width_px != max(mapping.content_width, 1024)
            or self.height_px != mapping.content_height + mapping.footer_height
            or mapping.footer_height <= 0
        ):
            raise ValueError("mapping must preserve unscaled content with the fixed footer")
        return self


class AnnotationPreflight(Contract):
    dry_run: Literal[True] = True
    input_hash: Sha256
    manifest: AnnotationInputManifest
    predicted_output: AnnotationCanvas | None
    budget_preflight: BudgetPreflightData
    blocker_codes: list[AnnotationBlocker] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def read_only_zero_cost(self):
        budget = self.budget_preflight
        if self.input_hash != self.manifest.canonical_sha256():
            raise ValueError("input hash must bind the exact normalized server manifest")
        if budget.input_hash != self.input_hash or budget.task_id != self.manifest.task_id:
            raise ValueError("budget preflight must bind the same normalized input")
        if budget.command != "evidence stamp" or budget.planned_calls != 0:
            raise ValueError("annotation preflight plans no billable provider calls")
        if (
            budget.next_call is not None
            or budget.estimate.charge != 0
            or budget.estimate.task_amount != 0
            or budget.estimate.basis not in {"zero", "cache_hit"}
            or budget.estimate.llm_tokens != 0
            or budget.estimate.ocr_pages != 0
            or budget.estimate.usd != 0
            or budget.estimate.unpriced_calls != 0
            or budget.estimate.unresolved_calls != 0
            or budget.admission_blocker is not None
        ):
            raise ValueError("local annotation has zero provider liability")
        if not self.blocker_codes and self.predicted_output is None:
            raise ValueError("an admitted preflight requires a bounded canvas prediction")
        return self


class AnnotationJobAccepted(Contract):
    job_id: UUID
    task_id: UUID
    card_id: UUID
    kind: Literal["annotation_render", "annotation_release"]
    status: AnnotationJobStatus
    duplicate: bool


class AnnotationRendering(Contract):
    renderer: AnnotationRendererIdentity
    plan_sha256: Sha256
    provenance_sha256: Sha256
    image: PNGDescriptor
    canvas: AnnotationCanvas
    content_pixel_sha256: Sha256
    root_mapping_sha256: Sha256

    @model_validator(mode="after")
    def complete_output_bounds(self):
        _bounded_image(self.image)
        if (self.image.width_px, self.image.height_px) != (
            self.canvas.width_px,
            self.canvas.height_px,
        ):
            raise ValueError("actual PNG dimensions differ from the complete canvas")
        return self


class AnnotationCandidateView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    job_id: UUID
    input_hash: Sha256
    manifest: AnnotationInputManifest
    asset_id: UUID
    rendition_id: UUID
    rendering: AnnotationRendering
    created_at: AwareDatetime
    status: Literal["unconfirmed_material"] = "unconfirmed_material"
    confirmed_by: Literal[None] = None
    eligible_for_draft_export: Literal[False] = False

    @model_validator(mode="after")
    def candidate_scope(self):
        if (self.org_id, self.task_id) != (self.manifest.org_id, self.manifest.task_id):
            raise ValueError("candidate scope differs from normalized manifest")
        if self.input_hash != self.manifest.canonical_sha256():
            raise ValueError("candidate must retain its exact submitted input hash")
        content = self.manifest.source.authorized_content
        crop = self.manifest.target.plan.crop or PixelRect(
            x=0, y=0, width=content.width, height=content.height
        )
        if self.rendering.canvas.mapping.crop != crop:
            raise ValueError("candidate mapping differs from the submitted authorized crop")
        if self.rendering.renderer != self.manifest.renderer or (
            self.rendering.plan_sha256 != self.manifest.plan_sha256
        ):
            raise ValueError("candidate receipt must retain exact renderer and plan identity")
        return self


class AnnotationSignaturePin(Contract):
    """Exact signature keys emitted by task_cosign.projections."""

    id: UUID
    domain: ReviewDomain
    signer_user_id: UUID
    request_sha256: Sha256
    reason_sha256: Sha256 | None


class AnnotationCoSignManifest(Contract):
    """Exact co_sign shape emitted by task_cosign.manifest_fields."""

    policy_revision: int = Field(strict=True, ge=0)
    task_rule_revision: Revision
    round_id: UUID | None
    round_revision: int = Field(strict=True, ge=0)
    purpose: Literal["response", "disposition"] | None
    required_domains: list[ReviewDomain] = Field(min_length=1, max_length=2)
    status: Literal["not_required", "pending", "partial", "complete", "invalidated"]
    evidence_sha256: Sha256 | None
    requirement_sha256: Sha256 | None
    citation_sha256: Sha256 | None
    content_sha256: Sha256 | None
    signatures: list[AnnotationSignaturePin] = Field(max_length=2)


class AnnotationApprovalBinding(Contract):
    """Server decision pin; a release worker never creates or changes approval."""

    evidence_id: UUID
    card_id: UUID
    card_revision: Revision
    card_revision_id: UUID
    confirmed_by: UUID
    confirmed_at: AwareDatetime
    candidate_annotation_id: UUID
    candidate_rendition_id: UUID
    candidate_image_sha256: Sha256
    candidate_plan_sha256: Sha256
    candidate_content_mapping: ContentMapping
    content_pixel_sha256: Sha256
    root_mapping_sha256: Sha256
    reviewed_region: PixelRect
    requirement: AnnotationRequirementPin
    decision_kind: Literal["single_domain", "cosign"]
    review_domain: ReviewDomain
    co_sign: AnnotationCoSignManifest | None
    card_content_sha256: Sha256
    evidence_binding_sha256: Sha256
    approval_binding_sha256: Sha256

    @model_validator(mode="after")
    def complete_approval(self):
        if self.requirement.state != "confirmed" or self.requirement.source_binding_sha256 is None:
            raise ValueError("release requires current B02 confirmation with a verified source")
        co_sign = self.co_sign
        if co_sign is not None and (
            len(set(co_sign.required_domains)) != len(co_sign.required_domains)
            or len({signature.domain for signature in co_sign.signatures})
            != len(co_sign.signatures)
            or len({signature.id for signature in co_sign.signatures}) != len(co_sign.signatures)
            or self.review_domain not in co_sign.required_domains
        ):
            raise ValueError("co-sign domains and signature identities must be unique and bound")
        if self.decision_kind == "single_domain" and co_sign is None:
            # Only a genuine historical decision without a round uses this shape.
            # The service must retain every real round, including one-domain rounds.
            return self
        if self.decision_kind == "single_domain" and co_sign is not None:
            if co_sign.required_domains != [self.review_domain]:
                raise ValueError("single-domain approval must retain its sole review domain")
            if co_sign.round_id is None:
                if (
                    co_sign.round_revision != 0
                    or co_sign.status != "not_required"
                    or co_sign.purpose is not None
                    or co_sign.signatures
                ):
                    raise ValueError("legacy approval cannot fabricate an active review round")
                return self
        if co_sign is None or (
            co_sign.round_id is None
            or co_sign.round_revision < 1
            or co_sign.status != "complete"
            or co_sign.purpose != "response"
            or set(co_sign.required_domains)
            != {signature.domain for signature in co_sign.signatures}
            or len(co_sign.required_domains) != len(co_sign.signatures)
            or len({signature.signer_user_id for signature in co_sign.signatures})
            != len(co_sign.signatures)
            or any(
                value is None
                for value in (
                    co_sign.evidence_sha256,
                    co_sign.requirement_sha256,
                    co_sign.citation_sha256,
                    co_sign.content_sha256,
                )
            )
        ):
            raise ValueError("co-sign approval must retain the exact complete response manifest")
        return self


class AnnotationReleaseInput(Contract):
    evidence_id: UUID
    card_id: UUID
    expected_card_revision: Revision
    expected_approval_binding_hash: Sha256
    request_id: UUID


class AnnotationReleaseRetry(AnnotationReleaseInput):
    job_id: UUID
    retry: Literal[True] = True


class AnnotationReleaseView(Contract):
    id: UUID
    annotation_id: UUID
    job_id: UUID
    asset_id: UUID
    rendition_id: UUID
    approval: AnnotationApprovalBinding
    renderer: AnnotationRendererIdentity
    rendering: AnnotationRendering
    created_at: AwareDatetime
    status: Literal["confirmed_release"] = "confirmed_release"
    decision_validity: Literal["current", "stale"]
    releasable: bool
    blocker_codes: list[AnnotationBlocker] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def release_preserves_reviewed_content(self):
        if self.renderer.profile != "annotation-release-v1":
            raise ValueError("release requires the fixed confirmed footer profile")
        if self.rendering.renderer != self.renderer:
            raise ValueError("release receipt must match the pinned renderer identity")
        if self.rendering.plan_sha256 != self.approval.candidate_plan_sha256:
            raise ValueError("release must retain the reviewed candidate plan hash")
        if self.annotation_id != self.approval.candidate_annotation_id:
            raise ValueError("release must name the approved immutable candidate")
        if (
            self.rendering.content_pixel_sha256 != self.approval.content_pixel_sha256
            or self.rendering.root_mapping_sha256 != self.approval.root_mapping_sha256
        ):
            raise ValueError("release cannot alter the reviewed content pixels or source mapping")
        if self.rendering.canvas.mapping.model_dump(exclude={"footer_height"}) != (
            self.approval.candidate_content_mapping.model_dump(exclude={"footer_height"})
        ):
            raise ValueError("release may change only the fixed footer, not candidate placement")
        if self.releasable and (self.decision_validity != "current" or self.blocker_codes):
            raise ValueError("stale or blocked decisions are historical, not releasable")
        return self


class AnnotationPreviewLink(Contract):
    annotation_id: UUID
    rendition_id: UUID
    kind: Literal["candidate", "release"]
    url: str = Field(min_length=1)
    expires_in: Literal[300] = 300
    image: PNGDescriptor
    mapping: ContentMapping


class AnnotationListQuery(Contract):
    cursor: str | None = Field(default=None, min_length=1, max_length=4096)
    limit: int = Field(default=50, strict=True, ge=1, le=100)
    card_id: UUID | None = None


class AnnotationListData(Contract):
    task_id: UUID
    next_cursor: str | None = Field(min_length=1, max_length=4096)
    returned: int = Field(strict=True, ge=0, le=100)
    has_more: bool

    @model_validator(mode="after")
    def continuation(self):
        if self.has_more != (self.next_cursor is not None):
            raise ValueError("continuation cursor must agree with remaining page entries")
        return self


class AnnotationCurrentStatus(Contract):
    validity: Literal["current", "stale"]
    active_selection: bool
    source_withdrawn: bool
    warning_codes: list[AnnotationBlocker] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def source_projection(self):
        if not self.active_selection and "source_inactive" not in self.warning_codes:
            raise ValueError("inactive sources require an explicit historical warning")
        if self.source_withdrawn and "source_withdrawn" not in self.warning_codes:
            raise ValueError("withdrawn sources require an explicit historical warning")
        if (not self.active_selection or self.source_withdrawn) and self.validity == "current":
            raise ValueError("inactive or withdrawn source bindings are stale")
        return self


class AnnotationShowData(Contract):
    candidate: AnnotationCandidateView
    current: AnnotationCurrentStatus


class AnnotationReleaseListData(AnnotationListData):
    annotation_id: UUID


class AnnotationRenderRequest(Contract):
    """Candidate input is source PNG. Release input is already marked candidate
    content with its footer removed: never crop or draw boxes a second time.
    The original plan is retained solely for hash/provenance validation on release.
    """

    protocol: Literal["annotation-render-v1"] = "annotation-render-v1"
    source: AnnotationSourceBinding
    plan: AnnotationPlan
    plan_sha256: Sha256
    renderer: AnnotationRendererIdentity
    content_mode: Literal["source_png", "marked_candidate_content"]
    input_content_sha256: Sha256
    provenance_sha256: Sha256
    approval: AnnotationApprovalBinding | None = None

    @model_validator(mode="after")
    def profile_gate(self):
        if self.plan_sha256 != self.plan.canonical_sha256():
            raise ValueError("renderer plan hash differs from canonical plan")
        if (self.renderer.profile == "annotation-release-v1") != (self.approval is not None):
            raise ValueError("only release rendering accepts an existing complete approval pin")
        release = self.renderer.profile == "annotation-release-v1"
        if release != (self.content_mode == "marked_candidate_content"):
            raise ValueError("release rendering must preserve already annotated content bytes")
        if not release and self.input_content_sha256 != self.source.source_png.sha256:
            raise ValueError("candidate render input must name the authorized source PNG")
        if self.approval is not None and self.plan_sha256 != self.approval.candidate_plan_sha256:
            raise ValueError("release provenance must retain the approved candidate plan")
        if len(self.model_dump_json().encode("utf-8")) > RENDERER_JSON_LIMIT:
            raise ValueError("renderer metadata exceeds the bounded JSON frame")
        return self


class AnnotationRenderer(Protocol):
    async def render(
        self, content: bytes, request: AnnotationRenderRequest
    ) -> tuple[bytes, AnnotationRendering]: ...


class AnnotationService(Protocol):
    async def preflight(
        self,
        db: "AsyncSession",
        actor: "Identity",
        task_id: UUID,
        input: AnnotationPreflightRequest,
    ) -> AnnotationPreflight: ...

    async def submit(
        self, db: "AsyncSession", actor: "Identity", task_id: UUID, input: AnnotationSubmit
    ) -> AnnotationJobAccepted: ...

    async def show(
        self, db: "AsyncSession", actor: "Identity", annotation_id: UUID
    ) -> AnnotationShowData: ...

    async def preview(
        self, db: "AsyncSession", actor: "Identity", annotation_id: UUID
    ) -> AnnotationPreviewLink: ...

    async def list_releases(
        self, db: "AsyncSession", actor: "Identity", annotation_id: UUID, query: AnnotationListQuery
    ) -> tuple[AnnotationReleaseListData, list[AnnotationReleaseView]]: ...

    async def release_preview(
        self, db: "AsyncSession", actor: "Identity", release_id: UUID
    ) -> AnnotationPreviewLink: ...

    async def release(
        self,
        db: "AsyncSession",
        actor: "Identity",
        annotation_id: UUID,
        input: AnnotationReleaseInput | AnnotationReleaseRetry,
    ) -> AnnotationJobAccepted: ...

    async def list(
        self, db: "AsyncSession", actor: "Identity", task_id: UUID, query: AnnotationListQuery
    ) -> tuple[AnnotationListData, list[AnnotationCandidateView]]: ...


# Existing human CardAction/CoSignConfirm remain the sole confirmation contracts.
# Payload names refer to Result.data/items; they do not register commands or scopes.
COMMAND_PAYLOADS = {
    "evidence stamp --dry-run": (AnnotationPreflight, None),
    "evidence stamp": (AnnotationJobAccepted, None),
    "evidence annotation list": (AnnotationListData, AnnotationCandidateView),
    "evidence annotation show": (AnnotationShowData, None),
    "evidence annotation preview": (AnnotationPreviewLink, None),
    "evidence annotation releases": (AnnotationReleaseListData, AnnotationReleaseView),
    "evidence annotation release preview": (AnnotationPreviewLink, None),
    "evidence annotation release retry": (AnnotationJobAccepted, None),
}
SHARED_SCHEMA_MODELS = (Result, Cost, CardAction, CoSignConfirm)
RESULT_CONTRACT_VERSION = CONTRACT_VERSION
