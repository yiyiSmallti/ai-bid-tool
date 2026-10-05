"""HTTP integration for the B09 check interface and queue boundary."""

from contextlib import asynccontextmanager
from uuid import UUID

import httpx
import pytest
from app.api.check import create_router
from fastapi import FastAPI

IDENTIFIER = UUID("00000000-0000-0000-0000-000000000001")
IDENTIFIER_2 = UUID("00000000-0000-0000-0000-000000000002")
SHA = "a" * 64


class Queue:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def enqueue(self, org_id: str, job_id: str):
        self.calls.append((org_id, job_id))
        if self.fail:
            raise OSError("synthetic queue failure")
        return 7


class Database:
    @asynccontextmanager
    async def transaction(self, org_id):
        yield self

    async def get(self, model, key):
        return None


class Session:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class QueuedJob:
    def __init__(self):
        self.id = IDENTIFIER
        self.status = "queued"
        self.queue_id = None


@pytest.fixture
def interface(monkeypatch):
    from app.services import check

    calls = []

    async def submit(session, actor, task_id, body, storage, settings):
        calls.append(("run", task_id, body))
        if not body.dry_run:
            job = QueuedJob()
            return {
                "job_id": str(job.id),
                "status": job.status,
                "cached": False,
            }, job
        return (
            {
                "dry_run": True,
                "input": {
                    "org_id": actor.org_id,
                    "task_id": task_id,
                    "draft_id": body.draft_id,
                    "extraction_job_id": IDENTIFIER,
                    "document_id": IDENTIFIER,
                    "input_hash": SHA,
                    "draft_input_hash": SHA,
                    "assessment_date": body.assessment_date,
                    "scope": "confirmed_draft",
                },
                "selected_item_ids": [],
                "estimated_cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0},
                "estimated_charge": "0",
                "billing_currency": "CNY",
                "cost_basis": "known",
                "cost_basis_reason": "no_model_calls",
                "estimate_kind": "first_pass_upper_bound",
                "admission_blocker": None,
                "estimated_duration_ms": None,
                "provider_config_id": None,
                "provider_source": None,
                "platform_model_id": None,
                "model_revision": None,
                "model": None,
                "reasoning": None,
                "redaction_revision": 1,
                "redaction_rule_version": "v1",
                "redacted_counts": {},
                "max_charge": None,
                "mode": "rules",
                "rule_version": "v1",
                "prompt_version": None,
                "schema_version": "v1",
                "rules_applicable": 0,
                "semantic_items": 0,
                "gap_requirements": 0,
                "limitations": ["confirmed_draft_only"],
            },
            None,
        )

    async def listing(session, actor, task_id, storage, settings, *, cursor=None, limit=50):
        calls.append(("list", cursor, limit))
        return {"task_id": str(task_id), "total": 0, "next_cursor": None}, []

    async def showing(session, actor, report_id, storage, settings):
        calls.append(("show", report_id))
        return {
            "report": {
                "completion": "partial",
                "validity": "stale",
                "limitations": ["confirmed_draft_only"],
            },
            "coverage": [],
            "certificates": [],
        }, [{"id": str(IDENTIFIER_2)}]

    async def deciding(session, actor, report_id, finding_id, body, storage, settings):
        calls.append(("decide", report_id, finding_id, body))
        return {"finding": {"id": str(finding_id)}, "decision": {"action": body.action}}

    async def history(
        session,
        actor,
        report_id,
        finding_id,
        storage,
        settings,
        *,
        cursor=None,
        limit=50,
    ):
        calls.append(("history", cursor, limit))
        return {"task_id": str(IDENTIFIER), "total": 0, "next_cursor": None}, []

    monkeypatch.setattr(check, "submit_check", submit)
    monkeypatch.setattr(check, "list_checks", listing)
    monkeypatch.setattr(check, "show_check", showing)
    monkeypatch.setattr(check, "decide_finding", deciding)
    monkeypatch.setattr(check, "decision_history", history)

    actor = type("Actor", (), {"org_id": IDENTIFIER})()
    session = Session()

    async def context():
        yield session, actor

    queue = Queue()
    app = FastAPI()
    app.include_router(create_router(context, Database(), object(), queue, object()))
    return app, calls, queue, session


