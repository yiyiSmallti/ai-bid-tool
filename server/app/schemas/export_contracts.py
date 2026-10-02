"""Approved human-only DOCX export contracts."""

from collections.abc import Hashable, Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import PurePath
from typing import Annotated, Literal
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.contracts import Contract, Cost

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ExportMode = Literal["final_section", "review_copy"]
SectionKind = Literal[
    "substantive",
    "commercial",
    "technical",
    "comply_only",
    "gaps",
    "evidence_appendix",
]
ColumnKind = Literal[
    "ordinal",
    "tender_clause",
    "source_location",
    "response",
    "deviation",
    "deviation_note",
    "evidence",
]

SECTION_ORDER = (
    "substantive",
    "commercial",
    "technical",
    "comply_only",
    "gaps",
    "evidence_appendix",
)
TABLE_SECTIONS = frozenset({"substantive", "commercial", "technical"})
COLUMN_KEYS = frozenset(
    {
        "ordinal",
        "tender_clause",
        "source_location",
        "response",
        "deviation",
        "deviation_note",
        "evidence",
    }
)
DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MAX_EXPORT_BYTES = 512 * 1024 * 1024


def _nonblank(value: str, field: str) -> str:
    if not value.strip():
        raise ValueError(f"{field} must contain non-whitespace characters")
    return value.strip()


def _unique(values: Sequence[Hashable], field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} must not contain duplicates")


class ExportColumn(Contract):
    key: ColumnKind
    width_percent: Decimal = Field(gt=0, le=100)


class ExportSectionBinding(Contract):
    section: SectionKind
    heading_style_id: str = Field(min_length=1, max_length=200)
    table_style_id: str = Field(min_length=1, max_length=200)
    columns: list[ExportColumn] = Field(default_factory=list, max_length=7)

    @field_validator("heading_style_id", "table_style_id")
    @classmethod
    def nonblank_style_id(cls, value: str, info) -> str:
        return _nonblank(value, info.field_name)

    @model_validator(mode="after")
    def section_columns(self):
        if self.section in TABLE_SECTIONS:
            keys = [column.key for column in self.columns]
            if len(keys) != 7 or set(keys) != COLUMN_KEYS:
                raise ValueError("table sections require each of the seven fixed columns once")
            if sum((column.width_percent for column in self.columns), Decimal()) != Decimal(100):
                raise ValueError("table column widths must total 100 percent")
        elif self.columns:
            raise ValueError("non-table sections do not accept columns")
        return self


class ExportBindingCreate(Contract):
    template_revision_id: UUID
    expected_template_sha256: Sha256
    sections: list[ExportSectionBinding] = Field(min_length=6, max_length=6)
    expected_static_content_hash: Sha256 | None = None
    dry_run: bool = False

    @model_validator(mode="after")
    def complete_binding(self):
        if tuple(section.section for section in self.sections) != SECTION_ORDER:
            raise ValueError("sections must contain the six fixed sections in contract order")
        if not self.dry_run and self.expected_static_content_hash is None:
            raise ValueError("binding creation requires expected_static_content_hash")
        return self


class ExportBindingView(Contract):
    id: UUID
    org_id: UUID
    template_revision_id: UUID
    template_sha256: Sha256
    binding_hash: Sha256
    static_content_hash: Sha256
    adapter_version: str = Field(min_length=1, max_length=100)
    sections: list[ExportSectionBinding] = Field(min_length=6, max_length=6)
    reviewed_by: UUID
    reviewed_at: datetime

    @field_validator("adapter_version")
    @classmethod
    def nonblank_adapter_version(cls, value: str) -> str:
        return _nonblank(value, "adapter_version")

    @field_validator("reviewed_at")
    @classmethod
    def aware_review_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reviewed_at must include a timezone")
        return value

    @model_validator(mode="after")
    def complete_binding(self):
        if tuple(section.section for section in self.sections) != SECTION_ORDER:
            raise ValueError("sections must contain the six fixed sections in contract order")
        return self


