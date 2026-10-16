"""Own account routes for gateway members, and member management for administrators."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.common import result
from app.core.config import Settings
from app.core.password_attempts import PasswordAttempts
from app.schemas.contracts import Result
from app.schemas.members import (
    DefaultModelSet,
    MemberCreate,
    MemberQuotaSet,
    MemberUpdate,
    PasswordChange,
)
from app.services import member_models, members


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
        return result("member reset-password", reset.model_dump(mode="json"))

    @router.delete(
        "/members/{member_id}/sessions", name="member_session_revoke", response_model=Result
    )
    async def member_session_revoke(member_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        revoked = await members.revoke_member_sessions(session, identity, member_id)
        return result("member revoke-sessions", {"id": str(member_id), "revoked_sessions": revoked})

    @router.put("/members/{member_id}/quota", name="member_quota", response_model=Result)
    async def member_quota(
        member_id: UUID, body: MemberQuotaSet, ctx=Depends(context, scope="function")
    ):
        session, identity = ctx
        quota = await member_models.set_quota(session, identity, member_id, body)
        return result("member quota", quota.model_dump(mode="json"))

    @router.get("/members/{member_id}/usage", name="member_usage", response_model=Result)
    async def member_usage(
        member_id: UUID,
        month: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
        ctx=Depends(context, scope="function"),
    ):
        session, identity = ctx
        data, rows = await member_models.usage(session, identity, member_id, month)
        return result(
            "member usage",
            data.model_dump(mode="json"),
            items=[row.model_dump(mode="json") for row in rows],
        )

    @router.put("/account/default-model", name="account_default_model", response_model=Result)
    async def account_default_model(body: DefaultModelSet, ctx=Depends(context, scope="function")):
        session, identity = ctx
        chosen = await member_models.set_default_model(session, identity, body)
        return result("account default-model", chosen.model_dump(mode="json"))

    return router
