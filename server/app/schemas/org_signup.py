"""Runtime org self-service application contracts."""

import re
from datetime import timedelta
from typing import Annotated, Literal, Protocol, Self
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.schemas.contracts import Contract

SOURCE_WINDOW = timedelta(hours=24)
SOURCE_WINDOW_LIMIT = 5
PENDING_LIMIT = 200
APPLICATION_TTL = timedelta(days=30)
PASSWORD_MIN = 12
PASSWORD_MAX = 1024
LIST_LIMIT = 200

ApplicationStatus = Literal["pending", "approved", "rejected", "expired"]
Command = Literal[
    "auth org-application submit",
    "platform org application list",
    "platform org application approve",
    "platform org application reject",
]
ErrorCode = Literal[
    "invalid_input",
    "weak_password",
    "signup_disabled",
    "too_many_attempts",
    "signup_busy",
    "auth_busy",
    "invalid_session",
    "not_found",
    "application_not_pending",
    "existing_user_requires_attach",
]

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+")
_PHONE = re.compile(r"\+?[0-9][0-9 ()-]{2,38}")


def _text(value: str, *, multiline: bool = False) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must contain non-whitespace characters")
    checked = value.replace("\n", "") if multiline else value
    if _CONTROL.search(checked):
        raise ValueError("control characters are not allowed")
    return value


OrgName = Annotated[str, Field(min_length=1, max_length=200)]
ContactName = Annotated[str, Field(min_length=1, max_length=100)]
Email = Annotated[str, Field(min_length=3, max_length=254)]
Phone = Annotated[str, Field(min_length=3, max_length=40)]
Note = Annotated[str, Field(min_length=1, max_length=500)]
Reason = Annotated[str, Field(min_length=1, max_length=500)]


class SignupContract(Contract):
    model_config = ConfigDict(extra="forbid", from_attributes=True, hide_input_in_errors=True)


class OrgApplicationSubmit(SignupContract):
    """Public form body. The password is write-only and leaves this model only as a hash."""

    org_name: OrgName
    contact_name: ContactName
    email: Email
    phone: Phone | None = None
    note: Note | None = None
    password: SecretStr = Field(repr=False, exclude=True, json_schema_extra={"writeOnly": True})

    @field_validator("org_name", "contact_name")
    @classmethod
    def single_line(cls, value: str) -> str:
        return _text(value)

    @field_validator("note")
    @classmethod
    def note_text(cls, value: str | None) -> str | None:
        return None if value is None else _text(value, multiline=True)

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value: str) -> str:
        value = value.strip().lower()
        # Lowercasing can expand Unicode; re-check the stored column bound.
        if len(value) > 254 or not _EMAIL.fullmatch(value):
            raise ValueError("email must be an email address")
        return value

    @field_validator("phone")
    @classmethod
    def plausible_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not _PHONE.fullmatch(value):
            raise ValueError(
                "phone may contain digits, spaces, parentheses, hyphens and a leading +"
            )
        return value

    @field_validator("password")
    @classmethod
    def bounded_password(cls, value: SecretStr) -> SecretStr:
        # Like password setup, the service rejects fewer than PASSWORD_MIN characters as
        # weak_password; the wire model only bounds the size before hashing.
        if not 1 <= len(value.get_secret_value()) <= PASSWORD_MAX:
            raise ValueError(f"password must have at most {PASSWORD_MAX} characters")
        return value


class OrgApplicationReceipt(SignupContract):
    """Identical for new, duplicate and already-registered emails; carries no identifiers."""

    submitted: Literal[True] = True
    review: Literal["platform_manual"] = "platform_manual"
    sign_in_after_approval: Literal[True] = True


class OrgApplicationListQuery(SignupContract):
    status: ApplicationStatus | None = "pending"
    limit: int = Field(default=50, ge=1, le=LIST_LIMIT)
    # Keyset cursor: created_at of the last row of the previous page, newest first.
    before: AwareDatetime | None = None


