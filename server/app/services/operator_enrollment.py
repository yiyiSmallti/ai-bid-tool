"""Unstored enrollment links and transactional operator factor binding."""

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError, not_found
from app.core.operator_secrets import OperatorSecrets
from app.core.password_attempts import PasswordAttempts, invalid_login
from app.core.security import TokenSigner, token_digest
from app.core.totp import generate_secret, matching_counter, provisioning_uri
from app.models.entities import User
from app.schemas.operator_enrollment import (
    LINK_TTL,
    PASSWORD_MAX,
    PASSWORD_MIN,
    EnrollmentComplete,
    EnrollmentLink,
    EnrollmentLinkRequest,
    EnrollmentResult,
    EnrollmentStart,
    EnrollmentStartRequest,
    OperatorStatus,
)
from app.services.platform import UNUSABLE_PASSWORD, audit


def invalid_link() -> ServiceError:
    return ServiceError("invalid_enrollment_link", "Enrollment link is invalid or expired", 400, 4)


def state_fingerprint(password_hash: str | None, generation: int | None) -> str:
    return hashlib.sha256(json.dumps([password_hash, generation]).encode()).hexdigest()


async def factor_row(session: AsyncSession, email: str):
    return (
        (
            await session.execute(
                text("SELECT * FROM public.platform_operator_factor(:email)"), {"email": email}
            )
        )
        .mappings()
        .first()
    )


async def factor_secret(settings: Settings, session: AsyncSession, email: str) -> str | None:
    secret = settings.platform_totp().get(email)
    if secret:
        return secret
    row = await factor_row(session, email)
    if row is None:
        return None
    return OperatorSecrets(settings).decrypt(
        row["secret_ciphertext"], email, row["generation"], row["key_version"]
    )


