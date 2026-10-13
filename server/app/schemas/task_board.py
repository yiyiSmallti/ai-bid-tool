"""Task board, progress and event contracts.

Transport uses the current shared Result/Cost contract.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from app.schemas.contracts import Category, Contract
from app.schemas.response_card_contracts import CardState, ReviewDomain

type BoardBucket = Literal[
    "gap", "draft_card", "pending_review", "needs_material", "confirmed", "comply_only"
]
type BoardBlocker = Literal[
    "annotation_source_stale",
    "annotation_release_pending",
    "annotation_release_failed",
    "missing_card",
    "unclassified",
    "invalid_citation",
    "stale_material",
    "unconfirmed",
    "rejected",
    "needs_material",
    "unconfirmed_evidence",
    "expired_certificate",
    "certificate_inactive",
    "prototype_undecided",
    "prototype_replacement_pending",
    "job_failed",
    "job_call_limit_exceeded",
]
type BoardNextAction = Literal[
    "inspect_annotated_material",
    "confirm_annotated_material",
    "retry_annotation_release",
    "replace_annotation_source",
    "create_card",
    "edit_card",
    "classify",
    "submit",
    "confirm",
    "supply_material",
    "repair_citation",
    "refresh_material",
    "reopen",
    "view",
    "view_job",
    "prototype_decide",
]
type CardEligibility = Literal[
    "eligible",
    "comply_only",
    "unconfirmed",
    "unclassified",
    "stale_material",
    "invalid_citation",
]
type JobState = Literal["queued", "running", "succeeded", "failed", "cancelled"]
type ActivityAction = Literal[
    "card_changed",
    "job_changed",
]
type Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
type Revision = Annotated[int, Field(strict=True, ge=1)]
type Cursor = Annotated[str, Field(min_length=1, max_length=1024)]
type Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


def _unique(values: list, field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} cannot contain duplicates")


class BoardQuery(Contract):
    extraction_job_id: UUID
    bucket: BoardBucket | None = None
    category: Category | None = None
    starred: bool | None = None
    review_domain: ReviewDomain | None = None
    blocker: BoardBlocker | None = None
    mine: bool = False
    cursor: Cursor | None = None
    limit: int = Field(default=50, strict=True, ge=1, le=100)


class BoardActionTarget(Contract):
    kind: Literal["task", "requirement", "card", "job", "evidence", "annotation"]
    id: UUID


class BoardActionView(Contract):
    code: BoardNextAction
    target: BoardActionTarget


class BoardRowFlags(Contract):
    human_needs_material: bool
    model_needs_material_hint: bool
    certificate_date_advisory: bool
    final_export_prototype_blocked: bool


class BoardRow(Contract):
    requirement_id: UUID
    title: str = Field(min_length=1, max_length=200)
    category: Category
    starred: bool
    bucket: BoardBucket
    card_id: UUID | None
    card_revision: Revision | None
    card_state: CardState | None
    eligibility: CardEligibility | None
    review_domain: ReviewDomain | None
    flags: BoardRowFlags
    blockers: list[BoardBlocker] = Field(max_length=20)
    next_actions: list[BoardActionView] = Field(max_length=20)

    @model_validator(mode="after")
    def card_presence(self):
        exists = self.card_id is not None
        if any(
            exists != (value is not None)
            for value in (self.card_revision, self.card_state, self.eligibility)
        ):
            raise ValueError("card ID, revision, state and eligibility must occur together")
        _unique(self.blockers, "blockers")
        _unique(
            [(action.code, action.target.kind, action.target.id) for action in self.next_actions],
            "next_actions",
        )
        return self


class BoardCounts(Contract):
    total: int = Field(ge=0, le=5000)
    gap: int = Field(ge=0)
    draft_card: int = Field(ge=0)
    pending_review: int = Field(ge=0)
    needs_material: int = Field(ge=0)
    confirmed: int = Field(ge=0)
    comply_only: int = Field(ge=0)

    @model_validator(mode="after")
    def buckets_partition_total(self):
        if self.total != sum(
            (
                self.gap,
                self.draft_card,
                self.pending_review,
                self.needs_material,
                self.confirmed,
                self.comply_only,
            )
        ):
            raise ValueError("six mutually exclusive bucket counts must sum to total")
        return self


class JobProgress(Contract):
    completed: int = Field(strict=True, ge=0)
    total: int | None = Field(default=None, strict=True, ge=0)

    @model_validator(mode="after")
    def bounded_progress(self):
        if self.total is not None and self.completed > self.total:
            raise ValueError("completed work cannot exceed known total")
        return self


class BoardJobView(Contract):
    job_id: UUID
    extraction_job_id: UUID | None
    state: JobState
    run_id: UUID | None
    attempts: int = Field(strict=True, ge=0)
    progress: JobProgress | None
    updated_at: AwareDatetime


class TaskProgressQuery(Contract):
    cursor: Cursor | None = None
    limit: int = Field(default=20, strict=True, ge=1, le=20)


class TaskProgressData(Contract):
    task_id: UUID
    event_cursor: Cursor
    as_of: AwareDatetime
    next_cursor: Cursor | None
    returned: int = Field(strict=True, ge=0, le=20)


class TaskProgressView(TaskProgressData):
    jobs: list[BoardJobView] = Field(max_length=20)

    @model_validator(mode="after")
    def page_jobs(self):
        _unique([job.job_id for job in self.jobs], "progress jobs")
        if len(self.jobs) != self.returned:
            raise ValueError("returned must equal progress job count")
        return self


class BoardActivityView(Contract):
    id: UUID
    action: ActivityAction
    requirement_id: UUID | None = None
    card_id: UUID | None = None
    job_id: UUID | None = None
    created_at: AwareDatetime


class BoardData(Contract):
    task_id: UUID
    extraction_job_id: UUID
    counts: BoardCounts
    matching: int = Field(strict=True, ge=0)
    returned: int = Field(strict=True, ge=0, le=100)
    next_cursor: Cursor | None = None
    event_cursor: Cursor
    as_of: AwareDatetime
    refresh_by: AwareDatetime
    jobs: list[BoardJobView] = Field(max_length=20)
    activity: list[BoardActivityView] = Field(max_length=20)

    @model_validator(mode="after")
    def pinned_task(self):
        if not self.returned <= self.matching <= self.counts.total:
            raise ValueError("returned <= matching <= total must hold")
        if not 0 < (self.refresh_by - self.as_of).total_seconds() <= 30:
            raise ValueError("board validity refresh deadline must be within 30 seconds")
        return self


class BoardView(BoardData):
    """Internal aggregate; Result.data uses BoardData and Result.items uses rows."""

    rows: list[BoardRow] = Field(max_length=100)

    @model_validator(mode="after")
    def page_rows(self):
        _unique([row.requirement_id for row in self.rows], "board requirements")
        if len(self.rows) != self.returned:
            raise ValueError("returned must equal page row count")
        return self


class BoardChanged(Contract):
    type: Literal["board_changed"] = "board_changed"
    extraction_job_id: UUID | None = None
    requirement_ids: list[UUID] = Field(default_factory=list, max_length=100)
    card_ids: list[UUID] = Field(default_factory=list, max_length=100)
    invalidate_all: bool = False

    @model_validator(mode="after")
    def bounded_invalidation(self):
        _unique(self.requirement_ids, "requirement_ids")
        _unique(self.card_ids, "card_ids")
        count = len(self.requirement_ids) + len(self.card_ids)
        if count > 100:
            raise ValueError("board change may address at most 100 objects")
        if self.invalidate_all == bool(count):
            raise ValueError("invalidate_all replaces explicit object IDs")
        return self


class JobProgressChanged(Contract):
    type: Literal["job_progress"] = "job_progress"
    job_id: UUID
    state: JobState
    run_id: UUID | None
    attempts: int = Field(strict=True, ge=0)
    progress: JobProgress | None


type TaskEventPayload = Annotated[BoardChanged | JobProgressChanged, Field(discriminator="type")]


class TaskEventHeadView(Contract):
    """Internal independent head; never expose sequence or serialize it in public Result."""

    task_id: UUID
    sequence: int = Field(strict=True, ge=0)
    retained_floor: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def retained_window(self):
        if self.retained_floor > self.sequence:
            raise ValueError("retained floor cannot exceed the committed head")
        return self


class TaskEventView(Contract):
    event_id: UUID
    task_id: UUID
    cursor: Cursor
    payload: TaskEventPayload
    created_at: AwareDatetime

    @model_validator(mode="after")
    def bounded_frame(self):
        if len(self.model_dump_json().encode("utf-8")) > 4096:
            raise ValueError("complete event data frame exceeds 4096 UTF-8 bytes")
        return self


class StoredTaskEvent(Contract):
    """Persist facts only; replay creates a cursor for the subscriber's visibility."""

    event_id: UUID
    task_id: UUID
    sequence: Revision
    payload: TaskEventPayload
    created_at: AwareDatetime


