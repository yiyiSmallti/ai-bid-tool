"""Current task access, one-owner membership and atomic archive transitions."""

from datetime import UTC, datetime
from hashlib import sha256
from typing import NoReturn
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.models.entities import ApiToken, Job, Membership, Org, Task, User, VendorCall
from app.models.team_workflow import TaskEventHead, TaskMember, TaskWorkflow
from app.schemas.team_workflow import (
    BoardNextAction,
    MemberCandidateView,
    PageData,
    TaskAccessView,
    TaskMemberData,
    TaskMemberView,
    TaskWorkflowView,
)
from app.services.auth import ROLE_SCOPES, SCOPES, Identity, set_actor_context
from app.services.versioned import audit

HUMAN_SCOPES = {
    "task:members:write",
    "task:archive",
    "card:assign",
    "card:comment",
    "task:review-policy",
    "card:cosign",
}
DECISIONS = {"evidence:confirm", "card:cosign", "check:decide", "score:rubric:review"}


def fail(code, message, status=409, exit_code=2) -> NoReturn:
    raise ServiceError(code, message, status, exit_code)


def domains(role):
    return {"bidder": ["commercial"], "technical": ["technical"]}.get(role, [])


async def live_actor(session, actor):
    row = (
        await session.execute(
            select(Membership, User, Org)
            .join(User, User.id == Membership.user_id)
            .join(Org, Org.id == Membership.org_id)
            .where(Membership.org_id == actor.org_id, Membership.user_id == actor.user_id)
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None or not row[0].active or not row[1].active or not row[2].active:
        raise not_found()
    member, _, _ = row
    scopes = actor.scopes & ROLE_SCOPES[member.role]
    if actor.token_id is not None:
        token = await session.get(ApiToken, actor.token_id)
        if (
            token is None
            or token.org_id != actor.org_id
            or token.user_id != actor.user_id
            or token.revoked
            or token.expires_at <= datetime.now(UTC)
        ):
            fail("forbidden", "Permission denied", 403, 4)
        scopes &= set(token.scopes) & SCOPES
    elif actor.actor_kind != "session":
        scopes -= HUMAN_SCOPES | {"evidence:confirm", "export"}
    return Identity(
        actor.user_id, actor.org_id, scopes, member.role, actor.token_id, actor.actor_kind
    )


async def access(
    session: AsyncSession,
    actor: Identity,
    task_id: UUID,
    scope="task:read",
    *,
    write=False,
    domain=None,
    lock: bool | None = None,
    management=False,
):
    # Preparation paths may check write authority without holding a task lock;
    # they must repeat this gate with the default write lock before publication.
    should_lock = write if lock is None else lock
    query = select(Task).where(Task.org_id == actor.org_id, Task.id == task_id)
    if should_lock:
        query = query.with_for_update()
    task = await session.scalar(query.execution_options(populate_existing=True))
    if task is None:
        raise not_found()
    live = await live_actor(session, actor)
    workflow_query = select(TaskWorkflow).where(
        TaskWorkflow.org_id == actor.org_id, TaskWorkflow.task_id == task_id
    )
    if should_lock:
        workflow_query = workflow_query.with_for_update()
    workflow = await session.scalar(workflow_query.execution_options(populate_existing=True))
    member = await session.scalar(
        select(TaskMember)
        .where(
            TaskMember.org_id == actor.org_id,
            TaskMember.task_id == task_id,
            TaskMember.user_id == actor.user_id,
            TaskMember.active.is_(True),
        )
        .execution_options(populate_existing=True)
    )
    recovery = live.actor_kind == "session" and live.token_id is None and live.role == "admin"
    if workflow is None or (member is None and not recovery):
        raise not_found()
    # A visible object is established before scope errors reveal which action was denied.
    live.require("task:read")
    live.require(scope)
    if scope in HUMAN_SCOPES and (live.actor_kind != "session" or live.token_id is not None):
        fail("forbidden", "Human session required", 403, 4)
    if management:
        if not recovery and (member is None or member.role != "owner"):
            fail("forbidden", "Task owner required", 403, 4)
    elif write:
        if (
            member is None
            or member.role == "observer"
            or (member.role == "reviewer" and scope not in DECISIONS)
        ):
            fail("forbidden", "Task role does not permit this action", 403, 4)
    if domain is not None:
        if (
            member is None
            or domain not in member.review_domains
            or domain not in domains(live.role)
        ):
            fail("forbidden", "Review domain is not granted", 403, 4)
    if write and workflow.state == "archived":
        fail("task_archived", "Task is archived")
    actor.scopes, actor.role = live.scopes, live.role
    await set_actor_context(session, live)
    return task, workflow, member


async def seed(session, actor, task):
    live = await live_actor(session, actor)
    live.require("task:create")
    if (
        task.created_by != actor.user_id
        or task.org_id != actor.org_id
        or live.role not in {"admin", "bidder"}
    ):
        fail("forbidden", "Task owner must be an eligible creator", 403, 4)
    await set_actor_context(session, live)
    now = datetime.now(UTC)
    # Flush owner before workflow to satisfy trigger eligibility; deferred FK guarantees
    # that a transaction cannot commit this intermediate state.
    session.add(
        TaskMember(
            org_id=actor.org_id,
            task_id=task.id,
            user_id=actor.user_id,
            role="owner",
            review_domains=domains(live.role),
            changed_by_user_id=actor.user_id,
            changed_at=now,
        )
    )
    await session.execute(
        insert(TaskEventHead)
        .values(org_id=actor.org_id, task_id=task.id)
        .on_conflict_do_nothing(index_elements=["org_id", "task_id"])
    )
    await session.flush()
    workflow = TaskWorkflow(org_id=actor.org_id, task_id=task.id, owner_user_id=actor.user_id)
    session.add(workflow)
    await session.flush()
    return workflow


def member_view(member, label=None, active=None):
    return TaskMemberView(
        org_id=member.org_id,
        task_id=member.task_id,
        user_id=member.user_id,
        display_label=label,
        role=member.role,
        review_domains=member.review_domains,
        active=member.active if active is None else active,
        revision=member.revision,
    )


async def view(session, actor, workflow, settings):
    from app.services.task_events import current_cursor

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
        last_event_cursor=await current_cursor(
            session, actor, workflow.task_id, settings, workflow
        ),
    )


async def show(session, actor, task_id, settings):
    _, workflow, member = await access(session, actor, task_id)
    actions: list[BoardNextAction] = ["view"]
    if (
        workflow.state == "archived"
        and (actor.role == "admin" or member is not None and member.role == "owner")
        and actor.actor_kind == "session"
    ):
        actions.append("unarchive")
    return TaskAccessView(
        workflow=await view(session, actor, workflow, settings),
        member=member_view(member) if member else None,
        management_recovery=member is None,
        allowed_actions=actions,
    )


async def list_members(
    session, actor, task_id, settings, *, cursor=None, limit=50, candidates=False
):
    from app.services.task_events import issue_cursor, open_cursor

    _, workflow, _ = await access(session, actor, task_id, management=candidates)
    if candidates and (actor.actor_kind != "session" or actor.token_id is not None):
        fail("forbidden", "Human session required", 403, 4)
    purpose = "member-candidates" if candidates else "members"
    after = None
    if cursor:
        try:
            after = UUID(
                open_cursor(cursor, actor, task_id, workflow, settings, purpose=purpose)["after"]
            )
        except (ValueError, KeyError, TypeError):
            fail("invalid_cursor", "Invalid member cursor", 400)
    if candidates:
        query = (
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.org_id == actor.org_id,
                Membership.active.is_(True),
                User.active.is_(True),
            )
            .with_for_update(read=True, of=(Membership, User))
        )
        key = Membership.user_id
    else:
        query = (
            select(TaskMember, User, Membership)
            .join(User, User.id == TaskMember.user_id)
            .join(
                Membership,
                (Membership.org_id == TaskMember.org_id)
                & (Membership.user_id == TaskMember.user_id),
            )
            .where(TaskMember.org_id == actor.org_id, TaskMember.task_id == task_id)
        )
        key = TaskMember.user_id
    if after:
        query = query.where(key > after)
    rows = (await session.execute(query.order_by(key).limit(limit + 1))).all()
    more = len(rows) > limit
    rows = rows[:limit]
    if candidates:
        items = [
            MemberCandidateView(
                org_id=actor.org_id,
                user_id=row[0].user_id,
                display_label=row[1].email,
                org_role=row[0].role,
            )
            for row in rows
        ]
    else:
        items = [
            member_view(row[0], row[1].email, row[0].active and row[1].active and row[2].active)
            for row in rows
        ]
    next_cursor = (
        issue_cursor(
            actor, task_id, workflow, settings, purpose=purpose, after=str(rows[-1][0].user_id)
        )
        if more
        else None
    )
    return PageData(
        org_id=actor.org_id,
        task_id=task_id,
        next_cursor=next_cursor,
        returned=len(items),
        has_more=more,
    ), items


