from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.contracts import Contract, Cost

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Purpose = Literal["prototype_offline", "vendor_capture"]
StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
StrictPositiveInt = Annotated[int, Field(strict=True, gt=0)]
StrictBool = Annotated[bool, Field(strict=True)]
PdfPage = Annotated[int, Field(strict=True, ge=1)]

PNG_KINDS = {"prototype_png", "capture_png", "pdf_page_png"}
MAX_PNG_SIDE = 8192
MAX_PNG_PIXELS = 20_000_000
MAX_ARTIFACT_BYTES = {
    "prototype_png": 40 * 1024 * 1024,
    "capture_png": 40 * 1024 * 1024,
    "pdf_page_png": 40 * 1024 * 1024,
    "rendered_html": 4 * 1024 * 1024,
    "source_pdf": 32 * 1024 * 1024,
    "request_manifest": 4 * 1024 * 1024,
    "capture_archive": 64 * 1024 * 1024,
    "provenance_manifest": 4 * 1024 * 1024,
}


class Viewport(Contract):
    width: Annotated[int, Field(strict=True, ge=320, le=4096)] = 1440
    height: Annotated[int, Field(strict=True, ge=240, le=4096)] = 900
    device_scale_factor: Literal[1] = 1

    @field_validator("device_scale_factor", mode="before")
    @classmethod
    def device_scale_factor_is_an_integer(cls, value):
        if type(value) is not int:
            raise ValueError("device_scale_factor must be an integer")
        return value


class PrototypeSpec(Contract):
    purpose: Literal["prototype_offline"]
    extraction_job_id: UUID
    task_feature_id: UUID
    expected_feature_revision_id: UUID
    html_sha256: Sha256
    html_size_bytes: Annotated[int, Field(strict=True, gt=0, le=4 * 1024 * 1024)]
    viewport: Viewport = Field(default_factory=Viewport)


class VendorSpec(Contract):
    purpose: Literal["vendor_capture"]
    extraction_job_id: UUID
    task_resource_id: UUID
    expected_product_revision_id: UUID
    source_field: Literal["official_url", "whitepaper_url"]
    expected_source_url_sha256: Sha256
    format: Literal["web", "pdf"]
    pdf_pages: list[PdfPage] = Field(default_factory=list, max_length=10)
    viewport: Viewport = Field(default_factory=Viewport)
    archive: Literal["manifest", "bundle"] = "manifest"
    capture_key: UUID

    @model_validator(mode="after")
    def pages_match_format(self):
        if self.format == "pdf":
            if not self.pdf_pages:
                raise ValueError("pdf capture requires at least one page")
            if self.pdf_pages != sorted(set(self.pdf_pages)):
                raise ValueError("pdf pages must be ordered and unique")
        elif self.pdf_pages:
            raise ValueError("web capture does not accept pdf pages")
        return self


RunSpec = Annotated[PrototypeSpec | VendorSpec, Field(discriminator="purpose")]


class SandboxSubmit(Contract):
    spec: RunSpec
    expected_request_hash: Sha256 | None = None
    dry_run: StrictBool = False
    retry: StrictBool = False

    @model_validator(mode="after")
    def preflight_and_submit_fields_match(self):
        if self.dry_run and self.retry:
            raise ValueError("dry run cannot retry a previous run")
        if not self.dry_run and self.expected_request_hash is None:
            raise ValueError("submission requires the preflight request hash")
        return self


class SandboxIssue(Contract):
    code: str = Field(pattern=r"^[a-z0-9_]{1,100}$")
    severity: Literal["block", "warning"]
    object_ids: list[UUID] = Field(default_factory=list)


class SandboxMetrics(Contract):
    wall_ms: StrictNonNegativeInt
    cpu_ms: StrictNonNegativeInt
    peak_memory_bytes: StrictNonNegativeInt
    input_bytes: StrictNonNegativeInt
    network_bytes: StrictNonNegativeInt
    output_bytes: StrictNonNegativeInt
    request_count: StrictNonNegativeInt


class SandboxPreview(Contract):
    dry_run: Literal[True] = True
    request_hash: Sha256
    purpose: Purpose
    profile: str = Field(min_length=1)
    policy_revision: str = Field(min_length=1)
    ready: StrictBool
    issues: list[SandboxIssue]
    estimated_cost: Cost
    estimate_basis: Literal["no_vendor_call", "upper_bound", "unknown"]
    reserved_charge: Decimal | None = Field(default=None, ge=0)
    charge_currency: str | None = Field(default=None, min_length=1)
    estimated_duration_ms: StrictNonNegativeInt | None = None

    @field_validator("dry_run", mode="before")
    @classmethod
    def dry_run_is_a_boolean(cls, value):
        if type(value) is not bool:
            raise ValueError("dry_run must be a boolean")
        return value

    @field_validator("profile", "policy_revision", "charge_currency")
    @classmethod
    def strings_are_not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("string value cannot be blank")
        return value


ArtifactKind = Literal[
    "prototype_png",
    "capture_png",
    "pdf_page_png",
    "rendered_html",
    "source_pdf",
    "request_manifest",
    "capture_archive",
    "provenance_manifest",
]


