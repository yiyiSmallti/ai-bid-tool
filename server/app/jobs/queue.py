import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

import procrastinate
from procrastinate.exceptions import ConnectorException
from procrastinate.jobs import Status
from psycopg import OperationalError
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError as SqlAlchemyOperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ServiceError
from app.jobs.sandbox_queue import register_sandbox_task
from app.models.entities import Job

logger = logging.getLogger(__name__)


async def dispatch_process_job(session: AsyncSession, queue, job: Job | None) -> None:
    """Publish a business job and its delivery in the caller's transaction."""
    if job is None or job.status != "queued":
        return
    try:
        await queue.ensure_process_delivery(session, job)
    except (OSError, ConnectorException, OperationalError, SqlAlchemyOperationalError) as exc:
        # Raising lets the request transaction roll back both business and queue
        # writes, including a queue insert whose response was interrupted.
        raise ServiceError(
            "queue_unavailable", "Job could not be scheduled; repeat the request", 503, 3
        ) from exc


class Queue:
    def __init__(self, settings: Settings):
        url = make_url(settings.database_url.get_secret_value()).set(drivername="postgresql")
        self.app = procrastinate.App(
            connector=procrastinate.PsycopgConnector(
                conninfo=url.render_as_string(hide_password=False), min_size=1, max_size=5
            )
        )
        self.processor: Callable[[str], Awaitable[None]] | None = None

        @self.app.task(
            name="bid.process",
            queue="bid",
            retry=procrastinate.RetryStrategy(max_attempts=3, wait=5),
        )
        async def process(job_id: str):
            if self.processor is None:
                raise RuntimeError("Worker processor is not configured")
            await self.processor(job_id)

        self.sandbox_task = register_sandbox_task(self.app, lambda: self.processor)
        self.task = process

        @self.app.task(name="bid.annotation_cleanup", queue="bid", retry=True)
        async def annotation_cleanup(job_id: str):
            from uuid import UUID

            from app.services.annotation_objects import reconcile

            if self.processor is None:
                raise RuntimeError("Worker processor is not configured")
            await reconcile(self.processor, UUID(job_id))

        self.annotation_cleanup_task = annotation_cleanup

        from app.jobs.attachment_events import register

        self.attachment_invalidation_task = register(self.app, lambda: self.processor)

        @self.app.task(name="bid.agent_wake", queue="bid", retry=True)
        async def agent_wake(session_id: str):
            from uuid import UUID

            from app.jobs.agent import wake

            if self.processor is None:
                raise RuntimeError("Worker processor is not configured")
            await wake(self.processor, UUID(session_id))

        @self.app.periodic(cron="* * * * * */30")
        @self.app.task(name="bid.agent_recover", queue="bid", retry=True)
        async def agent_recover(timestamp: int):
            await self.recover_agent_wakes()
            await self.recover_sandbox_jobs()
            await self.recover_process_jobs()

        self.agent_wake_task = agent_wake
        self.agent_recover_task = agent_recover
        self.lock = asyncio.Lock()
        self._process_recovery_cursor = 0

    @staticmethod
    async def transaction_connection(session: AsyncSession):
        """Borrow psycopg from the current SQLAlchemy transaction; never close it."""
        connection = await session.connection()
        raw = await connection.get_raw_connection()
        return raw.driver_connection

    async def enqueue_in_transaction(self, session: AsyncSession, job_id: str) -> int:
        connection = await self.transaction_connection(session)
        return await self.task.configure(connection=connection).defer_async(job_id=job_id)

    async def enqueue_attachment_invalidation_in_transaction(self, session, **arguments):
        from app.jobs.attachment_events import enqueue

        return await enqueue(self, session, **arguments)

    async def enqueue_annotation_cleanup_in_transaction(
        self, session: AsyncSession, job_id: str, *, delay: int = 300
    ) -> int:
        """Commit delayed cleanup with the stage record, without requeueing rendering."""
        from app.services.annotation_objects import GRACE_SECONDS

        key = f"annotation-cleanup:{job_id}"
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})
        existing = await session.scalar(
            text(
                "SELECT id FROM procrastinate_jobs WHERE task_name='bid.annotation_cleanup' "
                "AND queueing_lock=:key AND status='todo' ORDER BY id LIMIT 1"
            ),
            {"key": key},
        )
        if existing is not None:
            return existing
        connection = await self.transaction_connection(session)
        return await self.annotation_cleanup_task.configure(
            connection=connection,
            queueing_lock=key,
            schedule_at=datetime.now(UTC) + timedelta(seconds=max(GRACE_SECONDS, delay)),
        ).defer_async(job_id=job_id)

    async def ensure_process_delivery(self, session: AsyncSession, job) -> None:
        """A killed queue worker may never have reached the business Job claim."""
        # Autoflush preserves retry changes before refreshing a row another
        # submitter may have dispatched while this transaction waited for it.
        saved = await session.scalar(
            select(Job)
            .where(Job.id == job.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if saved is None or saved.status != "queued":
            return
        runnable = (
            await session.scalar(
                text(
                    "SELECT queued.status='todo' OR (queued.status='doing' AND EXISTS ("
                    "SELECT 1 FROM procrastinate_workers AS worker WHERE worker.id=queued.worker_id "
                    "AND worker.last_heartbeat>now()-interval '30 seconds')) "
                    "FROM procrastinate_jobs AS queued WHERE queued.id=:id "
                    "AND queued.task_name='bid.process' AND queued.queue_name='bid' "
                    "AND queued.args->>'job_id'=:job"
                ),
                {"id": job.queue_id, "job": str(job.id)},
            )
            if job.queue_id is not None
            else False
        )
        if not runnable:
            from app.models.entities import VendorCall

            unresolved = await session.scalar(
                select(VendorCall.id)
                .where(
                    VendorCall.job_id == job.id,
                    VendorCall.state.in_(["pending", "unknown"]),
                )
                .limit(1)
            )
            if unresolved is not None:
                raise ServiceError(
                    "job_recovery_required",
                    "Verify unresolved vendor calls before rescheduling this job",
                    409,
                    4,
                )
            job.queue_id = await self.enqueue_in_transaction(session, str(job.id))

    async def recover_process_jobs(self, *, limit: int = 100) -> None:
        """Recover only queued ordinary jobs discovered through abandoned deliveries.

        Running attempts and unresolved vendor calls require their explicit
        recovery flow, even when a worker heartbeat or application lease expired.
        """
        from app.jobs.execution import locked_job
        from app.jobs.processor import Processor
        from app.models.entities import VendorCall

        if not isinstance(self.processor, Processor):
            raise RuntimeError("Worker processor is not configured")
        limit = max(1, min(limit, 100))
        async with self.processor.db.transaction() as session:
            candidates = (
                await session.execute(
                    text(
                        "SELECT queued.id, queued.args FROM procrastinate_jobs AS queued "
                        "WHERE queued.id>:after AND queued.task_name='bid.process' "
                        "AND queued.queue_name='bid' AND queued.status='doing' "
                        "AND NOT EXISTS (SELECT 1 FROM procrastinate_workers AS live "
                        "WHERE live.id=queued.worker_id "
                        "AND live.last_heartbeat>now()-interval '30 seconds') "
                        "ORDER BY queued.id LIMIT :limit"
                    ),
                    {"after": self._process_recovery_cursor, "limit": limit},
                )
            ).all()
        # Retained/invalid rows cannot permanently hide later recoverable jobs.
        self._process_recovery_cursor = candidates[-1].id if len(candidates) == limit else 0
        recovered = 0
        for candidate in candidates:
            try:
                job_id = UUID(str(candidate.args.get("job_id")))
            except (AttributeError, ValueError):
                continue
            async with self.processor.db.transaction() as session:
                job = await locked_job(session, job_id)
                if (
                    job is None
                    or job.queue_id != candidate.id
                    or job.status != "queued"
                    or job.kind in {"agent", "sandbox"}
                    or job.agent_session_id is not None
                ):
                    continue
                unresolved = await session.scalar(
                    select(VendorCall.id)
                    .where(
                        VendorCall.job_id == job.id,
                        VendorCall.state.in_(["pending", "unknown"]),
                    )
                    .limit(1)
                )
                if unresolved is not None:
                    continue
                delivery = await session.scalar(
                    text(
                        "SELECT queued.id FROM procrastinate_jobs AS queued "
                        "WHERE queued.id=:id AND queued.status='doing' "
                        "AND queued.task_name='bid.process' AND queued.queue_name='bid' "
                        "AND queued.args->>'job_id'=:job "
                        "AND NOT EXISTS (SELECT 1 FROM procrastinate_workers AS live "
                        "WHERE live.id=queued.worker_id "
                        "AND live.last_heartbeat>now()-interval '30 seconds') "
                        "FOR UPDATE OF queued SKIP LOCKED"
                    ),
                    {"id": candidate.id, "job": str(job_id)},
                )
                if delivery is None:
                    continue
                await self.ensure_process_delivery(session, job)
                if job.queue_id != delivery:
                    await session.execute(
                        text("SELECT procrastinate_finish_job_v1(:id, 'aborted', false)"),
                        {"id": delivery},
                    )
                    recovered += 1
        logger.info(
            "Process queue reconciliation: checked=%s recovered=%s", len(candidates), recovered
        )

    async def enqueue_agent_wake(
        self, session: AsyncSession, session_id: str, *, delay: int = 30
    ) -> int:
        key = f"agent-wake:{session_id}"
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
        ).defer_async(session_id=session_id)

    async def recover_agent_wakes(self) -> None:
        """Only queue metadata is global; business recovery validates application state."""
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

    async def enqueue(self, job_id: str) -> int:
        async with self.lock, self.app.open_async():
            return await self.task.defer_async(job_id=job_id)

    async def recover_sandbox_jobs(self) -> None:
        """Close abandoned deliveries only after application checks."""
        from app.jobs.processor import Processor

        if not isinstance(self.processor, Processor):
            raise RuntimeError("Worker processor is not configured")
        candidates = await self.app.job_manager.get_stalled_jobs(
            queue="bid", task_name="bid.sandbox"
        )
        counts = {"checked": 0, "terminal": 0, "missing": 0, "retained": 0, "invalid": 0}
        end_statuses = {
            "succeeded": Status.SUCCEEDED,
            "failed": Status.FAILED,
            "cancelled": Status.ABORTED,
        }
        for candidate in candidates:
            counts["checked"] += 1
            try:
                job_id = UUID(str(candidate.task_kwargs.get("job_id")))
            except ValueError:
                counts["invalid"] += 1
                continue
            async with self.processor.db.transaction() as session:
                # Serialize recovery with queue completion/retry and other starters;
                # recheck the heartbeat because it may have resumed since the scan.
                queued = await session.scalar(
                    text(
                        "SELECT queued.id FROM procrastinate_jobs AS queued "
                        "WHERE queued.id=:id AND queued.status='doing' "
                        "AND queued.task_name='bid.sandbox' AND queued.queue_name='bid' "
                        "AND NOT EXISTS (SELECT 1 FROM procrastinate_workers AS live "
                        "WHERE live.id=queued.worker_id "
                        "AND live.last_heartbeat>=now()-interval '30 seconds') "
                        "FOR UPDATE OF queued SKIP LOCKED"
                    ),
                    {"id": candidate.id},
                )
                if queued is None:
                    counts["retained"] += 1
                    continue
                job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
                if job is not None and (job.kind != "sandbox" or job.status not in end_statuses):
                    counts["retained"] += 1
                    continue
                status = end_statuses[job.status] if job is not None else Status.ABORTED
                # Use Procrastinate's finish API on this locked transaction, retaining
                # its attempt/event bookkeeping. CANCELLED only applies to todo rows.
                await session.execute(
                    text("SELECT procrastinate_finish_job_v1(:id, :status, false)"),
                    {"id": queued, "status": status.value},
                )
            counts["missing" if job is None else "terminal"] += 1
        logger.info(
            "Sandbox queue reconciliation: checked=%(checked)d terminal=%(terminal)d "
            "missing=%(missing)d retained=%(retained)d invalid=%(invalid)d",
            counts,
        )

    async def enqueue_sandbox(self, job_id: str) -> int:
        async with self.lock, self.app.open_async():
            return await self.sandbox_task.defer_async(job_id=job_id)
