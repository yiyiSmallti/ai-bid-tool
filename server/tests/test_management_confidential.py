"""Real PostgreSQL/API acceptance for confidential management.

Failure inventory: org/task owner confusion, plaintext/ciphertext disclosure or
decrypting a read, unbounded pages, forged/stale authority cursors, token/human
gates, missing-null CAS, stale field/value preconditions, concurrent writers,
archive races, audit partial commits and accidental legacy append changes.
Requires an explicitly supplied disposable PostgreSQL runtime.
"""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.core.security import Secrets
from app.models.confidential import ConfidentialValue
from app.models.entities import ApiToken, AuditLog, Membership, Org, User
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_team_workflow_membership import add_member, new_task, person

FIELDS = "/v4/management/confidential-fields"
VALUES = "/v4/management/confidential-values"
SECRET = "synthetic-private-number-123456789"


async def create_field(
    api, auth, key="bank_number", *, scope="org", kind="bank_account", label=None
):
    reply = await api.post(
        "/confidential-fields",
        headers=auth,
        json={"key": key, "label": label or f"Synthetic {key}", "scope": scope, "kind": kind},
    )
    assert reply.status_code == 200, reply.text
    return reply.json()["data"]


def checked(value=SECRET, *, revision=1, expected=None, task_id=None):
    return {
        "value": value,
        "expected_field_revision": revision,
        "expected_value_id": expected,
        "task_id": task_id,
    }


async def write(api, auth, field, **patch):
    reply = await api.post(f"{FIELDS}/{field['id']}/values", headers=auth, json=checked(**patch))
    assert reply.status_code == 200, reply.text
    assert_safe(reply)
    return reply.json()["data"]


async def legacy_write(api, auth, field, value=SECRET, task_id=None):
    reply = await api.post(
        f"/confidential-fields/{field['id']}/values",
        headers=auth,
        json={"value": value, "task_id": task_id},
    )
    assert reply.status_code == 200, reply.text
    assert_safe(reply)
    return reply.json()["data"]


def assert_safe(reply, *secrets):
    assert SECRET not in reply.text
    for secret in secrets:
        assert secret not in reply.text
    assert '"encrypted_value"' not in reply.text and '"value"' not in reply.text


def assert_page(reply, auth, *, limit=25):
    assert reply.status_code == 200, reply.text
    assert_safe(reply)
    body = reply.json()
    assert set(body) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    assert set(body["data"]) == {"org_id", "as_of", "returned", "next_cursor", "has_more"}
    assert body["data"]["org_id"] == auth["X-Org-Id"]
    assert body["data"]["returned"] == len(body["items"]) <= limit
    assert bool(body["data"]["next_cursor"]) is body["data"]["has_more"]
    assert len(reply.content) <= 256 * 1024
    return body


async def test_bounded_order_prefix_archive_and_owner_pages(api, headers):
    org_fields = [await create_field(api, headers[0], f"alpha_{i}") for i in range(4)]
    task_field = await create_field(api, headers[0], "task_amount", scope="task", kind="amount")
    await create_field(api, headers[1], "alpha_0")
    task = await new_task(api, headers[0])
    await write(api, headers[0], org_fields[0])
    amount = await write(api, headers[0], task_field, value="123456.78", task_id=task)
    assert amount["tail"] is None
    page = assert_page(
        await api.post(FIELDS + "/query", headers=headers[0], json={"q": "ALP", "limit": 2}),
        headers[0],
        limit=2,
    )
    rest = assert_page(
        await api.post(
            FIELDS + "/query",
            headers=headers[0],
            json={"q": "alp", "limit": 2, "cursor": page["data"]["next_cursor"]},
        ),
        headers[0],
        limit=2,
    )
    assert [item["key"] for item in page["items"] + rest["items"]] == [
        f"alpha_{i}" for i in range(4)
    ]
    assert not rest["data"]["has_more"]
    for query in ({"q": "synt"}, {"q": "alpha"}):
        assert (
            assert_page(
                await api.post(FIELDS + "/query", headers=headers[0], json=query), headers[0]
            )["data"]["returned"]
            >= 4
        )
    for syntax in ("alpha | absent", "%", "' OR 1=1 --"):
        assert (
            assert_page(
                await api.post(FIELDS + "/query", headers=headers[0], json={"q": syntax}),
                headers[0],
            )["items"]
            == []
        )
    no_task = assert_page(
        await api.post(VALUES + "/query", headers=headers[0], json={}), headers[0]
    )
    assert [row["key"] for row in no_task["items"]] == [f"alpha_{i}" for i in range(4)]
    assert [row["status"] for row in no_task["items"]] == [
        "filled",
        "missing",
        "missing",
        "missing",
    ]
    with_task = assert_page(
        await api.post(VALUES + "/query", headers=headers[0], json={"task_id": task}), headers[0]
    )
    assert len(with_task["items"]) == 5
    assert (
        next(row for row in with_task["items"] if row["scope"] == "task")["value_id"]
        == amount["value_id"]
    )
    assert next(row for row in with_task["items"] if row["scope"] == "org")["task_id"] is None
    fields_in_task = assert_page(
        await api.post(FIELDS + "/query", headers=headers[0], json={"task_id": task}), headers[0]
    )
    assert len(fields_in_task["items"]) == 5
    archived = await api.post(
        f"/confidential-fields/{org_fields[0]['id']}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "archived": True},
    )
    assert archived.status_code == 200
    for path in (FIELDS, VALUES):
        visible = assert_page(
            await api.post(path + "/query", headers=headers[0], json={}), headers[0]
        )
        assert "alpha_0" not in {row["key"] for row in visible["items"]}
        all_rows = assert_page(
            await api.post(path + "/query", headers=headers[0], json={"archived": True}), headers[0]
        )
        assert {"alpha_0", "alpha_1"} <= {row["key"] for row in all_rows["items"]}


