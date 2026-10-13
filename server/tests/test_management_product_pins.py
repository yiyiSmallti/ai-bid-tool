"""Product pin API/PostgreSQL failure inventory, fixed before the service change.

Library head changes must not replace either task's immutable pin. Inactive roots
must reject new roots/lots/replacements, including old revisions, but an exact
active pin replay remains a write-free duplicate. A historical inactive snapshot
is not a duplicate. Task observers/reviewers, nonmembers, foreign orgs, removed
members, admin recovery readers and archived tasks cannot use the library to
bypass their task ceiling. Concurrent deactivate/select must serialize at the
root after task locks without losing a successful pin. Restoration never re-pins.
"""

import asyncio

from app.models.entities import AuditLog, TaskResource
from sqlalchemy import func, select
from test_resources import DATA, create, task


async def lifecycle(api, auth, product, content_revision, lifecycle_revision, state):
    return await api.post(
        f"/v4/management/resources/products/{product}/lifecycle",
        headers=auth,
        json={
            "expected_revision": content_revision,
            "expected_lifecycle_revision": lifecycle_revision,
            "state": state,
            "reason_code": "restored" if state == "active" else "obsolete",
        },
    )


async def pin(api, auth, task_id, product_id, revision=1, lot=None):
    return await api.post(
        f"/v4/tasks/{task_id}/products",
        headers=auth,
        json={"product_id": product_id, "revision": revision, "lot": lot},
    )


async def revise(api, auth, product_id):
    response = await api.post(
        f"/v4/resources/products/{product_id}/revisions",
        headers=auth,
        json={"expected_revision": 1, "data": {**DATA, "model": "Exact model B"}},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def test_two_tasks_remain_pinned_until_one_explicitly_replaces(api, headers):
    product = await create(api, headers)
    product_id = product["product_id"]
    first_task, second_task = (await task(api, headers), await task(api, headers))
    first = await pin(api, headers, first_task, product_id)
    second = await pin(api, headers, second_task, product_id)
    assert first.status_code == second.status_code == 200
    revision = await revise(api, headers, product_id)
    for task_id in (first_task, second_task):
        selected = await api.get(f"/v4/tasks/{task_id}/products", headers=headers)
        assert selected.status_code == 200
        assert selected.json()["items"][0]["product_revision_id"] == product["id"]
        assert selected.json()["items"][0]["data"]["model"] == DATA["model"]
    replaced = await pin(api, headers, first_task, product_id, revision=2)
    assert replaced.status_code == 200
    assert replaced.json()["data"]["product_revision_id"] == revision["id"]
    assert replaced.json()["data"]["replaced_snapshot_id"] == first.json()["data"]["id"]
    unchanged = await api.get(f"/v4/tasks/{second_task}/products", headers=headers)
    assert unchanged.json()["items"] == [
        {
            k: v
            for k, v in second.json()["data"].items()
            if k not in {"duplicate", "replaced_snapshot_id"}
        }
    ]


async def test_inactive_exact_duplicate_only_and_restore_preserves_history(
    api, headers, application
):
    product = await create(api, headers)
    product_id = product["product_id"]
    kept_task, replaced_task, empty_task = [await task(api, headers) for _ in range(3)]
    kept = await pin(api, headers, kept_task, product_id, lot=" lot-a ")
    old = await pin(api, headers, replaced_task, product_id)
    assert kept.status_code == old.status_code == 200
    await revise(api, headers, product_id)
    replaced = await pin(api, headers, replaced_task, product_id, revision=2)
    assert replaced.status_code == 200
    stopped = await lifecycle(api, headers, product_id, 2, 0, "inactive")
    assert stopped.status_code == 200, stopped.text

    async def write_counts():
        async with application.state.db.transaction() as session:
            return (
                await session.scalar(select(func.count()).select_from(TaskResource)),
                await session.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == "task.resource.select")
                ),
            )

    before = await write_counts()
    replay = await pin(api, headers, kept_task, product_id, lot="lot-a")
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["duplicate"] is True
    assert replay.json()["data"]["id"] == kept.json()["data"]["id"]
    assert await write_counts() == before
    for task_id, revision, lot in (
        (empty_task, 1, None),
        (empty_task, 2, None),
        (kept_task, 2, "lot-a"),
        (kept_task, 1, "new-lot"),
        (replaced_task, 1, None),
    ):
        denied = await pin(api, headers, task_id, product_id, revision, lot)
        assert denied.status_code == 409, denied.text
        assert denied.json()["data"]["error"]["code"] == "resource_inactive"
        assert denied.json()["data"]["error"]["exit_code"] == 2
    assert await write_counts() == before
    restored = await lifecycle(api, headers, product_id, 2, 1, "active")
    assert restored.status_code == 200, restored.text
    assert await write_counts() == before
    restored_pin = await pin(api, headers, replaced_task, product_id, revision=1)
    assert restored_pin.status_code == 200
    assert restored_pin.json()["data"]["id"] != old.json()["data"]["id"]
    assert restored_pin.json()["data"]["duplicate"] is False
    history = await api.get(
        f"/v4/tasks/{replaced_task}/products", headers=headers, params={"history": True}
    )
    assert [row["revision"] for row in history.json()["items"]] == [1, 2, 1]


async def test_deactivation_and_pin_serialize_without_lost_selection(api, headers):
    product = await create(api, headers)
    task_id = await task(api, headers)
    selected, stopped = await asyncio.gather(
        pin(api, headers, task_id, product["product_id"]),
        lifecycle(api, headers, product["product_id"], 1, 0, "inactive"),
    )
    assert stopped.status_code == 200, stopped.text
    assert selected.status_code in {200, 409}, selected.text
    current = await api.get(f"/v4/tasks/{task_id}/products", headers=headers)
    assert len(current.json()["items"]) == (1 if selected.status_code == 200 else 0)
    if selected.status_code == 200:
        assert current.json()["items"][0]["id"] == selected.json()["data"]["id"]
    else:
        assert selected.json()["data"]["error"]["code"] == "resource_inactive"
