"""Health, the installation probe, sign-in sessions and scoped API tokens."""

from collections.abc import Awaitable, Callable
from importlib.metadata import version
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from app.api.common import result
from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError
from app.core.password_attempts import PasswordAttempts
from app.core.security import TokenSigner, access_key_matches
from app.models.members import Member
from app.schemas.contracts import CONTRACT_VERSION, Result, SessionCreate, TokenCreate
from app.schemas.members import InstallationView, MemberSignIn
from app.services import members, tokens
from app.services.auth import access_key_refused


def create_router(
    context: Callable[..., Any],
    settings: Settings,
    db: Database,
    crypto: TokenSigner,
    llm,
    resolve: Callable[..., Awaitable[Any]] | None,
    attempts: PasswordAttempts,
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

    @router.get("/installation", name="installation_show", response_model=Result)
    async def installation_show():
        """Unauthenticated, so a client can probe an address before sign-in."""
        async with db.transaction() as session:
            row = (
                await session.execute(text("SELECT mode, company_name FROM public.installation"))
            ).first()
        data = InstallationView(
            mode=row.mode if row is not None else "personal",
            company_name=row.company_name if row is not None else None,
            server_version=version("ai-bid-tool"),
            contract_version=CONTRACT_VERSION,
        )
        return result("installation show", data.model_dump(mode="json"))

    @router.post("/auth/session", name="auth_session_create", response_model=Result)
    async def auth_session_create(request: Request, body: SessionCreate | MemberSignIn):
        if settings.deployment == "enterprise":
            if isinstance(body, SessionCreate):
                raise access_key_refused()
            source = request.client.host if request.client is not None else None
            view = await members.sign_in(db, attempts, settings, crypto, body, source)
            return result("auth session create", view.model_dump(mode="json"))
        if not isinstance(body, SessionCreate):
            raise ServiceError(
                "invalid_input", "This installation signs in with its access key", 422, 2
            )
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
        session, identity = ctx
        member = (
            await session.get(Member, identity.member_id)
            if identity.member_id is not None
            else None
        )
        return result(
            "auth session show",
            {
                "mode": settings.deployment,
                "actor_kind": identity.actor_kind,
                "scopes": sorted(identity.scopes),
                "expires_at": identity.session_expires_at.isoformat()
                if identity.session_expires_at is not None
                else None,
                "member": (await members.view(session, member)).model_dump(mode="json")
                if member is not None
                else None,
                "must_change_password": identity.must_change_password,
            },
        )

    @router.delete("/auth/session", name="auth_session_delete", response_model=Result)
    async def auth_session_delete(ctx=Depends(context, scope="function")):
        session, identity = ctx
        return result("auth session delete", {"revoked": await members.sign_out(session, identity)})

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
