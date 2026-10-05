"""Persistence failure cases, specified before implementation.

Cross-tenant and absent context must hide all six tables. Parent chains must
reject foreign owners/tasks; tokens cannot gain agent administration; messages,
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


# Budget persistence failures: None on nonbudget pauses must bind SQL NULL,
# typed budget references must retain their JSON object, and the database must
# reject a budget without a reference or a nonbudget pause with JSON content.
@pytest.mark.parametrize("kind", ["human_action", "authority", "recovery"])
def test_pause_budget_none_binds_sql_null(kind):
    from app.models.agent import AgentPause
    from sqlalchemy import insert
    from sqlalchemy.dialects.postgresql import psycopg

    dialect = psycopg.dialect()
    statement = insert(AgentPause).values(kind=kind, budget_ref=None).compile(dialect=dialect)
    column_type = AgentPause.__table__.c.budget_ref.type.dialect_impl(dialect)
    bind = column_type.bind_processor(dialect)
    assert bind(statement.params["budget_ref"]) is None


def test_pause_budget_reference_binds_json_object():
    from app.models.agent import AgentPause
    from app.schemas.agent_contracts import BudgetDependencyRef
    from sqlalchemy.dialects.postgresql import psycopg

    reference = BudgetDependencyRef(question_ref="job:synthetic:budget", revision_ref="1")
    dialect = psycopg.dialect()
    column_type = AgentPause.__table__.c.budget_ref.type.dialect_impl(dialect)
    bound = column_type.bind_processor(dialect)(reference.model_dump(mode="json"))
    assert bound.obj == reference.model_dump(mode="json")


@pytest.mark.parametrize("table", TABLES)
async def test_agent_table_forces_rls(application, tenants, admin_engine, table):
    with admin_engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=CAST(:t AS regclass)"
            ),
            {"t": table},
        ).one()
        assert row == (True, True)
        policy = connection.execute(
            text("SELECT qual,with_check FROM pg_policies WHERE tablename=:t"), {"t": table}
        ).one()
        assert "current_org" in policy.qual and "current_org" in policy.with_check
    for org in (tenants["orgs"][1], None):
        async with application.state.db.transaction(org) as session:
            assert (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar() == 0
    with pytest.raises(DBAPIError):
        async with application.state.db.transaction() as session:
            await session.execute(
                text(f"INSERT INTO {table}(org_id) VALUES (:org)"), {"org": tenants["orgs"][0]}
            )


@pytest.mark.parametrize("scope", ["agent:read", "agent:run", "agent:cancel"])
async def test_agent_management_cannot_be_delegated_to_token(api, headers, scope):
    response = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Synthetic prohibited delegation",
            "scopes": [scope],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code == 403


async def test_principal_rejects_foreign_membership_and_missing_actor(application, tenants):
    with pytest.raises(DBAPIError):
        async with application.state.db.transaction(tenants["orgs"][0]) as session:
            await session.execute(
                text("""
                INSERT INTO agent_principals(org_id,user_id,membership_id,initial_grants,scopes,authority_expires_at)
                SELECT :org,:user,id,'["task:read"]','["task:read"]',now()+interval '1 hour'
                FROM memberships WHERE org_id=:org
            """),
                {"org": tenants["orgs"][0], "user": tenants["users"][1]},
            )


# Additional failure cases specified before guard changes: stale controllers may
# not publish checkpoints; expired delegation may pause but not dispatch; complete
# encrypted receipts cannot be attached later; renewal cannot restore lost scopes.
@pytest.fixture
async def agent_history(application, tenants):
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
    from app.models.entities import Document, Job, Membership, Task
    from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
    from app.services.task_workflow import seed
    from sqlalchemy import select

    org, user = tenants["orgs"][0], tenants["users"][0]
    owner = Identity(user, org, set(ROLE_SCOPES["admin"]), "admin")
    encrypted = Secrets.for_data(Settings()).encrypt("Synthetic retained content")
    async with application.state.db.transaction(org) as db:
        await set_actor_context(db, owner)
        member = await db.scalar(select(Membership).where(Membership.user_id == user))
        task = Task(id=uuid4(), org_id=org, name="Synthetic agent persistence", created_by=user)
        db.add(task)
        await db.flush()
        await seed(db, owner, task)
        document = Document(
            id=uuid4(),
            org_id=org,
            task_id=task.id,
            name="synthetic.txt",
            sha256="1" * 64,
            storage_key=f"org/{org}/synthetic.txt",
            media_type="text/plain",
        )
        db.add(document)
        await db.flush()
        extraction = Job(
            id=uuid4(),
            org_id=org,
            task_id=task.id,
            document_id=document.id,
            kind="extract",
            cache_key="2" * 64,
            status="succeeded",
        )
        principal = AgentPrincipal(
            id=uuid4(),
            org_id=org,
            user_id=user,
            membership_id=member.id,
            initial_grants=["task:read"],
            scopes=["task:read"],
            authority_expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        db.add_all([extraction, principal])
        await db.flush()
        state = AgentSession(
            id=uuid4(),
            org_id=org,
            task_id=task.id,
            document_id=document.id,
            extraction_job_id=extraction.id,
            principal_id=principal.id,
            owner_user_id=user,
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
        owner.principal_id, owner.session_id = principal.id, state.id
        await set_actor_context(db, owner)
        job = Job(
            id=uuid4(),
            org_id=org,
            task_id=task.id,
            document_id=document.id,
            kind="agent",
            cache_key="7" * 64,
            status="running",
            run_id=uuid4(),
            lease_until=datetime.now(UTC) + timedelta(minutes=5),
        )
        db.add(job)
        await db.flush()
        executor = Identity(
            user,
            org,
            {"task:read"},
            "admin",
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
            org_id=org,
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
            org_id=org,
            session_id=state.id,
            ordinal=1,
            role="system",
            content="Synthetic redacted content",
            content_enc=encrypted,
            content_sha256="9" * 64,
        )
        pause = AgentPause(
            id=uuid4(),
            org_id=org,
            session_id=state.id,
            kind="authority",
            question="Renew authority",
            question_enc=encrypted,
            input_sha256="a" * 64,
        )
        link = AgentJobLink(
            id=uuid4(),
            org_id=org,
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
    return {"owner": owner, "executor": executor, "rows": rows, "encrypted": encrypted}


async def _insert_pause_budget_case(db, history, kind, budget_ref):
    from app.models.agent import AgentPause
    from app.services.auth import set_actor_context

    worker = history["executor"]
    await set_actor_context(db, worker)
    # Release the fixture's pending slot through its permitted terminal transition.
    await db.execute(
        text("UPDATE agent_pauses SET status='cancelled' WHERE id=:id"),
        {"id": history["rows"]["agent_pauses"]},
    )
    pause = AgentPause(
        org_id=worker.org_id,
        session_id=worker.session_id,
        kind=kind,
        question="Synthetic budget persistence check",
        question_enc=history["encrypted"],
        input_sha256="c" * 64,
        budget_ref=budget_ref,
    )
    db.add(pause)
    await db.flush()
    return pause


@pytest.mark.parametrize("kind", ["human_action", "authority", "recovery", "budget"])
async def test_pause_budget_reference_persisted_by_kind(application, agent_history, kind):
    from app.schemas.agent_contracts import BudgetDependencyRef

    reference = (
        BudgetDependencyRef(question_ref="job:synthetic:budget", revision_ref="1").model_dump(
            mode="json"
        )
        if kind == "budget"
        else None
    )
    async with application.state.db.transaction(agent_history["executor"].org_id) as db:
        pause = await _insert_pause_budget_case(db, agent_history, kind, reference)
        stored = (
            await db.execute(
                text(
                    "SELECT budget_ref IS NULL AS sql_null,jsonb_typeof(budget_ref) AS json_type,"
                    "budget_ref FROM agent_pauses WHERE id=:id"
                ),
                {"id": pause.id},
            )
        ).one()
        if kind == "budget":
            assert stored.sql_null is False and stored.json_type == "object"
            assert (
                BudgetDependencyRef.model_validate(stored.budget_ref).model_dump(mode="json")
                == reference
            )
        else:
            assert stored.sql_null is True and stored.budget_ref is None


@pytest.mark.parametrize(
    ("kind", "value"),
    [("budget", "absent")]
    + [
        (kind, value)
        for kind in ("human_action", "authority", "recovery")
        for value in ("object", "json_null")
    ],
)
async def test_pause_budget_check_rejects_mismatched_reference(
    application, agent_history, kind, value
):
    from app.schemas.agent_contracts import BudgetDependencyRef
    from sqlalchemy import JSON

    reference = {
        "absent": None,
        "object": BudgetDependencyRef(
            question_ref="job:synthetic:budget", revision_ref="1"
        ).model_dump(mode="json"),
        "json_null": JSON.NULL,
    }[value]
    with pytest.raises(DBAPIError) as rejected:
        async with application.state.db.transaction(agent_history["executor"].org_id) as db:
            await _insert_pause_budget_case(db, agent_history, kind, reference)
    assert rejected.value.orig.sqlstate == "23514"
    assert rejected.value.orig.diag.constraint_name == "agent_pause_budget"


@pytest.mark.parametrize("table", TABLES)
async def test_seeded_agent_tables_hide_foreign_reads_and_mutations(
    application, tenants, agent_history, table
):
    from uuid import uuid4

    from app.models.entities import Base
    from sqlalchemy import insert, select

    org = tenants["orgs"][1]
    row_id = agent_history["rows"][table]
    model = Base.metadata.tables[table]
    async with application.state.db.transaction(tenants["orgs"][0]) as db:
        original = dict(
            (await db.execute(select(model).where(model.c.id == row_id))).mappings().one()
        )
    async with application.state.db.transaction(org) as db:
        assert (
            await db.execute(text(f"SELECT id FROM {table} WHERE id=:id"), {"id": row_id})
        ).first() is None
        assert (
            await db.execute(
                text(f"UPDATE {table} SET id=id WHERE id=:id RETURNING id"), {"id": row_id}
            )
        ).first() is None
        # Deletion is never granted to the runtime role, even for its own history.
    with pytest.raises(DBAPIError):
        async with application.state.db.transaction(org) as db:
            await db.execute(text(f"DELETE FROM {table} WHERE id=:id"), {"id": row_id})
    for context in (org, None):
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction(context) as db:
                await db.execute(insert(model).values({**original, "id": uuid4()}))


async def test_stale_execution_cannot_update_session_or_step(application, agent_history):
    from dataclasses import replace
    from uuid import uuid4

    from app.services.auth import set_actor_context

    actor = replace(agent_history["executor"], run_id=uuid4())
    for table in ("agent_sessions", "agent_steps"):
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction(actor.org_id) as db:
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
        "INSERT INTO agent_messages(org_id,session_id,ordinal,role,author_user_id,content,content_enc,content_sha256,idempotency_key) VALUES (:org,:sid,2,'human',:user,'synthetic',:enc,:hash,:key)",
    ):
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction(owner.org_id) as db:
                await set_actor_context(db, owner)
                await db.execute(
                    text(mutation),
                    {
                        "org": owner.org_id,
                        "sid": owner.session_id,
                        "user": owner.user_id,
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
    async with application.state.db.transaction(owner.org_id) as db:
        await set_actor_context(db, owner)
        await db.execute(
            text(
                "UPDATE agent_principals SET authority_expires_at=now()-interval '1 second' WHERE id=:id"
            ),
            {"id": worker.principal_id},
        )
    for state in ("planned", "completed"):
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction(worker.org_id) as db:
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
            async with application.state.db.transaction(worker.org_id) as db:
                await set_actor_context(db, recovery_actor)
                await db.execute(
                    text("UPDATE agent_sessions SET state=:state,revision=revision+1 WHERE id=:id"),
                    {"id": worker.session_id, "state": cleanup},
                )
        async with application.state.db.transaction(worker.org_id) as db:
            await set_actor_context(db, recovery_actor)
            await db.execute(
                text("UPDATE jobs SET status='failed',lease_until=NULL WHERE id=:id"),
                {"id": worker.job_id},
            )
        worker = recovery_actor
    async with application.state.db.transaction(worker.org_id) as db:
        await set_actor_context(db, worker)
        await db.execute(
            text("UPDATE agent_sessions SET state=:state,revision=revision+1 WHERE id=:id"),
            {"id": worker.session_id, "state": cleanup},
        )


async def test_principal_renewal_cannot_restore_removed_scope(application, agent_history):
    from app.services.auth import set_actor_context

    owner = agent_history["owner"]
    async with application.state.db.transaction(owner.org_id) as db:
        await set_actor_context(db, owner)
        await db.execute(
            text("UPDATE agent_principals SET scopes='[]' WHERE id=:id"), {"id": owner.principal_id}
        )
    with pytest.raises(DBAPIError):
        async with application.state.db.transaction(owner.org_id) as db:
            await set_actor_context(db, owner)
            await db.execute(
                text("UPDATE agent_principals SET scopes='[\"task:read\"]' WHERE id=:id"),
                {"id": owner.principal_id},
            )
