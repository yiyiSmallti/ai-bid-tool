"""Failure-first PostgreSQL/API acceptance for org memory management.

Prevent cross-org pages/cursors, stale authority and revision approvals, leaked
task provenance or readable reasons, formerly-effective candidate edits, expiry
rewrites, conflict-key races, unsafe recovery advice and audit-only partial saves.
Existing memory suites cover all eight tables, feedback recording and worker fences.
"""

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.models.entities import AuditLog, Membership, User
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

BASE = "/v4/management/memories"
CONTENT = {
    "kind": "rule",
    "conflict_key": "delivery.source",
    "text": "Synthetic delivery guidance",
    "tags": ["delivery", "review"],
}


async def create(api, headers, *, content=None, **extra):
    reply = await api.post(
        "/v4/memories",
        headers=headers,
        json={"target": {"scope": "org"}, "content": content or CONTENT, **extra},
    )
    assert reply.status_code == 200, reply.text
    memory = reply.json()["data"]["memory"]
    assert memory["effective_status"] == memory["current"]["status"] == "candidate"
    assert memory["current"]["confirmed_by"] is None
    return memory


async def decision(api, headers, identifier, revision=1, action="approve", reason="Reviewed"):
    return await api.post(
        f"/v4/memories/{identifier}/decisions",
        headers=headers,
        json={"expected_revision": revision, "action": action, "reason": reason},
    )


async def revise(api, headers, identifier, revision, **content):
    return await api.put(
        f"/v4/memories/{identifier}",
        headers=headers,
        json={"expected_revision": revision, "content": {**CONTENT, **content}},
    )


def set_role(admin_engine, tenants, role):
    with Session(admin_engine) as session, session.begin():
        session.scalar(
            select(Membership).where(
                Membership.org_id == tenants["orgs"][0],
                Membership.user_id == tenants["users"][0],
            )
        ).role = role


async def test_bounded_search_exact_history_and_two_org_pages(api, headers, tenants):
    roots = [
        await create(api, headers[0], content={**CONTENT, "text": f"Synthetic delivery {i}"})
        for i in range(4)
    ]
    await create(api, headers[1])
    reply = await api.post(BASE + "/query", headers=headers[0], json={"q": "DEL", "limit": 2})
    assert reply.status_code == 200, reply.text
    page = reply.json()
    assert set(page) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    assert set(page["data"]) == {"org_id", "as_of", "returned", "next_cursor", "has_more"}
    assert page["data"]["returned"] == 2 and page["data"]["has_more"]
    assert len(reply.content) <= 256 * 1024
    assert all(row["org_id"] == str(tenants["orgs"][0]) for row in page["items"])
    second = await api.post(
        BASE + "/query",
        headers=headers[0],
        json={"q": "del", "limit": 2, "cursor": page["data"]["next_cursor"]},
    )
    assert second.status_code == 200, second.text
    assert not second.json()["data"]["has_more"]
    combined = page["items"] + second.json()["items"]
    assert len({row["id"] for row in combined}) == 4
    assert [row["id"] for row in combined] == [row["id"] for row in reversed(roots)]
    for body in (
        {"kind": "rule", "status": "candidate", "tags": ["REVIEW", "delivery"]},
        {"q": "delivery.so"},
        {"q": "rev"},
    ):
        assert (await api.post(BASE + "/query", headers=headers[0], json=body)).json()["data"][
            "returned"
        ] == 4
    for body in ({"tags": ["delivery", "absent"]}, {"q": "delivery | absent"}, {"q": "%_"}):
        assert (await api.post(BASE + "/query", headers=headers[0], json=body)).json()[
            "items"
        ] == []
    root = roots[0]
    assert (
        await revise(api, headers[0], root["id"], 1, text="Revised exact content")
    ).status_code == 200
    detail = await api.get(f"{BASE}/{root['id']}", headers=headers[0], params={"revision": 1})
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["current_revision"] == 2
    assert data["current_effective_status"] == "candidate"
    assert data["memory"]["current"]["id"] == root["current"]["id"]
    assert data["revised_at"] and data["revised_by"] == str(tenants["users"][0])
    history = await api.post(
        f"{BASE}/{root['id']}/history/query", headers=headers[0], json={"limit": 1}
    )
    assert history.status_code == 200, history.text
    assert history.json()["items"][0]["revision"] == 2
    older = await api.post(
        f"{BASE}/{root['id']}/history/query",
        headers=headers[0],
        json={"limit": 1, "cursor": history.json()["data"]["next_cursor"]},
    )
    assert older.status_code == 200
    assert older.json()["items"][0]["revision"] == 1


