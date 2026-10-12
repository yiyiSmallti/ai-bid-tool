"""Explicit owner-connection task setup for synthetic DB fixtures.

Existing SQL gate fixtures own synchronous transactions. The narrow facade keeps
those transactions and their triggers intact while running the same authorization
services as the async API. Unexpected asynchronous I/O fails immediately.
"""

import os
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from app.core.config import Settings
from app.models.entities import Membership, User
from app.models.team_workflow import TaskMember, TaskWorkflow
from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
from app.services.task_access import seed
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session


class ServiceSession:
    def __init__(self, session):
        self.session = session
        self.info = session.info

    def add(self, value):
        self.session.add(value)

    async def execute(self, *args, **kwargs):
        return self.session.execute(*args, **kwargs)

    async def scalar(self, *args, **kwargs):
        return self.session.scalar(*args, **kwargs)

    async def get(self, *args, **kwargs):
        return self.session.get(*args, **kwargs)

    async def scalars(self, *args, **kwargs):
        return self.session.scalars(*args, **kwargs)

    async def flush(self, *args, **kwargs):
        self.session.flush(*args, **kwargs)

    async def refresh(self, *args, **kwargs):
        self.session.refresh(*args, **kwargs)

    def expire_all(self):
        self.session.expire_all()


def service_call(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return completed.value
    else:
        coroutine.close()
        raise AssertionError("Synthetic sync task setup attempted asynchronous I/O")


def actor_context(session, org, user, *, kind="session", token=None):
    session.execute(text("SELECT set_config('app.current_org', :org, true)"), {"org": str(org)})
    member = session.scalar(
        select(Membership).where(Membership.org_id == org, Membership.user_id == user)
    )
    assert member is not None and member.active
    actor = Identity(user, org, set(ROLE_SCOPES[member.role]), member.role, token, kind)
    service_call(set_actor_context(ServiceSession(session), actor))
    return actor


async def actor_context_async(session, org, user, *, kind="session", token=None):
    await session.execute(
        text("SELECT set_config('app.current_org', :org, true)"), {"org": str(org)}
    )
    member = await session.scalar(
        select(Membership).where(Membership.org_id == org, Membership.user_id == user)
    )
    assert member is not None
    actor = Identity(user, org, set(ROLE_SCOPES[member.role]), member.role, token, kind)
    await set_actor_context(session, actor)
    return actor


async def review_card_async(session, org, user, card_id, *, action, storage=None, settings=None):
    """Use the authenticated response service to open or complete a real round.

    SQL gate tests still write invalid rows directly. Successful setup must pass
    the same input, warning, signer and Evidence checks as the public API.
    """
    from app.schemas.response_card_contracts import CardAction
    from app.services import response_cards

    actor = await actor_context_async(session, org, user)
    card, revision, requirement = await response_cards.require_card(session, card_id)
    values = {"action": action, "expected_revision": card.revision}
    if action == "confirm":
        await confirm_requirements_async(
            session, org, card.task_id, [requirement.id], settings=settings
        )
        actor = await actor_context_async(session, org, user)
        view = await response_cards.card_view(session, actor, card, revision, requirement, storage)
        values.update(
            reviewed_evidence_ids=[row["id"] for row in view["evidence"]],
            reviewed_warning_codes=view["warning_codes"],
            reason="Synthetic reviewer inspected all linked materials and warnings.",
        )
    await response_cards.card_action(
        session, actor, card_id, CardAction(**values), storage, settings or Settings()
    )
    return await session.get(type(revision), card.current_revision_id)


async def confirm_requirements_async(session, org, task_id, requirement_ids=None, *, settings=None):
    """Explicit accepted-input setup through the same review services as HTTP.

    Tests of unconfirmed inputs deliberately do not call this helper. Existing
    consumer fixtures use their real task owner and retain all DB/human triggers.
    """
    from app.models.entities import Requirement
    from app.schemas.requirement_confirmation import ConfirmationTarget, RequirementConfirmBatch
    from app.services import requirement_confirmation

    settings = settings or Settings()
    workflow = await session.scalar(
        select(TaskWorkflow).where(TaskWorkflow.org_id == org, TaskWorkflow.task_id == task_id)
    )
    assert workflow is not None
    actor = await actor_context_async(session, org, workflow.owner_user_id)
    query = select(Requirement).where(Requirement.org_id == org, Requirement.task_id == task_id)
    if requirement_ids is not None:
        query = query.where(Requirement.id.in_(requirement_ids))
    requirements = list(await session.scalars(query.order_by(Requirement.id)))
    for job_id in sorted({row.job_id for row in requirements}, key=str):
        await requirement_confirmation.seed_reviews(
            session, actor, task_id, job_id, settings=settings, origin="legacy"
        )
    for job_id in sorted({row.job_id for row in requirements}, key=str):
        rows = [row for row in requirements if row.job_id == job_id]
        current = await requirement_confirmation.effective_reviews(session, rows)
        pending = [row for row in rows if not current[row.id].confirmed]
        for offset in range(0, len(pending), 100):
            scope = await requirement_confirmation.scope_row(session, actor, task_id, job_id)
            await requirement_confirmation.confirm_batch(
                session,
                actor,
                task_id,
                job_id,
                RequirementConfirmBatch(
                    request_id=uuid4(),
                    expected_set_revision=scope.revision,
                    reviewed_each=True,
                    reason="Synthetic task owner inspected every selected requirement and its exact source.",
                    items=[
                        ConfirmationTarget(
                            requirement_id=row.id,
                            expected_revision=current[row.id].revision,
                            expected_review_hash=current[row.id].review_hash,
                        )
                        for row in pending[offset : offset + 100]
                    ],
                ),
                settings=settings,
            )


def confirm_requirements(session, org, task_id, requirement_ids=None, *, settings=None):
    return service_call(
        confirm_requirements_async(
            ServiceSession(session), org, task_id, requirement_ids, settings=settings
        )
    )


async def requirement_review_manifest_async(session, org, task_id, extraction_job_id):
    """Pin live review inputs for an explicitly constructed synthetic draft."""
    from app.models.entities import Requirement
    from app.services import requirement_consumption

    rows = list(
        await session.scalars(
            select(Requirement)
            .where(
                Requirement.org_id == org,
                Requirement.task_id == task_id,
                Requirement.job_id == extraction_job_id,
            )
            .order_by(Requirement.id)
        )
    )
    reviews = await requirement_consumption.effective(session, rows)
    return {
        "requirements": [
            {
                "requirement_id": str(row.id),
                "requirement_review": requirement_consumption.fields(reviews[row.id]),
            }
            for row in rows
        ]
    }


def requirement_review_manifest(session, org, task_id, extraction_job_id):
    return service_call(
        requirement_review_manifest_async(ServiceSession(session), org, task_id, extraction_job_id)
    )


def review_card(session, org, user, card_id, *, action, storage=None, settings=None):
    return service_call(
        review_card_async(
            ServiceSession(session),
            org,
            user,
            card_id,
            action=action,
            storage=storage,
            settings=settings,
        )
    )


def domain_reviewer(admin_engine, org, task_id, domain):
    """Enroll a distinct professional human whose authority survives actor switches."""
    from conftest import PASSWORD_HASH
    from sqlalchemy.orm import Session

    assert domain in {"technical", "commercial"}
    with Session(admin_engine) as session, session.begin():
        session.execute(text("SELECT set_config('app.current_org', :org, true)"), {"org": str(org)})
        user = User(
            id=uuid4(), email=f"reviewer-{uuid4().hex}@example.test", password_hash=PASSWORD_HASH
        )
        session.add(user)
        session.flush()
        session.add(
            Membership(
                org_id=org, user_id=user.id, role="technical" if domain == "technical" else "bidder"
            )
        )
        session.flush()
        add_member(session, org, task_id, user.id, role="contributor", review_domains=[domain])
        return user.id, user.email


async def reviewer_header(api, admin_engine, org, task_id, domain):
    from conftest import PASSWORD

    user, email = domain_reviewer(admin_engine, org, task_id, domain)
    response = await api.post(
        "/auth/login", json={"email": email, "password": PASSWORD, "org_id": str(org)}
    )
    assert response.status_code == 200, response.text
    return user, {
        "Authorization": "Bearer " + response.json()["data"]["session"],
        "X-Org-Id": str(org),
    }


def finish_scope(session):
    """Run deferred fixture checks before deliberately switching tenant context."""
    session.flush()
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    session.execute(text("SET CONSTRAINTS ALL DEFERRED"))


def seed_task(session, task):
    """Import an explicitly constructed task for its real eligible creator."""
    actor = actor_context(session, task.org_id, task.created_by)
    assert actor.role in {"admin", "bidder"}
    session.add(task)
    session.flush()
    service_call(seed(ServiceSession(session), actor, task))
    return task


async def seed_task_async(session, task):
    """Seed the actual creator in an existing async fixture transaction."""
    await session.execute(
        text("SELECT set_config('app.current_org', :org, true)"), {"org": str(task.org_id)}
    )
    member = await session.scalar(
        select(Membership).where(
            Membership.org_id == task.org_id, Membership.user_id == task.created_by
        )
    )
    assert member is not None and member.role in {"admin", "bidder"}
    actor = Identity(task.created_by, task.org_id, set(ROLE_SCOPES[member.role]), member.role)
    await set_actor_context(session, actor)
    session.add(task)
    await session.flush([task])
    await seed(session, actor, task)
    return task


@contextmanager
def owner_session(org):
    """Keep legacy access-state setup on the isolated test owner's connection."""
    engine = create_engine(os.environ["BID_TEST_ADMIN_URL"])
    try:
        with Session(engine, expire_on_commit=False) as session, session.begin():
            assert session.scalar(text("SELECT current_user")) != "bid_app"
            session.execute(
                text("SELECT set_config('app.current_org', :org, true)"), {"org": str(org)}
            )
            yield session
    finally:
        engine.dispose()


async def related_task(session, org, user, source_task_id, *, name, task_id=None):
    """Create committed same-org parents before exercising runtime binding guards.

    Read the source through the caller's transaction, which may have uncommitted
    fixture state. Only the independent new task is written by the test owner;
    runtime task-member management remains denied by the unchanged SQL trigger.
    """
    from app.models.entities import Task

    workflow = await session.scalar(
        select(TaskWorkflow).where(
            TaskWorkflow.org_id == org, TaskWorkflow.task_id == source_task_id
        )
    )
    assert workflow is not None
    tested_member = await session.scalar(
        select(TaskMember).where(
            TaskMember.org_id == org,
            TaskMember.task_id == source_task_id,
            TaskMember.user_id == user,
        )
    )
    assert tested_member is not None and tested_member.active
    owner = workflow.owner_user_id
    with owner_session(org) as fixture:
        task = seed_task(
            fixture, Task(id=task_id or uuid4(), org_id=org, created_by=owner, name=name)
        )
        if user != owner:
            add_member(
                fixture,
                org,
                task.id,
                user,
                role=tested_member.role,
                review_domains=list(tested_member.review_domains),
            )
        finish_scope(fixture)
    await actor_context_async(session, org, user)
    return task


def _manager(session, org):
    from conftest import PASSWORD_HASH

    email = f"task-fixture-manager-{org}@example.test"
    user = session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(id=uuid4(), email=email, password_hash=PASSWORD_HASH)
        session.add(user)
        session.flush()
        session.add(Membership(org_id=org, user_id=user.id, role="admin"))
        session.flush()
    return actor_context(session, org, user.id)


def set_role_in_session(session, org, user, role, *, task_ids=None):
    """Give a role-switch actor deliberate authority on only its existing tasks.

    A separate eligible human owns these test tasks. Switching the tested actor to
    technical/viewer must not leave an ineligible owner or fabricate review grants
    from its org role alone. Task-membership tests do not use this helper.
    """
    assert session.scalar(text("SELECT current_user")) != "bid_app"
    session.execute(text("SELECT set_config('app.current_org', :org, true)"), {"org": str(org)})
    query = (
        select(TaskWorkflow)
        .join(
            TaskMember,
            (TaskMember.org_id == TaskWorkflow.org_id)
            & (TaskMember.task_id == TaskWorkflow.task_id),
        )
        .where(TaskWorkflow.org_id == org, TaskMember.user_id == user, TaskMember.active.is_(True))
    )
    if task_ids is not None:
        query = query.where(TaskWorkflow.task_id.in_(task_ids))
    workflows = session.scalars(query).all()
    member = session.scalar(
        select(Membership).where(Membership.org_id == org, Membership.user_id == user)
    )
    assert member is not None
    manager = _manager(session, org) if workflows else None
    for workflow in workflows:
        if workflow.owner_user_id == user:
            assert manager is not None
            previous = session.scalar(
                select(TaskMember).where(
                    TaskMember.task_id == workflow.task_id, TaskMember.user_id == user
                )
            )
            assert previous is not None
            previous.role = "contributor"
            previous.review_domains = []
            previous.revision += 1
            previous.workflow_revision = workflow.revision + 1
            previous.changed_by_user_id = manager.user_id
            previous.changed_at = datetime.now(UTC)
            # Retire the unique active owner before inserting its replacement;
            # the deferred owner/FK checks run after the full transfer is stored.
            session.flush()
            add_member(
                session,
                org,
                workflow.task_id,
                manager.user_id,
                role="owner",
                review_domains=[],
                changed_by_user_id=manager.user_id,
            )
            workflow.owner_user_id = manager.user_id
            session.flush()
    member.role = role
    session.flush()
    for workflow in workflows:
        add_member(
            session,
            org,
            workflow.task_id,
            user,
            role="observer" if role == "viewer" else "contributor",
            review_domains={"technical": ["technical"], "bidder": ["commercial"]}.get(role, []),
        )
    # Fixture callers write subsequent rows as the tested actor, not the manager.
    actor_context(session, org, user)


def set_role(admin_engine, org, user, role, *, task_ids=None):
    from sqlalchemy.orm import Session

    with Session(admin_engine) as session, session.begin():
        set_role_in_session(session, org, user, role, task_ids=task_ids)


def add_member(session, org, task_id, user, *, role, review_domains, changed_by_user_id=None):
    """Construct legacy membership using an explicit migration-owner fixture connection."""
    assert session.scalar(text("SELECT current_user")) != "bid_app"
    workflow = session.scalar(
        select(TaskWorkflow).where(TaskWorkflow.org_id == org, TaskWorkflow.task_id == task_id)
    )
    assert workflow is not None
    workflow.revision += 1
    workflow.access_epoch += 1
    member = session.scalar(
        select(TaskMember).where(
            TaskMember.org_id == org, TaskMember.task_id == task_id, TaskMember.user_id == user
        )
    )
    if member is None:
        member = TaskMember(org_id=org, task_id=task_id, user_id=user, revision=1)
        session.add(member)
    else:
        member.revision += 1
    member.role = role
    member.review_domains = review_domains
    member.active = True
    member.workflow_revision = workflow.revision
    member.changed_by_user_id = changed_by_user_id or workflow.owner_user_id
    member.changed_at = datetime.now(UTC)
    session.flush()
    return member
