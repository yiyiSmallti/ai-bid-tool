"""Memory lifecycle at the authenticated API boundary."""

import pytest

CONTENT = {
    "kind": "rule",
    "conflict_key": "delivery.source",
    "text": "交货期以答疑为准",
    "tags": ["delivery"],
}


async def add(api, headers):
    reply = await api.post(
        "/memories", headers=headers, json={"target": {"scope": "org"}, "content": CONTENT}
    )
    assert reply.status_code == 200, reply.text
    memory = reply.json()["data"]["memory"]
    assert memory["effective_status"] == "candidate"
    assert memory["current"]["confirmed_by"] is None
    return memory


async def test_memory_lifecycle_exact_revision_and_cross_org(api, headers):
    memory = await add(api, headers[0])
    key = memory["id"]
    assert (await api.get(f"/memories/{key}", headers=headers[1])).status_code == 404
    assert (await api.get(f"/memories/{key}/history", headers=headers[1])).status_code == 404
    approve = {"expected_revision": 1, "action": "approve", "reason": "Verified applicability"}
    reply = await api.post(f"/memories/{key}/decisions", headers=headers[0], json=approve)
    assert reply.status_code == 200, reply.text
    assert reply.json()["data"]["memory"]["effective_status"] == "active"
    stale = await api.put(
        f"/memories/{key}", headers=headers[0], json={"expected_revision": 1, "content": CONTENT}
    )
    assert stale.status_code == 409
    update = await api.put(
        f"/memories/{key}", headers=headers[0], json={"expected_revision": 2, "content": CONTENT}
    )
    assert update.status_code == 200, update.text
    assert update.json()["data"]["memory"]["effective_status"] == "candidate"
    deletion = await api.request(
        "DELETE",
        f"/memories/{key}",
        headers=headers[0],
        json={"expected_revision": 3, "reason": "Retired"},
    )
    assert deletion.status_code == 200, deletion.text
    assert (await api.get(f"/memories/{key}", headers=headers[0])).status_code == 404
    history = await api.get(f"/memories/{key}/history", headers=headers[0])
    assert history.status_code == 200
    assert len(history.json()["items"]) == 4


@pytest.mark.parametrize(
    "extra",
    [
        {"org_id": "00000000-0000-0000-0000-000000000001"},
        {"status": "active"},
        {"confirmed_by": "00000000-0000-0000-0000-000000000001"},
    ],
)
async def test_memory_rejects_actor_state_injection(api, headers, extra):
    reply = await api.post(
        "/memories",
        headers=headers[0],
        json={"target": {"scope": "org"}, "content": CONTENT, **extra},
    )
    assert reply.status_code == 422


async def test_memory_conflict_and_cursor_boundaries(api, headers):
    first = await add(api, headers[0])
    second = await add(api, headers[0])
    decision = {"expected_revision": 1, "action": "approve", "reason": "Approved"}
    assert (
        await api.post(f"/memories/{first['id']}/decisions", headers=headers[0], json=decision)
    ).status_code == 200
    assert (
        await api.post(f"/memories/{second['id']}/decisions", headers=headers[0], json=decision)
    ).status_code == 409
    page = await api.get("/memories", headers=headers[0], params={"scope": "org", "limit": 1})
    cursor = page.json()["data"]["next_cursor"]
    assert cursor
    cross = await api.get(
        "/memories", headers=headers[1], params={"scope": "org", "cursor": cursor}
    )
    assert cross.status_code == 400
    changed = await api.get(
        "/memories",
        headers=headers[0],
        params={"scope": "org", "status": "candidate", "cursor": cursor},
    )
    assert changed.status_code == 400


async def test_unenabled_layers_explicit_and_sensitive_rejected(api, headers):
    reply = await api.post(
        "/memories",
        headers=headers[0],
        json={
            "target": {"scope": "project", "task_id": "00000000-0000-0000-0000-000000000001"},
            "content": CONTENT,
        },
    )
    assert reply.status_code == 403
    reply = await api.post(
        "/memories",
        headers=headers[0],
        json={
            "target": {"scope": "org"},
            "content": {**CONTENT, "text": "银行账号 6222021234567890123"},
        },
    )
    assert reply.status_code == 422


