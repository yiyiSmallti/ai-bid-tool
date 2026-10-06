"""B02 real API/PostgreSQL acceptance.

Failure scenarios were fixed before implementation in the delegated work inventory:
foreign org/task reads or writes leak metadata; replay reapplies obsolete decisions;
CAS and mixed batches partly commit; manual source verification accepts normalization
or changed previews; source change-back resurrects approval; tokens or reviewers can
confirm; SQL consumers bypass the human gate; source/history events lose atomicity.
These tests never create, stop or reconfigure a database service.
"""

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_response_cards import create_tender, phase_one_client

TABLES = (
    "requirement_review_sets",
    "requirement_reviews",
    "requirement_review_events",
    "requirement_review_requests",
)
ARTIFACT = Path(__file__).resolve().parents[2] / "data/work/requirement-confirmation/core.json"


async def review(api, auth, requirement_id):
    response = await api.get(f"/v4/requirements/{requirement_id}/review", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def decision(value, action="confirm", **changes):
    return {
        "request_id": str(uuid4()),
        "action": action,
        "expected_revision": value["requirement"]["revision"],
        "expected_review_hash": value["requirement"]["review_hash"],
        "reason": "Synthetic exact source inspection",
        **changes,
    }


async def post_decision(api, auth, requirement_id, payload):
    return await api.post(
        f"/v4/requirements/{requirement_id}/review-decisions", headers=auth, json=payload
    )


async def test_review_cas_replay_history_and_v4_boundary(tenants, tmp_path):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, job, requirements = await create_tender(
            api, app, headers[0], tmp_path, confirmed=False
        )
        rid = requirements[0]["id"]
        initial = await review(api, headers[0], rid)
        assert initial["requirement"]["state"] == "unconfirmed"
        body = decision(initial)
        accepted = await post_decision(api, headers[0], rid, body)
        assert accepted.status_code == 200, accepted.text
        approved = accepted.json()["data"]
        assert approved["requirement"]["state"] == "confirmed"
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            events = (
                await session.execute(
                    text(
                        "SELECT source_id,payload FROM task_events WHERE task_id=:task AND event_kind='board_changed' AND payload->'requirement_ids' ? :rid ORDER BY seq DESC LIMIT 1"
                    ),
                    {"task": UUID(task), "rid": rid},
                )
            ).all()
            assert events
            assert all(
                event.source_id is None and event.payload["card_ids"] == []
                for event in events
                if event.payload.get("requirement_ids") == [rid]
            )
        stale = await post_decision(api, headers[0], rid, {**body, "request_id": str(uuid4())})
        assert stale.status_code == 409
        reopened = await post_decision(api, headers[0], rid, decision(approved, "reopen"))
        assert reopened.status_code == 200, reopened.text
        replay = await post_decision(api, headers[0], rid, body)
        assert replay.status_code == 200, replay.text
        replayed = replay.json()["data"]
        assert replayed["replayed"] and replayed["requirement"]["state"] == "unconfirmed"
        assert replayed["event_ids"] == approved["event_ids"]
        conflict = await post_decision(api, headers[0], rid, {**body, "reason": "Different review"})
        assert (
            conflict.status_code == 409
            and conflict.json()["data"]["error"]["code"] == "idempotency_conflict"
        )
        history = await api.get(
            f"/v4/requirements/{rid}/review-history?limit=2", headers=headers[0]
        )
        assert history.status_code == 200, history.text
        assert len(history.json()["items"]) == 2
        page = await api.get(
            f"/v4/tasks/{task}/extractions/{job}/requirement-reviews?limit=1&starred=false",
            headers=headers[0],
        )
        assert page.status_code == 200, page.text
        assert (await api.get(f"/requirements/{rid}/review", headers=headers[0])).status_code == 404
        ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
        ARTIFACT.write_text(
            json.dumps(
                {
                    "command": "uv run pytest server/tests/test_requirement_confirmation.py -q",
                    "task_id": task,
                    "job_id": job,
                    "requirement_id": rid,
                    "replayed_event_ids": replayed["event_ids"],
                    "current_state": replayed["requirement"]["state"],
                },
                indent=2,
            )
            + "\n"
        )


async def test_manual_scope_preview_float_hash_and_replay(tenants, tmp_path):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, original_job, requirements = await create_tender(
            api, app, headers[0], tmp_path, confirmed=False
        )
        value = await review(api, headers[0], requirements[0]["id"])
        content = {
            **value["requirement"]["content"],
            "condition": {"small": 1e-7, "large": 1e20, "whole": 1.0},
        }
        preview_input = {"content": content, "reason": "Recover omitted interpretation"}
        preview = await api.post(
            f"/v4/tasks/{task}/requirements/manual-preview", headers=headers[0], json=preview_input
        )
        assert preview.status_code == 200, preview.text
        data = preview.json()["data"]
        assert data["creates_manual_scope"]
        body = {
            **preview_input,
            "request_id": str(uuid4()),
            "expected_preview_hash": data["preview_hash"],
        }
        saved = await api.post(
            f"/v4/tasks/{task}/requirements/manual", headers=headers[0], json=body
        )
        assert saved.status_code == 201, saved.text
        manual = saved.json()["data"]
        assert (
            manual["scope"]["origin"] == "manual"
            and manual["requirement"]["state"] == "unconfirmed"
        )
        assert manual["scope"]["extraction_job_id"] != original_job
        replay = await api.post(
            f"/v4/tasks/{task}/requirements/manual", headers=headers[0], json=body
        )
        assert replay.status_code == 200 and replay.json()["data"]["replayed"], replay.text
        accepted = await post_decision(
            api, headers[0], manual["requirement"]["requirement_id"], decision(manual)
        )
        assert accepted.status_code == 200, accepted.text
        async with app.state.db.transaction(UUID(headers[0]["X-Org-Id"])) as session:
            receipt = (
                await session.execute(
                    text(
                        "SELECT status,attempts,run_id,queue_id,provider_config_id FROM jobs WHERE id=:id"
                    ),
                    {"id": UUID(manual["scope"]["extraction_job_id"])},
                )
            ).one()
            assert tuple(receipt) == ("succeeded", 0, None, None, None)


@pytest.mark.parametrize("route", ["review", "review-history"])
async def test_review_foreign_org_is_uniform_404(tenants, tmp_path, route):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        _, _, _, requirements = await create_tender(api, app, headers[0], tmp_path, confirmed=False)
        foreign = await api.get(
            f"/v4/requirements/{requirements[0]['id']}/{route}", headers=headers[1]
        )
        missing = await api.get(f"/v4/requirements/{uuid4()}/{route}", headers=headers[1])
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json()["data"] == missing.json()["data"]


@pytest.mark.parametrize("table", TABLES)
async def test_review_tables_force_rls_and_foreign_reads(tenants, tmp_path, admin_engine, table):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        _, _, _, requirements = await create_tender(api, app, headers[0], tmp_path, confirmed=False)
        initial = await review(api, headers[0], requirements[0]["id"])
        assert (
            await post_decision(api, headers[0], requirements[0]["id"], decision(initial))
        ).status_code == 200
        with admin_engine.connect() as connection:
            assert all(
                connection.execute(
                    text(
                        "SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:table)"
                    ),
                    {"table": table},
                ).one()
            )
        async with app.state.db.transaction(tenants["orgs"][1]) as session:
            assert (
                await session.scalar(
                    text(f"SELECT count(*) FROM {table} WHERE org_id=:org"),
                    {"org": tenants["orgs"][0]},
                )
                == 0
            )
        if table in {"requirement_review_sets", "requirement_reviews"}:
            async with app.state.db.transaction(tenants["orgs"][1]) as session:
                changed = await session.execute(
                    text(f"UPDATE {table} SET revision=revision WHERE org_id=:org"),
                    {"org": tenants["orgs"][0]},
                )
                assert changed.rowcount == 0
        from app.models import Base
        from sqlalchemy import insert

        with admin_engine.connect() as connection:
            existing = connection.execute(Base.metadata.tables[table].select()).mappings().first()
            assert existing is not None
            foreign_row = {**dict(existing), "id": uuid4()}
        with pytest.raises(DBAPIError):
            async with app.state.db.transaction(tenants["orgs"][1]) as session:
                await session.execute(insert(Base.metadata.tables[table]).values(**foreign_row))
        async with app.state.db.engine.connect() as connection:
            await connection.execute(text("SELECT set_config('app.current_org','',true)"))
            assert await connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0
        with pytest.raises(DBAPIError):
            async with app.state.db.transaction(tenants["orgs"][0]) as session:
                await session.execute(text(f"DELETE FROM {table}"))


async def test_source_mutation_and_change_back_preserve_invalidation(
    tenants, tmp_path, admin_engine
):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        _, _, _, requirements = await create_tender(api, app, headers[0], tmp_path, confirmed=False)
        rid = requirements[0]["id"]
        initial = await review(api, headers[0], rid)
        assert (await post_decision(api, headers[0], rid, decision(initial))).status_code == 200
        source = initial["requirement"]["content"]["source"]
        with admin_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.current_org',:org,true)"),
                {"org": str(tenants["orgs"][0])},
            )
            original = connection.scalar(
                text("SELECT text FROM chunks WHERE id=:id"), {"id": source["chunk_id"]}
            )
            connection.execute(
                text("UPDATE chunks SET text=:value WHERE id=:id"),
                {"value": original + "\nAdditional parsed text", "id": source["chunk_id"]},
            )
        invalid = await review(api, headers[0], rid)
        assert invalid["requirement"]["state"] == "invalidated"
        with admin_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.current_org',:org,true)"),
                {"org": str(tenants["orgs"][0])},
            )
            connection.execute(
                text("UPDATE chunks SET text=:value WHERE id=:id"),
                {"value": original, "id": source["chunk_id"]},
            )
        restored = await review(api, headers[0], rid)
        assert restored["requirement"]["state"] == "invalidated"
        assert restored["requirement"]["revision"] > invalid["requirement"]["revision"]
        assert (await post_decision(api, headers[0], rid, decision(restored))).status_code == 200


