"""Restricted PostgreSQL acceptance for template lifecycle and binding storage.

Failure inventory: foreign org/root/revision/actor, zero or multiple lifecycle
arms, missing/token actor, mutable lifecycle/content/binding history, stale
sequence, non-atomic audit, inactive pin inserts/reactivation and binding writes.
RLS/composite relationships precede business guards. No SQLite substitutions.
"""

import json
from uuid import UUID, uuid4

import pytest
from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_management_products import lifecycle
from test_management_templates import BASE, bind, create, pin
from test_templates import task


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
async def test_lifecycle_relations_before_business_guards(
    change, state, api, headers, tenants, application
):
    from test_features import product

    own, foreign = await create(api, headers[0]), await create(api, headers[1])
    other_arm = await product(api, headers[0])
    org = tenants["orgs"][0]
    params = {
        "id": uuid4(),
        "org": org,
        "template": UUID(own["template_id"]),
        "product": None,
        "actor": tenants["users"][0],
        "version": 1,
    }
    if change == "foreign_org":
        params["org"] = tenants["orgs"][1]
    elif change == "foreign_root":
        params["template"] = UUID(foreign["template_id"])
    elif change == "foreign_actor":
        params["actor"] = tenants["users"][1]
    elif change == "wrong_revision":
        params["version"] = 77
    elif change == "neither_root":
        params["template"] = None
    elif change == "both_roots":
        params["product"] = UUID(other_arm)
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(
            None if change == "no_context" else org
        ) as session:
            await session.execute(
                text(
                    "INSERT INTO resource_lifecycle_events"
                    "(id,org_id,template_id,product_id,revision,resource_revision,before_state,after_state,reason_code,actor_user_id)"
                    " VALUES(:id,:org,:template,:product,1,:version,'active','inactive','obsolete',:actor)"
                ),
                params,
            )
    assert failure.value.orig.sqlstate == state


async def test_lifecycle_and_binding_rls_immutability_atomic_audit(
    api, headers, tenants, application
):
    row = await create(api, headers[0])
    binding = await bind(api, headers[0], row)
    assert (
        await api.post(
            f"{BASE}/{row['template_id']}/lifecycle", headers=headers[0], json=lifecycle()
        )
    ).status_code == 200
    for org in (None, tenants["orgs"][1]):
        async with application.state.db.transaction(org) as session:
            for table in (
                "resource_lifecycle_events",
                "templates",
                "template_revisions",
                "export_template_bindings",
            ):
                assert await session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    org = tenants["orgs"][0]
    actor = Identity(tenants["users"][0], org, set(ROLE_SCOPES["admin"]), "admin")
    params = {
        "org": org,
        "root": UUID(row["template_id"]),
        "actor": actor.user_id,
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
        "INSERT INTO resource_lifecycle_events(org_id,template_id,revision,resource_revision,before_state,after_state,reason_code,actor_user_id) VALUES(:org,:root,2,1,'inactive','active','restored',:actor)",
    ):
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction(org) as session:
                await set_actor_context(session, actor)
                await session.execute(text(sql), params)
    async with application.state.db.transaction(org) as session:
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
            assert flags.relrowsecurity and flags.relforcerowsecurity
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
    "change,state",
    [("foreign_revision", "23503"), ("same_org_wrong_root", "23503"), ("no_actor", "42501")],
)
async def test_template_pin_composite_fk_before_authority(
    change, state, api, headers, tenants, application
):
    own, other, foreign = (
        await create(api, headers[0]),
        await create(api, headers[0]),
        await create(api, headers[1]),
    )
    task_id = await task(api, headers[0])
    revision = (
        foreign["id"]
        if change == "foreign_revision"
        else other["id"]
        if change == "same_org_wrong_root"
        else own["id"]
    )
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(tenants["orgs"][0]) as session:
            await session.execute(
                text(
                    "INSERT INTO task_templates(id,org_id,task_id,template_id,template_revision_id,lot,active)"
                    " VALUES(:id,:org,:task,:root,:revision,'raw',true)"
                ),
                {
                    "id": uuid4(),
                    "org": tenants["orgs"][0],
                    "task": UUID(task_id),
                    "root": UUID(own["template_id"]),
                    "revision": UUID(revision),
                },
            )
    assert failure.value.orig.sqlstate == state


