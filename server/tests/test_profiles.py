import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.models.entities import AuditLog, Membership, OrgProfileRevision, TaskOrgProfile
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def metadata(**patch):
    return {
        "name": "Synthetic organization declaration",
        "registration_details": None,
        "performance_summary": "Synthetic summary A",
        "standard_wording": None,
        **patch,
    }


async def profile(api, header):
    response = await api.post("/resources/profiles", headers=header, json={"data": metadata()})
    assert response.status_code == 200 and "declaration" in response.json()["warnings"][0]
    return response.json()["data"]


async def task(api, header):
    return (await api.post("/tasks", headers=header, json={"name": "Synthetic task"})).json()[
        "data"
    ]["id"]


async def test_profile_versions_fixed_snapshots_history_and_audit(api, headers, application):
    row, task_id = await profile(api, headers[0]), await task(api, headers[0])
    path = f"/tasks/{task_id}/profiles"
    body = {"profile_id": row["profile_id"], "lot": "synthetic"}
    first = (await api.post(path, headers=headers[0], json=body)).json()["data"]
    assert row["data"]["registration_details"] is None and row["data"]["standard_wording"] is None
    updated = await api.post(
        f"/resources/profiles/{row['profile_id']}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "data": metadata(performance_summary="Synthetic summary B")},
    )
    assert updated.status_code == 200 and updated.json()["data"]["revision"] == 2
    fixed = (await api.get(path, headers=headers[0])).json()["items"][0]
    assert fixed["revision"] == 1 and fixed["data"] == row["data"]
    replacement = (await api.post(path, headers=headers[0], json=body)).json()["data"]
    assert replacement["revision"] == 2 and replacement["replaced_snapshot_id"] == first["id"]
    repeat = (await api.post(path, headers=headers[0], json=body)).json()["data"]
    assert repeat["duplicate"] and repeat["id"] == replacement["id"]
    history = (await api.get(path, headers=headers[0], params={"history": "true"})).json()
    assert [r["revision"] for r in history["items"]] == [1, 2]
    assert history["data"]["active_snapshot_ids"] == [replacement["id"]]
    versions = (
        await api.get(
            "/resources/profiles",
            headers=headers[0],
            params={"profile_id": row["profile_id"], "history": "true"},
        )
    ).json()
    assert [r["revision"] for r in versions["items"]] == [1, 2]
    assert versions["data"]["current_revisions"] == {row["profile_id"]: 2}
    explicit = await api.post(
        path, headers=headers[0], json={**body, "revision": 1, "lot": "second"}
    )
    assert explicit.status_code == 200 and explicit.json()["data"]["revision"] == 1
    async with application.state.db.transaction(UUID(headers[0]["X-Org-Id"])) as session:
        events = (
            await session.scalars(
                select(AuditLog)
                .where(AuditLog.action.like("%profile%"))
                .order_by(AuditLog.created_at)
            )
        ).all()
        assert len(events) == 5 and events[-2].details["old_revision_id"] == row["id"]
        assert all("Synthetic summary" not in str(e.details) for e in events)
    assert application.state.queue.calls == []