def route_cases(task, job, value):
    rid = value["requirement"]["requirement_id"]
    manual = {"content": value["requirement"]["content"], "reason": "Synthetic missing requirement"}
    batch = {
        "request_id": str(uuid4()),
        "expected_set_revision": value["scope"]["revision"],
        "items": [
            {
                "requirement_id": rid,
                "expected_revision": value["requirement"]["revision"],
                "expected_review_hash": value["requirement"]["review_hash"],
            }
        ],
        "reviewed_each": True,
        "reason": "Inspected each exact source",
    }
    return [
        ("GET", f"/v4/tasks/{task}/extractions/{job}/requirement-reviews", None),
        ("GET", f"/v4/requirements/{rid}/review", None),
        ("GET", f"/v4/requirements/{rid}/review-history", None),
        ("GET", f"/v4/tasks/{task}/extractions/{job}/rejected-items", None),
        ("POST", f"/v4/tasks/{task}/requirements/manual-preview", manual),
        (
            "POST",
            f"/v4/tasks/{task}/requirements/manual",
            {**manual, "request_id": str(uuid4()), "expected_preview_hash": "0" * 64},
        ),
        ("POST", f"/v4/requirements/{rid}/review-decisions", decision(value)),
        ("POST", f"/v4/tasks/{task}/extractions/{job}/requirement-confirmations", batch),
    ]


