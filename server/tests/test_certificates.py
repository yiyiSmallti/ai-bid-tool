import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.models.entities import AuditLog, CertificateRevision, Membership, TaskCertificate
from app.schemas.certificate_contracts import CertificateData
from app.services.certificates import inspect_dates
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def metadata(**patch):
    return {
        "kind": "qualification",
        "name": "Synthetic certificate declaration",
        "number": "SYNTHETIC-ONLY",
        "valid_from": "2026-01-01",
        "valid_until": "2026-12-31",
        **patch,
    }


async def certificate(api, header):
    response = await api.post("/resources/certificates", headers=header, json={"data": metadata()})
    assert response.status_code == 200 and "declarations" in response.json()["warnings"][0]
    return response.json()["data"]


async def task(api, header):
    return (await api.post("/tasks", headers=header, json={"name": "Synthetic task"})).json()[
        "data"
    ]["id"]


async def test_certificate_versions_fixed_snapshots_inspection_and_audit(api, headers, application):
    row = await certificate(api, headers[0])
    task_id = await task(api, headers[0])
    path = f"/tasks/{task_id}/certificates"
    body = {"certificate_id": row["certificate_id"], "lot": "synthetic"}
    first = (await api.post(path, headers=headers[0], json=body)).json()["data"]
    revised = await api.post(
        f"/resources/certificates/{row['certificate_id']}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "data": metadata(valid_until="2027-12-31")},
    )
    assert revised.status_code == 200 and revised.json()["data"]["revision"] == 2
    fixed = (await api.get(path, headers=headers[0], params={"as_of": "2027-01-01"})).json()
    assert fixed["items"][0]["data"] == row["data"] and fixed["items"][0]["revision"] == 1
    assert fixed["data"]["validity_by_revision"][row["id"]] == {
        "as_of": "2027-01-01",
        "state": "expired",
    }
    replacement = (await api.post(path, headers=headers[0], json=body)).json()["data"]
    assert replacement["revision"] == 2 and replacement["replaced_snapshot_id"] == first["id"]
    repeated = (await api.post(path, headers=headers[0], json=body)).json()["data"]
    assert repeated["duplicate"] and repeated["id"] == replacement["id"]
    history = (
        await api.get(path, headers=headers[0], params={"history": "true", "as_of": "2027-01-01"})
    ).json()
    assert [item["revision"] for item in history["items"]] == [1, 2]
    assert history["data"]["active_snapshot_ids"] == [replacement["id"]]
    assert sorted(v["state"] for v in history["data"]["validity_by_revision"].values()) == [
        "expired",
        "valid",
    ]
    versions = (
        await api.get("/resources/certificates", headers=headers[0], params={"history": "true"})
    ).json()
    assert [item["revision"] for item in versions["items"]] == [1, 2]
    assert all(
        v == {"as_of": None, "state": "unknown"}
        for v in versions["data"]["validity_by_revision"].values()
    )
    # Fixed revision and distinct lots remain independently selectable.
    explicit = await api.post(
        path, headers=headers[0], json={**body, "revision": 1, "lot": "second"}
    )
    assert explicit.status_code == 200 and explicit.json()["data"]["revision"] == 1
    async with application.state.db.transaction(UUID(headers[0]["X-Org-Id"])) as session:
        events = (
            await session.scalars(
                select(AuditLog)
                .where(AuditLog.action.like("%certificate%"))
                .order_by(AuditLog.created_at)
            )
        ).all()
        assert len(events) == 5 and events[-2].details["old_revision_id"] == row["id"]
        assert all("SYNTHETIC-ONLY" not in str(event.details) for event in events)
    assert application.state.queue.calls == []