async def approved_rule(api, headers, key, text, tags=None):
    reply = await api.post(
        "/memories",
        headers=headers,
        json={
            "target": {"scope": "org"},
            "content": {"kind": "rule", "conflict_key": key, "text": text, "tags": tags or []},
        },
    )
    assert reply.status_code == 200, reply.text
    memory = reply.json()["data"]["memory"]
    reply = await api.post(
        f"/memories/{memory['id']}/decisions",
        headers=headers,
        json={"expected_revision": 1, "action": "approve", "reason": "Synthetic reviewed rule"},
    )
    assert reply.status_code == 200, reply.text
    return memory


async def retrieve_rules(api, headers, **overrides):
    body = {"org_id": headers["X-Org-Id"], "scopes": ["org"], "query": "交货期", **overrides}
    return await api.post("/memories/retrieve?preview=true", headers=headers, json=body)


async def test_memory_keyword_exact_tag_normalization_and_literal_like(api, headers):
    exact = await approved_rule(api, headers[0], "exact", "交货期", ["交货期"])
    keyword = await approved_rule(api, headers[0], "keyword", "答疑说明交货期规则")
    tagged = await approved_rule(api, headers[0], "tagged", "审阅商务响应", ["交货期"])
    reply = await retrieve_rules(api, headers[0], tags=["交货期"])
    assert reply.status_code == 200, reply.text
    hits = {item["memory"]["memory_id"]: item for item in reply.json()["items"]}
    assert hits[exact["id"]]["relevance"] == 115
    assert hits[keyword["id"]]["relevance"] == 10
    assert hits[tagged["id"]]["relevance"] == 5
    normalized = await approved_rule(api, headers[0], "normalized", "ＦＯＯ   Bar")
    reply = await retrieve_rules(api, headers[0], query="foo bar")
    assert reply.json()["items"][0]["memory"]["memory_id"] == normalized["id"]
    assert reply.json()["items"][0]["matched_by"] == ["exact", "keyword"]
    reply = await retrieve_rules(api, headers[0], query="%_")
    assert reply.json()["items"] == []
    other = await retrieve_rules(api, headers[1], query="交货期")
    assert other.json()["items"] == []


async def test_memory_retrieval_full_entries_top_k_and_context_budget(api, headers):
    for number in range(3):
        await approved_rule(api, headers[0], f"budget.{number}", f"交货期{number}" + "规则" * 650)
    reply = await retrieve_rules(api, headers[0], max_context_chars=2000)
    assert reply.status_code == 200, reply.text
    output = reply.json()
    assert len(output["items"]) == 1
    assert len(output["items"][0]["text"]) == 1304
    assert output["data"]["context_chars"] <= 2000
    assert {item["reason"] for item in output["data"]["omitted"]} == {"context_limit"}
    reply = await retrieve_rules(api, headers[0], top_k=1)
    assert len(reply.json()["items"]) == 1
    assert {item["reason"] for item in reply.json()["data"]["omitted"]} == {"top_k_limit"}


@pytest.mark.parametrize(
    "scope,target",
    [
        ("global", {}),
        ("user", {"user_id": "00000000-0000-0000-0000-000000000001"}),
        ("project", {"task_id": "00000000-0000-0000-0000-000000000001"}),
    ],
)
async def test_memory_disabled_scope_error_is_explicit(api, headers, scope, target):
    reply = await api.post(
        "/memories",
        headers=headers[0],
        json={
            "target": {"scope": scope, **target},
            "content": {**CONTENT, "kind": "preference" if scope == "user" else "rule"},
        },
    )
    assert reply.status_code == 403
    assert reply.json()["data"]["error"]["code"] == "memory_scope_unavailable"
    query = await api.get("/memories", headers=headers[0], params={"scope": scope, **target})
    assert query.status_code == 403
    assert query.json()["data"]["error"]["code"] == "memory_scope_unavailable"
    retrieval = await retrieve_rules(api, headers[0], scopes=[scope], **target)
    assert retrieval.status_code == 403
    assert retrieval.json()["data"]["error"]["code"] == "memory_scope_unavailable"


@pytest.mark.parametrize(
    "verb,suffix,body",
    [
        ("PUT", "", {"expected_revision": 1, "content": CONTENT}),
        ("DELETE", "", {"expected_revision": 1, "reason": "Reviewed"}),
        ("POST", "/disable", {"expected_revision": 1, "reason": "Reviewed"}),
        ("POST", "/decisions", {"expected_revision": 1, "action": "approve", "reason": "Reviewed"}),
    ],
)
async def test_all_memory_mutations_hide_other_org(api, headers, verb, suffix, body):
    memory = await add(api, headers[1])
    reply = await api.request(
        verb, f"/memories/{memory['id']}{suffix}", headers=headers[0], json=body
    )
    assert reply.status_code == 404


