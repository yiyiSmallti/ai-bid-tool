"""Real PostgreSQL/API acceptance for the pre-recorded team workflow failures.

Failure scenarios are inventoried in test_team_board_events.py before the feature
implementation. These cases use real bid_app transactions, RLS, durable event
functions and ASGI endpoints. No test starts a database or calls a vendor.
Artifacts contain synthetic IDs and query plans only; no bearer or parameters
from authentication are retained. Run with an explicitly supplied bid_test DB.
"""

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

import pytest
from app.core.security import TokenSigner
from app.models.entities import Chunk, Document, Job, Requirement
from app.services import extraction, task_events, task_workflow
from app.services.auth import ROLE_SCOPES, authenticate, set_actor_context
from sqlalchemy import event, insert, text
from sqlalchemy.orm import Session
from test_citation_batch_equivalence import synthetic_page_sources
from test_team_workflow_membership import add_member, new_task, person

ARTIFACTS = Path(__file__).resolve().parents[2] / "data/work/team-workflow-acceptance/stream"
PRIVATE_MARKER = "SENSITIVE-SYNTHETIC-FULL-TEXT-NEVER-IN-EVENTS"
INVALIDATION = {
    "type": "board_changed",
    "invalidate_all": True,
    "requirement_ids": [],
    "card_ids": [],
}


def artifact(name, value):
    destination = ARTIFACTS / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(value, indent=2, default=str) + "\n")


async def seed_scope(api, headers, tenants, admin_engine, *, count=0, tenant=0, chunk_count=1):
    """Create ownership through the API, then persist a synthetic saved extraction."""
    task_id = UUID(await new_task(api, headers[tenant]))
    org, user = tenants["orgs"][tenant], tenants["users"][tenant]
    document_id, job_id = uuid4(), uuid4()
    chunk_ids = [uuid4() for _ in range(chunk_count)]
    chunk_id = chunk_ids[0]
    quotes, source_pages = synthetic_page_sources(
        count, chunk_count, characters=6000 if chunk_count > 1 else 0
    )
    requirement_ids = [uuid4() for _ in range(count)]
    with Session(admin_engine) as session, session.begin():
        session.execute(
            text(
                "SELECT set_config('app.current_org',:org,true),set_config('app.actor_kind','session',true),set_config('app.actor_user_id',:user,true),set_config('app.actor_token_id','',true),set_config('app.actor_scopes',:scopes,true)"
            ),
            {
                "org": str(org),
                "user": str(user),
                "scopes": json.dumps(sorted(ROLE_SCOPES["admin"])),
            },
        )
        session.add(
            Document(
                id=document_id,
                org_id=org,
                task_id=task_id,
                name="Synthetic extraction.pdf",
                sha256=sha256(str(task_id).encode()).hexdigest(),
                storage_key=f"org/{org}/synthetic-extraction/{document_id}.pdf",
                media_type="application/pdf",
                page_count=chunk_count,
                status="parsed",
                citation_mode="page",
            )
        )
        session.flush()
        session.add_all(
            Chunk(
                id=source_id,
                org_id=org,
                task_id=task_id,
                document_id=document_id,
                page=page + 1,
                seq=page + 1,
                text=source_pages[page],
            )
            for page, source_id in enumerate(chunk_ids)
        )
        session.add(
            Job(
                id=job_id,
                org_id=org,
                task_id=task_id,
                document_id=document_id,
                kind="extract",
                cache_key=sha256(str(job_id).encode()).hexdigest(),
                status="succeeded",
                actor_user_id=user,
                actor_kind="session",
                actor_scopes=sorted(ROLE_SCOPES["admin"]),
                result={
                    "warnings": ["synthetic-empty-extraction"] if count == 0 else [],
                    "private_input": PRIVATE_MARKER,
                },
            )
        )
        session.flush()
        if count:
            session.execute(
                insert(Requirement),
                [
                    {
                        "id": requirement_ids[i],
                        "org_id": org,
                        "task_id": task_id,
                        "document_id": document_id,
                        "chunk_id": chunk_ids[i % chunk_count],
                        "page": i % chunk_count + 1,
                        "quote": quote,
                        "text": quote,
                        "category": "technical" if i % 2 == 0 else "qualification",
                        "starred": i % 3 == 0,
                        "condition": {},
                        "fingerprint": sha256(f"{job_id}:{i}".encode()).hexdigest(),
                        "job_id": job_id,
                    }
                    for i, quote in enumerate(quotes)
                ],
            )
    return {
        "org_id": org,
        "task_id": task_id,
        "document_id": document_id,
        "chunk_id": chunk_id,
        "chunk_ids": chunk_ids,
        "chunk_count": chunk_count,
        "source_characters": sum(map(len, source_pages)),
        "job_id": job_id,
        "user_id": user,
        "count": count,
        "requirement_ids": requirement_ids,
    }