@pytest.mark.parametrize("history", [False, True])
async def test_management_object_reads_uniformly_hide_foreign_missing(api, headers, history):
    foreign = await create(api, headers[1])
    replies = []
    for identifier in (foreign["id"], str(uuid4())):
        replies.append(
            await api.post(f"{BASE}/{identifier}/history/query", headers=headers[0], json={})
            if history
            else await api.get(f"{BASE}/{identifier}", headers=headers[0], params={"revision": 1})
        )
    assert [reply.status_code for reply in replies] == [404, 404]
    assert replies[0].json()["data"]["error"] == replies[1].json()["data"]["error"]


async def test_all_management_reads_require_credentials_and_org_context(api, headers):
    memory = await create(api, headers[0])
    task = await api.post("/v4/tasks", headers=headers[0], json={"name": "Context boundary"})
    assert task.status_code == 200
    task_id = task.json()["data"]["id"]
    for method, path, body in (
        ("POST", BASE + "/query", {}),
        ("GET", f"{BASE}/{memory['id']}", None),
        ("POST", f"{BASE}/{memory['id']}/history/query", {}),
        ("GET", f"/v4/tasks/{task_id}/memory-feedback?management=true", None),
    ):
        unauthenticated = await api.request(
            method, path, headers={"X-Org-Id": headers[0]["X-Org-Id"]}, json=body
        )
        assert unauthenticated.status_code == 401
        absent_org = await api.request(
            method, path, headers={"Authorization": headers[0]["Authorization"]}, json=body
        )
        assert absent_org.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"limit": 0},
        {"limit": 101},
        {"q": " "},
        {"q": "x" * 201},
        {"scope": "project"},
        {"product_id": str(uuid4())},
        {"tags": ["same", " SAME "]},
        {"tags": [str(i) for i in range(21)]},
        {"cursor": "x" * 2049},
    ],
)
async def test_query_input_bounds(api, headers, body):
    assert (await api.post(BASE + "/query", headers=headers[0], json=body)).status_code == 422


async def test_cursor_is_bound_to_actor_filters_authority_and_time(
    api, headers, application, tenants, admin_engine
):
    for _ in range(3):
        await create(api, headers[0])
    page = await api.post(BASE + "/query", headers=headers[0], json={"limit": 1})
    cursor = page.json()["data"]["next_cursor"]
    for hdr, body in (
        (headers[1], {"limit": 1, "cursor": cursor}),
        (headers[0], {"limit": 1, "cursor": cursor, "tags": ["delivery"]}),
        (headers[0], {"limit": 1, "cursor": cursor, "expiry": "unexpired"}),
        (headers[0], {"cursor": "broken"}),
    ):
        response = await api.post(BASE + "/query", headers=hdr, json=body)
        assert response.status_code == 400, response.text
        assert response.json()["data"]["error"]["code"] == "management_cursor_invalid"
    payload = json.loads(application.state.crypto.cipher.decrypt(cursor.encode()))
    payload["exp"] = int(time.time()) - 1
    expired = application.state.crypto.cipher.encrypt(json.dumps(payload).encode()).decode()
    response = await api.post(
        BASE + "/query", headers=headers[0], json={"limit": 1, "cursor": expired}
    )
    assert response.status_code == 409
    assert response.json()["data"]["error"]["code"] == "management_cursor_expired"
    set_role(admin_engine, tenants, "viewer")
    assert (
        await api.post(BASE + "/query", headers=headers[0], json={"limit": 1, "cursor": cursor})
    ).status_code == 400


async def test_complete_envelope_budget_keeps_exact_continuation(api, headers):
    bulky = {
        **CONTENT,
        "text": "Guidance " + "x" * 1991,
        "tags": [f"tag{i:02}" + "x" * 35 for i in range(20)],
    }
    expected = {(await create(api, headers[0], content=bulky))["id"] for _ in range(80)}
    seen, cursor, page_count = set(), None, 0
    while True:
        response = await api.post(
            BASE + "/query", headers=headers[0], json={"limit": 100, "cursor": cursor}
        )
        assert response.status_code == 200, response.text
        assert len(response.content) <= 256 * 1024
        output = response.json()
        rows = {row["id"] for row in output["items"]}
        assert rows and not seen & rows
        seen.update(rows)
        assert all(row["current"]["content"] == bulky for row in output["items"])
        page_count += 1
        cursor = output["data"]["next_cursor"]
        if cursor is None:
            break
    assert seen == expected and page_count >= 2


