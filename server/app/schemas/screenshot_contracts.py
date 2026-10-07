"""Typed screenshot provenance, local privacy receipts and human decision inputs."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.contracts import Contract as BaseContract
from app.schemas.contracts import Cost
from app.schemas.evidence_source_contracts import EvidenceSourceArchive


class Contract(BaseContract):
    @field_validator("*", mode="after")
    @classmethod
    def clean(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if not value:
                raise ValueError("nonblank string required")
        if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timezone required")
        if isinstance(value, datetime):
            value = value.astimezone(UTC)
        return value


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ImageKind = Literal["screenshot", "diagram", "prototype", "certificate_page", "vendor_page"]
Environment = Literal["production", "test", "development", "prototype", "unknown"]
AnalysisPurpose = Literal["match_requirements", "propose_regions", "read_text"]


class PixelRect(Contract):
    x: int = Field(strict=True, ge=0, le=8192)
    y: int = Field(strict=True, ge=0, le=8192)
    width: int = Field(strict=True, ge=1, le=8192)
    height: int = Field(strict=True, ge=1, le=8192)

    def within(self, width: int, height: int) -> bool:
        return self.x + self.width <= width and self.y + self.height <= height


class ImagePlan(Contract):
    redact: list[PixelRect] = Field(default_factory=list, max_length=200)
    crop: PixelRect | None = None
    boxes: list[PixelRect] = Field(default_factory=list, max_length=20)


class UploadSource(Contract):
    kind: Literal["upload"]
    task_feature_id: UUID
    image_kind: Literal["screenshot", "diagram", "prototype"]
    source_label: str = Field(min_length=1, max_length=200)
    software_version: str = Field(min_length=1, max_length=200)
    environment: Environment
    captured_at: datetime | None = None
    prototype_run_id: UUID | None = None

    @model_validator(mode="after")
    def prototype_binding(self):
        if (self.image_kind == "prototype") != (self.environment == "prototype"):
            raise ValueError("prototype kind and environment must agree")
        if (self.image_kind == "prototype") != (self.prototype_run_id is not None):
            raise ValueError("prototype generation record required")
        return self


class BrowserSource(Contract):
    kind: Literal["local_browser"]
    task_feature_id: UUID
    image_kind: Literal["screenshot", "prototype"]
    software_version: str = Field(min_length=1, max_length=200)
    environment: Environment
    capture_id: UUID
    captured_at: datetime
    system_origin: str = Field(min_length=1, max_length=300)
    route_label: str = Field(min_length=1, max_length=200)
    browser_version: str = Field(min_length=1, max_length=100)
    tool_version: str = Field(min_length=1, max_length=100)
    viewport_width: int = Field(strict=True, ge=1, le=8192)
    viewport_height: int = Field(strict=True, ge=1, le=8192)
    device_scale_factor: float = Field(gt=0, le=4)
    capture_mode: Literal["viewport", "element"]
    prototype_run_id: UUID | None = None


class CertificateSource(Contract):
    kind: Literal["certificate_page"]
    evidence_source_id: UUID


class ArchiveDescriptor(Contract):
    sha256: Sha256
    size_bytes: int = Field(gt=0)
    media_type: Literal["application/pdf", "application/zip", "text/html"]


class VendorSource(Contract):
    """A page image published by a succeeded vendor_capture sandbox run.

    The server resolves the product binding, URLs, capture time and archive from the run;
    the client only names the PNG artifact it prepared.
    """

    kind: Literal["vendor_web", "vendor_pdf"]
    sandbox_artifact_id: UUID


class PrototypeSource(Contract):
    kind: Literal["prototype_render"]
    prototype_run_id: UUID


ScreenshotSource = Annotated[
    UploadSource | BrowserSource | CertificateSource | VendorSource | PrototypeSource,
    Field(discriminator="kind"),
]


class ScreenshotPrepareInput(Contract):
    source: Annotated[
        UploadSource | CertificateSource | VendorSource | PrototypeSource,
        Field(discriminator="kind"),
    ]
    plan: ImagePlan


class ScreenshotCaptureInput(Contract):
    task_feature_id: UUID
    image_kind: Literal["screenshot", "prototype"]
    software_version: str = Field(min_length=1, max_length=200)
    environment: Environment
    route_label: str = Field(min_length=1, max_length=200)
    viewport_width: int = Field(strict=True, ge=1, le=8192)
    viewport_height: int = Field(strict=True, ge=1, le=8192)
    device_scale_factor: float = Field(gt=0, le=4)
    capture_mode: Literal["viewport", "element"]
    prototype_run_id: UUID | None = None
    plan: ImagePlan


class VendorSearchInput(Contract):
    extraction_job_id: UUID
    task_resource_id: UUID
    expected_input_hash: Sha256 | None = None
    dry_run: bool = False
    retry: bool = False

    @model_validator(mode="after")
    def preflight(self):
        if self.dry_run and self.retry:
            raise ValueError("dry-run cannot retry")
        if not self.dry_run and self.expected_input_hash is None:
            raise ValueError("preflight input hash required")
        return self


class VendorSearchPreview(Contract):
    dry_run: Literal[True] = True
    input_hash: Sha256
    queries: list[str]
    search_identity: str | None
    admission_blocker: str | None


class VendorSearchCandidateView(Contract):
    id: UUID
    ref: str
    url: str
    title: str
    host: str
    engines: list[str]
    pdf: bool
    known_vendor_domain: bool


class VendorSearchView(Contract):
    id: UUID
    task_id: UUID
    extraction_job_id: UUID
    task_resource_id: UUID
    product_revision_id: UUID
    job_id: UUID
    input_hash: Sha256
    queries: list[str]
    unresponsive_engines: list[str]
    candidates: list[VendorSearchCandidateView]


class VendorSearchAdopt(Contract):
    field: Literal["official_url", "whitepaper_url"]
    expected_product_revision: int = Field(strict=True, ge=1)


class PrototypeGenerateInput(Contract):
    extraction_job_id: UUID
    requirement_id: UUID
    task_feature_id: UUID
    expected_input_hash: Sha256 | None = None
    reasoning: str | None = None
    dry_run: bool = False
    retry: bool = False


class PrototypeGenerationView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    generation_job_id: UUID
    requirement_id: UUID
    task_feature_id: UUID
    origin: Literal["prototype"] = "prototype"
    provider: str
    model: str
    catalog_identity: str
    input_hash: Sha256
    html: ArchiveDescriptor
    html_sha256: Sha256
    sandbox_receipt_id: str
    sandbox_receipt_sha256: Sha256
    source_image_sha256: Sha256


class PNGDescriptor(Contract):
    sha256: Sha256
    size_bytes: int = Field(gt=0, le=40 * 1024 * 1024)
    width_px: int = Field(strict=True, ge=1, le=8192)
    height_px: int = Field(strict=True, ge=1, le=8192)
    media_type: Literal["image/png"] = "image/png"


class ContentMapping(Contract):
    crop: PixelRect
    content_offset_x: int = Field(strict=True, ge=0)
    content_offset_y: int = Field(strict=True, ge=0)
    content_width: int = Field(strict=True, ge=1)
    content_height: int = Field(strict=True, ge=1)
    footer_height: int = Field(strict=True, ge=0)


class PreparedScreenshot(Contract):
    source: ScreenshotSource
    source_sha256: Sha256
    source_width: int = Field(strict=True, ge=1, le=8192)
    source_height: int = Field(strict=True, ge=1, le=8192)
    plan: ImagePlan
    plan_sha256: Sha256
    preparation_profile: Literal["screenshot-privacy-v1"]
    prepared_at: datetime
    image: PNGDescriptor
    mapping: ContentMapping


class ScreenshotIngest(Contract):
    extraction_job_id: UUID
    prepared: PreparedScreenshot
    reviewed_upload_sha256: Sha256
    reviewed_archive_sha256: Sha256 | None = None
    idempotency_key: UUID

    @model_validator(mode="after")
    def hash_review(self):
        if self.reviewed_upload_sha256 != self.prepared.image.sha256:
            raise ValueError("review must name the received PNG hash")
        vendor = self.prepared.source.kind in {"vendor_web", "vendor_pdf"}
        if vendor != (self.reviewed_archive_sha256 is not None):
            raise ValueError("archive review applies only to vendor sources")
        return self


class ScreenshotAnnotate(Contract):
    parent_rendition_id: UUID
    expected_image_sha256: Sha256
    plan: ImagePlan
    dry_run: bool = False
    retry: bool = False


class ScreenshotWithdraw(Contract):
    reason: str = Field(min_length=1, max_length=2000)


class VendorArchiveView(Contract):
    id: UUID
    sandbox_run_id: UUID
    task_resource_id: UUID
    product_revision_id: UUID
    format: Literal["web", "pdf"]
    source_field: Literal["official_url", "whitepaper_url"]
    source_url_sha256: Sha256
    final_url_sha256: Sha256
    final_origin: str = Field(min_length=1, max_length=300)
    title: str | None = Field(default=None, max_length=500)
    captured_at: datetime
    content_sha256: Sha256
    archive: ArchiveDescriptor
    policy_revision: str = Field(min_length=1, max_length=100)
    incomplete: bool
    failed_request_count: int = Field(strict=True, ge=0)


class ScreenshotView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    source: ScreenshotSource
    image_kind: ImageKind
    origin: Literal["user", "browser", "certificate", "vendor", "prototype"]
    selection_id: UUID
    resource_revision_id: UUID
    source_sha256: Sha256
    source_hash_assurance: Literal["client_declared", "server_verified"]
    source_archive: EvidenceSourceArchive | None
    vendor_archive_id: UUID | None
    vendor_archive: VendorArchiveView | None
    prototype_generation: PrototypeGenerationView | None
    received_at: datetime
    active_selection: bool
    withdrawn: bool
    status: Literal["unconfirmed_material"] = "unconfirmed_material"
    confirmed_by: Literal[None] = None
    eligible_for_draft_export: Literal[False] = False
    warning_codes: list[str]


class RenditionView(Contract):
    id: UUID
    asset_id: UUID
    parent_rendition_id: UUID | None
    image: PNGDescriptor
    plan: ImagePlan
    plan_sha256: Sha256
    profile: Literal["screenshot-markup-v1", "prototype-clean-v1"]
    mapping: ContentMapping
    privacy_review_id: UUID
    privacy_basis: Literal["human_upload_review", "safe_derivation"]
    prototype_watermark: Literal[False] = False


class AnnotationEvidencePlan(Contract):
    crop: PixelRect | None = None
    boxes: list[PixelRect] = Field(default_factory=list, max_length=20)


class AnnotationEvidenceRendition(Contract):
    """Canonical candidate metadata; release bytes have their own gated view."""

    id: UUID
    asset_id: UUID
    parent_rendition_id: Literal[None] = None
    image: PNGDescriptor
    plan: AnnotationEvidencePlan
    plan_sha256: Sha256
    profile: Literal["annotation-candidate-v1"]
    mapping: ContentMapping
    privacy_review_id: UUID
    privacy_basis: Literal["human_archive_review"] = "human_archive_review"
    prototype_watermark: Literal[False] = False


class ImageEvidenceInput(Contract):
    kind: Literal["image_region"]
    asset_id: UUID
    rendition_id: UUID
    expected_image_sha256: Sha256
    region: PixelRect
    claim_scope: Literal[
        "functional_observation", "design_explanation", "document_excerpt", "hardware_documentation"
    ]
    visual_observation: str = Field(min_length=1, max_length=4000)


class AnalysisImageInput(Contract):
    rendition_id: UUID
    expected_image_sha256: Sha256


class ScreenshotAnalyzeInput(Contract):
    extraction_job_id: UUID
    requirement_ids: list[UUID] = Field(min_length=1, max_length=50)
    images: list[AnalysisImageInput] = Field(min_length=1, max_length=20)
    purposes: list[AnalysisPurpose] = Field(min_length=1, max_length=3)
    expected_input_hash: Sha256 | None = None
    reasoning: str | None = None
    dry_run: bool = False
    retry: bool = False

    @model_validator(mode="after")
    def explicit_inputs(self):
        for values in (self.requirement_ids, [v.rendition_id for v in self.images], self.purposes):
            if len(set(values)) != len(values):
                raise ValueError("duplicate input")
        if self.dry_run and self.retry:
            raise ValueError("dry-run cannot retry")
        if not self.dry_run and self.expected_input_hash is None:
            raise ValueError("preflight input hash required")
        return self


class VisionProposal(Contract):
    image_ref: str = Field(min_length=1, max_length=80)
    requirement_ref: str | None = Field(default=None, max_length=80)
    purpose: AnalysisPurpose
    region: PixelRect | None = None
    suggested_text: str | None = Field(default=None, max_length=4000)
    confidence: float | None = Field(default=None, ge=0, le=1)


class ScreenshotSuggestionView(Contract):
    id: UUID
    analysis_run_id: UUID
    rendition_id: UUID
    image_sha256: Sha256
    requirement_id: UUID | None
    proposal: VisionProposal
    status: Literal["unreviewed"] = "unreviewed"
    confirmed_by: Literal[None] = None


class VendorJobPreview(Contract):
    dry_run: Literal[True] = True
    input_hash: Sha256
    outbound_image_hashes: list[Sha256]
    outbound_text_hashes: list[Sha256]
    catalog_identity: str
    price_revision: str
    input_image_count: int = Field(ge=0)
    planned_calls: int = Field(ge=0)
    estimated_cost: Cost
    estimated_charge: Decimal | None = Field(default=None, ge=0)
    billing_currency: str
    cost_basis: Literal["first_pass_upper_bound", "unknown"]
    admission_blocker: str | None


class PrototypeDecisionTarget(Contract):
    evidence_id: UUID
    card_revision_id: UUID
    rendition_id: UUID
    image_sha256: Sha256
    html_sha256: Sha256
    task_feature_id: UUID
    expected_previous_decision_id: UUID | None = None


class PrototypeDecisionPreviewInput(Contract):
    evidence_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=500)
    extraction_job_id: UUID
    module_label: str = Field(min_length=1, max_length=200)
    task_feature_ids: list[UUID] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_module_targets(self):
        for values in (self.task_feature_ids, self.evidence_ids):
            if values is not None and len(set(values)) != len(values):
                raise ValueError("duplicate module target")
        return self


class PrototypeDecisionPreview(Contract):
    input_hash: Sha256
    module_label: str
    targets: list[PrototypeDecisionTarget]


class PrototypeDecisionItem(PrototypeDecisionTarget):
    decision: Literal["keep", "replace"]
    keep_basis: Literal["already_delivered", "will_deliver"] | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def decision_basis(self):
        if (self.decision == "keep") != (self.keep_basis is not None):
            raise ValueError("keep requires an explicit basis; replace forbids it")
        if self.expected_previous_decision_id and not self.reason:
            raise ValueError("changing a decision requires a reason")
        return self


class PrototypeDecisionBatch(Contract):
    evidence_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=500)
    extraction_job_id: UUID
    module_label: str = Field(min_length=1, max_length=200)
    task_feature_ids: list[UUID] = Field(min_length=1, max_length=200)
    expected_input_hash: Sha256
    items: list[PrototypeDecisionItem] = Field(min_length=1, max_length=500)
    idempotency_key: UUID

    @model_validator(mode="after")
    def no_duplicates(self):
        for values in (
            self.task_feature_ids,
            [v.evidence_id for v in self.items],
            self.evidence_ids or [],
        ):
            if len(set(values)) != len(values):
                raise ValueError("duplicate target")
        return self


class PrototypeDecisionView(Contract):
    id: UUID
    batch_id: UUID
    target: PrototypeDecisionTarget
    decision: Literal["keep", "replace"]
    keep_basis: Literal["already_delivered", "will_deliver"] | None
    decided_by: UUID
    decided_at: datetime
    validity: Literal["current", "stale", "superseded"]


class ScreenshotPreviewLink(Contract):
    url: str
    expires_in: Literal[300] = 300
    rendition_id: UUID
    image: PNGDescriptor


class ScreenshotJobResult(Contract):
    job_id: UUID
    asset_id: UUID
    rendition: RenditionView
    duplicate: bool


class ScreenshotDryRun(Contract):
    dry_run: Literal[True] = True
    parent_rendition_id: UUID
    input_hash: Sha256
    estimated_cost: Cost
    estimated_charge: Decimal
    billing_currency: str
    cost_basis: Literal["known"] = "known"
    estimated_duration_ms: int | None = Field(default=None, ge=0)