async def poll(api, header, task_id, cursor=None, **extra):
    params = {**extra, **({"cursor": cursor} if cursor else {})}
    return await api.get(f"/tasks/{task_id}/events/poll", headers=header, params=params)


@asynccontextmanager
async def authenticated(application, header):
    org = UUID(header["X-Org-Id"])
    async with application.state.db.transaction(org) as session:
        actor = await authenticate(
            session, header["Authorization"].removeprefix("Bearer "), org, application.state.crypto
        )
        await set_actor_context(session, actor)
        yield session, actor


async def append_marker(application, header, task_id, marker=None):
    payload = (
        INVALIDATION
        if marker is None
        else {
            "type": "board_changed",
            "invalidate_all": False,
            "requirement_ids": [str(marker)],
            "card_ids": [],
        }
    )
    async with authenticated(application, header) as (session, actor):
        await task_events.append(session, actor.org_id, task_id, payload)


def is_auth_statement(statement):
    normalized = " ".join(statement.lower().split())
    if "set_config(" in normalized or normalized.startswith("set "):
        return True
    return any(
        f"from {table}" in normalized
        for table in (
            "users",
            "memberships",
            "orgs",
            "tasks",
            "task_workflows",
            "task_members",
            "api_tokens",
        )
    )


@pytest.mark.latency
@pytest.mark.parametrize("count", [0, 5000, 5001])
async def test_board_complete_bounds_and_query_plan(
    api, headers, tenants, admin_engine, application, count
):
    await assert_board_complete_bounds_and_query_plan(
        api, headers, tenants, admin_engine, application, count=count
    )


@pytest.mark.latency
async def test_board_5000_requirements_across_300_realistic_page_chunks(
    api, headers, tenants, admin_engine, application
):
    await assert_board_complete_bounds_and_query_plan(
        api, headers, tenants, admin_engine, application, count=5000, chunk_count=300
    )


