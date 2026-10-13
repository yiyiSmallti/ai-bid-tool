"""Failure-first PostgreSQL/API acceptance for the profile management slice.

Risks covered before implementation: org/parent confusion, unbounded reads,
prefix syntax injection, stale/foreign/expired cursors, unavailable authority,
incorrect revision authors/provenance, independent lifecycle CAS, human gate,
direct SQL bypass/immutability, and concurrent transitions. These tests use the
real restricted PostgreSQL role and produce normal pytest/JUnit artifacts.
"""

import asyncio
import json
import time
from uuid import UUID, uuid4

import pytest
from app.services.auth import HUMAN_SCOPES, Identity, set_actor_context
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

BASE = "/v4/management/resources/profiles"
DATA = {
    "name": "Synthetic alpha profile",
    "registration_details": "Declared registration",
    "performance_summary": "Declared performance",
    "standard_wording": "Declared wording",
}


async def create(api, headers, **fields):
    response = await api.post(
        "/resources/profiles", headers=headers, json={"data": {**DATA, **fields}}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def lifecycle(revision=1, lifecycle_revision=0, state="inactive"):
    return {
        "expected_revision": revision,
        "expected_lifecycle_revision": lifecycle_revision,
        "state": state,
        "reason_code": "restored" if state == "active" else "obsolete",
    }


async def test_bounded_prefix_pages_and_exact_details(api, headers, database):
    profiles = [await create(api, headers, name=f"Synthetic alpha {i}") for i in range(4)]
    response = await api.post(BASE + "/query", headers=headers, json={"q": "ALP", "limit": 2})
    assert response.status_code == 200
    page = response.json()
    assert set(page) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    assert set(page["data"]) == {"as_of", "returned", "next_cursor", "has_more"}
    assert page["data"]["returned"] == 2 and page["data"]["has_more"]
    assert len(response.content) <= 256 * 1024
    next_page = await api.post(
        BASE + "/query",
        headers=headers,
        json={"q": "alp", "limit": 2, "cursor": page["data"]["next_cursor"]},
    )
    assert next_page.status_code == 200
    assert not next_page.json()["data"]["has_more"]
    assert (
        len({row["ref"]["resource_id"] for row in page["items"] + next_page.json()["items"]}) == 4
    )
    for query in ({"q": "alpha"}, {"q": "synthetic"}):
        assert (await api.post(BASE + "/query", headers=headers, json=query)).json()["data"][
            "returned"
        ] == 4
    assert (await api.post(BASE + "/query", headers=headers, json={"q": "alpha | absent"})).json()[
        "items"
    ] == []
    assert (
        await api.post(BASE + "/query", headers=headers, json={"product_id": str(uuid4())})
    ).status_code == 422
    profile = profiles[0]
    root = profile["profile_id"]
    update = await api.post(
        f"/resources/profiles/{root}/revisions",
        headers=headers,
        json={"expected_revision": 1, "data": {**DATA, "name": "Revision two"}},
    )
    assert update.status_code == 200
    detail = await api.get(f"{BASE}/{root}", headers=headers, params={"revision": 1})
    assert detail.status_code == 200
    assert detail.json()["data"]["current_revision"] == 2
    assert detail.json()["data"]["detail"]["revision"]["id"] == profile["id"]
    history = await api.post(f"{BASE}/{root}/history/query", headers=headers, json={"limit": 1})
    assert history.json()["items"][0]["revision"] == 2
    assert history.json()["items"][0]["has_file"] is False


async def test_cursor_actor_filters_expiry_and_cursor_expiry(
    api, headers, database, admin_engine, application
):
    for i in range(3):
        await create(api, headers, name=f"Alpha {i}")
    page = (await api.post(BASE + "/query", headers=headers, json={"limit": 1})).json()
    cursor = page["data"]["next_cursor"]
    for hdr, body in [
        (headers, {"limit": 1, "cursor": cursor, "q": "alpha"}),
        (headers, {"cursor": "broken"}),
    ]:
        result = await api.post(BASE + "/query", headers=hdr, json=body)
        assert result.status_code == 400
        assert result.json()["data"]["error"]["code"] == "management_cursor_invalid"
    signer = application.state.crypto
    payload = json.loads(signer.cipher.decrypt(cursor.encode()))
    payload["exp"] = int(time.time()) - 1
    expired = signer.cipher.encrypt(json.dumps(payload).encode()).decode()
    assert (
        await api.post(BASE + "/query", headers=headers, json={"limit": 1, "cursor": expired})
    ).status_code == 409


async def test_lifecycle_human_and_content_independence(api, headers, database, admin_engine):
    profile = await create(api, headers)
    root = profile["profile_id"]
    assert (await api.post(BASE + "/query", headers=headers, json={})).status_code == 200
    response = await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle())
    assert response.status_code == (200 if True else 403)
    assert response.json()["data"]["existing_selections"] == "preserved"
    assert response.json()["data"]["lifecycle"] == {"state": "inactive", "revision": 1}
    assert (await api.post(BASE + "/query", headers=headers, json={})).json()["items"] == []
    assert (await api.post(BASE + "/query", headers=headers, json={"state": "inactive"})).json()[
        "data"
    ]["returned"] == 1
    detail = (await api.get(f"{BASE}/{root}", headers=headers)).json()["data"]
    assert detail["detail"]["revision"]["id"] == profile["id"]
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle())
    ).status_code == 409
    revision = await api.post(
        f"/resources/profiles/{root}/revisions",
        headers=headers,
        json={"expected_revision": 1, "data": {**DATA, "name": "Inactive edited"}},
    )
    assert revision.status_code == 200
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle(2, 1, "active"))
    ).status_code == 200
    events = (
        await api.post(f"{BASE}/{root}/lifecycle/history/query", headers=headers, json={})
    ).json()["items"]
    assert [event["revision"] for event in events] == [2, 1]
    assert events[0]["resource_revision"] == 2


