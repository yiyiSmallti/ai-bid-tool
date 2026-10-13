"""Agent management isolation at the real authenticated PostgreSQL API.

Failure matrix: unknown task/session, API tokens, removed identity fields,
out-of-range pagination, stale revisions,
and mutations that would silently resume a paused session.
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.schemas.contracts import Result
from conftest import FakeQueue
from test_response_cards import (
    PhaseOneExtraction,
    create_tender,
    login,
    sanitized_artifact,
)


def start_input(extraction):
    return {
        "extraction_job_id": extraction,
        "message": "Generate proposals and pause for human review before draft assembly.",
        "requested_scopes": [
            "task:read",
            "job:read",
            "card:read",
            "card:generate",
            "draft:read",
            "draft:run",
        ],
        "limits": {},
        "idempotency_key": str(uuid4()),
    }


@pytest.fixture
async def agent_case(database, tmp_path):
    settings = Settings(data_dir=tmp_path)
    queue = FakeQueue()
    app = create_app(settings, llm=PhaseOneExtraction(), queue=queue)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        owner = await login(api)
        task, document, extraction, _ = await create_tender(
            api, app, owner, tmp_path, suffix="agent"
        )
        request = start_input(extraction)
        accepted = await api.post(f"/v4/tasks/{task}/agent-sessions", headers=owner, json=request)
        assert accepted.status_code == 200, accepted.text
        Result.model_validate(accepted.json())
        view = accepted.json()["data"]["session"]
        yield {
            "api": api,
            "app": app,
            "owner": owner,
            "task": task,
            "document": document,
            "extraction": extraction,
            "session": view,
            "accepted": accepted.json(),
            "request": request,
            "tmp_path": tmp_path,
        }


def session_routes(case):
    base = f"/v4/agent-sessions/{case['session']['id']}"
    revision = case["session"]["revision"]
    return [
        ("GET", base, None),
        ("GET", base + "/messages", None),
        ("GET", base + "/steps", None),
        (
            "POST",
            base + "/messages",
            {
                "message": "Human clarification",
                "expected_revision": revision,
                "idempotency_key": str(uuid4()),
            },
        ),
        (
            "POST",
            base + "/resume",
            {
                "expected_revision": revision,
                "pause_id": str(uuid4()),
                "idempotency_key": str(uuid4()),
            },
        ),
        (
            "POST",
            base + "/cancel",
            {"expected_revision": revision, "idempotency_key": str(uuid4())},
        ),
    ]


async def test_agent_token_cannot_manage_sessions(agent_case):
    case = agent_case
    minted = await case["api"].post(
        "/tokens",
        headers=case["owner"],
        json={
            "name": "Synthetic automation",
            "scopes": ["task:read", "job:read", "card:read"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert minted.status_code == 200, minted.text
    token = {
        "Authorization": "Bearer " + minted.json()["data"]["token"],
    }
    routes = session_routes(case) + [
        ("GET", f"/v4/tasks/{case['task']}/agent-sessions", None),
        ("POST", f"/v4/tasks/{case['task']}/agent-sessions", start_input(case["extraction"])),
    ]
    for method, path, body in routes:
        response = await case["api"].request(
            method, path, headers=token, **({"json": body} if body else {})
        )
        assert response.status_code == 403, (method, path, response.text)


async def test_agent_start_preview_deduplicates_and_read_contracts(agent_case):
    case = agent_case
    base = f"/v4/agent-sessions/{case['session']['id']}"
    repeated = await case["api"].post(
        f"/v4/tasks/{case['task']}/agent-sessions", headers=case["owner"], json=case["request"]
    )
    assert repeated.status_code == 200 and repeated.json()["data"]["deduplicated"] is True
    assert repeated.json()["data"]["session"]["id"] == case["session"]["id"]
    dry = await case["api"].post(
        f"/v4/tasks/{case['task']}/agent-sessions",
        headers=case["owner"],
        json={**start_input(case["extraction"]), "dry_run": True},
    )
    assert dry.status_code == 200 and dry.json()["data"]["dry_run"] is True
    bodies = {"start": case["accepted"], "preview": dry.json()}
    for action, suffix in [("show", ""), ("messages", "/messages"), ("steps", "/steps")]:
        response = await case["api"].get(base + suffix, headers=case["owner"])
        assert response.status_code == 200, response.text
        assert Result.model_validate(response.json()).command == f"agent {action}"
        bodies[action] = response.json()
    listing = await case["api"].get(
        f"/v4/tasks/{case['task']}/agent-sessions", headers=case["owner"]
    )
    assert len(listing.json()["items"]) == 1
    artifact = case["tmp_path"] / "agent-api-contract.json"
    artifact.write_text(json.dumps(sanitized_artifact(bodies), indent=2, ensure_ascii=False))
    assert json.loads(artifact.read_text())["preview"]["data"]["dry_run"]


async def test_agent_input_fields_and_pagination_are_validated(agent_case):
    case = agent_case
    start = await case["api"].post(
        f"/v4/tasks/{case['task']}/agent-sessions",
        headers=case["owner"],
        json={**start_input(case["extraction"]), "owner_user_id": str(uuid4())},
    )
    assert start.status_code == 422
    for path in [
        f"/v4/tasks/{case['task']}/agent-sessions",
        f"/v4/agent-sessions/{case['session']['id']}/messages",
        f"/v4/agent-sessions/{case['session']['id']}/steps",
    ]:
        for params in ({"limit": 0}, {"limit": 101}, {"cursor": "not-a-uuid"}):
            response = await case["api"].get(path, headers=case["owner"], params=params)
            assert response.status_code == 422, response.text


async def test_agent_queued_message_resume_and_cancel_state_rules(agent_case):
    case = agent_case
    routes = session_routes(case)
    for method, path, body in routes[3:5]:
        response = await case["api"].request(method, path, headers=case["owner"], json=body)
        assert response.status_code == 409, response.text
    method, path, body = routes[-1]
    stale = await case["api"].request(
        method,
        path,
        headers=case["owner"],
        json={**body, "expected_revision": body["expected_revision"] + 1},
    )
    assert stale.status_code == 409
    cancelled = await case["api"].request(method, path, headers=case["owner"], json=body)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["data"]["session"]["state"] == "cancelled"
    repeated = await case["api"].request(method, path, headers=case["owner"], json=body)
    assert repeated.status_code == 200 and repeated.json()["data"]["deduplicated"] is True