class SandboxArtifactView(Contract):
    id: UUID
    run_id: UUID
    attempt_id: UUID
    kind: ArtifactKind
    sha256: Sha256
    size_bytes: StrictPositiveInt
    media_type: str = Field(min_length=1)
    width: Annotated[int, Field(strict=True, gt=0)] | None = None
    height: Annotated[int, Field(strict=True, gt=0)] | None = None
    page: PdfPage | None = None
    parent_artifact_id: UUID | None = None
    provenance_manifest_hash: Sha256

    @field_validator("media_type")
    @classmethod
    def media_type_is_not_blank(cls, value):
        if not value.strip():
            raise ValueError("media_type cannot be blank")
        return value

    @model_validator(mode="after")
    def dimensions_and_page_match_kind(self):
        if self.size_bytes > MAX_ARTIFACT_BYTES[self.kind]:
            raise ValueError("artifact exceeds its kind limit")
        if self.kind in PNG_KINDS:
            if self.width is None or self.height is None:
                raise ValueError("PNG artifacts require dimensions")
            if self.width > MAX_PNG_SIDE or self.height > MAX_PNG_SIDE:
                raise ValueError("PNG dimensions exceed the side limit")
            if self.width * self.height > MAX_PNG_PIXELS:
                raise ValueError("PNG dimensions exceed the pixel limit")
            if self.media_type != "image/png":
                raise ValueError("PNG artifacts require image/png")
        elif self.width is not None or self.height is not None:
            raise ValueError("non-PNG artifacts cannot include dimensions")
        if self.kind == "pdf_page_png":
            if self.page is None:
                raise ValueError("PDF page artifacts require a page")
        elif self.page is not None:
            raise ValueError("only PDF page artifacts can include a page")
        return self


class SandboxProvenance(Contract):
    origin: Literal["prototype", "public_web_capture", "public_pdf_capture"]
    generating_job_id: UUID | None = None
    generating_provider: str | None = Field(default=None, min_length=1)
    generating_model: str | None = Field(default=None, min_length=1)
    html_sha256: Sha256 | None = None
    render_manifest_sha256: Sha256

    @field_validator("generating_provider", "generating_model")
    @classmethod
    def optional_strings_are_not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("string value cannot be blank")
        return value

    @model_validator(mode="after")
    def prototype_has_html_hash(self):
        if self.origin == "prototype" and self.html_sha256 is None:
            raise ValueError("prototype provenance requires the HTML hash")
        if self.origin != "prototype" and self.html_sha256 is not None:
            raise ValueError("capture provenance cannot include an HTML hash")
        return self


class SandboxRunView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    job_id: UUID
    purpose: Purpose
    request_hash: Sha256
    profile: str = Field(min_length=1)
    policy_revision: str = Field(min_length=1)
    selection_active: StrictBool
    state: Literal[
        "queued",
        "running",
        "validating",
        "succeeded",
        "failed",
        "cancelled",
        "cleanup_pending",
    ]
    attempt_id: UUID | None
    cleanup_state: Literal["not_started", "pending", "complete", "failed"]
    artifacts: list[SandboxArtifactView]
    metrics: SandboxMetrics | None
    issues: list[SandboxIssue]
    usage_ids: list[UUID]
    charge: Decimal | None = Field(default=None, ge=0)
    charge_currency: str | None = Field(default=None, min_length=1)

    @field_validator("profile", "policy_revision", "charge_currency")
    @classmethod
    def strings_are_not_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("string value cannot be blank")
        return value

    @model_validator(mode="after")
    def succeeded_run_is_clean(self):
        if self.state == "succeeded":
            if self.cleanup_state != "complete":
                raise ValueError("succeeded run requires complete cleanup")
            if any(issue.severity == "block" for issue in self.issues):
                raise ValueError("succeeded run cannot have blocking issues")
            if self.attempt_id is None or self.metrics is None:
                raise ValueError("succeeded run requires attempt metrics")
            kinds = {artifact.kind for artifact in self.artifacts}
            if self.purpose == "prototype_offline":
                required = {"prototype_png", "rendered_html", "provenance_manifest"}
                if not required <= kinds:
                    raise ValueError("succeeded prototype run is missing required artifacts")
            else:
                required = {"request_manifest", "provenance_manifest"}
                if not required <= kinds:
                    raise ValueError("succeeded capture run is missing required manifests")
                if "capture_png" in kinds:
                    if "rendered_html" not in kinds:
                        raise ValueError("web capture is missing rendered HTML")
                elif not {"source_pdf", "pdf_page_png"} <= kinds:
                    raise ValueError("capture is missing web or PDF artifacts")
        return self


class SandboxDownloadLink(Contract):
    artifact: SandboxArtifactView
    url: str = Field(min_length=1)
    expires_in: Literal[300] = 300

    @field_validator("expires_in", mode="before")
    @classmethod
    def expiry_is_an_integer(cls, value):
        if type(value) is not int:
            raise ValueError("expires_in must be an integer")
        return value

    @field_validator("url")
    @classmethod
    def url_is_not_blank(cls, value):
        if not value.strip():
            raise ValueError("url cannot be blank")
        return value


class SandboxDownloadReceipt(Contract):
    artifact: SandboxArtifactView
    output_path: str = Field(min_length=1)

    @field_validator("output_path")
    @classmethod
    def output_path_is_not_blank(cls, value):
        if not value.strip():
            raise ValueError("output_path cannot be blank")
        return value
