"""Restricted PostgreSQL acceptance for template lifecycle and binding storage.

Failure inventory: invalid root/revision/actor, zero or multiple lifecycle
arms, missing/token actor, mutable lifecycle/content/binding history, stale
sequence, non-atomic audit, inactive pin inserts/reactivation and binding writes.
Composite relationships precede business guards. No SQLite substitutions.
"""

import json
from uuid import UUID, uuid4

import pytest
from app.services.auth import HUMAN_SCOPES, Identity, set_actor_context
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_management_products import lifecycle
from test_management_templates import BASE, bind, create, pin
from test_templates import task


@pytest.mark.parametrize(
    "change,state",
    [
        ("missing_root", "23503"),
        ("wrong_revision", "23503"),
        ("neither_root", "23514"),
        ("both_roots", "23514"),
        ("no_actor", "42501"),
    ],
)
async def test_lifecycle_relations_before_business_guards(
    change, state, api, headers, database, application
):
    from test_features import product

    own = await create(api, headers)
    other_arm = await product(api, headers)
    params = {"id": uuid4(), "template": UUID(own["template_id"]), "product": None, "version": 1}
    if change == "missing_root":
        params["template"] = uuid4()
    elif change == "wrong_revision":
        params["version"] = 77
    elif change == "neither_root":
        params["template"] = None
    elif change == "both_roots":
        params["product"] = UUID(other_arm)
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO resource_lifecycle_events(id,template_id,product_id,revision,resource_revision,before_state,after_state,reason_code) VALUES(:id,:template,:product,1,:version,'active','inactive','obsolete')"
                ),
                params,
            )
    assert failure.value.orig.sqlstate == state


async def test_lifecycle_and_binding_immutability_atomic_audit(api, headers, database, application):
    row = await create(api, headers)
    binding = await bind(api, headers, row)
    assert (
        await api.post(f"{BASE}/{row['template_id']}/lifecycle", headers=headers, json=lifecycle())
    ).status_code == 200
    actor = Identity(scopes=set(HUMAN_SCOPES))
    params = {
        "root": UUID(row["template_id"]),
        "binding": UUID(binding["id"]),
        "revision": UUID(row["id"]),
    }
    for sql in (
        "UPDATE resource_lifecycle_events SET reason_code='other'",
        "DELETE FROM resource_lifecycle_events",
        "UPDATE templates SET lifecycle_state='active',lifecycle_revision=2 WHERE id=:root",
        "UPDATE template_revisions SET data='{}' WHERE id=:revision",
        "DELETE FROM template_revisions WHERE id=:revision",
        "UPDATE export_template_bindings SET static_content_hash=repeat('d',64) WHERE id=:binding",
        "DELETE FROM export_template_bindings WHERE id=:binding",
        "INSERT INTO resource_lifecycle_events(template_id,revision,resource_revision,before_state,after_state,reason_code) VALUES(:root,2,1,'inactive','active','restored')",
    ):
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction() as session:
                await set_actor_context(session, actor)
                await session.execute(text(sql), params)
    async with application.state.db.transaction() as session:
        assert (
            await session.scalar(
                text("SELECT lifecycle_revision FROM templates WHERE id=:root"), params
            )
            == 1
        )
        for table in (
            "templates",
            "template_revisions",
            "resource_lifecycle_events",
            "export_template_bindings",
        ):
            flags = (
                await session.execute(
                    text(
                        "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=CAST(:table AS regclass)"
                    ),
                    {"table": table},
                )
            ).one()
            assert not flags.relrowsecurity and not flags.relforcerowsecurity
        assert (
            await session.scalar(
                text("SELECT count(*) FROM resource_lifecycle_events WHERE template_id=:root"),
                params,
            )
            == 1
        )
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM audit_logs WHERE object_id=:root AND action='resource.template.deactivate'"
                ),
                params,
            )
            == 1
        )
        await set_actor_context(session, actor)
        assert (
            await session.scalar(
                text("SELECT static_content_hash FROM export_template_bindings WHERE id=:binding"),
                params,
            )
            == binding["static_content_hash"]
        )


@pytest.mark.parametrize(
    "change,state", [("missing_revision", "23503"), ("wrong_root", "23503"), ("no_actor", "42501")]
)
async def test_template_pin_composite_fk_before_authority(
    change, state, api, headers, database, application
):
    own, other = (await create(api, headers), await create(api, headers))
    task_id = await task(api, headers)
    revision = (
        str(uuid4())
        if change == "missing_revision"
        else other["id"]
        if change == "wrong_root"
        else own["id"]
    )
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO task_templates(id,task_id,template_id,template_revision_id,lot,active) VALUES(:id,:task,:root,:revision,'raw',true)"
                ),
                {
                    "id": uuid4(),
                    "task": UUID(task_id),
                    "root": UUID(own["template_id"]),
                    "revision": UUID(revision),
                },
            )
    assert failure.value.orig.sqlstate == state