class ExportIssue(Contract):
    issue_id: Sha256
    code: str = Field(min_length=1, max_length=100)
    severity: Literal["block", "acknowledge"]
    requirement_ids: list[UUID] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_objects(self):
        _unique(self.requirement_ids, "requirement_ids")
        _unique(self.evidence_ids, "evidence_ids")
        return self


class ExportBindingAnchor(Contract):
    section: SectionKind
    paragraph: int = Field(ge=1)
    section_index: int = Field(ge=0)


class ExportBindingPreview(Contract):
    dry_run: Literal[True] = True
    template_revision_id: UUID
    template_sha256: Sha256
    static_content_hash: Sha256
    adapter_version: str = Field(min_length=1, max_length=100)
    anchors: list[ExportBindingAnchor] = Field(max_length=6)
    issues: list[ExportIssue]

    @field_validator("adapter_version")
    @classmethod
    def nonblank_adapter_version(cls, value: str) -> str:
        return _nonblank(value, "adapter_version")

    @model_validator(mode="after")
    def complete_anchor_set(self):
        sections = [anchor.section for anchor in self.anchors]
        _unique(sections, "anchors")
        expected_order = [section for section in SECTION_ORDER if section in sections]
        if sections != expected_order:
            raise ValueError("reported anchors must follow contract section order")
        _unique([issue.issue_id for issue in self.issues], "issues")
        return self


class ExportPrepare(Contract):
    draft_id: UUID
    task_template_id: UUID
    binding_id: UUID
    mode: ExportMode
    expected_input_hash: Sha256 | None = None
    acknowledged_issue_ids: list[Sha256] = Field(default_factory=list)
    dry_run: bool = False
    retry: bool = False

    @model_validator(mode="after")
    def valid_submission_mode(self):
        _unique(self.acknowledged_issue_ids, "acknowledged_issue_ids")
        if self.dry_run and (self.retry or self.acknowledged_issue_ids):
            raise ValueError("dry-run does not accept retry or acknowledged issues")
        if not self.dry_run and self.expected_input_hash is None:
            raise ValueError("prepare requires expected_input_hash outside dry-run")
        return self


class ExportPreview(Contract):
    dry_run: Literal[True] = True
    task_id: UUID
    draft_id: UUID
    mode: ExportMode
    input_hash: Sha256
    ready: bool
    requirement_count: int = Field(ge=0, le=2000)
    table_rows: dict[Literal["substantive", "commercial", "technical"], int]
    comply_only_count: int = Field(ge=0, le=2000)
    gap_count: int = Field(ge=0, le=2000)
    negative_count: int = Field(ge=0, le=2000)
    attachment_pages: int = Field(ge=0, le=300)
    issues: list[ExportIssue]
    estimated_output_bytes: int | None = Field(default=None, ge=0, le=MAX_EXPORT_BYTES)
    estimated_duration_ms: int | None = Field(default=None, ge=0)
    estimated_cost: Cost

    @model_validator(mode="after")
    def coherent_counts_and_readiness(self):
        if set(self.table_rows) != TABLE_SECTIONS or any(
            value < 0 for value in self.table_rows.values()
        ):
            raise ValueError("table_rows must contain the three table counts")
        accounted = sum(self.table_rows.values()) + self.comply_only_count + self.gap_count
        if accounted != self.requirement_count:
            raise ValueError("export counts must account for every requirement exactly once")
        if self.negative_count > sum(self.table_rows.values()):
            raise ValueError("negative_count cannot exceed table rows")
        if self.ready == any(issue.severity == "block" for issue in self.issues):
            raise ValueError("ready must be false exactly when a blocking issue exists")
        if (
            self.estimated_cost.llm_tokens != 0
            or self.estimated_cost.ocr_pages != 0
            or self.estimated_cost.usd != 0
        ):
            raise ValueError("export does not incur model or OCR cost")
        _unique([issue.issue_id for issue in self.issues], "issues")
        return self


