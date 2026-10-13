import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.models.entities import AuditLog, OrgProfileRevision, TaskOrgProfile
from sqlalchemy import func, select


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
    row, task_id = (await profile(api, headers), await task(api, headers))
    path = f"/tasks/{task_id}/profiles"
    body = {"profile_id": row["profile_id"], "lot": "synthetic"}
    first = (await api.post(path, headers=headers, json=body)).json()["data"]
    assert row["data"]["registration_details"] is None and row["data"]["standard_wording"] is None
    updated = await api.post(
        f"/resources/profiles/{row['profile_id']}/revisions",
        headers=headers,
        json={"expected_revision": 1, "data": metadata(performance_summary="Synthetic summary B")},
    )
    assert updated.status_code == 200 and updated.json()["data"]["revision"] == 2
    fixed = (await api.get(path, headers=headers)).json()["items"][0]
    assert fixed["revision"] == 1 and fixed["data"] == row["data"]
    replacement = (await api.post(path, headers=headers, json=body)).json()["data"]
    assert replacement["revision"] == 2 and replacement["replaced_snapshot_id"] == first["id"]
    repeat = (await api.post(path, headers=headers, json=body)).json()["data"]
    assert repeat["duplicate"] and repeat["id"] == replacement["id"]
    history = (await api.get(path, headers=headers, params={"history": "true"})).json()
    assert [r["revision"] for r in history["items"]] == [1, 2]
    assert history["data"]["active_snapshot_ids"] == [replacement["id"]]
    versions = (
        await api.get(
            "/resources/profiles",
            headers=headers,
            params={"profile_id": row["profile_id"], "history": "true"},
        )
    ).json()
    assert [r["revision"] for r in versions["items"]] == [1, 2]
    assert versions["data"]["current_revisions"] == {row["profile_id"]: 2}
    explicit = await api.post(path, headers=headers, json={**body, "revision": 1, "lot": "second"})
    assert explicit.status_code == 200 and explicit.json()["data"]["revision"] == 1
    async with application.state.db.transaction() as session:
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


async def test_profile_concurrent_updates_and_duplicate_selection(api, headers, application):
    row = await profile(api, headers)
    updates = await asyncio.gather(
        *(
            api.post(
                f"/resources/profiles/{row['profile_id']}/revisions",
                headers=headers,
                json={"expected_revision": 1, "data": metadata(performance_summary=number)},
            )
            for number in ("SYNTHETIC-A", "SYNTHETIC-B")
        )
    )
    assert sorted(response.status_code for response in updates) == [200, 409]
    task_id = await task(api, headers)
    responses = await asyncio.gather(
        *(
            api.post(
                f"/tasks/{task_id}/profiles",
                headers=headers,
                json={"profile_id": row["profile_id"]},
            )
            for _ in range(4)
        )
    )
    assert all(response.status_code == 200 for response in responses)
    assert len({response.json()["data"]["id"] for response in responses}) == 1
    assert sum(not response.json()["data"]["duplicate"] for response in responses) == 1
    async with application.state.db.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(OrgProfileRevision)) == 2
        assert await session.scalar(select(func.count()).select_from(TaskOrgProfile)) == 1


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
        await api.post("/resources/profiles", headers=headers, json={"data": metadata(**patch)})
    ).status_code == 422
    assert (await api.get("/resources/profiles", headers=headers)).json()["items"] == []


async def test_old_tokens_denied_new_scopes_and_scope_limits(
    api, headers, application, database, admin_engine
):
    expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    async def token(scopes):
        response = await api.post(
            "/tokens",
            headers=headers,
            json={"name": "Synthetic only", "scopes": scopes, "expires_at": expiry},
        )
        assert response.status_code == 200
        row = response.json()["data"]
        return ({**headers, "Authorization": "Bearer " + row["token"]}, row["id"])

    row, task_id = (await profile(api, headers), await task(api, headers))
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
            f"/tasks/{task_id}/profiles", headers=writer, json={"profile_id": created["profile_id"]}
        )
    ).status_code == 403
    selector, _ = await token(["task:profile", "task:read", "profile:read"])
    assert (
        await api.post(
            f"/tasks/{task_id}/profiles", headers=selector, json={"profile_id": row["profile_id"]}
        )
    ).status_code == 200
    assert (await api.get(f"/tasks/{task_id}/profiles", headers=selector)).status_code == 200
    async with application.state.db.transaction() as session:
        event = await session.scalar(
            select(AuditLog).where(AuditLog.object_id == UUID(created["profile_id"]))
        )
        assert str(event.actor_token_id) == token_id


async def test_profile_commit_failure_has_no_partial_success(api, headers, monkeypatch):
    from app.services import versioned

    def invalid_event(session, actor, action, object_id, details):
        session.add(
            AuditLog(
                actor_token_id=uuid4(),
                action="synthetic-invalid-actor",
                object_id=object_id,
                details={},
            )
        )

    monkeypatch.setattr(versioned, "audit", invalid_event)
    response = await api.post("/resources/profiles", headers=headers, json={"data": metadata()})
    assert response.status_code == 409 and (not response.json()["ok"])
    assert (await api.get("/resources/profiles", headers=headers)).json()["items"] == []
