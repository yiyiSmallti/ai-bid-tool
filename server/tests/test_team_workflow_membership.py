"""API/PostgreSQL acceptance failure inventory (written before implementation).

* Two-org and nonexistent task IDs have identical 404; same-org nonmembers see 404.
* Scope denial follows visibility; disabled User/Membership immediately removes access.
* Tokens cannot mutate members/archive, including direct SQL token scope insertion.
* Observer/reviewer cannot draft; review domains cannot exceed current org role.
* Owner cannot be removed, doubled or lost; handover and expected-revision races
  commit one result. Archived task blocks members and all content writes.
* Queued/running jobs and pending/unknown calls block archive, including failed jobs.
* Every new table FORCE RLS rejects foreign-org writes and missing org context.
* Composite parent bindings and immutable event rows reject direct SQL bypass.
* Reviewed import is atomic, unresolved tasks and live jobs block cutover;
  rerun imports must not replace existing authorization.

Execution requires an explicitly supplied isolated PostgreSQL test runtime.
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.core.config import Settings
from app.core.db import Database
from app.models.entities import ApiToken, Membership, Task, User
from app.services.auth import ROLE_SCOPES
from app.team_workflow_admin import run
from conftest import PASSWORD, PASSWORD_HASH
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

TABLES = ("task_workflows", "task_members", "task_event_heads", "task_events")
HUMAN_SCOPES = (
    "task:members:write",
    "task:archive",
    "card:assign",
    "card:comment",
    "task:review-policy",
    "card:cosign",
)


async def new_task(api, headers):
    response = await api.post("/tasks", headers=headers, json={"name": "Synthetic workflow task"})
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


async def person(api, admin_engine, org, role="bidder"):
    uid = uuid4()
    email = f"{uid}@example.test"
    with Session(admin_engine) as session, session.begin():
        session.execute(text("SELECT set_config('app.current_org',:org,true)"), {"org": str(org)})
        session.add(User(id=uid, email=email, password_hash=PASSWORD_HASH))
        session.flush()
        session.add(Membership(org_id=org, user_id=uid, role=role))
    response = await api.post(
        "/auth/login", json={"email": email, "password": PASSWORD, "org_id": str(org)}
    )
    assert response.status_code == 200
    return uid, {
        "Authorization": "Bearer " + response.json()["data"]["session"],
        "X-Org-Id": str(org),
    }


async def workflow(api, headers, task):
    response = await api.get(f"/tasks/{task}/workflow", headers=headers)
    assert response.status_code == 200, response.text
    assert set(response.json()["data"]) == {"workflow"}
    return response.json()["data"]["workflow"]


async def add_member(api, headers, task, user, revision, role="contributor", domains=None):
    return await api.put(
        f"/tasks/{task}/members/{user}",
        headers=headers,
        json={
            "expected_revision": revision,
            "reason": "Synthetic membership acceptance",
            "role": role,
            "review_domains": domains or [],
        },
    )


async def test_creation_membership_masking_archive(api, headers, tenants, admin_engine):
    task = await new_task(api, headers[0])
    own = await workflow(api, headers[0], task)
    assert own["owner_user_id"] == str(tenants["users"][0])
    assert own["revision"] == 1 and own["state"] == "active"
    stranger, stranger_headers = await person(api, admin_engine, tenants["orgs"][0])
    for target, auth in ((task, headers[1]), (task, stranger_headers), (str(uuid4()), headers[0])):
        response = await api.get(f"/tasks/{target}/workflow", headers=auth)
        assert response.status_code == 404
        assert response.json()["data"]["error"]["code"] == "not_found"
    added = await add_member(api, headers[0], task, stranger, 1)
    assert added.status_code == 200, added.text
    assert (await workflow(api, stranger_headers, task))["revision"] == 2
    stale = await add_member(api, headers[0], task, stranger, 1)
    assert stale.status_code == 409
    archived = await api.post(
        f"/tasks/{task}/archive",
        headers=headers[0],
        json={"expected_revision": 2, "reason": "Synthetic idle archive"},
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["data"]["state"] == "archived"
    assert (await workflow(api, stranger_headers, task))["state"] == "archived"
    denied = await add_member(api, headers[0], task, stranger, 3)
    assert denied.status_code == 409 and denied.json()["data"]["error"]["code"] == "task_archived"
    restored = await api.post(
        f"/tasks/{task}/unarchive",
        headers=headers[0],
        json={"expected_revision": 3, "reason": "Synthetic restore"},
    )
    assert restored.status_code == 200
    assert restored.json()["data"]["revision"] == 4


async def test_owner_handover_and_disabled_recovery(api, headers, tenants, admin_engine):
    task = await new_task(api, headers[0])
    target, target_headers = await person(api, admin_engine, tenants["orgs"][0])
    removed = await api.post(
        f"/tasks/{task}/members/{tenants['users'][0]}/remove",
        headers=headers[0],
        json={"expected_revision": 1, "reason": "Cannot remove owner"},
    )
    assert removed.status_code == 409
    moved = await api.post(
        f"/tasks/{task}/handover",
        headers=headers[0],
        json={
            "expected_revision": 1,
            "reason": "Synthetic handover",
            "target_user_id": str(target),
            "previous_owner_role": "observer",
            "previous_owner_review_domains": [],
        },
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["data"]["owner_user_id"] == str(target)
    members = await api.get(f"/tasks/{task}/members", headers=target_headers)
    assert [
        m["user_id"] for m in members.json()["items"] if m["role"] == "owner" and m["active"]
    ] == [str(target)]
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE memberships SET active=false WHERE org_id=:org AND user_id=:user"),
            {"org": tenants["orgs"][0], "user": target},
        )
    denied = await api.get(f"/tasks/{task}/workflow", headers=target_headers)
    assert denied.status_code == 404
    recovered = await api.post(
        f"/tasks/{task}/handover",
        headers=headers[0],
        json={
            "expected_revision": 2,
            "reason": "Recover disabled owner",
            "target_user_id": str(tenants["users"][0]),
            "previous_owner_role": "observer",
            "previous_owner_review_domains": [],
        },
    )
    assert recovered.status_code == 200, recovered.text


@pytest.mark.parametrize(
    "role,domains", [("reviewer", ["technical"]), ("contributor", ["technical"])]
)
async def test_domain_ceiling(api, headers, tenants, admin_engine, role, domains):
    task = await new_task(api, headers[0])
    user, _ = await person(api, admin_engine, tenants["orgs"][0], "bidder")
    response = await add_member(api, headers[0], task, user, 1, role, domains)
    assert response.status_code == 400
    assert (await workflow(api, headers[0], task))["revision"] == 1


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


def test_reviewed_import_and_cutover(tenants, admin_engine, tmp_path):
    org, user = tenants["orgs"][0], tenants["users"][0]
    with Session(admin_engine) as session, session.begin():
        session.execute(text("SELECT set_config('app.current_org',:org,true)"), {"org": str(org)})
        task = Task(id=uuid4(), org_id=org, name="Historical unmapped task", created_by=user)
        session.add(task)
        session.flush()
        task_id = task.id
    assert not run(admin_engine, org)["ready"]
    with pytest.raises(ValueError, match="cutover blocked"):
        run(admin_engine, org, cutover=True, workers_drained=True)
    mapping = tmp_path / "reviewed-task-map.json"
    mapping.write_text(
        json.dumps(
            {
                "reviewed": True,
                "reviewed_by_user_id": str(user),
                "org_id": str(org),
                "tasks": [{"task_id": str(task_id), "owner_user_id": str(user), "members": []}],
            }
        )
    )
    assert run(admin_engine, org, mapping_path=str(mapping))["applied"] is False
    result = run(admin_engine, org, mapping_path=str(mapping), apply=True)
    assert result["ready"] and result["mapped"] == 1
    assert run(admin_engine, org, cutover=True, workers_drained=True)["cutover_verified"]
    with pytest.raises(ValueError, match="cannot replace"):
        run(admin_engine, org, mapping_path=str(mapping), apply=True)


@pytest.mark.parametrize(
    "path,method",
    [
        ("workflow", "GET"),
        ("members", "GET"),
        ("member-candidates", "GET"),
        ("members/{user}", "PUT"),
        ("members/{user}/remove", "POST"),
        ("handover", "POST"),
        ("archive", "POST"),
        ("unarchive", "POST"),
    ],
)
async def test_every_member_route_masks_foreign_task(api, headers, tenants, path, method):
    task = await new_task(api, headers[1])
    path = path.format(user=tenants["users"][1])
    body = {"expected_revision": 1, "reason": "Synthetic foreign task request"}
    if method == "PUT":
        body.update(role="observer", review_domains=[])
    if path == "handover":
        body.update(
            target_user_id=str(tenants["users"][0]),
            previous_owner_role="observer",
            previous_owner_review_domains=[],
        )
    for target in (task, str(uuid4())):
        response = await api.request(
            method,
            f"/tasks/{target}/{path}",
            headers=headers[0],
            **({"json": body} if method != "GET" else {}),
        )
        assert response.status_code == 404, response.text
        assert response.json()["data"]["error"]["code"] == "not_found"


@pytest.mark.parametrize("pending_state", ["pending", "unknown"])
async def test_archive_rejects_unsettled_call_of_failed_job(
    api, headers, tenants, admin_engine, pdf_bytes, pending_state
):
    from app.models.entities import Job, VendorCall
    from app.schemas.budget_contracts import BudgetCallQuote

    task = await new_task(api, headers[0])
    uploaded = await api.post(
        f"/tasks/{task}/documents",
        headers=headers[0],
        files={"file": ("synthetic.pdf", pdf_bytes, "application/pdf")},
    )
    assert uploaded.status_code == 200, uploaded.text
    document = UUID(uploaded.json()["data"]["id"])
    org, user = tenants["orgs"][0], tenants["users"][0]
    with Session(admin_engine) as session, session.begin():
        session.execute(
            text(
                "SELECT set_config('app.current_org',:org,true),set_config('app.actor_kind','session',true),set_config('app.actor_user_id',:user,true),set_config('app.actor_scopes',:scopes,true)"
            ),
            {
                "org": str(org),
                "user": str(user),
                "scopes": json.dumps(sorted(ROLE_SCOPES["admin"])),
            },
        )
        job = Job(
            org_id=org,
            task_id=UUID(task),
            document_id=document,
            kind="extract",
            cache_key=uuid4().hex * 2,
            status="failed",
            run_id=uuid4(),
            actor_user_id=user,
            actor_kind="session",
            actor_scopes=sorted(ROLE_SCOPES["admin"]),
        )
        session.add(job)
        session.flush()
        quote = BudgetCallQuote(
            capability="llm",
            payer="local_free",
            provider="test",
            model="test",
            version="test",
            price_revision="test",
            request_sha256="a" * 64,
            currency="USD",
            reserved_charge="0",
            reserved_task_amount="0",
            vendor_usd_upper_bound="0",
        )
        call = VendorCall(
            org_id=org,
            task_id=UUID(task),
            job_id=job.id,
            run_id=job.run_id,
            budget_revision=1,
            capability=quote.capability,
            payer=quote.payer,
            currency=quote.currency,
            price_revision=quote.price_revision,
            request_sha256=quote.request_sha256,
            reserved_charge=quote.reserved_charge,
            reserved_task_amount=quote.reserved_task_amount,
            quote=quote.model_dump(mode="json"),
        )
        session.add(call)
        session.flush()
        if pending_state == "unknown":
            call.state = "unknown"
    response = await api.post(
        f"/tasks/{task}/archive",
        headers=headers[0],
        json={"expected_revision": 1, "reason": "Must retain unresolved liability"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["data"]["error"]["code"] == "task_busy"
    assert (await workflow(api, headers[0], task))["state"] == "active"


@pytest.mark.parametrize("status", ["queued", "running"])
async def test_archive_rejects_active_jobs(api, headers, tenants, admin_engine, pdf_bytes, status):
    from app.models.entities import Job

    task = await new_task(api, headers[0])
    uploaded = await api.post(
        f"/tasks/{task}/documents",
        headers=headers[0],
        files={"file": ("synthetic.pdf", pdf_bytes, "application/pdf")},
    )
    assert uploaded.status_code == 200
    org, user = tenants["orgs"][0], tenants["users"][0]
    with Session(admin_engine) as session, session.begin():
        session.execute(
            text(
                "SELECT set_config('app.current_org',:org,true),set_config('app.actor_kind','session',true),set_config('app.actor_user_id',:user,true),set_config('app.actor_scopes',:scopes,true)"
            ),
            {
                "org": str(org),
                "user": str(user),
                "scopes": json.dumps(sorted(ROLE_SCOPES["admin"])),
            },
        )
        session.add(
            Job(
                org_id=org,
                task_id=UUID(task),
                document_id=UUID(uploaded.json()["data"]["id"]),
                kind="extract",
                cache_key=uuid4().hex * 2,
                status=status,
                actor_user_id=user,
                actor_kind="session",
                actor_scopes=sorted(ROLE_SCOPES["admin"]),
            )
        )
    response = await api.post(
        f"/tasks/{task}/archive",
        headers=headers[0],
        json={"expected_revision": 1, "reason": "Cannot archive active job"},
    )
    assert response.status_code == 409
    assert response.json()["data"]["error"]["code"] == "task_busy"


@pytest.mark.parametrize(
    "role,org_role,domains", [("observer", "bidder", []), ("reviewer", "technical", ["technical"])]
)
async def test_read_only_task_roles_cannot_upload(
    api, headers, tenants, admin_engine, pdf_bytes, role, org_role, domains
):
    task = await new_task(api, headers[0])
    user, member_headers = await person(api, admin_engine, tenants["orgs"][0], org_role)
    assert (await add_member(api, headers[0], task, user, 1, role, domains)).status_code == 200
    assert (await workflow(api, member_headers, task))["state"] == "active"
    response = await api.post(
        f"/tasks/{task}/documents",
        headers=member_headers,
        files={"file": ("synthetic.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 403


async def test_concurrent_workflow_writers_commit_once(api, headers, tenants, admin_engine):
    import asyncio

    task = await new_task(api, headers[0])
    a, _ = await person(api, admin_engine, tenants["orgs"][0])
    b, _ = await person(api, admin_engine, tenants["orgs"][0])
    results = await asyncio.gather(
        add_member(api, headers[0], task, a, 1), add_member(api, headers[0], task, b, 1)
    )
    assert sorted(response.status_code for response in results) == [200, 409]
    assert (await workflow(api, headers[0], task))["revision"] == 2


async def test_token_creation_seeds_human_owner_but_cannot_archive(api, headers, tenants):
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
    response = await api.post(
        f"/tasks/{task}/archive",
        headers=token_headers,
        json={"expected_revision": 1, "reason": "Token must not manage"},
    )
    assert response.status_code == 403


async def test_member_reason_is_encrypted_and_audit_has_enum_changes(
    api, application, headers, tenants, admin_engine
):
    from app.core.security import Secrets

    task = await new_task(api, headers[0])
    uid, _ = await person(api, admin_engine, tenants["orgs"][0])
    response = await add_member(api, headers[0], task, uid, 1, role="observer")
    assert response.status_code == 200, response.text
    with admin_engine.connect() as connection:
        cipher = connection.scalar(
            text(
                "SELECT last_reason_ciphertext FROM task_members WHERE org_id=:org AND task_id=:task AND user_id=:user"
            ),
            {"org": tenants["orgs"][0], "task": UUID(task), "user": uid},
        )
        details = connection.scalar(
            text(
                "SELECT details FROM audit_logs WHERE org_id=:org AND object_id=:task AND action='task.member_set' ORDER BY created_at DESC LIMIT 1"
            ),
            {"org": tenants["orgs"][0], "task": UUID(task)},
        )
    assert cipher and "Synthetic" not in cipher
    assert Secrets.for_data(application.state.processor.settings).decrypt(cipher)
    assert details["before_role"] is None and details["after_role"] == "observer"
    assert details["before_active"] is False and details["after_active"] is True
    assert "reason_sha256" in details and "reason" not in details