async def eligible(session, actor, user_id, role, review_domains):
    row = (
        await session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.org_id == actor.org_id,
                Membership.user_id == user_id,
                Membership.active.is_(True),
                User.active.is_(True),
            )
            .with_for_update(read=True, of=(Membership, User))
        )
    ).first()
    if row is None:
        raise not_found()
    org_member, _ = row
    if role == "owner" and org_member.role not in {"admin", "bidder"}:
        fail("invalid_member", "Task owner must be an active administrator or bidder", 400)
    if not set(review_domains) <= set(domains(org_member.role)):
        fail("invalid_member", "Review domains exceed the current organization role", 400)
    return org_member


def expected(workflow, revision):
    if workflow.revision != revision:
        fail("revision_conflict", "Workflow changed; refresh before retrying")


def changed(session, actor, workflow, action, reason, user_id=None, **metadata):
    workflow.revision += 1
    workflow.access_epoch += 1
    audit(
        session,
        actor,
        action,
        workflow.task_id,
        {
            "task_id": str(workflow.task_id),
            "revision": workflow.revision,
            "user_id": str(user_id) if user_id else None,
            "reason_sha256": sha256(reason.encode()).hexdigest(),
            "actor_kind": actor.actor_kind,
            **metadata,
        },
    )


async def set_member(session, actor, task_id, user_id, body, settings):
    _, workflow, _ = await access(
        session, actor, task_id, "task:members:write", write=True, management=True
    )
    expected(workflow, body.expected_revision)
    await eligible(session, actor, user_id, body.role, body.review_domains)
    member = await session.scalar(
        select(TaskMember).where(TaskMember.task_id == task_id, TaskMember.user_id == user_id)
    )
    if user_id == workflow.owner_user_id:
        fail("owner_handover_required", "Change the owner through handover")
    before_role = member.role if member else None
    before_domains = list(member.review_domains) if member else []
    before_active = member.active if member else False
    if member is None:
        member = TaskMember(
            org_id=actor.org_id,
            task_id=task_id,
            user_id=user_id,
            role=body.role,
            review_domains=body.review_domains,
            active=True,
            changed_by_user_id=actor.user_id,
            changed_at=datetime.now(UTC),
        )
        session.add(member)
    else:
        member.role, member.review_domains, member.active = body.role, body.review_domains, True
        member.revision += 1
        member.changed_by_user_id, member.changed_at = actor.user_id, datetime.now(UTC)
    member.last_reason_ciphertext = Secrets.for_data(settings).encrypt(body.reason)
    await session.flush()
    workflow.last_reason_ciphertext = member.last_reason_ciphertext
    changed(
        session,
        actor,
        workflow,
        "task.member_set",
        body.reason,
        user_id,
        before_role=before_role,
        after_role=member.role,
        before_review_domains=before_domains,
        after_review_domains=member.review_domains,
        before_active=before_active,
        after_active=True,
    )
    await session.flush()
    return TaskMemberData(
        workflow=await view(session, actor, workflow, settings), member=member_view(member)
    )


