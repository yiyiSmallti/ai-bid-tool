"""Review-only platform operator enrollment contracts; no routes, CLI commands or I/O are registered."""

import re
from datetime import timedelta
from typing import Literal, Protocol

from app.schemas.contracts import Contract
from pydantic import AwareDatetime, ConfigDict, Field, SecretStr, field_validator

LINK_TTL = timedelta(minutes=30)
PASSWORD_MIN = 12
PASSWORD_MAX = 1024
TOKEN_MAX = 4096

FactorSource = Literal["environment", "database", "none"]
PasswordStep = Literal["set", "confirm"]
Command = Literal[
    "platform operator list",
    "platform operator enrollment-link",
    "platform enrollment start",
    "platform enrollment complete",
]
ErrorCode = Literal[
    "invalid_input",
    "invalid_session",
    "not_found",
    "invalid_enrollment_link",
    "invalid_enrollment",
    "factor_managed_by_deployment",
    "enrollment_unavailable",
    "weak_password",
    "too_many_attempts",
    "auth_busy",
]

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+")


class EnrollmentContract(Contract):
    model_config = ConfigDict(extra="forbid", from_attributes=True, hide_input_in_errors=True)


def _email(value: str) -> str:
    value = value.strip().lower()
    if len(value) > 254 or not _EMAIL.fullmatch(value):
        raise ValueError("email must be an email address")
    return value


class OperatorStatus(EnrollmentContract):
    """One allowlisted email; never carries a secret or ciphertext."""

    email: str
    has_account: bool
    factor_source: FactorSource
    enrolled_at: AwareDatetime | None = None
    enrolled_by: str | None = None


class EnrollmentLinkRequest(EnrollmentContract):
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def normalized(cls, value: str) -> str:
        return _email(value)


class EnrollmentLink(EnrollmentContract):
    """Shown once to the issuer; the audit row records only issuer, email and expiry."""

    email: str
    url: str = Field(pattern=r"^/app/platform/enroll#token=[A-Za-z0-9_.=-]+$")
    expires_at: AwareDatetime


class EnrollmentStartRequest(EnrollmentContract):
    token: SecretStr = Field(repr=False, json_schema_extra={"writeOnly": True})

    @field_validator("token")
    @classmethod
    def bounded_token(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value()) <= TOKEN_MAX:
            raise ValueError("token has an invalid length")
        return value


class EnrollmentStart(EnrollmentContract):
    """The new secret is returned only here, for the QR code; nothing is stored yet."""

    email: str
    password: PasswordStep
    totp_secret: str = Field(pattern=r"^[A-Z2-7]{32}$")
    otpauth_uri: str = Field(pattern=r"^otpauth://totp/")
    # Signed and encrypted; carries the pending secret back to completion.
    pending: str = Field(min_length=1, max_length=TOKEN_MAX)
    expires_at: AwareDatetime


class EnrollmentComplete(EnrollmentContract):
    token: SecretStr = Field(repr=False, json_schema_extra={"writeOnly": True})
    pending: SecretStr = Field(repr=False, json_schema_extra={"writeOnly": True})
    password: SecretStr = Field(repr=False, exclude=True, json_schema_extra={"writeOnly": True})
    code: str = Field(pattern=r"^\d{6}$")

    @field_validator("token", "pending")
    @classmethod
    def bounded_blob(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value()) <= TOKEN_MAX:
            raise ValueError("enrollment value has an invalid length")
        return value

    @field_validator("password")
    @classmethod
    def bounded_password(cls, value: SecretStr) -> SecretStr:
        # Like password setup, the service rejects fewer than PASSWORD_MIN characters
        # as weak_password when setting a new one; confirmation accepts any stored length.
        if not 1 <= len(value.get_secret_value()) <= PASSWORD_MAX:
            raise ValueError(f"password must have at most {PASSWORD_MAX} characters")
        return value


class EnrollmentResult(EnrollmentContract):
    email: str
    enrolled: Literal[True] = True
    account_created: bool
    password_set: bool


class OperatorEnrollmentService(Protocol):
    """Allowlist from deployment configuration; factors from the environment or the database."""

    async def list_operators(self, operator_email: str) -> list[OperatorStatus]: ...

    async def issue_link(self, issuer: str, body: EnrollmentLinkRequest) -> EnrollmentLink:
        """Allowlisted email without an environment secret only; issuer is an operator or "host"."""
        ...

    async def start(self, body: EnrollmentStartRequest) -> EnrollmentStart:
        """Validate signature, expiry, allowlist and state fingerprint; write nothing."""
        ...

    async def complete(self, body: EnrollmentComplete, source: str | None) -> EnrollmentResult:
        """Re-check the link under a row lock, verify the code against the pending secret,
        set or confirm the password, store the encrypted factor and audit the TOTP step."""
        ...

    async def factor_secret(self, email: str) -> str | None:
        """Login lookup: the environment secret if present, else the decrypted database factor."""
        ...
