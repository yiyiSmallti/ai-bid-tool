"""Agent management isolation at the real authenticated PostgreSQL API.

Failure matrix: foreign task/session, same-org nonowner (including admin), API
tokens, untrusted ownership fields, out-of-range pagination, stale revisions,
and mutations that would silently resume a paused session.
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.models.entities import Membership
from app.schemas.contracts import Result
from conftest import FakeQueue
from sqlalchemy.orm import Session
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
        "limits": {"max_vendor_usd": "2", "max_platform_charge": "2", "billing_currency": "USD"},
        "idempotency_key": str(uuid4()),
    }


@pytest.fixture
async def agent_case(tenants, admin_engine, tmp_path):
    settings = Settings(data_dir=tmp_path)
    queue = FakeQueue()
    app = create_app(settings, llm=PhaseOneExtraction(), queue=queue)
    # The other tenant's existing identity also joins A as an administrator. Neither
    # global identity sharing nor the administrator role grants session ownership.
    with Session(admin_engine) as session, session.begin():
        session.add(
            Membership(org_id=tenants["orgs"][0], user_id=tenants["users"][1], role="admin")
        )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        owner = await login(api, tenants["orgs"][0], "a")
        foreign = await login(api, tenants["orgs"][1], "b")
        nonowner = await login(api, tenants["orgs"][0], "b")
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
            "foreign": foreign,
            "nonowner": nonowner,
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


@pytest.mark.parametrize("actor", ["foreign", "nonowner"])
async def test_agent_session_routes_hide_foreign_and_nonowner_resources(agent_case, actor):
    case = agent_case
    results = []
    for method, path, body in session_routes(case):
        response = await case["api"].request(
            method, path, headers=case[actor], **({"json": body} if body else {})
        )
        assert response.status_code == 404, (method, path, response.text)
        result = Result.model_validate(response.json())
        assert not result.ok and result.data["error"]["code"] == "not_found"
        results.append({"method": method, "path": path, "result": result.model_dump(mode="json")})
    after = await case["api"].get(
        f"/v4/agent-sessions/{case['session']['id']}", headers=case["owner"]
    )
    assert (
        after.status_code == 200
        and after.json()["data"]["session"]["revision"] == case["session"]["revision"]
    )
    artifact = case["tmp_path"] / f"agent-isolation-{actor}.json"
    artifact.write_text(json.dumps(sanitized_artifact(results), ensure_ascii=False, indent=2))
    assert len(json.loads(artifact.read_text())) == 6


async def test_agent_task_routes_are_org_scoped_and_lists_are_owner_scoped(agent_case):
    case = agent_case
    path = f"/v4/tasks/{case['task']}/agent-sessions"
    for method, body in [("GET", None), ("POST", start_input(case["extraction"]))]:
        response = await case["api"].request(
            method, path, headers=case["foreign"], **({"json": body} if body else {})
        )
        assert response.status_code == 404, response.text
    owner = await case["api"].get(path, headers=case["owner"])
    nonowner = await case["api"].get(path, headers=case["nonowner"])
    assert owner.status_code == nonowner.status_code == 200
    assert [item["id"] for item in owner.json()["items"]] == [case["session"]["id"]]
    assert nonowner.json()["items"] == []


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
        "X-Org-Id": case["owner"]["X-Org-Id"],
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
