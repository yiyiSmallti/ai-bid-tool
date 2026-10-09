"""Review-only org member management contracts; no routes, CLI commands or I/O are registered."""

import re
from typing import Literal, Protocol
from uuid import UUID

from app.schemas.contracts import Contract
from pydantic import AwareDatetime, ConfigDict, Field, field_validator

Role = Literal["admin", "bidder", "technical", "viewer"]
Command = Literal[
    "org member list",
    "org member add",
    "org member role",
    "org member set-active",
    "org member invite",
]
ErrorCode = Literal[
    "invalid_input",
    "invalid_session",
    "forbidden",
    "not_found",
    "member_exists",
    "revision_conflict",
    "last_admin_required",
    "password_already_set",
]

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+")


class MemberContract(Contract):
    model_config = ConfigDict(extra="forbid", from_attributes=True, hide_input_in_errors=True)


class OrgMemberView(MemberContract):
    """Admins receive every field; other roles receive only active members' email and role."""

    user_id: UUID
    email: str
    role: Role
    active: bool
    password_set: bool | None = None
    revision: int | None = Field(default=None, ge=1)
    created_by: UUID | None = None
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None


class OrgMemberAdd(MemberContract):
    email: str = Field(min_length=3, max_length=254)
    role: Role

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value: str) -> str:
        value = value.strip().lower()
        if len(value) > 254 or not _EMAIL.fullmatch(value):
            raise ValueError("email must be an email address")
        return value


class OrgMemberRoleChange(MemberContract):
    role: Role
    expected_revision: int = Field(strict=True, ge=1)


class OrgMemberActiveChange(MemberContract):
    active: bool
    expected_revision: int = Field(strict=True, ge=1)


class OrgMemberInvited(MemberContract):
    """Same shape whether or not the email already had an account; the link is shown once."""

    member: OrgMemberView
    invitation_url: str = Field(pattern=r"^/app/(setup-password#token=[A-Za-z0-9_.=-]+|org/login)$")
    expires_in: int | None = Field(default=None, ge=1)


class OrgMemberService(Protocol):
    """Org-scoped under RLS; every mutation requires the human-only member:manage scope."""

    async def list_members(self, actor_user_id: UUID, org_id: UUID) -> list[OrgMemberView]: ...

    async def add(self, actor_user_id: UUID, org_id: UUID, body: OrgMemberAdd) -> OrgMemberInvited:
        """Create or attach the user by email through the restricted function; never touch an
        existing password or reveal other memberships."""
        ...

    async def change_role(
        self, actor_user_id: UUID, org_id: UUID, user_id: UUID, body: OrgMemberRoleChange
    ) -> OrgMemberView:
        """CAS on revision; refuse when no active admin would remain."""
        ...

    async def set_active(
        self, actor_user_id: UUID, org_id: UUID, user_id: UUID, body: OrgMemberActiveChange
    ) -> OrgMemberView:
        """Deactivation revokes this org's API tokens for the member; refuse the last admin."""
        ...

    async def invite(self, actor_user_id: UUID, org_id: UUID, user_id: UUID) -> OrgMemberInvited:
        """Fresh set-password link while the account has no password; otherwise password_already_set."""
        ...
