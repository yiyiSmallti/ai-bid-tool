"""Member sign-in with database-serialized attempt limits and bounded CPU admission.

Failures count per login and per source address inside a sliding window; at
the limit a sign-in is refused before the password is checked. PBKDF2 runs on
a small thread pool, and deployment-wide advisory-lock slots bound it across
API processes too. An unknown or disabled login verifies against a dummy hash
so the response time does not reveal it.
"""

import asyncio
import hashlib
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any, TypeVar

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Database
from app.core.errors import ServiceError, log_unexpected
from app.core.security import hash_password, verify_password
from app.models.members import LoginAttempt, Member

MAX_FAILURES = 5
MAX_SOURCE_FAILURES = 30
FAILURE_WINDOW = timedelta(minutes=15)
PASSWORD_WORKERS = 4
PASSWORD_QUEUE = 8
QUEUE_SECONDS = 1.0
DUMMY_HASH = "pbkdf2$600000$MDAwMDAwMDAwMDAwMDAwMA==$MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="
logger = logging.getLogger(__name__)
T = TypeVar("T")


def invalid_login() -> ServiceError:
    return ServiceError("invalid_login", "Invalid login or password", 401, 4)


def busy() -> ServiceError:
    return ServiceError("auth_busy", "Sign-in capacity is busy; try again later", 503, 3)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def lock_key(value: str) -> int:
    # Stable across workers and restarts; a collision only serializes unrelated work.
    return int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], signed=True)


class PasswordAttempts:
    def __init__(self, db: Database):
        self.db = db
        self.executor = ThreadPoolExecutor(
            max_workers=PASSWORD_WORKERS, thread_name_prefix="password"
        )
        self.slots = asyncio.Semaphore(PASSWORD_WORKERS)
        self.tasks: set[asyncio.Task[Any]] = set()
        self.closed = False

    async def close(self) -> None:
        self.closed = True
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.executor.shutdown(wait=True)

    def finished(self, task: asyncio.Task[Any]) -> None:
        self.tasks.remove(task)
        # A disconnected request still finishes and records its attempt; retrieve
        # its exception too. Connected requests receive it through shield().
        if not task.cancelled():
            error = task.exception()
            if error is not None and not isinstance(error, ServiceError):
                log_unexpected(logger, "Password attempt", error)

    async def admit(self, work: Callable[[], Any]) -> Any:
        if self.closed or len(self.tasks) >= PASSWORD_WORKERS + PASSWORD_QUEUE:
            raise busy()
        # No await between admission and registration, so the queue cannot overfill.
        task = asyncio.create_task(self.slotted(work))
        self.tasks.add(task)
        task.add_done_callback(self.finished)
        # Cancellation cannot stop a running PBKDF2 thread; keep its admission
        # and transaction until it finishes, including failure accounting.
        return await asyncio.shield(task)

    async def slotted(self, work: Callable[[], Any]) -> Any:
        try:
            await asyncio.wait_for(self.slots.acquire(), timeout=QUEUE_SECONDS)
        except TimeoutError:
            raise busy() from None
        try:
            return await work()
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "55P03":
                raise busy() from None
            raise
        finally:
            self.slots.release()

    async def database_slot(self, session: AsyncSession) -> None:
        for slot in range(PASSWORD_WORKERS):
            if await session.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": lock_key(f"password-slot:{slot}")},
            ):
                return
        raise busy()

    async def run(self, function: Callable[..., T], *arguments: Any) -> T:
        return await asyncio.get_running_loop().run_in_executor(self.executor, function, *arguments)

    async def hash(self, password: str) -> str:
        async def work():
            async with self.db.transaction() as session:
                await self.database_slot(session)
                return await self.run(hash_password, password)

        return await self.admit(work)

    async def verify(self, password: str, encoded: str) -> bool:
        async def work():
            async with self.db.transaction() as session:
                await self.database_slot(session)
                return await self.run(verify_password, password, encoded)

        return await self.admit(work)

    async def sign_in(self, login: str, password: str, source: str | None) -> Member:
        login = login.strip().lower()
        return await self.admit(lambda: self.check(login, password, source))

    async def failures(self, session: AsyncSession, column, value: str, limit: int) -> bool:
        count = await session.scalar(
            select(func.count())
            .select_from(LoginAttempt)
            .where(
                column == value,
                LoginAttempt.outcome == "failure",
                LoginAttempt.created_at >= func.clock_timestamp() - FAILURE_WINDOW,
            )
        )
        return (count or 0) >= limit

    async def check(self, login: str, password: str, source: str | None) -> Member:
        login_digest = digest(login)
        source_digest = digest(source) if source else None
        failure: ServiceError | None = None
        async with self.db.transaction() as session:
            # A waiter must see its predecessor's commit even under a repeatable
            # default isolation level.
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
            await session.execute(text("SET LOCAL lock_timeout = '1s'"))
            # Source before login, always, so concurrent attempts cannot deadlock.
            for key in ([f"source:{source_digest}"] if source_digest else []) + [
                f"login:{login_digest}"
            ]:
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key(key)}
                )
            if await self.failures(
                session, LoginAttempt.login_digest, login_digest, MAX_FAILURES
            ) or (
                source_digest is not None
                and await self.failures(
                    session, LoginAttempt.source_digest, source_digest, MAX_SOURCE_FAILURES
                )
            ):
                raise ServiceError(
                    "too_many_attempts", "Too many failed sign-ins; try again later", 429, 3
                )
            await self.database_slot(session)
            member = await session.scalar(
                select(Member).where(func.lower(Member.login) == login, Member.status == "active")
            )
            encoded = member.password_hash if member is not None else DUMMY_HASH
            valid = await self.run(verify_password, password, encoded)
            if member is None or not valid:
                failure = invalid_login()
            session.add(
                LoginAttempt(
                    login_digest=login_digest,
                    source_digest=source_digest,
                    outcome="failure" if failure is not None else "success",
                )
            )
        # Raising inside the transaction would erase the failure and unlock it.
        if failure is not None:
            raise failure
        assert member is not None
        return member