async def assert_board_complete_bounds_and_query_plan(
    api, headers, tenants, admin_engine, application, *, count, chunk_count=1
):
    scope = await seed_scope(
        api, headers, tenants, admin_engine, count=count, chunk_count=chunk_count
    )
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        # Statement text only; auth SQL parameters must never become artifacts.
        statements.append(statement)

    event.listen(application.state.db.engine.sync_engine, "before_cursor_execute", capture)
    extraction.normalized_spans.cache_clear()
    started = perf_counter()
    try:
        response = await api.get(
            f"/tasks/{scope['task_id']}/board",
            headers=headers[0],
            params={"extraction_job_id": str(scope["job_id"]), "limit": 100},
        )
    finally:
        api_elapsed_seconds = perf_counter() - started
        event.remove(application.state.db.engine.sync_engine, "before_cursor_execute", capture)
    artifact_name = f"board-{count}" + (f"-chunks-{chunk_count}" if chunk_count > 1 else "")
    artifact(
        f"{artifact_name}.json",
        {
            "fixture": scope,
            "status": response.status_code,
            "api_elapsed_seconds": api_elapsed_seconds,
            "database_backed_api_executed": True,
        },
    )
    if count > 5000:
        assert response.status_code == 422, response.text
        assert response.json()["data"]["error"]["code"] == "board_limit_exceeded"
        assert response.json()["items"] == []
        artifact(
            f"{artifact_name}.json",
            {
                "fixture": scope,
                "status": response.status_code,
                "error": response.json()["data"]["error"]["code"],
                "api_elapsed_seconds": api_elapsed_seconds,
                "database_backed_api_executed": True,
            },
        )
        return
    assert response.status_code == 200, response.text
    assert api_elapsed_seconds <= 2, f"Board API took {api_elapsed_seconds:.3f}s"
    payload = response.json()
    assert len(response.content) <= 1024 * 1024
    assert payload["data"]["counts"]["total"] == count
    assert payload["data"]["counts"]["gap"] == count
    assert all("invalid_citation" not in item["blockers"] for item in payload["items"])
    assert sum(v for k, v in payload["data"]["counts"].items() if k != "total") == count
    assert payload["data"]["matching"] == count
    assert payload["data"]["returned"] == min(count, 100)
    assert len(payload["items"]) == min(count, 100)
    assert bool(payload["data"]["next_cursor"]) == (count > 100)
    projection = [s for s in statements if not is_auth_statement(s)]
    assert len(projection) <= 20, f"{len(projection)} projection SQL statements"
    invalid = await api.get(
        f"/tasks/{scope['task_id']}/board",
        headers=headers[0],
        params={
            "extraction_job_id": str(scope["job_id"]),
            "limit": 100,
            "blocker": "invalid_citation",
        },
    )
    assert invalid.status_code == 200, invalid.text
    assert invalid.json()["data"]["matching"] == 0
    assert invalid.json()["items"] == []
    plan = None
    if count == 5000:
        async with authenticated(application, headers[0]) as (session, _):
            plan = (
                await session.execute(
                    text(
                        "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) SELECT id FROM requirements WHERE org_id=:org AND task_id=:task AND job_id=:job AND document_id=:document ORDER BY id LIMIT 5001"
                    ),
                    {
                        "org": scope["org_id"],
                        "task": scope["task_id"],
                        "job": scope["job_id"],
                        "document": scope["document_id"],
                    },
                )
            ).scalar_one()
        assert plan[0]["Plan"]["Actual Rows"] == 5000
    artifact(
        f"{artifact_name}.json",
        {
            "fixture": scope,
            "status": response.status_code,
            "counts": payload["data"]["counts"],
            "response_bytes": len(response.content),
            "sql_total": len(statements),
            "projection_sql_count": len(projection),
            "projection_sql": projection,
            "requirements_plan": plan,
            "api_elapsed_seconds": api_elapsed_seconds,
            "database_backed_api_executed": True,
            "invalid_citation_matching": invalid.json()["data"]["matching"],
        },
    )


async def test_board_cursor_binds_filters_day_head_and_exact_extraction(
    api, headers, tenants, admin_engine, application
):
    scope = await seed_scope(api, headers, tenants, admin_engine, count=3)
    path = f"/tasks/{scope['task_id']}/board"
    params = {"extraction_job_id": str(scope["job_id"]), "limit": 1}
    initial = await api.get(path, headers=headers[0], params=params)
    assert initial.status_code == 200, initial.text
    first = initial.json()
    cursor = first["data"]["next_cursor"]
    second = await api.get(path, headers=headers[0], params={**params, "cursor": cursor})
    assert second.status_code == 200
    assert first["items"][0]["requirement_id"] != second.json()["items"][0]["requirement_id"]
    altered = await api.get(
        path, headers=headers[0], params={**params, "cursor": cursor, "starred": True}
    )
    assert altered.status_code == 409 and altered.json()["data"]["error"]["code"] == "board_changed"
    signer = TokenSigner.for_tokens(application.state.processor.settings)
    decoded = signer.open(cursor)
    decoded["d"] = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
    prior_day = signer.cipher.encrypt(json.dumps(decoded).encode()).decode()
    expired_day = await api.get(path, headers=headers[0], params={**params, "cursor": prior_day})
    assert expired_day.status_code == 409
    await append_marker(application, headers[0], scope["task_id"])
    head_changed = await api.get(path, headers=headers[0], params={**params, "cursor": cursor})
    assert head_changed.status_code == 409
    missing = await api.get(path, headers=headers[0])
    assert (
        missing.status_code == 422
        and missing.json()["data"]["error"]["code"] == "extraction_required"
    )
    foreign = await seed_scope(api, headers, tenants, admin_engine, count=0, tenant=1)
    mismatch = await api.get(
        path, headers=headers[0], params={"extraction_job_id": str(foreign["job_id"])}
    )
    assert mismatch.status_code == 404
    artifact(
        "board-cursor-bindings.json",
        {
            "fixture": scope,
            "distinct_pages": True,
            "changed_filters": altered.status_code,
            "changed_day": expired_day.status_code,
            "changed_head": head_changed.status_code,
            "foreign_extraction": mismatch.status_code,
        },
    )