async def remove_member(session, actor, task_id, user_id, body, settings):
    _, workflow, _ = await access(
        session, actor, task_id, "task:members:write", write=True, management=True
    )
    expected(workflow, body.expected_revision)
    member = await session.scalar(
        select(TaskMember).where(
            TaskMember.task_id == task_id,
            TaskMember.user_id == user_id,
            TaskMember.active.is_(True),
        )
    )
    if member is None:
        raise not_found()
    if user_id == workflow.owner_user_id:
        fail("owner_handover_required", "The task owner cannot be removed")
    member.last_reason_ciphertext = Secrets.for_data(settings).encrypt(body.reason)
    member.active = False
    member.revision += 1
    member.changed_by_user_id, member.changed_at = actor.user_id, datetime.now(UTC)
    await session.flush()
    workflow.last_reason_ciphertext = member.last_reason_ciphertext
    changed(
        session,
        actor,
        workflow,
        "task.member_removed",
        body.reason,
        user_id,
        before_role=member.role,
        after_role=member.role,
        before_active=True,
        after_active=False,
    )
    await session.flush()
    return await view(session, actor, workflow, settings)


async def handover(session, actor, task_id, body, settings):
    _, workflow, _ = await access(
        session, actor, task_id, "task:members:write", write=True, management=True
    )
    expected(workflow, body.expected_revision)
    if body.target_user_id == workflow.owner_user_id:
        fail("invalid_transition", "The selected user already owns the task")
    target_org = await eligible(session, actor, body.target_user_id, "owner", [])
    previous = await session.scalar(
        select(TaskMember).where(
            TaskMember.task_id == task_id, TaskMember.user_id == workflow.owner_user_id
        )
    )
    if previous is None:
        raise not_found()
    old_org = await session.scalar(
        select(Membership).where(
            Membership.org_id == actor.org_id, Membership.user_id == previous.user_id
        )
    )
    if old_org is None or not set(body.previous_owner_review_domains) <= set(domains(old_org.role)):
        fail("invalid_member", "Previous owner's domains exceed the current organization role", 400)
    # Resolve eligibility before mutating the old member: querying User after the
    # demotion would autoflush an active member whose org membership is disabled.
    old_user = await session.get(User, previous.user_id)
    previous_active = bool(old_org.active and old_user and old_user.active)
    previous.last_reason_ciphertext = Secrets.for_data(settings).encrypt(body.reason)
    previous.role, previous.review_domains = (
        body.previous_owner_role,
        body.previous_owner_review_domains,
    )
    previous.revision += 1
    previous.changed_by_user_id, previous.changed_at = actor.user_id, datetime.now(UTC)
    # A disabled previous owner remains historical, without regaining effective access.
    previous.active = previous_active
    await session.flush()
    target = await session.scalar(
        select(TaskMember).where(
            TaskMember.task_id == task_id, TaskMember.user_id == body.target_user_id
        )
    )
    if target is None:
        target = TaskMember(
            org_id=actor.org_id,
            task_id=task_id,
            user_id=body.target_user_id,
            role="owner",
            review_domains=domains(target_org.role),
            changed_by_user_id=actor.user_id,
            changed_at=datetime.now(UTC),
        )
        session.add(target)
    else:
        target.role, target.review_domains, target.active = "owner", domains(target_org.role), True
        target.revision += 1
        target.changed_by_user_id, target.changed_at = actor.user_id, datetime.now(UTC)
    target.last_reason_ciphertext = previous.last_reason_ciphertext
    await session.flush()
    workflow.last_reason_ciphertext = previous.last_reason_ciphertext
    old_owner = workflow.owner_user_id
    workflow.owner_user_id = body.target_user_id
    changed(
        session,
        actor,
        workflow,
        "task.owner_handed_over",
        body.reason,
        target.user_id,
        before_owner_user_id=str(old_owner),
        after_owner_user_id=str(target.user_id),
        previous_owner_role=previous.role,
        previous_owner_review_domains=previous.review_domains,
        after_review_domains=target.review_domains,
    )
    await session.flush()
    return await view(session, actor, workflow, settings)