async def test_every_read_never_decrypts_and_history_is_bounded(api, headers, monkeypatch):
    field = await create_field(api, headers[0])
    values = [await legacy_write(api, headers[0], field, f"{SECRET}-{i}") for i in range(4)]

    def fail_decrypt(self, value):
        pytest.fail("Confidential management reads must not decrypt any stored value")

    monkeypatch.setattr(Secrets, "decrypt", fail_decrypt)
    for path in (FIELDS + "/query", VALUES + "/query"):
        assert_page(await api.post(path, headers=headers[0], json={}), headers[0])
    path = f"{FIELDS}/{field['id']}/values/history/query"
    first = assert_page(
        await api.post(path, headers=headers[0], json={"limit": 2}), headers[0], limit=2
    )
    second = assert_page(
        await api.post(
            path, headers=headers[0], json={"limit": 2, "cursor": first["data"]["next_cursor"]}
        ),
        headers[0],
        limit=2,
    )
    assert [row["value_id"] for row in first["items"] + second["items"]] == [
        row["value_id"] for row in reversed(values)
    ]
    assert [row["version"] for row in first["items"] + second["items"]] == [4, 3, 2, 1]


async def test_exact_field_filter_hydrates_beyond_first_prefix_page(api, headers):
    fields = [
        await create_field(api, headers[0], f"shared_{index:02}", label="Shared editor label")
        for index in range(30)
    ]
    target = fields[-1]
    saved = await write(api, headers[0], target)
    foreign = await create_field(api, headers[1], "shared_29", label="Shared editor label")
    foreign_task = await new_task(api, headers[1])
    for path, identifier in ((FIELDS, "id"), (VALUES, "field_id")):
        prefix = assert_page(
            await api.post(path + "/query", headers=headers[0], json={"q": "shared"}),
            headers[0],
        )
        assert len(prefix["items"]) == 25 and prefix["data"]["has_more"]
        assert target["id"] not in {row[identifier] for row in prefix["items"]}
        exact = assert_page(
            await api.post(path + "/query", headers=headers[0], json={"field_id": target["id"]}),
            headers[0],
        )
        assert [row[identifier] for row in exact["items"]] == [target["id"]]
        assert not exact["data"]["has_more"]
        if path == VALUES:
            assert exact["items"][0]["value_id"] == saved["value_id"]
        for absent in (foreign["id"], str(uuid4())):
            page = assert_page(
                await api.post(path + "/query", headers=headers[0], json={"field_id": absent}),
                headers[0],
            )
            assert page["items"] == [] and not page["data"]["has_more"]
        denied = await api.post(
            path + "/query",
            headers=headers[0],
            json={"field_id": target["id"], "task_id": foreign_task},
        )
        assert denied.status_code == 404 and denied.json()["data"]["error"]["code"] == "not_found"
        rebound = await api.post(
            path + "/query",
            headers=headers[0],
            json={"q": "shared", "field_id": target["id"], "cursor": prefix["data"]["next_cursor"]},
        )
        assert rebound.status_code == 400
        assert rebound.json()["data"]["error"]["code"] == "management_cursor_invalid"