@pytest.mark.parametrize("rollback_first", [False, True])
async def test_event_head_serializes_two_real_transactions_without_cursor_holes(
    api, headers, tenants, admin_engine, application, rollback_first
):
    scope = await seed_scope(api, headers, tenants, admin_engine, count=2)
    initial = await poll(api, headers[0], scope["task_id"])
    assert initial.status_code == 200
    baseline = initial.json()["data"]["head_cursor"]
    first_marker, second_marker = scope["requirement_ids"]
    db = application.state.db
    started = asyncio.Event()
    second_pid = []

    async def second_transaction():
        async with authenticated(application, headers[0]) as (session, actor):
            second_pid.append(await session.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            await task_events.append(
                session,
                actor.org_id,
                scope["task_id"],
                {
                    "type": "board_changed",
                    "invalidate_all": False,
                    "requirement_ids": [str(second_marker)],
                    "card_ids": [],
                },
            )

    async with db.sessions() as first_session:
        first_tx = await first_session.begin()
        await first_session.execute(
            text("SELECT set_config('app.current_org',:org,true)"), {"org": str(scope["org_id"])}
        )
        await task_events.append(
            first_session,
            scope["org_id"],
            scope["task_id"],
            {
                "type": "board_changed",
                "invalidate_all": False,
                "requirement_ids": [str(first_marker)],
                "card_ids": [],
            },
        )
        pending = asyncio.create_task(second_transaction())
        try:
            await asyncio.wait_for(started.wait(), 3)
            blocking = []
            async with asyncio.timeout(3):
                while not blocking:
                    with admin_engine.connect() as connection:
                        blocking = connection.scalar(
                            text("SELECT pg_blocking_pids(:pid)"), {"pid": second_pid[0]}
                        )
                    if not blocking:
                        await asyncio.sleep(0.01)
            assert not pending.done()
            during = await poll(api, headers[0], scope["task_id"], baseline)
            assert during.status_code == 200 and during.json()["items"] == []
            if rollback_first:
                await first_tx.rollback()
            else:
                await first_tx.commit()
            await asyncio.wait_for(pending, 5)
        finally:
            if first_tx.is_active:
                await first_tx.rollback()
            if not pending.done():
                pending.cancel()
                with suppress(asyncio.CancelledError):
                    await pending
    replay = await poll(api, headers[0], scope["task_id"], baseline)
    assert replay.status_code == 200, replay.text
    markers = [UUID(e["payload"]["requirement_ids"][0]) for e in replay.json()["items"]]
    assert markers == ([second_marker] if rollback_first else [first_marker, second_marker])
    again = await poll(api, headers[0], scope["task_id"], replay.json()["data"]["next_cursor"])
    assert again.status_code == 200 and again.json()["items"] == []
    artifact(
        f"event-order-{'rollback' if rollback_first else 'commit'}.json",
        {
            "fixture": scope,
            "blocked_writer_observed": True,
            "markers": markers,
            "rollback_first": rollback_first,
            "cursor_caught_up": True,
        },
    )


def assert_no_private_fields(value):
    if isinstance(value, dict):
        assert (
            not {
                "seq",
                "sequence",
                "last_seq",
                "retained_floor_seq",
                "source_id",
                "response_text",
                "object_key",
                "private_input",
            }
            & value.keys()
        )
        for child in value.values():
            assert_no_private_fields(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_private_fields(child)


async def test_hidden_export_job_events_and_progress_reveal_no_content_to_admin_or_token(
    api, headers, tenants, admin_engine, application
):
    scope = await seed_scope(api, headers, tenants, admin_engine, count=1)
    token = await api.post(
        "/tokens",
        headers=headers[0],
        json={
            "name": "Synthetic read events",
            "scopes": ["task:read", "card:read", "job:read"],
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
    )
    assert token.status_code == 200
    token_header = {**headers[0], "Authorization": "Bearer " + token.json()["data"]["token"]}
    readers = [headers[0], token_header]
    baselines = []
    for reader in readers:
        response = await poll(api, reader, scope["task_id"])
        assert response.status_code == 200
        baselines.append(response.json()["data"]["head_cursor"])
    hidden_ids = [uuid4(), uuid4()]
    with Session(admin_engine) as session, session.begin():
        session.execute(
            text(
                "SELECT set_config('app.current_org',:org,true),set_config('app.actor_kind','session',true),set_config('app.actor_user_id',:user,true),set_config('app.actor_scopes',:scopes,true)"
            ),
            {
                "org": str(scope["org_id"]),
                "user": str(scope["user_id"]),
                "scopes": json.dumps(sorted(ROLE_SCOPES["admin"])),
            },
        )
        for job_id, kind in zip(hidden_ids, ("export_render", "export_preview"), strict=True):
            session.add(
                Job(
                    id=job_id,
                    org_id=scope["org_id"],
                    task_id=scope["task_id"],
                    document_id=scope["document_id"],
                    kind=kind,
                    status="failed",
                    cache_key=sha256(str(job_id).encode()).hexdigest(),
                    actor_user_id=scope["user_id"],
                    actor_kind="session",
                    actor_scopes=sorted(ROLE_SCOPES["admin"]),
                    result={"private_input": PRIVATE_MARKER},
                )
            )
    marker = scope["requirement_ids"][0]
    await append_marker(application, headers[0], scope["task_id"], marker)
    receipts = []
    for reader, baseline in zip(readers, baselines, strict=True):
        response = await poll(api, reader, scope["task_id"], baseline)
        assert response.status_code == 200, response.text
        assert PRIVATE_MARKER not in response.text
        assert all(str(job_id) not in response.text for job_id in hidden_ids)
        assert_no_private_fields(response.json())
        assert [item["payload"]["requirement_ids"] for item in response.json()["items"]] == [
            [str(marker)]
        ]
        caught_up = await poll(
            api, reader, scope["task_id"], response.json()["data"]["next_cursor"]
        )
        assert caught_up.status_code == 200 and caught_up.json()["items"] == []
        progress = await api.get(f"/tasks/{scope['task_id']}/progress", headers=reader)
        assert progress.status_code == 200
        assert {item["job_id"] for item in progress.json()["items"]} == {str(scope["job_id"])}
        assert all(str(job_id) not in progress.text for job_id in hidden_ids)
        receipts.append(
            {
                "visible_events": len(response.json()["items"]),
                "visible_jobs": len(progress.json()["items"]),
                "no_private_payload": True,
                "checkpoint_advanced": True,
            }
        )
    artifact(
        "hidden-jobs-privacy.json",
        {"fixture": scope, "readers": ["human_admin", "scoped_token"], "receipts": receipts},
    )


async def test_event_cursor_foreign_future_and_expired_are_explicit(
    api, headers, tenants, admin_engine, application
):
    scope = await seed_scope(api, headers, tenants, admin_engine)
    foreign = await seed_scope(api, headers, tenants, admin_engine, tenant=1)
    source = await poll(api, headers[1], foreign["task_id"])
    wrong = await poll(api, headers[0], scope["task_id"], source.json()["data"]["head_cursor"])
    assert wrong.status_code == 404
    settings = application.state.processor.settings
    async with authenticated(application, headers[0]) as (session, actor):
        _, workflow, _ = await task_workflow.access(session, actor, scope["task_id"])
        head = await task_events.head(session, actor.org_id, scope["task_id"])
        future = task_events.issue_cursor(
            actor, scope["task_id"], workflow, settings, seq=head.last_seq + 1
        )
        current = task_events.issue_cursor(
            actor, scope["task_id"], workflow, settings, seq=head.last_seq
        )
    invalid = await poll(api, headers[0], scope["task_id"], future)
    assert (
        invalid.status_code == 422
        and invalid.json()["data"]["error"]["code"] == "invalid_event_cursor"
    )
    signer = TokenSigner.for_tokens(settings)
    value = signer.open(current)
    value["exp"] = 1
    expired_cursor = signer.cipher.encrypt(json.dumps(value).encode()).decode()
    expired = await poll(api, headers[0], scope["task_id"], expired_cursor)
    assert (
        expired.status_code == 409
        and expired.json()["data"]["error"]["code"] == "event_cursor_expired"
    )
    artifact(
        "event-cursor-errors.json",
        {
            "fixture": scope,
            "foreign": wrong.status_code,
            "future": invalid.status_code,
            "expired": expired.status_code,
        },
    )


async def test_durable_retention_prunes_bounded_history_and_forces_refresh(
    api, headers, tenants, admin_engine, application
):
    scope = await seed_scope(api, headers, tenants, admin_engine)
    initial = await poll(api, headers[0], scope["task_id"])
    assert initial.status_code == 200
    cursor = initial.json()["data"]["head_cursor"]
    # Seed an actual ordered historical window in one statement using only the
    # dedicated test migration connection. Runtime appending performs retention.
    with admin_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.current_org',:org,true)"), {"org": str(scope["org_id"])}
        )
        head = connection.scalar(
            text(
                "SELECT last_seq FROM task_event_heads WHERE org_id=:org AND task_id=:task FOR UPDATE"
            ),
            {"org": scope["org_id"], "task": scope["task_id"]},
        )
        connection.execute(
            text(
                "INSERT INTO task_events(id,org_id,task_id,seq,event_kind,payload,created_at) SELECT gen_random_uuid(),:org,:task,:head+n,'board_changed',CAST(:payload AS jsonb),clock_timestamp()-interval '8 days' FROM generate_series(1,50000) n"
            ),
            {
                "org": scope["org_id"],
                "task": scope["task_id"],
                "head": head,
                "payload": json.dumps(INVALIDATION),
            },
        )
        connection.execute(
            text(
                "UPDATE task_event_heads SET last_seq=:head+50000 WHERE org_id=:org AND task_id=:task"
            ),
            {"org": scope["org_id"], "task": scope["task_id"], "head": head},
        )
    await append_marker(application, headers[0], scope["task_id"])
    expired = await poll(api, headers[0], scope["task_id"], cursor)
    assert (
        expired.status_code == 409
        and expired.json()["data"]["error"]["code"] == "event_cursor_expired"
    )
    async with authenticated(application, headers[0]) as (session, _):
        retained = await session.scalar(
            text("SELECT count(*) FROM task_events WHERE task_id=:task"), {"task": scope["task_id"]}
        )
        floor = await session.scalar(
            text("SELECT retained_floor_seq FROM task_event_heads WHERE task_id=:task"),
            {"task": scope["task_id"]},
        )
    assert retained == 1
    assert floor == head + 50000
    fresh = await poll(api, headers[0], scope["task_id"])
    assert (
        fresh.status_code == 200
        and fresh.json()["data"]["reset_required"]["reason"] == "initial_snapshot_required"
    )
    artifact(
        "retention.json",
        {
            "fixture": scope,
            "seeded_historical_events": 50000,
            "retained_after_append": retained,
            "old_cursor_rejected": True,
            "fresh_snapshot_required": True,
        },
    )


class LiveASGIStream:
    """Exercise the real streaming endpoint without httpx's full-body buffering."""

    def __init__(self, app, path, headers):
        self.app = app
        self.path = path
        self.headers = headers
        self.inbound = asyncio.Queue()
        self.outbound = asyncio.Queue()
        self.messages = []
        self.task = None

    async def __aenter__(self):
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": self.path,
            "raw_path": self.path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in self.headers.items()],
            "client": ("synthetic-client", 1),
            "server": ("test", 80),
        }
        await self.inbound.put({"type": "http.request", "body": b"", "more_body": False})

        async def send(message):
            self.messages.append(message)
            await self.outbound.put(message)

        self.task = asyncio.create_task(self.app(scope, self.inbound.get, send))
        return self

    async def next(self):
        return await asyncio.wait_for(self.outbound.get(), 5)

    async def __aexit__(self, *exc):
        await self.inbound.put({"type": "http.disconnect"})
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, 5)
            except TimeoutError:
                self.task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.task

    def body(self):
        return b"".join(m.get("body", b"") for m in self.messages).decode()


