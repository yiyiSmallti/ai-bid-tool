"""Bounded read projections; business eligibility comes from current card rules."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import case, literal, select, text

from app.core.errors import ServiceError, not_found
from app.models.entities import Job, Membership, User
from app.models.team_workflow import TaskMember
from app.schemas.team_workflow import (
    BoardActionView,
    BoardActivityView,
    BoardCounts,
    BoardJobView,
    BoardQuery,
    BoardRow,
    BoardRowFlags,
    BoardView,
    JobProgress,
    PageData,
    PageView,
    TaskProgressQuery,
    TaskProgressView,
    TaskWorkflowView,
)
from app.services import budgets, requirement_consumption, task_events, task_workflow
from app.services.auth import ROLE_SCOPES

MAX_REQUIREMENTS = 5000
BAD_ELIGIBILITY = {"invalid_citation", "needs_reconfirmation", "stale_material", "unclassified"}


def workflow_view(workflow, cursor):
    return TaskWorkflowView(
        org_id=workflow.org_id,
        task_id=workflow.task_id,
        owner_user_id=workflow.owner_user_id,
        state=workflow.state,
        revision=workflow.revision,
        access_epoch=workflow.access_epoch,
        co_sign_starred=workflow.co_sign_starred,
        rule_revision=workflow.rule_revision,
        archived_at=workflow.archived_at,
        archived_by_user_id=workflow.archived_by_user_id,
        last_event_cursor=cursor,
    )


def persisted_progress(job):
    value = (job.result or {}).get("progress")
    if value is None:
        return None
    return JobProgress.model_validate(value)


def job_view(job):
    return BoardJobView(
        job_id=job.id,
        extraction_job_id=(job.result or {}).get("extraction_job_id")
        or (job.result or {}).get("submission", {}).get("extraction_job_id"),
        state=job.status,
        run_id=job.run_id,
        attempts=job.attempts,
        progress=persisted_progress(job),
        updated_at=job.finished_at or job.created_at,
    )


async def job_page(session, actor, task_id, storage, *, limit=20, after=None):
    active = case((Job.status.in_(["queued", "running"]), 0), else_=1)
    query = select(Job).where(
        Job.org_id == actor.org_id,
        Job.task_id == task_id,
        Job.kind.not_in(task_events.hidden_job_kinds(actor)),
    )
    # Sorting changes invalidate the enclosing snapshot continuation watermark.
    if after is not None:
        rank, created, ident = after
        from sqlalchemy import tuple_

        query = query.where(
            tuple_(active, Job.created_at, Job.id)
            > tuple_(literal(rank), literal(datetime.fromisoformat(created)), literal(UUID(ident)))
        )
    candidates = list(
        await session.scalars(query.order_by(active, Job.created_at, Job.id).limit(100))
    )
    session.info.setdefault("task_board_job_rows", {}).update({job.id: job for job in candidates})
    jobs = []
    scanned = after
    for job in candidates:
        if await task_events.visible_job(session, actor, job, storage):
            if len(jobs) == limit:
                return jobs, scanned
            jobs.append(job_view(job))
        scanned = [
            0 if job.status in ("queued", "running") else 1,
            job.created_at.isoformat(),
            str(job.id),
        ]
    if len(candidates) == 100:
        return jobs, scanned
    return jobs, None


def query_hash(query):
    filters = query.model_dump(mode="json", exclude={"cursor", "limit"})
    return hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()


def page_cursor(actor, task_id, workflow, settings, watermark, *, purpose, day, query="", key=None):
    return task_events.issue_cursor(
        actor, task_id, workflow, settings, purpose=purpose, seq=watermark, d=day, q=query, k=key
    )


def validate_page(cursor, actor, task_id, workflow, settings, watermark, *, purpose, day, query=""):
    data = task_events.open_cursor(cursor, actor, task_id, workflow, settings, purpose=purpose)
    if data["s"] != watermark or data.get("d") != day or data.get("q") != query:
        raise ServiceError("board_changed", "Snapshot changed; reload first page", 409, 2)
    return data.get("k")


async def progress(session, actor, task_id, query: TaskProgressQuery, storage, settings):
    _, workflow, _ = await task_workflow.access(session, actor, task_id)
    actor.require("job:read")
    watermark = await task_events.head(session, actor.org_id, task_id)
    as_of = datetime.now(UTC)
    key = (
        validate_page(
            query.cursor,
            actor,
            task_id,
            workflow,
            settings,
            watermark.last_seq,
            purpose="progress",
            day=as_of.date().isoformat(),
        )
        if query.cursor
        else None
    )
    items, next_key = await job_page(session, actor, task_id, storage, limit=query.limit, after=key)
    cursor = task_events.issue_cursor(actor, task_id, workflow, settings, seq=watermark.last_seq)
    return TaskProgressView(
        org_id=actor.org_id,
        task_id=task_id,
        workflow=workflow_view(workflow, cursor),
        event_cursor=cursor,
        as_of=as_of,
        next_cursor=page_cursor(
            actor,
            task_id,
            workflow,
            settings,
            watermark.last_seq,
            purpose="progress",
            day=as_of.date().isoformat(),
            key=next_key,
        )
        if next_key
        else None,
        returned=len(items),
        jobs=items,
    )


async def activity(
    session, actor, task_id, storage, settings, *, cursor=None, limit=50, authorized=None
):
    """Project allowlisted audit actions; reason/body/details never leave the service."""
    from sqlalchemy import and_, literal, or_, tuple_

    from app.models.entities import AuditLog
    from app.models.response_cards import ResponseCard

    if authorized is None:
        _, workflow, _ = await task_workflow.access(session, actor, task_id)
        watermark = await task_events.head(session, actor.org_id, task_id)
    else:
        workflow, watermark = authorized
    actor.require("card:read")
    actor.require("job:read")
    day = datetime.now(UTC).date().isoformat()
    key = (
        validate_page(
            cursor,
            actor,
            task_id,
            workflow,
            settings,
            watermark.last_seq,
            purpose="activity",
            day=day,
        )
        if cursor
        else None
    )
    actions = {
        "task.member_set": "member_set",
        "task.member_removed": "member_removed",
        "task.owner_handed_over": "owner_handed_over",
        "task.archived": "task_archived",
        "task.unarchived": "task_restored",
        "task.assignment_changed": "assignment_set",
        "task.thread_created": "thread_created",
        "task.comment_added": "comment_replied",
        "task.review_rule_changed": "task_rule_set",
        "task.review_policy_changed": "cosign_policy_set",
        "task.review_round_opened": "card_changed",
        "task.review_round_invalidated": "review_invalidated",
        "task.domain_signed": "review_signed",
        "task.cosign_completed": "review_signed",
    }
    card_actions = {
        f"card.{value}"
        for value in (
            "create",
            "update",
            "classify",
            "submit",
            "withdraw",
            "confirm",
            "reject",
            "needs_material",
            "reopen",
            "disposition",
        )
    }
    cards = select(ResponseCard.id).where(
        ResponseCard.org_id == actor.org_id, ResponseCard.task_id == task_id
    )
    task_jobs = select(Job.id).where(Job.org_id == actor.org_id, Job.task_id == task_id)
    # Existing card audits bind their object directly; member actions bind the task.
    # Job audits bind a real stored job rather than trusting arbitrary detail IDs.
    query = select(AuditLog).where(
        AuditLog.org_id == actor.org_id,
        or_(
            and_(AuditLog.object_id == task_id, AuditLog.action.in_(actions)),
            and_(AuditLog.object_id.in_(cards), AuditLog.action.in_(card_actions)),
            and_(
                AuditLog.object_id.in_(task_jobs),
                or_(
                    AuditLog.action.like("job.%"),
                    AuditLog.action.in_(
                        [
                            "check.submit",
                            "check.cancelled",
                            "score.submit",
                            "score.cancelled",
                            "score_rubric.submit",
                            "score_rubric.cancelled",
                        ]
                    ),
                ),
            ),
        ),
    )
    if key:
        query = query.where(
            tuple_(AuditLog.created_at, AuditLog.id)
            < tuple_(literal(datetime.fromisoformat(key[0])), literal(UUID(key[1])))
        )
    rows = list(
        await session.scalars(
            query.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).limit(limit + 1)
        )
    )
    job_ids = {
        row.object_id
        for row in rows
        if row.action not in actions and row.action not in card_actions
    }
    jobs = (
        {
            job.id: job
            for job in await session.scalars(
                select(Job).where(
                    Job.org_id == actor.org_id, Job.task_id == task_id, Job.id.in_(job_ids)
                )
            )
        }
        if job_ids
        else {}
    )
    visible_jobs = {
        ident
        for ident, job in jobs.items()
        if await task_events.visible_job(session, actor, job, storage)
    }
    projected = []
    for row in rows:
        card_id = row.object_id if row.action in card_actions else None
        job_id = row.object_id if row.object_id in jobs else None
        if job_id is not None and job_id not in visible_jobs:
            continue
        action = actions.get(row.action, "card_changed" if card_id else "job_changed")
        projected.append(
            (
                row,
                BoardActivityView.model_validate(
                    dict(
                        id=row.id,
                        actor_user_id=row.actor_user_id,
                        action=action,
                        card_id=row.details.get("card_id") if row.action in actions else card_id,
                        requirement_id=row.details.get("requirement_id")
                        if row.action in actions
                        else None,
                        thread_id=row.details.get("thread_id") if row.action in actions else None,
                        job_id=job_id,
                        created_at=row.created_at,
                    )
                ),
            )
        )
    more = len(projected) > limit or len(rows) > limit
    selected = projected[:limit]
    next_cursor = (
        page_cursor(
            actor,
            task_id,
            workflow,
            settings,
            watermark.last_seq,
            purpose="activity",
            day=day,
            key=[
                (selected[-1][0] if len(projected) > limit else rows[-1]).created_at.isoformat(),
                str((selected[-1][0] if len(projected) > limit else rows[-1]).id),
            ],
        )
        if more
        else None
    )
    return PageView[BoardActivityView](
        data=PageData(
            org_id=actor.org_id,
            task_id=task_id,
            next_cursor=next_cursor,
            returned=len(selected),
            has_more=more,
        ),
        items=[item for _, item in selected],
    )


def row_projection(
    requirement,
    view,
    workflow,
    actor,
    member,
    reviewers,
    *,
    assignment=None,
    thread_count=0,
    mentioned_thread_id=None,
):
    blockers = []
    if view is None:
        bucket = "gap"
        blockers = ["missing_card"]
    else:
        eligibility = view["eligibility"]
        state = view["state"]
        if eligibility in BAD_ELIGIBILITY or state == "rejected":
            bucket = "gap"
            blockers.append("rejected" if state == "rejected" else eligibility)
        elif eligibility == "comply_only":
            bucket = "comply_only"
        elif state == "needs_material" or (
            state == "draft" and view["review_hint"] == "needs_material"
        ):
            bucket = "needs_material"
        elif state == "pending_review":
            bucket = "pending_review"
        elif eligibility == "eligible":
            bucket = "confirmed"
        else:
            bucket = "draft_card"
        if eligibility == "unconfirmed":
            blockers.append("unconfirmed")
        if any(evidence["confirmed_by"] is None for evidence in view["evidence"]):
            blockers.append("unconfirmed_evidence")
    domain = (
        view["review_domain"]
        if view
        else {"technical": "technical", "qualification": "commercial"}.get(requirement.category)
    )
    co_sign = view.get("co_sign") if view else None
    required_domains = (
        co_sign["required_domains"]
        if co_sign
        else (
            ["commercial", "technical"]
            if domain
            and (
                bool(assignment and assignment.co_sign_required)
                or workflow.co_sign_starred
                and requirement.starred
            )
            else [domain]
            if domain
            else []
        )
    )
    if co_sign is None:
        co_sign = {
            "status": "pending" if len(required_domains) > 1 else "not_required",
            "round_revision": 0,
            "required_domains": required_domains,
            "signed_domains": [],
            "pending_domains": required_domains,
        }
    pending_domains = co_sign["pending_domains"]
    eligible = [entry.user_id for entry in reviewers if domain in entry.review_domains]
    missing_domains = [
        required
        for required in required_domains
        if not any(required in entry.review_domains for entry in reviewers)
    ]
    signer_domains = [
        required
        for required in pending_domains
        if any(
            entry.user_id == actor.user_id and required in entry.review_domains
            for entry in reviewers
        )
    ]
    if co_sign and co_sign["status"] in {"pending", "partial"}:
        if len(required_domains) > 1:
            blockers.append("pending_cosign")
        if view and view.get("co_sign_purpose") == "disposition":
            bucket = "pending_review"
    elif co_sign and co_sign["status"] == "invalidated":
        if len(required_domains) > 1:
            blockers.append("invalidated_cosign")
        elif not {"stale_material", "invalid_citation", "needs_reconfirmation"}.intersection(
            blockers
        ):
            blockers.append("needs_reconfirmation")
        bucket = "gap"
    if view:
        blockers.extend(view.get("blockers", []))
    if missing_domains:
        blockers.append("missing_reviewer")
    assignee = assignment.assignee_user_id if assignment else None
    available = any(entry.user_id == assignee and entry.can_edit for entry in reviewers)
    if assignee is None:
        blockers.append("unassigned")
    elif not available:
        blockers.append("assignee_unavailable")
    actions = []
    target = {
        "kind": "card" if view else "requirement",
        "id": view["id"] if view else requirement.id,
    }
    if workflow.state == "archived":
        blockers.append("task_archived")
        if (
            actor.actor_kind == "session"
            and actor.token_id is None
            and "task:archive" in actor.scopes
            and (workflow.owner_user_id == actor.user_id or actor.role == "admin")
        ):
            actions.append(
                BoardActionView.model_validate(
                    dict(
                        code="unarchive",
                        target={"kind": "task", "id": workflow.task_id},
                        eligible_user_ids=[actor.user_id],
                    )
                )
            )
    elif member and member.role in ("owner", "contributor", "reviewer"):
        code = (
            "create_card"
            if view is None
            else "repair_citation"
            if "invalid_citation" in blockers
            else "refresh_material"
            if "stale_material" in blockers
            else "classify"
            if domain is None
            and actor.role == "admin"
            and actor.actor_kind == "session"
            and actor.token_id is None
            and member.role in ("owner", "contributor")
            else "reopen"
            if view
            and view["state"] == "confirmed"
            and view["eligibility"] == "needs_reconfirmation"
            and actor.actor_kind == "session"
            and actor.token_id is None
            and actor.user_id in eligible
            else "cosign"
            if bucket == "pending_review"
            and len(required_domains) > 1
            and co_sign is not None
            and co_sign["status"] in {"pending", "partial"}
            and signer_domains
            and actor.actor_kind == "session"
            and actor.token_id is None
            else "confirm"
            if bucket == "pending_review"
            and (co_sign is None or len(co_sign["required_domains"]) <= 1)
            and actor.actor_kind == "session"
            and actor.token_id is None
            and actor.user_id in eligible
            else "supply_material"
            if bucket == "needs_material"
            else "edit_card"
            if view["state"] == "rejected"
            else "submit"
            if bucket == "draft_card"
            else "view"
        )
        scopes = {
            "create_card": "card:write",
            "classify": "evidence:confirm",
            "submit": "card:write",
            "reopen": "evidence:confirm",
            "edit_card": "card:write",
            "supply_material": "card:write",
            "refresh_material": "card:write",
            "repair_citation": "req:extract",
            "confirm": "evidence:confirm",
            "cosign": "card:cosign",
            "view": "card:read",
        }
        if code == "repair_citation" and not (
            actor.role == "admin"
            and actor.actor_kind == "session"
            and actor.token_id is None
            and "evidence:confirm" in actor.scopes
        ):
            code = "view"
        if scopes[code] in actor.scopes and (
            code in ("view", "confirm", "cosign", "reopen")
            or member.role in ("owner", "contributor")
        ):
            actions.append(
                BoardActionView.model_validate(
                    dict(
                        code=code,
                        target={"kind": "requirement", "id": requirement.id}
                        if code == "repair_citation"
                        else target,
                        eligible_user_ids=[actor.user_id],
                        eligible_domains=signer_domains
                        if code == "cosign"
                        else [domain]
                        if code in {"confirm", "reopen"} and domain
                        else [],
                    )
                )
            )
    if (
        workflow.state == "active"
        and "missing_reviewer" in blockers
        and actor.actor_kind == "session"
        and "task:members:write" in actor.scopes
        and actor.token_id is None
        and (actor.role == "admin" or member and member.role == "owner")
    ):
        actions.append(
            BoardActionView.model_validate(
                dict(
                    code="find_reviewer",
                    target={"kind": "task", "id": workflow.task_id},
                    eligible_user_ids=[actor.user_id],
                    eligible_domains=missing_domains,
                )
            )
        )
    if (
        workflow.state == "active"
        and (assignee is None or not available)
        and actor.actor_kind == "session"
        and actor.token_id is None
        and "card:assign" in actor.scopes
        and (actor.role == "admin" or member and member.role == "owner")
    ):
        actions.insert(
            0,
            BoardActionView.model_validate(
                dict(
                    code="assign",
                    target={"kind": "requirement", "id": requirement.id},
                    eligible_user_ids=[actor.user_id],
                )
            ),
        )
    if mentioned_thread_id is not None:
        actions.append(
            BoardActionView.model_validate(
                dict(
                    code="view",
                    target={"kind": "thread", "id": mentioned_thread_id},
                    eligible_user_ids=[actor.user_id],
                )
            )
        )
    if not actions:
        actions = [
            BoardActionView.model_validate(
                dict(code="view", target=target, eligible_user_ids=[actor.user_id])
            )
        ]
    return BoardRow.model_validate(
        dict(
            requirement_id=requirement.id,
            title=(requirement.quote.strip() or "Requirement")[:200],
            category=requirement.category,
            starred=requirement.starred,
            bucket=bucket,
            card_id=view["id"] if view else None,
            card_revision=view["revision"] if view else None,
            card_state=view["state"] if view else None,
            eligibility=view["eligibility"] if view else None,
            review_domain=domain,
            owner_user_id=assignee,
            assignment_revision=assignment.assignment_revision if assignment else 0,
            co_sign=co_sign,
            flags=BoardRowFlags(
                human_needs_material=bool(view and view["state"] == "needs_material"),
                model_needs_material_hint=bool(
                    view and view["state"] == "draft" and view["review_hint"] == "needs_material"
                ),
                certificate_date_advisory=bool(view and view.get("date_advisory")),
                final_export_prototype_blocked=bool(view and view.get("prototype_blocked")),
            ),
            blockers=list(dict.fromkeys(blockers)),
            next_actions=actions,
            comment_thread_count=thread_count,
        )
    )


def matches(row, query, actor, *, bucket=True, mentioned=False):
    return (
        (not bucket or query.bucket is None or row.bucket == query.bucket)
        and (query.category is None or row.category == query.category)
        and (query.starred is None or row.starred == query.starred)
        and (query.owner_user_id is None or row.owner_user_id == query.owner_user_id)
        and (not query.unassigned or row.owner_user_id is None)
        and (
            query.review_domain is None
            or row.review_domain == query.review_domain
            or row.co_sign is not None
            and query.review_domain in row.co_sign.required_domains
        )
        and (query.blocker is None or query.blocker in row.blockers)
        and (
            not query.mine
            or row.owner_user_id == actor.user_id
            or mentioned
            or (
                actor.token_id is None
                and actor.actor_kind == "session"
                and any(
                    (
                        action.code in {"confirm", "cosign", "reopen"}
                        or action.target.kind == "thread"
                    )
                    and actor.user_id in action.eligible_user_ids
                    for action in row.next_actions
                )
            )
        )
    )


async def board(session, actor, task_id, query: BoardQuery, storage, settings):
    task, workflow, member = await task_workflow.access(session, actor, task_id)
    actor.require("card:read")
    actor.require("job:read")
    extraction = await session.get(Job, query.extraction_job_id)
    if extraction is None or extraction.task_id != task_id or extraction.kind != "extract":
        raise not_found()
    if extraction.status != "succeeded":
        raise ServiceError("invalid_extraction_job", "Choose a succeeded extraction job", 422, 2)
    if not await task_events.visible_job(session, actor, extraction, storage):
        raise not_found()
    from app.services.task_board_projection import load, requirements_with_collaboration

    scoped = await requirements_with_collaboration(
        session, actor, task_id, extraction, limit=MAX_REQUIREMENTS + 1, member=member
    )
    requirements = [entry[0] for entry in scoped]
    if len(requirements) > MAX_REQUIREMENTS:
        raise ServiceError(
            "board_limit_exceeded", "Extraction exceeds 5000 saved requirements", 422, 2
        )
    watermark = await task_events.head(session, actor.org_id, task_id)
    as_of = datetime.now(UTC)
    day = as_of.date().isoformat()
    digest = query_hash(query)
    key = (
        validate_page(
            query.cursor,
            actor,
            task_id,
            workflow,
            settings,
            watermark.last_seq,
            purpose="board",
            day=day,
            query=digest,
        )
        if query.cursor
        else None
    )
    views = await load(session, actor, requirements, task_id, as_of.date())
    reviewers = list(
        await session.execute(
            select(TaskMember, Membership.role)
            .join(
                Membership,
                (Membership.org_id == TaskMember.org_id)
                & (Membership.user_id == TaskMember.user_id),
            )
            .join(User, User.id == Membership.user_id)
            .where(
                TaskMember.org_id == actor.org_id,
                TaskMember.task_id == task_id,
                TaskMember.active.is_(True),
                Membership.active.is_(True),
                User.active.is_(True),
            )
        )
    )
    reviewers = [
        SimpleNamespace(
            user_id=entry.user_id,
            review_domains=set(entry.review_domains) & set(task_workflow.domains(role)),
            can_edit=entry.role in {"owner", "contributor"} and "card:write" in ROLE_SCOPES[role],
            can_confirm_requirement=entry.role in {"owner", "contributor"}
            and "req:confirm" in ROLE_SCOPES[role],
            role=role,
        )
        for entry, role in reviewers
    ]
    jobs, _ = await job_page(session, actor, task_id, storage)
    admission_codes = {
        "insufficient_balance",
        "spend_cap_reached",
        "job_charge_limit_exceeded",
        "job_call_limit_exceeded",
    }
    job_blockers = set()
    for summary in jobs:
        saved = session.info["task_board_job_rows"][summary.job_id]
        if saved.status == "failed":
            job_blockers.add("job_failed")
        code = (saved.error or {}).get("code")
        if code in admission_codes:
            job_blockers.add(code)
    rows = []
    all_rows = []
    review_hints = {}
    for requirement, assignment, thread_count, mentioned_thread_id in scoped:
        view = views.get(requirement.id)
        row = row_projection(
            requirement,
            view,
            workflow,
            actor,
            member,
            reviewers,
            assignment=assignment,
            thread_count=thread_count,
            mentioned_thread_id=mentioned_thread_id,
        )
        if view is None and not session.info["board_requirement_citations"][requirement.id]:
            row = BoardRow.model_validate(
                {
                    **row.model_dump(mode="json"),
                    "blockers": [*row.blockers, "invalid_citation"],
                    "next_actions": [
                        BoardActionView.model_validate(
                            dict(
                                code="repair_citation",
                                target={"kind": "requirement", "id": requirement.id},
                                eligible_user_ids=[actor.user_id],
                            )
                        ).model_dump(mode="json")
                    ]
                    if member
                    and member.role in ("owner", "contributor")
                    and "req:extract" in actor.scopes
                    and "evidence:confirm" in actor.scopes
                    and actor.role == "admin"
                    and actor.actor_kind == "session"
                    and actor.token_id is None
                    and workflow.state == "active"
                    else [action.model_dump(mode="json") for action in row.next_actions],
                }
            )
        if job_blockers:
            row = BoardRow.model_validate(
                {
                    **row.model_dump(mode="json"),
                    "blockers": list(dict.fromkeys([*row.blockers, *sorted(job_blockers)])),
                    "next_actions": [
                        BoardActionView.model_validate(
                            dict(
                                code="resolve_budget",
                                target={"kind": "task", "id": task_id},
                                eligible_user_ids=[actor.user_id],
                            )
                        ).model_dump(mode="json")
                    ]
                    if job_blockers & admission_codes
                    and workflow.state == "active"
                    and actor.token_id is None
                    and actor.role in ("admin", "bidder")
                    and actor.actor_kind == "session"
                    and "task:budget:write" in actor.scopes
                    else [action.model_dump(mode="json") for action in row.next_actions],
                }
            )
        review = session.info["board_requirement_reviews"][requirement.id]
        from app.services.requirement_board import actor_hint

        review_hints[requirement.id] = (
            actor_hint(
                actor,
                workflow,
                member,
                reviewers,
                assignment,
                session.info["board_requirement_citations"][requirement.id],
            )
            if review.state != "confirmed"
            else None
        )
        if requirement_consumption.gap_reason(review):
            row = row.model_copy(
                update={
                    "bucket": "gap",
                    "eligibility": "unconfirmed" if row.eligibility is not None else None,
                    "blockers": list(dict.fromkeys([*row.blockers, "unconfirmed"])),
                    "next_actions": [
                        BoardActionView.model_validate(
                            dict(
                                code="view",
                                target={"kind": "requirement", "id": requirement.id},
                                eligible_user_ids=[],
                            )
                        )
                    ],
                }
            )
        all_rows.append(row)
        if matches(row, query, actor, bucket=False, mentioned=mentioned_thread_id is not None):
            rows.append(row)
    session.info["requirement_board_inputs"] = {
        "rows": all_rows,
        "requirements": requirements,
        "hints": review_hints,
        "workflow": workflow,
        "watermark": watermark,
        "mentioned": {entry[0].id for entry in scoped if entry[3] is not None},
    }
    # Eligibility and citation checks use shared pure validators; SQL performs
    # the grouped count of that bounded metadata set rather than per-row reads.
    groups = dict(
        (
            await session.execute(
                text(
                    "SELECT value,count(*) FROM jsonb_array_elements_text(CAST(:buckets AS jsonb)) GROUP BY value"
                ),
                {"buckets": json.dumps([row.bucket for row in rows])},
            )
        ).all()
    )
    counts = {
        "total": sum(groups.values()),
        **{
            name: groups.get(name, 0)
            for name in (
                "gap",
                "draft_card",
                "pending_review",
                "needs_material",
                "confirmed",
                "comply_only",
            )
        },
    }
    mentioned_requirements = {entry[0].id for entry in scoped if entry[3] is not None}
    matching = [
        row
        for row in rows
        if matches(row, query, actor, mentioned=row.requirement_id in mentioned_requirements)
    ]
    page = [row for row in matching if key is None or str(row.requirement_id) > key][: query.limit]
    remaining = bool(
        page and any(str(row.requirement_id) > str(page[-1].requirement_id) for row in matching)
    )
    recent = await activity(
        session, actor, task_id, storage, settings, limit=20, authorized=(workflow, watermark)
    )
    cursor = task_events.issue_cursor(actor, task_id, workflow, settings, seq=watermark.last_seq)
    budget = await budgets.view(session, task, settings)
    midnight = datetime.combine(as_of.date() + timedelta(days=1), datetime.min.time(), UTC)
    return BoardView(
        org_id=actor.org_id,
        task_id=task_id,
        extraction_job_id=extraction.id,
        workflow=workflow_view(workflow, cursor),
        counts=BoardCounts(**counts),
        matching=len(matching),
        returned=len(page),
        budget=budget,
        next_cursor=page_cursor(
            actor,
            task_id,
            workflow,
            settings,
            watermark.last_seq,
            purpose="board",
            day=day,
            query=digest,
            key=str(page[-1].requirement_id),
        )
        if remaining
        else None,
        event_cursor=cursor,
        as_of=as_of,
        refresh_by=min(midnight, as_of + timedelta(seconds=30)),
        rows=page,
        jobs=jobs,
        activity=recent.items,
    )