async def test_profile_endpoints_hide_foreign_data_and_reject_context_forgery(api, headers):
    own, foreign = await profile(api, headers[0]), await profile(api, headers[1])
    own_task, foreign_task = await task(api, headers[0]), await task(api, headers[1])
    routes = [
        ("POST", "/resources/profiles", {"data": metadata()}),
        (
            "POST",
            f"/resources/profiles/{own['profile_id']}/revisions",
            {"expected_revision": 1, "data": metadata()},
        ),
        ("GET", "/resources/profiles", None),
        ("POST", f"/tasks/{own_task}/profiles", {"profile_id": own["profile_id"]}),
        ("GET", f"/tasks/{own_task}/profiles", None),
    ]
    forged = {**headers[0], "X-Org-Id": headers[1]["X-Org-Id"]}
    for method, path, body in routes:
        assert (await api.request(method, path, headers=forged, json=body)).status_code == 404
        assert (
            await api.request(method, path, headers={"X-Org-Id": headers[0]["X-Org-Id"]}, json=body)
        ).status_code == 401
    for method, path, body in [
        (
            "POST",
            f"/resources/profiles/{foreign['profile_id']}/revisions",
            {"expected_revision": 1, "data": metadata()},
        ),
        ("GET", f"/resources/profiles?profile_id={foreign['profile_id']}", None),
        ("POST", f"/tasks/{own_task}/profiles", {"profile_id": foreign["profile_id"]}),
        ("POST", f"/tasks/{foreign_task}/profiles", {"profile_id": own["profile_id"]}),
        ("GET", f"/tasks/{foreign_task}/profiles", None),
        (
            "POST",
            f"/tasks/{own_task}/profiles",
            {"profile_id": own["profile_id"], "revision": 999},
        ),
    ]:
        response = await api.request(method, path, headers=headers[0], json=body)
        assert (
            response.status_code == 404 and response.json()["data"]["error"]["code"] == "not_found"
        )
    assert [
        item["profile_id"]
        for item in (await api.get("/resources/profiles", headers=headers[0])).json()["items"]
    ] == [own["profile_id"]]
    assert (
        await api.post(
            "/resources/profiles",
            headers=headers[0],
            json={"org_id": foreign["org_id"], "data": metadata()},
        )
    ).status_code == 422


async def test_profile_concurrent_updates_and_duplicate_selection(api, headers, application):
    row = await profile(api, headers[0])
    updates = await asyncio.gather(
        *(
            api.post(
                f"/resources/profiles/{row['profile_id']}/revisions",
                headers=headers[0],
                json={"expected_revision": 1, "data": metadata(performance_summary=number)},
            )
            for number in ("SYNTHETIC-A", "SYNTHETIC-B")
        )
    )
    assert sorted(response.status_code for response in updates) == [200, 409]
    task_id = await task(api, headers[0])
    responses = await asyncio.gather(
        *(
            api.post(
                f"/tasks/{task_id}/profiles",
                headers=headers[0],
                json={"profile_id": row["profile_id"]},
            )
            for _ in range(4)
        )
    )
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["data"]["id"] for response in responses}) == 1
    assert sum(not response.json()["data"]["duplicate"] for response in responses) == 1
    async with application.state.db.transaction(UUID(headers[0]["X-Org-Id"])) as session:
        assert await session.scalar(select(func.count()).select_from(OrgProfileRevision)) == 2
        assert await session.scalar(select(func.count()).select_from(TaskOrgProfile)) == 1


