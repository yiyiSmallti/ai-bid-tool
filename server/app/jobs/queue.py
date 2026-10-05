import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import procrastinate
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.jobs.sandbox_queue import register_sandbox_task


class Queue:
    def __init__(self, settings: Settings):
        url = make_url(settings.database_url.get_secret_value()).set(drivername="postgresql")
        self.app = procrastinate.App(
            connector=procrastinate.PsycopgConnector(
                conninfo=url.render_as_string(hide_password=False), min_size=1, max_size=5
            )
        )
        self.processor: Callable[[str, str], Awaitable[None]] | None = None

        @self.app.task(
            name="bid.process",
            queue="bid",
            retry=procrastinate.RetryStrategy(max_attempts=3, wait=5),
        )
        async def process(org_id: str, job_id: str):
            if self.processor is None:
                raise RuntimeError("Worker processor is not configured")
            await self.processor(org_id, job_id)

        self.sandbox_task = register_sandbox_task(self.app, lambda: self.processor)
        self.task = process

        @self.app.task(name="bid.agent_wake", queue="bid", retry=True)
        async def agent_wake(org_id: str, session_id: str):
            from uuid import UUID

            from app.jobs.agent import wake

            if self.processor is None:
                raise RuntimeError("Worker processor is not configured")
            await wake(self.processor, UUID(org_id), UUID(session_id))

        @self.app.periodic(cron="* * * * * */30")
        @self.app.task(name="bid.agent_recover", queue="bid", retry=True)
        async def agent_recover(timestamp: int):
            await self.recover_agent_wakes()

        self.agent_wake_task = agent_wake
        self.agent_recover_task = agent_recover
        self.lock = asyncio.Lock()

    @staticmethod
    async def transaction_connection(session: AsyncSession):
        """Borrow psycopg from the current SQLAlchemy transaction; never close it."""
        connection = await session.connection()
        raw = await connection.get_raw_connection()
        return raw.driver_connection

    async def enqueue_in_transaction(self, session: AsyncSession, org_id: str, job_id: str) -> int:
        connection = await self.transaction_connection(session)
        return await self.task.configure(connection=connection).defer_async(
            org_id=org_id, job_id=job_id
        )

    async def ensure_process_delivery(self, session: AsyncSession, job) -> None:
        """A killed queue worker may never have reached the business Job claim."""
        runnable = (
            await session.scalar(
                text(
                    "SELECT queued.status='todo' OR (queued.status='doing' AND EXISTS ("
                    "SELECT 1 FROM procrastinate_workers AS worker WHERE worker.id=queued.worker_id "
                    "AND worker.last_heartbeat>now()-interval '30 seconds')) "
                    "FROM procrastinate_jobs AS queued WHERE queued.id=:id"
                ),
                {"id": job.queue_id},
            )
            if job.queue_id is not None
            else False
        )
        if not runnable:
            job.queue_id = await self.enqueue_in_transaction(session, str(job.org_id), str(job.id))

    async def enqueue_agent_wake(
        self, session: AsyncSession, org_id: str, session_id: str, *, delay: int = 30
    ) -> int:
        key = f"agent-wake:{org_id}:{session_id}"
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})
        existing = await session.scalar(
            text(
                "SELECT id FROM procrastinate_jobs WHERE task_name='bid.agent_wake' "
                "AND queueing_lock=:key AND status='todo' ORDER BY id LIMIT 1"
            ),
            {"key": key},
        )
        if existing is not None:
            return existing
        connection = await self.transaction_connection(session)
        return await self.agent_wake_task.configure(
            connection=connection,
            queueing_lock=key,
            schedule_at=datetime.now(UTC) + timedelta(seconds=max(0, min(delay, 30))),
        ).defer_async(org_id=org_id, session_id=session_id)

    async def recover_agent_wakes(self) -> None:
        """Only queue metadata is global; business recovery always re-enters tenant RLS."""
        from app.jobs.processor import Processor

        if not isinstance(self.processor, Processor):
            raise RuntimeError("Worker processor is not configured")
        async with self.processor.db.transaction() as session:
            # A todo/doing wake is retained until a successor is committed. Reset
            # only a wake whose worker no longer has a live heartbeat.
            await session.execute(
                text(
                    "UPDATE procrastinate_jobs AS queued SET status='todo', worker_id=NULL, "
                    "scheduled_at=now(), abort_requested=false "
                    "WHERE queued.task_name='bid.agent_wake' AND queued.status='doing' "
                    "AND NOT EXISTS (SELECT 1 FROM procrastinate_workers AS live "
                    "WHERE live.id=queued.worker_id "
                    "AND live.last_heartbeat>now()-interval '30 seconds') "
                    "AND NOT EXISTS (SELECT 1 FROM procrastinate_jobs AS successor "
                    "WHERE successor.task_name='bid.agent_wake' "
                    "AND successor.queueing_lock=queued.queueing_lock AND successor.status='todo') "
                    "AND queued.id=(SELECT max(candidate.id) FROM procrastinate_jobs AS candidate "
                    "WHERE candidate.task_name='bid.agent_wake' AND candidate.status='doing' "
                    "AND candidate.queueing_lock=queued.queueing_lock)"
                )
            )

    async def enqueue(self, org_id: str, job_id: str) -> int:
        async with self.lock, self.app.open_async():
            return await self.task.defer_async(org_id=org_id, job_id=job_id)

    async def enqueue_sandbox(self, org_id: str, job_id: str) -> int:
        async with self.lock, self.app.open_async():
            return await self.sandbox_task.defer_async(org_id=org_id, job_id=job_id)