@pytest.mark.parametrize("suffix,body", [("values/history/query", {}), ("values", checked())])
async def test_each_field_route_masks_foreign_and_nonexistent(suffix, body, api, headers):
    foreign = await create_field(api, headers[1])
    replies = [
        await api.post(f"{FIELDS}/{root}/{suffix}", headers=headers[0], json=body)
        for root in (foreign["id"], str(uuid4()))
    ]
    assert [reply.status_code for reply in replies] == [404, 404]
    assert replies[0].json()["data"]["error"] == replies[1].json()["data"]["error"]
    assert all(SECRET not in reply.text for reply in replies)


async def test_exact_task_owners_wrong_scopes_and_task_visibility(
    api, headers, tenants, admin_engine
):
    field = await create_field(api, headers[0], "task_secret", scope="task")
    org_field = await create_field(api, headers[0], "org_secret")
    tasks = [await new_task(api, headers[0]) for _ in range(2)]
    first = await write(api, headers[0], field, task_id=tasks[0])
    second = await write(api, headers[0], field, task_id=tasks[1])
    assert first["version"] == second["version"] == 1
    for task, value in zip(tasks, (first, second), strict=True):
        page = assert_page(
            await api.post(
                f"{FIELDS}/{field['id']}/values/history/query",
                headers=headers[0],
                json={"task_id": task},
            ),
            headers[0],
        )
        assert [row["value_id"] for row in page["items"]] == [value["value_id"]]
    for target, task, code in (
        (field, None, "confidential_task_required"),
        (org_field, tasks[0], "confidential_task_not_allowed"),
    ):
        for suffix, body in (
            ("values/history/query", {"task_id": task}),
            ("values", checked(task_id=task)),
        ):
            reply = await api.post(
                f"{FIELDS}/{target['id']}/{suffix}", headers=headers[0], json=body
            )
            assert reply.status_code == 400, reply.text
            assert reply.json()["data"]["error"]["code"] == code
    foreign_task = await new_task(api, headers[1])
    _, stranger = await person(api, admin_engine, tenants["orgs"][0])
    for auth, task in (
        (headers[0], foreign_task),
        (headers[0], str(uuid4())),
        (stranger, tasks[0]),
    ):
        for path, body in (
            (FIELDS + "/query", {"task_id": task}),
            (VALUES + "/query", {"task_id": task}),
            (f"{FIELDS}/{field['id']}/values/history/query", {"task_id": task}),
            (f"{FIELDS}/{field['id']}/values", checked(task_id=task)),
        ):
            reply = await api.post(path, headers=auth, json=body)
            assert reply.status_code == 404, reply.text
            assert reply.json()["data"]["error"]["code"] == "not_found"


async def test_required_null_cas_field_cas_archive_and_legacy_append(api, headers):
    field = await create_field(api, headers[0])
    path = f"{FIELDS}/{field['id']}/values"
    missing = checked()
    del missing["expected_value_id"]
    assert (await api.post(path, headers=headers[0], json=missing)).status_code == 422
    first = await write(api, headers[0], field)
    for expected in (None, str(uuid4())):
        reply = await api.post(path, headers=headers[0], json=checked(expected=expected))
        assert reply.status_code == 409
        assert reply.json()["data"]["error"]["code"] == "confidential_value_conflict"
        assert_safe(reply)
    legacy = await legacy_write(api, headers[0], field)
    assert legacy["version"] == 2
    assert (
        await api.post(path, headers=headers[0], json=checked(expected=first["value_id"]))
    ).status_code == 409
    renamed = await api.post(
        f"/confidential-fields/{field['id']}/revisions",
        headers=headers[0],
        json={"expected_revision": 1, "label": "Synthetic renamed field"},
    )
    assert renamed.status_code == 200
    stale = await api.post(path, headers=headers[0], json=checked(expected=legacy["value_id"]))
    assert stale.status_code == 409 and stale.json()["data"]["error"]["code"] == "revision_conflict"
    third = await write(api, headers[0], field, expected=legacy["value_id"], revision=2)
    assert third["version"] == 3
    assert (
        await api.post(
            f"/confidential-fields/{field['id']}/revisions",
            headers=headers[0],
            json={"expected_revision": 2, "archived": True},
        )
    ).status_code == 200
    refused = await api.post(
        path, headers=headers[0], json=checked(expected=third["value_id"], revision=3)
    )
    assert (
        refused.status_code == 409
        and refused.json()["data"]["error"]["code"] == "confidential_field_archived"
    )
    history = assert_page(
        await api.post(f"{FIELDS}/{field['id']}/values/history/query", headers=headers[0], json={}),
        headers[0],
    )
    assert [row["version"] for row in history["items"]] == [3, 2, 1]
    for key, value in (("key", "changed_key"), ("kind", "amount"), ("scope", "task")):
        assert (
            await api.post(
                f"/confidential-fields/{field['id']}/revisions",
                headers=headers[0],
                json={"expected_revision": 3, key: value},
            )
        ).status_code == 422


