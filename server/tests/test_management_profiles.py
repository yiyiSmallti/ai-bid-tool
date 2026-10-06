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
from app.models.entities import AuditLog, Membership
from app.services.auth import ROLE_SCOPES, Identity, set_actor_context
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

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


async def test_bounded_prefix_pages_and_exact_details(api, headers, tenants):
    profiles = [await create(api, headers[0], name=f"Synthetic alpha {i}") for i in range(4)]
    await create(api, headers[1])
    response = await api.post(BASE + "/query", headers=headers[0], json={"q": "ALP", "limit": 2})
    assert response.status_code == 200
    page = response.json()
    assert set(page) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    assert set(page["data"]) == {"org_id", "as_of", "returned", "next_cursor", "has_more"}
    assert page["data"]["returned"] == 2 and page["data"]["has_more"]
    assert len(response.content) <= 256 * 1024
    assert all(row["org_id"] == str(tenants["orgs"][0]) for row in page["items"])
    next_page = await api.post(
        BASE + "/query",
        headers=headers[0],
        json={"q": "alp", "limit": 2, "cursor": page["data"]["next_cursor"]},
    )
    assert next_page.status_code == 200
    assert not next_page.json()["data"]["has_more"]
    assert (
        len({row["ref"]["resource_id"] for row in page["items"] + next_page.json()["items"]}) == 4
    )
    for query in ({"q": "alpha"}, {"q": "synthetic"}):
        assert (await api.post(BASE + "/query", headers=headers[0], json=query)).json()["data"][
            "returned"
        ] == 4
    # Search syntax is not a tsquery program: punctuation cannot select everything.
    assert (
        await api.post(BASE + "/query", headers=headers[0], json={"q": "alpha | absent"})
    ).json()["items"] == []
    assert (
        await api.post(BASE + "/query", headers=headers[0], json={"product_id": str(uuid4())})
    ).status_code == 422
    profile = profiles[0]
    root = profile["profile_id"]
    update = await api.post(
        f"/resources/profiles/{root}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "data": {**DATA, "name": "Revision two"}},
    )
    assert update.status_code == 200
    detail = await api.get(f"{BASE}/{root}", headers=headers[0], params={"revision": 1})
    assert detail.status_code == 200
    assert detail.json()["data"]["current_revision"] == 2
    assert detail.json()["data"]["detail"]["revision"]["id"] == profile["id"]
    history = await api.post(f"{BASE}/{root}/history/query", headers=headers[0], json={"limit": 1})
    assert history.json()["items"][0]["revision"] == 2
    assert history.json()["items"][0]["created_by"] == str(tenants["users"][0])
    assert history.json()["items"][0]["has_file"] is False


@pytest.mark.parametrize(
    "suffix,body",
    [
        ("", {}),
        ("history/query", {}),
        ("lifecycle/history/query", {}),
        ("lifecycle", lifecycle()),
    ],
)
async def test_every_object_route_masks_foreign_and_missing(suffix, body, api, headers):
    foreign = await create(api, headers[1])
    replies = []
    for root in (foreign["profile_id"], str(uuid4())):
        replies.append(
            await api.post(f"{BASE}/{root}/{suffix}", headers=headers[0], json=body)
            if suffix
            else await api.get(f"{BASE}/{root}", headers=headers[0])
        )
    assert [reply.status_code for reply in replies] == [404, 404]
    assert replies[0].json()["data"]["error"] == replies[1].json()["data"]["error"]