async def test_certificate_endpoints_hide_foreign_data_and_reject_context_forgery(api, headers):
    own, foreign = await certificate(api, headers[0]), await certificate(api, headers[1])
    own_task, foreign_task = await task(api, headers[0]), await task(api, headers[1])
    routes = [
        ("POST", "/resources/certificates", {"data": metadata()}),
        (
            "POST",
            f"/resources/certificates/{own['certificate_id']}/revisions",
            {"expected_revision": 1, "data": metadata()},
        ),
        ("GET", "/resources/certificates", None),
        ("POST", f"/tasks/{own_task}/certificates", {"certificate_id": own["certificate_id"]}),
        ("GET", f"/tasks/{own_task}/certificates", None),
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
            f"/resources/certificates/{foreign['certificate_id']}/revisions",
            {"expected_revision": 1, "data": metadata()},
        ),
        ("GET", f"/resources/certificates?certificate_id={foreign['certificate_id']}", None),
        ("POST", f"/tasks/{own_task}/certificates", {"certificate_id": foreign["certificate_id"]}),
        ("POST", f"/tasks/{foreign_task}/certificates", {"certificate_id": own["certificate_id"]}),
        ("GET", f"/tasks/{foreign_task}/certificates", None),
        (
            "POST",
            f"/tasks/{own_task}/certificates",
            {"certificate_id": own["certificate_id"], "revision": 999},
        ),
    ]:
        response = await api.request(method, path, headers=headers[0], json=body)
        assert (
            response.status_code == 404 and response.json()["data"]["error"]["code"] == "not_found"
        )
    assert [
        item["certificate_id"]
        for item in (await api.get("/resources/certificates", headers=headers[0])).json()["items"]
    ] == [own["certificate_id"]]
    assert (
        await api.post(
            "/resources/certificates",
            headers=headers[0],
            json={"org_id": foreign["org_id"], "data": metadata()},
        )
    ).status_code == 422


async def test_certificate_concurrent_updates_and_duplicate_selection(api, headers, application):
    row = await certificate(api, headers[0])
    updates = await asyncio.gather(
        *(
            api.post(
                f"/resources/certificates/{row['certificate_id']}/revisions",
                headers=headers[0],
                json={"expected_revision": 1, "data": metadata(number=number)},
            )
            for number in ("SYNTHETIC-A", "SYNTHETIC-B")
        )
    )
    assert sorted(response.status_code for response in updates) == [200, 409]
    task_id = await task(api, headers[0])
    responses = await asyncio.gather(
        *(
            api.post(
                f"/tasks/{task_id}/certificates",
                headers=headers[0],
                json={"certificate_id": row["certificate_id"]},
            )
            for _ in range(4)
        )
    )
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["data"]["id"] for response in responses}) == 1
    assert sum(not response.json()["data"]["duplicate"] for response in responses) == 1
    async with application.state.db.transaction(UUID(headers[0]["X-Org-Id"])) as session:
        assert await session.scalar(select(func.count()).select_from(CertificateRevision)) == 2
        assert await session.scalar(select(func.count()).select_from(TaskCertificate)) == 1


@pytest.mark.parametrize(
    "role,write,select_allowed",
    [
        ("admin", True, True),
        ("bidder", True, True),
        ("technical", False, True),
        ("viewer", False, False),
    ],
)
async def test_certificate_roles_preserve_old_grants(
    role, write, select_allowed, api, headers, tenants, admin_engine
):
    from app.services.auth import ROLE_SCOPES

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
    assert {
        scope
        for scope in ROLE_SCOPES[role]
        if scope
        not in {
            "certificate:read",
            "certificate:write",
            "task:certificate",
            "profile:read",
            "profile:write",
            "task:profile",
            "certificate:file:read",
            "certificate:file:write",
            "evidence:source:read",
            "evidence:source:write",
            "template:read",
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
        }
    } == old_expected[role]
    row, task_id = await certificate(api, headers[0]), await task(api, headers[0])
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(select(Membership).where(Membership.org_id == tenants["orgs"][0]))
        member.role = role
    assert (await api.get("/resources/certificates", headers=headers[0])).status_code == 200
    assert (await api.get(f"/tasks/{task_id}/certificates", headers=headers[0])).status_code == 200
    for path, body in [
        ("/resources/certificates", {"data": metadata()}),
        (
            f"/resources/certificates/{row['certificate_id']}/revisions",
            {"expected_revision": 1, "data": metadata()},
        ),
    ]:
        assert (await api.post(path, headers=headers[0], json=body)).status_code == (
            200 if write else 403
        )
    assert (
        await api.post(
            f"/tasks/{task_id}/certificates",
            headers=headers[0],
            json={"certificate_id": row["certificate_id"]},
        )
    ).status_code == (200 if select_allowed else 403)