@pytest.mark.parametrize("existing", [False, True])
async def test_concurrent_checked_writers_commit_one_value_and_audit(
    existing, api, headers, application
):
    field = await create_field(api, headers[0])
    previous = await write(api, headers[0], field) if existing else None
    body = checked(expected=previous["value_id"] if previous else None)
    replies = await asyncio.gather(
        *(
            api.post(f"{FIELDS}/{field['id']}/values", headers=headers[0], json=body)
            for _ in range(2)
        )
    )
    assert sorted(reply.status_code for reply in replies) == [200, 409]
    assert all(SECRET not in reply.text for reply in replies)
    async with application.state.db.transaction(UUID(headers[0]["X-Org-Id"])) as session:
        values = (
            await session.scalars(
                select(ConfidentialValue).where(ConfidentialValue.field_id == UUID(field["id"]))
            )
        ).all()
        audits = (
            await session.scalars(
                select(AuditLog).where(
                    AuditLog.object_id == UUID(field["id"]),
                    AuditLog.action == "confidential.value.set",
                )
            )
        ).all()
        assert len(values) == len(audits) == (2 if existing else 1)
        assert {row.details["value_id"] for row in audits} == {str(row.id) for row in values}
        assert SECRET not in json.dumps([row.details for row in audits])
        assert all(SECRET not in row.encrypted_value for row in values)


@pytest.mark.parametrize(
    "role,allowed", [("admin", True), ("bidder", True), ("technical", False), ("viewer", False)]
)
async def test_live_role_reads_write_and_reveal_gates(
    role, allowed, api, headers, tenants, admin_engine
):
    field = await create_field(api, headers[0])
    value = await write(api, headers[0], field)
    with Session(admin_engine) as session, session.begin():
        session.scalar(
            select(Membership).where(Membership.org_id == tenants["orgs"][0])
        ).role = role
    for path in (
        FIELDS + "/query",
        VALUES + "/query",
        f"{FIELDS}/{field['id']}/values/history/query",
    ):
        assert_page(await api.post(path, headers=headers[0], json={}), headers[0])
    assert (
        await api.post(
            f"{FIELDS}/{field['id']}/values",
            headers=headers[0],
            json=checked(expected=value["value_id"]),
        )
    ).status_code == (200 if allowed else 403)
    reveal = await api.post(f"/confidential-values/{value['value_id']}/reveal", headers=headers[0])
    assert reveal.status_code == (200 if allowed else 403)
    if allowed:
        assert reveal.json()["data"]["value"] == SECRET