async def test_sse_revocation_closes_connection_before_next_batch(
    api, headers, tenants, admin_engine, application
):
    scope = await seed_scope(api, headers, tenants, admin_engine, count=1)
    user, reader = await person(api, admin_engine, tenants["orgs"][0], "technical")
    added = await add_member(api, headers[0], scope["task_id"], user, 1, "observer")
    assert added.status_code == 200
    baseline = await poll(api, reader, scope["task_id"])
    assert baseline.status_code == 200
    marker = scope["requirement_ids"][0]
    stream_headers = {**reader, "Last-Event-ID": baseline.json()["data"]["head_cursor"]}
    async with LiveASGIStream(
        application, f"/tasks/{scope['task_id']}/events", stream_headers
    ) as stream:
        started = await stream.next()
        assert started["type"] == "http.response.start" and started["status"] == 200
        removed = await api.post(
            f"/tasks/{scope['task_id']}/members/{user}/remove",
            headers=headers[0],
            json={"expected_revision": 2, "reason": "Synthetic revoke while streaming"},
        )
        assert removed.status_code == 200, removed.text
        await append_marker(application, headers[0], scope["task_id"], marker)
        assert stream.task is not None
        await asyncio.wait_for(asyncio.shield(stream.task), 5)
        assert str(marker) not in stream.body()
        assert PRIVATE_MARKER not in stream.body()
        assert stream.messages[-1]["type"] == "http.response.body"
        assert not stream.messages[-1].get("more_body", False)
    denied = await api.get(f"/tasks/{scope['task_id']}/events", headers=reader)
    assert denied.status_code == 404
    artifact(
        "sse-revocation.json",
        {
            "fixture": scope,
            "initial_status": 200,
            "removed_user_id": user,
            "connection_closed": True,
            "post_revoke_marker_delivered": False,
            "reconnect_status": denied.status_code,
        },
    )


