"""Explicit v4 requirement-review overlay over the legacy-safe response board."""

from collections import Counter
from types import SimpleNamespace

from sqlalchemy import select

from app.models.entities import AuditLog, Requirement
from app.schemas.requirement_confirmation import (
    RequirementActivityView,
    RequirementActorHint,
    RequirementBoardCounts,
    RequirementBoardData,
    RequirementBoardItem,
    RequirementBoardOverlay,
    RequirementProgressData,
)
from app.schemas.task_board import BoardCounts, BoardData, BoardQuery, TaskProgressData
from app.services import requirement_confirmation, requirement_consumption, task_board


def actor_hint(actor, citation_valid):
    action = "confirm_requirement" if citation_valid else "repair_requirement"
    required = {"req:confirm"} if citation_valid else {"req:extract", "evidence:confirm"}
    can_act = actor.actor_kind == "session" and actor.token_id is None and required <= actor.scopes
    return RequirementActorHint(action=action, can_current_actor_act=can_act)


async def activity(session, actor, task_id, extraction_id):
    actions = (
        "requirement.manual_added",
        "requirement.confirmed",
        "requirement.reopened",
        "requirement.invalidated",
        "requirement.repair_citation",
    )
    rows = await session.execute(
        select(AuditLog, Requirement.job_id)
        .join(Requirement, Requirement.id == AuditLog.object_id)
        .where(
            Requirement.task_id == task_id,
            Requirement.job_id == extraction_id,
            AuditLog.action.in_(actions),
        )
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(20)
    )
    return [
        RequirementActivityView(
            id=row.id,
            action=row.action,
            requirement_id=row.object_id,
            extraction_job_id=job_id,
            created_at=row.created_at,
        )
        for row, job_id in rows
    ]


async def board(session, actor, task_id, query, storage, settings):
    # One bounded graph supplies full-scope counts, response rows and B02 states.
    legacy = await task_board.board(
        session,
        actor,
        task_id,
        BoardQuery(extraction_job_id=query.extraction_job_id, limit=100),
        storage,
        settings,
        review_details=True,
    )
    inputs = session.info["requirement_board_inputs"]
    reviews = session.info["board_requirement_reviews"]
    citations = session.info["board_requirement_citations"]
    overlays = {}
    for row in inputs["rows"]:
        state = reviews[row.requirement_id]
        reason = requirement_consumption.gap_reason(state)
        hint = inputs["hints"][row.requirement_id]
        overlays[row.requirement_id] = RequirementBoardOverlay.model_validate(
            dict(
                requirement_id=row.requirement_id,
                state=state.state,
                bucket="requirement_review" if reason else row.bucket,
                next_action=hint.action
                if hint and hint.action in {"confirm_requirement", "repair_requirement"}
                else "view"
                if hint
                else row.next_actions[0].code,
                next_actor=hint,
                blockers=([reason] if reason else [])
                + ([] if citations[row.requirement_id] else ["invalid_citation"]),
                counts_as_response_complete=not reason
                and row.bucket in {"confirmed", "comply_only"},
            )
        )

    def matches(row):
        overlay = overlays[row.requirement_id]
        ordinary = query.model_copy(update={"bucket": None, "blocker": None})
        return (
            task_board.matches(row, ordinary, actor)
            and (query.bucket is None or overlay.bucket == query.bucket)
            and (
                query.blocker is None
                or query.blocker in overlay.blockers
                or query.blocker in row.blockers
            )
        )

    matching = [row for row in inputs["rows"] if matches(row)]
    digest = task_board.query_hash(query)
    watermark = inputs["watermark"]
    day = legacy.as_of.date().isoformat()
    key = (
        task_board.validate_page(
            query.cursor,
            actor,
            task_id,
            settings,
            watermark.last_seq,
            purpose="requirement-board",
            day=day,
            query=digest,
        )
        if query.cursor
        else None
    )
    page = [row for row in matching if key is None or str(row.requirement_id) > key][: query.limit]
    more = bool(
        page and any(str(row.requirement_id) > str(page[-1].requirement_id) for row in matching)
    )
    cursor = (
        task_board.page_cursor(
            actor,
            task_id,
            settings,
            watermark.last_seq,
            purpose="requirement-board",
            day=day,
            query=digest,
            key=str(page[-1].requirement_id),
        )
        if more
        else None
    )
    response_counts = Counter(
        row.bucket for row in inputs["rows"] if reviews[row.requirement_id].confirmed
    )
    counts = BoardCounts(
        total=sum(response_counts.values()),
        **{name: response_counts[name] for name in BoardCounts.model_fields if name != "total"},
    )
    scope = await requirement_confirmation.scope_view(
        session,
        actor,
        task_id,
        query.extraction_job_id,
        settings=settings,
        reviews=[
            SimpleNamespace(
                requirement_id=req.id,
                revision=reviews[req.id].revision,
                review_hash=reviews[req.id].review_hash,
                state=reviews[req.id].state,
                citation_valid=reviews[req.id].citation_valid,
                origin=reviews[req.id].stored.origin if reviews[req.id].stored else "legacy",
            )
            for req in inputs["requirements"]
        ],
        requirements=inputs["requirements"],
    )
    data = RequirementBoardData(
        board=BoardData.model_validate(
            {
                **legacy.model_dump(mode="json", exclude={"rows"}),
                "returned": len(page),
                "matching": len(matching),
                "next_cursor": cursor,
            }
        ),
        scope=scope,
        buckets=RequirementBoardCounts(
            total=len(inputs["rows"]),
            requirement_review=len(inputs["rows"]) - counts.total,
            responses=counts,
        ),
        requirement_activity=await activity(session, actor, task_id, query.extraction_job_id),
    )
    return data, [
        RequirementBoardItem(response=row, requirement=overlays[row.requirement_id]) for row in page
    ]


async def progress(session, actor, task_id, extraction_id, query, storage, settings):
    view = await task_board.progress(session, actor, task_id, query, storage, settings)
    scope = await requirement_confirmation.scope_view(
        session, actor, task_id, extraction_id, settings=settings
    )
    return RequirementProgressData(
        progress=TaskProgressData.model_validate(view.model_dump(mode="json", exclude={"jobs"})),
        scope=scope,
    ), view.jobs
