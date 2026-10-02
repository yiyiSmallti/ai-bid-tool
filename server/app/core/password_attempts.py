"""Shared, database-serialized password attempts with bounded CPU admission."""

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Database
from app.core.errors import ServiceError, log_unexpected
from app.core.security import verify_password
from app.models.entities import PlatformAuditLog, User

MAX_FAILURES = 5
MAX_SOURCE_FAILURES = 30
FAILURE_WINDOW = timedelta(minutes=15)
PASSWORD_WORKERS = 4
PASSWORD_QUEUE = 8
QUEUE_SECONDS = 1.0
LOGIN_ACTIONS = ("auth.password", "platform.login")
DUMMY_HASH = "pbkdf2$600000$MDAwMDAwMDAwMDAwMDAwMA==$MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
SecondFactor = Callable[[AsyncSession, User], Awaitable[dict]]
logger = logging.getLogger(__name__)


def invalid_login() -> ServiceError:
    return ServiceError("invalid_login", "Invalid credentials", 401, 4)


def busy() -> ServiceError:
    return ServiceError("auth_busy", "Sign-in capacity is busy; try again later", 503, 3)


def lock_key(value: str) -> int:
    # Stable across workers/restarts; a collision only serializes unrelated work.
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], signed=True)


class PasswordAttempts:
    def __init__(self, db: Database):
        self.db = db
        self.executor = ThreadPoolExecutor(
            max_workers=PASSWORD_WORKERS, thread_name_prefix="password"
        )
        self.slots = asyncio.Semaphore(PASSWORD_WORKERS)
        self.tasks: set[asyncio.Task[User]] = set()
        self.closed = False

    @property
    def pending(self) -> int:
        return len(self.tasks)

    async def close(self) -> None:
        self.closed = True
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.executor.shutdown(wait=True)

    def finished(self, task: asyncio.Task[User]) -> None:
        self.tasks.remove(task)
        # Disconnected requests still finish/account for work. Retrieve their
        # exception as well; connected requests receive it through shield().
        if not task.cancelled():
            error = task.exception()
            if error is not None and not isinstance(error, ServiceError):
                log_unexpected(logger, "Password attempt", error)

    async def authenticate(
        self,
        email: str,
        password: str,
        source: str | None,
        second_factor: SecondFactor | None = None,
    ) -> User:
        email = email.strip().lower()
        # Unicode lowercasing can expand an input that passed the wire length
        # check. Reject it explicitly before it can overflow the audit column.
        if len(email) > 254:
            raise ServiceError(
                "invalid_input", "Invalid or missing request parameters: email", 422, 2
            )
        if self.closed or self.pending >= PASSWORD_WORKERS + PASSWORD_QUEUE:
            raise busy()
        # No await between admission and registration, so the queue cannot overfill.
        task = asyncio.create_task(self.run(email, password, source, second_factor))
        self.tasks.add(task)
        task.add_done_callback(self.finished)
        # Cancellation cannot stop an already-running PBKDF2 thread. Keep its
        # admission and transaction until it finishes, including failure accounting.
        return await asyncio.shield(task)

    async def run(
        self, email: str, password: str, source: str | None, second_factor: SecondFactor | None
    ) -> User:
        try:
            await asyncio.wait_for(self.slots.acquire(), timeout=QUEUE_SECONDS)
        except TimeoutError:
            raise busy() from None
        try:
            return await self.check(email, password, source, second_factor)
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise busy() from None
            raise
        finally:
            self.slots.release()

    async def failures(self, session: AsyncSession, actor: str, actions: tuple[str, ...]) -> int:
        return (
            await session.execute(
                select(func.count())
                .select_from(PlatformAuditLog)
                .where(
                    PlatformAuditLog.actor_email == actor,
                    PlatformAuditLog.action.in_(actions),
                    PlatformAuditLog.outcome == "denied",
                    PlatformAuditLog.created_at >= func.statement_timestamp() - FAILURE_WINDOW,
                )
            )
        ).scalar_one()

    async def check(
        self, email: str, password: str, source: str | None, second_factor: SecondFactor | None
    ) -> User:
        source_actor = "source:" + hashlib.sha256(source.encode()).hexdigest() if source else None
        action = "platform.login" if second_factor is not None else "auth.password"
        failure: ServiceError | None = None
        async with self.db.transaction() as session:
            # A waiter must see its predecessor's commit even if the database's
            # deployment default uses a repeatable transaction snapshot.
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
            # Always take source before account, including for absent identities.
            # The lock and failure/success writes share the same transaction.
            await session.execute(text("SET LOCAL lock_timeout = '1s'"))
            for key in ([f"source:{source}"] if source else []) + [f"account:{email}"]:
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key(key)}
                )
            if await self.failures(session, email, LOGIN_ACTIONS) >= MAX_FAILURES or (
                source_actor is not None
                and await self.failures(session, source_actor, ("auth.source",))
                >= MAX_SOURCE_FAILURES
            ):
                raise ServiceError(
                    "too_many_attempts", "Too many failed sign-ins; try again later", 429, 3
                )

            # Deployment-wide PBKDF2 slots also bound separate API processes.
            # Never queue threads/connections on these slots when all are occupied.
            for slot in range(PASSWORD_WORKERS):
                if await session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(:key)"),
                    {"key": lock_key(f"password-slot:{slot}")},
                ):
                    break
            else:
                raise busy()

            user = await session.scalar(
                select(User).where(User.email == email, User.active.is_(True))
            )
            # Setup-only identities must not skip the expensive work either.
            encoded = user.password_hash if user is not None else DUMMY_HASH
            if encoded == "!setup":
                encoded = DUMMY_HASH
            valid = await asyncio.get_running_loop().run_in_executor(
                self.executor, verify_password, password, encoded
            )
            details: dict = {}
            if user is None or not valid:
                failure = invalid_login()
            elif second_factor is not None:
                try:
                    details = await second_factor(session, user)
                except ServiceError as exc:
                    if exc.code != "invalid_login":
                        raise
                    failure = exc

            if failure is not None or second_factor is not None:
                session.add(
                    PlatformAuditLog(
                        actor_email=email,
                        action=action,
                        outcome="denied" if failure is not None else "success",
                        details={} if failure is not None else details,
                        created_at=func.clock_timestamp(),
                    )
                )
            if failure is not None and source_actor is not None:
                session.add(
                    PlatformAuditLog(
                        actor_email=source_actor,
                        action="auth.source",
                        outcome="denied",
                        details={},
                        created_at=func.clock_timestamp(),
                    )
                )
        # Raising inside the transaction would erase the failure and unlock it.
        if failure is not None:
            raise failure
        assert user is not None
        return user