@pytest.mark.parametrize(
    "role,write,select_allowed",
    [
        ("admin", True, True),
        ("bidder", True, True),
        ("technical", False, True),
        ("viewer", False, False),
    ],
)
async def test_profile_roles_preserve_old_grants(
    role, write, select_allowed, api, headers, tenants, admin_engine
):
    from app.services.auth import HUMAN_ONLY_SCOPES, ROLE_SCOPES, SCOPES

    attachment_human = {
        "attachment:write",
        "attachment:review",
        "attachment:manage",
        "attachment:original:read",
        "attachment:privacy",
        "task:attachment",
    }
    assert ROLE_SCOPES[role] & attachment_human == (
        attachment_human if role in {"admin", "bidder"} else set()
    )
    assert {"attachment:read", "attachment:page:read"} <= ROLE_SCOPES[role]
    assert attachment_human | {"attachment:page:read"} <= HUMAN_ONLY_SCOPES
    assert (attachment_human | {"attachment:page:read"}).isdisjoint(SCOPES)
    assert "attachment:read" in SCOPES

    # U01 permits every human org role to open originals, never API tokens.
    assert "template:file:read" in ROLE_SCOPES[role]
    assert "template:file:read" in HUMAN_ONLY_SCOPES
    assert "template:file:read" not in SCOPES

    human_workflow_scopes = {"req:confirm", "req:manual", "evidence:annotate"}
    assert ROLE_SCOPES[role] & human_workflow_scopes == (
        human_workflow_scopes if role in {"admin", "bidder", "technical"} else set()
    )
    assert human_workflow_scopes <= HUMAN_ONLY_SCOPES
    assert human_workflow_scopes.isdisjoint(SCOPES)

    lifecycle_scopes = {"certificate:lifecycle", "profile:lifecycle"}
    assert ROLE_SCOPES[role] & lifecycle_scopes == (
        lifecycle_scopes if role in {"admin", "bidder"} else set()
    )
    assert lifecycle_scopes <= HUMAN_ONLY_SCOPES
    assert lifecycle_scopes.isdisjoint(SCOPES)

    old_expected = {
        "admin": {
            "task:read",
            "task:create",
            "tender:upload",
            "tender:parse",
            "req:extract",
            "job:read",
            "job:cancel",
            "resource:read",
            "resource:write",
            "task:resource",
            "token:create",
        },
        "bidder": {
            "task:read",
            "task:create",
            "tender:upload",
            "tender:parse",
            "req:extract",
            "job:read",
            "job:cancel",
            "resource:read",
            "task:resource",
        },
        "technical": {
            "task:read",
            "tender:upload",
            "tender:parse",
            "req:extract",
            "job:read",
            "job:cancel",
            "resource:read",
            "resource:write",
            "task:resource",
        },
        "viewer": {"task:read", "job:read", "resource:read"},
    }
    old_expected[role] |= {"certificate:read"}
    if role in ("admin", "bidder"):
        old_expected[role] |= {"certificate:write"}
    if role != "viewer":
        old_expected[role] |= {"task:certificate"}
    assert {
        scope
        for scope in ROLE_SCOPES[role]
        if scope
        not in {
            "attachment:read",
            "attachment:write",
            "attachment:review",
            "attachment:manage",
            "attachment:original:read",
            "attachment:page:read",
            "attachment:privacy",
            "task:attachment",
            "certificate:lifecycle",
            "profile:lifecycle",
            "profile:read",
            "profile:write",
            "task:profile",
            "certificate:file:read",
            "certificate:file:write",
            "evidence:source:read",
            "evidence:source:write",
            "evidence:annotate",
            "template:read",
            "template:file:read",
            "template:write",
            "task:template",
            "billing:read",
            "billing:redeem",
            "provider:read",
            "provider:write",
            "card:read",
            "card:write",
            "card:generate",
            "draft:run",
            "draft:read",
            "evidence:confirm",
            "export",
            "sandbox:read",
            "sandbox:render",
            "sandbox:capture",
            "screenshot:read",
            "screenshot:write",
            "screenshot:ingest",
            "confidential:read",
            "confidential:write",
            "confidential:reveal",
            "check:read",
            "check:run",
            "check:decide",
            "score:read",
            "score:run",
            "score:rubric:generate",
            "score:rubric:review",
            "agent:read",
            "agent:run",
            "agent:cancel",
            "task:members:write",
            "task:archive",
            "card:assign",
            "card:comment",
            "task:review-policy",
            "card:cosign",
            "task:budget:write",
            "billing:alert:write",
            "memory:read",
            "memory:write",
            "memory:retrieve",
            "memory:candidate:run",
            "memory:approve",
            "memory:manage",
            "memory:eval:read",
            "memory:eval:review",
            "req:confirm",
            "req:manual",
        }
    } == old_expected[role]
    row, task_id = await profile(api, headers[0]), await task(api, headers[0])
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(select(Membership).where(Membership.org_id == tenants["orgs"][0]))
        member.role = role
    assert (await api.get("/resources/profiles", headers=headers[0])).status_code == 200
    assert (await api.get(f"/tasks/{task_id}/profiles", headers=headers[0])).status_code == 200
    for path, body in [
        ("/resources/profiles", {"data": metadata()}),
        (
            f"/resources/profiles/{row['profile_id']}/revisions",
            {"expected_revision": 1, "data": metadata()},
        ),
    ]:
        assert (await api.post(path, headers=headers[0], json=body)).status_code == (
            200 if write else 403
        )
    assert (
        await api.post(
            f"/tasks/{task_id}/profiles",
            headers=headers[0],
            json={"profile_id": row["profile_id"]},
        )
    ).status_code == (200 if select_allowed else 403)