@pytest.mark.parametrize("restored", [False, True])
async def test_inactive_new_pin_and_retained_history_reactivation_sql(
    api, headers, database, application, restored
):
    row = await create(api, headers)
    task_id = await task(api, headers)
    selected = await pin(api, headers, task_id, row)
    assert selected.status_code == 200
    assert (
        await api.post(f"{BASE}/{row['template_id']}/lifecycle", headers=headers, json=lifecycle())
    ).status_code == 200
    actor = Identity(scopes=set(HUMAN_SCOPES))
    params = {
        "id": uuid4(),
        "task": UUID(task_id),
        "root": UUID(row["template_id"]),
        "revision": UUID(row["id"]),
        "pin": UUID(selected.json()["data"]["id"]),
    }
    async with application.state.db.transaction() as session:
        await set_actor_context(session, actor)
        await session.execute(text("UPDATE task_templates SET active=false WHERE id=:pin"), params)
    if restored:
        assert (
            await api.post(
                f"{BASE}/{row['template_id']}/lifecycle",
                headers=headers,
                json=lifecycle(1, 1, "active"),
            )
        ).status_code == 200
    attempts = [
        (
            "UPDATE task_templates SET active=true WHERE id=:pin",
            "42501",
            "Historical selections cannot be reactivated; create a new selection",
        )
    ]
    if not restored:
        attempts.append(
            (
                "INSERT INTO task_templates(id,task_id,template_id,template_revision_id,lot,active) VALUES(:id,:task,:root,:revision,'raw-new',true)",
                "23514",
                "inactive template cannot receive a new pin",
            )
        )
    for sql, sqlstate, message in attempts:
        with pytest.raises(DBAPIError) as failure:
            async with application.state.db.transaction() as session:
                await set_actor_context(session, actor)
                await session.execute(text(sql), params)
        assert failure.value.orig.sqlstate == sqlstate
        assert failure.value.orig.diag.message_primary == message
    async with application.state.db.transaction() as session:
        assert await session.scalar(text("SELECT count(*) FROM task_templates WHERE active")) == 0
    if restored:
        fresh = await pin(api, headers, task_id, row)
        assert fresh.status_code == 200
        assert fresh.json()["data"]["id"] != str(params["pin"])
        assert fresh.json()["data"]["duplicate"] is False


async def test_independent_template_lifecycle_sequence_unique_and_no_baseline_events(
    api, headers, database, application
):
    from test_features import product

    own = await create(api, headers)
    parent = await product(api, headers)
    async with application.state.db.transaction() as session:
        root = (
            await session.execute(
                text("SELECT lifecycle_state,lifecycle_revision FROM templates WHERE id=:id"),
                {"id": UUID(own["template_id"])},
            )
        ).one()
        assert (root.lifecycle_state, root.lifecycle_revision) == ("active", 0)
        assert await session.scalar(text("SELECT count(*) FROM resource_lifecycle_events")) == 0
    for kind, identifier in (("templates", own["template_id"]), ("products", parent)):
        assert (
            await api.post(
                f"/v4/management/resources/{kind}/{identifier}/lifecycle",
                headers=headers,
                json=lifecycle(),
            )
        ).status_code == 200
    async with application.state.db.transaction() as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM resource_lifecycle_events WHERE revision=1")
            )
            == 2
        )
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction() as session:
            await session.execute(
                text(
                    "INSERT INTO resource_lifecycle_events(template_id,revision,resource_revision,before_state,after_state,reason_code) VALUES(:root,1,1,'active','inactive','obsolete')"
                ),
                {"root": UUID(own["template_id"])},
            )
    assert failure.value.orig.sqlstate == "23505"


@pytest.mark.parametrize("kind", ["missing", "token", "missing_revision"])
async def test_binding_sql_rejects_actor_bypass_and_missing_parent(
    kind, api, headers, database, application, admin_engine
):
    row = await create(api, headers)
    binding = await bind(api, headers, row)
    actor = Identity(scopes=set(HUMAN_SCOPES))
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction() as session:
            if kind != "missing":
                await set_actor_context(session, actor)
            if kind == "token":
                await session.execute(text("SELECT set_config('app.actor_kind','token',true)"))
            await session.execute(
                text(
                    "INSERT INTO export_template_bindings(id,template_revision_id,template_sha256,binding_hash,sections,static_content_hash,adapter_version,reviewed_at) VALUES(:id,:revision,:sha,repeat('d',64),CAST(:sections AS jsonb),:static,:adapter,now())"
                ),
                {
                    "id": uuid4(),
                    "revision": uuid4() if kind == "missing_revision" else UUID(row["id"]),
                    "sha": binding["template_sha256"],
                    "sections": json.dumps(binding["sections"]),
                    "static": binding["static_content_hash"],
                    "adapter": binding["adapter_version"],
                },
            )
    assert failure.value.orig.sqlstate == ("23503" if kind == "missing_revision" else "42501")


async def test_template_deactivation_new_pin_race_keeps_one_valid_outcome(api, headers):
    import asyncio

    row = await create(api, headers)
    task_id = await task(api, headers)
    selected, stopped = await asyncio.gather(
        pin(api, headers, task_id, row),
        api.post(f"{BASE}/{row['template_id']}/lifecycle", headers=headers, json=lifecycle()),
    )
    assert stopped.status_code == 200
    assert selected.status_code in {200, 409}, selected.text
    listed = await api.get(f"/tasks/{task_id}/templates", headers=headers)
    assert len(listed.json()["items"]) == (1 if selected.status_code == 200 else 0)
    if selected.status_code == 409:
        assert selected.json()["data"]["error"]["code"] == "resource_inactive"