async def test_check_http_routes_wrap_data_items_pagination_and_partial(interface):
    app, calls, queue, _ = interface
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        run = await api.post(
            f"/tasks/{IDENTIFIER}/checks",
            json={
                "draft_id": str(IDENTIFIER_2),
                "assessment_date": "2026-10-04",
                "dry_run": True,
            },
        )
        listing = await api.get(
            f"/tasks/{IDENTIFIER}/checks", params={"cursor": "opaque", "limit": 25}
        )
        shown = await api.get(f"/checks/{IDENTIFIER}")
        decided = await api.post(
            f"/checks/{IDENTIFIER}/findings/{IDENTIFIER_2}/decisions",
            json={
                "expected_revision": 1,
                "expected_input_hash": SHA,
                "action": "dismiss",
                "reason": "Reviewed against the confirmed draft.",
            },
        )
        history = await api.get(
            f"/checks/{IDENTIFIER}/findings/{IDENTIFIER_2}/decisions",
            params={"cursor": "opaque", "limit": 10},
        )

    for response in (run, listing, shown, decided, history):
        assert response.status_code == 200, response.text
        assert set(response.json()) == {
            "ok",
            "command",
            "data",
            "items",
            "warnings",
            "cost",
            "duration_ms",
        }
    assert run.json()["data"]["limitations"] == ["confirmed_draft_only"]
    assert listing.json()["command"] == "check list"
    assert shown.json()["ok"] is False
    assert shown.json()["warnings"] == ["check_input_changed", "confirmed_draft_only"]
    assert shown.json()["items"] == [{"id": str(IDENTIFIER_2)}]
    assert decided.json()["data"]["decision"]["action"] == "dismiss"
    assert history.json()["command"] == "check history"
    assert calls[1] == ("list", "opaque", 25)
    assert calls[4] == ("history", "opaque", 10)
    assert queue.calls == []


async def test_check_http_limit_is_bounded_before_service(interface):
    app, calls, _, _ = interface
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        low = await api.get(f"/tasks/{IDENTIFIER}/checks", params={"limit": 0})
        high = await api.get(f"/tasks/{IDENTIFIER}/checks", params={"limit": 201})
    assert low.status_code == high.status_code == 422
    assert calls == []


async def test_check_submission_commits_before_queue_dispatch(interface):
    app, _, queue, session = interface
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        response = await api.post(
            f"/tasks/{IDENTIFIER}/checks",
            json={
                "draft_id": str(IDENTIFIER_2),
                "assessment_date": "2026-10-04",
                "expected_input_hash": SHA,
            },
        )
    assert response.status_code == 200
    assert response.json()["data"] == {
        "job_id": str(IDENTIFIER),
        "status": "queued",
        "cached": False,
    }
    assert session.commits == 1
    assert queue.calls == [(str(IDENTIFIER), str(IDENTIFIER))]


async def test_check_queue_failure_returns_durable_job_id(interface):
    app, _, queue, session = interface
    queue.fail = True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        response = await api.post(
            f"/tasks/{IDENTIFIER}/checks",
            json={
                "draft_id": str(IDENTIFIER_2),
                "assessment_date": "2026-10-04",
                "expected_input_hash": SHA,
            },
        )
    assert response.status_code == 503
    body = response.json()
    assert set(body) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
    assert body["ok"] is False and body["command"] == "check run"
    assert body["data"] == {
        "error": {
            "code": "queue_unavailable",
            "message": "Check job saved; repeat request to schedule it",
            "exit_code": 3,
        },
        "job_id": str(IDENTIFIER),
    }
    assert session.commits == 1
    assert queue.calls == [(str(IDENTIFIER), str(IDENTIFIER))]
