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
from uuid import UUID, uuid4

import pytest
from app.models.entities import AuditLog, TaskResource
from sqlalchemy import func, select, text
from test_resources import DATA, create, task
from test_team_workflow_membership import add_member, person, workflow


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
    product = await create(api, headers[0])
    product_id = product["product_id"]
    first_task, second_task = await task(api, headers[0]), await task(api, headers[0])
    first = await pin(api, headers[0], first_task, product_id)
    second = await pin(api, headers[0], second_task, product_id)
    assert first.status_code == second.status_code == 200
    revision = await revise(api, headers[0], product_id)
    for task_id in (first_task, second_task):
        selected = await api.get(f"/v4/tasks/{task_id}/products", headers=headers[0])
        assert selected.status_code == 200
        assert selected.json()["items"][0]["product_revision_id"] == product["id"]
        assert selected.json()["items"][0]["data"]["model"] == DATA["model"]
    replaced = await pin(api, headers[0], first_task, product_id, revision=2)
    assert replaced.status_code == 200
    assert replaced.json()["data"]["product_revision_id"] == revision["id"]
    assert replaced.json()["data"]["replaced_snapshot_id"] == first.json()["data"]["id"]
    unchanged = await api.get(f"/v4/tasks/{second_task}/products", headers=headers[0])
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
    product = await create(api, headers[0])
    product_id = product["product_id"]
    kept_task, replaced_task, empty_task = [await task(api, headers[0]) for _ in range(3)]
    kept = await pin(api, headers[0], kept_task, product_id, lot=" lot-a ")
    old = await pin(api, headers[0], replaced_task, product_id)
    assert kept.status_code == old.status_code == 200
    await revise(api, headers[0], product_id)
    replaced = await pin(api, headers[0], replaced_task, product_id, revision=2)
    assert replaced.status_code == 200
    stopped = await lifecycle(api, headers[0], product_id, 2, 0, "inactive")
    assert stopped.status_code == 200, stopped.text

    org = UUID(headers[0]["X-Org-Id"])

    async def write_counts():
        async with application.state.db.transaction(org) as session:
            return (
                await session.scalar(select(func.count()).select_from(TaskResource)),
                await session.scalar(
                    select(func.count())
                    .select_from(AuditLog)
                    .where(AuditLog.action == "task.resource.select")
                ),
            )

    before = await write_counts()
    replay = await pin(api, headers[0], kept_task, product_id, lot="lot-a")
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
        denied = await pin(api, headers[0], task_id, product_id, revision, lot)
        assert denied.status_code == 409, denied.text
        assert denied.json()["data"]["error"]["code"] == "resource_inactive"
        assert denied.json()["data"]["error"]["exit_code"] == 2
    assert await write_counts() == before
    restored = await lifecycle(api, headers[0], product_id, 2, 1, "active")
    assert restored.status_code == 200, restored.text
    assert await write_counts() == before
    restored_pin = await pin(api, headers[0], replaced_task, product_id, revision=1)
    assert restored_pin.status_code == 200
    assert restored_pin.json()["data"]["id"] != old.json()["data"]["id"]
    assert restored_pin.json()["data"]["duplicate"] is False
    history = await api.get(
        f"/v4/tasks/{replaced_task}/products", headers=headers[0], params={"history": True}
    )
    assert [row["revision"] for row in history.json()["items"]] == [1, 2, 1]


@pytest.mark.parametrize(
    "org_role,task_role,domains,status",
    [
        ("technical", "contributor", ["technical"], 200),
        ("bidder", "contributor", ["commercial"], 200),
        ("technical", "reviewer", ["technical"], 403),
        ("bidder", "reviewer", ["commercial"], 403),
        ("technical", "observer", [], 403),
        ("viewer", "observer", [], 403),
        ("admin", None, [], 403),
        ("technical", None, [], 404),
    ],
)
async def test_library_access_never_grants_task_pinning(
    api, headers, tenants, admin_engine, org_role, task_role, domains, status
):
    product = await create(api, headers[0])
    task_id = await task(api, headers[0])
    user, auth = await person(api, admin_engine, tenants["orgs"][0], org_role)
    if task_role:
        added = await add_member(api, headers[0], task_id, user, 1, task_role, domains)
        assert added.status_code == 200, added.text
    visible = await api.get(
        f"/v4/management/resources/products/{product['product_id']}", headers=auth
    )
    assert visible.status_code == 200
    response = await pin(api, auth, task_id, product["product_id"])
    assert response.status_code == status, response.text


async def test_archive_denies_even_inactive_duplicate_but_keeps_pin_readable(api, headers):
    product = await create(api, headers[0])
    task_id = await task(api, headers[0])
    selected = await pin(api, headers[0], task_id, product["product_id"])
    assert selected.status_code == 200
    stopped = await lifecycle(api, headers[0], product["product_id"], 1, 0, "inactive")
    assert stopped.status_code == 200
    state = await workflow(api, headers[0], task_id)
    archived = await api.post(
        f"/v4/tasks/{task_id}/archive",
        headers=headers[0],
        json={"expected_revision": state["revision"], "reason": "Synthetic pin archival"},
    )
    assert archived.status_code == 200, archived.text
    denied = await pin(api, headers[0], task_id, product["product_id"])
    assert denied.status_code == 409
    assert denied.json()["data"]["error"]["code"] == "task_archived"
    read = await api.get(f"/v4/tasks/{task_id}/products", headers=headers[0])
    assert read.status_code == 200
    assert read.json()["items"][0]["id"] == selected.json()["data"]["id"]


async def test_foreign_missing_and_removed_member_pin_masking(api, headers, tenants, admin_engine):
    own, foreign = await create(api, headers[0]), await create(api, headers[1])
    own_task, foreign_task = await task(api, headers[0]), await task(api, headers[1])
    results = []
    for task_id, product_id in (
        (own_task, foreign["product_id"]),
        (foreign_task, own["product_id"]),
        (str(uuid4()), own["product_id"]),
        (own_task, str(uuid4())),
    ):
        response = await pin(api, headers[0], task_id, product_id)
        assert response.status_code == 404
        results.append(response.json()["data"]["error"])
    user, auth = await person(api, admin_engine, tenants["orgs"][0], "technical")
    added = await add_member(api, headers[0], own_task, user, 1)
    assert added.status_code == 200
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE memberships SET active=false WHERE org_id=:org AND user_id=:user"),
            {"org": tenants["orgs"][0], "user": user},
        )
    removed = await pin(api, auth, own_task, own["product_id"])
    assert removed.status_code == 404
    assert all(error == removed.json()["data"]["error"] for error in results)


async def test_deactivation_and_pin_serialize_without_lost_selection(api, headers):
    product = await create(api, headers[0])
    task_id = await task(api, headers[0])
    selected, stopped = await asyncio.gather(
        pin(api, headers[0], task_id, product["product_id"]),
        lifecycle(api, headers[0], product["product_id"], 1, 0, "inactive"),
    )
    assert stopped.status_code == 200, stopped.text
    assert selected.status_code in {200, 409}, selected.text
    current = await api.get(f"/v4/tasks/{task_id}/products", headers=headers[0])
    assert len(current.json()["items"]) == (1 if selected.status_code == 200 else 0)
    if selected.status_code == 200:
        assert current.json()["items"][0]["id"] == selected.json()["data"]["id"]
    else:
        assert selected.json()["data"]["error"]["code"] == "resource_inactive"