async def archive(session, actor, task_id, body, settings, *, restore=False):
    _, workflow, _ = await access(
        session, actor, task_id, "task:archive", lock=True, management=True
    )
    expected(workflow, body.expected_revision)
    target_state = "active" if restore else "archived"
    if workflow.state == target_state:
        fail("invalid_transition", "Task already has the requested state")
    if not restore:
        busy = await session.scalar(
            select(Job.id)
            .where(Job.task_id == task_id, Job.status.in_(["queued", "running"]))
            .limit(1)
        )
        liability = await session.scalar(
            select(VendorCall.id)
            .join(Job, (Job.org_id == VendorCall.org_id) & (Job.id == VendorCall.job_id))
            .where(Job.task_id == task_id, VendorCall.state.in_(["pending", "unknown"]))
            .limit(1)
        )
        if busy or liability:
            fail("task_busy", "Finish or reconcile outstanding task work before archiving")
    before_state = workflow.state
    workflow.last_reason_ciphertext = Secrets.for_data(settings).encrypt(body.reason)
    workflow.state = target_state
    workflow.archived_at = None if restore else datetime.now(UTC)
    workflow.archived_by_user_id = None if restore else actor.user_id
    changed(
        session,
        actor,
        workflow,
        "task.unarchived" if restore else "task.archived",
        body.reason,
        before_state=before_state,
        after_state=target_state,
    )
    await session.flush()
    return await view(session, actor, workflow, settings)
