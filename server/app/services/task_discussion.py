"""Atomic human assignment and append-only encrypted card discussions.

Task/workflow locks serialize membership, archive, assignment and request receipts.
Database producers insert invalidations in the same transaction; comments never
write a card revision, evidence, model input or external notification.
"""

import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from cryptography.fernet import InvalidToken
from pydantic import ValidationError
from sqlalchemy import func, literal, select, tuple_

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.models.entities import Job, Membership, Requirement, User
from app.models.response_cards import ResponseCard
from app.models.team_workflow import (
    CardComment,
    CardCommentMention,
    CardCommentThread,
    RequirementWorkflow,
    TaskMember,
)
from app.schemas.team_workflow import (
    AssignmentData,
    CommentData,
    CommentMessageView,
    CommentReplyCreate,
    CommentThreadCreate,
    CommentThreadView,
    PageData,
    PageQuery,
    PageView,
    RequirementAssignmentSet,
    RequirementAssignmentView,
    ThreadCreatedData,
)
from app.services import task_events, task_workflow
from app.services.auth import ROLE_SCOPES
from app.services.versioned import audit


async def active_members(session, org_id, task_id, user_ids, *, lock=False):
    query = (
        select(TaskMember, Membership.role)
        .join(
            Membership,
            (Membership.org_id == TaskMember.org_id) & (Membership.user_id == TaskMember.user_id),
        )
        .join(User, User.id == TaskMember.user_id)
        .where(
            TaskMember.org_id == org_id,
            TaskMember.task_id == task_id,
            TaskMember.user_id.in_(user_ids),
            TaskMember.active.is_(True),
            Membership.active.is_(True),
            User.active.is_(True),
        )
        .order_by(TaskMember.user_id)
    )
    if lock:
        # Membership revocation and assignment/mention validation cannot pass each
        # other mid-write. Task membership changes already share the task lock.
        query = query.with_for_update(read=True, of=(TaskMember, Membership, User))
    return {member.user_id: (member, role) for member, role in await session.execute(query)}