async def test_cursor_actor_filters_expiry_and_live_membership(
    api, headers, tenants, admin_engine, application
):
    for i in range(3):
        await create(api, headers[0], name=f"Alpha {i}")
    page = (await api.post(BASE + "/query", headers=headers[0], json={"limit": 1})).json()
    cursor = page["data"]["next_cursor"]
    for hdr, body in [
        (headers[1], {"limit": 1, "cursor": cursor}),
        (headers[0], {"limit": 1, "cursor": cursor, "q": "alpha"}),
        (headers[0], {"cursor": "broken"}),
    ]:
        result = await api.post(BASE + "/query", headers=hdr, json=body)
        assert result.status_code == 400
        assert result.json()["data"]["error"]["code"] == "management_cursor_invalid"
    signer = application.state.crypto
    payload = json.loads(signer.cipher.decrypt(cursor.encode()))
    payload["exp"] = int(time.time()) - 1
    expired = signer.cipher.encrypt(json.dumps(payload).encode()).decode()
    assert (
        await api.post(BASE + "/query", headers=headers[0], json={"limit": 1, "cursor": expired})
    ).status_code == 409
    with Session(admin_engine) as session, session.begin():
        session.scalar(
            select(Membership).where(Membership.org_id == tenants["orgs"][0])
        ).role = "viewer"
    assert (
        await api.post(BASE + "/query", headers=headers[0], json={"limit": 1, "cursor": cursor})
    ).status_code == 400


@pytest.mark.parametrize(
    "role,allowed", [("admin", True), ("technical", False), ("bidder", True), ("viewer", False)]
)
async def test_lifecycle_roles_and_content_independence(
    role, allowed, api, headers, tenants, admin_engine
):
    profile = await create(api, headers[0])
    root = profile["profile_id"]
    with Session(admin_engine) as session, session.begin():
        session.scalar(
            select(Membership).where(Membership.org_id == tenants["orgs"][0])
        ).role = role
    assert (await api.post(BASE + "/query", headers=headers[0], json={})).status_code == 200
    response = await api.post(f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle())
    assert response.status_code == (200 if allowed else 403)
    if not allowed:
        return
    assert response.json()["data"]["existing_selections"] == "preserved"
    assert response.json()["data"]["lifecycle"] == {"state": "inactive", "revision": 1}
    assert (await api.post(BASE + "/query", headers=headers[0], json={})).json()["items"] == []
    assert (await api.post(BASE + "/query", headers=headers[0], json={"state": "inactive"})).json()[
        "data"
    ]["returned"] == 1
    detail = (await api.get(f"{BASE}/{root}", headers=headers[0])).json()["data"]
    assert detail["detail"]["revision"]["id"] == profile["id"]
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle())
    ).status_code == 409
    revision = await api.post(
        f"/resources/profiles/{root}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "data": {**DATA, "name": "Inactive edited"}},
    )
    assert revision.status_code == 200
    assert (
        await api.post(
            f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle(2, 1, "active")
        )
    ).status_code == 200
    events = (
        await api.post(f"{BASE}/{root}/lifecycle/history/query", headers=headers[0], json={})
    ).json()["items"]
    assert [event["revision"] for event in events] == [2, 1]
    assert events[0]["resource_revision"] == 2


