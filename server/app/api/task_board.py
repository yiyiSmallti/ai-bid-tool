"""Task snapshots and bounded SSE, with fresh authentication per delivered batch."""

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import set_org
from app.core.errors import ServiceError
from app.core.security import TokenSigner
from app.schemas.contracts import Category, Cost, Result
from app.schemas.response_card_contracts import ReviewDomain
from app.schemas.team_workflow import (
    BoardQuery,
    EventReplayQuery,
    TaskProgressQuery,
)
from app.services import task_board, task_events
from app.services.auth import authenticate, set_actor_context

MAX_RESULT_BYTES = 1024 * 1024
MAX_BUFFER_BYTES = 256 * 1024
MAX_BUFFER_FRAMES = 100


def bounded_result(command, data, items, *, warnings=None, currency="USD"):
    result = Result(
        ok=True,
        command=command,
        data=data,
        items=items,
        warnings=warnings or [],
        cost=Cost(billing_currency=currency),
    )
    if len(result.model_dump_json().encode()) > MAX_RESULT_BYTES:
        raise ServiceError("board_limit_exceeded", "Snapshot exceeds 1 MiB response bound", 422, 2)
    return result


def frame(event, *, cursor=None):
    payload = event.model_dump_json()
    if len(payload.encode()) > 4096:
        raise ServiceError("board_limit_exceeded", "Event frame exceeds 4 KiB", 422, 2)
    name = event.payload.type if hasattr(event, "payload") else event.type
    return ((f"id: {cursor}\n" if cursor else "") + f"event: {name}\ndata: {payload}\n\n").encode()


class StreamCaps:
    """Database-wide admission through one AUTOCOMMIT advisory-lock connection.

    Local held-slot sets prohibit PostgreSQL's session-lock reentrancy. A lost
    connection invalidates all this process's permits before later delivery.
    No transaction or business row lock survives socket writes.
    """

    def __init__(self, db):
        self.db = db
        self.connection = None
        self.guard = asyncio.Lock()
        self.held = {}
        self.generation = 0

    async def _connection(self):
        if self.connection is None or self.connection.closed or self.connection.invalidated:
            if self.connection is not None:
                await self.connection.close()
            self.generation += 1
            self.held.clear()
            self.connection = await self.db.engine.connect()
            self.connection = await self.connection.execution_options(isolation_level="AUTOCOMMIT")
        return self.connection

    async def _invalidate(self):
        self.generation += 1
        self.held.clear()
        if self.connection is not None:
            await self.connection.close()
            self.connection = None

    async def _slot(self, connection, family, count):
        row = await connection.scalar(
            text(
                "WITH available AS MATERIALIZED (SELECT slot FROM generate_series(1,:cap) slot "
                "WHERE NOT (slot=ANY(CAST(:held AS integer[])))) "
                "SELECT slot FROM available WHERE pg_try_advisory_lock(hashtextextended(:family,slot)) LIMIT 1"
            ),
            {"cap": count, "held": sorted(self.held.get(family, set())), "family": family},
        )
        if row is None:
            return None
        self.held.setdefault(family, set()).add(row)
        return family, row

    async def _unlock(self, connection, slot):
        family, number = slot
        await connection.execute(
            text("SELECT pg_advisory_unlock(hashtextextended(:family,:slot))"),
            {"family": family, "slot": number},
        )
        self.held[family].discard(number)
        if not self.held[family]:
            del self.held[family]

    async def acquire(self, actor, task_id):
        async with self.guard:
            try:
                connection = await self._connection()
                org = await self._slot(connection, "task_stream_org:" + str(actor.org_id), 100)
                if org is None:
                    raise ServiceError("stream_limit", "Stream limit exceeded; retry later", 429, 3)
                identity = await self._slot(
                    connection,
                    "task_stream_identity:"
                    + ":".join(
                        map(str, (actor.org_id, actor.user_id, actor.token_id or "", task_id))
                    ),
                    3,
                )
                if identity is None:
                    await self._unlock(connection, org)
                    raise ServiceError("stream_limit", "Stream limit exceeded; retry later", 429, 3)
                return self.generation, org, identity
            except SQLAlchemyError:
                await self._invalidate()
                raise ServiceError(
                    "board_busy", "Stream admission unavailable; retry", 503, 3
                ) from None

    async def check(self, permit):
        async with self.guard:
            if permit[0] != self.generation or self.connection is None:
                raise ServiceError("board_busy", "Stream admission lost; reconnect", 503, 3)
            try:
                await self.connection.execute(text("SELECT 1"))
            except SQLAlchemyError:
                await self._invalidate()
                raise ServiceError(
                    "board_busy", "Stream admission lost; reconnect", 503, 3
                ) from None

    async def release(self, permit):
        async with self.guard:
            if permit[0] != self.generation or self.connection is None:
                return
            try:
                for slot in permit[1:]:
                    await self._unlock(self.connection, slot)
            except SQLAlchemyError:
                await self._invalidate()

    async def close(self):
        async with self.guard:
            await self._invalidate()


