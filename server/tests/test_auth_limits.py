"""Password admission and TOTP replay protection through the real API and PostgreSQL.

Failure modes: cross-route/case/source bypass; concurrent overshoot; unknown,
inactive and setup-only identities; normalization overflow; success resetting failures; expired windows;
source spraying; queue overflow/timeouts; cancellation releasing running work;
cross-app TOTP replay and password capacity bypass. No database substitutes.
"""

import asyncio
import threading
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from app.api.main import create_app
from app.core import password_attempts
from app.core.totp import code_at
from app.models.entities import PlatformAuditLog, User
from conftest import OPERATOR, OPERATOR_PASSWORD, FakeQueue
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_platform_auth import SECRET, now_counter, platform_settings

PATHS = ("/auth/orgs", "/auth/login", "/platform/auth/login")


@pytest.fixture
async def clients(operator, tmp_path):
    apps = [create_app(platform_settings(tmp_path), queue=FakeQueue()) for _ in range(2)]
    async with apps[0].router.lifespan_context(apps[0]), apps[1].router.lifespan_context(apps[1]):
        async with (
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=apps[0], client=("192.0.2.1", 1)),
                base_url="http://test",
            ) as first,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=apps[1], client=("192.0.2.2", 1)),
                base_url="http://test",
            ) as second,
        ):
            yield apps, (first, second)


def payload(path, tenants, email=OPERATOR, password="wrong-password", counter=None):
    body = {"email": email, "password": password}
    if path == "/auth/login":
        body["org_id"] = str(tenants["orgs"][0])
    if path == "/platform/auth/login":
        body["totp"] = code_at(SECRET, now_counter() if counter is None else counter)
    return body


def error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["data"]["error"]["code"] == code
    if status in (429, 503):
        assert response.json()["data"]["error"]["exit_code"] == 3
        assert response.headers["Retry-After"] == ("900" if status == 429 else "1")


@pytest.mark.parametrize("entry", PATHS)
@pytest.mark.parametrize("email", [OPERATOR, "absent@example.test"])
async def test_every_entry_shares_normalized_lock_across_apps(
    clients, tenants, monkeypatch, entry, email
):
    _, (first, second) = clients
    for i in range(password_attempts.MAX_FAILURES):
        variant = f" {email.upper()} " if i % 2 else email
        response = await first.post(entry, json=payload(entry, tenants, variant))
        error(response, 401, "invalid_login")

    def forbidden(*args):
        pytest.fail("A locked account must not run PBKDF2")

    monkeypatch.setattr(password_attempts, "verify_password", forbidden)
    responses = []
    for path in PATHS:
        response = await second.post(path, json=payload(path, tenants, email, OPERATOR_PASSWORD))
        error(response, 429, "too_many_attempts")
        responses.append(response.json()["data"])
    assert responses[0] == responses[1] == responses[2]


async def test_concurrent_fifth_failure_is_atomic_across_apps(clients, tenants, admin_engine):
    _, apis = clients
    for i in range(password_attempts.MAX_FAILURES - 1):
        path = PATHS[i % len(PATHS)]
        error(await apis[0].post(path, json=payload(path, tenants)), 401, "invalid_login")
    responses = await asyncio.gather(
        *[apis[i % 2].post(path, json=payload(path, tenants)) for i, path in enumerate(PATHS)]
    )
    assert sorted(r.status_code for r in responses) == [401, 429, 429]
    with Session(admin_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(PlatformAuditLog)
                .where(
                    PlatformAuditLog.actor_email == OPERATOR,
                    PlatformAuditLog.outcome == "denied",
                )
            )
            == password_attempts.MAX_FAILURES
        )


async def test_success_does_not_clear_failures(clients, tenants):
    _, (api, _) = clients
    for _ in range(password_attempts.MAX_FAILURES - 1):
        error(await api.post(PATHS[0], json=payload(PATHS[0], tenants)), 401, "invalid_login")
    assert (
        await api.post(PATHS[0], json=payload(PATHS[0], tenants, password=OPERATOR_PASSWORD))
    ).status_code == 200
    error(await api.post(PATHS[1], json=payload(PATHS[1], tenants)), 401, "invalid_login")
    error(
        await api.post(PATHS[2], json=payload(PATHS[2], tenants, password=OPERATOR_PASSWORD)),
        429,
        "too_many_attempts",
    )


