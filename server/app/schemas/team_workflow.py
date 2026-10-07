"""Approved task access, board, discussion and human co-sign contracts.

Transport uses the current shared Result/Cost contract.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, field_validator, model_validator

from app.schemas.budget_contracts import TaskBudgetView
from app.schemas.contracts import Category, Contract
from app.schemas.response_card_contracts import CardAction, CardState, ReviewDomain

type TaskRole = Literal["owner", "contributor", "reviewer", "observer"]
type MemberRole = Literal["contributor", "reviewer", "observer"]
type TaskWorkflowState = Literal["active", "archived"]
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
    "needs_reconfirmation",
    "unconfirmed",
    "rejected",
    "needs_material",
    "pending_cosign",
    "invalidated_cosign",
    "task_archived",
    "unconfirmed_evidence",
    "expired_certificate",
    "certificate_inactive",
    "prototype_undecided",
    "prototype_replacement_pending",
    "missing_reviewer",
    "unassigned",
    "assignee_unavailable",
    "job_failed",
    "insufficient_balance",
    "spend_cap_reached",
    "job_charge_limit_exceeded",
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
    "cosign",
    "supply_material",
    "repair_citation",
    "refresh_material",
    "reopen",
    "view",
    "assign",
    "find_reviewer",
    "view_job",
    "resolve_budget",
    "prototype_decide",
    "unarchive",
]
type CardEligibility = Literal[
    "eligible",
    "comply_only",
    "unconfirmed",
    "unclassified",
    "stale_material",
    "invalid_citation",
    "needs_reconfirmation",
]
type JobState = Literal["queued", "running", "succeeded", "failed", "cancelled"]
type WorkflowAction = Literal[
    "member_set",
    "member_removed",
    "owner_handed_over",
    "task_archived",
    "task_restored",
    "assignment_set",
    "thread_created",
    "comment_replied",
    "cosign_policy_set",
    "task_rule_set",
    "review_signed",
    "review_invalidated",
    "card_changed",
    "job_changed",
]
type Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
type Revision = Annotated[int, Field(strict=True, ge=1)]
type Cursor = Annotated[str, Field(min_length=1, max_length=1024)]
type Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
type Domains = Annotated[list[ReviewDomain], Field(max_length=2)]


def _unique(values: list, field: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field} cannot contain duplicates")


class _MemberFields(Contract):
    role: TaskRole
    review_domains: Domains = Field(default_factory=list)

    @model_validator(mode="after")
    def role_domains(self):
        _unique(self.review_domains, "review_domains")
        if self.role == "reviewer" and not self.review_domains:
            raise ValueError("reviewers require at least one review domain")
        if self.role == "observer" and self.review_domains:
            raise ValueError("observers cannot carry review domains")
        return self


class TaskMemberSet(_MemberFields):
    expected_revision: Revision
    role: MemberRole
    reason: Reason


class TaskMemberView(_MemberFields):
    org_id: UUID
    task_id: UUID
    user_id: UUID
    display_label: str | None = Field(default=None, min_length=1, max_length=254)
    active: bool
    revision: Revision


class WorkflowMutation(Contract):
    expected_revision: Revision
    reason: Reason


class TaskOwnerHandover(WorkflowMutation):
    target_user_id: UUID
    previous_owner_role: MemberRole
    previous_owner_review_domains: Domains = Field(default_factory=list)

    @model_validator(mode="after")
    def previous_owner_domains(self):
        _unique(self.previous_owner_review_domains, "previous_owner_review_domains")
        if self.previous_owner_role == "reviewer" and not self.previous_owner_review_domains:
            raise ValueError("a previous owner remaining a reviewer requires review domains")
        if self.previous_owner_role == "observer" and self.previous_owner_review_domains:
            raise ValueError("a previous owner becoming an observer cannot have review domains")
        return self


class TaskArchiveSet(WorkflowMutation):
    state: TaskWorkflowState


class TaskRuleSet(WorkflowMutation):
    co_sign_starred: bool
    dry_run: bool = False


class TaskWorkflowView(Contract):
    org_id: UUID
    task_id: UUID
    owner_user_id: UUID
    state: TaskWorkflowState
    revision: Revision
    access_epoch: Revision
    co_sign_starred: bool
    rule_revision: Revision
    last_event_cursor: Cursor
    archived_at: AwareDatetime | None = None
    archived_by_user_id: UUID | None = None

    @model_validator(mode="after")
    def archive_metadata(self):
        archived = self.state == "archived"
        if archived != (self.archived_at is not None) or archived != (
            self.archived_by_user_id is not None
        ):
            raise ValueError("archive actor and time accompany only archived state")
        return self


class TaskAccessView(Contract):
    workflow: TaskWorkflowView
    member: TaskMemberView | None
    management_recovery: bool = False
    allowed_actions: list[BoardNextAction] = Field(max_length=20)

    @model_validator(mode="after")
    def same_task(self):
        if self.member is None and not self.management_recovery:
            raise ValueError("missing task membership requires human-admin management recovery")
        if self.member is not None and (self.workflow.org_id, self.workflow.task_id) != (
            self.member.org_id,
            self.member.task_id,
        ):
            raise ValueError("task access must refer to one org and task")
        _unique(self.allowed_actions, "allowed_actions")
        return self


class RequirementAssignmentSet(Contract):
    expected_assignment_revision: int = Field(strict=True, ge=0)
    assignee_user_id: UUID | None
    reason: Reason


class RequirementAssignmentView(Contract):
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    requirement_id: UUID
    revision: int = Field(strict=True, ge=0)
    assignee_user_id: UUID | None
    changed_by_user_id: UUID | None
    changed_at: AwareDatetime | None

    @model_validator(mode="after")
    def assignment_metadata(self):
        changed = self.revision > 0
        if changed != (self.changed_by_user_id is not None) or changed != (
            self.changed_at is not None
        ):
            raise ValueError("persisted assignments require actor and time")
        if not changed and self.assignee_user_id is not None:
            raise ValueError("an initial unassigned slot cannot have an assignee")
        return self


class CommentContent(Contract):
    # Plain text, counted in Unicode code points; render as escaped text, not HTML.
    body: str = Field(min_length=1, max_length=4000)
    mentioned_user_ids: list[UUID] = Field(default_factory=list, max_length=20)
    client_request_id: UUID

    @field_validator("body")
    @classmethod
    def meaningful_body(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("comment body cannot be blank")
        if len(value.encode("utf-8")) > 16384:
            raise ValueError("comment body exceeds 16 KiB UTF-8")
        return value

    @field_validator("mentioned_user_ids")
    @classmethod
    def unique_mentions(cls, value: list[UUID]) -> list[UUID]:
        _unique(value, "mentioned_user_ids")
        return value


class CommentThreadCreate(CommentContent):
    expected_card_revision: Revision


class CommentReplyCreate(CommentContent):
    """Append-only reply: request UUID is the retry boundary, not a thread version."""


class CommentMessageView(CommentContent):
    id: UUID
    org_id: UUID
    task_id: UUID
    card_id: UUID
    thread_id: UUID
    author_user_id: UUID
    created_at: AwareDatetime


class CommentThreadView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    card_id: UUID
    created_card_revision_id: UUID
    revision: Revision
    created_by_user_id: UUID
    created_at: AwareDatetime
    last_message_at: AwareDatetime
    message_count: int = Field(ge=1)


class CommentThreadDetail(Contract):
    thread: CommentThreadView
    messages: list[CommentMessageView] = Field(max_length=100)
    next_cursor: Cursor | None = None

    @model_validator(mode="after")
    def messages_belong_to_thread(self):
        _unique([message.id for message in self.messages], "messages")
        if any(
            (message.org_id, message.task_id, message.card_id, message.thread_id)
            != (self.thread.org_id, self.thread.task_id, self.thread.card_id, self.thread.id)
            for message in self.messages
        ):
            raise ValueError("thread detail cannot contain messages from another thread")
        return self


class RequirementCoSignPolicySet(Contract):
    expected_policy_revision: int = Field(strict=True, ge=0)
    co_sign_required: bool
    reason: Reason


class RequirementCoSignPolicyView(Contract):
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    requirement_id: UUID
    revision: int = Field(strict=True, ge=0)
    primary_domain: ReviewDomain | None
    co_sign_required: bool
    starred: bool
    co_sign_starred: bool
    task_rule_revision: Revision
    required_domains: Domains

    @model_validator(mode="after")
    def effective_domains(self):
        _unique(self.required_domains, "required_domains")
        required = {self.primary_domain} if self.primary_domain is not None else set()
        if self.primary_domain is not None and (
            self.co_sign_required or (self.starred and self.co_sign_starred)
        ):
            required = {"commercial", "technical"}
        if set(self.required_domains) != required:
            raise ValueError("effective domains must include primary and applicable co-sign domain")
        return self


class CoSignSummary(Contract):
    status: Literal["not_required", "pending", "partial", "complete", "invalidated"]
    round_revision: int = Field(strict=True, ge=0)
    required_domains: Domains
    signed_domains: Domains = Field(default_factory=list)
    pending_domains: Domains = Field(default_factory=list)

    @model_validator(mode="after")
    def domain_partition(self):
        required, signed, pending = map(
            set, (self.required_domains, self.signed_domains, self.pending_domains)
        )
        for field, domains in (
            ("required_domains", required),
            ("signed_domains", signed),
            ("pending_domains", pending),
        ):
            if len(getattr(self, field)) != len(domains):
                raise ValueError(f"{field} cannot contain duplicates")
        if signed & pending or signed | pending != required:
            raise ValueError("signed and pending must partition the required domains")
        if self.status == "not_required":
            if self.round_revision != 0 or len(required) > 1:
                raise ValueError("not-required is an unclassified/single-domain slot without round")
        elif not required:
            raise ValueError("review states require a classified primary domain")
        if self.status in {"partial", "complete", "invalidated"} and self.round_revision == 0:
            raise ValueError("partial/complete/invalidated states require a persisted round")
        if self.status == "pending" and signed:
            raise ValueError("pending rounds cannot contain signatures")
        if self.status == "partial" and (len(required) != 2 or len(signed) != 1):
            raise ValueError("partial rounds require exactly one domain signature")
        if self.status == "complete" and pending:
            raise ValueError("complete rounds have no pending domains")
        if self.status == "invalidated" and signed:
            raise ValueError("invalidated summaries exclude signatures from superseded rounds")
        return self


class CoSignRoundView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    card_id: UUID
    extraction_job_id: UUID
    requirement_id: UUID
    round_revision: Revision
    card_revision: Revision
    card_revision_id: UUID
    policy_revision: int = Field(strict=True, ge=0)
    task_rule_revision: Revision
    access_epoch: Revision
    evidence_sha256: Sha256
    requirement_sha256: Sha256
    citation_sha256: Sha256
    content_sha256: Sha256
    required_domains: Domains = Field(min_length=1)
    purpose: Literal["response", "disposition"]
    intended_disposition: Literal["respond", "comply_only"]
    disposition_reason: Reason | None = None
    prior_card_state: CardState
    state: Literal["open", "complete", "invalidated"]
    created_at: AwareDatetime

    @field_validator("required_domains")
    @classmethod
    def unique_domains(cls, value: list[ReviewDomain]) -> list[ReviewDomain]:
        _unique(value, "required_domains")
        return value

    @model_validator(mode="after")
    def response_round_state(self):
        if self.purpose == "response" and self.prior_card_state != "pending_review":
            raise ValueError("response co-sign rounds snapshot a pending-review card")
        if self.purpose == "response" and self.intended_disposition != "respond":
            raise ValueError("response reviews cannot approve comply-only disposition")
        if self.purpose == "disposition" and self.prior_card_state not in {
            "draft",
            "rejected",
            "needs_material",
        }:
            raise ValueError("disposition rounds require a disposition-editable card state")
        return self


class CoSignOpen(WorkflowMutation):
    """Bind a card revision; respond/comply-only waits for signatures before disposition."""

    intended_disposition: Literal["respond", "comply_only"]
    purpose: Literal["disposition"] = "disposition"
    client_request_id: UUID


class CoSignConfirm(CardAction):
    """expected_revision is the exact card revision; partial signatures do not advance it."""

    expected_revision: Revision
    action: Literal["confirm"] = "confirm"
    purpose: Literal["response"] = "response"
    selected_domain: ReviewDomain
    expected_round: Revision
    client_request_id: UUID


class CoSignDispositionSign(Contract):
    """Disposition acknowledgment never confirms Evidence or creates a response row."""

    expected_revision: Revision
    purpose: Literal["disposition"] = "disposition"
    expected_round: Revision
    selected_domain: ReviewDomain
    reviewed_warning_codes: list[str] = Field(default_factory=list, max_length=100)
    reason: Reason
    client_request_id: UUID

    @field_validator("reviewed_warning_codes")
    @classmethod
    def reviewed_warnings(cls, value: list[str]) -> list[str]:
        value = [code.strip() for code in value]
        _unique(value, "reviewed_warning_codes")
        if any(not code for code in value):
            raise ValueError("reviewed warning codes cannot be blank")
        return value


type CoSignSignRequest = Annotated[
    CoSignConfirm | CoSignDispositionSign, Field(discriminator="purpose")
]


class CoSignSignatureView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    card_id: UUID
    round_id: UUID
    purpose: Literal["response", "disposition"]
    domain: ReviewDomain
    signer_user_id: UUID
    signer_org_role: Literal["bidder", "technical"]
    reviewed_evidence_ids: list[UUID] = Field(max_length=100)
    reviewed_warning_codes: list[str] = Field(max_length=100)
    reason_sha256: Sha256 | None
    client_request_id: UUID
    created_at: AwareDatetime

    @model_validator(mode="after")
    def authorized_role(self):
        if self.signer_org_role != {"commercial": "bidder", "technical": "technical"}[self.domain]:
            raise ValueError("signature org role must authorize the selected domain")
        _unique(self.reviewed_evidence_ids, "reviewed_evidence_ids")
        _unique(self.reviewed_warning_codes, "reviewed_warning_codes")
        if any(not code.strip() for code in self.reviewed_warning_codes):
            raise ValueError("reviewed warning codes cannot be blank")
        if self.reviewed_warning_codes and self.reason_sha256 is None:
            raise ValueError("reviewed warnings require a retained reason hash")
        if self.purpose == "disposition" and (
            self.reviewed_evidence_ids or self.reason_sha256 is None
        ):
            raise ValueError("disposition signatures require a reason and no reviewed Evidence")
        return self


class CoSignReviewView(Contract):
    round: CoSignRoundView
    signatures: list[CoSignSignatureView] = Field(max_length=2)
    summary: CoSignSummary

    @model_validator(mode="after")
    def signatures_match_round(self):
        _unique([signature.domain for signature in self.signatures], "signature domains")
        _unique([signature.signer_user_id for signature in self.signatures], "signers")
        if any(
            (signature.org_id, signature.task_id, signature.card_id, signature.round_id)
            != (self.round.org_id, self.round.task_id, self.round.card_id, self.round.id)
            or signature.domain not in self.round.required_domains
            for signature in self.signatures
        ):
            raise ValueError("signatures must belong to the exact review round")
        if self.round.purpose == "disposition" and any(
            signature.reviewed_evidence_ids or signature.reason_sha256 is None
            for signature in self.signatures
        ):
            raise ValueError("disposition signatures require reasons and cannot confirm Evidence")
        if any(signature.purpose != self.round.purpose for signature in self.signatures):
            raise ValueError("signature purpose must match the exact review round")
        if self.summary.round_revision != self.round.round_revision or set(
            self.summary.required_domains
        ) != set(self.round.required_domains):
            raise ValueError("summary must describe the exact review round")
        current = self.round.state != "invalidated"
        if current and set(self.summary.signed_domains) != {s.domain for s in self.signatures}:
            raise ValueError("active summary must include exactly the current signatures")
        if (self.round.state == "complete") != (self.summary.status == "complete") or (
            self.round.state == "invalidated"
        ) != (self.summary.status == "invalidated"):
            raise ValueError("round and summary states must agree")
        return self


class BoardQuery(Contract):
    extraction_job_id: UUID
    bucket: BoardBucket | None = None
    category: Category | None = None
    starred: bool | None = None
    owner_user_id: UUID | None = None
    unassigned: bool = False
    review_domain: ReviewDomain | None = None
    blocker: BoardBlocker | None = None
    mine: bool = False
    cursor: Cursor | None = None
    limit: int = Field(default=50, strict=True, ge=1, le=100)

    @model_validator(mode="after")
    def assignee_filter(self):
        if self.unassigned and self.owner_user_id is not None:
            raise ValueError("unassigned and owner_user_id filters are mutually exclusive")
        return self


class BoardActionTarget(Contract):
    kind: Literal["task", "requirement", "card", "job", "thread", "evidence", "annotation"]
    id: UUID


class BoardActionView(Contract):
    code: BoardNextAction
    target: BoardActionTarget
    eligible_user_ids: list[UUID] = Field(max_length=100)
    eligible_domains: Domains = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_eligibility(self):
        _unique(self.eligible_user_ids, "eligible_user_ids")
        _unique(self.eligible_domains, "eligible_domains")
        return self


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
    owner_user_id: UUID | None
    assignment_revision: int = Field(strict=True, ge=0)
    co_sign: CoSignSummary | None
    flags: BoardRowFlags
    blockers: list[BoardBlocker] = Field(max_length=20)
    next_actions: list[BoardActionView] = Field(max_length=20)
    comment_thread_count: int = Field(ge=0)

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
    org_id: UUID
    task_id: UUID
    workflow: TaskWorkflowView
    event_cursor: Cursor
    as_of: AwareDatetime
    next_cursor: Cursor | None
    returned: int = Field(strict=True, ge=0, le=20)

    @model_validator(mode="after")
    def same_task(self):
        if (self.org_id, self.task_id) != (self.workflow.org_id, self.workflow.task_id):
            raise ValueError("progress and workflow must belong to one task")
        return self


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
    actor_user_id: UUID | None
    action: WorkflowAction
    requirement_id: UUID | None = None
    card_id: UUID | None = None
    job_id: UUID | None = None
    thread_id: UUID | None = None
    created_at: AwareDatetime


class BoardData(Contract):
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    workflow: TaskWorkflowView
    counts: BoardCounts
    matching: int = Field(strict=True, ge=0)
    returned: int = Field(strict=True, ge=0, le=100)
    budget: TaskBudgetView
    next_cursor: Cursor | None = None
    event_cursor: Cursor
    as_of: AwareDatetime
    refresh_by: AwareDatetime
    jobs: list[BoardJobView] = Field(max_length=20)
    activity: list[BoardActivityView] = Field(max_length=20)

    @model_validator(mode="after")
    def pinned_task(self):
        if (self.org_id, self.task_id) != (self.workflow.org_id, self.workflow.task_id):
            raise ValueError("board workflow must belong to the requested task")
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
    thread_id: UUID | None = None
    comment_id: UUID | None = None
    invalidate_all: bool = False

    @model_validator(mode="after")
    def bounded_invalidation(self):
        _unique(self.requirement_ids, "requirement_ids")
        _unique(self.card_ids, "card_ids")
        count = (
            len(self.requirement_ids)
            + len(self.card_ids)
            + int(self.thread_id is not None)
            + int(self.comment_id is not None)
        )
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


class TaskAccessChanged(Contract):
    type: Literal["access_changed"] = "access_changed"
    state: TaskWorkflowState
    access_epoch: Revision


type TaskEventPayload = Annotated[
    BoardChanged | JobProgressChanged | TaskAccessChanged, Field(discriminator="type")
]


class TaskEventHeadView(Contract):
    """Internal independent head; never expose sequence or serialize it in public Result."""

    org_id: UUID
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
    org_id: UUID
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
    org_id: UUID
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
    org_id: UUID
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
        if any(
            (event.org_id, event.task_id) != (self.org_id, self.task_id) for event in self.events
        ):
            raise ValueError("replay contains events from exactly one task")
        if self.reset_required is not None and (self.events or self.has_more):
            raise ValueError("expired replay returns only a reset control")
        return self


class TaskWorkflowData(Contract):
    workflow: TaskWorkflowView


class TaskMemberData(Contract):
    workflow: TaskWorkflowView
    member: TaskMemberView


class PageQuery(Contract):
    cursor: Cursor | None = None
    limit: int = Field(default=50, strict=True, ge=1, le=100)


class PageData(Contract):
    org_id: UUID
    task_id: UUID
    card_id: UUID | None = None
    thread_id: UUID | None = None
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


class MemberCandidateView(Contract):
    """Only active verified same-org Membership labels, never a global user directory."""

    org_id: UUID
    user_id: UUID
    display_label: str = Field(min_length=1, max_length=254)
    org_role: Literal["admin", "bidder", "technical", "viewer"]
    active: Literal[True] = True


class TaskRuleView(Contract):
    org_id: UUID
    task_id: UUID
    workflow_revision: Revision
    rule_revision: Revision
    co_sign_starred: bool


class TaskRuleData(Contract):
    rule: TaskRuleView
    dry_run: bool
    affected_requirements: int = Field(strict=True, ge=0)


class AssignmentData(Contract):
    assignment: RequirementAssignmentView


class CommentData(Contract):
    comment: CommentMessageView


class ThreadCreatedData(Contract):
    thread: CommentThreadView
    first_comment: CommentMessageView


class CoSignPolicyData(Contract):
    policy: RequirementCoSignPolicyView


class CoSignData(Contract):
    summary: CoSignSummary
    signature: CoSignSignatureView
    current_card_revision: Revision
    current_card_revision_id: UUID


class ReviewRoundData(Contract):
    round: CoSignRoundView


class SignoffsData(PageData):
    round: CoSignRoundView | None
    summary: CoSignSummary


class SignoffsView(Contract):
    data: SignoffsData
    items: list[CoSignSignatureView] = Field(max_length=100)

    @model_validator(mode="after")
    def returned_count(self):
        if len(self.items) != self.data.returned:
            raise ValueError("returned must equal the signature history item count")
        return self