class OperatorEnrollmentService:
    def __init__(
        self, db: Database, settings: Settings, crypto: TokenSigner, attempts: PasswordAttempts
    ):
        self.db, self.settings, self.crypto, self.attempts = db, settings, crypto, attempts

    def eligible(self, email: str, *, link: bool = False) -> None:
        if email not in self.settings.platform_admins():
            raise invalid_link() if link else not_found()
        if email in self.settings.platform_totp():
            raise ServiceError(
                "factor_managed_by_deployment", "This factor is managed by deployment", 409, 4
            )

    async def state(self, session: AsyncSession, email: str, *, lock: bool = False) -> dict:
        if lock:
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM public.platform_operator_lock(:email)"),
                        {"email": email},
                    )
                )
                .mappings()
                .one()
            )
            return dict(row)
        user = await session.scalar(select(User).where(User.email == email))
        factor = await factor_row(session, email)
        return {
            "account_exists": user is not None,
            "active": user.active if user else True,
            "password_hash": user.password_hash if user else None,
            "generation": factor["generation"] if factor else None,
        }

    async def list_operators(self, operator_email: str) -> list[OperatorStatus]:
        if operator_email not in self.settings.platform_admins():
            raise not_found()
        async with self.db.transaction() as session:
            rows = (
                (
                    await session.execute(
                        text("SELECT * FROM public.platform_operator_status(:emails)"),
                        {"emails": sorted(set(self.settings.platform_admins()))},
                    )
                )
                .mappings()
                .all()
            )
        environment = self.settings.platform_totp()
        return [
            OperatorStatus(
                email=row["email"],
                has_account=row["has_account"],
                factor_source="environment"
                if row["email"] in environment
                else "database"
                if row["generation"] is not None
                else "none",
                enrolled_at=row["enrolled_at"],
                enrolled_by=row["enrolled_by"],
            )
            for row in rows
        ]

    async def issue_link(self, issuer: str, body: EnrollmentLinkRequest) -> EnrollmentLink:
        email = body.email
        if issuer != "host" and issuer not in self.settings.platform_admins():
            raise not_found()
        self.eligible(email)
        OperatorSecrets(self.settings)
        async with self.db.transaction() as session:
            state = await self.state(session, email)
            if state["account_exists"] and not state["active"]:
                raise not_found()
            # A current database factor gives self-service rotation a concrete purpose.
            if issuer == email and state["generation"] is None:
                raise not_found()
            token = self.crypto.issue(
                {
                    "kind": "operator-enroll",
                    "email": email,
                    "fp": state_fingerprint(state["password_hash"], state["generation"]),
                    "issuer": "link" if issuer == "host" else issuer,
                },
                int(LINK_TTL.total_seconds()),
            )
            expiry = datetime.fromtimestamp(self.crypto.open(token)["exp"], UTC)
            audit(
                session,
                issuer,
                "platform.operator.enrollment-link",
                "success",
                None,
                {"email": email, "issuer": issuer, "expires_at": expiry.isoformat()},
            )
        return EnrollmentLink(
            email=email, url="/app/platform/enroll#token=" + token, expires_at=expiry
        )

    def open_link(self, token: str) -> dict:
        try:
            payload = self.crypto.open(token)
            if (
                payload.get("kind") != "operator-enroll"
                or not isinstance(payload.get("email"), str)
                or not isinstance(payload.get("fp"), str)
                or re.fullmatch(r"[0-9a-f]{64}", payload["fp"]) is None
                or not isinstance(payload.get("exp"), int)
                or not isinstance(payload.get("issuer"), str)
            ):
                raise invalid_link()
        except ServiceError:
            raise invalid_link() from None
        self.eligible(payload["email"], link=True)
        return payload

    def check_state(self, payload: dict, state: dict) -> None:
        if (state["account_exists"] and not state["active"]) or not hmac.compare_digest(
            payload["fp"], state_fingerprint(state["password_hash"], state["generation"])
        ):
            raise invalid_link()

    async def start(self, body: EnrollmentStartRequest) -> EnrollmentStart:
        token = body.token.get_secret_value()
        payload = self.open_link(token)
        cipher = OperatorSecrets(self.settings)
        async with self.db.transaction() as session:
            state = await self.state(session, payload["email"])
            self.check_state(payload, state)
        email, secret = payload["email"], generate_secret()
        generation = (state["generation"] or 0) + 1
        pending = self.crypto.issue(
            {
                "kind": "operator-enroll-pending",
                "email": email,
                "link": token_digest(token),
                "ciphertext": cipher.encrypt(secret, email, generation),
                "generation": generation,
                "key_version": cipher.VERSION,
            },
            0,
            expires_at=payload["exp"],
        )
        return EnrollmentStart(
            email=email,
            password="set" if state["password_hash"] in (None, UNUSABLE_PASSWORD) else "confirm",
            totp_secret=secret,
            otpauth_uri=provisioning_uri(email, secret),
            pending=pending,
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )

    async def complete(self, body: EnrollmentComplete, source: str | None) -> EnrollmentResult:
        token = body.token.get_secret_value()
        payload = self.open_link(token)
        cipher = OperatorSecrets(self.settings)
        try:
            pending = self.crypto.open(body.pending.get_secret_value())
            if (
                pending.get("kind") != "operator-enroll-pending"
                or pending.get("email") != payload["email"]
                or pending.get("link") != token_digest(token)
                or pending.get("exp") != payload["exp"]
                or not isinstance(pending.get("generation"), int)
                or not isinstance(pending.get("ciphertext"), str)
                or not isinstance(pending.get("key_version"), int)
            ):
                raise invalid_link()
            secret = cipher.decrypt(
                pending["ciphertext"],
                payload["email"],
                pending["generation"],
                pending["key_version"],
            )
        except ServiceError:
            raise invalid_link() from None
        async with self.db.transaction() as session:
            initial = await self.state(session, payload["email"])
            self.check_state(payload, initial)
        password = body.password.get_secret_value()
        needs_password = initial["password_hash"] in (None, UNUSABLE_PASSWORD)
        if needs_password and not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
            raise ServiceError("weak_password", "Password must have 12 to 1024 characters", 400, 2)
        password_hash = await self.attempts.hash_password(password) if needs_password else None
        locked: dict = {}
        completed: dict = {}

        async def prepare(session: AsyncSession, user: User | None) -> User:
            # Account lock (including absent identities) is already held by PasswordAttempts.
            current = self.open_link(token)
            locked.update(await self.state(session, current["email"], lock=True))
            self.check_state(current, locked)
            if pending["generation"] != (locked["generation"] or 0) + 1:
                raise invalid_link()
            if password_hash is not None:
                return User(
                    id=uuid4(), email=current["email"], password_hash=password_hash, active=True
                )
            if user is None:
                raise invalid_link()
            # Verify the locking read rather than the ORM identity-map snapshot.
            return User(
                id=user.id,
                email=current["email"],
                password_hash=locked["password_hash"],
                active=True,
            )

        async def enroll(session: AsyncSession, user: User) -> dict:
            counter = matching_counter(secret, body.code)
            if counter is None:
                raise invalid_login()
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM public.platform_operator_enroll(:email, :expected_hash, :generation, "
                            ":ciphertext, :version, :new_hash, :issuer, :counter)"
                        ),
                        {
                            "email": payload["email"],
                            "expected_hash": locked["password_hash"],
                            "generation": locked["generation"],
                            "ciphertext": pending["ciphertext"],
                            "version": cipher.VERSION,
                            "new_hash": password_hash,
                            "issuer": payload["issuer"],
                            "counter": counter,
                        },
                    )
                )
                .mappings()
                .one()
            )
            if row["outcome"] != "enrolled":
                raise invalid_link()
            completed.update(row)
            return {}

        try:
            await self.attempts.authenticate(
                payload["email"], password, source, enroll, enrollment_prepare=prepare
            )
        except ServiceError as exc:
            if exc.code == "invalid_login":
                raise ServiceError(
                    "invalid_enrollment", "Invalid enrollment credentials", 401, 4
                ) from None
            raise
        return EnrollmentResult(
            email=payload["email"],
            account_created=completed["account_created"],
            password_set=completed["password_set"],
        )

    async def factor_secret(self, email: str) -> str | None:
        async with self.db.transaction() as session:
            return await factor_secret(self.settings, session, email)
