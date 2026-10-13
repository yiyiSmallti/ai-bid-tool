"""Memory persistence security exercised against migrated PostgreSQL.

Failures to prevent: token human-only scopes, mutable history, pointer jumps,
and non-human activation.
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


@pytest.mark.parametrize(
    "scope", ["memory:approve", "memory:manage", "memory:eval:read", "memory:eval:review"]
)
def test_token_cannot_receive_memory_human_scope(database, admin_engine, scope):
    with pytest.raises(DBAPIError), admin_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO api_tokens(id,name,digest,scopes,expires_at,revoked) VALUES(:id,'invalid-memory-scope',:digest,jsonb_build_array(CAST(:scope AS text)),now()+interval '1 day',false)"
            ),
            {"id": uuid4(), "digest": uuid4().hex, "scope": scope},
        )


def test_library_epoch_monotonicity(database, admin_engine):
    with admin_engine.begin() as connection:
        connection.execute(text("INSERT INTO memory_scope_epochs(scope,epoch) VALUES('library',0)"))
        connection.execute(text("UPDATE memory_scope_epochs SET epoch=1 WHERE scope='library'"))
    with admin_engine.connect() as connection:
        connection.execute(text("SET LOCAL ROLE bid_app"))
        assert connection.execute(text("SELECT scope,epoch FROM memory_scope_epochs")).one() == (
            "library",
            1,
        )
    for statement in (
        "UPDATE memory_scope_epochs SET epoch=-1",
        "UPDATE memory_scope_epochs SET epoch=0",
        "UPDATE memory_scope_epochs SET scope='project'",
        "DELETE FROM memory_scope_epochs",
    ):
        with pytest.raises(DBAPIError), admin_engine.begin() as connection:
            connection.execute(text(statement))


async def test_memory_storage_full_flow_and_immutable_history(database, tmp_path, admin_engine):
    """API → provider → human feedback → worker, then raw application-role attacks."""
    import asyncio
    import json
    from pathlib import Path

    from test_card_generation import drafting_client, execute, slots, submit
    from test_response_cards import create_tender, login, require_action, select_real_materials

    async with drafting_client(database, tmp_path) as (api, app, headers, _, _):
        created = await api.post(
            "/memories",
            headers=headers,
            json={
                "target": {"scope": "library"},
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
            headers=headers,
            json={"expected_revision": 1, "action": "approve", "reason": "Applies to drafting"},
        )
        assert approved.status_code == 200, approved.text
        task, _, extraction, _ = await create_tender(api, app, headers, tmp_path)
        await select_real_materials(api, headers, task, tmp_path)
        terminal = await execute(api, app, headers, await submit(api, headers, task, extraction))
        assert terminal["data"]["status"] == "succeeded", terminal
        current = await slots(api, headers, task, extraction)
        human = await login(api)
        pending = await require_action(api, human, current[2]["card"], "submit")
        await require_action(api, human, pending, "reject", reason="State concrete delivery terms")
        feedback = await api.get(f"/tasks/{task}/memory-feedback", headers=human)
        assert feedback.status_code == 200, feedback.text
        event_id = feedback.json()["items"][0]["id"]
        proposal = await api.post(
            f"/tasks/{task}/memory-candidates", headers=human, json={"event_ids": [event_id]}
        )
        assert proposal.status_code == 200, proposal.text
        await app.state.processor(proposal.json()["data"]["job_id"])
        status = await api.get(f"/jobs/{proposal.json()['data']['job_id']}", headers=human)
        assert status.json()["data"]["status"] == "succeeded", status.text
        counts = {}
        for table in TABLES:
            with admin_engine.connect() as connection:
                connection.execute(text("SET LOCAL ROLE bid_app"))
                rows = (
                    connection.execute(text(f'SELECT to_jsonb(t) FROM "{table}" t')).scalars().all()
                )
                assert rows, table
                counts[table] = len(rows)
            identity = "scope" if table == "memory_scope_epochs" else "id"
            identifier = rows[0][identity]
            own_update = (
                "UPDATE memory_scope_epochs SET epoch=-1 WHERE scope=:identifier"
                if table == "memory_scope_epochs"
                else f'UPDATE "{table}" SET id=id WHERE id=:identifier'
            )
            for statement in (own_update, f'DELETE FROM "{table}" WHERE {identity}=:identifier'):
                with pytest.raises(DBAPIError), admin_engine.begin() as connection:
                    connection.execute(text("SET LOCAL ROLE bid_app"))
                    connection.execute(text(statement), {"identifier": identifier})
            forged = {**rows[0]}
            if table != "memory_scope_epochs":
                forged["id"] = str(uuid4())
            with pytest.raises(DBAPIError), admin_engine.begin() as connection:
                connection.execute(text("SET LOCAL ROLE bid_app"))
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
                        "history_immutable",
                        "unattributed_insert_rejected",
                        "duplicate_scope_rejected",
                    ],
                },
                sort_keys=True,
                indent=2,
            ),
        )


@pytest.mark.parametrize("actor_kind", ["token", "agent", "worker"])
async def test_nonhuman_direct_sql_cannot_activate_memory(
    api, headers, database, admin_engine, actor_kind
):
    """A matching actor context does not confer human approval."""
    import hashlib
    import json
    from datetime import UTC, datetime, timedelta

    from test_memory_api import add

    memory = await add(api, headers)
    token_id = None
    if actor_kind == "token":
        response = await api.post(
            "/tokens",
            headers=headers,
            json={
                "name": "memory-sql-gate",
                "scopes": ["memory:read", "memory:write"],
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
        assert response.status_code == 200, response.text
        with admin_engine.connect() as connection:
            token_id = connection.execute(
                text("SELECT id FROM api_tokens WHERE name='memory-sql-gate'")
            ).scalar_one()
    with admin_engine.connect() as connection:
        original = connection.execute(
            text("SELECT to_jsonb(r) FROM memory_revisions r WHERE id=:revision"),
            {"revision": memory["current"]["id"]},
        ).scalar_one()
        epoch_before = connection.execute(
            text("SELECT epoch FROM memory_scope_epochs WHERE scope='library'")
        ).scalar_one()
    revision_id = uuid4()
    forged = {
        **original,
        "id": str(revision_id),
        "revision": 2,
        "status": "active",
        "actor_kind": actor_kind,
        "actor_token_id": str(token_id) if token_id else None,
        "confirmed_at": datetime.now(UTC).isoformat(),
        "decision": "approve",
        "decision_reason_sha256": hashlib.sha256(b"Synthetic forged approval").hexdigest(),
    }
    with pytest.raises(DBAPIError) as rejected, admin_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE bid_app"))
        connection.execute(
            text(
                "SELECT set_config('app.actor_kind',:kind,true),set_config('app.actor_token_id',:token,true)"
            ),
            {"kind": actor_kind, "token": str(token_id) if token_id else ""},
        )
        connection.execute(
            text(
                "INSERT INTO memory_revisions SELECT (jsonb_populate_record(NULL::memory_revisions,CAST(:row AS jsonb))).*"
            ),
            {"row": json.dumps(forged)},
        )
        connection.execute(
            text("UPDATE memories SET revision=2,current_revision_id=:revision WHERE id=:memory"),
            {"revision": revision_id, "memory": memory["id"]},
        )
    assert getattr(rejected.value.orig, "sqlstate", None) == "42501"
    assert "Human decision context required" in str(rejected.value.orig)
    with admin_engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT m.revision,r.status,r.confirmed_at FROM memories m JOIN memory_revisions r ON r.id=m.current_revision_id WHERE m.id=:memory"
            ),
            {"memory": memory["id"]},
        ).one() == (1, "candidate", None)
        assert (
            connection.execute(
                text("SELECT count(*) FROM memory_revisions WHERE memory_id=:memory"),
                {"memory": memory["id"]},
            ).scalar_one()
            == 1
        )
        assert (
            connection.execute(
                text("SELECT epoch FROM memory_scope_epochs WHERE scope='library'")
            ).scalar_one()
            == epoch_before
        )


async def test_concurrent_conflicting_approvals_activate_exactly_one(
    api, headers, database, admin_engine
):
    """Two independent API transactions serialize at the library epoch."""
    import asyncio

    from test_memory_api import add

    first, second = (await add(api, headers), await add(api, headers))

    async def approve(memory):
        return await api.post(
            f"/memories/{memory['id']}/decisions",
            headers=headers,
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
                    "SELECT r.status FROM memories m JOIN memory_revisions r ON r.id=m.current_revision_id"
                ),
                {},
            )
            .scalars()
            .all()
        )
        assert sorted(states) == ["active", "candidate"]
        assert (
            connection.execute(
                text("SELECT epoch FROM memory_scope_epochs WHERE scope='library'"), {}
            ).scalar_one()
            == 1
        )
