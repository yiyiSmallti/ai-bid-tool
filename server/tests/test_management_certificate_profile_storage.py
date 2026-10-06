"""Failure-first database acceptance for the certificate/profile lifecycle arms.

RLS and composite foreign keys must reject forged org/root/revision/actor before
business guards; zero/multiple arms, missing audit and raw root transitions fail.
Inactive roots preserve exact pins and immutable file/evidence archives but reject
new/replacement pins. Tokens cannot acquire either new human-only lifecycle scope.
"""

import asyncio
from uuid import UUID, uuid4

import pytest
from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_certificates import certificate, task
from test_profiles import profile

KINDS = [
    (
        "certificates",
        "certificate",
        "certificates",
        "certificate_revisions",
        "task_certificates",
        certificate,
    ),
    ("profiles", "profile", "org_profiles", "org_profile_revisions", "task_org_profiles", profile),
]


async def transition(api, auth, kind, root, state="inactive", sequence=0, revision=1):
    response = await api.post(
        f"/v4/management/resources/{kind}/{root}/lifecycle",
        headers=auth,
        json={
            "expected_revision": revision,
            "expected_lifecycle_revision": sequence,
            "state": state,
            "reason_code": "restored" if state == "active" else "obsolete",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


@pytest.mark.parametrize("kind,arm,table,revisions,pins,create", KINDS)
@pytest.mark.parametrize(
    "change,sqlstate",
    [
        ("foreign_org", "42501"),
        ("no_context", "42501"),
        ("foreign_root", "23503"),
        ("foreign_actor", "23503"),
        ("wrong_revision", "23503"),
        ("no_root", "23514"),
        ("both_roots", "23514"),
        ("no_actor", "42501"),
    ],
)
async def test_event_rejection_order(
    kind, arm, table, revisions, pins, create, change, sqlstate, api, headers, tenants, application
):
    own, foreign = await create(api, headers[0]), await create(api, headers[1])
    params = {
        "id": uuid4(),
        "org": tenants["orgs"][0],
        "certificate": None,
        "profile": None,
        "actor": tenants["users"][0],
        "version": 1,
    }
    params[arm] = UUID(own[f"{arm}_id"])
    if change == "foreign_org":
        params["org"] = tenants["orgs"][1]
    elif change == "foreign_root":
        params[arm] = UUID(foreign[f"{arm}_id"])
    elif change == "foreign_actor":
        params["actor"] = tenants["users"][1]
    elif change == "wrong_revision":
        params["version"] = 77
    elif change == "no_root":
        params[arm] = None
    elif change == "both_roots":
        params["certificate"] = params["profile"] = uuid4()
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(
            None if change == "no_context" else tenants["orgs"][0]
        ) as session:
            await session.execute(
                text(
                    "INSERT INTO resource_lifecycle_events(id,org_id,certificate_id,profile_id,revision,"
                    "resource_revision,before_state,after_state,reason_code,actor_user_id)"
                    " VALUES(:id,:org,:certificate,:profile,1,:version,'active','inactive','obsolete',:actor)"
                ),
                params,
            )
    assert failure.value.orig.sqlstate == sqlstate


@pytest.mark.parametrize("kind,arm,table,revisions,pins,create", KINDS)
async def test_arm_rls_immutability_atomicity_and_guards(
    kind, arm, table, revisions, pins, create, api, headers, tenants, application
):
    row = await create(api, headers[0])
    root, org = UUID(row[f"{arm}_id"]), tenants["orgs"][0]
    result = await transition(api, headers[0], kind, root)
    assert result["existing_selections"] == "preserved"
    for hidden in (None, tenants["orgs"][1]):
        async with application.state.db.transaction(hidden) as session:
            for name in (table, revisions, "resource_lifecycle_events"):
                assert await session.scalar(text(f"SELECT count(*) FROM {name}")) == 0
    actor = Identity(tenants["users"][0], org, set(ROLE_SCOPES["admin"]), "admin")
    params = {"org": org, "root": root, "actor": actor.user_id}
    for statement in (
        "UPDATE resource_lifecycle_events SET reason_code='other'",
        "DELETE FROM resource_lifecycle_events",
        f"UPDATE {table} SET lifecycle_state='active',lifecycle_revision=2 WHERE id=:root",
        f"INSERT INTO resource_lifecycle_events(org_id,{arm}_id,revision,resource_revision,"
        "before_state,after_state,reason_code,actor_user_id)"
        " VALUES(:org,:root,2,1,'inactive','active','restored',:actor)",
    ):
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction(org) as session:
                await set_actor_context(session, actor)
                await session.execute(text(statement), params)
    async with application.state.db.transaction(org) as session:
        assert (
            await session.scalar(
                text(f"SELECT lifecycle_revision FROM {table} WHERE id=:root"), params
            )
            == 1
        )
        for name in (table, revisions, pins, "resource_lifecycle_events"):
            assert await session.scalar(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class WHERE oid=CAST(:name AS regclass)"
                ),
                {"name": name},
            )
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(org) as session:
            await session.execute(
                text(
                    f"INSERT INTO resource_lifecycle_events(org_id,{arm}_id,revision,resource_revision,"
                    "before_state,after_state,reason_code,actor_user_id)"
                    " VALUES(:org,:root,1,1,'active','inactive','obsolete',:actor)"
                ),
                params,
            )
    assert failure.value.orig.sqlstate == "23505"


@pytest.mark.parametrize("kind,arm,table,revisions,pins,create", KINDS)
@pytest.mark.parametrize(
    "change,sqlstate",
    [
        ("foreign_revision", "23503"),
        ("foreign_task", "23503"),
        ("foreign_org", "42501"),
        ("no_actor", "42501"),
    ],
)
async def test_pin_fk_before_authority(
    kind, arm, table, revisions, pins, create, change, sqlstate, api, headers, tenants, application
):
    own, foreign = await create(api, headers[0]), await create(api, headers[1])
    own_task, foreign_task = await task(api, headers[0]), await task(api, headers[1])
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(tenants["orgs"][0]) as session:
            await session.execute(
                text(
                    f"INSERT INTO {pins}(id,org_id,task_id,{arm}_id,{arm}_revision_id,lot,active)"
                    " VALUES(:id,:org,:task,:root,:revision,'raw',true)"
                ),
                {
                    "id": uuid4(),
                    "org": tenants["orgs"][1 if change == "foreign_org" else 0],
                    "task": UUID(foreign_task if change == "foreign_task" else own_task),
                    "root": UUID(own[f"{arm}_id"]),
                    "revision": UUID(foreign["id"] if change == "foreign_revision" else own["id"]),
                },
            )
    assert failure.value.orig.sqlstate == sqlstate


@pytest.mark.parametrize("kind,arm,table,revisions,pins,create", KINDS)
async def test_inactive_pins_and_raw_sql(
    kind, arm, table, revisions, pins, create, api, headers, tenants, application
):
    row = await create(api, headers[0])
    root, org = UUID(row[f"{arm}_id"]), tenants["orgs"][0]
    task_id = await task(api, headers[0])
    path, body = f"/tasks/{task_id}/{kind}", {f"{arm}_id": str(root), "revision": 1}
    first = await api.post(path, headers=headers[0], json=body)
    assert first.status_code == 200, first.text
    await transition(api, headers[0], kind, root)
    duplicate = await api.post(path, headers=headers[0], json=body)
    assert duplicate.status_code == 200 and duplicate.json()["data"]["duplicate"]
    denied = await api.post(path, headers=headers[0], json={**body, "lot": "new"})
    assert (
        denied.status_code == 409 and denied.json()["data"]["error"]["code"] == "resource_inactive"
    )
    actor = Identity(tenants["users"][0], org, set(ROLE_SCOPES["admin"]), "admin")
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(org) as session:
            await set_actor_context(session, actor)
            await session.execute(
                text(
                    f"INSERT INTO {pins}(id,org_id,task_id,{arm}_id,{arm}_revision_id,lot,active)"
                    " VALUES(:id,:org,:task,:root,:revision,'raw',true)"
                ),
                {
                    "id": uuid4(),
                    "org": org,
                    "task": UUID(task_id),
                    "root": root,
                    "revision": UUID(row["id"]),
                },
            )
    assert failure.value.orig.sqlstate == "23514"
    # Full declarations can still be revised while inactive; no implicit restore.
    updated = await api.post(
        f"/resources/{kind}/{root}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "data": row["data"]},
    )
    assert updated.status_code == 200, updated.text
    replacement = await api.post(path, headers=headers[0], json={**body, "revision": 2})
    assert replacement.status_code == 409
    await transition(api, headers[0], kind, root, "active", 1, 2)
    replacement = await api.post(path, headers=headers[0], json={**body, "revision": 2})
    assert (
        replacement.status_code == 200
        and replacement.json()["data"]["replaced_snapshot_id"] == first.json()["data"]["id"]
    )


@pytest.mark.parametrize("kind,arm,table,revisions,pins,create", KINDS)
async def test_deactivate_new_pin_race(kind, arm, table, revisions, pins, create, api, headers):
    row = await create(api, headers[0])
    task_id = await task(api, headers[0])
    pin, _ = await asyncio.gather(
        api.post(
            f"/tasks/{task_id}/{kind}",
            headers=headers[0],
            json={f"{arm}_id": row[f"{arm}_id"], "revision": 1},
        ),
        transition(api, headers[0], kind, row[f"{arm}_id"]),
    )
    assert pin.status_code in {200, 409}, pin.text
    retained = await api.get(f"/tasks/{task_id}/{kind}", headers=headers[0])
    assert len(retained.json()["items"]) == (1 if pin.status_code == 200 else 0)


@pytest.mark.parametrize("scope", ["certificate:lifecycle", "profile:lifecycle"])
async def test_token_scope_database_guard(scope, tenants, application):
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(tenants["orgs"][0]) as session:
            await session.execute(
                text(
                    "INSERT INTO api_tokens(id,org_id,user_id,digest,name,scopes,expires_at,revoked)"
                    " VALUES(:id,:org,:user,:digest,'forged',jsonb_build_array(CAST(:scope AS text)),now()+interval '1 hour',false)"
                ),
                {
                    "id": uuid4(),
                    "org": tenants["orgs"][0],
                    "user": tenants["users"][0],
                    "digest": uuid4().hex,
                    "scope": scope,
                },
            )
    assert failure.value.orig.sqlstate == "23514"


@pytest.mark.parametrize("kind,arm,table,revisions,pins,create", KINDS)
@pytest.mark.parametrize("restored", [False, True])
async def test_retained_inactive_pin_cannot_be_reactivated(
    kind, arm, table, revisions, pins, create, restored, api, headers, tenants, application
):
    row = await create(api, headers[0])
    root = row[f"{arm}_id"]
    task_id = await task(api, headers[0])
    first = await api.post(
        f"/tasks/{task_id}/{kind}", headers=headers[0], json={f"{arm}_id": root, "revision": 1}
    )
    assert first.status_code == 200
    updated = await api.post(
        f"/resources/{kind}/{root}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "data": row["data"]},
    )
    assert updated.status_code == 200
    second = await api.post(
        f"/tasks/{task_id}/{kind}", headers=headers[0], json={f"{arm}_id": root, "revision": 2}
    )
    assert second.status_code == 200
    await transition(api, headers[0], kind, root, revision=2)
    if restored:
        await transition(api, headers[0], kind, root, "active", sequence=1, revision=2)
    org = tenants["orgs"][0]
    actor = Identity(tenants["users"][0], org, set(ROLE_SCOPES["admin"]), "admin")
    if kind == "profiles":
        # The replacement still owns the active slot: its UNIQUE rejection must
        # precede the AFTER history guard, independently of library lifecycle.
        with pytest.raises(DBAPIError) as duplicate:
            async with application.state.db.transaction(org) as session:
                await set_actor_context(session, actor)
                await session.execute(
                    text(f"UPDATE {pins} SET active=true WHERE id=:id"),
                    {"id": UUID(first.json()["data"]["id"])},
                )
        assert duplicate.value.orig.sqlstate == "23505"
        assert duplicate.value.orig.diag.constraint_name == "task_profile_active_slot"
    # Remove the separate slot conflict so only historical reactivation is under
    # test. A library restore must never make a retired snapshot writable again.
    async with application.state.db.transaction(org) as session:
        await set_actor_context(session, actor)
        await session.execute(
            text(f"UPDATE {pins} SET active=false WHERE id=:id"),
            {"id": UUID(second.json()["data"]["id"])},
        )
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(org) as session:
            await set_actor_context(session, actor)
            await session.execute(
                text(f"UPDATE {pins} SET active=true WHERE id=:id"),
                {"id": UUID(first.json()["data"]["id"])},
            )
    assert failure.value.orig.sqlstate == "42501"
    assert "Historical selections cannot be reactivated" in failure.value.orig.diag.message_primary


@pytest.mark.parametrize("kind,arm,table,revisions,pins,create", KINDS)
@pytest.mark.parametrize(
    "state,selected_revision", [("inactive", 1), ("inactive", 2), ("active", 1), ("active", 2)]
)
async def test_lifecycle_holding_root_serializes_old_and_current_pins(
    kind,
    arm,
    table,
    revisions,
    pins,
    create,
    state,
    selected_revision,
    api,
    headers,
    tenants,
    application,
):
    """Hold the lifecycle transaction open until the HTTP pin reaches its root lock."""
    from app.schemas.management_pages import ResourceLifecycleSet
    from app.services import management_certificates, management_profiles
    from sqlalchemy import event

    row = await create(api, headers[0])
    root = UUID(row[f"{arm}_id"])
    task_id = await task(api, headers[0])
    updated = await api.post(
        f"/resources/{kind}/{root}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "data": row["data"]},
    )
    assert updated.status_code == 200
    sequence = 0
    if state == "active":
        await transition(api, headers[0], kind, root, revision=2)
        sequence = 1
    org = tenants["orgs"][0]
    actor = Identity(tenants["users"][0], org, set(ROLE_SCOPES["admin"]), "admin")
    service = management_certificates if kind == "certificates" else management_profiles
    reached_root = asyncio.Event()

    def observe(connection, cursor, statement, parameters, context, executemany):
        if f"FROM {table}" in statement and "FOR SHARE" in statement:
            reached_root.set()

    engine = application.state.db.engine.sync_engine
    request = None
    event.listen(engine, "before_cursor_execute", observe)
    try:
        async with application.state.db.transaction(org) as session:
            await service.set_state(
                session,
                actor,
                root,
                ResourceLifecycleSet(
                    expected_revision=2,
                    expected_lifecycle_revision=sequence,
                    state=state,
                    reason_code="restored" if state == "active" else "obsolete",
                ),
            )
            request = asyncio.create_task(
                api.post(
                    f"/tasks/{task_id}/{kind}",
                    headers=headers[0],
                    json={f"{arm}_id": str(root), "revision": selected_revision},
                )
            )
            await asyncio.wait_for(reached_root.wait(), 5)
            assert not request.done()
        response = await asyncio.wait_for(request, 5)
        assert response.status_code == (200 if state == "active" else 409), response.text
        if state == "active":
            assert response.json()["data"]["revision"] == selected_revision
        else:
            assert response.json()["data"]["error"]["code"] == "resource_inactive"
    finally:
        event.remove(engine, "before_cursor_execute", observe)
        if request is not None and not request.done():
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)


