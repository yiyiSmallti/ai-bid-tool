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


class ProjectMemberView(Contract):
    member_id: UUID
    login: str
    display_name: str
    role: Literal["owner", "member"]
    status: Literal["active", "disabled"]
    added_at: datetime


class ProjectMemberAdd(Contract):
    member_ids: list[UUID] = Field(min_length=1, max_length=100)


class ProjectOwnerChange(Contract):
    member_id: UUID


class ProjectSharingUpdate(Contract):
    export_to_personal_allowed: bool


class MemberQuotaSet(Contract):
    # Null clears the cap; personal models are never capped.
    monthly_company_tokens: int | None = Field(ge=0, le=10**15)


class MemberQuotaView(Contract):
    member_id: UUID
    monthly_company_tokens: int | None
    updated_at: datetime | None


class DefaultModelSet(Contract):
    # Null returns the member to the company default.
    slot: UUID | None


class DefaultModelView(Contract):
    default_slot: UUID | None
    # What a run without a model uses now.
    effective_slot: UUID | None


class ModelUsage(Contract):
    owner: Literal["company", "personal"]
    # Absent for another member's personal models, which only their owner sees.
    slot: UUID | None
    provider: str | None
    model: str | None
    calls: int
    input_tokens: int
    output_tokens: int
    tokens: int


class MemberUsageView(Contract):
    member_id: UUID
    month: str
    timezone: Literal["UTC"] = "UTC"
    monthly_company_tokens: int | None
    company_tokens: int