async def set_assignment(
    session,
    actor,
    task_id,
    requirement_id,
    extraction_job_id,
    body: RequirementAssignmentSet,
    settings,
):
    # Establish the task and requirement/extraction binding before action errors
    # can disclose whether a foreign parent belongs to an otherwise visible task.
    await task_workflow.access(session, actor, task_id, lock=True)
    requirement = await session.scalar(
        select(Requirement)
        .join(Job, (Job.org_id == Requirement.org_id) & (Job.id == Requirement.job_id))
        .where(
            Requirement.org_id == actor.org_id,
            Requirement.task_id == task_id,
            Requirement.id == requirement_id,
            Requirement.job_id == extraction_job_id,
            Requirement.document_id == Job.document_id,
            Job.task_id == task_id,
            Job.kind == "extract",
            Job.status == "succeeded",
        )
    )
    if requirement is None:
        raise not_found()
    await task_workflow.access(session, actor, task_id, "card:assign", write=True, management=True)
    saved = await session.scalar(
        select(RequirementWorkflow)
        .where(
            RequirementWorkflow.org_id == actor.org_id,
            RequirementWorkflow.task_id == task_id,
            RequirementWorkflow.extraction_job_id == extraction_job_id,
            RequirementWorkflow.requirement_id == requirement_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    revision = saved.assignment_revision if saved else 0
    if revision != body.expected_assignment_revision:
        task_workflow.fail("revision_conflict", "Assignment changed; refresh before retrying")
    members = await active_members(
        session,
        actor.org_id,
        task_id,
        {actor.user_id, body.assignee_user_id} if body.assignee_user_id else {actor.user_id},
        lock=True,
    )
    # Recovery admins may assign without task membership. Their active org
    # identity is rechecked by the database gate; assignees always need membership.
    if body.assignee_user_id is not None:
        target = members.get(body.assignee_user_id)
        if target is None:
            raise not_found()
        member, role = target
        if member.role not in {"owner", "contributor"} or "card:write" not in ROLE_SCOPES[role]:
            task_workflow.fail("forbidden", "Assignee must be an active task editor", 403, 4)
    before = saved.assignee_user_id if saved else None
    if saved is None:
        saved = RequirementWorkflow(
            org_id=actor.org_id,
            task_id=task_id,
            requirement_id=requirement_id,
            extraction_job_id=extraction_job_id,
        )
        session.add(saved)
    saved.assignee_user_id = body.assignee_user_id
    saved.assignment_revision = revision + 1
    saved.changed_by_user_id = actor.user_id
    saved.changed_at = datetime.now(UTC)
    saved.last_reason_ciphertext = Secrets.for_data(settings).encrypt(body.reason)
    audit(
        session,
        actor,
        "task.assignment_changed",
        task_id,
        {
            "task_id": str(task_id),
            "requirement_id": str(requirement_id),
            "extraction_job_id": str(extraction_job_id),
            "revision": saved.assignment_revision,
            "before_assignee_user_id": str(before) if before else None,
            "after_assignee_user_id": str(body.assignee_user_id) if body.assignee_user_id else None,
            "reason_sha256": sha256(body.reason.encode()).hexdigest(),
            "actor_kind": actor.actor_kind,
        },
    )
    await session.flush()
    return AssignmentData(
        assignment=RequirementAssignmentView(
            org_id=actor.org_id,
            task_id=task_id,
            extraction_job_id=extraction_job_id,
            requirement_id=requirement_id,
            revision=saved.assignment_revision,
            assignee_user_id=saved.assignee_user_id,
            changed_by_user_id=saved.changed_by_user_id,
            changed_at=saved.changed_at,
        )
    )


async def card_access(session, actor, card_id, *, write=False):
    card = await session.scalar(
        select(ResponseCard).where(ResponseCard.org_id == actor.org_id, ResponseCard.id == card_id)
    )
    if card is None:
        raise not_found()
    _, workflow, _ = await task_workflow.access(
        session,
        actor,
        card.task_id,
        "card:comment" if write else "card:read",
        write=write,
        require_member=write,
    )
    actor.require("card:read")
    if write:
        card = await session.scalar(
            select(ResponseCard)
            .where(ResponseCard.id == card_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if card is None:
            raise not_found()
    return card, workflow


async def require_thread(session, actor, card, thread_id):
    thread = await session.scalar(
        select(CardCommentThread).where(
            CardCommentThread.org_id == actor.org_id,
            CardCommentThread.task_id == card.task_id,
            CardCommentThread.card_id == card.id,
            CardCommentThread.id == thread_id,
        )
    )
    if thread is None:
        raise not_found()
    return thread


def request_hash(body, card_id, thread_id=None):
    values = body.model_dump(mode="json")
    values["mentioned_user_ids"] = sorted(values["mentioned_user_ids"])
    values.update(
        kind="reply" if thread_id else "thread",
        card_id=str(card_id),
        thread_id=str(thread_id) if thread_id else None,
    )
    return sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def receipt(session, actor, card, body, digest):
    original = await session.scalar(
        select(CardComment).where(
            CardComment.org_id == actor.org_id,
            CardComment.task_id == card.task_id,
            CardComment.author_user_id == actor.user_id,
            CardComment.client_request_id == body.client_request_id,
        )
    )
    if original is not None and original.request_sha256 != digest:
        task_workflow.fail("idempotency_conflict", "Request ID was used for different input")
    return original


def thread_view(thread, count, last_message_at):
    return CommentThreadView(
        id=thread.id,
        org_id=thread.org_id,
        task_id=thread.task_id,
        card_id=thread.card_id,
        created_card_revision_id=thread.created_card_revision_id,
        revision=thread.revision,
        created_by_user_id=thread.created_by_user_id,
        created_at=thread.created_at,
        last_message_at=last_message_at,
        message_count=count,
    )


async def message_views(session, actor, card, rows, settings):
    mentions: dict[UUID, list[UUID]] = {row.id: [] for row in rows}
    if rows:
        # Historical mention records are retained, but recipients without current
        # task/org/user membership no longer resolve or receive content/navigation.
        current = await session.execute(
            select(CardCommentMention.comment_id, CardCommentMention.user_id)
            .join(
                TaskMember,
                (TaskMember.org_id == CardCommentMention.org_id)
                & (TaskMember.task_id == CardCommentMention.task_id)
                & (TaskMember.user_id == CardCommentMention.user_id),
            )
            .join(
                Membership,
                (Membership.org_id == TaskMember.org_id)
                & (Membership.user_id == TaskMember.user_id),
            )
            .join(User, User.id == TaskMember.user_id)
            .where(
                CardCommentMention.org_id == actor.org_id,
                CardCommentMention.task_id == card.task_id,
                CardCommentMention.card_id == card.id,
                CardCommentMention.comment_id.in_(mentions),
                TaskMember.active.is_(True),
                Membership.active.is_(True),
                User.active.is_(True),
            )
            .order_by(CardCommentMention.user_id)
        )
        for comment_id, user_id in current:
            mentions[comment_id].append(user_id)
    cipher = Secrets.for_data(settings)
    items = []
    for row in rows:
        try:
            body = cipher.decrypt(row.body_ciphertext)
        except (InvalidToken, UnicodeError, ValueError):
            raise ServiceError(
                "content_integrity", "Comment content could not be verified", 409, 4
            ) from None
        if sha256(body.encode()).hexdigest() != row.body_sha256:
            raise ServiceError("content_integrity", "Comment content could not be verified", 409, 4)
        try:
            item = CommentMessageView(
                id=row.id,
                org_id=row.org_id,
                task_id=row.task_id,
                card_id=row.card_id,
                thread_id=row.thread_id,
                author_user_id=row.author_user_id,
                body=body,
                mentioned_user_ids=mentions[row.id],
                client_request_id=row.client_request_id,
                created_at=row.created_at,
            )
        except ValidationError:
            raise ServiceError(
                "content_integrity", "Comment content could not be verified", 409, 4
            ) from None
        items.append(item)
    return items


async def lock_comment_members(session, actor, card, body):
    members = await active_members(
        session, actor.org_id, card.task_id, {actor.user_id, *body.mentioned_user_ids}, lock=True
    )
    if set(members) != {actor.user_id, *body.mentioned_user_ids}:
        raise not_found()
    return {user: members[user] for user in body.mentioned_user_ids}


async def append_comment(session, actor, card, thread, body, digest, settings, mentioned):
    saved = CardComment(
        org_id=actor.org_id,
        task_id=card.task_id,
        card_id=card.id,
        thread_id=thread.id,
        author_user_id=actor.user_id,
        body_ciphertext=Secrets.for_data(settings).encrypt(body.body),
        body_sha256=sha256(body.body.encode()).hexdigest(),
        request_sha256=digest,
        client_request_id=body.client_request_id,
    )
    session.add(saved)
    await session.flush()
    details = {
        "task_id": str(card.task_id),
        "card_id": str(card.id),
        "thread_id": str(thread.id),
        "comment_id": str(saved.id),
        "body_sha256": saved.body_sha256,
        "mentioned_user_ids": sorted(str(user) for user in mentioned),
        "client_request_id": str(body.client_request_id),
        "actor_kind": actor.actor_kind,
    }
    audit(session, actor, "task.comment_added", card.task_id, details)
    for user in sorted(mentioned, key=str):
        session.add(
            CardCommentMention(
                org_id=actor.org_id,
                task_id=card.task_id,
                card_id=card.id,
                thread_id=thread.id,
                comment_id=saved.id,
                user_id=user,
            )
        )
        audit(
            session, actor, "task.mention_created", card.task_id, {**details, "user_id": str(user)}
        )
    await session.flush()
    return saved


async def create_thread(session, actor, card_id, body: CommentThreadCreate, settings):
    card, _ = await card_access(session, actor, card_id, write=True)
    digest = request_hash(body, card_id)
    original = await receipt(session, actor, card, body, digest)
    if original is not None:
        thread = await require_thread(session, actor, card, original.thread_id)
        return ThreadCreatedData(
            thread=thread_view(thread, 1, original.created_at),
            first_comment=(await message_views(session, actor, card, [original], settings))[0],
        )
    if card.revision != body.expected_card_revision:
        task_workflow.fail("revision_conflict", "Card changed; refresh before starting the thread")
    # Acquire all member locks before inserting the thread: its trigger acquires
    # the event head, which must be the final lock in a business transaction.
    mentioned = await lock_comment_members(session, actor, card, body)
    thread = CardCommentThread(
        org_id=actor.org_id,
        task_id=card.task_id,
        card_id=card.id,
        created_card_revision_id=card.current_revision_id,
        created_by_user_id=actor.user_id,
    )
    session.add(thread)
    await session.flush()
    comment = await append_comment(session, actor, card, thread, body, digest, settings, mentioned)
    audit(
        session,
        actor,
        "task.thread_created",
        card.task_id,
        {
            "task_id": str(card.task_id),
            "card_id": str(card.id),
            "thread_id": str(thread.id),
            "comment_id": str(comment.id),
            "body_sha256": comment.body_sha256,
            "revision": thread.revision,
            "actor_kind": actor.actor_kind,
        },
    )
    await session.flush()
    return ThreadCreatedData(
        thread=thread_view(thread, 1, comment.created_at),
        first_comment=(await message_views(session, actor, card, [comment], settings))[0],
    )


async def add_comment(session, actor, card_id, thread_id, body: CommentReplyCreate, settings):
    card, _ = await card_access(session, actor, card_id, write=True)
    thread = await require_thread(session, actor, card, thread_id)
    digest = request_hash(body, card_id, thread_id)
    original = await receipt(session, actor, card, body, digest)
    if original is not None:
        saved = original
    else:
        mentioned = await lock_comment_members(session, actor, card, body)
        saved = await append_comment(
            session, actor, card, thread, body, digest, settings, mentioned
        )
    return CommentData(comment=(await message_views(session, actor, card, [saved], settings))[0])


def open_page(cursor, actor, card, workflow, settings, purpose, thread_id):
    if cursor is None:
        return None
    payload = task_events.open_cursor(
        cursor, actor, card.task_id, workflow, settings, purpose=purpose
    )
    if payload.get("c") != str(card.id) or payload.get("h") != (
        str(thread_id) if thread_id else None
    ):
        raise not_found()
    try:
        when, ident = payload["k"]
        created = datetime.fromisoformat(when)
        if created.tzinfo is None:
            raise ValueError("missing timezone")
        return created, UUID(ident)
    except (KeyError, TypeError, ValueError):
        task_workflow.fail("invalid_cursor", "Invalid discussion cursor", 422)


def page_data(actor, card, workflow, settings, purpose, rows, limit, thread_id=None):
    more = len(rows) > limit
    visible = rows[:limit]
    cursor = (
        task_events.issue_cursor(
            actor,
            card.task_id,
            workflow,
            settings,
            purpose=purpose,
            c=str(card.id),
            h=str(thread_id) if thread_id else None,
            k=[visible[-1].created_at.isoformat(), str(visible[-1].id)],
        )
        if more
        else None
    )
    return PageData(
        org_id=actor.org_id,
        task_id=card.task_id,
        card_id=card.id,
        thread_id=thread_id,
        next_cursor=cursor,
        returned=len(visible),
        has_more=more,
    )


async def list_threads(session, actor, card_id, settings, *, cursor=None, limit=50):
    page = PageQuery(cursor=cursor, limit=limit)
    card, workflow = await card_access(session, actor, card_id)
    after = open_page(page.cursor, actor, card, workflow, settings, "threads", None)
    summary = (
        select(
            CardComment.thread_id,
            func.count(CardComment.id).label("message_count"),
            func.max(CardComment.created_at).label("last_message_at"),
        )
        .where(
            CardComment.org_id == actor.org_id,
            CardComment.task_id == card.task_id,
            CardComment.card_id == card.id,
        )
        .group_by(CardComment.thread_id)
        .subquery()
    )
    query = (
        select(CardCommentThread, summary.c.message_count, summary.c.last_message_at)
        .join(summary, summary.c.thread_id == CardCommentThread.id)
        .where(
            CardCommentThread.org_id == actor.org_id,
            CardCommentThread.task_id == card.task_id,
            CardCommentThread.card_id == card.id,
        )
    )
    if after:
        query = query.where(
            tuple_(CardCommentThread.created_at, CardCommentThread.id)
            > tuple_(literal(after[0]), literal(after[1]))
        )
    rows = (
        await session.execute(
            query.order_by(CardCommentThread.created_at, CardCommentThread.id).limit(page.limit + 1)
        )
    ).all()
    data = page_data(
        actor, card, workflow, settings, "threads", [row[0] for row in rows], page.limit
    )
    return PageView[CommentThreadView](
        data=data, items=[thread_view(*row) for row in rows[: page.limit]]
    )


async def list_comments(session, actor, card_id, thread_id, settings, *, cursor=None, limit=50):
    page = PageQuery(cursor=cursor, limit=limit)
    card, workflow = await card_access(session, actor, card_id)
    await require_thread(session, actor, card, thread_id)
    after = open_page(page.cursor, actor, card, workflow, settings, "comments", thread_id)
    query = select(CardComment).where(
        CardComment.org_id == actor.org_id,
        CardComment.task_id == card.task_id,
        CardComment.card_id == card.id,
        CardComment.thread_id == thread_id,
    )
    if after:
        query = query.where(
            tuple_(CardComment.created_at, CardComment.id)
            > tuple_(literal(after[0]), literal(after[1]))
        )
    rows = list(
        await session.scalars(
            query.order_by(CardComment.created_at, CardComment.id).limit(page.limit + 1)
        )
    )
    data = page_data(actor, card, workflow, settings, "comments", rows, page.limit, thread_id)
    return PageView[CommentMessageView](
        data=data, items=await message_views(session, actor, card, rows[: page.limit], settings)
    )