class BoardRouter(APIRouter):
    stream_caps: StreamCaps


def create_router(context, db, storage, queue, settings):
    router = BoardRouter()
    bearer = HTTPBearer(auto_error=False)
    crypto = TokenSigner.for_tokens(settings)
    caps = StreamCaps(db)
    router.stream_caps = caps

    @asynccontextmanager
    async def snapshot(credentials, org_id):
        if credentials is None:
            raise ServiceError("invalid_session", "Bearer credentials required", 401, 4)
        # Isolation is selected before the first org/auth SQL statement. Releasing
        # the connection before socket writes avoids a long-lived RLS transaction.
        async with db.engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="REPEATABLE READ")
            async with (
                AsyncSession(bind=connection, expire_on_commit=False) as session,
                session.begin(),
            ):
                await set_org(session, org_id)
                await session.execute(text("SET LOCAL statement_timeout='2000ms'"))
                actor = await authenticate(session, credentials.credentials, org_id, crypto)
                await set_actor_context(session, actor)
                session.info["memory_settings"] = settings
                yield session, actor

    async def read(credentials, org_id, operation):
        deadline = asyncio.timeout(2)
        try:
            async with deadline, snapshot(credentials, org_id) as (session, actor):
                return await operation(session, actor)
        except DBAPIError as error:
            # Cancelling an in-flight psycopg query leaves the connection busy, so the
            # rollback on exit raises a DBAPIError that replaces the timeout's
            # CancelledError; the pool discards that connection.
            if deadline.expired() or getattr(error.orig, "sqlstate", None) == "57014":
                raise ServiceError("board_busy", "Snapshot timed out; retry", 503, 3) from None
            raise
        except TimeoutError:
            raise ServiceError("board_busy", "Snapshot timed out; retry", 503, 3) from None

    @router.get("/tasks/{task_id}/board", name="task_board", response_model=Result)
    async def board(
        request: Request,
        task_id: UUID,
        view: Literal["requirement-review"] | None = None,
        extraction_job_id: UUID | None = None,
        bucket: str | None = None,
        category: Category | None = None,
        starred: bool | None = None,
        owner_user_id: UUID | None = None,
        unassigned: bool = False,
        review_domain: ReviewDomain | None = None,
        blocker: str | None = None,
        mine: bool = False,
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        x_org_id: UUID = Header(...),
    ):
        async def operation(session, actor):
            if extraction_job_id is None:
                from app.services.task_workflow import access

                await access(session, actor, task_id)
                raise ServiceError("extraction_required", "Choose an extraction job", 422, 2)
            try:
                from app.schemas.requirement_confirmation import RequirementBoardQuery

                query_type = RequirementBoardQuery if view == "requirement-review" else BoardQuery
                query = query_type.model_validate(
                    dict(
                        extraction_job_id=extraction_job_id,
                        bucket=bucket,
                        category=category,
                        starred=starred,
                        owner_user_id=owner_user_id,
                        unassigned=unassigned,
                        review_domain=review_domain,
                        blocker=blocker,
                        mine=mine,
                        cursor=cursor,
                        limit=limit,
                    )
                )
            except ValidationError as error:
                raise RequestValidationError(error.errors()) from None
            if view == "requirement-review":
                if request.state.contract_version != "4.0":
                    raise ServiceError(
                        "contract_version_required",
                        "Requirement review requires contract 4.0",
                        404,
                        4,
                    )
                from app.services import requirement_board

                data, items = await requirement_board.board(
                    session, actor, task_id, query, storage, settings
                )
                return bounded_result(
                    "task board",
                    data.model_dump(mode="json"),
                    [item.model_dump(mode="json") for item in items],
                    currency=settings.billing_currency,
                )
            result_view = await task_board.board(
                session,
                actor,
                task_id,
                BoardQuery.model_validate(query.model_dump()),
                storage,
                settings,
            )
            pending = any(
                value.state != "confirmed"
                for value in session.info.get("board_requirement_reviews", {}).values()
            )
            return bounded_result(
                "task board",
                result_view.model_dump(mode="json", exclude={"rows"}),
                [row.model_dump(mode="json") for row in result_view.rows],
                warnings=["requirement_review_pending:open_requirement_review"] if pending else [],
                currency=settings.billing_currency,
            )

        return await read(credentials, x_org_id, operation)

    @router.get("/tasks/{task_id}/progress", name="task_progress", response_model=Result)
    async def progress(
        request: Request,
        task_id: UUID,
        view: Literal["requirement-review"] | None = None,
        extraction_job_id: UUID | None = None,
        cursor: str | None = None,
        limit: int = Query(20, ge=1, le=20),
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        x_org_id: UUID = Header(...),
    ):
        async def operation(session, actor):
            if view == "requirement-review":
                from app.services import requirement_board
                from app.services.task_workflow import access

                await access(session, actor, task_id)
                if request.state.contract_version != "4.0":
                    raise ServiceError(
                        "contract_version_required",
                        "Requirement review requires contract 4.0",
                        404,
                        4,
                    )
                if extraction_job_id is None:
                    raise ServiceError("extraction_required", "Choose an extraction job", 422, 2)
                data, jobs = await requirement_board.progress(
                    session,
                    actor,
                    task_id,
                    extraction_job_id,
                    TaskProgressQuery(cursor=cursor, limit=limit),
                    storage,
                    settings,
                )
                return bounded_result(
                    "task progress",
                    data.model_dump(mode="json"),
                    [job.model_dump(mode="json") for job in jobs],
                    currency=settings.billing_currency,
                )
            result_view = await task_board.progress(
                session,
                actor,
                task_id,
                TaskProgressQuery(cursor=cursor, limit=limit),
                storage,
                settings,
            )
            return bounded_result(
                "task progress",
                result_view.model_dump(mode="json", exclude={"jobs"}),
                [job.model_dump(mode="json") for job in result_view.jobs],
                currency=settings.billing_currency,
            )

        return await read(credentials, x_org_id, operation)

    @router.get("/tasks/{task_id}/activity", name="task_activity", response_model=Result)
    async def activity(
        task_id: UUID,
        cursor: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        x_org_id: UUID = Header(...),
    ):
        async def operation(session, actor):
            page = await task_board.activity(
                session, actor, task_id, storage, settings, cursor=cursor, limit=limit
            )
            return bounded_result(
                "task activity",
                page.data.model_dump(mode="json"),
                [item.model_dump(mode="json") for item in page.items],
            )

        return await read(credentials, x_org_id, operation)

    async def replay(credentials, org_id, task_id, cursor, limit):
        async def operation(session, actor):
            page = await task_events.replay(
                session, actor, task_id, settings, storage, cursor=cursor, limit=limit
            )
            return actor, page

        return await read(credentials, org_id, operation)

    @router.get("/tasks/{task_id}/events/poll", name="task_events_poll", response_model=Result)
    async def poll(
        task_id: UUID,
        cursor: str | None = None,
        limit: int = Query(100, ge=1, le=100),
        wait_seconds: int = Query(0, ge=0, le=25),
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        x_org_id: UUID = Header(...),
    ):
        query = EventReplayQuery(cursor=cursor, limit=limit, wait_seconds=wait_seconds)
        deadline = time.monotonic() + query.wait_seconds
        while True:
            _, page = await replay(credentials, x_org_id, task_id, query.cursor, query.limit)
            if page.events or page.reset_required or time.monotonic() >= deadline:
                break
            query.cursor = page.next_cursor
            await asyncio.sleep(min(1, deadline - time.monotonic()))
        return bounded_result(
            "task events",
            page.model_dump(mode="json", exclude={"events"}),
            [event.model_dump(mode="json") for event in page.events],
        )

    @router.get("/tasks/{task_id}/events", name="task_events")
    async def stream(
        request: Request,
        task_id: UUID,
        last_event_id: str | None = Header(None),
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        x_org_id: UUID = Header(...),
    ):
        actor, initial = await replay(credentials, x_org_id, task_id, last_event_id, 50)
        key = await caps.acquire(actor, task_id)

        async def messages():
            cursor = last_event_id
            page = initial
            heartbeat = time.monotonic() + 15
            try:
                while not await request.is_disconnected():
                    await caps.check(key)
                    if page.reset_required:
                        yield frame(page.reset_required)
                        return
                    frames = [frame(event, cursor=event.cursor) for event in page.events]
                    if len(frames) > MAX_BUFFER_FRAMES or sum(map(len, frames)) > MAX_BUFFER_BYTES:
                        return
                    for item in frames:
                        yield item
                    cursor = page.next_cursor or cursor
                    if time.monotonic() >= heartbeat:
                        from app.schemas.team_workflow import StreamHeartbeat

                        yield frame(
                            StreamHeartbeat(
                                cursor=cursor or page.head_cursor, as_of=datetime.now(UTC)
                            )
                        )
                        heartbeat = time.monotonic() + 15
                    # Each next batch checks current user/token/org/task/objects.
                    # No transaction survives any yield or polling delay.
                    await asyncio.sleep(1)
                    try:
                        _, page = await replay(credentials, x_org_id, task_id, cursor, 50)
                    except ServiceError as error:
                        if error.code in (
                            "event_cursor_expired",
                            "board_changed",
                            "invalid_event_cursor",
                        ):
                            _, fresh = await replay(credentials, x_org_id, task_id, None, 100)
                            from app.schemas.team_workflow import StreamResetRequired

                            reason = (
                                "retention_expired"
                                if error.code == "event_cursor_expired"
                                else "access_changed"
                                if error.code == "board_changed"
                                else "snapshot_expired"
                            )
                            yield frame(
                                StreamResetRequired(reason=reason, head_cursor=fresh.head_cursor)
                            )
                        return
            finally:
                await caps.release(key)

        return StreamingResponse(
            messages(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    return router
