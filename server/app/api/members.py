"""Own account routes for gateway members, and member management for administrators."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.common import result
from app.core.config import Settings
from app.core.password_attempts import PasswordAttempts
from app.schemas.contracts import Result
from app.schemas.members import MemberCreate, MemberUpdate, PasswordChange
from app.services import members


def create_router(
    context: Callable[..., Any], settings: Settings, attempts: PasswordAttempts
) -> APIRouter:
    router = APIRouter()

    @router.post("/account/password", name="account_password_change", response_model=Result)
    async def account_password_change(body: PasswordChange, ctx=Depends(context, scope="function")):
        session, identity = ctx
        revoked = await members.change_password(session, attempts, settings, identity, body)
        return result("account password change", {"changed": True, "revoked_sessions": revoked})

    @router.get("/account/sessions", name="account_session_list", response_model=Result)
    async def account_session_list(ctx=Depends(context, scope="function")):
        session, identity = ctx
        rows = await members.own_sessions(session, identity)
        return result("account session list", items=[row.model_dump(mode="json") for row in rows])

    @router.delete(
        "/account/sessions/{session_id}", name="account_session_revoke", response_model=Result
    )
    async def account_session_revoke(session_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        await members.revoke_own_session(session, identity, session_id)
        return result("account session revoke", {"id": str(session_id), "revoked": True})

    @router.get("/members", name="member_list", response_model=Result)
    async def member_list(ctx=Depends(context, scope="function")):
        session, identity = ctx
        rows = await members.list_members(session, identity)
        return result("member list", items=[row.model_dump(mode="json") for row in rows])

    @router.post("/members", name="member_create", response_model=Result)
    async def member_create(body: MemberCreate, ctx=Depends(context, scope="function")):
        session, identity = ctx
        created = await members.create_member(session, attempts, settings, body, identity)
        return result("member create", created.model_dump(mode="json"))

    @router.patch("/members/{member_id}", name="member_update", response_model=Result)
    async def member_update(
        member_id: UUID, body: MemberUpdate, ctx=Depends(context, scope="function")
    ):
        session, identity = ctx
        updated = await members.update_member(session, identity, member_id, body)
        return result("member update", updated.model_dump(mode="json"))

    @router.post(
        "/members/{member_id}/password-reset", name="member_password_reset", response_model=Result
    )
    async def member_password_reset(member_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        reset = await members.reset_password(session, attempts, settings, identity, member_id)
        return result("member reset password", reset.model_dump(mode="json"))

    @router.delete(
        "/members/{member_id}/sessions", name="member_session_revoke", response_model=Result
    )
    async def member_session_revoke(member_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        revoked = await members.revoke_member_sessions(session, identity, member_id)
        return result("member revoke sessions", {"id": str(member_id), "revoked_sessions": revoked})

    return router
