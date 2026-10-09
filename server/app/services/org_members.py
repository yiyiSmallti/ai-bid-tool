"""Tenant-scoped member administration, atomic CAS and one-time setup invitations."""

from typing import Any
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError, not_found
from app.core.security import TokenSigner
from app.models.entities import ApiToken, Membership, User
from app.schemas.org_members import (
    OrgMemberActiveChange,
    OrgMemberAdd,
    OrgMemberInvited,
    OrgMemberRoleChange,
    OrgMemberView,
)
from app.services.auth import Identity, set_actor_context
from app.services.platform import SETUP_SECONDS, UNUSABLE_PASSWORD, setup_token
from app.services.task_workflow import live_actor
from app.services.versioned import audit


def conflict(code: str, message: str) -> ServiceError:
    return ServiceError(code, message, 409, 2)


class OrgMemberService:
    def __init__(self, session: AsyncSession, actor: Identity, crypto: TokenSigner):
        self.session = session
        self.actor = actor
        self.crypto = crypto

    async def _authorize(self, actor_user_id: UUID, org_id: UUID, *, manage: bool) -> Identity:
        if (actor_user_id, org_id) != (self.actor.user_id, self.actor.org_id):
            raise not_found()
        if manage:
            # Fail tokens before locks or target lookups, including manually constructed identities.
            self.actor.require("member:manage")
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:org, 680068))"),
                {"org": str(org_id)},
            )
        live = await live_actor(self.session, self.actor)
        if manage:
            live.require("member:manage")
        await set_actor_context(self.session, live)
        return live

    async def _member(self, org_id: UUID, user_id: UUID) -> tuple[Membership, User]:
        row = (
            await self.session.execute(
                select(Membership, User)
                .join(User, User.id == Membership.user_id)
                .where(Membership.org_id == org_id, Membership.user_id == user_id)
                .execution_options(populate_existing=True)
            )
        ).first()
        if row is None:
            raise not_found()
        return row[0], row[1]

    @staticmethod
    def _view(member: Membership, user: User, *, full: bool = True) -> OrgMemberView:
        fields: dict[str, Any] = dict(
            user_id=user.id, email=user.email, role=member.role, active=member.active
        )
        if full:
            fields.update(
                password_set=user.password_hash != UNUSABLE_PASSWORD,
                revision=member.revision,
                created_by=member.created_by,
                created_at=member.created_at,
                updated_at=member.updated_at,
            )
        return OrgMemberView.model_validate(fields)

    def _invitation(self, member: Membership, user: User) -> OrgMemberInvited:
        needs_password = user.password_hash == UNUSABLE_PASSWORD
        url = (
            "/app/setup-password#token=" + setup_token(self.crypto, user.id, user.password_hash)
            if needs_password
            else "/app/org/login"
        )
        return OrgMemberInvited(
            member=self._view(member, user),
            invitation_url=url,
            expires_in=SETUP_SECONDS if needs_password else None,
        )

    async def list_members(self, actor_user_id: UUID, org_id: UUID) -> list[OrgMemberView]:
        actor = await self._authorize(actor_user_id, org_id, manage=False)
        full = actor.role == "admin"
        query = (
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == org_id)
            .order_by(User.email, Membership.user_id)
        )
        if not full:
            query = query.where(Membership.active.is_(True), User.active.is_(True))
        rows = (await self.session.execute(query)).all()
        return [self._view(member, user, full=full) for member, user in rows]

    async def add(self, actor_user_id: UUID, org_id: UUID, body: OrgMemberAdd) -> OrgMemberInvited:
        actor = await self._authorize(actor_user_id, org_id, manage=True)
        row = (
            await self.session.execute(
                text("SELECT * FROM public.org_add_member(:org, :actor, :email, :role)"),
                {"org": org_id, "actor": actor_user_id, "email": body.email, "role": body.role},
            )
        ).one()
        if row.outcome == "member_exists":
            raise conflict("member_exists", "This email is already a member of this organization")
        if row.outcome == "forbidden":
            raise ServiceError("forbidden", "Human organization administrator required", 403, 4)
        if row.outcome == "invalid_input":
            raise ServiceError("invalid_input", "Invalid member input", 400, 2)
        if row.outcome != "added":
            raise RuntimeError("Unexpected member creation outcome")
        member, user = await self._member(org_id, row.user_id)
        audit(
            self.session,
            actor,
            "org.member.add",
            user.id,
            {"user_id": str(user.id), "before": None, "after": self._state(member)},
        )
        return self._invitation(member, user)

    @staticmethod
    def _state(member: Membership) -> dict:
        return {"role": member.role, "active": member.active, "revision": member.revision}

    async def _change(
        self,
        actor_user_id: UUID,
        org_id: UUID,
        user_id: UUID,
        expected_revision: int,
        *,
        role: str | None = None,
        active: bool | None = None,
    ) -> OrgMemberView:
        actor = await self._authorize(actor_user_id, org_id, manage=True)
        member, user = await self._member(org_id, user_id)
        if member.revision != expected_revision:
            raise conflict("revision_conflict", "Member changed; refresh before submitting again")
        new_role = member.role if role is None else role
        new_active = member.active if active is None else active
        if member.active and member.role == "admin" and (new_role != "admin" or not new_active):
            other_admin = await self.session.scalar(
                select(Membership.id)
                .join(User, User.id == Membership.user_id)
                .where(
                    Membership.org_id == org_id,
                    Membership.user_id != user_id,
                    Membership.active.is_(True),
                    Membership.role == "admin",
                    User.active.is_(True),
                )
                .limit(1)
            )
            if other_admin is None:
                raise conflict("last_admin_required", "Keep at least one active administrator")
        before = self._state(member)
        await self.session.execute(
            text("SELECT set_config('app.member_expected_revision', :revision, true)"),
            {"revision": str(expected_revision)},
        )
        # The predicate is the CAS fence; the row guard rejects non-CAS role/active writes.
        changed = await self.session.scalar(
            update(Membership)
            .where(
                Membership.org_id == org_id,
                Membership.user_id == user_id,
                Membership.revision == expected_revision,
            )
            .values(role=new_role, active=new_active, revision=expected_revision + 1)
            .returning(Membership)
            .execution_options(populate_existing=True)
        )
        if changed is None:
            raise conflict("revision_conflict", "Member changed; refresh before submitting again")
        if active is False:
            await self.session.execute(
                update(ApiToken)
                .where(ApiToken.org_id == org_id, ApiToken.user_id == user_id)
                .values(revoked=True)
            )
        audit(
            self.session,
            actor,
            "org.member.role" if role is not None else "org.member.active",
            user_id,
            {"user_id": str(user_id), "before": before, "after": self._state(changed)},
        )
        return self._view(changed, user)

    async def change_role(
        self, actor_user_id: UUID, org_id: UUID, user_id: UUID, body: OrgMemberRoleChange
    ) -> OrgMemberView:
        return await self._change(
            actor_user_id, org_id, user_id, body.expected_revision, role=body.role
        )

    async def set_active(
        self, actor_user_id: UUID, org_id: UUID, user_id: UUID, body: OrgMemberActiveChange
    ) -> OrgMemberView:
        return await self._change(
            actor_user_id, org_id, user_id, body.expected_revision, active=body.active
        )

    async def invite(self, actor_user_id: UUID, org_id: UUID, user_id: UUID) -> OrgMemberInvited:
        actor = await self._authorize(actor_user_id, org_id, manage=True)
        member, user = await self._member(org_id, user_id)
        if user.password_hash != UNUSABLE_PASSWORD:
            raise conflict("password_already_set", "This account has a password; sign in normally")
        state = self._state(member)
        audit(
            self.session,
            actor,
            "org.member.invite",
            user_id,
            {"user_id": str(user_id), "before": state, "after": state},
        )
        return self._invitation(member, user)
