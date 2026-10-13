import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.models.entities import AuditLog, FeatureRevision, TaskFeature
from sqlalchemy import func, select


async def product(api, header):
    response = await api.post(
        "/resources/products",
        headers=header,
        json={
            "data": {
                "name": "Synthetic",
                "vendor": "Synthetic vendor",
                "model": "Exact synthetic model",
            }
        },
    )
    assert response.status_code == 200
    return response.json()["data"]["product_id"]


def metadata(product_id, status="planned"):
    return {
        "product_id": product_id,
        "name": "Synthetic feature",
        "description": "Synthetic declaration, not implementation evidence",
        "status": status,
    }


async def feature(api, header, product_id=None):
    product_id = product_id or await product(api, header)
    response = await api.post(
        "/resources/features", headers=header, json={"data": metadata(product_id)}
    )
    assert response.status_code == 200
    assert response.json()["warnings"] and "evidence" in response.json()["warnings"][0]
    return response.json()["data"]


async def task(api, header):
    return (
        await api.post("/tasks", headers=header, json={"name": "Synthetic feature task"})
    ).json()["data"]["id"]


async def test_feature_version_snapshot_and_product_reassociation(api, headers, application):
    row = await feature(api, headers)
    task_id = await task(api, headers)
    path = f"/tasks/{task_id}/features"
    body = {"feature_id": row["feature_id"], "lot": "synthetic"}
    first = (await api.post(path, headers=headers, json=body)).json()["data"]
    other_product = await product(api, headers)
    updated = await api.post(
        f"/resources/features/{row['feature_id']}/revisions",
        headers=headers,
        json={"expected_revision": 1, "data": metadata(other_product, "developing")},
    )
    assert updated.status_code == 200 and updated.json()["data"]["revision"] == 2
    fixed = (await api.get(path, headers=headers)).json()["items"][0]
    assert fixed["data"] == row["data"] and fixed["revision"] == 1
    assert fixed["data"]["product_id"] != other_product
    replacement = (await api.post(path, headers=headers, json=body)).json()["data"]
    assert replacement["revision"] == 2 and replacement["replaced_snapshot_id"] == first["id"]
    repeated = (await api.post(path, headers=headers, json=body)).json()["data"]
    assert repeated["duplicate"] and repeated["id"] == replacement["id"]
    history = (await api.get(path, headers=headers, params={"history": "true"})).json()
    assert [item["revision"] for item in history["items"]] == [1, 2]
    assert history["data"]["active_snapshot_ids"] == [replacement["id"]]
    versions = (
        await api.get("/resources/features", headers=headers, params={"history": "true"})
    ).json()["items"]
    assert [item["revision"] for item in versions] == [1, 2]
    assert all(
        "screenshot" not in item["data"] and "verified" not in item["data"] for item in versions
    )
    async with application.state.db.transaction() as session:
        events = (
            await session.scalars(
                select(AuditLog)
                .where(AuditLog.action.like("%feature%"))
                .order_by(AuditLog.created_at)
            )
        ).all()
        assert len(events) == 4 and events[-1].details["old_revision_id"] == row["id"]
    assert application.state.queue.calls == []


async def test_feature_concurrent_updates_and_duplicate_selection(api, headers, application):
    row = await feature(api, headers)
    updates = await asyncio.gather(
        *(
            api.post(
                f"/resources/features/{row['feature_id']}/revisions",
                headers=headers,
                json={"expected_revision": 1, "data": metadata(row["data"]["product_id"], status)},
            )
            for status in ("developing", "implemented")
        )
    )
    assert sorted(response.status_code for response in updates) == [200, 409]
    task_id = await task(api, headers)
    requests = await asyncio.gather(
        *(
            api.post(
                f"/tasks/{task_id}/features",
                headers=headers,
                json={"feature_id": row["feature_id"]},
            )
            for _ in range(4)
        )
    )
    assert all(response.status_code == 200 for response in requests)
    assert len({response.json()["data"]["id"] for response in requests}) == 1
    assert sum(not response.json()["data"]["duplicate"] for response in requests) == 1
    async with application.state.db.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(FeatureRevision)) == 2
        assert await session.scalar(select(func.count()).select_from(TaskFeature)) == 1


@pytest.mark.parametrize(
    "patch",
    [{"status": "unknown"}, {"description": "   "}, {"name": "   "}, {"screenshot": "fake"}],
)
async def test_invalid_feature_metadata_has_no_writes(patch, api, headers, application):
    product_id = await product(api, headers)
    body = {**metadata(product_id), **patch}
    response = await api.post("/resources/features", headers=headers, json={"data": body})
    assert response.status_code == 422
    assert (await api.get("/resources/features", headers=headers)).json()["items"] == []
    async with application.state.db.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(FeatureRevision)) == 0
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 1


async def test_feature_token_scope_and_actor(api, headers, application):
    product_id = await product(api, headers)
    expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    async def token(scopes):
        row = (
            await api.post(
                "/tokens",
                headers=headers,
                json={"name": "Synthetic feature token", "scopes": scopes, "expires_at": expiry},
            )
        ).json()["data"]
        return ({**headers, "Authorization": "Bearer " + row["token"]}, row["id"])

    old, _ = await token(["task:read"])
    assert (await api.get("/resources/features", headers=old)).status_code == 403
    writer, token_id = await token(["resource:write", "resource:read"])
    row = await feature(api, writer, product_id)
    assert (await api.get("/resources/features", headers=writer)).status_code == 200
    task_id = await task(api, headers)
    assert (
        await api.post(
            f"/tasks/{task_id}/features", headers=writer, json={"feature_id": row["feature_id"]}
        )
    ).status_code == 403
    async with application.state.db.transaction() as session:
        event = await session.scalar(
            select(AuditLog).where(AuditLog.action == "resource.feature.create")
        )
        assert str(event.actor_token_id) == token_id


async def test_feature_commit_failure_rolls_back(api, headers, application, monkeypatch):
    from app.services import versioned

    product_id = await product(api, headers)

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
    response = await api.post(
        "/resources/features", headers=headers, json={"data": metadata(product_id)}
    )
    assert response.status_code == 409 and (not response.json()["ok"])
    assert (await api.get("/resources/features", headers=headers)).json()["items"] == []