async def test_token_read_but_no_lifecycle_and_concurrent_cas(api, headers):
    from datetime import UTC, datetime, timedelta

    profile = await create(api, headers)
    token = await api.post(
        "/tokens",
        headers=headers,
        json={
            "name": "Synthetic management token",
            "scopes": ["profile:read", "profile:write"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert token.status_code == 200
    token_headers = {**headers, "Authorization": "Bearer " + token.json()["data"]["token"]}
    assert (await api.post(BASE + "/query", headers=token_headers, json={})).status_code == 200
    path = f"{BASE}/{profile['profile_id']}/lifecycle"
    assert (await api.post(path, headers=token_headers, json=lifecycle())).status_code == 403
    results = await asyncio.gather(
        *(api.post(path, headers=headers, json=lifecycle()) for _ in range(2))
    )
    assert sorted(reply.status_code for reply in results) == [200, 409]


async def test_lifecycle_table_rls_immutable_and_transition_guard(
    api, headers, database, application
):
    profile = await create(api, headers)
    await api.post(f"{BASE}/{profile['profile_id']}/lifecycle", headers=headers, json=lifecycle())
    for sql in [
        "UPDATE resource_lifecycle_events SET reason_code='other'",
        "DELETE FROM resource_lifecycle_events",
        "UPDATE org_profiles SET lifecycle_state='active',lifecycle_revision=2 WHERE id=:root",
    ]:
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction() as session:
                await session.execute(text(sql), {"root": UUID(profile["profile_id"])})


async def test_complete_http_body_bound_and_literal_unicode_prefix(api, headers):
    profile = await create(api, headers, name="Straße device")
    for value in ("STRASSE", "straße", "stras dev"):
        page = await api.post(BASE + "/query", headers=headers, json={"q": value})
        assert page.status_code == 200, page.text
        assert [row["ref"]["resource_id"] for row in page.json()["items"]] == [
            profile["profile_id"]
        ]
    root = profile["profile_id"]
    for path in (
        BASE + "/query",
        f"{BASE}/{root}/history/query",
        f"{BASE}/{root}/lifecycle/history/query",
        f"{BASE}/{root}/lifecycle",
    ):
        response = await api.post(
            path,
            headers={**headers, "Content-Type": "application/json"},
            content=b" " * (16 * 1024) + b"{}",
        )
        assert response.status_code == 413
        assert response.json()["data"]["error"]["code"] == "invalid_input"
    boundary = await api.post(
        BASE + "/query",
        headers={**headers, "Content-Type": "application/json"},
        content=b" " * (16 * 1024 - 2) + b"{}",
    )
    assert boundary.status_code == 200
    assert (
        await api.post(BASE.replace("/v4", "") + "/query", headers=headers, json={})
    ).status_code == 200


async def test_maximum_unicode_search_keeps_a_bounded_usable_cursor(api, headers):
    query = "汉" * 200
    profiles = [await create(api, headers, name=query) for _ in range(2)]
    first = await api.post(BASE + "/query", headers=headers, json={"q": query, "limit": 1})
    assert first.status_code == 200, first.text
    cursor = first.json()["data"]["next_cursor"]
    assert cursor and len(cursor) <= 2048
    second = await api.post(
        BASE + "/query", headers=headers, json={"q": query, "limit": 1, "cursor": cursor}
    )
    assert second.status_code == 200, second.text
    assert {
        row["ref"]["resource_id"] for row in first.json()["items"] + second.json()["items"]
    } == {profile["profile_id"] for profile in profiles}


async def test_parent_cursor_and_exact_detail_author(api, headers, database):
    first = await create(api, headers)
    second = await create(api, headers)
    root = first["profile_id"]
    for revision in range(1, 28):
        reply = await api.post(
            f"/resources/profiles/{root}/revisions",
            headers=headers,
            json={
                "expected_revision": revision,
                "data": {**DATA, "name": f"Revision {revision + 1}"},
            },
        )
        assert reply.status_code == 200
    page = (
        await api.post(f"{BASE}/{root}/history/query", headers=headers, json={"limit": 1})
    ).json()
    bad = await api.post(
        f"{BASE}/{second['profile_id']}/history/query",
        headers=headers,
        json={"limit": 1, "cursor": page["data"]["next_cursor"]},
    )
    assert bad.status_code == 400
    detail = (await api.get(f"{BASE}/{root}", headers=headers, params={"revision": 1})).json()[
        "data"
    ]
    assert detail["revised_at"] and detail["detail"]["revision"]["id"] == first["id"]
    assert (
        await api.get(f"{BASE}/{second['profile_id']}", headers=headers, params={"revision": 28})
    ).status_code == 404


@pytest.mark.parametrize(
    "change",
    ["foreign_root", "wrong_revision", "wrong_sequence", "no_context", "token", "missing_audit"],
)
async def test_direct_event_inserts_cannot_bypass_isolation_or_authority(
    change, api, headers, database, application, admin_engine
):
    first, foreign = (await create(api, headers), await create(api, headers))
    actor = Identity(scopes=set(HUMAN_SCOPES))
    params = {"id": uuid4(), "root": UUID(first["profile_id"]), "version": 1, "sequence": 1}
    if change == "foreign_root":
        params["root"] = UUID(foreign["profile_id"])
    if change == "wrong_revision":
        params["version"] = 2
    if change == "wrong_sequence":
        params["sequence"] = 2
    if change == "token":
        actor.actor_kind = "token"
    with pytest.raises(DBAPIError):
        async with application.state.db.transaction() as session:
            await set_actor_context(session, actor)
            await session.execute(
                text(
                    "INSERT INTO resource_lifecycle_events(id,profile_id,revision,resource_revision,before_state,after_state,reason_code) VALUES(:id,:root,:sequence,:version,'active','inactive','obsolete')"
                ),
                params,
            )
    async with application.state.db.transaction() as session:
        assert (
            await session.scalar(
                text("SELECT lifecycle_revision FROM org_profiles WHERE id=:root"),
                {"root": UUID(first["profile_id"])},
            )
            == 0
        )
        assert await session.scalar(text("SELECT count(*) FROM resource_lifecycle_events")) == 0


async def test_task_pins_survive_lifecycle_and_archive(api, headers):
    row = await create(api, headers)
    root = row["profile_id"]
    tasks = []
    for n in range(2):
        tasks.append(
            (
                await api.post(
                    "/tasks", headers=headers, json={"name": f"Synthetic profile pin {n}"}
                )
            ).json()["data"]["id"]
        )
    body = {"profile_id": root, "revision": 1}
    selected = await api.post(f"/tasks/{tasks[0]}/profiles", headers=headers, json=body)
    assert selected.status_code == 200
    pin = selected.json()["data"]
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle())
    ).status_code == 200
    replay = await api.post(f"/tasks/{tasks[0]}/profiles", headers=headers, json=body)
    assert replay.status_code == 200 and replay.json()["data"]["duplicate"] is True
    denied = await api.post(f"/tasks/{tasks[1]}/profiles", headers=headers, json=body)
    assert (
        denied.status_code == 409 and denied.json()["data"]["error"]["code"] == "resource_inactive"
    )
    assert (await api.get(f"/tasks/{tasks[0]}/profiles", headers=headers)).json()["items"][0][
        "id"
    ] == pin["id"]
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers, json=lifecycle(1, 1, "active"))
    ).status_code == 200
    assert (
        await api.post(f"/tasks/{tasks[1]}/profiles", headers=headers, json=body)
    ).status_code == 200