async def test_tokens_read_only_and_live_revocation(api, headers, tenants, admin_engine):
    field = await create_field(api, headers[0])
    await create_field(api, headers[0], "other_field")
    issued = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Synthetic metadata token",
            "scopes": ["confidential:read"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert issued.status_code == 200
    auth = {**headers[0], "Authorization": "Bearer " + issued.json()["data"]["token"]}
    page = assert_page(
        await api.post(FIELDS + "/query", headers=auth, json={"limit": 1}), auth, limit=1
    )
    for path in (VALUES + "/query", f"{FIELDS}/{field['id']}/values/history/query"):
        assert_page(await api.post(path, headers=auth, json={}), auth)
    assert (
        await api.post(f"{FIELDS}/{field['id']}/values", headers=auth, json=checked())
    ).status_code == 403
    task = await new_task(api, headers[0])
    assert (
        await api.post(VALUES + "/query", headers=auth, json={"task_id": task})
    ).status_code == 403
    with Session(admin_engine) as session, session.begin():
        token = session.scalar(select(ApiToken).where(ApiToken.org_id == tenants["orgs"][0]))
        token.revoked = True
    assert (
        await api.post(
            FIELDS + "/query", headers=auth, json={"cursor": page["data"]["next_cursor"]}
        )
    ).status_code == 401


async def test_cursors_filters_actor_parent_expiry_and_live_authority(
    api, headers, application, tenants, admin_engine
):
    field = await create_field(api, headers[0])
    for i in range(3):
        await create_field(api, headers[0], f"other_{i}")
        await legacy_write(api, headers[0], field)
    page = assert_page(
        await api.post(FIELDS + "/query", headers=headers[0], json={"limit": 1}),
        headers[0],
        limit=1,
    )
    cursor = page["data"]["next_cursor"]
    for auth, path, body in (
        (headers[1], FIELDS, {}),
        (headers[0], VALUES, {}),
        (headers[0], FIELDS, {"q": "other"}),
        (headers[0], FIELDS, {"archived": True}),
    ):
        reply = await api.post(path + "/query", headers=auth, json={"cursor": cursor, **body})
        assert (
            reply.status_code == 400
            and reply.json()["data"]["error"]["code"] == "management_cursor_invalid"
        )
    assert (
        await api.post(FIELDS + "/query", headers=headers[0], json={"cursor": "broken"})
    ).status_code == 400
    payload = json.loads(application.state.crypto.cipher.decrypt(cursor.encode()))
    payload["exp"] = int(time.time()) - 1
    expired = application.state.crypto.cipher.encrypt(json.dumps(payload).encode()).decode()
    assert (
        await api.post(FIELDS + "/query", headers=headers[0], json={"cursor": expired})
    ).status_code == 409
    history = assert_page(
        await api.post(
            f"{FIELDS}/{field['id']}/values/history/query", headers=headers[0], json={"limit": 1}
        ),
        headers[0],
        limit=1,
    )
    other = await create_field(api, headers[0], "third_field")
    assert (
        await api.post(
            f"{FIELDS}/{other['id']}/values/history/query",
            headers=headers[0],
            json={"cursor": history["data"]["next_cursor"]},
        )
    ).status_code == 400
    with Session(admin_engine) as session, session.begin():
        session.scalar(
            select(Membership).where(Membership.org_id == tenants["orgs"][0])
        ).role = "viewer"
    assert (
        await api.post(FIELDS + "/query", headers=headers[0], json={"cursor": cursor})
    ).status_code == 400


@pytest.mark.parametrize("disabled,status", [("user", 401), ("membership", 404), ("org", 403)])
async def test_every_read_reauthenticates_live_authority(
    disabled, status, api, headers, tenants, admin_engine
):
    field = await create_field(api, headers[0])
    with Session(admin_engine) as session, session.begin():
        if disabled == "user":
            session.get(User, tenants["users"][0]).active = False
        elif disabled == "org":
            session.get(Org, tenants["orgs"][0]).active = False
        else:
            session.scalar(
                select(Membership).where(Membership.org_id == tenants["orgs"][0])
            ).active = False
    for path in (
        FIELDS + "/query",
        VALUES + "/query",
        f"{FIELDS}/{field['id']}/values/history/query",
    ):
        assert (await api.post(path, headers=headers[0], json={})).status_code == status


async def test_removed_task_member_cursor_and_archived_task_write(
    api, headers, tenants, admin_engine
):
    task = await new_task(api, headers[0])
    field = await create_field(api, headers[0], scope="task")
    for _ in range(2):
        await legacy_write(api, headers[0], field, task_id=task)
    user, auth = await person(api, admin_engine, tenants["orgs"][0])
    assert (await add_member(api, headers[0], task, user, 1)).status_code == 200
    path = f"{FIELDS}/{field['id']}/values/history/query"
    page = assert_page(
        await api.post(path, headers=auth, json={"task_id": task, "limit": 1}), auth, limit=1
    )
    removed = await api.post(
        f"/tasks/{task}/members/{user}/remove",
        headers=headers[0],
        json={"expected_revision": 2, "reason": "Synthetic removal"},
    )
    assert removed.status_code == 200, removed.text
    assert (
        await api.post(
            path, headers=auth, json={"task_id": task, "cursor": page["data"]["next_cursor"]}
        )
    ).status_code == 404
    archived = await api.post(
        f"/tasks/{task}/archive",
        headers=headers[0],
        json={"expected_revision": 3, "reason": "Synthetic archive"},
    )
    assert archived.status_code == 200, archived.text
    latest = assert_page(
        await api.post(path, headers=headers[0], json={"task_id": task}), headers[0]
    )["items"][0]
    refused = await api.post(
        f"{FIELDS}/{field['id']}/values",
        headers=headers[0],
        json=checked(task_id=task, expected=latest["value_id"]),
    )
    assert refused.status_code == 409 and refused.json()["data"]["error"]["code"] == "task_archived"
