"""Failure inventory established before implementation.

Foreign org/root/revision/actor must fail through RLS/composite FKs before
business authority. Exactly one event root, immutable history and atomic audit
are mandatory. Inactive parents block association and pins, preserving exact
active duplicates. Raw SQL and concurrent parent changes cannot bypass guards.
"""

from uuid import UUID, uuid4

import pytest
from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_features import feature, task


async def stop(api, auth, kind, root):
    response = await api.post(
        f"/v4/management/resources/{kind}/{root}/lifecycle",
        headers=auth,
        json={
            "expected_revision": 1,
            "expected_lifecycle_revision": 0,
            "state": "inactive",
            "reason_code": "obsolete",
        },
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("kind", ["products", "features"])
@pytest.mark.parametrize(
    "change,state",
    [
        ("foreign_org", "42501"),
        ("no_context", "42501"),
        ("foreign_root", "23503"),
        ("foreign_actor", "23503"),
        ("wrong_revision", "23503"),
        ("neither_root", "23514"),
        ("both_roots", "23514"),
        ("no_actor", "42501"),
    ],
)
async def test_lifecycle_relations_reject_before_business_guards(
    kind, change, state, api, headers, tenants, application
):
    own, foreign = await feature(api, headers[0]), await feature(api, headers[1])
    org = tenants["orgs"][0]
    arm = "product" if kind == "products" else "feature"
    params = {
        "id": uuid4(),
        "org": org,
        "product": None,
        "feature": None,
        "actor": tenants["users"][0],
        "version": 1,
    }
    params[arm] = UUID(own["data"]["product_id"] if arm == "product" else own["feature_id"])
    if change == "foreign_org":
        params["org"] = tenants["orgs"][1]
    elif change == "foreign_root":
        params[arm] = UUID(
            foreign["data"]["product_id"] if arm == "product" else foreign["feature_id"]
        )
    elif change == "foreign_actor":
        params["actor"] = tenants["users"][1]
    elif change == "wrong_revision":
        params["version"] = 77
    elif change == "neither_root":
        params["product"] = params["feature"] = None
    elif change == "both_roots":
        params["product"] = UUID(own["data"]["product_id"])
        params["feature"] = UUID(own["feature_id"])
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(
            None if change == "no_context" else org
        ) as session:
            await session.execute(
                text(
                    "INSERT INTO resource_lifecycle_events"
                    "(id,org_id,product_id,feature_id,revision,resource_revision,"
                    "before_state,after_state,reason_code,actor_user_id)"
                    " VALUES(:id,:org,:product,:feature,1,:version,'active','inactive','obsolete',:actor)"
                ),
                params,
            )
    assert failure.value.orig.sqlstate == state


async def test_feature_lifecycle_rls_immutability_and_atomic_audit(
    api, headers, tenants, application
):
    row = await feature(api, headers[0])
    await stop(api, headers[0], "features", row["feature_id"])
    for org in (None, tenants["orgs"][1]):
        async with application.state.db.transaction(org) as session:
            for table in ("resource_lifecycle_events", "features", "feature_revisions"):
                assert await session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    org = tenants["orgs"][0]
    actor = Identity(tenants["users"][0], org, set(ROLE_SCOPES["admin"]), "admin")
    for sql in (
        "UPDATE resource_lifecycle_events SET reason_code='other'",
        "DELETE FROM resource_lifecycle_events",
        "UPDATE features SET lifecycle_state='active',lifecycle_revision=2 WHERE id=:root",
        "INSERT INTO resource_lifecycle_events(org_id,feature_id,revision,resource_revision,"
        "before_state,after_state,reason_code,actor_user_id)"
        " VALUES(:org,:root,2,1,'inactive','active','restored',:actor)",
    ):
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction(org) as session:
                await set_actor_context(session, actor)
                await session.execute(
                    text(sql), {"org": org, "root": UUID(row["feature_id"]), "actor": actor.user_id}
                )
    async with application.state.db.transaction(org) as session:
        assert (
            await session.scalar(
                text("SELECT lifecycle_revision FROM features WHERE id=:root"),
                {"root": UUID(row["feature_id"])},
            )
            == 1
        )
        assert await session.scalar(
            text(
                "SELECT relforcerowsecurity FROM pg_class WHERE oid='resource_lifecycle_events'::regclass"
            )
        )


async def test_inactive_parent_preserves_exact_pin_and_blocks_writes(api, headers):
    row = await feature(api, headers[0])
    task_id = await task(api, headers[0])
    path = f"/tasks/{task_id}/features"
    selected = await api.post(path, headers=headers[0], json={"feature_id": row["feature_id"]})
    assert selected.status_code == 200
    await stop(api, headers[0], "products", row["data"]["product_id"])
    replay = await api.post(path, headers=headers[0], json={"feature_id": row["feature_id"]})
    assert replay.status_code == 200 and replay.json()["data"]["duplicate"]
    for target, body in (
        (path, {"feature_id": row["feature_id"], "lot": "new"}),
        ("/resources/features", {"data": row["data"]}),
        (
            f"/resources/features/{row['feature_id']}/revisions",
            {"expected_revision": 1, "data": row["data"]},
        ),
    ):
        denied = await api.post(target, headers=headers[0], json=body)
        assert denied.status_code == 409, denied.text
        assert denied.json()["data"]["error"]["code"] == "resource_inactive"


@pytest.mark.parametrize("change,state", [("foreign_revision", "23503"), ("no_actor", "42501")])
async def test_feature_pin_fk_before_authority(change, state, api, headers, tenants, application):
    row = await feature(api, headers[0])
    foreign = await feature(api, headers[1])
    task_id = await task(api, headers[0])
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(tenants["orgs"][0]) as session:
            await session.execute(
                text(
                    "INSERT INTO task_features(id,org_id,task_id,feature_id,feature_revision_id,lot,active)"
                    " VALUES(:id,:org,:task,:root,:revision,'raw',true)"
                ),
                {
                    "id": uuid4(),
                    "org": tenants["orgs"][0],
                    "task": UUID(task_id),
                    "root": UUID(row["feature_id"]),
                    "revision": UUID(foreign["id"] if change == "foreign_revision" else row["id"]),
                },
            )
    assert failure.value.orig.sqlstate == state


async def test_feature_search_does_not_index_description(api, headers):
    row = await feature(api, headers[0])
    response = await api.post(
        "/v4/management/resources/features/query", headers=headers[0], json={"q": "declaration"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    matched = await api.post(
        "/v4/management/resources/features/query", headers=headers[0], json={"q": "synthetic"}
    )
    assert matched.status_code == 200, matched.text
    assert matched.json()["items"][0]["ref"]["resource_id"] == row["feature_id"]


async def test_lifecycle_arms_have_independent_sequences(api, headers, tenants, application):
    row = await feature(api, headers[0])
    await stop(api, headers[0], "features", row["feature_id"])
    await stop(api, headers[0], "products", row["data"]["product_id"])
    org = tenants["orgs"][0]
    async with application.state.db.transaction(org) as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM resource_lifecycle_events WHERE revision=1")
            )
            == 2
        )
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(org) as session:
            await session.execute(
                text(
                    "INSERT INTO resource_lifecycle_events(org_id,feature_id,revision,resource_revision,"
                    "before_state,after_state,reason_code,actor_user_id)"
                    " VALUES(:org,:root,1,1,'active','inactive','obsolete',:actor)"
                ),
                {"org": org, "root": UUID(row["feature_id"]), "actor": tenants["users"][0]},
            )
    assert failure.value.orig.sqlstate == "23505"


@pytest.mark.parametrize("kind", ["products", "features"])
async def test_sql_inactive_feature_or_parent_rejects_insert_and_reactivation(
    kind, api, headers, tenants, application
):
    row = await feature(api, headers[0])
    task_id = await task(api, headers[0])
    selected = await api.post(
        f"/tasks/{task_id}/features", headers=headers[0], json={"feature_id": row["feature_id"]}
    )
    assert selected.status_code == 200, selected.text
    await stop(
        api,
        headers[0],
        kind,
        row["feature_id"] if kind == "features" else row["data"]["product_id"],
    )
    org = tenants["orgs"][0]
    actor = Identity(tenants["users"][0], org, set(ROLE_SCOPES["admin"]), "admin")
    params = {
        "org": org,
        "root": UUID(row["feature_id"]),
        "revision": UUID(row["id"]),
        "task": UUID(task_id),
        "pin": UUID(selected.json()["data"]["id"]),
        "id": uuid4(),
    }
    async with application.state.db.transaction(org) as session:
        await set_actor_context(session, actor)
        await session.execute(text("UPDATE task_features SET active=false WHERE id=:pin"), params)
    # Retained selections cannot be reactivated even with an active library:
    # the existing BEFORE history guard precedes the AFTER lifecycle guard.
    for sql, sqlstate, message in (
        (
            "UPDATE task_features SET active=true WHERE id=:pin",
            "42501",
            "Historical selections cannot be reactivated; create a new selection",
        ),
        (
            "INSERT INTO task_features(id,org_id,task_id,feature_id,feature_revision_id,lot,active)"
            " VALUES(:id,:org,:task,:root,:revision,'raw-new',true)",
            "23514",
            "inactive feature or parent cannot receive a new pin",
        ),
    ):
        with pytest.raises(DBAPIError) as failure:
            async with application.state.db.transaction(org) as session:
                await set_actor_context(session, actor)
                await session.execute(text(sql), params)
        assert failure.value.orig.sqlstate == sqlstate
        assert failure.value.orig.diag.message_primary == message


async def test_same_statement_feature_parents_still_require_pin_authority(
    api, headers, tenants, application
):
    from test_features import product

    parent = await product(api, headers[0])
    task_id = await task(api, headers[0])
    params = {
        "org": tenants["orgs"][0],
        "user": tenants["users"][0],
        "parent": UUID(parent),
        "root": uuid4(),
        "revision": uuid4(),
        "pin": uuid4(),
        "task": UUID(task_id),
    }
    statement = (
        "WITH new_feature AS ("
        " INSERT INTO features(id,org_id,created_by,current_revision)"
        " VALUES(:root,:org,:user,1) RETURNING id"
        "), new_revision AS ("
        " INSERT INTO feature_revisions(id,org_id,feature_id,product_id,revision,data)"
        " SELECT :revision,:org,new_feature.id,:parent,1,"
        " jsonb_build_object('product_id',CAST(:parent AS text),'name','Same statement',"
        " 'description','Synthetic declaration','status','planned') FROM new_feature"
        " RETURNING id,feature_id"
        ") INSERT INTO task_features(id,org_id,task_id,feature_id,feature_revision_id,lot,active)"
        " SELECT :pin,:org,:task,new_revision.feature_id,new_revision.id,'same-statement',true"
        " FROM new_revision"
    )
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(params["org"]) as session:
            await session.execute(text(statement), params)
    assert failure.value.orig.sqlstate == "42501"
    async with application.state.db.transaction(params["org"]) as session:
        assert await session.scalar(text("SELECT id FROM features WHERE id=:root"), params) is None


async def test_feature_revision_parent_fk_and_inactive_parent_sql_guard(
    api, headers, tenants, application
):
    import json

    from test_features import metadata, product

    row = await feature(api, headers[0])
    foreign_parent = await product(api, headers[1])
    await stop(api, headers[0], "products", row["data"]["product_id"])
    for parent, sqlstate in ((foreign_parent, "23503"), (row["data"]["product_id"], "23514")):
        with pytest.raises(DBAPIError) as failure:
            async with application.state.db.transaction(tenants["orgs"][0]) as session:
                await session.execute(
                    text(
                        "INSERT INTO feature_revisions(id,org_id,feature_id,product_id,revision,data)"
                        " VALUES(:id,:org,:root,:parent,2,CAST(:data AS jsonb))"
                    ),
                    {
                        "id": uuid4(),
                        "org": tenants["orgs"][0],
                        "root": UUID(row["feature_id"]),
                        "parent": UUID(parent),
                        "data": json.dumps(metadata(parent)),
                    },
                )
        assert failure.value.orig.sqlstate == sqlstate


@pytest.mark.parametrize("inactive_parent", ["current", "historical"])
async def test_old_revision_pins_check_both_parent_associations(inactive_parent, api, headers):
    from test_features import product

    row = await feature(api, headers[0])
    old_parent = row["data"]["product_id"]
    new_parent = await product(api, headers[0])
    task_id = await task(api, headers[0])
    pin_path = f"/tasks/{task_id}/features"
    old_pin = await api.post(
        pin_path, headers=headers[0], json={"feature_id": row["feature_id"], "revision": 1}
    )
    assert old_pin.status_code == 200, old_pin.text
    revised = await api.post(
        f"/resources/features/{row['feature_id']}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "data": {**row["data"], "product_id": new_parent}},
    )
    assert revised.status_code == 200, revised.text
    await stop(
        api, headers[0], "products", new_parent if inactive_parent == "current" else old_parent
    )
    duplicate = await api.post(
        pin_path, headers=headers[0], json={"feature_id": row["feature_id"], "revision": 1}
    )
    assert duplicate.status_code == 200 and duplicate.json()["data"]["duplicate"]
    denied = await api.post(
        pin_path,
        headers=headers[0],
        json={"feature_id": row["feature_id"], "revision": 1, "lot": "new"},
    )
    assert denied.status_code == 409, denied.text
    assert denied.json()["data"]["error"]["code"] == "resource_inactive"


async def test_parent_deactivation_and_new_pin_serialize(api, headers):
    import asyncio

    row = await feature(api, headers[0])
    task_id = await task(api, headers[0])
    selected, _ = await asyncio.gather(
        api.post(
            f"/tasks/{task_id}/features", headers=headers[0], json={"feature_id": row["feature_id"]}
        ),
        stop(api, headers[0], "products", row["data"]["product_id"]),
    )
    assert selected.status_code in {200, 409}, selected.text
    current = await api.get(f"/tasks/{task_id}/features", headers=headers[0])
    assert current.status_code == 200
    assert len(current.json()["items"]) == (1 if selected.status_code == 200 else 0)
    if selected.status_code == 409:
        assert selected.json()["data"]["error"]["code"] == "resource_inactive"