@pytest.mark.parametrize("role", ["admin", "bidder", "technical", "viewer"])
async def test_memory_role_matrix_and_direct_http_actions(
    api, headers, tenants, admin_engine, role
):
    memory = await create(api, headers[0])
    set_role(admin_engine, tenants, role)
    assert (await api.post(BASE + "/query", headers=headers[0], json={})).status_code == 200
    assert (await api.get(f"{BASE}/{memory['id']}", headers=headers[0])).status_code == 200
    assert (
        await api.post(f"{BASE}/{memory['id']}/history/query", headers=headers[0], json={})
    ).status_code == 200
    created = await api.post(
        "/v4/memories", headers=headers[0], json={"target": {"scope": "org"}, "content": CONTENT}
    )
    assert created.status_code == (403 if role == "viewer" else 200)
    edited = await revise(api, headers[0], memory["id"], 1)
    assert edited.status_code == (403 if role == "viewer" else 200)
    revision = 1 if role == "viewer" else 2
    reviewed = await decision(api, headers[0], memory["id"], revision)
    assert reviewed.status_code == (200 if role == "admin" else 403)
    disabled = await api.post(
        f"/v4/memories/{memory['id']}/disable",
        headers=headers[0],
        json={"expected_revision": revision + (role == "admin"), "reason": "Retire"},
    )
    assert disabled.status_code == (200 if role == "admin" else 403)
    deleted_page = await api.post(
        BASE + "/query", headers=headers[0], json={"include_deleted": True}
    )
    assert deleted_page.status_code == (200 if role == "admin" else 403)