async def test_token_read_but_no_lifecycle_and_concurrent_cas(api, headers):
    from datetime import UTC, datetime, timedelta

    profile = await create(api, headers[0])
    token = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Synthetic management token",
            "scopes": ["profile:read", "profile:write"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert token.status_code == 200
    token_headers = {**headers[0], "Authorization": "Bearer " + token.json()["data"]["token"]}
    assert (await api.post(BASE + "/query", headers=token_headers, json={})).status_code == 200
    path = f"{BASE}/{profile['profile_id']}/lifecycle"
    assert (await api.post(path, headers=token_headers, json=lifecycle())).status_code == 403
    results = await asyncio.gather(
        *(api.post(path, headers=headers[0], json=lifecycle()) for _ in range(2))
    )
    assert sorted(reply.status_code for reply in results) == [200, 409]


async def test_exact_audit_author_unknown_if_ambiguous(api, headers, tenants, admin_engine):
    profile = await create(api, headers[0])
    with Session(admin_engine) as session, session.begin():
        session.add(
            AuditLog(
                org_id=tenants["orgs"][0],
                actor_user_id=tenants["users"][0],
                action="resource.profile.create",
                object_id=UUID(profile["profile_id"]),
                details={"new_revision_id": profile["id"], "revision": 1},
            )
        )
    page = (await api.post(BASE + "/query", headers=headers[0], json={})).json()
    assert page["items"][0]["revised_by"] is None


async def test_lifecycle_table_rls_immutable_and_transition_guard(
    api, headers, tenants, application
):
    profile = await create(api, headers[0])
    await api.post(
        f"{BASE}/{profile['profile_id']}/lifecycle", headers=headers[0], json=lifecycle()
    )
    async with application.state.db.transaction(tenants["orgs"][1]) as session:
        assert await session.scalar(text("SELECT count(*) FROM resource_lifecycle_events")) == 0
    async with application.state.db.transaction() as session:
        assert await session.scalar(text("SELECT count(*) FROM resource_lifecycle_events")) == 0
    for sql in [
        "UPDATE resource_lifecycle_events SET reason_code='other'",
        "DELETE FROM resource_lifecycle_events",
        "UPDATE org_profiles SET lifecycle_state='active',lifecycle_revision=2 WHERE id=:root",
    ]:
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction(tenants["orgs"][0]) as session:
                await session.execute(text(sql), {"root": UUID(profile["profile_id"])})
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        forced = await session.scalar(
            text(
                "SELECT relforcerowsecurity FROM pg_class WHERE oid='resource_lifecycle_events'::regclass"
            )
        )
        assert forced


async def test_complete_http_body_bound_and_literal_unicode_prefix(api, headers):
    profile = await create(api, headers[0], name="Straße device")
    for value in ("STRASSE", "straße", "stras dev"):
        page = await api.post(BASE + "/query", headers=headers[0], json={"q": value})
        assert page.status_code == 200, page.text
        assert [row["ref"]["resource_id"] for row in page.json()["items"]] == [
            profile["profile_id"]
        ]
    response = await api.post(
        BASE + "/query",
        headers={**headers[0], "Content-Type": "application/json"},
        content=b" " * (16 * 1024) + b"{}",
    )
    assert response.status_code == 413
    assert response.json()["data"]["error"]["code"] == "invalid_input"
    assert (
        await api.post(BASE.replace("/v4", "") + "/query", headers=headers[0], json={})
    ).status_code == 404


async def test_maximum_unicode_search_keeps_a_bounded_usable_cursor(api, headers):
    # JSON Unicode escaping must not make a valid 200-character filter produce
    # a cursor beyond the 2048-character contract limit.
    query = "汉" * 200
    profiles = [await create(api, headers[0], name=query) for _ in range(2)]
    first = await api.post(BASE + "/query", headers=headers[0], json={"q": query, "limit": 1})
    assert first.status_code == 200, first.text
    cursor = first.json()["data"]["next_cursor"]
    assert cursor and len(cursor) <= 2048
    second = await api.post(
        BASE + "/query", headers=headers[0], json={"q": query, "limit": 1, "cursor": cursor}
    )
    assert second.status_code == 200, second.text
    assert {
        row["ref"]["resource_id"] for row in first.json()["items"] + second.json()["items"]
    } == {profile["profile_id"] for profile in profiles}


async def test_same_org_parent_cursor_and_exact_detail_author(api, headers, tenants):
    first = await create(api, headers[0])
    second = await create(api, headers[0])
    root = first["profile_id"]
    for revision in range(1, 28):
        reply = await api.post(
            f"/resources/profiles/{root}/revisions",
            headers=headers[0],
            json={
                "expected_revision": revision,
                "data": {**DATA, "name": f"Revision {revision + 1}"},
            },
        )
        assert reply.status_code == 200
    page = (
        await api.post(f"{BASE}/{root}/history/query", headers=headers[0], json={"limit": 1})
    ).json()
    bad = await api.post(
        f"{BASE}/{second['profile_id']}/history/query",
        headers=headers[0],
        json={"limit": 1, "cursor": page["data"]["next_cursor"]},
    )
    assert bad.status_code == 400
    detail = (await api.get(f"{BASE}/{root}", headers=headers[0], params={"revision": 1})).json()[
        "data"
    ]
    assert detail["revised_by"] == str(tenants["users"][0])
    assert detail["revised_at"] and detail["detail"]["revision"]["id"] == first["id"]
    assert (
        await api.get(f"{BASE}/{second['profile_id']}", headers=headers[0], params={"revision": 28})
    ).status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        "foreign_org",
        "foreign_root",
        "foreign_actor",
        "wrong_revision",
        "wrong_sequence",
        "no_context",
        "token",
        "role",
        "missing_audit",
    ],
)
async def test_direct_event_inserts_cannot_bypass_isolation_or_authority(
    change, api, headers, tenants, application, admin_engine
):
    first, foreign = await create(api, headers[0]), await create(api, headers[1])
    org, user = tenants["orgs"][0], tenants["users"][0]
    actor = Identity(user, org, set(ROLE_SCOPES["admin"]), "admin")
    params = {
        "id": uuid4(),
        "org": org,
        "root": UUID(first["profile_id"]),
        "actor": user,
        "version": 1,
        "sequence": 1,
    }
    if change == "foreign_org":
        params["org"] = tenants["orgs"][1]
    if change == "foreign_root":
        params["root"] = UUID(foreign["profile_id"])
    if change == "foreign_actor":
        params["actor"] = tenants["users"][1]
    if change == "wrong_revision":
        params["version"] = 2
    if change == "wrong_sequence":
        params["sequence"] = 2
    if change == "token":
        actor.actor_kind = "token"
    if change == "role":
        with Session(admin_engine) as session, session.begin():
            session.scalar(select(Membership).where(Membership.org_id == org)).role = "viewer"
    with pytest.raises(DBAPIError):
        async with application.state.db.transaction(
            None if change == "no_context" else org
        ) as session:
            await set_actor_context(session, actor)
            await session.execute(
                text(
                    "INSERT INTO resource_lifecycle_events(id,org_id,profile_id,revision,resource_revision,before_state,after_state,reason_code,actor_user_id) VALUES(:id,:org,:root,:sequence,:version,'active','inactive','obsolete',:actor)"
                ),
                params,
            )
    async with application.state.db.transaction(org) as session:
        assert (
            await session.scalar(
                text("SELECT lifecycle_revision FROM org_profiles WHERE id=:root"),
                {"root": UUID(first["profile_id"])},
            )
            == 0
        )
        assert await session.scalar(text("SELECT count(*) FROM resource_lifecycle_events")) == 0


