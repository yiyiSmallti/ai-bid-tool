"""PostgreSQL acceptance failure scenarios written before slice 2 implementation.

Runtime SELECT/INSERT/UPDATE/DELETE under a foreign org or missing org context
must hide or reject every new table. Foreign task/extraction/document/card/thread
or historical card
revision must not be writable. Tokens, workers, inactive members and admin recovery
without membership must not discuss. Observers may discuss but cannot assign.
Assignees need current owner/contributor + edit authority; assignment does not
change content revisions. Assigned member removal/demotion (including handover)
must fail, while external Membership revocation remains allowed. Discussion rows
are immutable, first message is atomic, mention recipients are current task
members, request IDs are unique per task/author, and writes/events roll back
jointly. Events bind IDs, never plaintext, ciphertext, hashes or user labels.

The integrating session owns execution on an explicitly supplied bid_test runtime;
this module never starts or stops services. Artifacts stay in ignored data/work.
"""

import json
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.core.security import Secrets
from app.services.auth import ROLE_SCOPES
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_team_workflow_membership import add_member, person, workflow
from test_team_workflow_stream_acceptance import seed_scope

TABLES = ("requirement_workflows", "card_comment_threads", "card_comments", "card_comment_mentions")
MARKER = "SYNTHETIC-PRIVATE-DISCUSSION-NOT-FOR-EVENTS"
ARTIFACT = (
    Path(__file__).resolve().parents[2] / "data/work/team-workflow-acceptance/assignment-db.json"
)


async def context(session, user, *, role="admin", kind="session", scopes=None):
    await session.execute(
        text(
            "SELECT set_config('app.actor_user_id',:user,true),"
            "set_config('app.actor_kind',:kind,true),"
            "set_config('app.actor_token_id','',true),"
            "set_config('app.actor_scopes',:scopes,true)"
        ),
        {
            "user": str(user),
            "kind": kind,
            "scopes": json.dumps(sorted(ROLE_SCOPES[role] if scopes is None else scopes)),
        },
    )


async def scope_with_card(api, headers, tenants, admin_engine, *, tenant=0):
    scope = await seed_scope(api, headers, tenants, admin_engine, count=2, tenant=tenant)
    made = await api.post(
        f"/tasks/{scope['task_id']}/cards",
        headers=headers[tenant],
        json={
            "extraction_job_id": str(scope["job_id"]),
            "requirement_id": str(scope["requirement_ids"][0]),
            "content": {},
        },
    )
    assert made.status_code == 200, made.text
    card = made.json()["data"]
    scope.update(card_id=UUID(card["id"]), card_revision_id=UUID(card["revision_id"]))
    return scope


async def assignment(session, scope, *, user=None, job=None, requirement=None, revision=1):
    await session.execute(
        text(
            "INSERT INTO requirement_workflows(org_id,task_id,extraction_job_id,"
            "requirement_id,assignee_user_id,assignment_revision,changed_by_user_id,"
            "changed_at,last_reason_ciphertext) VALUES(:org,:task,:job,:req,:user,"
            ":revision,:actor,clock_timestamp(),:reason)"
        ),
        {
            "org": scope["org_id"],
            "task": scope["task_id"],
            "job": job or scope["job_id"],
            "req": requirement or scope["requirement_ids"][0],
            "user": user or scope["user_id"],
            "actor": scope["user_id"],
            "revision": revision,
            "reason": Secrets.for_data(Settings()).encrypt("Synthetic assignment reason"),
        },
    )


async def discussion(
    session, scope, *, author=None, mentioned=None, card=None, thread=None, request=None
):
    thread_id, comment_id = thread or uuid4(), uuid4()
    card_id = card or scope["card_id"]
    actor = author or scope["user_id"]
    if thread is None:
        await session.execute(
            text(
                "INSERT INTO card_comment_threads(id,org_id,task_id,card_id,"
                "created_card_revision_id,revision,created_by_user_id) "
                "VALUES(:id,:org,:task,:card,:rev,1,:actor)"
            ),
            {
                "id": thread_id,
                "org": scope["org_id"],
                "task": scope["task_id"],
                "card": card_id,
                "rev": scope["card_revision_id"],
                "actor": actor,
            },
        )
    await session.execute(
        text(
            "INSERT INTO card_comments(id,org_id,task_id,card_id,thread_id,author_user_id,"
            "body_ciphertext,body_sha256,request_sha256,client_request_id) "
            "VALUES(:id,:org,:task,:card,:thread,:actor,:body,:hash,:hash,:request)"
        ),
        {
            "id": comment_id,
            "org": scope["org_id"],
            "task": scope["task_id"],
            "card": card_id,
            "thread": thread_id,
            "actor": actor,
            "body": Secrets.for_data(Settings()).encrypt(MARKER),
            "hash": sha256(MARKER.encode()).hexdigest(),
            "request": request or uuid4(),
        },
    )
    for user in mentioned or []:
        await session.execute(
            text(
                "INSERT INTO card_comment_mentions(org_id,task_id,card_id,thread_id,"
                "comment_id,user_id) VALUES(:org,:task,:card,:thread,:comment,:user)"
            ),
            {
                "org": scope["org_id"],
                "task": scope["task_id"],
                "card": card_id,
                "thread": thread_id,
                "comment": comment_id,
                "user": user,
            },
        )
    return thread_id, comment_id


