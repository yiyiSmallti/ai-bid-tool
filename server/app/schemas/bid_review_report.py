"""Bounded console reports and human-only immutable Word artifacts."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.schemas.bid_review import FILE_BYTE_LIMIT, NonBlank
from app.schemas.budget_contracts import BudgetPreflightData
from app.schemas.contracts import Contract
from app.schemas.screenshot_contracts import Sha256

ReportSectionKey = Literal[
    "overall",
    "basic_information",
    "compliance",
    "signatures",
    "risks",
    "scores",
    "evidence",
    "remediation",
    "methodology",
]
SECTIONS = (
    ("overall", "一、总体结论"),
    ("basic_information", "二、基本信息"),
    ("compliance", "三、废标判定"),
    ("signatures", "签章校验"),
    ("risks", "四、高风险缺陷"),
    ("scores", "五、得分预估"),
    ("evidence", "六、证据核对"),
    ("remediation", "七、补救清单"),
    ("methodology", "八、检验说明"),
)
ADVISORY = "评标委员会决定最终评审结果；本报告仅供辅助审查。"


class BidReportRenderRequest(Contract):
    request_id: UUID
    report_id: UUID
    dry_run: bool = False
    retry: bool = False
    expected_input_hash: Sha256 | None = None
    preflight_token: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)
    expected_decisions_snapshot_sha256: Sha256

    @model_validator(mode="after")
    def explicit_submit(self):
        if not self.dry_run and (not self.expected_input_hash or not self.preflight_token):
            raise ValueError("report rendering requires the preview hash and receipt")
        if self.dry_run and (self.retry or self.expected_input_hash or self.preflight_token):
            raise ValueError("preview cannot carry submission fields")
        return self


class BidReportRenderPreview(Contract):
    dry_run: Literal[True] = True
    report_id: UUID
    input_hash: Sha256
    report_input_hash: Sha256
    decisions_snapshot_sha256: Sha256
    renderer_identity: NonBlank
    budget: BudgetPreflightData
    expires_at: AwareDatetime
    preflight_token: str = Field(min_length=1, max_length=4096, repr=False)
    formats: tuple[Literal["docx"], Literal["console"]] = ("docx", "console")

    @model_validator(mode="after")
    def local_only(self):
        if self.budget.input_hash != self.input_hash or self.budget.planned_calls != 0:
            raise ValueError("report rendering has zero external calls")
        if self.budget.next_call is not None or self.budget.estimate.task_amount != 0:
            raise ValueError("report rendering has zero paid call liability")
        if (self.expires_at - self.budget.as_of).total_seconds() != 900:
            raise ValueError("report receipt lasts fifteen minutes")
        return self


class BidReviewReportArtifact(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    report_id: UUID
    snapshot_id: UUID
    format: Literal["docx", "console"]
    sha256: Sha256
    size_bytes: int = Field(strict=True, gt=0, le=FILE_BYTE_LIMIT)
    report_input_hash: Sha256
    decisions_snapshot_sha256: Sha256
    renderer_identity: NonBlank
    created_at: AwareDatetime
    human_only: Literal[True] = True
    advisory_only: Literal[True] = True


class BidReportDownloadLink(Contract):
    url: str
    expires_in: int = Field(gt=0, le=300)
    artifact: BidReviewReportArtifact


class BidReportDownloadReceipt(Contract):
    artifact_id: UUID
    output_path: str
    artifact: BidReviewReportArtifact


class BidReportSectionData(Contract):
    review_id: UUID
    task_id: UUID
    snapshot_id: UUID | None
    input_hash: Sha256
    report_input_hash: Sha256
    decisions_snapshot_sha256: Sha256
    current_decisions_snapshot_sha256: Sha256
    renderer_identity: NonBlank
    completion: Literal["complete", "partial"]
    advisory_statement: Literal["评标委员会决定最终评审结果；本报告仅供辅助审查。"] = ADVISORY
    sections: list[dict[str, str]]
    section: ReportSectionKey
    projection: Literal["protected", "cleared", "safe"]
    next_cursor: str | None = None
    artifacts: list[BidReviewReportArtifact] = Field(default_factory=list, max_length=2)