@pytest.mark.parametrize(
    "patch",
    [
        {"kind": "fake"},
        {"kind": None},
        {"name": "  "},
        {"number": "  "},
        {"scan": "fake"},
        {"valid_until": "2025-12-31"},
        {"valid_from": "2026-02-30"},
    ],
)
async def test_certificate_invalid_inputs_never_write(patch, api, headers):
    assert (
        await api.post(
            "/resources/certificates", headers=headers[0], json={"data": metadata(**patch)}
        )
    ).status_code == 422
    assert (await api.get("/resources/certificates", headers=headers[0])).json()["items"] == []


@pytest.mark.parametrize(
    "start,end,as_of,state",
    [
        ("2026-01-01", "2026-12-31", "2026-01-01", "valid"),
        ("2026-01-01", "2026-12-31", "2026-12-31", "valid"),
        ("2026-01-01", "2026-12-31", "2027-01-01", "expired"),
        ("2026-01-01", "2026-12-31", "2025-12-31", "not_yet_valid"),
        (None, None, "2026-01-01", "unknown"),
        (None, "2026-12-31", "2026-12-31", "unknown"),
        ("2026-01-01", None, "2026-01-01", "unknown"),
        (None, "2026-12-31", "2027-01-01", "expired"),
        ("2026-01-01", None, "2025-12-31", "not_yet_valid"),
        ("2026-01-01", "2026-12-31", None, "unknown"),
    ],
)
def test_declared_date_boundaries(start, end, as_of, state):
    result = inspect_dates(
        CertificateData.model_validate(metadata(valid_from=start, valid_until=end)),
        date.fromisoformat(as_of) if as_of else None,
    )
    assert result == {"as_of": as_of, "state": state}


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

    row, task_id = await certificate(api, headers[0]), await task(api, headers[0])
    old, _ = await token(["task:read", "resource:read", "resource:write", "task:resource"])
    for method, path, body in [
        ("GET", "/resources/certificates", None),
        ("GET", f"/tasks/{task_id}/certificates", None),
        ("POST", "/resources/certificates", {"data": metadata()}),
        (
            "POST",
            f"/resources/certificates/{row['certificate_id']}/revisions",
            {"expected_revision": 1, "data": metadata()},
        ),
        ("POST", f"/tasks/{task_id}/certificates", {"certificate_id": row["certificate_id"]}),
    ]:
        assert (await api.request(method, path, headers=old, json=body)).status_code == 403
    writer, token_id = await token(["certificate:read", "certificate:write"])
    created = await certificate(api, writer)
    assert (await api.get("/resources/certificates", headers=writer)).status_code == 200
    assert (
        await api.post(
            f"/tasks/{task_id}/certificates",
            headers=writer,
            json={"certificate_id": created["certificate_id"]},
        )
    ).status_code == 403
    selector, _ = await token(["task:certificate", "task:read", "certificate:read"])
    assert (
        await api.post(
            f"/tasks/{task_id}/certificates",
            headers=selector,
            json={"certificate_id": row["certificate_id"]},
        )
    ).status_code == 200
    assert (await api.get(f"/tasks/{task_id}/certificates", headers=selector)).status_code == 200
    async with application.state.db.transaction(UUID(headers[0]["X-Org-Id"])) as session:
        event = await session.scalar(
            select(AuditLog).where(AuditLog.object_id == UUID(created["certificate_id"]))
        )
        assert str(event.actor_token_id) == token_id
    with Session(admin_engine) as session, session.begin():
        member = session.scalar(select(Membership).where(Membership.org_id == tenants["orgs"][0]))
        member.role = "technical"
    assert (
        await api.post("/resources/certificates", headers=writer, json={"data": metadata()})
    ).status_code == 403


async def test_certificate_commit_failure_has_no_partial_success(api, headers, monkeypatch):
    from app.services import certificates

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

    monkeypatch.setattr(certificates, "audit", invalid_event)
    response = await api.post(
        "/resources/certificates", headers=headers[0], json={"data": metadata()}
    )
    assert response.status_code == 409 and not response.json()["ok"]
    assert (await api.get("/resources/certificates", headers=headers[0])).json()["items"] == []