async def token_headers(api, headers, name):
    reply = await api.post(
        "/tokens",
        headers=headers,
        json={
            "name": name,
            "scopes": ["memory:read", "memory:write", "memory:retrieve"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert reply.status_code == 200, reply.text
    return {**headers, "Authorization": "Bearer " + reply.json()["data"]["token"]}


async def test_token_ownership_never_effective_fence_and_human_only_actions(api, headers):
    first = await token_headers(api, headers[0], "memory-proposer-one")
    second = await token_headers(api, headers[0], "memory-proposer-two")
    memory = await create(api, first)
    assert (await api.post(BASE + "/query", headers=first, json={})).status_code == 200
    assert (await revise(api, second, memory["id"], 1)).status_code == 404
    assert (await revise(api, first, memory["id"], 1)).status_code == 200
    assert (await decision(api, first, memory["id"], 2)).status_code == 403
    assert (
        await api.post(BASE + "/query", headers=first, json={"include_deleted": True})
    ).status_code == 403
    assert (await decision(api, headers[0], memory["id"], 2)).status_code == 200
    assert (await revise(api, headers[0], memory["id"], 3)).status_code == 200
    assert (await revise(api, first, memory["id"], 4)).status_code == 404
    for path, body in (
        (f"/v4/memories/{memory['id']}/disable", {"expected_revision": 4, "reason": "Retire"}),
        (
            f"/v4/memories/{memory['id']}/decisions",
            {"expected_revision": 4, "action": "reject", "reason": "Reviewed"},
        ),
    ):
        assert (await api.post(path, headers=first, json=body)).status_code == 403


async def test_reject_reactivation_withdrawal_and_reason_hash_only(
    api, headers, tenants, admin_engine
):
    memory = await create(api, headers[0])
    reason = "Synthetic review reason retained only by digest"
    rejected = await decision(api, headers[0], memory["id"], action="reject", reason=reason)
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["memory"]["effective_status"] == "disabled"
    assert (await decision(api, headers[0], memory["id"], 2)).status_code == 409
    assert (await revise(api, headers[0], memory["id"], 2)).status_code == 200
    assert (await decision(api, headers[0], memory["id"], 3)).status_code == 200
    assert (
        await revise(api, headers[0], memory["id"], 4, text="Withdraw previous active content")
    ).status_code == 200
    shown = await api.get(f"{BASE}/{memory['id']}", headers=headers[0])
    assert shown.json()["data"]["memory"]["effective_status"] == "candidate"
    history = await api.post(f"{BASE}/{memory['id']}/history/query", headers=headers[0], json={})
    assert [row["status"] for row in history.json()["items"]] == [
        "candidate",
        "active",
        "candidate",
        "disabled",
        "candidate",
    ]
    rejected_revision = history.json()["items"][3]
    assert (
        rejected_revision["decision_reason_sha256"] == hashlib.sha256(reason.encode()).hexdigest()
    )
    assert reason not in history.text
    with Session(admin_engine) as session:
        audits = session.scalars(
            select(AuditLog).where(
                AuditLog.org_id == tenants["orgs"][0], AuditLog.object_id == UUID(memory["id"])
            )
        ).all()
        assert len(audits) == 5
        assert reason not in json.dumps([audit.details for audit in audits])
    set_role(admin_engine, tenants, "technical")
    assert (await revise(api, headers[0], memory["id"], 5)).status_code == 404


async def test_concurrent_exact_approval_and_conflict_key_require_human_resolution(api, headers):
    one = await create(api, headers[0])
    race = await asyncio.gather(*(decision(api, headers[0], one["id"]) for _ in range(2)))
    assert sorted(reply.status_code for reply in race) == [200, 409]
    two = await create(api, headers[0])
    blocked = await decision(api, headers[0], two["id"])
    assert blocked.status_code == 409
    assert blocked.json()["data"]["error"]["code"] == "memory_conflict_key"
    assert (
        await api.post(
            f"/v4/memories/{one['id']}/disable",
            headers=headers[0],
            json={"expected_revision": 2, "reason": "Superseded after human review"},
        )
    ).status_code == 200
    assert (await decision(api, headers[0], two["id"])).status_code == 200
    same_key_other_kind = await create(api, headers[0], content={**CONTENT, "kind": "preference"})
    assert (await decision(api, headers[0], same_key_other_kind["id"])).status_code == 200
    left = await create(api, headers[0], content={**CONTENT, "conflict_key": "race.exact"})
    right = await create(api, headers[0], content={**CONTENT, "conflict_key": "race.exact"})
    competing = await asyncio.gather(
        decision(api, headers[0], left["id"]), decision(api, headers[0], right["id"])
    )
    assert sorted(reply.status_code for reply in competing) == [200, 409]


async def test_server_expiry_preserves_stored_revision(api, headers, monkeypatch):
    from app.memory import crud, management

    expires = datetime.now(UTC) + timedelta(minutes=10)
    memory = await create(api, headers[0], expires_at=expires.isoformat())
    assert (await decision(api, headers[0], memory["id"])).status_code == 200

    class FutureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return (expires + timedelta(minutes=1)).astimezone(tz)

    monkeypatch.setattr(crud, "datetime", FutureClock)
    monkeypatch.setattr(management, "datetime", FutureClock)
    expired = await api.post(
        BASE + "/query", headers=headers[0], json={"status": "active", "expiry": "expired"}
    )
    assert expired.status_code == 200, expired.text
    row = expired.json()["items"][0]
    assert row["current"]["status"] == "active" and row["effective_status"] == "expired"
    assert row["current"]["revision"] == 2
    unexpired = await api.post(BASE + "/query", headers=headers[0], json={"expiry": "unexpired"})
    assert unexpired.json()["items"] == []
    exact = await api.get(f"{BASE}/{memory['id']}", headers=headers[0])
    assert exact.json()["data"]["memory"]["effective_status"] == "expired"
    history = await api.post(f"{BASE}/{memory['id']}/history/query", headers=headers[0], json={})
    assert len(history.json()["items"]) == 2


async def test_tombstone_history_admin_gate_and_no_reactivation(
    api, headers, tenants, admin_engine
):
    memory = await create(api, headers[0])
    removed = await api.request(
        "DELETE",
        f"/v4/memories/{memory['id']}",
        headers=headers[0],
        json={"expected_revision": 1, "reason": "Retired"},
    )
    assert removed.status_code == 200, removed.text
    assert (await api.post(BASE + "/query", headers=headers[0], json={})).json()["items"] == []
    included = await api.post(BASE + "/query", headers=headers[0], json={"include_deleted": True})
    assert included.json()["items"][0]["effective_status"] == "deleted"
    history = await api.post(f"{BASE}/{memory['id']}/history/query", headers=headers[0], json={})
    assert history.status_code == 200 and history.json()["items"][0]["decision"] == "delete"
    assert (await revise(api, headers[0], memory["id"], 2)).status_code == 404
    set_role(admin_engine, tenants, "technical")
    assert (
        await api.post(BASE + "/query", headers=headers[0], json={"include_deleted": True})
    ).status_code == 403


@pytest.mark.parametrize("state", ["user", "membership"])
async def test_inactive_identity_cannot_read_or_write(api, headers, tenants, admin_engine, state):
    memory = await create(api, headers[0])
    with Session(admin_engine) as session, session.begin():
        if state == "user":
            session.get(User, tenants["users"][0]).active = False
        else:
            session.scalar(
                select(Membership).where(
                    Membership.org_id == tenants["orgs"][0],
                    Membership.user_id == tenants["users"][0],
                )
            ).active = False
    for method, path, body in (
        ("POST", BASE + "/query", {}),
        ("GET", f"{BASE}/{memory['id']}", None),
        ("POST", f"{BASE}/{memory['id']}/history/query", {}),
        ("PUT", f"/v4/memories/{memory['id']}", {"expected_revision": 1, "content": CONTENT}),
    ):
        assert (await api.request(method, path, headers=headers[0], json=body)).status_code in {
            401,
            404,
        }


async def test_sensitive_rejection_never_offers_redaction_bypass(api, headers):
    reply = await api.post(
        "/v4/memories",
        headers=headers[0],
        json={
            "target": {"scope": "org"},
            "content": {**CONTENT, "text": "银行账号 6222021234567890123"},
        },
    )
    assert reply.status_code == 422
    error = reply.json()["data"]["error"]
    assert error["code"] == "memory_sensitive_value"
    assert "6222021234567890123" not in reply.text
    assert not any(
        text in error["message"].casefold()
        for text in ("disable redaction", "redaction off", "关闭脱敏")
    )
    assert (await api.post(BASE + "/query", headers=headers[0], json={})).json()["items"] == []


async def test_same_org_inaccessible_task_provenance_is_redacted(
    api, headers, tenants, admin_engine
):
    from test_response_cards import login

    task = await api.post("/v4/tasks", headers=headers[0], json={"name": "Private source task"})
    assert task.status_code == 200, task.text
    task_id = task.json()["data"]["id"]
    memory = await create(api, headers[0], source={"task_id": task_id})
    with Session(admin_engine) as session, session.begin():
        session.add(
            Membership(org_id=tenants["orgs"][0], user_id=tenants["users"][1], role="technical")
        )
    same_org = await login(api, tenants["orgs"][0], "b")
    for path, method, body in (
        (BASE + "/query", "POST", {}),
        (f"{BASE}/{memory['id']}", "GET", None),
        (f"{BASE}/{memory['id']}/history/query", "POST", {}),
    ):
        reply = await api.request(method, path, headers=same_org, json=body)
        assert reply.status_code == 200, reply.text
        assert task_id not in reply.text
        if method == "GET":
            source = reply.json()["data"]["memory"]["current"]["source"]
        elif path.endswith("history/query"):
            source = reply.json()["items"][0]["source"]
        else:
            source = reply.json()["items"][0]["current"]["source"]
        assert source["origin"] == "human" and source["provenance_redacted"] is True
        assert source["task_id"] is None
    assert (await revise(api, same_org, memory["id"], 1)).status_code == 404
    assert (
        await api.get(f"/v4/tasks/{task_id}/memory-feedback?management=true", headers=same_org)
    ).status_code == 404
    assert (
        await api.post(
            f"/v4/tasks/{task_id}/memory-candidates",
            headers=same_org,
            json={"event_ids": [str(uuid4())]},
        )
    ).status_code == 404


async def test_mutation_audit_failure_rolls_back_new_revision(api, headers, monkeypatch):
    from app.core.errors import ServiceError
    from app.memory import crud

    memory = await create(api, headers[0])

    def unavailable(*args, **kwargs):
        raise ServiceError("synthetic_audit_failure", "Synthetic audit unavailable", 503, 3)

    monkeypatch.setattr(crud, "audit", unavailable)
    failed = await revise(api, headers[0], memory["id"], 1, text="Uncommitted content")
    assert failed.status_code == 503
    shown = await api.get(f"{BASE}/{memory['id']}", headers=headers[0])
    assert shown.json()["data"]["memory"]["current"] == memory["current"]
    history = await api.post(f"{BASE}/{memory['id']}/history/query", headers=headers[0], json={})
    assert len(history.json()["items"]) == 1


async def test_feedback_saved_dispatch_job_and_result_link(
    tenants, tmp_path, admin_engine, monkeypatch
):
    from app.models.entities import Job
    from app.models.memory import MemoryFeedbackEvent
    from test_memory_feedback import feedback_client
    from test_response_cards import card_action, require_action

    async with feedback_client(tenants, tmp_path, admin_engine) as (
        api,
        app,
        human,
        task,
        current,
        _,
    ):
        pending = await require_action(api, human, current[2]["card"], "submit")
        original = app.state.queue.enqueue

        async def unavailable(*args):
            raise OSError("Synthetic queue unavailable")

        monkeypatch.setattr(app.state.queue, "enqueue", unavailable)
        rejected = await card_action(
            api, human, pending, "reject", reason="Use firm delivery wording"
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["data"]["state"] == "rejected"
        assert any(
            warning.startswith("memory_candidate_dispatch_pending:")
            for warning in rejected.json()["warnings"]
        )
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            event = await session.scalar(select(MemoryFeedbackEvent))
            job = await session.scalar(select(Job).where(Job.kind == "memory_candidate"))
            assert event and job and job.status == "queued" and job.queue_id is None
            event_id, job_id = str(event.id), str(job.id)
        listed = await api.get(f"/v4/tasks/{task}/memory-feedback?management=true", headers=human)
        assert listed.status_code == 200, listed.text
        row = listed.json()["items"][0]
        assert row["id"] == event_id
        assert row["after_revision_id"] == rejected.json()["data"]["revision_id"]
        assert row["candidate_job_id"] == job_id and row["candidate_job_status"] == "queued"
        assert row["candidate_event_ids"] == [event_id] and row["candidate_memory_id"] is None
        assert "Use firm delivery wording" not in listed.text
        assert "encrypted_summary" not in listed.text
        monkeypatch.setattr(app.state.queue, "enqueue", original)
        retried = await api.post(
            f"/v4/tasks/{task}/memory-candidates",
            headers=human,
            json={"event_ids": row["candidate_event_ids"], "action": {"retry": True}},
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["data"]["job_id"] == job_id
        await app.state.processor(human["X-Org-Id"], job_id)
        completed = await api.get(
            f"/v4/tasks/{task}/memory-feedback?management=true", headers=human
        )
        outcome = completed.json()["items"][0]
        assert outcome["candidate_job_id"] == job_id
        assert outcome["candidate_job_status"] == "succeeded" and outcome["candidate_memory_id"]
        candidate = await api.get(f"{BASE}/{outcome['candidate_memory_id']}", headers=human)
        assert candidate.status_code == 200, candidate.text
        assert candidate.json()["data"]["memory"]["effective_status"] == "candidate"
        assert (
            candidate.json()["data"]["memory"]["current"]["source"]["feedback_event_id"] == event_id
        )
        for method, path, body in (
            ("PUT", f"/v4/tasks/{task}/memory-feedback/{event_id}", {"kind": "card_edited"}),
            ("POST", f"/v4/tasks/{task}/memory-feedback", {"kind": "card_rejected"}),
        ):
            assert (await api.request(method, path, headers=human, json=body)).status_code in {
                404,
                405,
            }
        invented = await api.post(
            f"/v4/tasks/{task}/memory-candidates", headers=human, json={"event_ids": [str(uuid4())]}
        )
        assert invented.status_code == 404
        from app.models.team_workflow import TaskWorkflow
        from app.schemas.team_workflow import WorkflowMutation
        from app.services.task_workflow import archive
        from task_fixtures import actor_context_async

        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            workflow = await session.scalar(
                select(TaskWorkflow).where(TaskWorkflow.task_id == UUID(task))
            )
            owner = await actor_context_async(session, tenants["orgs"][0], workflow.owner_user_id)
            await archive(
                session,
                owner,
                UUID(task),
                WorkflowMutation(
                    expected_revision=workflow.revision, reason="Synthetic archived task"
                ),
                app.state.processor.settings,
            )
        assert (
            await api.get(f"/v4/tasks/{task}/memory-feedback?management=true", headers=human)
        ).status_code == 200
        archived = await api.post(
            f"/v4/tasks/{task}/memory-candidates",
            headers=human,
            json={"event_ids": [event_id], "action": {"retry": True}},
        )
        assert archived.status_code == 409
        assert archived.json()["data"]["error"]["code"] == "task_archived"


@pytest.mark.parametrize("task_role", ["reviewer", "observer"])
async def test_feedback_task_read_roles_cannot_retry(tenants, tmp_path, admin_engine, task_role):
    from app.models.memory import MemoryFeedbackEvent
    from task_fixtures import add_member
    from test_memory_feedback import feedback_client
    from test_response_cards import require_action

    async with feedback_client(tenants, tmp_path, admin_engine) as (
        api,
        app,
        human,
        task,
        current,
        _,
    ):
        for slot in current[2:4]:
            pending = await require_action(api, human, slot["card"], "submit")
            await require_action(api, human, pending, "reject", reason="Use firm delivery wording")
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            events = list(await session.scalars(select(MemoryFeedbackEvent)))
            assert len(events) == 2
            event_id = str(events[0].id)
        page = await api.get(
            f"/v4/tasks/{task}/memory-feedback?management=true&limit=1", headers=human
        )
        assert page.status_code == 200, page.text
        cursor = page.json()["data"]["next_cursor"]
        assert cursor
        with Session(admin_engine) as session, session.begin():
            add_member(
                session,
                tenants["orgs"][0],
                UUID(task),
                tenants["users"][0],
                role=task_role,
                review_domains=["technical"] if task_role == "reviewer" else [],
            )
        stale = await api.get(
            f"/v4/tasks/{task}/memory-feedback",
            headers=human,
            params={"management": "true", "cursor": cursor, "limit": 1},
        )
        assert stale.status_code == 400
        assert stale.json()["data"]["error"]["code"] == "management_cursor_invalid"
        # The same management cursor on the legacy projection keeps its old error code.
        legacy = await api.get(
            f"/v4/tasks/{task}/memory-feedback",
            headers=human,
            params={"cursor": cursor, "limit": 1},
        )
        assert legacy.status_code == 400
        assert legacy.json()["data"]["error"]["code"] == "invalid_cursor"
        assert (
            await api.get(f"/v4/tasks/{task}/memory-feedback?management=true", headers=human)
        ).status_code == 200
        denied = await api.post(
            f"/v4/tasks/{task}/memory-candidates",
            headers=human,
            json={"event_ids": [event_id], "action": {"retry": True}},
        )
        assert denied.status_code == 403, denied.text


async def test_current_root_projection_excludes_history_and_tracks_decisions(
    api, headers, tenants, admin_engine
):
    """Search and filters follow the exact head while old approved content survives."""
    expires = datetime.now(UTC) + timedelta(hours=1)
    previous = {
        "kind": "rule",
        "conflict_key": "obsolete.word",
        "text": "Alphaonly guidance",
        "tags": ["oldtag"],
    }
    memory = await create(api, headers[0], content=previous, expires_at=expires.isoformat())

    def projection():
        with admin_engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT search_kind,search_status,search_tags,search_expires_at,search_vector::text FROM memories WHERE org_id=:org AND id=:id"
                ),
                {"org": tenants["orgs"][0], "id": memory["id"]},
            ).one()

    initial = projection()
    assert initial[:4] == ("rule", "candidate", ["oldtag"], expires)
    assert "alphaonly" in initial[4] and "oldtag" in initial[4]
    assert (await decision(api, headers[0], memory["id"])).status_code == 200
    assert projection()[:4] == ("rule", "active", ["oldtag"], expires)
    active = await api.post(
        BASE + "/query",
        headers=headers[0],
        json={"kind": "rule", "status": "active", "expiry": "unexpired", "tags": ["oldtag"]},
    )
    assert active.json()["items"][0]["id"] == memory["id"]
    replacement = {
        "kind": "preference",
        "conflict_key": "rewritten.key",
        "text": "Betacurrent guidance",
        "tags": ["newtag"],
    }
    updated = await api.put(
        f"/v4/memories/{memory['id']}",
        headers=headers[0],
        json={"expected_revision": 2, "content": replacement},
    )
    assert updated.status_code == 200, updated.text
    current = projection()
    assert current[:4] == ("preference", "candidate", ["newtag"], None)
    assert "betacurrent" in current[4] and "alphaonly" not in current[4]
    for body in (
        {"q": "alphaonly"},
        {"q": "obsolete.word"},
        {"q": "oldtag"},
        {"tags": ["oldtag"]},
        {"kind": "rule"},
        {"status": "active"},
        {"expiry": "expired"},
    ):
        reply = await api.post(BASE + "/query", headers=headers[0], json=body)
        assert reply.status_code == 200, reply.text
        assert reply.json()["items"] == []
    for body in (
        {"q": "betacurrent"},
        {"q": "rewritten.key"},
        {"q": "newtag"},
        {"kind": "preference", "status": "candidate", "tags": ["newtag"], "expiry": "unexpired"},
    ):
        reply = await api.post(BASE + "/query", headers=headers[0], json=body)
        assert reply.status_code == 200 and [row["id"] for row in reply.json()["items"]] == [
            memory["id"]
        ]
    exact = await api.get(f"{BASE}/{memory['id']}?revision=2", headers=headers[0])
    assert exact.status_code == 200
    assert exact.json()["data"]["memory"]["current"]["content"] == previous
    assert exact.json()["data"]["memory"]["current"]["status"] == "active"
    assert exact.json()["data"]["current_revision"] == 3
    assert exact.json()["data"]["current_effective_status"] == "candidate"
    rejected = await decision(api, headers[0], memory["id"], 3, action="reject")
    assert rejected.status_code == 200
    assert projection()[:4] == ("preference", "disabled", ["newtag"], None)
    disabled = await api.post(
        BASE + "/query", headers=headers[0], json={"kind": "preference", "status": "disabled"}
    )
    assert disabled.json()["items"][0]["id"] == memory["id"]


@pytest.mark.parametrize(
    "assignment",
    [
        "search_vector=to_tsvector('simple','inventedword')",
        "search_tags=ARRAY['inventedtag']::text[]",
        "search_kind='preference'",
        "search_status='active'",
        "search_expires_at=now()+interval '1 day'",
        "search_vector=(SELECT public.management_memory_vector(r.normalized_text,r.content,r.normalized_tags) FROM memory_revisions r WHERE r.org_id=memories.org_id AND r.memory_id=memories.id AND r.revision=1)",
    ],
)
async def test_raw_forged_or_stale_search_projection_is_rejected(
    api, headers, tenants, admin_engine, assignment
):
    memory = await create(
        api, headers[0], content={**CONTENT, "text": "Previously searchable content"}
    )
    updated = await revise(api, headers[0], memory["id"], 1, text="Currently searchable content")
    assert updated.status_code == 200
    with pytest.raises(DBAPIError) as rejected, admin_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE bid_app"))
        connection.execute(
            text("SELECT set_config('app.current_org',:org,true)"), {"org": str(tenants["orgs"][0])}
        )
        connection.execute(
            text(f"UPDATE memories SET {assignment} WHERE org_id=:org AND id=:id"),
            {"org": tenants["orgs"][0], "id": memory["id"]},
        )
    assert getattr(rejected.value.orig, "sqlstate", None) == "23514"
    shown = await api.get(f"{BASE}/{memory['id']}", headers=headers[0])
    assert shown.status_code == 200 and shown.json()["data"]["current_revision"] == 2
    assert (
        shown.json()["data"]["memory"]["current"]["content"]["text"]
        == "Currently searchable content"
    )
    assert (await api.post(BASE + "/query", headers=headers[0], json={"q": "currently"})).json()[
        "items"
    ][0]["id"] == memory["id"]
    assert (await api.post(BASE + "/query", headers=headers[0], json={"q": "previously"})).json()[
        "items"
    ] == []


@pytest.mark.parametrize(
    "assignment",
    [
        "id=id",
        "search_vector=search_vector,search_tags=search_tags,search_kind=search_kind,search_status=search_status,search_expires_at=search_expires_at",
        "revision=1,current_revision_id=:old_revision",
        "revision=revision+2",
        "deleted_at=now()",
    ],
)
async def test_projection_only_repair_path_cannot_bypass_root_history_guard(
    api, headers, tenants, admin_engine, assignment
):
    memory = await create(api, headers[0])
    assert (
        await revise(api, headers[0], memory["id"], 1, text="Current exact head")
    ).status_code == 200
    parameters = {
        "org": tenants["orgs"][0],
        "id": memory["id"],
        "old_revision": memory["current"]["id"],
    }
    with pytest.raises(DBAPIError) as rejected, admin_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE bid_app"))
        connection.execute(
            text("SELECT set_config('app.current_org',:org,true)"), {"org": str(tenants["orgs"][0])}
        )
        connection.execute(
            text(f"UPDATE memories SET {assignment} WHERE org_id=:org AND id=:id"), parameters
        )
    assert getattr(rejected.value.orig, "sqlstate", None) == "23514"
    history = await api.post(f"{BASE}/{memory['id']}/history/query", headers=headers[0], json={})
    assert history.status_code == 200 and [row["revision"] for row in history.json()["items"]] == [
        2,
        1,
    ]


async def test_identical_candidate_revision_keeps_projection_without_noop_root_write(
    api, headers, tenants, admin_engine
):
    """A real new revision may leave every indexed search field unchanged."""
    memory = await create(api, headers[0])
    with admin_engine.connect() as connection:
        before = connection.execute(
            text(
                "SELECT search_vector::text,search_tags,search_kind,search_status,search_expires_at "
                "FROM memories WHERE org_id=:org AND id=:id"
            ),
            {"org": tenants["orgs"][0], "id": memory["id"]},
        ).one()
    updated = await revise(api, headers[0], memory["id"], 1)
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["memory"]["current"]["revision"] == 2
    with admin_engine.connect() as connection:
        after = connection.execute(
            text(
                "SELECT search_vector::text,search_tags,search_kind,search_status,search_expires_at "
                "FROM memories WHERE org_id=:org AND id=:id"
            ),
            {"org": tenants["orgs"][0], "id": memory["id"]},
        ).one()
    assert after == before
    history = await api.post(f"{BASE}/{memory['id']}/history/query", headers=headers[0], json={})
    assert history.status_code == 200
    assert [row["revision"] for row in history.json()["items"]] == [2, 1]