class StreamHeartbeat(Contract):
    type: Literal["heartbeat"] = "heartbeat"
    cursor: Cursor
    as_of: AwareDatetime


class StreamResetRequired(Contract):
    type: Literal["reset_required"] = "reset_required"
    reason: Literal[
        "retention_expired", "snapshot_expired", "initial_snapshot_required", "access_changed"
    ]
    head_cursor: Cursor


type StreamControl = Annotated[StreamHeartbeat | StreamResetRequired, Field(discriminator="type")]


class EventReplayQuery(Contract):
    cursor: Cursor | None = None
    limit: int = Field(default=100, strict=True, ge=1, le=100)
    wait_seconds: int = Field(default=0, strict=True, ge=0, le=25)


class EventReplayData(Contract):
    task_id: UUID
    next_cursor: Cursor | None
    head_cursor: Cursor
    has_more: bool
    reset_required: StreamResetRequired | None = None


class EventReplayView(EventReplayData):
    """Internal aggregate; public page entries belong in Result.items."""

    events: list[TaskEventView] = Field(max_length=100)

    @model_validator(mode="after")
    def ordered_task_events(self):
        _unique([event.event_id for event in self.events], "event IDs")
        _unique([event.cursor for event in self.events], "event cursors")
        if any(event.task_id != self.task_id for event in self.events):
            raise ValueError("replay contains events from exactly one task")
        if self.reset_required is not None and (self.events or self.has_more):
            raise ValueError("expired replay returns only a reset control")
        return self


class PageQuery(Contract):
    cursor: Cursor | None = None
    limit: int = Field(default=50, strict=True, ge=1, le=100)


class PageData(Contract):
    task_id: UUID
    next_cursor: Cursor | None
    returned: int = Field(strict=True, ge=0, le=100)
    has_more: bool

    @model_validator(mode="after")
    def continuation(self):
        if self.has_more and self.next_cursor is None:
            raise ValueError("a page with more entries requires a continuation cursor")
        return self


class PageView[T](Contract):
    data: PageData
    items: list[T] = Field(max_length=100)

    @model_validator(mode="after")
    def returned_count(self):
        if len(self.items) != self.data.returned:
            raise ValueError("returned must equal the page item count")
        return self
