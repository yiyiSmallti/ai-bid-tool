"""Read-only provider readiness and bounded call planning contracts."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from app.schemas.contracts import Contract, Cost

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Capability = Literal["llm", "vision", "ocr", "search", "embedding", "browser"]


class CallPlan(Contract):
    capability: Capability
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=100)
    provider_config_id: UUID | None = None
    platform_model_id: str | None = None
    request_sha256: Sha256
    input_tokens_upper_bound: int = Field(default=0, ge=0)
    output_tokens_upper_bound: int = Field(default=0, ge=0)
    ocr_pages_upper_bound: int = Field(default=0, ge=0)
    search_requests: int = Field(default=0, ge=0)
    browser_units: int = Field(default=0, ge=0)
    image_count: int = Field(default=0, ge=0, le=20)
    image_input_sha256: Sha256 | None = None


class PreflightData(Contract):
    dry_run: Literal[True] = True
    command: str
    task_id: UUID | None
    input_hash: Sha256
    as_of: AwareDatetime
    planned_calls: int | None = Field(ge=0)
    maximum_calls: int | None = Field(default=None, ge=0)
    estimate: Cost
    next_call: CallPlan | None = None
    admission_blocker: str | None = None
    full_run_guaranteed: Literal[False] = False
    cached_job_id: UUID | None = None
    cached_result_cost: Cost | None = None
    estimated_duration_ms: int | None = Field(default=None, ge=0)
    duration_basis: Literal["unknown", "measured_profile"] = "unknown"
    uncertainty: list[Literal["retry_or_split", "dynamic_search", "unresolved_usage"]] = Field(
        default_factory=list
    )