@pytest.mark.parametrize(
    "failure", ["foreign_task", "missing_bearer", "missing_card_scope", "foreign_cursor"]
)
async def test_sse_subscription_failures_do_not_start_successful_stream(
    api, headers, tenants, admin_engine, failure
):
    scope = await seed_scope(api, headers, tenants, admin_engine)
    reader = dict(headers[0])
    expected = 404
    if failure == "foreign_task":
        reader = headers[1]
    elif failure == "missing_bearer":
        reader.pop("Authorization")
        expected = 401
    elif failure == "missing_card_scope":
        token = await api.post(
            "/tokens",
            headers=headers[0],
            json={
                "name": "Synthetic incomplete stream token",
                "scopes": ["task:read", "job:read"],
                "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            },
        )
        assert token.status_code == 200
        reader["Authorization"] = "Bearer " + token.json()["data"]["token"]
        expected = 403
    else:
        foreign = await seed_scope(api, headers, tenants, admin_engine, tenant=1)
        initial = await poll(api, headers[1], foreign["task_id"])
        reader["Last-Event-ID"] = initial.json()["data"]["head_cursor"]
    response = await api.get(f"/tasks/{scope['task_id']}/events", headers=reader)
    assert response.status_code == expected, response.text
    assert not response.headers.get("content-type", "").startswith("text/event-stream")
    assert response.json()["ok"] is False
    assert response.json()["items"] == []
