"""Platform operator identity, login with TOTP, audit and one-time password setup."""

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError
from app.core.security import Secrets, hash_password, verify_password
from app.core.totp import matching_counter
from app.models.entities import PlatformAuditLog, User

LOGIN_ACTION = "platform.login"
MAX_FAILURES = 5
FAILURE_WINDOW = timedelta(minutes=15)
SETUP_SECONDS = 24 * 3600
UNUSABLE_PASSWORD = "!setup"
DUMMY_HASH = "pbkdf2$600000$MDAwMDAwMDAwMDAwMDAwMA==$MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="


@dataclass
class PlatformIdentity:
    email: str


def audit(
    session: AsyncSession,
    actor: str,
    action: str,
    outcome: str,
    object_id: str | None = None,
    details: dict | None = None,
) -> None:
    session.add(
        PlatformAuditLog(
            id=uuid4(),
            actor_email=actor[:254],
            action=action,
            object_id=object_id,
            outcome=outcome,
            details=details or {},
        )
    )


async def record_failure(db: Database, email: str, reason: str) -> None:
    # Failures are committed separately because the request transaction rolls back.
    async with db.transaction() as session:
        audit(session, email, LOGIN_ACTION, "denied", details={"reason": reason})


async def login(
    db: Database, settings: Settings, crypto: Secrets, email: str, password: str, code: str
) -> dict:
    email = email.strip().lower()
    since = datetime.now(UTC) - FAILURE_WINDOW
    async with db.transaction() as session:
        failures = await session.scalar(
            select(func.count())
            .select_from(PlatformAuditLog)
            .where(
                PlatformAuditLog.actor_email == email[:254],
                PlatformAuditLog.action == LOGIN_ACTION,
                PlatformAuditLog.outcome != "success",
                PlatformAuditLog.created_at >= since,
            )
        )
        if (failures or 0) >= MAX_FAILURES:
            raise ServiceError(
                "too_many_attempts", "Too many failed sign-ins; try again later", 429, 3
            )
        user = await session.scalar(select(User).where(User.email == email, User.active.is_(True)))
        last_counter = await session.scalar(
            select(func.max(PlatformAuditLog.details["totp_counter"].as_integer())).where(
                PlatformAuditLog.actor_email == email,
                PlatformAuditLog.action == LOGIN_ACTION,
                PlatformAuditLog.outcome == "success",
            )
        )
    secret = settings.platform_totp().get(email)
    password_ok = await asyncio.to_thread(
        verify_password, password, user.password_hash if user else DUMMY_HASH
    )
    counter = matching_counter(secret, code) if secret else None
    # One generic answer for every failure, so the response reveals nothing about which check failed.
    if email not in settings.platform_admins() or user is None or not password_ok:
        await record_failure(db, email, "credentials")
        raise ServiceError("invalid_login", "Invalid credentials", 401, 4)
    if counter is None or (last_counter is not None and counter <= last_counter):
        await record_failure(db, email, "totp")
        raise ServiceError("invalid_login", "Invalid credentials", 401, 4)
    async with db.transaction() as session:
        audit(session, email, LOGIN_ACTION, "success", details={"totp_counter": counter})
    return {
        "session": crypto.issue(
            {"kind": "platform", "email": email}, settings.platform_session_seconds
        ),
        "email": email,
        "expires_in": settings.platform_session_seconds,
    }


def identify(settings: Settings, crypto: Secrets, bearer: str) -> PlatformIdentity:
    payload = crypto.open(bearer)
    email = payload.get("email")
    # Removing an operator from the deployment config revokes their sessions at once.
    if payload.get("kind") != "platform" or email not in settings.platform_admins():
        raise ServiceError("invalid_session", "Invalid credentials", 401, 4)
    return PlatformIdentity(email=email)


def fingerprint(password_hash: str) -> str:
    return hashlib.sha256(password_hash.encode()).hexdigest()[:32]


def setup_token(crypto: Secrets, user_id: UUID, password_hash: str) -> str:
    # Bound to the current hash, so the link stops working once a password is set.
    return crypto.issue(
        {"kind": "password-setup", "user_id": str(user_id), "fp": fingerprint(password_hash)},
        SETUP_SECONDS,
    )


async def setup_password(db: Database, crypto: Secrets, token: str, password: str) -> None:
    invalid = ServiceError("invalid_setup_link", "Setup link is invalid or expired", 400, 4)
    try:
        payload = crypto.open(token)
    except ServiceError:
        raise invalid from None
    if payload.get("kind") != "password-setup":
        raise invalid
    if len(password) < 12:
        raise ServiceError("weak_password", "Password must have at least 12 characters", 400, 2)
    async with db.transaction() as session:
        user = await session.scalar(
            select(User).where(User.id == UUID(payload["user_id"])).with_for_update()
        )
        if user is None or not user.active or fingerprint(user.password_hash) != payload.get("fp"):
            raise invalid
        user.password_hash = await asyncio.to_thread(hash_password, password)