class ExportRunView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    draft_id: UUID
    render_job_id: UUID
    mode: ExportMode
    input_hash: Sha256
    state: Literal[
        "queued",
        "rendering",
        "awaiting_release",
        "released",
        "failed",
        "cancelled",
        "invalidated",
    ]
    candidate_sha256: Sha256 | None
    export_id: UUID | None
    issues: list[ExportIssue]

    @model_validator(mode="after")
    def coherent_state(self):
        if self.state == "awaiting_release" and (
            self.candidate_sha256 is None or self.export_id is not None
        ):
            raise ValueError("an awaiting-release run requires only a candidate hash")
        if self.state == "released" and (self.candidate_sha256 is None or self.export_id is None):
            raise ValueError("a released run requires its candidate hash and export id")
        if self.state == "invalidated" and self.candidate_sha256 is not None:
            raise ValueError("an invalidated run cannot expose a render candidate")
        if self.state in {"queued", "rendering", "failed", "cancelled"} and (
            self.candidate_sha256 is not None or self.export_id is not None
        ):
            raise ValueError("an unfinished run cannot expose publication identifiers")
        _unique([issue.issue_id for issue in self.issues], "issues")
        return self


class ExportRelease(Contract):
    expected_input_hash: Sha256
    expected_candidate_sha256: Sha256


class ExportFile(Contract):
    name: str = Field(min_length=1, max_length=200)
    sha256: Sha256
    size_bytes: int = Field(gt=0, le=MAX_EXPORT_BYTES)
    media_type: Literal[
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ] = DOCX_MEDIA_TYPE

    @field_validator("name")
    @classmethod
    def safe_docx_name(cls, value: str) -> str:
        if (
            value != value.strip()
            or PurePath(value).name != value
            or not value.lower().endswith(".docx")
            or any(char in value for char in "/\\")
            or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("a safe DOCX file name is required")
        return value


class ExportView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    run_id: UUID
    draft_id: UUID
    task_template_id: UUID
    template_revision_id: UUID
    binding_id: UUID
    mode: ExportMode
    completion: Literal["complete", "partial"]
    validity: Literal["current", "stale"]
    input_hash: Sha256
    manifest_hash: Sha256
    file: ExportFile
    released_by: UUID
    released_at: datetime
    issues: list[ExportIssue]
    invalidated_requirement_ids: list[UUID]

    @field_validator("released_at")
    @classmethod
    def aware_release_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("released_at must include a timezone")
        return value

    @model_validator(mode="after")
    def coherent_export(self):
        expected = "partial" if self.mode == "review_copy" else "complete"
        if self.completion != expected:
            raise ValueError("completion must match the requested export mode")
        _unique([issue.issue_id for issue in self.issues], "issues")
        _unique(self.invalidated_requirement_ids, "invalidated_requirement_ids")
        return self


class ExportDownloadLink(Contract):
    export_id: UUID
    file: ExportFile
    url: str = Field(min_length=1, max_length=8192)
    expires_in: Literal[300] = 300

    @model_validator(mode="after")
    def same_service_download_route(self):
        parsed = urlsplit(self.url)
        try:
            query = parse_qs(parsed.query, strict_parsing=True)
        except ValueError as exc:
            raise ValueError("download URL has an invalid query") from exc
        expected_path = f"/exports/{self.export_id}/download"
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or parsed.path != expected_path
            or set(query) != {"signature"}
            or len(query["signature"]) != 1
            or not 0 < len(query["signature"][0]) < 8192
        ):
            raise ValueError("download URL must be the signed route for this export")
        return self


class ExportDownloadReceipt(Contract):
    export_id: UUID
    output_path: str = Field(min_length=1)
    file: ExportFile

    @field_validator("output_path")
    @classmethod
    def nonblank_output_path(cls, value: str) -> str:
        return _nonblank(value, "output_path")


class ExportDownloadResult(ExportDownloadReceipt):
    """CLI receipt fields needed to preserve review-copy partial semantics."""

    mode: ExportMode
    completion: Literal["complete", "partial"]
    validity: Literal["current", "stale"]
    issues: list[ExportIssue]

    @model_validator(mode="after")
    def coherent_completion(self):
        expected = "partial" if self.mode == "review_copy" else "complete"
        if self.completion != expected:
            raise ValueError("completion must match the downloaded export mode")
        _unique([issue.issue_id for issue in self.issues], "issues")
        return self
