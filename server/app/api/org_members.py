"""Current-org member list and human administrator mutations."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.common import result
from app.core.security import TokenSigner
from app.schemas.contracts import Result
from app.schemas.org_members import OrgMemberActiveChange, OrgMemberAdd, OrgMemberRoleChange
from app.services.org_members import OrgMemberService


def create_router(context: Callable[..., Any], crypto: TokenSigner) -> APIRouter:
    router = APIRouter()

    async def manage(ctx=Depends(context, scope="function")):
        ctx[1].require("member:manage")
        return ctx

    def service(ctx):
        session, actor = ctx
        return OrgMemberService(session, actor, crypto)

    @router.get("/org/members", name="org_member_list", response_model=Result)
    async def list_members(ctx=Depends(context, scope="function")):
        actor = ctx[1]
        items = await service(ctx).list_members(actor.user_id, actor.org_id)
        # Non-admin views omit the optional administrative metadata, rather than nulling it.
        return result(
            "org member list",
            items=[item.model_dump(mode="json", exclude_unset=True) for item in items],
        )

    @router.post("/org/members", name="org_member_add", response_model=Result)
    async def add(body: OrgMemberAdd, ctx=Depends(manage, scope="function")):
        actor = ctx[1]
        invited = await service(ctx).add(actor.user_id, actor.org_id, body)
        return result("org member add", invited.model_dump(mode="json"))

    @router.post("/org/members/{user_id}/role", name="org_member_role", response_model=Result)
    async def change_role(
        user_id: UUID, body: OrgMemberRoleChange, ctx=Depends(manage, scope="function")
    ):
        actor = ctx[1]
        member = await service(ctx).change_role(actor.user_id, actor.org_id, user_id, body)
        return result("org member role", member.model_dump(mode="json"))

    @router.post(
        "/org/members/{user_id}/active", name="org_member_set-active", response_model=Result
    )
    async def set_active(
        user_id: UUID, body: OrgMemberActiveChange, ctx=Depends(manage, scope="function")
    ):
        actor = ctx[1]
        member = await service(ctx).set_active(actor.user_id, actor.org_id, user_id, body)
        return result("org member set-active", member.model_dump(mode="json"))

    @router.post(
        "/org/members/{user_id}/invitation", name="org_member_invite", response_model=Result
    )
    async def invite(user_id: UUID, ctx=Depends(manage, scope="function")):
        actor = ctx[1]
        invited = await service(ctx).invite(actor.user_id, actor.org_id, user_id)
        return result("org member invite", invited.model_dump(mode="json"))

    return router