@pytest.mark.parametrize("scope", ["foreign_org", "same_org_nonmember"])
@pytest.mark.parametrize("route_index", range(8))
async def test_every_review_route_masks_inaccessible_tasks(
    tenants, tmp_path, admin_engine, scope, route_index
):
    from test_team_workflow_membership import person

    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, job, requirements = await create_tender(
            api, app, headers[0], tmp_path, confirmed=False
        )
        value = await review(api, headers[0], requirements[0]["id"])
        if scope == "foreign_org":
            denied = headers[1]
        else:
            _, denied = await person(api, admin_engine, tenants["orgs"][0])
        method, path, body = route_cases(task, job, value)[route_index]
        response = await api.request(
            method, path, headers=denied, **({"json": body} if body is not None else {})
        )
        assert response.status_code == 404, response.text
        assert response.json()["data"]["error"]["code"] == "not_found"
        current = await review(api, headers[0], requirements[0]["id"])
        assert current["requirement"]["revision"] == value["requirement"]["revision"]


@pytest.mark.parametrize("authority", ["token", "observer", "reviewer", "archived"])
@pytest.mark.parametrize("route_index", [4, 5, 6, 7])
async def test_every_mutation_requires_active_human_owner_or_contributor(
    tenants, tmp_path, admin_engine, authority, route_index
):
    from datetime import UTC, datetime, timedelta

    from test_team_workflow_membership import add_member, person, workflow

    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, job, requirements = await create_tender(
            api, app, headers[0], tmp_path, confirmed=False
        )
        value = await review(api, headers[0], requirements[0]["id"])
        auth = headers[0]
        if authority == "token":
            response = await api.post(
                "/tokens",
                headers=headers[0],
                json={
                    "name": "Read-only review agent",
                    "scopes": ["task:read", "job:read"],
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                },
            )
            assert response.status_code == 200, response.text
            auth = {**headers[0], "Authorization": "Bearer " + response.json()["data"]["token"]}
        elif authority in {"observer", "reviewer"}:
            uid, auth = await person(api, admin_engine, tenants["orgs"][0])
            state = await workflow(api, headers[0], task)
            response = await add_member(
                api, headers[0], task, uid, state["revision"], role=authority
            )
            assert response.status_code == 200, response.text
        else:
            state = await workflow(api, headers[0], task)
            response = await api.post(
                f"/tasks/{task}/archive",
                headers=headers[0],
                json={
                    "expected_revision": state["revision"],
                    "reason": "Archive synthetic completed extraction",
                },
            )
            assert response.status_code == 200, response.text
        method, path, body = route_cases(task, job, value)[route_index]
        response = await api.request(method, path, headers=auth, json=body)
        assert response.status_code == (409 if authority == "archived" else 403), response.text
        assert (await review(api, headers[0], requirements[0]["id"]))["requirement"][
            "state"
        ] == "unconfirmed"