@pytest.mark.parametrize(
    "disabled,expected", [("user", 401), ("membership", 404), ("org", 403), ("context", 422)]
)
async def test_live_identity_and_context_for_every_route(
    disabled, expected, api, headers, tenants, admin_engine
):
    from app.models.entities import Org, User

    row = await create(api, headers[0])
    root = row["profile_id"]
    auth = dict(headers[0])
    if disabled == "context":
        auth.pop("X-Org-Id")
    else:
        with Session(admin_engine) as session, session.begin():
            if disabled == "user":
                session.get(User, tenants["users"][0]).active = False
            elif disabled == "membership":
                session.scalar(
                    select(Membership).where(Membership.org_id == tenants["orgs"][0])
                ).active = False
            else:
                session.get(Org, tenants["orgs"][0]).active = False
    assert (await api.get(f"{BASE}/{root}", headers=auth)).status_code == expected
    for path, body in (
        ("/query", {}),
        (f"/{root}/history/query", {}),
        (f"/{root}/lifecycle/history/query", {}),
        (f"/{root}/lifecycle", lifecycle()),
    ):
        assert (await api.post(BASE + path, headers=auth, json=body)).status_code == expected


async def test_task_pins_survive_lifecycle_and_archive(api, headers):
    from test_team_workflow_membership import workflow

    row = await create(api, headers[0])
    root = row["profile_id"]
    tasks = []
    for n in range(2):
        tasks.append(
            (
                await api.post(
                    "/tasks", headers=headers[0], json={"name": f"Synthetic profile pin {n}"}
                )
            ).json()["data"]["id"]
        )
    body = {"profile_id": root, "revision": 1}
    selected = await api.post(f"/tasks/{tasks[0]}/profiles", headers=headers[0], json=body)
    assert selected.status_code == 200
    pin = selected.json()["data"]
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle())
    ).status_code == 200
    replay = await api.post(f"/tasks/{tasks[0]}/profiles", headers=headers[0], json=body)
    assert replay.status_code == 200 and replay.json()["data"]["duplicate"] is True
    denied = await api.post(f"/tasks/{tasks[1]}/profiles", headers=headers[0], json=body)
    assert (
        denied.status_code == 409 and denied.json()["data"]["error"]["code"] == "resource_inactive"
    )
    assert (await api.get(f"/tasks/{tasks[0]}/profiles", headers=headers[0])).json()["items"][0][
        "id"
    ] == pin["id"]
    state = await workflow(api, headers[0], tasks[0])
    assert (
        await api.post(
            f"/v4/tasks/{tasks[0]}/archive",
            headers=headers[0],
            json={"expected_revision": state["revision"], "reason": "Synthetic archive"},
        )
    ).status_code == 200
    assert (await api.post(f"/tasks/{tasks[0]}/profiles", headers=headers[0], json=body)).json()[
        "data"
    ]["error"]["code"] == "task_archived"
    assert (
        await api.post(
            f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle(1, 1, "active")
        )
    ).status_code == 200
    assert (
        await api.post(f"/tasks/{tasks[1]}/profiles", headers=headers[0], json=body)
    ).status_code == 200


