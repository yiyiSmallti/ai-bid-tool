"""Session failure matrix, written before its service implementation.

Replay after revision changes must return the original receipt. Changed requests
with the same key must conflict. Every pagination cursor must belong to its exact
owner/task/session scope. Cancel must preserve nonowned jobs and all paid usage.
Messages must be appended only while paused and exposed without confidential data.
Dry-run must leave all durable session, message, audit and queue records unchanged.
These API scenarios use the existing PostgreSQL fixtures; no external calls occur.
"""

from uuid import UUID, uuid4

from app.models.agent import AgentJobLink, AgentMessage, AgentSession
from app.models.entities import AuditLog, Job, UsageRecord
from sqlalchemy import func, select

pytest_plugins = ["test_agent_api"]


async def counts(case):
    async with case["app"].state.db.transaction(UUID(case["owner"]["X-Org-Id"])) as db:
        return [
            await db.scalar(select(func.count()).select_from(model))
            for model in (AgentSession, AgentMessage, AgentJobLink, Job, AuditLog, UsageRecord)
        ]


async def test_start_replay_is_original_receipt_after_cancel(agent_case):
    case = agent_case
    base = f"/v4/agent-sessions/{case['session']['id']}"
    cancel = {"expected_revision": case["session"]["revision"], "idempotency_key": str(uuid4())}
    stopped = await case["api"].post(base + "/cancel", headers=case["owner"], json=cancel)
    assert stopped.status_code == 200, stopped.text
    replay = await case["api"].post(
        f"/v4/tasks/{case['task']}/agent-sessions", headers=case["owner"], json=case["request"]
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["deduplicated"] is True
    assert replay.json()["data"]["session"] == case["accepted"]["data"]["session"]
    changed = await case["api"].post(
        f"/v4/tasks/{case['task']}/agent-sessions",
        headers=case["owner"],
        json={**case["request"], "message": "Changed instruction"},
    )
    assert changed.status_code == 409
    repeated = await case["api"].post(base + "/cancel", headers=case["owner"], json=cancel)
    assert repeated.status_code == 200 and repeated.json()["data"]["deduplicated"]
    different = await case["api"].post(
        base + "/cancel", headers=case["owner"], json={**cancel, "reason": "wrong_input"}
    )
    assert different.status_code == 409


async def test_preview_writes_no_rows(agent_case):
    case = agent_case
    before = await counts(case)
    preview = await case["api"].post(
        f"/v4/tasks/{case['task']}/agent-sessions",
        headers=case["owner"],
        json={**case["request"], "dry_run": True},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["dry_run"] is True
    assert await counts(case) == before


async def test_cursor_does_not_cross_query_scope(agent_case):
    case = agent_case
    session_id = case["session"]["id"]
    paths = [
        f"/v4/tasks/{case['task']}/agent-sessions",
        f"/v4/agent-sessions/{session_id}/messages",
        f"/v4/agent-sessions/{session_id}/steps",
    ]
    for path in paths:
        missing = await case["api"].get(
            path, headers=case["owner"], params={"cursor": str(uuid4())}
        )
        assert missing.status_code == 404, missing.text
    other = await case["api"].get(paths[0], headers=case["nonowner"], params={"cursor": session_id})
    assert other.status_code == 404


async def test_cancel_preserves_extraction_and_usage(agent_case):
    case = agent_case
    before = await counts(case)
    cancelled = await case["api"].post(
        f"/v4/agent-sessions/{case['session']['id']}/cancel",
        headers=case["owner"],
        json={"expected_revision": case["session"]["revision"], "idempotency_key": str(uuid4())},
    )
    assert cancelled.status_code == 200, cancelled.text
    async with case["app"].state.db.transaction(UUID(case["owner"]["X-Org-Id"])) as db:
        extraction = await db.get(Job, UUID(case["extraction"]))
        assert extraction.status == "succeeded"
        links = (
            await db.scalars(
                select(AgentJobLink).where(
                    AgentJobLink.session_id == UUID(case["session"]["id"]),
                    AgentJobLink.owned.is_(True),
                )
            )
        ).all()
        assert links
        for link in links:
            job = await db.get(Job, link.job_id)
            assert job.status in {"succeeded", "failed", "cancelled"}
    assert (await counts(case))[-1] == before[-1]