@pytest.mark.parametrize("table", TABLES)
async def test_assignment_discussion_force_rls(api, headers, tenants, admin_engine, table):
    foreign = await scope_with_card(api, headers, tenants, admin_engine, tenant=1)
    db = Database(Settings())
    try:
        async with db.transaction(foreign["org_id"]) as session:
            await context(session, foreign["user_id"])
            await assignment(session, foreign)
            await discussion(session, foreign, mentioned=[foreign["user_id"]])
        with admin_engine.connect() as connection:
            assert all(
                connection.execute(
                    text(
                        "SELECT relrowsecurity,relforcerowsecurity "
                        "FROM pg_class WHERE oid=to_regclass(:name)"
                    ),
                    {"name": table},
                ).one()
            )
            row = connection.scalar(text(f"SELECT to_jsonb(t) FROM {table} t LIMIT 1"))
        foreign_id = UUID(row["id"])
        async with db.transaction(tenants["orgs"][0]) as session:
            assert not (await session.execute(text(f"SELECT id FROM {table}"))).all()
            await session.execute(text("SELECT set_config('app.current_org','',true)"))
            assert not (await session.execute(text(f"SELECT id FROM {table}"))).all()
        for missing_context in (False, True):
            parameters = {"id": foreign_id}
            update = text(f"UPDATE {table} SET created_at=created_at WHERE id=:id")
            if table == "requirement_workflows":
                async with db.transaction(tenants["orgs"][0]) as session:
                    await context(session, tenants["users"][0])
                    if missing_context:
                        await session.execute(text("SELECT set_config('app.current_org','',true)"))
                    assert (await session.execute(update, parameters)).rowcount == 0
            else:
                with pytest.raises(DBAPIError):
                    async with db.transaction(tenants["orgs"][0]) as session:
                        await context(session, tenants["users"][0])
                        if missing_context:
                            await session.execute(
                                text("SELECT set_config('app.current_org','',true)")
                            )
                        await session.execute(update, parameters)
            with pytest.raises(DBAPIError):
                async with db.transaction(tenants["orgs"][0]) as session:
                    await context(session, tenants["users"][0])
                    if missing_context:
                        await session.execute(text("SELECT set_config('app.current_org','',true)"))
                    await session.execute(text(f"DELETE FROM {table} WHERE id=:id"), parameters)
            if missing_context:
                missing_row = {**row, "id": str(uuid4())}
                with pytest.raises(DBAPIError):
                    async with db.transaction(tenants["orgs"][0]) as session:
                        await context(session, tenants["users"][0])
                        await session.execute(text("SELECT set_config('app.current_org','',true)"))
                        await session.execute(
                            text(
                                f"INSERT INTO {table} SELECT * FROM "
                                f"jsonb_populate_record(NULL::{table},CAST(:row AS jsonb))"
                            ),
                            {"row": json.dumps(missing_row, default=str)},
                        )
        for org in (foreign["org_id"], tenants["orgs"][0]):
            row.update(id=str(uuid4()), org_id=str(org))
            with pytest.raises(DBAPIError):
                async with db.transaction(tenants["orgs"][0]) as session:
                    await context(session, tenants["users"][0])
                    await session.execute(
                        text(
                            f"INSERT INTO {table} SELECT * FROM "
                            f"jsonb_populate_record(NULL::{table},CAST(:row AS jsonb))"
                        ),
                        {"row": json.dumps(row, default=str)},
                    )
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("kind", ["token", "worker", "agent"])
async def test_direct_sql_nonhuman_discussion_and_assignment_denied(
    api, headers, tenants, admin_engine, kind
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    db = Database(Settings())
    try:
        for operation in (assignment, discussion):
            with pytest.raises(DBAPIError):
                async with db.transaction(scope["org_id"]) as session:
                    await context(
                        session,
                        scope["user_id"],
                        kind=kind,
                        scopes={"card:assign", "card:comment", "card:write"},
                    )
                    await operation(session, scope)
    finally:
        await db.engine.dispose()


async def test_observer_discussion_ids_events_and_immutable_history(
    api, headers, tenants, admin_engine
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    observer, _ = await person(api, admin_engine, scope["org_id"], "viewer")
    added = await add_member(api, headers[0], scope["task_id"], observer, 1, "observer")
    assert added.status_code == 200, added.text
    db = Database(Settings())
    try:
        async with db.transaction(scope["org_id"]) as session:
            await context(
                session, observer, role="viewer", scopes={"card:comment", "task:read", "card:read"}
            )
            before = await session.scalar(
                text("SELECT revision FROM response_cards WHERE id=:id"), {"id": scope["card_id"]}
            )
            thread, comment = await discussion(
                session, scope, author=observer, mentioned=[scope["user_id"]]
            )
            assert (
                await session.scalar(
                    text("SELECT revision FROM response_cards WHERE id=:id"),
                    {"id": scope["card_id"]},
                )
                == before
            )
        async with db.transaction(scope["org_id"]) as session:
            payloads = (
                await session.scalars(
                    text(
                        "SELECT payload FROM task_events WHERE task_id=:task "
                        "AND event_kind='board_changed' ORDER BY seq"
                    ),
                    {"task": scope["task_id"]},
                )
            ).all()
            addressed = [p for p in payloads if p.get("thread_id") == str(thread)]
            assert addressed and any(p.get("comment_id") == str(comment) for p in addressed)
            assert all(str(scope["card_id"]) in p["card_ids"] for p in addressed)
            encoded = json.dumps(payloads)
            assert MARKER not in encoded and sha256(MARKER.encode()).hexdigest() not in encoded
        for table, identifier in (("card_comment_threads", thread), ("card_comments", comment)):
            for operation in ("UPDATE", "DELETE"):
                with pytest.raises(DBAPIError):
                    async with db.transaction(scope["org_id"]) as session:
                        await context(session, observer, role="viewer", scopes={"card:comment"})
                        statement = (
                            f"UPDATE {table} SET created_at=created_at WHERE id=:id"
                            if operation == "UPDATE"
                            else f"DELETE FROM {table} WHERE id=:id"
                        )
                        await session.execute(text(statement), {"id": identifier})
        with pytest.raises(DBAPIError):
            async with db.transaction(scope["org_id"]) as session:
                await context(session, observer, role="viewer", scopes={"card:assign"})
                await assignment(session, scope, user=observer)
        ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
        ARTIFACT.write_text(
            json.dumps(
                {
                    "force_rls": list(TABLES),
                    "observer_comment": True,
                    "card_revision_unchanged": True,
                    "event_id_binding": True,
                    "plaintext_absent": True,
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        await db.engine.dispose()


async def test_assigned_member_demotion_blocked_external_revocation_allowed(
    api, headers, tenants, admin_engine
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    member, _ = await person(api, admin_engine, scope["org_id"])
    assert (await add_member(api, headers[0], scope["task_id"], member, 1)).status_code == 200
    db = Database(Settings())
    try:
        async with db.transaction(scope["org_id"]) as session:
            await context(session, scope["user_id"])
            await assignment(session, scope, user=member)
        for mutation in (
            "active=false",
            "role='observer'",
            "role='reviewer',review_domains='[\"commercial\"]'",
        ):
            with pytest.raises(DBAPIError, match="member_has_assignments"):
                async with db.transaction(scope["org_id"]) as session:
                    await context(session, scope["user_id"])
                    await session.execute(
                        text(
                            f"UPDATE task_members SET {mutation},revision=revision+1 "
                            "WHERE org_id=:org AND task_id=:task AND user_id=:user"
                        ),
                        {"org": scope["org_id"], "task": scope["task_id"], "user": member},
                    )
        with admin_engine.begin() as connection:
            connection.execute(
                text("UPDATE memberships SET active=false WHERE org_id=:org AND user_id=:user"),
                {"org": scope["org_id"], "user": member},
            )
        with pytest.raises(DBAPIError):
            async with db.transaction(scope["org_id"]) as session:
                await context(session, scope["user_id"])
                await assignment(
                    session, scope, user=member, requirement=scope["requirement_ids"][1]
                )
    finally:
        await db.engine.dispose()


async def test_first_message_parent_mentions_and_request_guards(
    api, headers, tenants, admin_engine
):
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    other = await scope_with_card(api, headers, tenants, admin_engine)
    stranger, _ = await person(api, admin_engine, scope["org_id"])
    db = Database(Settings())
    try:
        async with db.transaction(scope["org_id"]) as session:
            await context(session, scope["user_id"])
            thread, _ = await discussion(session, scope)
        for invalid in (
            dict(card=other["card_id"]),
            dict(mentioned=[stranger]),
            dict(mentioned=[tenants["users"][1]]),
            dict(thread=uuid4()),
        ):
            with pytest.raises(DBAPIError):
                async with db.transaction(scope["org_id"]) as session:
                    await context(session, scope["user_id"])
                    await discussion(session, scope, **invalid)
        for invalid in (
            dict(job=other["job_id"]),
            dict(requirement=other["requirement_ids"][0]),
            dict(revision=0),
        ):
            with pytest.raises(DBAPIError):
                async with db.transaction(scope["org_id"]) as session:
                    await context(session, scope["user_id"])
                    await assignment(session, scope, **invalid)
        with pytest.raises(DBAPIError, match="first message"):
            async with db.transaction(scope["org_id"]) as session:
                await context(session, scope["user_id"])
                await session.execute(
                    text(
                        "INSERT INTO card_comment_threads(org_id,task_id,card_id,"
                        "created_card_revision_id,created_by_user_id,revision) "
                        "VALUES(:org,:task,:card,:rev,:user,1)"
                    ),
                    {
                        "org": scope["org_id"],
                        "task": scope["task_id"],
                        "card": scope["card_id"],
                        "rev": scope["card_revision_id"],
                        "user": scope["user_id"],
                    },
                )
                await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        request = uuid4()
        async with db.transaction(scope["org_id"]) as session:
            await context(session, scope["user_id"])
            await discussion(session, scope, thread=thread, request=request)
        with pytest.raises(DBAPIError):
            async with db.transaction(scope["org_id"]) as session:
                await context(session, scope["user_id"])
                await discussion(session, scope, thread=thread, request=request)
        current = await workflow(api, headers[0], scope["task_id"])
        archived = await api.post(
            f"/tasks/{scope['task_id']}/archive",
            headers=headers[0],
            json={
                "expected_revision": current["revision"],
                "reason": "Synthetic discussion archive",
            },
        )
        assert archived.status_code == 200, archived.text
        with pytest.raises(DBAPIError):
            async with db.transaction(scope["org_id"]) as session:
                await context(session, scope["user_id"])
                await discussion(session, scope, thread=thread)
    finally:
        await db.engine.dispose()


async def test_owner_ciphertext_rotation_preserves_discussion_assignment_events(
    api, headers, tenants, admin_engine
):
    """Failure scenario: immutable history blocks rewrap, or rewrap fabricates events.

    Only the migration/table owner may rewrite ciphertext without changing actors,
    timestamps, hashes or revisions; runtime scope flags must not grant this path.
    """
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    db = Database(Settings())
    crypto = Secrets.for_data(Settings())
    try:
        async with db.transaction(scope["org_id"]) as session:
            await context(session, scope["user_id"])
            await assignment(session, scope)
            _, comment = await discussion(session, scope)
        with admin_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.current_org',:org,true)"),
                {"org": str(scope["org_id"])},
            )
            head = connection.scalar(
                text("SELECT last_seq FROM task_event_heads WHERE task_id=:task"),
                {"task": scope["task_id"]},
            )
            connection.execute(
                text("UPDATE card_comments SET body_ciphertext=:body WHERE id=:id"),
                {"body": crypto.encrypt(MARKER), "id": comment},
            )
            connection.execute(
                text(
                    "UPDATE requirement_workflows SET last_reason_ciphertext=:reason "
                    "WHERE task_id=:task"
                ),
                {"reason": crypto.encrypt("Synthetic assignment reason"), "task": scope["task_id"]},
            )
            assert (
                connection.scalar(
                    text("SELECT last_seq FROM task_event_heads WHERE task_id=:task"),
                    {"task": scope["task_id"]},
                )
                == head
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT assignment_revision FROM requirement_workflows WHERE task_id=:task"
                    ),
                    {"task": scope["task_id"]},
                )
                == 1
            )
        with pytest.raises(DBAPIError):
            async with db.transaction(scope["org_id"]) as session:
                await context(session, scope["user_id"])
                await session.execute(
                    text(
                        "UPDATE requirement_workflows SET last_reason_ciphertext=:reason "
                        "WHERE task_id=:task"
                    ),
                    {
                        "reason": crypto.encrypt("Synthetic assignment reason"),
                        "task": scope["task_id"],
                    },
                )
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("missing", ["task:read", "card:read"])
async def test_direct_sql_comment_cannot_bypass_read_grants(
    api, headers, tenants, admin_engine, missing
):
    """Failure scenario: comment grant alone bypasses its existing task/card ceiling."""
    scope = await scope_with_card(api, headers, tenants, admin_engine)
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError):
            async with db.transaction(scope["org_id"]) as session:
                await context(
                    session,
                    scope["user_id"],
                    scopes={"card:comment", "task:read", "card:read"} - {missing},
                )
                await discussion(session, scope)
    finally:
        await db.engine.dispose()
