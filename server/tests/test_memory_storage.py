"""Memory persistence security exercised against migrated PostgreSQL.

Failures to prevent: missing tenant context, foreign-org writes and reads, token
human-only scopes, mutable history, pointer jumps, and non-human activation.
The application-level memory tests cover the full feedback and generation flows.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

TABLES = (
    "memories",
    "memory_revisions",
    "memory_scope_epochs",
    "memory_feedback_events",
    "memory_eval_samples",
    "memory_retrievals",
    "memory_retrieval_items",
    "memory_call_inputs",
)


@pytest.mark.parametrize("table", TABLES)
def test_memory_tables_force_tenant_security(tenants, admin_engine, table):
    with admin_engine.connect() as connection:
        assert connection.execute(
            text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE relname=:table"),
            {"table": table},
        ).one() == (True, True)
        assert (
            connection.execute(
                text(
                    "SELECT is_nullable FROM information_schema.columns WHERE table_name=:table AND column_name='org_id'"
                ),
                {"table": table},
            ).scalar_one()
            == "NO"
        )
        policy = connection.execute(
            text(
                "SELECT qual,with_check FROM pg_policies WHERE tablename=:table AND policyname='tenant_scope'"
            ),
            {"table": table},
        ).one()
        assert all("app.current_org" in value for value in policy)
        connection.execute(text("SET LOCAL ROLE bid_app"))
        connection.execute(text("SELECT set_config('app.current_org','',true)"))
        assert connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one() == 0
        for org in tenants["orgs"]:
            connection.execute(
                text("SELECT set_config('app.current_org',:org,true)"), {"org": str(org)}
            )
            assert (
                connection.execute(
                    text(f'SELECT count(*) FROM "{table}" WHERE org_id<>:org'), {"org": org}
                ).scalar_one()
                == 0
            )


@pytest.mark.parametrize(
    "scope", ["memory:approve", "memory:manage", "memory:eval:read", "memory:eval:review"]
)
def test_token_cannot_receive_memory_human_scope(tenants, admin_engine, scope):
    with pytest.raises(DBAPIError), admin_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO api_tokens(id,org_id,user_id,name,digest,scopes,expires_at,revoked) "
                "VALUES(:id,:org,:user,'invalid-memory-scope',:digest,jsonb_build_array(CAST(:scope AS text)),now()+interval '1 day',false)"
            ),
            {
                "id": uuid4(),
                "org": tenants["orgs"][0],
                "user": tenants["users"][0],
                "digest": uuid4().hex,
                "scope": scope,
            },
        )


@pytest.mark.parametrize("table", TABLES)
def test_memory_composite_foreign_keys(tenants, admin_engine, table):
    with admin_engine.connect() as connection:
        keys = (
            connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=CAST(:table AS regclass) AND contype='f'"
                ),
                {"table": table},
            )
            .scalars()
            .all()
        )
        assert keys
        assert all(key.startswith("FOREIGN KEY (org_id") for key in keys)


def test_epoch_isolation_and_monotonicity(tenants, admin_engine):
    org_a, org_b = tenants["orgs"]
    with admin_engine.begin() as connection:
        for org in (org_a, org_b):
            connection.execute(
                text(
                    "INSERT INTO memory_scope_epochs(org_id,scope,owner_id,epoch) VALUES(:org,'org',:org,0)"
                ),
                {"org": org},
            )
    with admin_engine.connect() as connection:
        connection.execute(text("SET LOCAL ROLE bid_app"))
        connection.execute(
            text("SELECT set_config('app.current_org',:org,true)"), {"org": str(org_a)}
        )
        assert connection.execute(
            text("SELECT org_id FROM memory_scope_epochs")
        ).scalars().all() == [org_a]
    for statement, parameters in (
        ("UPDATE memory_scope_epochs SET epoch=-1 WHERE org_id=:org", {"org": org_a}),
        (
            "UPDATE memory_scope_epochs SET owner_id=:other WHERE org_id=:org",
            {"org": org_a, "other": org_b},
        ),
    ):
        with pytest.raises(DBAPIError), admin_engine.begin() as connection:
            connection.execute(text(statement), parameters)


async def test_memory_storage_full_flow_isolation_and_immutable_history(
    tenants, tmp_path, admin_engine
):
    """API → provider → human feedback → worker, then raw application-role attacks."""
    import asyncio
    import json
    from pathlib import Path

    from test_card_generation import drafting_client, execute, slots, submit
    from test_response_cards import (
        create_tender,
        login,
        require_action,
        select_real_materials,
        set_role,
    )

    org_a, org_b = tenants["orgs"]
    async with drafting_client(tenants, tmp_path) as (api, app, headers, _, _):
        created = await api.post(
            "/memories",
            headers=headers[0],
            json={
                "target": {"scope": "org"},
                "content": {
                    "kind": "rule",
                    "conflict_key": "storage.delivery",
                    "text": "Use concise response wording",
                    "tags": ["drafting"],
                },
            },
        )
        assert created.status_code == 200, created.text
        memory_id = created.json()["data"]["memory"]["id"]
        approved = await api.post(
            f"/memories/{memory_id}/decisions",
            headers=headers[0],
            json={"expected_revision": 1, "action": "approve", "reason": "Applies to drafting"},
        )
        assert approved.status_code == 200, approved.text
        task, _, extraction, _ = await create_tender(api, app, headers[0], tmp_path)
        await select_real_materials(api, headers[0], task, tmp_path)
        terminal = await execute(
            api, app, headers[0], await submit(api, headers[0], task, extraction)
        )
        assert terminal["data"]["status"] == "succeeded", terminal
        current = await slots(api, headers[0], task, extraction)
        set_role(admin_engine, org_a, tenants["users"][0], "technical")
        human = await login(api, org_a, "a")
        pending = await require_action(api, human, current[2]["card"], "submit")
        await require_action(api, human, pending, "reject", reason="State concrete delivery terms")
        feedback = await api.get(f"/tasks/{task}/memory-feedback", headers=human)
        assert feedback.status_code == 200, feedback.text
        event_id = feedback.json()["items"][0]["id"]
        proposal = await api.post(
            f"/tasks/{task}/memory-candidates", headers=human, json={"event_ids": [event_id]}
        )
        assert proposal.status_code == 200, proposal.text
        await app.state.processor(human["X-Org-Id"], proposal.json()["data"]["job_id"])
        status = await api.get(f"/jobs/{proposal.json()['data']['job_id']}", headers=human)
        assert status.json()["data"]["status"] == "succeeded", status.text

        counts = {}
        for table in TABLES:
            with admin_engine.connect() as connection:
                connection.execute(text("SET LOCAL ROLE bid_app"))
                connection.execute(
                    text("SELECT set_config('app.current_org',:org,true)"), {"org": str(org_a)}
                )
                rows = (
                    connection.execute(text(f'SELECT to_jsonb(t) FROM "{table}" t')).scalars().all()
                )
                assert rows, table
                counts[table] = len(rows)
                for foreign_context in (str(org_b), ""):
                    connection.execute(
                        text("SELECT set_config('app.current_org',:org,true)"),
                        {"org": foreign_context},
                    )
                    assert (
                        connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()
                        == 0
                    )
            identity = "org_id" if table == "memory_scope_epochs" else "id"
            identifier = rows[0][identity]
            for statement in (
                f'UPDATE "{table}" SET {identity}={identity} WHERE {identity}=:identifier',
                f'DELETE FROM "{table}" WHERE {identity}=:identifier',
            ):
                # A foreign identity cannot mutate any seeded row. Tables without
                # UPDATE/DELETE grants reject earlier, which is also expected.
                with admin_engine.connect() as connection:
                    connection.execute(text("SET LOCAL ROLE bid_app"))
                    connection.execute(
                        text("SELECT set_config('app.current_org',:org,true)"), {"org": str(org_b)}
                    )
                    try:
                        assert (
                            connection.execute(text(statement), {"identifier": identifier}).rowcount
                            == 0
                        )
                    except DBAPIError as exc:
                        assert getattr(exc.orig, "sqlstate", None) == "42501"
                    connection.rollback()
            # Authorized tenant still cannot rewrite durable history or erase it.
            own_update = (
                "UPDATE memory_scope_epochs SET epoch=-1 WHERE org_id=:identifier"
                if table == "memory_scope_epochs"
                else f'UPDATE "{table}" SET id=id WHERE id=:identifier'
            )
            for statement in (own_update, f'DELETE FROM "{table}" WHERE {identity}=:identifier'):
                with pytest.raises(DBAPIError), admin_engine.begin() as connection:
                    connection.execute(text("SET LOCAL ROLE bid_app"))
                    connection.execute(
                        text("SELECT set_config('app.current_org',:org,true)"), {"org": str(org_a)}
                    )
                    connection.execute(text(statement), {"identifier": identifier})
            forged = {**rows[0], "org_id": str(org_b)}
            if table == "memory_scope_epochs":
                forged["owner_id"] = str(org_b)
            else:
                forged["id"] = str(uuid4())
            with pytest.raises(DBAPIError), admin_engine.begin() as connection:
                connection.execute(text("SET LOCAL ROLE bid_app"))
                connection.execute(
                    text("SELECT set_config('app.current_org',:org,true)"), {"org": str(org_a)}
                )
                connection.execute(
                    text(
                        f'INSERT INTO "{table}" SELECT (jsonb_populate_record(NULL::"{table}",CAST(:row AS jsonb))).*'
                    ),
                    {"row": json.dumps(forged)},
                )

        artifact = Path("data/work/memory-validation/storage.json")
        await asyncio.to_thread(artifact.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(
            artifact.write_text,
            json.dumps(
                {
                    "version": "memory-storage-v1",
                    "tables": counts,
                    "assertions": [
                        "all_tables_populated",
                        "foreign_and_missing_context_hidden",
                        "foreign_mutations_blocked",
                        "own_history_immutable",
                        "cross_org_insert_rejected",
                    ],
                },
                sort_keys=True,
                indent=2,
            ),
        )


@pytest.mark.parametrize("actor_kind", ["token", "agent", "worker"])
async def test_nonhuman_direct_sql_cannot_activate_memory(
    api, headers, tenants, admin_engine, actor_kind
):
    """A valid tenant and matching actor context do not confer human approval."""
    import hashlib
    import json
    from datetime import UTC, datetime, timedelta

    from test_memory_api import add

    memory = await add(api, headers[0])
    org_id, user_id = tenants["orgs"][0], tenants["users"][0]
    token_id = None
    if actor_kind == "token":
        response = await api.post(
            "/tokens",
            headers=headers[0],
            json={
                "name": "memory-sql-gate",
                "scopes": ["memory:read", "memory:write"],
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
        assert response.status_code == 200, response.text
        with admin_engine.connect() as connection:
            token_id = connection.execute(
                text("SELECT id FROM api_tokens WHERE org_id=:org AND name='memory-sql-gate'"),
                {"org": org_id},
            ).scalar_one()
    with admin_engine.connect() as connection:
        original = connection.execute(
            text("SELECT to_jsonb(r) FROM memory_revisions r WHERE org_id=:org AND id=:revision"),
            {"org": org_id, "revision": memory["current"]["id"]},
        ).scalar_one()
        epoch_before = connection.execute(
            text("SELECT epoch FROM memory_scope_epochs WHERE org_id=:org AND scope='org'"),
            {"org": org_id},
        ).scalar_one()
    revision_id = uuid4()
    forged = {
        **original,
        "id": str(revision_id),
        "revision": 2,
        "status": "active",
        "actor_kind": actor_kind,
        "actor_token_id": str(token_id) if token_id else None,
        "created_by": str(user_id),
        "confirmed_by": str(user_id),
        "confirmed_at": datetime.now(UTC).isoformat(),
        "decision": "approve",
        "decision_reason_sha256": hashlib.sha256(b"Synthetic forged approval").hexdigest(),
    }
    with pytest.raises(DBAPIError) as rejected, admin_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE bid_app"))
        connection.execute(
            text(
                "SELECT set_config('app.current_org',:org,true),"
                "set_config('app.actor_user_id',:actor,true),"
                "set_config('app.actor_token_id',:token,true),"
                "set_config('app.actor_kind',:kind,true)"
            ),
            {
                "org": str(org_id),
                "actor": str(user_id),
                "token": str(token_id) if token_id else "",
                "kind": actor_kind,
            },
        )
        connection.execute(
            text(
                "INSERT INTO memory_revisions SELECT "
                "(jsonb_populate_record(NULL::memory_revisions,CAST(:row AS jsonb))).*"
            ),
            {"row": json.dumps(forged)},
        )
        connection.execute(
            text(
                "UPDATE memories SET revision=2,current_revision_id=:revision WHERE org_id=:org AND id=:memory"
            ),
            {"revision": revision_id, "org": org_id, "memory": memory["id"]},
        )
    assert getattr(rejected.value.orig, "sqlstate", None) == "42501"
    assert "Human decision context required" in str(rejected.value.orig)
    with admin_engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT m.revision,r.status,r.confirmed_by FROM memories m "
                "JOIN memory_revisions r ON r.org_id=m.org_id AND r.id=m.current_revision_id "
                "WHERE m.org_id=:org AND m.id=:memory"
            ),
            {"org": org_id, "memory": memory["id"]},
        ).one() == (1, "candidate", None)
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM memory_revisions WHERE org_id=:org AND memory_id=:memory"
                ),
                {"org": org_id, "memory": memory["id"]},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT epoch FROM memory_scope_epochs WHERE org_id=:org AND scope='org'"),
                {"org": org_id},
            ).scalar_one()
            == epoch_before
        )


async def test_concurrent_conflicting_approvals_activate_exactly_one(
    api, headers, tenants, admin_engine
):
    """Two independent API transactions serialize at the organization epoch."""
    import asyncio

    from test_memory_api import add

    first, second = await add(api, headers[0]), await add(api, headers[0])

    async def approve(memory):
        return await api.post(
            f"/memories/{memory['id']}/decisions",
            headers=headers[0],
            json={"expected_revision": 1, "action": "approve", "reason": "Concurrent review"},
        )

    responses = await asyncio.wait_for(asyncio.gather(approve(first), approve(second)), timeout=15)
    assert sorted(response.status_code for response in responses) == [200, 409], [
        response.text for response in responses
    ]
    with admin_engine.connect() as connection:
        states = (
            connection.execute(
                text(
                    "SELECT r.status FROM memories m JOIN memory_revisions r "
                    "ON r.org_id=m.org_id AND r.id=m.current_revision_id WHERE m.org_id=:org"
                ),
                {"org": tenants["orgs"][0]},
            )
            .scalars()
            .all()
        )
        assert sorted(states) == ["active", "candidate"]
        assert (
            connection.execute(
                text("SELECT epoch FROM memory_scope_epochs WHERE org_id=:org AND scope='org'"),
                {"org": tenants["orgs"][0]},
            ).scalar_one()
            == 1
        )
