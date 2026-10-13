"""Synthetic task inputs prepared through real human authorization and review gates."""

import os
from contextlib import contextmanager
from uuid import uuid4

from app.core.config import Settings
from app.services.auth import HUMAN_SCOPES, SCOPES, Identity, set_actor_context
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


def actor_context(session, *, kind="session", token=None, scopes=None):
    granted = HUMAN_SCOPES if kind == "session" and token is None else SCOPES
    actor = Identity(
        scopes=set(granted if scopes is None else scopes), token_id=token, actor_kind=kind
    )
    service_call(set_actor_context(ServiceSession(session), actor))
    return actor


async def actor_context_async(session, *, kind="session", token=None, scopes=None):
    granted = HUMAN_SCOPES if kind == "session" and token is None else SCOPES
    actor = Identity(
        scopes=set(granted if scopes is None else scopes), token_id=token, actor_kind=kind
    )
    await set_actor_context(session, actor)
    return actor


async def review_card_async(session, card_id, *, action, storage=None, settings=None):
    """Use the authenticated response service to review a real response card.

    SQL gate tests still write invalid rows directly. Successful setup must pass
    the same input, warning and Evidence checks as the public API.
    """
    from app.schemas.response_card_contracts import CardAction
    from app.services import response_cards

    actor = await actor_context_async(session)
    card, revision, requirement = await response_cards.require_card(session, card_id)
    values = {"action": action, "expected_revision": card.revision}
    if action == "confirm":
        await confirm_requirements_async(session, card.task_id, [requirement.id], settings=settings)
        actor = await actor_context_async(session)
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


async def confirm_requirements_async(session, task_id, requirement_ids=None, *, settings=None):
    """Explicit accepted-input setup through the same review services as HTTP.

    Tests of unconfirmed inputs deliberately do not call this helper. Existing
    consumer fixtures use the installation human and retain all DB/human triggers.
    """
    from app.models.entities import Requirement
    from app.schemas.requirement_confirmation import ConfirmationTarget, RequirementConfirmBatch
    from app.services import requirement_confirmation

    settings = settings or Settings()
    actor = await actor_context_async(session)
    query = select(Requirement).where(Requirement.task_id == task_id)
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
                    reason="Synthetic human inspected every selected requirement and its exact source.",
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


def confirm_requirements(session, task_id, requirement_ids=None, *, settings=None):
    return service_call(
        confirm_requirements_async(
            ServiceSession(session), task_id, requirement_ids, settings=settings
        )
    )


async def requirement_review_manifest_async(session, task_id, extraction_job_id):
    """Pin live review inputs for an explicitly constructed synthetic draft."""
    from app.models.entities import Requirement
    from app.services import requirement_consumption

    rows = list(
        await session.scalars(
            select(Requirement)
            .where(
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


def requirement_review_manifest(session, task_id, extraction_job_id):
    return service_call(
        requirement_review_manifest_async(ServiceSession(session), task_id, extraction_job_id)
    )


def review_card(session, card_id, *, action, storage=None, settings=None):
    return service_call(
        review_card_async(
            ServiceSession(session),
            card_id,
            action=action,
            storage=storage,
            settings=settings,
        )
    )


def finish_scope(session):
    """Run deferred fixture checks before a subsequent fixture action."""
    session.flush()
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    session.execute(text("SET CONSTRAINTS ALL DEFERRED"))


def seed_task(session, task):
    """Create a task and its event head under the installation human's authority."""
    actor = actor_context(session)
    session.add(task)
    session.flush()
    service_call(seed(ServiceSession(session), actor, task))
    return task


async def seed_task_async(session, task):
    actor = await actor_context_async(session)
    session.add(task)
    await session.flush([task])
    await seed(session, actor, task)
    return task


@contextmanager
def owner_session():
    """Use only the explicitly configured test owner's connection for setup."""
    engine = create_engine(os.environ["BID_TEST_ADMIN_URL"], hide_parameters=True)
    try:
        with Session(engine, expire_on_commit=False) as session, session.begin():
            assert session.scalar(text("SELECT current_user")) != "bid_app"
            yield session
    finally:
        engine.dispose()


async def related_task(session, source_task_id, *, name, task_id=None):
    """Create an independent task after checking the caller's real source access."""
    from app.models.entities import Task
    from app.services.task_access import access

    actor = await actor_context_async(session)
    await access(session, actor, source_task_id)
    with owner_session() as fixture:
        task = seed_task(fixture, Task(id=task_id or uuid4(), name=name))
        finish_scope(fixture)
    await actor_context_async(session)
    return task
