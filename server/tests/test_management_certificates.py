"""Failure-first PostgreSQL/API acceptance for the certificate management slice.

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

BASE = "/v4/management/resources/certificates"
DATA = {
    "kind": "qualification",
    "name": "Synthetic alpha certificate",
    "number": "CERT-AB-20",
    "valid_from": "2026-01-01",
    "valid_until": "2026-12-31",
}


async def create(api, headers, **fields):
    response = await api.post(
        "/resources/certificates", headers=headers, json={"data": {**DATA, **fields}}
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
    certificates = [await create(api, headers[0], name=f"Synthetic alpha {i}") for i in range(4)]
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
    for query in ({"q": "CERT"}, {"q": "AB"}):
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
    certificate = certificates[0]
    root = certificate["certificate_id"]
    update = await api.post(
        f"/resources/certificates/{root}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "data": {**DATA, "name": "Revision two"}},
    )
    assert update.status_code == 200
    detail = await api.get(f"{BASE}/{root}", headers=headers[0], params={"revision": 1})
    assert detail.status_code == 200
    assert detail.json()["data"]["current_revision"] == 2
    assert detail.json()["data"]["detail"]["revision"]["id"] == certificate["id"]
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
    for root in (foreign["certificate_id"], str(uuid4())):
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
    certificate = await create(api, headers[0])
    root = certificate["certificate_id"]
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
    assert detail["detail"]["revision"]["id"] == certificate["id"]
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle())
    ).status_code == 409
    revision = await api.post(
        f"/resources/certificates/{root}/revisions",
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

    certificate = await create(api, headers[0])
    token = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Synthetic management token",
            "scopes": ["certificate:read", "certificate:write"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert token.status_code == 200
    token_headers = {**headers[0], "Authorization": "Bearer " + token.json()["data"]["token"]}
    assert (await api.post(BASE + "/query", headers=token_headers, json={})).status_code == 200
    path = f"{BASE}/{certificate['certificate_id']}/lifecycle"
    assert (await api.post(path, headers=token_headers, json=lifecycle())).status_code == 403
    results = await asyncio.gather(
        *(api.post(path, headers=headers[0], json=lifecycle()) for _ in range(2))
    )
    assert sorted(reply.status_code for reply in results) == [200, 409]


async def test_exact_audit_author_unknown_if_ambiguous(api, headers, tenants, admin_engine):
    certificate = await create(api, headers[0])
    with Session(admin_engine) as session, session.begin():
        session.add(
            AuditLog(
                org_id=tenants["orgs"][0],
                actor_user_id=tenants["users"][0],
                action="resource.certificate.create",
                object_id=UUID(certificate["certificate_id"]),
                details={"new_revision_id": certificate["id"], "revision": 1},
            )
        )
    page = (await api.post(BASE + "/query", headers=headers[0], json={})).json()
    assert page["items"][0]["revised_by"] is None


async def test_lifecycle_table_rls_immutable_and_transition_guard(
    api, headers, tenants, application
):
    certificate = await create(api, headers[0])
    await api.post(
        f"{BASE}/{certificate['certificate_id']}/lifecycle", headers=headers[0], json=lifecycle()
    )
    async with application.state.db.transaction(tenants["orgs"][1]) as session:
        assert await session.scalar(text("SELECT count(*) FROM resource_lifecycle_events")) == 0
    async with application.state.db.transaction() as session:
        assert await session.scalar(text("SELECT count(*) FROM resource_lifecycle_events")) == 0
    for sql in [
        "UPDATE resource_lifecycle_events SET reason_code='other'",
        "DELETE FROM resource_lifecycle_events",
        "UPDATE certificates SET lifecycle_state='active',lifecycle_revision=2 WHERE id=:root",
    ]:
        with pytest.raises(DBAPIError):
            async with application.state.db.transaction(tenants["orgs"][0]) as session:
                await session.execute(text(sql), {"root": UUID(certificate["certificate_id"])})
    async with application.state.db.transaction(tenants["orgs"][0]) as session:
        forced = await session.scalar(
            text(
                "SELECT relforcerowsecurity FROM pg_class WHERE oid='resource_lifecycle_events'::regclass"
            )
        )
        assert forced


async def test_complete_http_body_bound_and_literal_unicode_prefix(api, headers):
    certificate = await create(api, headers[0], name="Straße device", number="AB-20 ABC/DEF")
    for value in ("STRASSE", "straße", "AB-2", "stras ab", "ABC/DEF"):
        page = await api.post(BASE + "/query", headers=headers[0], json={"q": value})
        assert page.status_code == 200, page.text
        assert [row["ref"]["resource_id"] for row in page.json()["items"]] == [
            certificate["certificate_id"]
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
    certificates = [await create(api, headers[0], name=query) for _ in range(2)]
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
    } == {certificate["certificate_id"] for certificate in certificates}


async def test_same_org_parent_cursor_and_exact_detail_author(api, headers, tenants):
    first = await create(api, headers[0])
    second = await create(api, headers[0])
    root = first["certificate_id"]
    for revision in range(1, 28):
        reply = await api.post(
            f"/resources/certificates/{root}/revisions",
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
        f"{BASE}/{second['certificate_id']}/history/query",
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
        await api.get(
            f"{BASE}/{second['certificate_id']}", headers=headers[0], params={"revision": 28}
        )
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
        "root": UUID(first["certificate_id"]),
        "actor": user,
        "version": 1,
        "sequence": 1,
    }
    if change == "foreign_org":
        params["org"] = tenants["orgs"][1]
    if change == "foreign_root":
        params["root"] = UUID(foreign["certificate_id"])
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
                    "INSERT INTO resource_lifecycle_events(id,org_id,certificate_id,revision,resource_revision,before_state,after_state,reason_code,actor_user_id) VALUES(:id,:org,:root,:sequence,:version,'active','inactive','obsolete',:actor)"
                ),
                params,
            )
    async with application.state.db.transaction(org) as session:
        assert (
            await session.scalar(
                text("SELECT lifecycle_revision FROM certificates WHERE id=:root"),
                {"root": UUID(first["certificate_id"])},
            )
            == 0
        )
        assert await session.scalar(text("SELECT count(*) FROM resource_lifecycle_events")) == 0


async def test_exact_original_revision_author_and_explicit_date_advisory(
    api, headers, tenants, pdf_bytes
):
    """File creation author is the metadata revision author; a later edit loses its file."""
    import hashlib

    from test_certificate_files import upload

    row = await create(api, headers[0])
    root = row["certificate_id"]
    uploaded = await upload(api, headers[0], row, pdf_bytes, data=DATA)
    assert uploaded.status_code == 200, uploaded.text
    original = uploaded.json()["data"]
    second = (await api.get(f"{BASE}/{root}", headers=headers[0])).json()["data"]
    assert second["detail"]["revision"]["id"] == original["certificate_revision_id"]
    assert second["detail"]["file"] == original
    assert second["revised_by"] == str(tenants["users"][0])
    assert second["date_advisory"] == {"as_of": None, "state": "unknown"}
    assert original["file"]["sha256"] == hashlib.sha256(pdf_bytes).hexdigest()
    revised = await api.post(
        f"/resources/certificates/{root}/revisions",
        headers=headers[0],
        json={"expected_revision": 2, "data": {**DATA, "name": "Metadata only"}},
    )
    assert revised.status_code == 200
    assert (await api.get(f"{BASE}/{root}", headers=headers[0])).json()["data"]["detail"][
        "file"
    ] is None
    history = (await api.post(f"{BASE}/{root}/history/query", headers=headers[0], json={})).json()
    assert [item["has_file"] for item in history["items"]] == [False, True, False]
    for date, state in (
        ("2027-01-01", "expired"),
        ("2025-12-31", "not_yet_valid"),
        ("2026-12-31", "valid"),
    ):
        detail = await api.get(
            f"{BASE}/{root}", headers=headers[0], params={"revision": 2, "as_of": date}
        )
        assert detail.status_code == 200
        assert detail.json()["data"]["date_advisory"] == {"as_of": date, "state": state}
        assert detail.json()["data"]["detail"]["file"] == original
        assert (
            "storage_key" not in detail.text
            and "url" not in detail.json()["data"]["detail"]["file"]
        )
    assert (
        await api.get(f"{BASE}/{root}", headers=headers[1], params={"revision": 2})
    ).status_code == 404


async def test_retained_composed_parts_match_exact_revision(api, headers, pdf_bytes):
    row = await create(api, headers[0])
    reply = await api.post(
        f"/resources/certificates/{row['certificate_id']}/file-revisions",
        headers=headers[0],
        data={"metadata": json.dumps({"expected_revision": 1, "data": DATA})},
        files=[("file", ("first.pdf", pdf_bytes)), ("file", ("second.pdf", pdf_bytes))],
    )
    assert reply.status_code == 200, reply.text
    detail = await api.get(f"{BASE}/{row['certificate_id']}", headers=headers[0])
    assert detail.json()["data"]["detail"]["file"] == reply.json()["data"]
    assert [p["ordinal"] for p in detail.json()["data"]["detail"]["file"]["parts"]] == [1, 2]


@pytest.mark.parametrize(
    "disabled,expected", [("user", 401), ("membership", 404), ("org", 403), ("context", 422)]
)
async def test_live_identity_and_context_for_every_route(
    disabled, expected, api, headers, tenants, admin_engine
):
    from app.models.entities import Org, User

    row = await create(api, headers[0])
    root = row["certificate_id"]
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
    root = row["certificate_id"]
    tasks = []
    for n in range(2):
        tasks.append(
            (
                await api.post(
                    "/tasks", headers=headers[0], json={"name": f"Synthetic certificate pin {n}"}
                )
            ).json()["data"]["id"]
        )
    body = {"certificate_id": root, "revision": 1}
    selected = await api.post(f"/tasks/{tasks[0]}/certificates", headers=headers[0], json=body)
    assert selected.status_code == 200
    pin = selected.json()["data"]
    assert (
        await api.post(f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle())
    ).status_code == 200
    replay = await api.post(f"/tasks/{tasks[0]}/certificates", headers=headers[0], json=body)
    assert replay.status_code == 200 and replay.json()["data"]["duplicate"] is True
    denied = await api.post(f"/tasks/{tasks[1]}/certificates", headers=headers[0], json=body)
    assert (
        denied.status_code == 409 and denied.json()["data"]["error"]["code"] == "resource_inactive"
    )
    assert (await api.get(f"/tasks/{tasks[0]}/certificates", headers=headers[0])).json()["items"][
        0
    ]["id"] == pin["id"]
    state = await workflow(api, headers[0], tasks[0])
    assert (
        await api.post(
            f"/v4/tasks/{tasks[0]}/archive",
            headers=headers[0],
            json={"expected_revision": state["revision"], "reason": "Synthetic archive"},
        )
    ).status_code == 200
    assert (
        await api.post(f"/tasks/{tasks[0]}/certificates", headers=headers[0], json=body)
    ).json()["data"]["error"]["code"] == "task_archived"
    assert (
        await api.post(
            f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle(1, 1, "active")
        )
    ).status_code == 200
    assert (
        await api.post(f"/tasks/{tasks[1]}/certificates", headers=headers[0], json=body)
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
    assert (await api.get(f"{BASE}/{row['certificate_id']}", headers=auth)).status_code == 200
    reply = await api.post(
        f"/tasks/{task}/certificates",
        headers=auth,
        json={"certificate_id": row["certificate_id"], "revision": 1},
    )
    assert reply.status_code == status, reply.text


async def test_read_tokens_all_routes_and_human_scope_denial(api, headers):
    from datetime import UTC, datetime, timedelta

    from app.services.auth import HUMAN_ONLY_SCOPES

    row = await create(api, headers[0])
    root = row["certificate_id"]
    issued = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Synthetic read token",
            "scopes": ["certificate:read"],
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
    assert "certificate:lifecycle" in HUMAN_ONLY_SCOPES
    denied = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Synthetic forbidden scope",
            "scopes": ["certificate:lifecycle"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert denied.status_code == 403


async def test_only_declared_search_fields_are_indexed(api, headers):
    row = await create(api, headers[0], **{"number": "UNIQUE DECLARED NUMBER"})
    for q, present in [("unique", True), ("declared", True)]:
        reply = await api.post(BASE + "/query", headers=headers[0], json={"q": q})
        assert reply.status_code == 200, reply.text
        assert [item["ref"]["resource_id"] for item in reply.json()["items"]] == (
            [row["certificate_id"]] if present else []
        )


async def test_deactivation_retains_pinned_original_and_unconfirmed_source_archive(
    api, headers, pdf_bytes
):
    """Lifecycle never changes retained bytes, task pins or human evidence gates."""
    import hashlib

    from test_evidence_sources import add, source_fixture
    from test_team_workflow_membership import workflow

    row, task, scan, pin = await source_fixture(api, headers[0], pdf_bytes)
    root = row["certificate_id"]
    archived = await add(api, headers[0], task, pin["id"])
    assert archived.status_code == 200, archived.text
    source = archived.json()["data"]["source"]
    preview_path = f"/evidence-sources/{source['id']}/preview/download-link"
    preview_link = (await api.get(preview_path, headers=headers[0])).json()["data"]
    preview = await api.get(preview_link["url"], headers=headers[0])
    assert preview.status_code == 200
    assert hashlib.sha256(preview.content).hexdigest() == source["preview"]["sha256"]

    stopped = await api.post(f"{BASE}/{root}/lifecycle", headers=headers[0], json=lifecycle(2))
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["data"]["existing_selections"] == "preserved"
    detail = (await api.get(f"{BASE}/{root}", headers=headers[0], params={"revision": 2})).json()[
        "data"
    ]
    assert detail["lifecycle"] == {"state": "inactive", "revision": 1}
    assert detail["detail"]["file"] == scan
    pins = (await api.get(f"/tasks/{task}/certificates", headers=headers[0])).json()["items"]
    assert (
        pins[0]["id"] == pin["id"]
        and pins[0]["certificate_revision_id"] == scan["certificate_revision_id"]
    )
    listing = (await api.get(f"/tasks/{task}/evidence-sources", headers=headers[0])).json()["items"]
    assert listing == [source]
    assert source["confirmed_by"] is None and source["eligible_for_draft_export"] is False

    state = await workflow(api, headers[0], task)
    response = await api.post(
        f"/tasks/{task}/archive",
        headers=headers[0],
        json={"expected_revision": state["revision"], "reason": "Synthetic archived original"},
    )
    assert response.status_code == 200, response.text
    revision = scan["certificate_revision_id"]
    original_path = f"/resources/certificates/revisions/{revision}/file/download-link"
    original_link = await api.get(original_path, headers=headers[0])
    assert original_link.status_code == 200
    assert original_link.json()["data"]["expires_in"] == 300
    downloaded = await api.get(original_link.json()["data"]["url"], headers=headers[0])
    assert downloaded.status_code == 200 and downloaded.content == pdf_bytes
    assert hashlib.sha256(downloaded.content).hexdigest() == scan["file"]["sha256"]
    assert (await api.get(preview_link["url"], headers=headers[0])).content == preview.content
    assert (await api.get(f"/tasks/{task}/evidence-sources", headers=headers[0])).json()[
        "items"
    ] == [source]
    denied = await add(api, headers[0], task, pin["id"], page=2)
    assert denied.status_code == 409 and denied.json()["data"]["error"]["code"] == "task_archived"
    for path in (
        original_path,
        preview_path,
        original_link.json()["data"]["url"],
        preview_link["url"],
    ):
        assert (await api.get(path, headers=headers[1])).status_code == 404
    assert (
        await api.get(
            original_link.json()["data"]["url"], headers={"X-Org-Id": headers[0]["X-Org-Id"]}
        )
    ).status_code == 401