async def test_totp_failures_also_lock_org_entry_points(clients, tenants):
    _, (first, second) = clients
    for _ in range(password_attempts.MAX_FAILURES):
        response = await first.post(
            PATHS[2],
            json=payload(PATHS[2], tenants, password=OPERATOR_PASSWORD, counter=now_counter() + 5),
        )
        error(response, 401, "invalid_login")
    for path in PATHS:
        error(
            await second.post(path, json=payload(path, tenants, password=OPERATOR_PASSWORD)),
            429,
            "too_many_attempts",
        )


async def test_expanded_account_is_rejected_before_password_work(clients, tenants, monkeypatch):
    _, (api, _) = clients

    def forbidden(*args):
        pytest.fail("Invalid normalized account must not run PBKDF2")

    monkeypatch.setattr(password_attempts, "verify_password", forbidden)
    for path in PATHS:
        error(await api.post(path, json=payload(path, tenants, "İ" * 254)), 422, "invalid_input")


async def test_expired_and_legacy_failures(clients, tenants, admin_engine):
    _, (api, _) = clients
    with Session(admin_engine) as session, session.begin():
        for _ in range(password_attempts.MAX_FAILURES):
            session.add(
                PlatformAuditLog(
                    actor_email=OPERATOR,
                    action="platform.login",
                    outcome="denied",
                    details={},
                    created_at=datetime.now(UTC) - timedelta(minutes=16),
                )
            )
    assert (
        await api.post(PATHS[0], json=payload(PATHS[0], tenants, password=OPERATOR_PASSWORD))
    ).status_code == 200
    with Session(admin_engine) as session, session.begin():
        for _ in range(password_attempts.MAX_FAILURES):
            session.add(
                PlatformAuditLog(
                    actor_email=OPERATOR, action="platform.login", outcome="denied", details={}
                )
            )
    error(await api.post(PATHS[0], json=payload(PATHS[0], tenants)), 429, "too_many_attempts")


@pytest.mark.parametrize("path", PATHS)
async def test_missing_inactive_and_setup_accounts_are_uniform(
    clients, tenants, admin_engine, path
):
    _, (api, _) = clients
    with Session(admin_engine) as session, session.begin():
        session.add_all(
            [
                User(
                    id=uuid4(), email="inactive@example.test", password_hash="!setup", active=False
                ),
                User(id=uuid4(), email="setup@example.test", password_hash="!setup"),
            ]
        )
    results = []
    for email in (OPERATOR, "absent@example.test", "inactive@example.test", "setup@example.test"):
        response = await api.post(path, json=payload(path, tenants, email))
        error(response, 401, "invalid_login")
        results.append(response.json()["data"])
    assert all(item == results[0] for item in results)


async def test_source_limit_survives_account_rotation_and_ignores_forwarded_headers(
    clients, tenants, monkeypatch
):
    _, (first, second) = clients
    monkeypatch.setattr(password_attempts, "MAX_SOURCE_FAILURES", 3)
    for i, path in enumerate(PATHS):
        response = await first.post(
            path,
            json=payload(path, tenants, f"spray-{i}@example.test"),
            headers={"X-Forwarded-For": f"198.51.100.{i}"},
        )
        error(response, 401, "invalid_login")
    for path in PATHS:
        error(await first.post(path, json=payload(path, tenants)), 429, "too_many_attempts")
    error(await second.post(PATHS[0], json=payload(PATHS[0], tenants)), 401, "invalid_login")


@asynccontextmanager
async def stalled_passwords(monkeypatch):
    original = password_attempts.verify_password
    release = threading.Event()
    entered = 0
    lock = threading.Lock()

    def stalled(password, encoded):
        nonlocal entered
        with lock:
            entered += 1
        if not release.wait(10):
            raise TimeoutError("Test did not release password verification")
        return original(password, encoded)

    monkeypatch.setattr(password_attempts, "verify_password", stalled)
    try:
        yield lambda: entered, release
    finally:
        release.set()


