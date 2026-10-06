"""Human requirement decisions, explicit manual scopes and current consumption pins."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import select, text

from app.core.config import Settings
from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.models.entities import Chunk, Document, Job, Membership, Requirement, User
from app.models.requirement_confirmation import (
    RequirementReview,
    RequirementReviewEvent,
    RequirementReviewRequest,
    RequirementReviewSet,
)
from app.models.team_workflow import RequirementWorkflow, TaskMember
from app.schemas import requirement_confirmation as schema
from app.schemas.contracts import ExtractedRequirement
from app.services.extraction import fingerprint
from app.services.requirement_source import (
    canonical,
    content_of,
    digest,
    effective_reviews,
    raw_digest,
    verify,
)
from app.services.task_workflow import access, fail
from app.services.versioned import audit

__all__ = [
    "effective_reviews",
    "show",
    "list_reviews",
    "history",
    "rejected",
    "preview_manual",
    "add_manual",
    "decide",
    "confirm_batch",
    "consumption_manifest",
    "seed_reviews",
]


def config(settings):
    return settings if settings is not None else Settings.model_validate({})


def seal(settings, org_id, record_id, payload):
    return Secrets.for_data(settings).encrypt(
        canonical({"org_id": str(org_id), "record_id": str(record_id), "payload": payload})
    )


def unseal(settings, org_id, record_id, ciphertext):
    value = json.loads(Secrets.for_data(settings).decrypt(ciphertext))
    if (value.get("org_id"), value.get("record_id")) != (str(org_id), str(record_id)):
        fail("content_integrity", "Encrypted review record binding differs", 409, 4)
    return value["payload"]


async def requirement(session, actor, requirement_id, *, write=False, settings=None):
    req = await session.get(Requirement, requirement_id, populate_existing=True)
    if req is None or req.org_id != actor.org_id:
        raise not_found()
    await access(
        session,
        actor,
        req.task_id,
        "req:confirm" if write else "task:read",
        write=write,
        require_member=write,
    )
    if write:
        metadata = await session.scalar(
            select(RequirementReview.id).where(RequirementReview.requirement_id == req.id)
        )
        if metadata is None:
            await seed_reviews(
                session, actor, req.task_id, req.job_id, settings=config(settings), origin="legacy"
            )
        await scope_row(session, actor, req.task_id, req.job_id, lock=True)
        req = await session.scalar(
            select(Requirement)
            .where(Requirement.id == req.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    return req


async def scope_row(session, actor, task_id, job_id, *, lock=False):
    job = await session.get(Job, job_id, populate_existing=True)
    if job is None or (job.org_id, job.task_id, job.kind) != (actor.org_id, task_id, "extract"):
        raise not_found()
    if job.status != "succeeded":
        fail("invalid_extraction_job", "Choose a succeeded extraction scope")
    query = select(RequirementReviewSet).where(
        RequirementReviewSet.org_id == actor.org_id,
        RequirementReviewSet.extraction_job_id == job_id,
    )
    if lock:
        query = query.with_for_update()
    scope = await session.scalar(query.execution_options(populate_existing=True))
    if scope is None:
        if lock:
            fail(
                "requirement_review_missing", "Review metadata must be initialized before mutation"
            )
        scope = RequirementReviewSet(
            org_id=actor.org_id,
            task_id=task_id,
            extraction_job_id=job.id,
            document_id=job.document_id,
            origin="manual" if job.result.get("origin") == "manual" else "model",
            revision=1,
            membership_sha256=digest([]),
            confirmation_sha256=digest([]),
        )
    return scope


async def members(session, actor, task_id):
    rows = (
        await session.execute(
            select(TaskMember, Membership, User)
            .join(
                Membership,
                (Membership.org_id == TaskMember.org_id)
                & (Membership.user_id == TaskMember.user_id),
            )
            .join(User, User.id == TaskMember.user_id)
            .where(
                TaskMember.org_id == actor.org_id,
                TaskMember.task_id == task_id,
                TaskMember.active.is_(True),
                Membership.active.is_(True),
                User.active.is_(True),
                TaskMember.role.in_(["owner", "contributor"]),
                Membership.role.in_(["admin", "bidder", "technical"]),
            )
        )
    ).all()
    return {m.user_id: (m, om) for m, om, _ in rows}


async def render_many(session, actor, reqs, workflow, *, effective=None):
    if effective is None:
        effective = await effective_reviews(session, reqs)
    eligible = await members(session, actor, workflow.task_id)
    assigned = (
        {
            r.requirement_id: r.assignee_user_id
            for r in (
                await session.scalars(
                    select(RequirementWorkflow).where(
                        RequirementWorkflow.task_id == workflow.task_id,
                        RequirementWorkflow.requirement_id.in_([r.id for r in reqs]),
                    )
                )
            ).all()
        }
        if reqs
        else {}
    )
    out = []
    readable_jobs = {}
    for req in reqs:
        eff = effective[req.id]
        row = eff.stored
        hint = None
        if not eff.confirmed:
            action_people = (
                eligible
                if eff.citation_valid
                else {
                    user_id: person
                    for user_id, person in eligible.items()
                    if person[1].role == "admin"
                }
            )
            user_id = assigned.get(req.id)
            basis = "assignee"
            if user_id not in action_people:
                user_id, basis = workflow.owner_user_id, "owner"
            if user_id not in action_people:
                user_id, basis = None, "owner_recovery"
            action = "confirm_requirement" if eff.citation_valid else "repair_requirement"
            if workflow.state == "archived":
                action = "unarchive"
            if basis == "owner_recovery" and action != "unarchive":
                action = "assign"
            can = actor.actor_kind == "session" and actor.token_id is None
            if action == "confirm_requirement":
                can = can and actor.user_id in eligible and "req:confirm" in actor.scopes
            elif action == "repair_requirement":
                can = (
                    can
                    and actor.role == "admin"
                    and actor.user_id in eligible
                    and {"req:extract", "evidence:confirm"} <= actor.scopes
                )
            else:
                can = can and (actor.role == "admin" or actor.user_id == workflow.owner_user_id)
            hint = schema.RequirementActorHint(
                user_id=user_id, basis=basis, action=action, can_current_actor_act=can
            )
        reference = None
        if row and row.rejected_job_id:
            if row.rejected_job_id not in readable_jobs:
                job = await session.get(Job, row.rejected_job_id)
                try:
                    from app.services.jobs import read_access

                    if job is None:
                        raise not_found()
                    await read_access(session, actor, job, cast(Any, None))
                except ServiceError:
                    readable_jobs[row.rejected_job_id] = False
                else:
                    readable_jobs[row.rejected_job_id] = True
            if readable_jobs[row.rejected_job_id]:
                reference = schema.RejectedItemRef(
                    job_id=row.rejected_job_id,
                    index=row.rejected_index,
                    summary_sha256=row.rejected_summary_sha256,
                )
        out.append(
            schema.RequirementReviewView(
                org_id=req.org_id,
                task_id=req.task_id,
                extraction_job_id=req.job_id,
                requirement_id=req.id,
                origin=row.origin if row else "legacy",
                content=content_of(req),
                model_quote=req.model_quote,
                revision=eff.revision,
                review_hash=eff.review_hash,
                state=eff.state,
                citation_valid=eff.citation_valid,
                source_pin=eff.source_pin,
                confirmed_by_user_id=eff.confirmed_by_user_id,
                confirmed_at=eff.confirmed_at,
                rejected_item=reference,
                next_actor=hint,
            )
        )
    return out


def summary_rows(requirements, reviews):
    return [
        SimpleNamespace(
            requirement_id=req.id,
            origin=reviews[req.id].stored.origin if reviews[req.id].stored else "legacy",
            revision=reviews[req.id].revision,
            review_hash=reviews[req.id].review_hash,
            state=reviews[req.id].state,
            citation_valid=reviews[req.id].citation_valid,
        )
        for req in requirements
    ]


async def _scope_view(session, actor, scope, workflow, settings, *, views=None):
    from app.services.task_events import current_cursor

    if views is None:
        reqs = list(
            (
                await session.scalars(
                    select(Requirement)
                    .where(Requirement.job_id == scope.extraction_job_id)
                    .order_by(Requirement.id)
                )
            ).all()
        )
        if len(reqs) > 5000:
            fail("review_limit_exceeded", "Review scope exceeds 5000 requirements", 422)
        views = summary_rows(reqs, await effective_reviews(session, reqs))
    counts = {
        state: sum(v.state == state for v in views)
        for state in ("unconfirmed", "legacy_unconfirmed", "confirmed", "invalidated")
    }
    job = await session.get(Job, scope.extraction_job_id)
    rejected_count = None
    if job is not None:
        from app.services.jobs import read_access

        try:
            await read_access(session, actor, job, cast(Any, None))
        except ServiceError:
            pass
        else:
            if isinstance(job.result.get("rejected"), list):
                rejected_count = len(job.result["rejected"])
    return schema.RequirementSetView(
        org_id=actor.org_id,
        task_id=scope.task_id,
        extraction_job_id=scope.extraction_job_id,
        document_id=scope.document_id,
        origin=scope.origin,
        revision=scope.revision,
        membership_sha256=digest(
            [str(v.requirement_id) for v in sorted(views, key=lambda v: str(v.requirement_id))]
        ),
        confirmation_sha256=digest(
            [
                [str(v.requirement_id), v.revision, v.review_hash, v.state]
                for v in sorted(views, key=lambda v: str(v.requirement_id))
            ]
        ),
        summary=schema.ReviewSummary(
            total=len(views),
            confirmed=counts["confirmed"],
            unconfirmed=counts["unconfirmed"],
            legacy_unconfirmed=counts["legacy_unconfirmed"],
            invalidated=counts["invalidated"],
            invalid_citations=sum(not v.citation_valid for v in views),
            rejected_reported=rejected_count,
            manual_added=sum(v.origin.startswith("manual_") for v in views),
        ),
        last_event_cursor=await current_cursor(session, actor, scope.task_id, settings, workflow),
    )


async def show(session, actor, requirement_id, *, settings=None):
    settings = config(settings)
    req = await requirement(session, actor, requirement_id)
    _, workflow, _ = await access(session, actor, req.task_id)
    scope = await scope_row(session, actor, req.task_id, req.job_id)
    view = (await render_many(session, actor, [req], workflow))[0]
    bounded_page([view], 0, 1)
    return schema.RequirementReviewData(
        requirement=view, scope=await _scope_view(session, actor, scope, workflow, settings)
    )


def page_offset(query, actor, task_id, workflow, settings, purpose, binding):
    from app.services.task_events import open_cursor

    if query.cursor is None:
        return 0
    value = open_cursor(query.cursor, actor, task_id, workflow, settings, purpose=purpose)
    if value.get("binding") != binding:
        fail("invalid_cursor", "Cursor filters or source changed", 409)
    offset = value.get("offset")
    if type(offset) is not int or offset < 0:
        fail("invalid_cursor", "Invalid cursor offset", 422)
    return offset


def next_page(actor, task_id, workflow, settings, purpose, binding, offset, limit, total):
    from app.services.task_events import issue_cursor

    return (
        issue_cursor(
            actor,
            task_id,
            workflow,
            settings,
            purpose=purpose,
            binding=binding,
            offset=offset + limit,
        )
        if offset + limit < total
        else None
    )


def bounded_page(items, offset, limit):
    """Reserve envelope/cursor metadata and continue before crossing 512 KiB."""
    result, size = [], 8192
    for item in items[offset : offset + limit]:
        added = len(item.model_dump_json().encode()) + 1
        if size + added > 512 * 1024:
            if not result:
                fail(
                    "legacy_item_too_large",
                    "A saved record exceeds the review page byte limit",
                    422,
                )
            break
        result.append(item)
        size += added
    return result


async def list_reviews(session, actor, task_id, extraction_job_id, query, *, settings=None):
    settings = config(settings)
    _, workflow, _ = await access(session, actor, task_id)
    scope = await scope_row(session, actor, task_id, extraction_job_id)
    pairs = list(
        (
            await session.execute(
                select(Requirement, Chunk)
                .join(
                    Chunk, (Chunk.org_id == Requirement.org_id) & (Chunk.id == Requirement.chunk_id)
                )
                .where(
                    Requirement.org_id == actor.org_id,
                    Requirement.task_id == task_id,
                    Requirement.job_id == extraction_job_id,
                )
                .limit(5001)
            )
        ).all()
    )
    if len(pairs) > 5000:
        fail("review_limit_exceeded", "Review scope exceeds 5000 requirements", 422)

    def reading_order(pair):
        req, chunk = pair
        blocks = [block["block_id"] for block in chunk.blocks or []]
        block = (req.location or {}).get("block_id")
        return (
            str(req.document_id),
            chunk.seq,
            blocks.index(block) if block in blocks else 0,
            req.created_at,
            str(req.id),
        )

    reqs = [req for req, _ in sorted(pairs, key=reading_order)]
    effective = await effective_reviews(session, reqs)
    scoped = await _scope_view(
        session, actor, scope, workflow, settings, views=summary_rows(reqs, effective)
    )
    if query.rejected_job_id is not None:
        await rejection_job(session, actor, task_id, query.rejected_job_id)
    filtered = []
    for req in reqs:
        value = effective[req.id]
        stored = value.stored
        if (
            (query.state is None or value.state == query.state)
            and (query.category is None or req.category == query.category)
            and (query.starred is None or req.starred == query.starred)
            and (query.origin is None or (stored.origin if stored else "legacy") == query.origin)
            and (
                query.rejected_job_id is None
                or stored is not None
                and stored.rejected_job_id == query.rejected_job_id
                and stored.rejected_index == query.rejected_index
            )
        ):
            filtered.append(req)
    binding = digest(
        [
            str(extraction_job_id),
            scoped.confirmation_sha256,
            query.model_dump(mode="json", exclude={"cursor", "limit"}),
        ]
    )
    offset = page_offset(query, actor, task_id, workflow, settings, "requirement-reviews", binding)
    candidates = await render_many(
        session, actor, filtered[offset : offset + query.limit], workflow, effective=effective
    )
    selected = bounded_page(candidates, 0, query.limit)
    cursor = next_page(
        actor,
        task_id,
        workflow,
        settings,
        "requirement-reviews",
        binding,
        offset,
        len(selected),
        len(filtered),
    )
    return schema.ReviewPageData(
        task_id=task_id,
        extraction_job_id=extraction_job_id,
        total=len(filtered),
        next_cursor=cursor,
        scope=scoped,
    ), selected


async def history(session, actor, requirement_id, query, *, settings=None):
    settings = config(settings)
    req = await requirement(session, actor, requirement_id)
    _, workflow, _ = await access(session, actor, req.task_id)
    events = list(
        (
            await session.scalars(
                select(RequirementReviewEvent)
                .where(RequirementReviewEvent.requirement_id == req.id)
                .order_by(RequirementReviewEvent.revision)
            )
        ).all()
    )
    binding = digest([str(req.id), len(events)])
    offset = page_offset(
        query, actor, req.task_id, workflow, settings, "requirement-history", binding
    )
    items = []
    for row in events[offset : offset + query.limit]:
        snapshot = unseal(settings, actor.org_id, row.review_id, row.snapshot_ciphertext)
        if digest(snapshot) != row.snapshot_sha256:
            fail("content_integrity", "Review snapshot hash differs", 409, 4)
        items.append(
            schema.RequirementReviewEvent(
                id=row.id,
                org_id=row.org_id,
                task_id=row.task_id,
                extraction_job_id=row.extraction_job_id,
                requirement_id=row.requirement_id,
                revision=row.revision,
                action=row.action,
                state_after=row.state_after,
                content=snapshot,
                review_hash=row.review_hash,
                source_pin=row.source_pin,
                actor_kind=row.actor_kind,
                actor_user_id=row.actor_user_id,
                reason_sha256=row.reason_sha256,
                request_id=row.request_id,
                occurred_at=row.occurred_at,
            )
        )
    items = bounded_page(items, 0, query.limit)
    return schema.PageData(
        task_id=req.task_id,
        extraction_job_id=req.job_id,
        total=len(events),
        next_cursor=next_page(
            actor,
            req.task_id,
            workflow,
            settings,
            "requirement-history",
            binding,
            offset,
            len(items),
            len(events),
        ),
    ), items


async def rejection_job(session, actor, task_id, job_id):
    from app.services.jobs import read_access

    job = await session.get(Job, job_id)
    if job is None or (job.org_id, job.task_id, job.kind) != (actor.org_id, task_id, "extract"):
        raise not_found()
    await read_access(session, actor, job, cast(Any, None))
    return job


async def rejected(session, actor, task_id, job_id, query, *, settings=None):
    settings = config(settings)
    _, workflow, _ = await access(session, actor, task_id)
    job = await rejection_job(session, actor, task_id, job_id)
    summaries = job.result.get("rejected", [])
    if not isinstance(summaries, list):
        fail("rejected_reference_changed", "Rejected receipt is unavailable", 409)
    binding = digest([str(job_id), summaries])
    offset = page_offset(query, actor, task_id, workflow, settings, "requirement-rejected", binding)
    linked = list(
        (
            await session.scalars(
                select(RequirementReview).where(RequirementReview.rejected_job_id == job_id)
            )
        ).all()
    )
    items = []
    for index in range(offset, min(offset + query.limit, len(summaries))):
        summary = summaries[index]
        ids = sorted([r.requirement_id for r in linked if r.rejected_index == index], key=str)
        items.append(
            schema.RejectedItemView(
                job_id=job_id,
                index=index,
                summary_sha256=digest(summary),
                position=summary["position"],
                quote=summary["quote"],
                reason=summary["reason"],
                entered_requirement_ids=ids[:100],
                entered_requirement_count=len(ids),
            )
        )
    items = bounded_page(items, 0, query.limit)
    return schema.PageData(
        task_id=task_id,
        extraction_job_id=job_id,
        total=len(summaries),
        next_cursor=next_page(
            actor,
            task_id,
            workflow,
            settings,
            "requirement-rejected",
            binding,
            offset,
            len(items),
            len(summaries),
        ),
    ), items


async def manual_parents(session, actor, task_id, request):
    """Authorize all named parents before exposing conflicts or source details."""
    source = request.content.source
    found = await session.scalar(
        select(Chunk.id)
        .join(Document, (Document.org_id == Chunk.org_id) & (Document.id == Chunk.document_id))
        .where(
            Chunk.org_id == actor.org_id,
            Chunk.task_id == task_id,
            Chunk.id == source.chunk_id,
            Document.task_id == task_id,
            Document.id == source.document_id,
        )
    )
    if found is None:
        raise not_found()
    if request.extraction_job_id is not None:
        scope = await scope_row(session, actor, task_id, request.extraction_job_id)
        if scope.document_id != source.document_id:
            raise not_found()
    if request.rejected_item is not None:
        job = await rejection_job(session, actor, task_id, request.rejected_item.job_id)
        if job.document_id != source.document_id:
            raise not_found()


async def preview_manual(session, actor, task_id, request, *, settings=None):
    await access(session, actor, task_id, "req:manual", write=True, lock=False, require_member=True)
    if len(request.model_dump_json().encode()) > 256 * 1024:
        fail("input_too_large", "Manual request exceeds 256 KiB", 422)
    await manual_parents(session, actor, task_id, request)
    pin = await verify(session, actor, task_id, request.content.source)
    scope = None
    duplicate = None
    if request.extraction_job_id:
        scope = await scope_row(session, actor, task_id, request.extraction_job_id)
        if scope.document_id != request.content.source.document_id:
            raise not_found()
        if scope.revision != request.expected_set_revision:
            fail("review_changed", "Requirement membership changed; refresh")
        duplicate = await session.scalar(
            select(Requirement.id).where(
                Requirement.job_id == scope.extraction_job_id,
                Requirement.fingerprint
                == fingerprint(ExtractedRequirement.model_validate(request.content.model_dump())),
            )
        )
    if request.rejected_item:
        ref = request.rejected_item
        job = await rejection_job(session, actor, task_id, ref.job_id)
        if job.document_id != request.content.source.document_id:
            raise not_found()
        entries = job.result.get("rejected", [])
        if (
            not isinstance(entries, list)
            or ref.index >= len(entries)
            or digest(entries[ref.index]) != ref.summary_sha256
        ):
            fail("rejected_reference_changed", "Rejected receipt changed; inspect it again")
    body = request.model_dump(mode="json", exclude={"request_id", "expected_preview_hash"})
    return schema.ManualEntryPreview(
        task_id=task_id,
        extraction_job_id=request.extraction_job_id,
        creates_manual_scope=scope is None,
        expected_set_revision=request.expected_set_revision,
        preview_hash=digest([str(actor.org_id), str(task_id), body, pin.model_dump(mode="json")]),
        verified_source=pin,
        duplicate_requirement_id=duplicate,
        estimated_cost=schema.Cost(billing_currency=config(settings).billing_currency),
    )


async def replay(session, actor, task_id, request, route, settings):
    # Serialize globally per org/user/request before touching a receipt in any task.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
        {"key": f"req-review:{actor.org_id}:{actor.user_id}:{request.request_id}"},
    )
    row = await session.scalar(
        select(RequirementReviewRequest).where(
            RequirementReviewRequest.org_id == actor.org_id,
            RequirementReviewRequest.actor_user_id == actor.user_id,
            RequirementReviewRequest.request_id == request.request_id,
        )
    )
    request_hash = digest([route, str(task_id), request.model_dump(mode="json")])
    if row:
        if row.task_id != task_id or row.request_sha256 != request_hash:
            fail("idempotency_conflict", "Request ID is already bound to another operation")
        return unseal(settings, actor.org_id, row.id, row.receipt_ciphertext), request_hash
    return None, request_hash


def save_receipt(session, actor, task_id, request, action, request_hash, payload, settings):
    row_id = uuid4()
    session.add(
        RequirementReviewRequest(
            id=row_id,
            org_id=actor.org_id,
            task_id=task_id,
            actor_user_id=actor.user_id,
            request_id=request.request_id,
            action=action,
            request_sha256=request_hash,
            receipt_ciphertext=seal(settings, actor.org_id, row_id, payload),
        )
    )


async def seed_reviews(
    session,
    actor,
    task_id,
    extraction_job_id,
    *,
    settings,
    origin="extracted",
    manual_request=None,
    manual_requirement_id=None,
):
    """Publish encrypted seeds in the extract transaction; never reset existing decisions."""
    if origin == "legacy":
        await access(session, actor, task_id, "req:confirm", write=True, require_member=True)
    job = await session.get(Job, extraction_job_id)
    if job is None or (job.org_id, job.task_id, job.kind, job.status) != (
        actor.org_id,
        task_id,
        "extract",
        "succeeded",
    ):
        raise not_found()
    scope = await session.scalar(
        select(RequirementReviewSet).where(
            RequirementReviewSet.org_id == actor.org_id,
            RequirementReviewSet.extraction_job_id == job.id,
        )
    )
    if scope is None:
        scope = RequirementReviewSet(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task_id,
            extraction_job_id=job.id,
            document_id=job.document_id,
            origin="manual" if origin.startswith("manual") else "model",
            revision=1,
            membership_sha256=digest([]),
            confirmation_sha256=digest([]),
        )
        session.add(scope)
        await session.flush()
    reqs = list(
        (
            await session.scalars(
                select(Requirement).where(Requirement.job_id == job.id).order_by(Requirement.id)
            )
        ).all()
    )
    if manual_request is not None:
        # A manual add owns only its new ID, never historical model rows.
        if manual_requirement_id is None:
            fail("invalid_manual_entry", "Manual seed requires its newly inserted requirement", 422)
        reqs = [req for req in reqs if req.id == manual_requirement_id]
    states = await effective_reviews(session, reqs)
    for req in reqs:
        if states[req.id].stored is not None:
            continue
        eff = states[req.id]
        rid, eid = uuid4(), uuid4()
        snapshot = content_of(req).model_dump(mode="json")
        ciphertext = seal(settings, actor.org_id, rid, snapshot)
        pin = eff.source_pin.model_dump(mode="json") if eff.source_pin else None
        row = RequirementReview(
            id=rid,
            org_id=actor.org_id,
            task_id=task_id,
            extraction_job_id=job.id,
            requirement_id=req.id,
            origin=origin,
            revision=1,
            current_event_id=eid,
            state="legacy_unconfirmed" if origin == "legacy" else "unconfirmed",
            review_hash=eff.review_hash,
            source_pin=pin,
            snapshot_ciphertext=ciphertext,
            rejected_job_id=manual_request.rejected_item.job_id
            if manual_request and manual_request.rejected_item
            else None,
            rejected_index=manual_request.rejected_item.index
            if manual_request and manual_request.rejected_item
            else None,
            rejected_summary_sha256=manual_request.rejected_item.summary_sha256
            if manual_request and manual_request.rejected_item
            else None,
        )
        session.add(row)
        await session.flush()
        session.add(
            RequirementReviewEvent(
                id=eid,
                org_id=actor.org_id,
                task_id=task_id,
                extraction_job_id=job.id,
                requirement_id=req.id,
                review_id=rid,
                revision=1,
                action="manual_add" if manual_request else "seed",
                state_after="legacy_unconfirmed" if origin == "legacy" else "unconfirmed",
                review_hash=eff.review_hash,
                snapshot_sha256=digest(snapshot),
                snapshot_ciphertext=ciphertext,
                source_pin=pin,
                actor_kind="session" if manual_request or origin == "legacy" else "worker",
                actor_user_id=actor.user_id if manual_request or origin == "legacy" else None,
                request_id=manual_request.request_id if manual_request else None,
                reason_sha256=raw_digest(manual_request.reason) if manual_request else None,
                reason_ciphertext=seal(
                    settings, actor.org_id, rid, {"reason": manual_request.reason}
                )
                if manual_request
                else None,
                occurred_at=datetime.now(UTC),
            )
        )
        await session.flush()
    await session.refresh(scope)
    return scope


async def apply_decision(session, actor, req, body, settings, *, effective=None):
    eff = effective if effective is not None else (await effective_reviews(session, [req]))[req.id]
    row = eff.stored
    if row is None:
        fail("requirement_review_missing", "Requirement review metadata is missing")
    if (body.expected_revision, body.expected_review_hash) != (eff.revision, eff.review_hash):
        fail("review_changed", "Requirement changed; review the current content")
    if body.action == "confirm":
        if eff.confirmed:
            fail("invalid_review_transition", "Requirement is already confirmed")
        if not eff.citation_valid:
            fail("invalid_citation", "Repair the citation before confirmation", 422)
    elif not eff.confirmed:
        fail("invalid_review_transition", "Only a confirmed requirement can be reopened")
    before_revision, before_hash = row.revision, row.review_hash
    row.revision += 1
    row.current_event_id = uuid4()
    row.state = "confirmed" if body.action == "confirm" else "unconfirmed"
    row.review_hash = eff.review_hash
    row.source_pin = eff.source_pin.model_dump(mode="json") if eff.source_pin else None
    row.confirmed_by_user_id = actor.user_id if row.state == "confirmed" else None
    row.confirmed_at = datetime.now(UTC) if row.state == "confirmed" else None
    snapshot = content_of(req).model_dump(mode="json")
    row.snapshot_ciphertext = seal(settings, actor.org_id, row.id, snapshot)
    session.add(
        RequirementReviewEvent(
            id=row.current_event_id,
            org_id=actor.org_id,
            task_id=req.task_id,
            extraction_job_id=req.job_id,
            requirement_id=req.id,
            review_id=row.id,
            revision=row.revision,
            action=body.action,
            state_after=row.state,
            review_hash=row.review_hash,
            snapshot_sha256=digest(snapshot),
            snapshot_ciphertext=row.snapshot_ciphertext,
            reason_ciphertext=seal(settings, actor.org_id, row.id, {"reason": body.reason}),
            reason_sha256=raw_digest(body.reason),
            source_pin=row.source_pin,
            actor_kind="session",
            actor_user_id=actor.user_id,
            request_id=body.request_id,
            occurred_at=datetime.now(UTC),
        )
    )
    audit(
        session,
        actor,
        "requirement.confirmed" if body.action == "confirm" else "requirement.reopened",
        req.id,
        {
            "task_id": str(req.task_id),
            "extraction_job_id": str(req.job_id),
            "revision": row.revision,
            "before_revision": before_revision,
            "after_revision": row.revision,
            "before_review_hash": before_hash,
            "after_review_hash": row.review_hash,
            "actor_kind": actor.actor_kind,
            "request_id": str(body.request_id),
            "reason_sha256": raw_digest(body.reason),
        },
    )
    await session.flush()
    return {
        "requirement_id": str(req.id),
        "event_id": str(row.current_event_id),
        "confirmed_revision": row.revision,
    }


async def decide(session, actor, requirement_id, request, *, settings=None):
    settings = config(settings)
    req = await requirement(session, actor, requirement_id, write=True, settings=settings)
    receipt, request_hash = await replay(
        session, actor, req.task_id, request, f"requirement:{req.id}:{request.action}", settings
    )
    if receipt is None:
        item = await apply_decision(session, actor, req, request, settings)
        receipt = {"requirement_id": str(req.id), "event_ids": [item["event_id"]]}
        save_receipt(
            session, actor, req.task_id, request, request.action, request_hash, receipt, settings
        )
        await session.flush()
        replayed = False
    else:
        replayed = True
    result = await show(session, actor, req.id, settings=settings)
    return result.model_copy(
        update={
            "request_id": request.request_id,
            "event_ids": [UUID(v) for v in receipt["event_ids"]],
            "replayed": replayed,
        }
    )


async def confirm_batch(session, actor, task_id, extraction_job_id, request, *, settings=None):
    settings = config(settings)
    _, workflow, member = await access(
        session, actor, task_id, "req:confirm", write=True, require_member=True
    )
    if member is None or member.role != "owner":
        fail("forbidden", "Batch confirmation requires the task owner", 403, 4)
    await scope_row(session, actor, task_id, extraction_job_id)
    ids = [item.requirement_id for item in request.items]
    reqs = list(
        (
            await session.scalars(
                select(Requirement)
                .where(
                    Requirement.org_id == actor.org_id,
                    Requirement.task_id == task_id,
                    Requirement.job_id == extraction_job_id,
                    Requirement.id.in_(ids),
                )
                .order_by(Requirement.id)
                .with_for_update()
            )
        ).all()
    )
    if len(reqs) != len(ids):
        raise not_found()
    existing_scope = await session.scalar(
        select(RequirementReviewSet.id).where(
            RequirementReviewSet.extraction_job_id == extraction_job_id
        )
    )
    if existing_scope is None:
        await seed_reviews(
            session, actor, task_id, extraction_job_id, settings=settings, origin="legacy"
        )
    scope = await scope_row(session, actor, task_id, extraction_job_id, lock=True)
    receipt, request_hash = await replay(
        session, actor, task_id, request, f"batch:{extraction_job_id}", settings
    )
    replayed = receipt is not None
    if receipt is None:
        if scope.revision != request.expected_set_revision:
            fail("review_changed", "Requirement set changed; refresh before confirming")
        by_id = {item.requirement_id: item for item in request.items}
        effective = await effective_reviews(session, reqs)
        items = []
        for req in reqs:
            target = by_id[req.id]
            items.append(
                await apply_decision(
                    session,
                    actor,
                    req,
                    schema.RequirementDecision(
                        request_id=request.request_id,
                        action="confirm",
                        expected_revision=target.expected_revision,
                        expected_review_hash=target.expected_review_hash,
                        reason=request.reason,
                    ),
                    settings,
                    effective=effective[req.id],
                )
            )
        receipt = {"items": items}
        save_receipt(
            session, actor, task_id, request, "confirm_batch", request_hash, receipt, settings
        )
        await session.refresh(scope)
        audit(
            session,
            actor,
            "requirement.confirm_batch",
            task_id,
            {
                "task_id": str(task_id),
                "extraction_job_id": str(extraction_job_id),
                "request_id": str(request.request_id),
                "actor_kind": actor.actor_kind,
                "before_set_revision": request.expected_set_revision,
                "after_set_revision": scope.revision,
                "changed": len(items),
                "reason_sha256": raw_digest(request.reason),
                "requirement_ids": [item["requirement_id"] for item in items],
            },
        )
        await session.flush()
    current = await effective_reviews(session, reqs)
    items = []
    for item in receipt["items"]:
        rid = UUID(item["requirement_id"])
        value = current[rid]
        items.append(
            schema.ConfirmationReceiptItem(
                **item, current_revision=value.revision, current_state=value.state
            )
        )
    await session.refresh(scope)
    return schema.ConfirmationBatchData(
        scope=await _scope_view(session, actor, scope, workflow, settings),
        request_id=request.request_id,
        event_ids=[i.event_id for i in items],
        changed=len(items),
        replayed=replayed,
    ), items


async def add_manual(session, actor, task_id, request, *, settings=None):
    settings = config(settings)
    await access(session, actor, task_id, "req:manual", write=True, require_member=True)
    await manual_parents(session, actor, task_id, request)
    receipt, request_hash = await replay(session, actor, task_id, request, "manual", settings)
    replayed = receipt is not None
    if receipt is None:
        if request.extraction_job_id:
            initialized = await session.scalar(
                select(RequirementReviewSet.id).where(
                    RequirementReviewSet.extraction_job_id == request.extraction_job_id
                )
            )
            if initialized is None:
                await seed_reviews(
                    session,
                    actor,
                    task_id,
                    request.extraction_job_id,
                    settings=settings,
                    origin="legacy",
                )
            await scope_row(session, actor, task_id, request.extraction_job_id, lock=True)
        preview = await preview_manual(session, actor, task_id, request, settings=settings)
        if preview.preview_hash != request.expected_preview_hash:
            fail("manual_preview_changed", "Source or manual input changed; preview again")
        if preview.duplicate_requirement_id:
            fail(
                "duplicate_requirement", f"Existing requirement: {preview.duplicate_requirement_id}"
            )
        content = request.content
        job_id = request.extraction_job_id
        if job_id is None:
            job_id = uuid4()
            session.add(
                Job(
                    id=job_id,
                    org_id=actor.org_id,
                    task_id=task_id,
                    document_id=content.source.document_id,
                    kind="extract",
                    status="succeeded",
                    attempts=0,
                    cache_key=digest(
                        [
                            "manual-extract-v1",
                            str(actor.org_id),
                            str(task_id),
                            str(content.source.document_id),
                            str(request.request_id),
                        ]
                    ),
                    result={"origin": "manual", "created": 0, "rejected": []},
                    finished_at=datetime.now(UTC),
                    actor_user_id=actor.user_id,
                    actor_kind="session",
                    actor_scopes=sorted(actor.scopes),
                    command="req add",
                )
            )
            await session.flush()
        req = Requirement(
            id=uuid4(),
            org_id=actor.org_id,
            task_id=task_id,
            job_id=job_id,
            document_id=content.source.document_id,
            chunk_id=content.source.chunk_id,
            page=content.source.page,
            location=content.source.location.model_dump(mode="json")
            if content.source.location
            else None,
            quote=content.source.quote,
            text=content.text,
            category=content.category.value,
            starred=content.starred,
            condition=content.condition,
            fingerprint=fingerprint(ExtractedRequirement.model_validate(content.model_dump())),
        )
        session.add(req)
        await session.flush()
        # Seed is explicitly manual; neither this job nor its review creates model work.
        await seed_reviews(
            session,
            actor,
            task_id,
            job_id,
            settings=settings,
            origin="manual_rejected" if request.rejected_item else "manual_missing",
            manual_request=request,
            manual_requirement_id=req.id,
        )
        row = await session.scalar(
            select(RequirementReview).where(RequirementReview.requirement_id == req.id)
        )
        receipt = {
            "requirement_id": str(req.id),
            "event_ids": [str(row.current_event_id)],
            "created_scope": request.extraction_job_id is None,
        }
        save_receipt(
            session, actor, task_id, request, "manual_add", request_hash, receipt, settings
        )
        audit(
            session,
            actor,
            "requirement.manual_added",
            req.id,
            {
                "task_id": str(task_id),
                "extraction_job_id": str(job_id),
                "actor_kind": actor.actor_kind,
                "before_revision": None,
                "after_revision": row.revision,
                "before_review_hash": None,
                "after_review_hash": row.review_hash,
                "request_id": str(request.request_id),
                "reason_sha256": raw_digest(request.reason),
            },
        )
        await session.flush()
    result = await show(session, actor, UUID(receipt["requirement_id"]), settings=settings)
    return schema.ManualEntryData(
        **result.model_dump(exclude={"request_id", "event_ids", "replayed"}),
        request_id=request.request_id,
        event_ids=receipt["event_ids"],
        replayed=replayed,
        created_scope=receipt["created_scope"],
    )


async def consumption_manifest(session, actor, task_id, extraction_job_id, *, settings=None):
    settings = config(settings)
    _, workflow, _ = await access(session, actor, task_id)
    scope = await scope_row(session, actor, task_id, extraction_job_id)
    reqs = list(
        (
            await session.scalars(
                select(Requirement)
                .where(Requirement.job_id == extraction_job_id)
                .order_by(Requirement.id)
                .limit(2001)
            )
        ).all()
    )
    if len(reqs) > 2000:
        fail("review_limit_exceeded", "Consumption scope exceeds 2000 requirements", 422)
    views = await render_many(session, actor, reqs, workflow)
    value = await _scope_view(session, actor, scope, workflow, settings, views=views)
    return schema.RequirementConsumptionManifest(
        task_id=task_id,
        extraction_job_id=extraction_job_id,
        set_revision=value.revision,
        membership_sha256=value.membership_sha256,
        confirmation_sha256=value.confirmation_sha256,
        entries=[
            schema.RequirementConsumptionEntry(
                requirement_id=v.requirement_id,
                review_revision=v.revision,
                review_hash=v.review_hash,
                state=v.state,
                source_binding_sha256=v.source_pin.binding_sha256 if v.source_pin else None,
                disposition="accepted" if v.state == "confirmed" else "gap",
            )
            for v in views
        ],
    )


async def scope_view(
    session, actor, task_id, extraction_job_id, *, settings=None, reviews=None, requirements=None
):
    """Public batched scope projection for board/history consumers."""
    settings = config(settings)
    _, workflow, _ = await access(session, actor, task_id)
    scope = await scope_row(session, actor, task_id, extraction_job_id)
    views = reviews
    if isinstance(reviews, dict):
        if requirements is None:
            requirements = list(
                (
                    await session.scalars(
                        select(Requirement)
                        .where(Requirement.job_id == extraction_job_id)
                        .order_by(Requirement.id)
                        .limit(5001)
                    )
                ).all()
            )
        views = summary_rows(requirements, reviews)
    elif views is None and requirements is not None:
        views = summary_rows(requirements, await effective_reviews(session, requirements))
    return await _scope_view(session, actor, scope, workflow, settings, views=views)
