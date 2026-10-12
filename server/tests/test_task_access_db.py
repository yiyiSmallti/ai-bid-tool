"""Retained creator, live task-access, token and PostgreSQL isolation acceptance."""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.models.entities import ApiToken
from app.services.auth import HUMAN_ONLY_SCOPES, ROLE_SCOPES
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from task_access_fixtures import add_member, archive_task, new_task, person, workflow

TABLES = ("task_workflows", "task_members", "task_event_heads", "task_events")
HUMAN_SCOPES = tuple(sorted(HUMAN_ONLY_SCOPES))


async def test_creation_membership_masking_archive(api, headers, tenants, admin_engine):
    task = await new_task(api, headers[0])
    own = await workflow(api, headers[0], task)
    assert own["owner_user_id"] == str(tenants["users"][0])
    assert own["revision"] == 1 and own["state"] == "active"
    stranger, stranger_headers = await person(api, admin_engine, tenants["orgs"][0])
    for target, auth in ((task, headers[1]), (task, stranger_headers), (str(uuid4()), headers[0])):
        response = await api.get(f"/tasks/{target}/progress", headers=auth)
        assert response.status_code == 404
        assert response.json()["data"]["error"]["code"] == "not_found"
    added = await add_member(api, headers[0], task, stranger, 1)
    assert added.active
    assert (await workflow(api, stranger_headers, task))["revision"] == 2
    await archive_task(api, headers[0], task)
    assert (await workflow(api, stranger_headers, task))["state"] == "archived"
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE memberships SET active=false WHERE org_id=:org AND user_id=:user"),
            {"org": tenants["orgs"][0], "user": stranger},
        )
    assert (await api.get(f"/tasks/{task}/progress", headers=stranger_headers)).status_code == 404


@pytest.mark.parametrize("scope", HUMAN_SCOPES)
async def test_token_human_scopes_rejected_api_and_sql(api, headers, tenants, scope):
    response = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Synthetic forbidden workflow token",
            "scopes": [scope],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code == 403
    db = Database(Settings())
    try:
        with pytest.raises(DBAPIError):
            async with db.transaction(tenants["orgs"][0]) as session:
                session.add(
                    ApiToken(
                        org_id=tenants["orgs"][0],
                        user_id=tenants["users"][0],
                        name="forbidden",
                        digest="d" * 64,
                        scopes=[scope],
                        expires_at=datetime.now(UTC) + timedelta(hours=1),
                    )
                )
                await session.flush()
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize("table", TABLES)
async def test_new_tables_force_rls_and_cross_org_sql(api, headers, tenants, admin_engine, table):
    await new_task(api, headers[0])
    foreign = await new_task(api, headers[1])
    with admin_engine.connect() as connection:
        flags = connection.execute(
            text(
                "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:table)"
            ),
            {"table": table},
        ).one()
        assert all(flags)
        foreign_id = connection.scalar(
            text(f"SELECT id FROM {table} WHERE task_id=:task LIMIT 1"), {"task": UUID(foreign)}
        )
        assert foreign_id
        row = connection.scalar(
            text(f"SELECT to_jsonb(t) FROM {table} t WHERE id=:id"), {"id": foreign_id}
        )
        row["id"] = str(uuid4())
    db = Database(Settings())
    try:
        async with db.transaction(tenants["orgs"][0]) as session:
            assert not (
                await session.execute(
                    text(f"SELECT id FROM {table} WHERE id=:id"), {"id": foreign_id}
                )
            ).all()
        if table in {"task_events", "task_event_heads"}:
            with pytest.raises(DBAPIError):
                async with db.transaction(tenants["orgs"][0]) as session:
                    await session.execute(
                        text(f"UPDATE {table} SET created_at=created_at WHERE id=:id"),
                        {"id": foreign_id},
                    )
        else:
            async with db.transaction(tenants["orgs"][0]) as session:
                result = await session.execute(
                    text(f"UPDATE {table} SET created_at=created_at WHERE id=:id"),
                    {"id": foreign_id},
                )
                assert result.rowcount == 0
        with pytest.raises(DBAPIError):
            async with db.transaction(tenants["orgs"][0]) as session:
                await session.execute(text(f"DELETE FROM {table} WHERE id=:id"), {"id": foreign_id})
        for foreign_row in (row, {**row, "org_id": str(tenants["orgs"][0])}):
            with pytest.raises(DBAPIError):
                async with db.transaction(tenants["orgs"][0]) as session:
                    await session.execute(
                        text(
                            f"INSERT INTO {table} SELECT * FROM jsonb_populate_record(NULL::{table}, CAST(:row AS jsonb))"
                        ),
                        {"row": json.dumps(foreign_row, default=str)},
                    )
        # Missing context must hide every business row, even on the runtime connection.
        async with db.transaction(tenants["orgs"][0]) as session:
            await session.execute(text("SELECT set_config('app.current_org','',true)"))
            assert not (await session.execute(text(f"SELECT id FROM {table}"))).all()
    finally:
        await db.engine.dispose()


async def test_direct_sql_owner_and_event_guards(api, headers, tenants):
    task = UUID(await new_task(api, headers[0]))
    org, user = tenants["orgs"][0], tenants["users"][0]
    db = Database(Settings())
    try:
        for statement in (
            "UPDATE task_members SET role='contributor', revision=revision+1 WHERE task_id=:task AND role='owner'",
            "UPDATE task_workflows SET owner_user_id=:other, revision=revision+1, access_epoch=access_epoch+1 WHERE task_id=:task",
            "UPDATE task_events SET payload='{}' WHERE task_id=:task",
        ):
            with pytest.raises(DBAPIError):
                async with db.transaction(org) as session:
                    await session.execute(
                        text(
                            "SELECT set_config('app.actor_kind','session',true),set_config('app.actor_user_id',:user,true),set_config('app.actor_scopes',:scopes,true)"
                        ),
                        {"user": str(user), "scopes": json.dumps(sorted(ROLE_SCOPES["admin"]))},
                    )
                    await session.execute(
                        text(statement), {"task": task, "other": tenants["users"][1]}
                    )
                    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    finally:
        await db.engine.dispose()


@pytest.mark.parametrize(
    "role,org_role,domains", [("observer", "bidder", []), ("reviewer", "technical", ["technical"])]
)
async def test_read_only_task_roles_cannot_upload(
    api, headers, tenants, admin_engine, pdf_bytes, role, org_role, domains
):
    task = await new_task(api, headers[0])
    user, member_headers = await person(api, admin_engine, tenants["orgs"][0], org_role)
    assert (await add_member(api, headers[0], task, user, 1, role, domains)).active
    assert (await workflow(api, member_headers, task))["state"] == "active"
    response = await api.post(
        f"/tasks/{task}/documents",
        headers=member_headers,
        files={"file": ("synthetic.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 403


async def test_token_creation_seeds_human_owner(api, headers, tenants):
    created = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Synthetic task creator",
            "scopes": ["task:create", "task:read"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert created.status_code == 200
    token_headers = {**headers[0], "Authorization": "Bearer " + created.json()["data"]["token"]}
    task = await new_task(api, token_headers)
    state = await workflow(api, token_headers, task)
    assert state["owner_user_id"] == str(tenants["users"][0])
