"""Bounded read projections; business eligibility comes from current card rules."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import case, literal, select, text

from app.core.errors import ServiceError, not_found
from app.models.entities import Job
from app.schemas.task_board import (
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
)
from app.services import requirement_consumption, task_access, task_events

MAX_REQUIREMENTS = 5000
BAD_ELIGIBILITY = {"invalid_citation", "stale_material", "unclassified"}


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


def page_cursor(actor, task_id, settings, watermark, *, purpose, day, query="", key=None):
    return task_events.issue_cursor(
        actor, task_id, settings, purpose=purpose, seq=watermark, d=day, q=query, k=key
    )


def validate_page(cursor, actor, task_id, settings, watermark, *, purpose, day, query=""):
    data = task_events.open_cursor(cursor, actor, task_id, settings, purpose=purpose)
    if data["s"] != watermark or data.get("d") != day or data.get("q") != query:
        raise ServiceError("board_changed", "Snapshot changed; reload first page", 409, 2)
    return data.get("k")


async def progress(session, actor, task_id, query: TaskProgressQuery, storage, settings):
    await task_access.access(session, actor, task_id)
    actor.require("job:read")
    watermark = await task_events.head(session, task_id)
    as_of = datetime.now(UTC)
    key = (
        validate_page(
            query.cursor,
            actor,
            task_id,
            settings,
            watermark.last_seq,
            purpose="progress",
            day=as_of.date().isoformat(),
        )
        if query.cursor
        else None
    )
    items, next_key = await job_page(session, actor, task_id, storage, limit=query.limit, after=key)
    cursor = task_events.issue_cursor(actor, task_id, settings, seq=watermark.last_seq)
    return TaskProgressView(
        task_id=task_id,
        event_cursor=cursor,
        as_of=as_of,
        next_cursor=page_cursor(
            actor,
            task_id,
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
        await task_access.access(session, actor, task_id)
        watermark = await task_events.head(session, task_id)
    else:
        watermark = authorized
    actor.require("card:read")
    actor.require("job:read")
    day = datetime.now(UTC).date().isoformat()
    key = (
        validate_page(
            cursor,
            actor,
            task_id,
            settings,
            watermark.last_seq,
            purpose="activity",
            day=day,
        )
        if cursor
        else None
    )
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
    cards = select(ResponseCard.id).where(ResponseCard.task_id == task_id)
    task_jobs = select(Job.id).where(Job.task_id == task_id)
    # Existing card audits bind their object directly.
    # Job audits bind a real stored job rather than trusting arbitrary detail IDs.
    query = select(AuditLog).where(
        or_(
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
    job_ids = {row.object_id for row in rows if row.action not in card_actions}
    jobs = (
        {
            job.id: job
            for job in await session.scalars(
                select(Job).where(Job.task_id == task_id, Job.id.in_(job_ids))
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
        action = "card_changed" if card_id else "job_changed"
        projected.append(
            (
                row,
                BoardActivityView.model_validate(
                    dict(
                        id=row.id,
                        action=action,
                        card_id=card_id,
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
    actor,
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
        if any(evidence["confirmed_at"] is None for evidence in view["evidence"]):
            blockers.append("unconfirmed_evidence")
    domain = (
        view["review_domain"]
        if view
        else {"technical": "technical", "qualification": "commercial"}.get(requirement.category)
    )
    if view:
        blockers.extend(view.get("blockers", []))
    actions = []
    target = {
        "kind": "card" if view else "requirement",
        "id": view["id"] if view else requirement.id,
    }
    if "card:read" in actor.scopes:
        code = (
            "create_card"
            if view is None
            else "repair_citation"
            if "invalid_citation" in blockers
            else "refresh_material"
            if "stale_material" in blockers
            else "classify"
            if domain is None and actor.actor_kind == "session" and actor.token_id is None
            else "confirm"
            if bucket == "pending_review"
            and actor.actor_kind == "session"
            and actor.token_id is None
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
            "view": "card:read",
        }
        if code == "repair_citation" and not (
            actor.actor_kind == "session"
            and actor.token_id is None
            and "evidence:confirm" in actor.scopes
        ):
            code = "view"
        if scopes[code] in actor.scopes:
            actions.append(
                BoardActionView.model_validate(
                    dict(
                        code=code,
                        target={"kind": "requirement", "id": requirement.id}
                        if code == "repair_citation"
                        else target,
                    )
                )
            )
    annotation = view.get("annotation") if view else None
    if annotation and view is not None:
        can_create = (
            actor.actor_kind == "session"
            and actor.token_id is None
            and "evidence:annotate" in actor.scopes
        )
        action_code = None
        action_target = {"kind": "annotation", "id": annotation["annotation_id"]}
        if not annotation["source_current"]:
            blockers.append("annotation_source_stale")
            if can_create:
                action_code = "replace_annotation_source"
        elif not annotation["attached"] and can_create:
            action_code = "inspect_annotated_material"
        elif (
            view["state"] == "pending_review"
            and actor.actor_kind == "session"
            and actor.token_id is None
            and "evidence:confirm" in actor.scopes
        ):
            action_code = "confirm_annotated_material"
        elif view["state"] == "confirmed" and not annotation["released"]:
            failed = annotation["job_status"] in {"failed", "cancelled"}
            blockers.append("annotation_release_failed" if failed else "annotation_release_pending")
            if failed and can_create:
                action_code = "retry_annotation_release"
            elif annotation["job_id"]:
                action_code = "view_job"
                action_target = {"kind": "job", "id": annotation["job_id"]}
        if action_code:
            actions.insert(
                0,
                BoardActionView.model_validate(
                    dict(
                        code=action_code,
                        target=action_target,
                    )
                ),
            )
    if not actions:
        actions = [BoardActionView.model_validate(dict(code="view", target=target))]
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
        )
    )


def matches(row, query, actor, *, bucket=True):
    return (
        (not bucket or query.bucket is None or row.bucket == query.bucket)
        and (query.category is None or row.category == query.category)
        and (query.starred is None or row.starred == query.starred)
        and (query.review_domain is None or row.review_domain == query.review_domain)
        and (query.blocker is None or query.blocker in row.blockers)
        and (
            not query.mine
            or (
                actor.token_id is None
                and actor.actor_kind == "session"
                and any((action.code in {"confirm", "reopen"}) for action in row.next_actions)
            )
        )
    )


async def board(
    session, actor, task_id, query: BoardQuery, storage, settings, *, review_details=False
):
    await task_access.access(session, actor, task_id)
    actor.require("card:read")
    actor.require("job:read")
    extraction = await session.get(Job, query.extraction_job_id)
    if extraction is None or extraction.task_id != task_id or extraction.kind != "extract":
        raise not_found()
    if extraction.status != "succeeded":
        raise ServiceError("invalid_extraction_job", "Choose a succeeded extraction job", 422, 2)
    if not await task_events.visible_job(session, actor, extraction, storage):
        raise not_found()
    from app.services.task_board_projection import load, scoped_requirements

    scoped = await scoped_requirements(
        session, actor, task_id, extraction, limit=MAX_REQUIREMENTS + 1
    )
    requirements = scoped
    if len(requirements) > MAX_REQUIREMENTS:
        raise ServiceError(
            "board_limit_exceeded", "Extraction exceeds 5000 saved requirements", 422, 2
        )
    watermark = await task_events.head(session, task_id)
    as_of = datetime.now(UTC)
    day = as_of.date().isoformat()
    digest = query_hash(query)
    key = (
        validate_page(
            query.cursor,
            actor,
            task_id,
            settings,
            watermark.last_seq,
            purpose="board",
            day=day,
            query=digest,
        )
        if query.cursor
        else None
    )
    views = await load(
        session, actor, requirements, task_id, as_of.date(), review_details=review_details
    )
    jobs, _ = await job_page(session, actor, task_id, storage)
    admission_codes = {
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
    for requirement in requirements:
        view = views.get(requirement.id)
        row = row_projection(
            requirement,
            view,
            actor,
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
                            )
                        ).model_dump(mode="json")
                    ]
                    if "req:extract" in actor.scopes
                    and "evidence:confirm" in actor.scopes
                    and actor.actor_kind == "session"
                    and actor.token_id is None
                    else [action.model_dump(mode="json") for action in row.next_actions],
                }
            )
        if job_blockers:
            row = BoardRow.model_validate(
                {
                    **row.model_dump(mode="json"),
                    "blockers": list(dict.fromkeys([*row.blockers, *sorted(job_blockers)])),
                }
            )
        review = session.info["board_requirement_reviews"][requirement.id]
        from app.services.requirement_board import actor_hint

        review_hints[requirement.id] = (
            actor_hint(
                actor,
                session.info["board_requirement_citations"][requirement.id],
            )
            if review_details and review.state != "confirmed"
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
                            )
                        )
                    ],
                }
            )
        all_rows.append(row)
        if matches(row, query, actor, bucket=False):
            rows.append(row)
    session.info["requirement_board_inputs"] = {
        "rows": all_rows,
        "requirements": requirements,
        "hints": review_hints,
        "watermark": watermark,
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
    matching = [row for row in rows if matches(row, query, actor)]
    page = [row for row in matching if key is None or str(row.requirement_id) > key][: query.limit]
    remaining = bool(
        page and any(str(row.requirement_id) > str(page[-1].requirement_id) for row in matching)
    )
    recent = await activity(
        session, actor, task_id, storage, settings, limit=20, authorized=watermark
    )
    cursor = task_events.issue_cursor(actor, task_id, settings, seq=watermark.last_seq)
    midnight = datetime.combine(as_of.date() + timedelta(days=1), datetime.min.time(), UTC)
    return BoardView(
        task_id=task_id,
        extraction_job_id=extraction.id,
        counts=BoardCounts(**counts),
        matching=len(matching),
        returned=len(page),
        next_cursor=page_cursor(
            actor,
            task_id,
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
