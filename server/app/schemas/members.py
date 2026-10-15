"""Gateway sign-in, member accounts and their sessions."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr

from app.schemas.contracts import Contract

Grant = Literal["library:edit", "memory:edit", "confidential:reveal"]
LOGIN_PATTERN = r"^[a-z0-9._-]{3,64}$"


class MemberSignIn(Contract):
    login: str = Field(min_length=3, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=256)
    client_label: str | None = Field(default=None, max_length=100)


class MemberView(Contract):
    id: UUID
    login: str
    display_name: str
    role: Literal["admin", "member"]
    grants: list[Grant]
    status: Literal["active", "disabled"]


class SessionView(Contract):
    session: str = Field(description="Signed session token; returned once")
    expires_in: int
    member: MemberView
    must_change_password: bool


class PasswordChange(Contract):
    current: SecretStr = Field(min_length=1, max_length=256)
    new: SecretStr = Field(
        min_length=1, max_length=256, description="At least BID_PASSWORD_MIN_LENGTH characters"
    )


class MemberCreate(Contract):
    login: str = Field(pattern=LOGIN_PATTERN)
    display_name: str = Field(min_length=1, max_length=60, pattern=r"\S")
    role: Literal["admin", "member"] = "member"
    grants: list[Grant] = Field(default_factory=list)


class MemberUpdate(Contract):
    display_name: str | None = Field(default=None, min_length=1, max_length=60, pattern=r"\S")
    role: Literal["admin", "member"] | None = None
    grants: list[Grant] | None = None
    status: Literal["active", "disabled"] | None = None


class MemberPassword(Contract):
    """A one-time initial password, shown once and never stored in plaintext."""

    member: MemberView
    initial_password: str


class MemberSessionView(Contract):
    id: UUID
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    client_label: str | None
    current: bool


class InstallationView(Contract):
    mode: Literal["personal", "enterprise"]
    company_name: str | None
    server_version: str
    contract_version: str
