"""Global pre-tenant onboarding through restricted database functions."""

import base64
import hashlib
import hmac
import logging
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError, log_unexpected
from app.core.password_attempts import PasswordAttempts
from app.schemas.org_signup import (
    PASSWORD_MIN,
    OrgApplicationApprove,
    OrgApplicationDecision,
    OrgApplicationListQuery,
    OrgApplicationReceipt,
    OrgApplicationReject,
    OrgApplicationSubmit,
    OrgApplicationView,
)

logger = logging.getLogger(__name__)


def source_digest(settings: Settings, source: str | None) -> str:
    # Domain separation keeps these identifiers distinct from token signing material.
    root = base64.urlsafe_b64decode(settings.token_key.get_secret_value())
    key = hmac.digest(root, b"bid:org-signup:source:v1", "sha256")
    return hmac.new(key, (source or "unknown").encode(), hashlib.sha256).hexdigest()


_ERRORS = {
    "too_many_attempts": ("Too many applications; try again later", 429, 3),
    "signup_busy": ("Application review capacity is full; try again later", 503, 3),
    "not_found": ("Resource not found", 404, 4),
    "application_not_pending": ("Application is no longer pending", 409, 2),
    "existing_user_requires_attach": ("Explicit existing account attachment is required", 409, 2),
}


def check_outcome(outcome: str) -> None:
    if outcome in _ERRORS:
        message, status, exit_code = _ERRORS[outcome]
        raise ServiceError(outcome, message, status, exit_code)


class OrgSignupService:
    def __init__(self, settings: Settings, db: Database, attempts: PasswordAttempts | None = None):
        self.settings = settings
        self.db = db
        self.attempts = attempts

    @asynccontextmanager
    async def transaction(self):
        try:
            async with self.db.transaction() as session:
                yield session
        except SQLAlchemyError as exc:
            # PostgreSQL DETAIL can include a failing row, even with hidden bind
            # parameters. Keep that row/hash out of ASGI exception logging.
            log_unexpected(logger, "Organization application database operation", exc)
            raise ServiceError(
                "internal_error", "Unable to process organization application", 500, 4
            ) from None

    def require_enabled(self) -> None:
        if not self.settings.org_signup_enabled:
            raise ServiceError("signup_disabled", "Organization signup is disabled", 404, 4)

    async def submit(self, body: OrgApplicationSubmit, source: str | None) -> OrgApplicationReceipt:
        self.require_enabled()
        password = body.password.get_secret_value()
        if len(password) < PASSWORD_MIN:
            raise ServiceError("weak_password", "Password must have at least 12 characters", 400, 2)
        params = {
            **body.model_dump(),
            "password_hash": None,
            "source_digest": source_digest(self.settings, source),
        }
        statement = text(
            "SELECT * FROM public.org_application_submit("
            ":org_name, :contact_name, :email, :phone, :note, :password_hash, :source_digest)"
        )
        # Preflight avoids hashing rejected traffic. The final insert serializes and
        # rechecks both limits across replicas; it never trusts this earlier result.
        async with self.transaction() as session:
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
            row = (await session.execute(statement, params)).mappings().one()
            check_outcome(row["outcome"])
            if row["outcome"] != "ready":
                raise RuntimeError("Unexpected application preflight outcome")
        if self.attempts is None:
            raise RuntimeError("Shared password admission is required for signup")
        try:
            params["password_hash"] = await self.attempts.hash_password(password)
        except ServiceError:
            raise
        except Exception as exc:
            log_unexpected(logger, "Organization application password hashing", exc)
            raise ServiceError(
                "internal_error", "Unable to process organization application", 500, 4
            ) from None
        async with self.transaction() as session:
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL READ COMMITTED"))
            row = (await session.execute(statement, params)).mappings().one()
            check_outcome(row["outcome"])
            if row["outcome"] not in {"submitted", "duplicate"}:
                raise RuntimeError("Unexpected application submit outcome")
        # Neither inserted IDs nor duplicate status cross the public boundary.
        return OrgApplicationReceipt()

    async def list_applications(
        self, operator_email: str, query: OrgApplicationListQuery
    ) -> list[OrgApplicationView]:
        async with self.transaction() as session:
            rows = (
                (
                    await session.execute(
                        text("SELECT * FROM public.org_application_list(:status, :limit, :before)"),
                        query.model_dump(),
                    )
                )
                .mappings()
                .all()
            )
        return [OrgApplicationView.model_validate(row) for row in rows]

    async def approve(
        self, operator_email: str, application_id: UUID, body: OrgApplicationApprove
    ) -> OrgApplicationDecision:
        async with self.transaction() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT * FROM public.org_application_approve(:actor, :id, :org_name, :attach)"
                        ),
                        {
                            "actor": operator_email,
                            "id": application_id,
                            "org_name": body.org_name,
                            "attach": body.attach_existing_user,
                        },
                    )
                )
                .mappings()
                .one()
            )
            check_outcome(row["outcome"])
            return OrgApplicationDecision.model_validate(
                {k: v for k, v in row.items() if k != "outcome"}
            )

    async def reject(
        self, operator_email: str, application_id: UUID, body: OrgApplicationReject
    ) -> OrgApplicationDecision:
        async with self.transaction() as session:
            row = (
                (
                    await session.execute(
                        text("SELECT * FROM public.org_application_reject(:actor, :id, :reason)"),
                        {"actor": operator_email, "id": application_id, "reason": body.reason},
                    )
                )
                .mappings()
                .one()
            )
            check_outcome(row["outcome"])
            return OrgApplicationDecision.model_validate(
                {k: v for k, v in row.items() if k != "outcome"}
            )

    async def expire_overdue(self) -> int:
        async with self.transaction() as session:
            return int(
                (await session.execute(text("SELECT public.org_application_expire()"))).scalar_one()
            )
