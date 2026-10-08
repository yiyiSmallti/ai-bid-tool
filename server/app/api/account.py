"""Health, sign-in, the current org, its prepaid balance and API tokens."""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.api.common import result
from app.core.config import Settings
from app.core.db import Database
from app.core.password_attempts import PasswordAttempts
from app.core.security import TokenSigner
from app.schemas.contracts import CONTRACT_VERSION, Login, Result, TokenCreate
from app.schemas.platform_contracts import CardRedeem
from app.services import billing, tokens
from app.services.auth import login


def create_router(
    context: Callable[..., Any],
    settings: Settings,
    db: Database,
    crypto: TokenSigner,
    password_attempts: PasswordAttempts,
    llm,
    resolve: Callable[..., Awaitable[Any]] | None,
) -> APIRouter:
    router = APIRouter()

    @router.get("/health", name="health", response_model=Result)
    async def health():
        active = llm
        if resolve is not None:
            async with db.transaction() as session:
                active = await resolve(session)
        configured = not active.test_only and active.name != "unconfigured"
        return result(
            "health",
            {
                "status": "ok",
                "version": CONTRACT_VERSION,
                "real_llm_configured": configured,
                "org_signup_enabled": settings.org_signup_enabled,
            },
        )

    @router.post("/auth/login", name="login", response_model=Result)
    async def auth_login(body: Login, request: Request):
        user = await login(
            password_attempts,
            body.email,
            body.password,
            body.org_id,
            request.client.host if request.client else None,
        )
        token = crypto.issue({"kind": "session", "user_id": str(user.id)}, settings.session_seconds)
        return result(
            "login",
            {
                "session": token,
                "org_id": str(body.org_id),
                "expires_in": settings.session_seconds,
            },
        )

    @router.get("/org/current", name="org_use", response_model=Result)
    async def org_use(ctx=Depends(context, scope="function")):
        _, identity = ctx
        return result(
            "org use",
            {
                "org_id": str(identity.org_id),
                "role": identity.role,
                "user_id": str(identity.user_id),
            },
        )

    @router.get("/billing", name="billing_balance", response_model=Result)
    async def billing_balance(ctx=Depends(context, scope="function")):
        session, identity = ctx
        identity.require("billing:read")
        data, items = await billing.overview(session, settings.billing_currency)
        return result("billing balance", data, items)

    @router.post("/billing/redeem", name="billing_redeem", response_model=Result)
    async def billing_redeem(body: CardRedeem, ctx=Depends(context, scope="function")):
        _, identity = ctx
        data = await billing.redeem(db, identity, body.code, settings.billing_currency)
        return result("billing redeem", data)

    @router.post("/tokens", name="token_create", response_model=Result)
    async def token_create(body: TokenCreate, ctx=Depends(context, scope="function")):
        session, identity = ctx
        token, secret = await tokens.create_token(session, identity, body)
        return result(
            "token create",
            {
                "id": str(token.id),
                "token": secret,
                "scopes": token.scopes,
                "expires_at": body.expires_at.isoformat(),
            },
        )

    return router