@pytest.mark.parametrize("restored", [False, True])
async def test_inactive_new_pin_and_retained_history_reactivation_sql(
    api, headers, tenants, application, restored
):
    row = await create(api, headers[0])
    task_id = await task(api, headers[0])
    selected = await pin(api, headers[0], task_id, row)
    assert selected.status_code == 200
    assert (
        await api.post(
            f"{BASE}/{row['template_id']}/lifecycle", headers=headers[0], json=lifecycle()
        )
    ).status_code == 200
    org = tenants["orgs"][0]
    actor = Identity(tenants["users"][0], org, set(ROLE_SCOPES["admin"]), "admin")
    params = {
        "id": uuid4(),
        "org": org,
        "task": UUID(task_id),
        "root": UUID(row["template_id"]),
        "revision": UUID(row["id"]),
        "pin": UUID(selected.json()["data"]["id"]),
    }
    async with application.state.db.transaction(org) as session:
        await set_actor_context(session, actor)
        await session.execute(text("UPDATE task_templates SET active=false WHERE id=:pin"), params)
    if restored:
        assert (
            await api.post(
                f"{BASE}/{row['template_id']}/lifecycle",
                headers=headers[0],
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
                "INSERT INTO task_templates(id,org_id,task_id,template_id,template_revision_id,lot,active) VALUES(:id,:org,:task,:root,:revision,'raw-new',true)",
                "23514",
                "inactive template cannot receive a new pin",
            )
        )
    for sql, sqlstate, message in attempts:
        with pytest.raises(DBAPIError) as failure:
            async with application.state.db.transaction(org) as session:
                await set_actor_context(session, actor)
                await session.execute(text(sql), params)
        assert failure.value.orig.sqlstate == sqlstate
        assert failure.value.orig.diag.message_primary == message
    async with application.state.db.transaction(org) as session:
        assert await session.scalar(text("SELECT count(*) FROM task_templates WHERE active")) == 0
    if restored:
        # Restoration permits a fresh selection, never a rewrite of retained history.
        fresh = await pin(api, headers[0], task_id, row)
        assert fresh.status_code == 200
        assert fresh.json()["data"]["id"] != str(params["pin"])
        assert fresh.json()["data"]["duplicate"] is False


async def test_independent_template_lifecycle_sequence_unique_and_no_baseline_events(
    api, headers, tenants, application
):
    from test_features import product

    own = await create(api, headers[0])
    parent = await product(api, headers[0])
    org = tenants["orgs"][0]
    async with application.state.db.transaction(org) as session:
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
                headers=headers[0],
                json=lifecycle(),
            )
        ).status_code == 200
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
                    "INSERT INTO resource_lifecycle_events(org_id,template_id,revision,resource_revision,before_state,after_state,reason_code,actor_user_id) VALUES(:org,:root,1,1,'active','inactive','obsolete',:actor)"
                ),
                {"org": org, "root": UUID(own["template_id"]), "actor": tenants["users"][0]},
            )
    assert failure.value.orig.sqlstate == "23505"


@pytest.mark.parametrize(
    "kind", ["missing", "token", "technical", "bidder", "viewer", "foreign_org", "foreign_revision"]
)
async def test_binding_sql_rejects_actor_bypass_and_foreign_parent(
    kind, api, headers, tenants, application, admin_engine
):
    row, foreign = await create(api, headers[0]), await create(api, headers[1])
    binding = await bind(api, headers[0], row)
    org = tenants["orgs"][0]
    actor = Identity(tenants["users"][0], org, set(ROLE_SCOPES["admin"]), "admin")
    if kind in {"technical", "bidder", "viewer"}:
        from app.models.entities import Membership
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        with Session(admin_engine) as owner, owner.begin():
            owner.scalar(select(Membership).where(Membership.org_id == org)).role = kind
    with pytest.raises(DBAPIError) as failure:
        async with application.state.db.transaction(org) as session:
            if kind != "missing":
                await set_actor_context(session, actor)
            if kind == "token":
                await session.execute(text("SELECT set_config('app.actor_kind','token',true)"))
            await session.execute(
                text(
                    "INSERT INTO export_template_bindings(id,org_id,template_revision_id,template_sha256,binding_hash,sections,static_content_hash,adapter_version,reviewed_by,reviewed_at)"
                    " VALUES(:id,:org,:revision,:sha,repeat('d',64),CAST(:sections AS jsonb),:static,:adapter,:actor,now())"
                ),
                {
                    "id": uuid4(),
                    "org": tenants["orgs"][1] if kind == "foreign_org" else org,
                    "revision": UUID(foreign["id"] if kind == "foreign_revision" else row["id"]),
                    "sha": binding["template_sha256"],
                    "sections": json.dumps(binding["sections"]),
                    "static": binding["static_content_hash"],
                    "adapter": binding["adapter_version"],
                    "actor": actor.user_id,
                },
            )
    # The tenant composite FK rejects a foreign revision before the AFTER
    # export actor/hash guard; RLS still rejects a foreign org first.
    assert failure.value.orig.sqlstate == ("23503" if kind == "foreign_revision" else "42501")


async def test_template_deactivation_new_pin_race_keeps_one_valid_outcome(api, headers):
    import asyncio

    row = await create(api, headers[0])
    task_id = await task(api, headers[0])
    selected, stopped = await asyncio.gather(
        pin(api, headers[0], task_id, row),
        api.post(f"{BASE}/{row['template_id']}/lifecycle", headers=headers[0], json=lifecycle()),
    )
    assert stopped.status_code == 200
    assert selected.status_code in {200, 409}, selected.text
    listed = await api.get(f"/tasks/{task_id}/templates", headers=headers[0])
    assert len(listed.json()["items"]) == (1 if selected.status_code == 200 else 0)
    if selected.status_code == 409:
        assert selected.json()["data"]["error"]["code"] == "resource_inactive"