@pytest.mark.parametrize("invalid_target", ["stale_revision", "foreign_id"])
async def test_owner_batch_is_atomic(tenants, tmp_path, invalid_target):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, job, requirements = await create_tender(
            api, app, headers[0], tmp_path, confirmed=False
        )
        values = [await review(api, headers[0], item["id"]) for item in requirements[:2]]
        targets = [
            {
                "requirement_id": v["requirement"]["requirement_id"],
                "expected_revision": v["requirement"]["revision"],
                "expected_review_hash": v["requirement"]["review_hash"],
            }
            for v in values
        ]
        if invalid_target == "foreign_id":
            targets[1]["requirement_id"] = str(uuid4())
        else:
            targets[1]["expected_revision"] += 10
        response = await api.post(
            f"/v4/tasks/{task}/extractions/{job}/requirement-confirmations",
            headers=headers[0],
            json={
                "request_id": str(uuid4()),
                "expected_set_revision": values[0]["scope"]["revision"],
                "items": targets,
                "reviewed_each": True,
                "reason": "Exact bounded atomic review",
            },
        )
        assert response.status_code == (404 if invalid_target == "foreign_id" else 409), (
            response.text
        )
        for value in values:
            current = await review(api, headers[0], value["requirement"]["requirement_id"])
            assert current["requirement"]["state"] == "unconfirmed"
            assert current["requirement"]["revision"] == value["requirement"]["revision"]


@pytest.mark.parametrize("scope", ["req:confirm", "req:manual"])
async def test_new_human_scopes_cannot_be_issued_to_tokens(api, headers, scope):
    from datetime import UTC, datetime, timedelta

    response = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Forbidden human capability",
            "scopes": ["task:read", scope],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code in {400, 403, 422}, response.text