async def eventually(predicate):
    async with asyncio.timeout(3):
        # Observe worker-thread/admission state; neither is an asyncio event.
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0.01)


async def test_bounded_queue_fails_fast_and_cancellation_keeps_capacity(
    clients, tenants, monkeypatch
):
    apps, (api, _) = clients
    attempts = apps[0].state.password_attempts
    async with stalled_passwords(monkeypatch) as (entered, release):
        # Omit source metadata so its serialization cannot mask CPU capacity.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=apps[0], client=None), base_url="http://test"
        ) as anonymous:
            requests = [
                asyncio.create_task(
                    anonymous.post(
                        PATHS[i % 3], json=payload(PATHS[i % 3], tenants, f"busy-{i}@example.test")
                    )
                )
                for i in range(password_attempts.PASSWORD_WORKERS)
            ]
            await eventually(lambda: entered() == password_attempts.PASSWORD_WORKERS)
            queued = [
                asyncio.create_task(
                    api.post(
                        PATHS[i % 3],
                        json=payload(PATHS[i % 3], tenants, f"queued-{i}@example.test"),
                    )
                )
                for i in range(password_attempts.PASSWORD_QUEUE)
            ]
            await eventually(
                lambda: (
                    attempts.pending
                    == password_attempts.PASSWORD_WORKERS + password_attempts.PASSWORD_QUEUE
                )
            )
            for path in PATHS:
                response = await asyncio.wait_for(api.post(path, json=payload(path, tenants)), 0.5)
                error(response, 503, "auth_busy")
            requests[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await requests[0]
            assert (
                attempts.pending
                == password_attempts.PASSWORD_WORKERS + password_attempts.PASSWORD_QUEUE
            )
            timed_out = await asyncio.gather(*queued)
            assert all(r.status_code == 503 for r in timed_out)
            assert entered() == password_attempts.PASSWORD_WORKERS
            release.set()
            await asyncio.gather(*requests[1:])
            await eventually(lambda: attempts.pending == 0)
            error(await api.post(PATHS[0], json=payload(PATHS[0], tenants)), 401, "invalid_login")


async def test_password_capacity_is_shared_across_apps(clients, tenants, monkeypatch):
    apps, (_, second) = clients
    # A queued request would wait far longer than the bound below, so passing it still
    # means an immediate refusal even on a busy CI machine.
    monkeypatch.setattr(password_attempts, "QUEUE_SECONDS", 30.0)
    async with stalled_passwords(monkeypatch) as (entered, release):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=apps[0], client=None), base_url="http://test"
        ) as first:
            requests = [
                asyncio.create_task(
                    first.post(
                        PATHS[0], json=payload(PATHS[0], tenants, f"global-{i}@example.test")
                    )
                )
                for i in range(password_attempts.PASSWORD_WORKERS)
            ]
            await eventually(lambda: entered() == password_attempts.PASSWORD_WORKERS)
            for path in PATHS:
                error(
                    await asyncio.wait_for(second.post(path, json=payload(path, tenants)), 5),
                    503,
                    "auth_busy",
                )
            assert entered() == password_attempts.PASSWORD_WORKERS
            release.set()
            await asyncio.gather(*requests)


async def test_concurrent_totp_consumed_once_across_apps(clients, tenants, admin_engine):
    _, apis = clients
    counter = now_counter()
    body = payload(PATHS[2], tenants, password=OPERATOR_PASSWORD, counter=counter)
    responses = await asyncio.gather(*(api.post(PATHS[2], json=body) for api in apis))
    assert sorted(r.status_code for r in responses) == [200, 401]
    assert sum("session" in r.json()["data"] for r in responses) == 1
    with Session(admin_engine) as session:
        successes = session.scalars(
            select(PlatformAuditLog).where(
                PlatformAuditLog.action == "platform.login", PlatformAuditLog.outcome == "success"
            )
        ).all()
        assert len(successes) == 1 and successes[0].details["totp_counter"] == counter
    body["totp"] = code_at(SECRET, counter + 1)
    assert (await apis[0].post(PATHS[2], json=body)).status_code == 200
