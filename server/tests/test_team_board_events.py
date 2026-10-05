"""Slice-one acceptance failures recorded before implementation.

Failure inventory: cross-org/task access and foreign encrypted cursors; removed
members and revoked/expired identities during streams; token access to human
export jobs; malformed/future/pruned cursors; snapshot/event race and changed
filters/head/day; rollback versus committed durable ordering; hidden-event
checkpoint leaks; content/raw sequence in frames; >5000 requirements; >1MiB
Result; slow snapshots; unknown/falsified job percentages; duplicate frames;
stream/org caps and bounded queues; successful empty extraction; archived writes.
The PostgreSQL integration cases require an explicitly supplied running runtime;
this suite must never start or stop database services.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.api import task_board as board_api
from app.core.errors import ServiceError
from app.schemas.contracts import Result
from app.schemas.team_workflow import EventReplayView, StreamResetRequired
from app.services.auth import Identity
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import SecretStr


@pytest.fixture
def transport(monkeypatch):
    org, task, user = uuid4(), uuid4(), uuid4()
    actor = Identity(user, org, {"task:read", "card:read", "job:read"}, "bidder")
    lifecycle = []

    class Connection:
        closed = False
        invalidated = False

        async def scalar(self, statement, values):
            return next(
                (slot for slot in range(1, values["cap"] + 1) if slot not in values["held"]), None
            )

        async def execute(self, statement, *args):
            pass

        async def close(self):
            self.closed = True

        async def execution_options(self, **options):
            assert options["isolation_level"] in {"REPEATABLE READ", "AUTOCOMMIT"}
            lifecycle.append("repeatable_read")
            return self

    class Engine:
        def connect(self):
            lifecycle.append("connect")

            class Connected(Connection):
                def __await__(self):
                    async def ready():
                        return self

                    return ready().__await__()

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    lifecycle.append("disconnect")
                    await self.close()

            return Connected()

    class Session:
        def __init__(self, **kwargs):
            self.info = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            lifecycle.append("release_session")

        @asynccontextmanager
        async def begin(self):
            yield self
            lifecycle.append("end_transaction")

        async def execute(self, statement, *args):
            lifecycle.append("sql")

    async def authenticate(session, bearer, org_id, crypto):
        if bearer != "synthetic-session" or org_id != org:
            raise ServiceError("invalid_session", "Invalid credentials", 401, 4)
        lifecycle.append("authenticated")
        return actor

    async def context(session, actor):
        pass

    async def replay(session, current, task_id, settings, storage, *, cursor=None, limit=100):
        if task_id != task:
            raise ServiceError("not_found", "Resource not found", 404, 4)
        lifecycle.append("replay")
        return EventReplayView(
            org_id=org,
            task_id=task,
            head_cursor="opaque-encrypted-head",
            next_cursor=None,
            has_more=False,
            events=[],
            reset_required=StreamResetRequired(
                reason="initial_snapshot_required", head_cursor="opaque-encrypted-head"
            ),
        )

    monkeypatch.setattr(board_api, "AsyncSession", Session)
    monkeypatch.setattr(board_api, "authenticate", authenticate)
    monkeypatch.setattr(board_api, "set_actor_context", context)
    monkeypatch.setattr(board_api.task_events, "replay", replay)
    settings = SimpleNamespace(token_key=SecretStr(Fernet.generate_key().decode()))
    app = FastAPI()

    @app.exception_handler(ServiceError)
    async def errors(request, error):
        return JSONResponse(
            status_code=error.status,
            content=Result(
                ok=False,
                command="task events",
                data={
                    "error": {
                        "code": error.code,
                        "message": error.message,
                        "exit_code": error.exit_code,
                    }
                },
            ).model_dump(mode="json"),
        )

    app.include_router(
        board_api.create_router(None, SimpleNamespace(engine=Engine()), None, None, settings)
    )
    headers = {"Authorization": "Bearer synthetic-session", "X-Org-Id": str(org)}
    return app, task, headers, lifecycle


async def test_initial_poll_and_stream_release_transaction_before_frames(transport):
    app, task, headers, lifecycle = transport
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        poll = await client.get(f"/tasks/{task}/events/poll", headers=headers)
        assert poll.status_code == 200
        body = poll.json()
        assert set(body) == {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
        assert body["data"]["reset_required"]["reason"] == "initial_snapshot_required"
        assert body["items"] == []
        lifecycle.clear()
        stream = await client.get(f"/tasks/{task}/events", headers=headers)
        assert stream.status_code == 200
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert "event: reset_required\n" in stream.text
        assert "sequence" not in stream.text and "seq" not in stream.text
        assert (
            lifecycle.index("replay")
            < lifecycle.index("end_transaction")
            < lifecycle.index("disconnect")
        )


@pytest.mark.parametrize("path", ["events", "events/poll", "progress", "activity", "board"])
async def test_new_read_routes_require_bearer_before_task_disclosure(transport, path):
    app, task, headers, _ = transport
    headers.pop("Authorization")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/tasks/{task}/{path}", headers=headers)
    assert response.status_code == 401
    assert response.json()["data"]["error"]["code"] == "invalid_session"


@pytest.mark.parametrize("params", [{"limit": 101}, {"limit": 0}, {"wait_seconds": 26}])
async def test_poll_rejects_unbounded_requests(transport, params):
    app, task, headers, _ = transport
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/tasks/{task}/events/poll", headers=headers, params=params)
    assert response.status_code == 422


async def test_event_handshake_foreign_task_uniform_not_found(transport):
    app, _, headers, _ = transport
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(f"/tasks/{uuid4()}/events", headers=headers)
    assert response.status_code == 404
    assert response.json()["data"]["error"]["code"] == "not_found"
