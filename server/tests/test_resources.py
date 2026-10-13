import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.models.entities import AuditLog, ProductRevision, TaskResource
from sqlalchemy import func, select

DATA = {
    "name": "Synthetic product",
    "vendor": "Synthetic vendor",
    "model": "Exact model A",
    "model_version": None,
    "official_url": "https://vendor.example.invalid/spec",
}


async def create(api, headers, data=None):
    response = await api.post("/resources/products", headers=headers, json={"data": data or DATA})
    assert response.status_code == 200
    return response.json()["data"]


async def task(api, headers):
    return (
        await api.post("/tasks", headers=headers, json={"name": "Synthetic snapshot task"})
    ).json()["data"]["id"]


async def test_version_snapshot_replacement_and_history(api, headers, application):
    product = await create(api, headers)
    task_id = await task(api, headers)
    path = f"/tasks/{task_id}/products"
    selection = {"product_id": product["product_id"], "lot": "lot-a"}
    first = (await api.post(path, headers=headers, json=selection)).json()["data"]
    assert first["revision"] == 1 and (not first["duplicate"])
    updated = await api.post(
        f"/resources/products/{product['product_id']}/revisions",
        headers=headers,
        json={"expected_revision": 1, "data": {**DATA, "model": "Exact model B"}},
    )
    assert updated.status_code == 200 and updated.json()["data"]["revision"] == 2
    current = (await api.get(path, headers=headers)).json()["items"]
    assert current[0]["revision"] == 1 and current[0]["data"]["model"] == "Exact model A"
    replaced = (await api.post(path, headers=headers, json=selection)).json()["data"]
    assert replaced["revision"] == 2 and replaced["replaced_snapshot_id"] == first["id"]
    assert (await api.post(path, headers=headers, json=selection)).json()["data"]["duplicate"]
    history = (await api.get(path, headers=headers, params={"history": "true"})).json()
    assert [row["revision"] for row in history["items"]] == [1, 2]
    assert history["data"]["active_snapshot_ids"] == [replaced["id"]]
    products = (
        await api.get("/resources/products", headers=headers, params={"history": "true"})
    ).json()["items"]
    assert [row["revision"] for row in products] == [1, 2]
    async with application.state.db.transaction() as session:
        events = (await session.scalars(select(AuditLog).order_by(AuditLog.created_at))).all()
        assert len(events) == 4
        assert events[-1].details["old_revision_id"] == product["id"]
        assert "model" not in str(events[-1].details)
    assert application.state.queue.calls == []


async def test_concurrent_revision_conflict_and_duplicate_selection(api, headers, application):
    product = await create(api, headers)
    updates = await asyncio.gather(
        *(
            api.post(
                f"/resources/products/{product['product_id']}/revisions",
                headers=headers,
                json={"expected_revision": 1, "data": {**DATA, "model": model}},
            )
            for model in ("Synthetic B", "Synthetic C")
        )
    )
    assert sorted(row.status_code for row in updates) == [200, 409]
    assert (
        next(row for row in updates if row.status_code == 409).json()["data"]["error"]["exit_code"]
        == 2
    )
    task_id = await task(api, headers)
    selections = await asyncio.gather(
        *(
            api.post(
                f"/tasks/{task_id}/products",
                headers=headers,
                json={"product_id": product["product_id"]},
            )
            for _ in range(4)
        )
    )
    assert all(row.status_code == 200 for row in selections)
    assert len({row.json()["data"]["id"] for row in selections}) == 1
    assert sum(not row.json()["data"]["duplicate"] for row in selections) == 1
    async with application.state.db.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(ProductRevision)) == 2
        assert await session.scalar(select(func.count()).select_from(TaskResource)) == 1
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 3


async def test_tokens_require_explicit_new_scopes_and_audit_actor(api, headers, application):
    expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    async def token(scopes):
        value = (
            await api.post(
                "/tokens",
                headers=headers,
                json={"name": "Synthetic token", "scopes": scopes, "expires_at": expiry},
            )
        ).json()["data"]
        return ({**headers, "Authorization": "Bearer " + value["token"]}, value["id"])

    old, old_token_id = await token(["task:read"])
    assert (await api.get("/resources/products", headers=old)).status_code == 403
    writer, token_id = await token(["resource:write", "resource:read"])
    product = await create(api, writer)
    assert (await api.get("/resources/products", headers=writer)).status_code == 200
    task_id = await task(api, headers)
    assert (
        await api.post(
            f"/tasks/{task_id}/products", headers=writer, json={"product_id": product["product_id"]}
        )
    ).status_code == 403
    async with application.state.db.transaction() as session:
        record = await session.scalar(
            select(AuditLog).where(
                AuditLog.action == "resource.product.create",
                AuditLog.object_id == UUID(product["product_id"]),
            )
        )
        assert record is not None
        assert str(record.actor_token_id) == token_id
        assert record.actor_kind == "token" and record.initiated_by == "external_agent"
        assert record.invocation_id is not None
        denied = await session.scalar(
            select(AuditLog).where(
                AuditLog.action == "command.failed", AuditLog.actor_token_id == UUID(old_token_id)
            )
        )
        assert denied is not None and denied.details["reason_code"] == "forbidden"
        assert denied.actor_kind == "token" and denied.initiated_by == "external_agent"


@pytest.mark.parametrize(
    "body",
    [
        {"data": {**DATA, "model": "  "}},
        {"data": {**DATA, "official_url": "https://user:password@example.invalid"}},
        {"data": DATA, "unknown": True},
    ],
)
async def test_invalid_metadata_is_rejected_without_writes(body, api, headers, application):
    assert (await api.post("/resources/products", headers=headers, json=body)).status_code == 422
    assert (await api.get("/resources/products", headers=headers)).json()["items"] == []
    async with application.state.db.transaction() as session:
        assert await session.scalar(select(func.count()).select_from(AuditLog)) == 0


async def test_commit_failure_returns_error_and_rolls_back(api, headers, application, monkeypatch):
    from app.services import versioned

    original = versioned.audit

    def invalid_audit(session, actor, action, object_id, details):
        original(session, actor, action, object_id, details)
        session.add(
            AuditLog(
                actor_token_id=uuid4(),
                action="synthetic-invalid-actor",
                object_id=object_id,
                details={},
            )
        )

    monkeypatch.setattr(versioned, "audit", invalid_audit)
    response = await api.post("/resources/products", headers=headers, json={"data": DATA})
    assert response.status_code == 409
    assert response.json()["ok"] is False
    assert response.json()["data"]["error"]["code"] == "conflict"
    assert "synthetic-invalid-actor" not in response.text
    assert (await api.get("/resources/products", headers=headers)).json()["items"] == []
