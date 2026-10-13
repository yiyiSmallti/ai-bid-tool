"""Health, installation sessions and scoped API tokens."""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends

from app.api.common import result
from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError
from app.core.security import TokenSigner, access_key_matches
from app.schemas.contracts import CONTRACT_VERSION, Result, SessionCreate, TokenCreate
from app.services import tokens


def create_router(
    context: Callable[..., Any],
    settings: Settings,
    db: Database,
    crypto: TokenSigner,
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
            },
        )

    @router.post("/auth/session", name="auth_session_create", response_model=Result)
    async def auth_session_create(body: SessionCreate):
        if not access_key_matches(
            body.access_key.get_secret_value(), settings.access_key.get_secret_value()
        ):
            raise ServiceError("invalid_access_key", "Invalid access key", 401, 4)
        token = crypto.issue({"kind": "session"}, settings.session_seconds)
        return result(
            "auth session create",
            {"session": token, "expires_in": settings.session_seconds},
        )

    @router.get("/auth/session", name="auth_session_show", response_model=Result)
    async def auth_session_show(ctx=Depends(context, scope="function")):
        _, identity = ctx
        return result(
            "auth session show",
            {
                "actor_kind": identity.actor_kind,
                "scopes": sorted(identity.scopes),
                "expires_at": identity.session_expires_at.isoformat()
                if identity.session_expires_at is not None
                else None,
            },
        )

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
