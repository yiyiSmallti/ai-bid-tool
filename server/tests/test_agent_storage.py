"""Persistence failure cases, specified before implementation.

Absent actor context must reject writes. Parent chains must
reject mismatched tasks; tokens cannot gain agent administration; messages,
terminal steps and provenance cannot be rewritten; only humans resolve pauses.
These tests use the real migrated PostgreSQL application role, never SQLite.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

TABLES = (
    "agent_principals",
    "agent_sessions",
    "agent_messages",
    "agent_steps",
    "agent_pauses",
    "agent_job_links",
)


@pytest.mark.parametrize("scope", ["agent:read", "agent:run", "agent:cancel"])
async def test_agent_management_cannot_be_delegated_to_token(api, headers, scope):
    response = await api.post(
        "/tokens",
        headers=headers,
        json={
            "name": "Synthetic prohibited delegation",
            "scopes": [scope],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code == 403


# Additional failure cases specified before guard changes: stale controllers may
# not publish checkpoints; expired delegation may pause but not dispatch; complete
# encrypted receipts cannot be attached later; renewal cannot restore lost scopes.
@pytest.fixture
async def agent_history(application, database):
    from uuid import uuid4

    from app.core.config import Settings
    from app.core.security import Secrets
    from app.models.agent import (
        AgentJobLink,
        AgentMessage,
        AgentPause,
        AgentPrincipal,
        AgentSession,
        AgentStep,
    )
    from app.models.entities import Document, Job, Task
    from app.services.auth import HUMAN_SCOPES, Identity, set_actor_context
    from app.services.task_access import seed

    owner = Identity(scopes=set(HUMAN_SCOPES))
    encrypted = Secrets.for_data(Settings()).encrypt("Synthetic retained content")
    async with application.state.db.transaction() as db:
        await set_actor_context(db, owner)
        task = Task(id=uuid4(), name="Synthetic agent persistence")
        db.add(task)
        await db.flush()
        await seed(db, owner, task)
        document = Document(
            id=uuid4(),
            task_id=task.id,
            name="synthetic.txt",
            sha256="1" * 64,
            storage_key="synthetic.txt",
            media_type="text/plain",
        )
        db.add(document)
        await db.flush()
        extraction = Job(
            id=uuid4(),
            task_id=task.id,
            document_id=document.id,
            kind="extract",
            cache_key="2" * 64,
            status="succeeded",
        )
        principal = AgentPrincipal(
            id=uuid4(),
            initial_grants=["task:read"],
            scopes=["task:read"],
            authority_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add_all([extraction, principal])
        await db.flush()
        state = AgentSession(
            id=uuid4(),
            task_id=task.id,
            document_id=document.id,
            extraction_job_id=extraction.id,
            principal_id=principal.id,
            limits={},
            tool_schema_sha256="3" * 64,
            model_sha256="4" * 64,
            input_sha256="5" * 64,
            start_idempotency_key=uuid4(),
            start_request_sha256="6" * 64,
            expires_at=datetime.now(UTC) + timedelta(hours=2),
        )
        db.add(state)
        await db.flush()
        await set_actor_context(db, owner)
        job = Job(
            id=uuid4(),
            task_id=task.id,
            document_id=document.id,
            kind="agent",
            initiated_by="builtin_agent",
            agent_principal_id=principal.id,
            agent_session_id=state.id,
            invocation_id=uuid4(),
            cache_key="7" * 64,
            status="running",
            run_id=uuid4(),
            lease_until=datetime.now(UTC) + timedelta(minutes=5),
        )
        db.add(job)
        await db.flush()
        executor = Identity(
            scopes={"task:read"},
            actor_kind="worker",
            principal_id=principal.id,
            session_id=state.id,
            job_id=job.id,
            run_id=job.run_id,
        )
        await set_actor_context(db, executor)
        state.current_job_id, state.current_run_id = job.id, job.run_id
        state.state, state.revision = "running", 2
        await db.flush()
        step = AgentStep(
            id=uuid4(),
            session_id=state.id,
            task_id=task.id,
            document_id=document.id,
            ordinal=1,
            kind="decision",
            created_by_job_id=job.id,
            created_by_run_id=job.run_id,
            last_transition_job_id=job.id,
            last_transition_run_id=job.run_id,
            schema_sha256="8" * 64,
        )
        db.add(step)
        await db.flush()
        message = AgentMessage(
            id=uuid4(),
            session_id=state.id,
            ordinal=1,
            role="system",
            content="Synthetic redacted content",
            content_enc=encrypted,
            content_sha256="9" * 64,
        )
        pause = AgentPause(
            id=uuid4(),
            session_id=state.id,
            kind="authority",
            question="Renew authority",
            question_enc=encrypted,
            input_sha256="a" * 64,
        )
        link = AgentJobLink(
            id=uuid4(),
            session_id=state.id,
            task_id=task.id,
            document_id=document.id,
            job_id=job.id,
            role="controller",
            owned=True,
        )
        db.add_all([message, pause, link])
        await db.flush()
        rows = {row.__tablename__: row.id for row in (principal, state, step, message, pause, link)}
    return {
        "owner": owner,
        "executor": executor,
        "session_id": state.id,
        "principal_id": principal.id,
        "rows": rows,
        "encrypted": encrypted,
    }


async def test_stale_execution_cannot_update_session_or_step(application, agent_history):
    from dataclasses import replace
    from uuid import uuid4

    from app.services.auth import set_actor_context

    actor = replace(agent_history["executor"], run_id=uuid4())
    for table in ("agent_sessions", "agent_steps"):
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction() as db:
                await set_actor_context(db, actor)
                await db.execute(
                    text(f"UPDATE {table} SET revision=revision+1 WHERE id=:id"),
                    {"id": agent_history["rows"][table]},
                )


async def test_message_is_append_only_and_receipt_must_be_complete(application, agent_history):
    from uuid import uuid4

    from app.services.auth import set_actor_context

    owner = agent_history["owner"]
    for mutation in (
        "UPDATE agent_messages SET content='rewritten' WHERE session_id=:sid",
        "INSERT INTO agent_messages(session_id,ordinal,role,content,content_enc,content_sha256,idempotency_key) VALUES (:sid,2,'human','synthetic',:enc,:hash,:key)",
    ):
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction() as db:
                await set_actor_context(db, owner)
                await db.execute(
                    text(mutation),
                    {
                        "sid": agent_history["session_id"],
                        "enc": agent_history["encrypted"],
                        "hash": "b" * 64,
                        "key": uuid4(),
                    },
                )


@pytest.mark.parametrize("cleanup", ["paused", "partial"])
@pytest.mark.parametrize("recovery", [False, True])
async def test_expired_authority_allows_cleanup_but_no_completed_step(
    application, agent_history, cleanup, recovery
):
    from dataclasses import replace

    from app.services.auth import set_actor_context

    owner, worker = agent_history["owner"], agent_history["executor"]
    async with application.state.db.transaction() as db:
        await set_actor_context(db, owner)
        await db.execute(
            text(
                "UPDATE agent_principals SET authority_expires_at=now()-interval '1 second' WHERE id=:id"
            ),
            {"id": worker.principal_id},
        )
    for state in ("planned", "completed"):
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction() as db:
                await set_actor_context(db, worker)
                await db.execute(
                    text(
                        "UPDATE agent_steps SET state=:state,revision=revision+1 WHERE session_id=:id"
                    ),
                    {"id": worker.session_id, "state": state},
                )
    if recovery:
        recovery_actor = replace(worker, job_id=None, run_id=None)
        # A sweeper has no execution fence and cannot supersede a live controller.
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction() as db:
                await set_actor_context(db, recovery_actor)
                await db.execute(
                    text("UPDATE agent_sessions SET state=:state,revision=revision+1 WHERE id=:id"),
                    {"id": worker.session_id, "state": cleanup},
                )
        async with application.state.db.transaction() as db:
            await set_actor_context(db, recovery_actor)
            await db.execute(
                text("UPDATE jobs SET status='failed',lease_until=NULL WHERE id=:id"),
                {"id": worker.job_id},
            )
        worker = recovery_actor
    async with application.state.db.transaction() as db:
        await set_actor_context(db, worker)
        await db.execute(
            text("UPDATE agent_sessions SET state=:state,revision=revision+1 WHERE id=:id"),
            {"id": worker.session_id, "state": cleanup},
        )


async def test_principal_renewal_cannot_restore_removed_scope(application, agent_history):
    from app.services.auth import set_actor_context

    owner = agent_history["owner"]
    async with application.state.db.transaction() as db:
        await set_actor_context(db, owner)
        await db.execute(
            text("UPDATE agent_principals SET scopes='[]' WHERE id=:id"),
            {"id": agent_history["principal_id"]},
        )
    with pytest.raises(DBAPIError):
        async with application.state.db.transaction() as db:
            await set_actor_context(db, owner)
            await db.execute(
                text("UPDATE agent_principals SET scopes='[\"task:read\"]' WHERE id=:id"),
                {"id": agent_history["principal_id"]},
            )
