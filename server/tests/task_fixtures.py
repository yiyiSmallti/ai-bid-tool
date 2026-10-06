"""Explicit task setup through real workflow services for synthetic DB fixtures.

Existing SQL gate fixtures own synchronous transactions. The narrow facade keeps
those transactions and their triggers intact while running the same authorization
services as the async API. Unexpected asynchronous I/O fails immediately.
"""

from uuid import uuid4

from app.core.config import Settings
from app.models.entities import Membership, User
from app.models.team_workflow import TaskMember, TaskWorkflow
from app.schemas.team_workflow import TaskMemberSet, TaskOwnerHandover
from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
from app.services.task_workflow import handover, seed, set_member
from sqlalchemy import select, text


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


async def related_task(session, org, user, source_task_id, *, name, task_id=None):
    """Create another valid task for a same-org relational-binding negative case."""
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
    task = await seed_task_async(
        session, Task(id=task_id or uuid4(), org_id=org, created_by=owner, name=name)
    )
    if user != owner:
        owner_org_member = await session.scalar(
            select(Membership).where(Membership.org_id == org, Membership.user_id == owner)
        )
        assert owner_org_member is not None
        actor = Identity(owner, org, set(ROLE_SCOPES[owner_org_member.role]), owner_org_member.role)
        await set_member(
            session,
            actor,
            task.id,
            user,
            TaskMemberSet(
                expected_revision=1,
                role=tested_member.role,
                review_domains=list(tested_member.review_domains),
                reason="Synthetic cross-task case preserves the tested actor's authority",
            ),
            Settings(),
        )
    member = await session.scalar(
        select(Membership).where(Membership.org_id == org, Membership.user_id == user)
    )
    assert member is not None
    await set_actor_context(
        session, Identity(user, org, set(ROLE_SCOPES[member.role]), member.role)
    )
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
    settings = Settings()
    for workflow in workflows:
        if workflow.owner_user_id == user:
            service_call(
                handover(
                    ServiceSession(session),
                    manager,
                    workflow.task_id,
                    TaskOwnerHandover(
                        expected_revision=workflow.revision,
                        target_user_id=manager.user_id,
                        previous_owner_role="contributor",
                        previous_owner_review_domains=[],
                        reason="Synthetic fixture separates ownership from review actor",
                    ),
                    settings,
                )
            )
    member.role = role
    session.flush()
    for workflow in workflows:
        service_call(
            set_member(
                ServiceSession(session),
                manager,
                workflow.task_id,
                user,
                TaskMemberSet(
                    expected_revision=workflow.revision,
                    role="observer" if role == "viewer" else "contributor",
                    review_domains={"technical": ["technical"], "bidder": ["commercial"]}.get(
                        role, []
                    ),
                    reason="Synthetic fixture assigns the tested actor's exact task authority",
                ),
                settings,
            )
        )
    # Fixture callers write subsequent rows as the tested actor, not the manager.
    actor_context(session, org, user)


def set_role(admin_engine, org, user, role, *, task_ids=None):
    from sqlalchemy.orm import Session

    with Session(admin_engine) as session, session.begin():
        set_role_in_session(session, org, user, role, task_ids=task_ids)


def add_member(session, org, task_id, user, *, role, review_domains):
    """Have the actual owner grant one explicitly named test participant."""
    workflow = session.scalar(
        select(TaskWorkflow).where(TaskWorkflow.org_id == org, TaskWorkflow.task_id == task_id)
    )
    assert workflow is not None
    owner = actor_context(session, org, workflow.owner_user_id)
    return service_call(
        set_member(
            ServiceSession(session),
            owner,
            task_id,
            user,
            TaskMemberSet(
                expected_revision=workflow.revision,
                role=role,
                review_domains=review_domains,
                reason="Synthetic fixture explicitly enrolls a second task participant",
            ),
            Settings(),
        )
    )