@pytest.mark.parametrize("kind,arm,table,revisions,pins,create", KINDS)
async def test_pin_holding_root_survives_concurrent_deactivation(
    kind, arm, table, revisions, pins, create, api, headers, tenants, application
):
    """The opposite lock order retains a successful pin after withdrawal commits."""
    from app.services import certificates, profiles, versioned
    from sqlalchemy import event

    row = await create(api, headers[0])
    root = UUID(row[f"{arm}_id"])
    task_id = await task(api, headers[0])
    org = tenants["orgs"][0]
    actor = Identity(tenants["users"][0], org, set(ROLE_SCOPES["admin"]), "admin")
    resource = certificates.CERTIFICATES if kind == "certificates" else profiles.PROFILES
    reached_root = asyncio.Event()

    def observe(connection, cursor, statement, parameters, context, executemany):
        if f"FROM {table}" in statement and "FOR UPDATE" in statement:
            reached_root.set()

    engine = application.state.db.engine.sync_engine
    request = None
    event.listen(engine, "before_cursor_execute", observe)
    try:
        async with application.state.db.transaction(org) as session:
            await set_actor_context(session, actor)
            # Public selection dispatch establishes workflow/task authorization
            # before the shared helper takes its root lock.
            selected = await versioned.select_revision(
                session, actor, resource, UUID(task_id), root, 1, None
            )
            request = asyncio.create_task(transition(api, headers[0], kind, root))
            await asyncio.wait_for(reached_root.wait(), 5)
            assert not request.done()
        await asyncio.wait_for(request, 5)
        retained = await api.get(f"/tasks/{task_id}/{kind}", headers=headers[0])
        assert retained.status_code == 200
        assert retained.json()["items"][0]["id"] == selected["id"]
        assert retained.json()["items"][0]["revision"] == 1
    finally:
        event.remove(engine, "before_cursor_execute", observe)
        if request is not None and not request.done():
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)