@pytest.mark.parametrize(
    "patch",
    [
        {"name": None},
        {"name": "  "},
        {"registration_details": " "},
        {"performance_summary": " "},
        {"standard_wording": " "},
        {"contract_file": "fake"},
        {"registration_details": 12},
        {"standard_wording": "x" * 20001},
    ],
)
async def test_profile_invalid_inputs_never_write(patch, api, headers):
    assert (
        await api.post("/resources/profiles", headers=headers[0], json={"data": metadata(**patch)})
    ).status_code == 422
    assert (await api.get("/resources/profiles", headers=headers[0])).json()["items"] == []


async def test_old_tokens_denied_new_scopes_and_membership_intersection(
    api, headers, application, tenants, admin_engine
):
    expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    async def token(scopes):
        response = await api.post(
            "/tokens",
            headers=headers[0],
            json={"name": "Synthetic only", "scopes": scopes, "expires_at": expiry},
        )
        assert response.status_code == 200
        row = response.json()["data"]
        return {**headers[0], "Authorization": "Bearer " + row["token"]}, row["id"]

    row, task_id = await profile(api, headers[0]), await task(api, headers[0])
    old, _ = await token(
        [
            "task:read",
            "resource:read",
            "resource:write",
            "task:resource",
            "certificate:read",
            "certificate:write",
            "task:certificate",
        ]
    )
    for method, path, body in [
        ("GET", "/resources/profiles", None),
        ("GET", f"/tasks/{task_id}/profiles", None),
        ("POST", "/resources/profiles", {"data": metadata()}),
        (
            "POST",
            f"/resources/profiles/{row['profile_id']}/revisions",
            {"expected_revision": 1, "data": metadata()},
        ),
        ("POST", f"/tasks/{task_id}/profiles", {"profile_id": row["profile_id"]}),
    ]:
        assert (await api.request(method, path, headers=old, json=body)).status_code == 403
    writer, token_id = await token(["profile:read", "profile:write"])
    created = await profile(api, writer)
    assert (await api.get("/resources/profiles", headers=writer)).status_code == 200
    assert (
        await api.post(
            f"/tasks/{task_id}/profiles",
            headers=writer,
            json={"profile_id": created["profile_id"]},
        )
    ).status_code == 403
    selector, _ = await token(["task:profile", "task:read", "profile:read"])
    assert (
        await api.post(
            f"/tasks/{task_id}/profiles",
            headers=selector,
            json={"profile_id": row["profile_id"]},
        )
    ).status_code == 200
    assert (await api.get(f"/tasks/{task_id}/profiles", headers=selector)).status_code == 200
    async with application.state.db.transaction(UUID(headers[0]["X-Org-Id"])) as session:
        event = await session.scalar(
            select(AuditLog).where(AuditLog.object_id == UUID(created["profile_id"]))
        )
        assert str(event.actor_token_id) == token_id
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(select(Membership).where(Membership.org_id == tenants["orgs"][0]))
        member.role = "technical"
    assert (
        await api.post("/resources/profiles", headers=writer, json={"data": metadata()})
    ).status_code == 403


async def test_profile_commit_failure_has_no_partial_success(api, headers, monkeypatch):
    from app.services import versioned

    def invalid_event(session, actor, action, object_id, details):
        session.add(
            AuditLog(
                org_id=actor.org_id,
                actor_user_id=uuid4(),
                action="synthetic-invalid-actor",
                object_id=object_id,
                details={},
            )
        )

    monkeypatch.setattr(versioned, "audit", invalid_event)
    response = await api.post("/resources/profiles", headers=headers[0], json={"data": metadata()})
    assert response.status_code == 409 and not response.json()["ok"]
    assert (await api.get("/resources/profiles", headers=headers[0])).json()["items"] == []