@pytest.mark.parametrize(
    "org_role,task_role,domains,status",
    [
        ("bidder", "contributor", ["commercial"], 200),
        ("technical", "contributor", ["technical"], 200),
        ("bidder", "reviewer", ["commercial"], 403),
        ("bidder", "observer", [], 403),
        ("admin", None, [], 403),
        ("bidder", None, [], 404),
        ("bidder", "removed", ["commercial"], 404),
    ],
)
async def test_library_read_never_grants_task_selection(
    org_role, task_role, domains, status, api, headers, tenants, admin_engine
):
    from test_team_workflow_membership import add_member, person

    row = await create(api, headers[0])
    task = (
        await api.post("/tasks", headers=headers[0], json={"name": "Synthetic authority task"})
    ).json()["data"]["id"]
    user, auth = await person(api, admin_engine, tenants["orgs"][0], org_role)
    if task_role:
        added = await add_member(
            api,
            headers[0],
            task,
            user,
            1,
            "contributor" if task_role == "removed" else task_role,
            domains,
        )
        assert added.status_code == 200, added.text
        if task_role == "removed":
            removed = await api.request(
                "DELETE",
                f"/tasks/{task}/members/{user}",
                headers=headers[0],
                json={"expected_revision": 2, "reason": "Synthetic removed member"},
            )
            assert removed.status_code == 200, removed.text
    assert (await api.get(f"{BASE}/{row['profile_id']}", headers=auth)).status_code == 200
    reply = await api.post(
        f"/tasks/{task}/profiles",
        headers=auth,
        json={"profile_id": row["profile_id"], "revision": 1},
    )
    assert reply.status_code == status, reply.text


async def test_read_tokens_all_routes_and_human_scope_denial(api, headers):
    from datetime import UTC, datetime, timedelta

    from app.services.auth import HUMAN_ONLY_SCOPES

    row = await create(api, headers[0])
    root = row["profile_id"]
    issued = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Synthetic read token",
            "scopes": ["profile:read"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert issued.status_code == 200
    auth = {**headers[0], "Authorization": "Bearer " + issued.json()["data"]["token"]}
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
        headers=headers[0],
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
        headers[0],
        **{
            "registration_details": "UNSEARCHABLE REGISTRATION",
            "performance_summary": "UNSEARCHABLE PERFORMANCE",
            "standard_wording": "UNSEARCHABLE WORDING",
        },
    )
    for q, present in [("unsearchable", False), ("registration", False), ("alpha", True)]:
        reply = await api.post(BASE + "/query", headers=headers[0], json={"q": q})
        assert reply.status_code == 200, reply.text
        assert [item["ref"]["resource_id"] for item in reply.json()["items"]] == (
            [row["profile_id"]] if present else []
        )