class OrgApplicationView(SignupContract):
    """Platform-only projection. Never includes the password hash or the raw client address."""

    id: UUID
    status: ApplicationStatus
    org_name: str
    contact_name: str
    email: str
    phone: str | None
    note: str | None
    created_at: AwareDatetime
    expires_at: AwareDatetime
    existing_user: bool
    source_submissions_24h: int = Field(ge=1)
    decided_at: AwareDatetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    org_id: UUID | None = None
    admin_user_id: UUID | None = None
    user_created: bool | None = None
    attached_existing_user: bool | None = None

    @model_validator(mode="after")
    def decision_shape(self) -> Self:
        decided = self.decided_at is not None
        if self.status == "pending" and (decided or self.org_id is not None):
            raise ValueError("pending applications carry no decision")
        if self.status in ("approved", "rejected") and not (decided and self.decided_by):
            raise ValueError("decided applications record the operator and time")
        if (self.status == "approved") != (self.org_id is not None):
            raise ValueError("only approved applications reference an org")
        if self.status == "approved" and (
            self.admin_user_id is None
            or self.user_created is None
            or self.attached_existing_user is None
            or self.user_created == self.attached_existing_user
        ):
            raise ValueError("approval records exactly one of user creation or attachment")
        return self


class OrgApplicationApprove(SignupContract):
    # Operators may correct the org name; contact fields stay as submitted.
    org_name: OrgName | None = None
    attach_existing_user: bool = False

    @field_validator("org_name")
    @classmethod
    def corrected_name(cls, value: str | None) -> str | None:
        return None if value is None else _text(value)


class OrgApplicationReject(SignupContract):
    reason: Reason

    @field_validator("reason")
    @classmethod
    def reason_text(cls, value: str) -> str:
        return _text(value, multiline=True)


class OrgApplicationDecision(SignupContract):
    application_id: UUID
    status: Literal["approved", "rejected"]
    org_id: UUID | None = None
    admin_user_id: UUID | None = None
    user_created: bool | None = None
    attached_existing_user: bool | None = None

    @model_validator(mode="after")
    def decision_matches_status(self) -> Self:
        approved = (
            self.org_id is not None
            and self.admin_user_id is not None
            and self.user_created is not None
            and self.attached_existing_user is not None
            and self.user_created != self.attached_existing_user
        )
        empty = (
            self.org_id is None
            and self.admin_user_id is None
            and self.user_created is None
            and self.attached_existing_user is None
        )
        if (self.status == "approved" and not approved) or (
            self.status == "rejected" and not empty
        ):
            raise ValueError("decision fields must match the decision status")
        return self


class OrgApplicationService(Protocol):
    """Service boundary; HTTP and CLI adapters only translate to the seven-key Result."""

    async def submit(self, body: OrgApplicationSubmit, source: str | None) -> OrgApplicationReceipt:
        """Check the feature switch, source window and pending cap before hashing.

        Hash through the shared password pool, then insert through the submit function.
        A duplicate pending email or an existing user returns the same receipt; a
        duplicate stores nothing and keeps the first password.
        """
        ...

    async def list_applications(
        self, operator_email: str, query: OrgApplicationListQuery
    ) -> list[OrgApplicationView]:
        """Overdue pending rows are reported as expired."""
        ...

    async def approve(
        self, operator_email: str, application_id: UUID, body: OrgApplicationApprove
    ) -> OrgApplicationDecision:
        """Lock the row, re-check status and email ownership, create org and admin in one transaction.

        A new user receives the stored hash; an existing user requires attach and keeps
        its password. The stored hash is cleared in the same transaction.
        """
        ...

    async def reject(
        self, operator_email: str, application_id: UUID, body: OrgApplicationReject
    ) -> OrgApplicationDecision: ...

    async def expire_overdue(self) -> int:
        """Daily worker task: mark overdue pending rows expired and clear their hashes."""
        ...