async def test_memory_retrieval_record_rechecks_org_and_list_hidden(api, headers):
    await approved_rule(api, headers[1], "b.private", "交货期")
    listed = await api.get("/memories", headers=headers[0], params={"scope": "org"})
    assert listed.json()["items"] == []
    saved = await api.post(
        "/memories/retrieve",
        headers=headers[1],
        json={"org_id": headers[1]["X-Org-Id"], "scopes": ["org"], "query": "交货期"},
    )
    assert saved.status_code == 200, saved.text
    identifier = saved.json()["data"]["retrieval_id"]
    assert (
        await api.get(f"/memory-retrievals/{identifier}", headers=headers[0])
    ).status_code == 404
    mismatch = await api.post(
        "/memories/retrieve?preview=true",
        headers=headers[0],
        json={"org_id": headers[1]["X-Org-Id"], "scopes": ["org"], "query": "交货期"},
    )
    assert mismatch.status_code == 404


async def test_memory_tokens_can_only_propose_and_edit_own_never_active(api, headers):
    from datetime import UTC, datetime, timedelta

    expiry = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    for scope in (
        "memory:approve",
        "memory:manage",
        "memory:eval:read",
        "memory:eval:review",
        "evidence:confirm",
        "export",
    ):
        reply = await api.post(
            "/tokens",
            headers=headers[0],
            json={"name": "memory-gate", "scopes": [scope], "expires_at": expiry},
        )
        assert reply.status_code == 403
    reply = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "memory-propose",
            "scopes": ["memory:read", "memory:write", "memory:retrieve"],
            "expires_at": expiry,
        },
    )
    assert reply.status_code == 200, reply.text
    token_headers = {**headers[0], "Authorization": "Bearer " + reply.json()["data"]["token"]}
    memory = await add(api, token_headers)
    identifier = memory["id"]
    updated = await api.put(
        f"/memories/{identifier}",
        headers=token_headers,
        json={"expected_revision": 1, "content": CONTENT},
    )
    assert updated.status_code == 200, updated.text
    decision = {"expected_revision": 2, "action": "approve", "reason": "Reviewed"}
    assert (
        await api.post(f"/memories/{identifier}/decisions", headers=token_headers, json=decision)
    ).status_code == 403
    assert (
        await api.post(f"/memories/{identifier}/decisions", headers=headers[0], json=decision)
    ).status_code == 200
    assert (
        await api.put(
            f"/memories/{identifier}",
            headers=headers[0],
            json={"expected_revision": 3, "content": CONTENT},
        )
    ).status_code == 200
    assert (
        await api.put(
            f"/memories/{identifier}",
            headers=token_headers,
            json={"expected_revision": 4, "content": CONTENT},
        )
    ).status_code == 404
    other = await add(api, headers[0])
    assert (
        await api.put(
            f"/memories/{other['id']}",
            headers=token_headers,
            json={"expected_revision": 1, "content": CONTENT},
        )
    ).status_code == 404


@pytest.mark.parametrize("actor_kind", ["token", "agent", "worker"])
async def test_internal_actor_cannot_borrow_human_approval(
    api, headers, tenants, application, actor_kind
):
    from uuid import UUID

    from app.core.errors import ServiceError
    from app.memory import crud
    from app.schemas.memory_contracts import MemoryDecision
    from app.services.auth import ROLE_SCOPES, Identity

    memory = await add(api, headers[0])
    actor = Identity(
        tenants["users"][0],
        tenants["orgs"][0],
        set(ROLE_SCOPES["admin"]),
        "admin",
        actor_kind=actor_kind,
    )
    async with application.state.db.transaction(actor.org_id) as session:
        with pytest.raises(ServiceError) as denied:
            await crud.decide_memory(
                session,
                actor,
                UUID(memory["id"]),
                MemoryDecision(expected_revision=1, action="approve", reason="Synthetic review"),
                application.state.processor.settings,
            )
        assert denied.value.status == 403
    after = await api.get(f"/memories/{memory['id']}", headers=headers[0])
    assert after.json()["data"]["memory"]["effective_status"] == "candidate"