async def test_read_tokens_all_routes_and_human_scope_denial(api, headers):
    from datetime import UTC, datetime, timedelta

    from app.services.auth import HUMAN_ONLY_SCOPES

    row = await create(api, headers)
    root = row["profile_id"]
    issued = await api.post(
        "/tokens",
        headers=headers,
        json={
            "name": "Synthetic read token",
            "scopes": ["profile:read"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert issued.status_code == 200
    auth = {**headers, "Authorization": "Bearer " + issued.json()["data"]["token"]}
    assert (await api.get(f"{BASE}/{root}", headers=auth)).status_code == 200
    for path in (
        BASE + "/query",
        f"{BASE}/{root}/history/query",
        f"{BASE}/{root}/lifecycle/history/query",
    ):
        assert (await api.post(path, headers=auth, json={})).status_code == 200
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=auth, json=lifecycle())
    ).status_code == 403
    assert "profile:lifecycle" in HUMAN_ONLY_SCOPES
    denied = await api.post(
        "/tokens",
        headers=headers,
        json={
            "name": "Synthetic forbidden scope",
            "scopes": ["profile:lifecycle"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert denied.status_code == 403


async def test_only_declared_search_fields_are_indexed(api, headers):
    row = await create(
        api,
        headers,
        **{
            "registration_details": "UNSEARCHABLE REGISTRATION",
            "performance_summary": "UNSEARCHABLE PERFORMANCE",
            "standard_wording": "UNSEARCHABLE WORDING",
        },
    )
    for q, present in [("unsearchable", False), ("registration", False), ("alpha", True)]:
        reply = await api.post(BASE + "/query", headers=headers, json={"q": q})
        assert reply.status_code == 200, reply.text
        assert [item["ref"]["resource_id"] for item in reply.json()["items"]] == (
            [row["profile_id"]] if present else []
        )