@pytest.mark.parametrize("kind,arm,table,revisions,pins,create", KINDS)
async def test_two_tasks_keep_exact_pins_until_one_explicit_replacement(
    kind, arm, table, revisions, pins, create, api, headers
):
    row = await create(api, headers[0])
    root = row[f"{arm}_id"]
    tasks = [await task(api, headers[0]) for _ in range(2)]
    original = []
    for task_id in tasks:
        result = await api.post(
            f"/tasks/{task_id}/{kind}",
            headers=headers[0],
            json={f"{arm}_id": root, "revision": 1},
        )
        assert result.status_code == 200, result.text
        original.append(result.json()["data"])
    revised = await api.post(
        f"/resources/{kind}/{root}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "data": {**row["data"], "name": "Revised declaration"}},
    )
    assert revised.status_code == 200, revised.text
    for task_id in tasks:
        fixed = await api.get(f"/tasks/{task_id}/{kind}", headers=headers[0])
        assert fixed.json()["items"][0][f"{arm}_revision_id"] == row["id"]
    replacement = await api.post(
        f"/tasks/{tasks[0]}/{kind}",
        headers=headers[0],
        json={f"{arm}_id": root, "revision": 2},
    )
    assert replacement.status_code == 200
    assert replacement.json()["data"]["replaced_snapshot_id"] == original[0]["id"]
    assert replacement.json()["data"][f"{arm}_revision_id"] == revised.json()["data"]["id"]
    kept = await api.get(f"/tasks/{tasks[1]}/{kind}", headers=headers[0])
    assert kept.json()["items"][0]["id"] == original[1]["id"]
    assert kept.json()["items"][0]["revision"] == 1
