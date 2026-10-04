"""Platform operator routes; no org context and no access to org business data."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError
from app.core.password_attempts import PasswordAttempts
from app.core.security import TokenSigner
from app.schemas.contracts import Result
from app.schemas.platform_contracts import (
    OrgLookup,
    PasswordSetup,
    PlatformBalanceAdjust,
    PlatformCardCreate,
    PlatformLogin,
    PlatformModelSet,
    PlatformOrgActive,
    PlatformOrgCreate,
)
from app.services import platform


def result(command: str, data=None, items=None) -> dict:
    return Result(ok=True, command=command, data=data or {}, items=items or []).model_dump(
        mode="json"
    )


def create_router(
    settings: Settings,
    db: Database,
    crypto: TokenSigner,
    attempts: PasswordAttempts,
    transport=None,
) -> APIRouter:
    router = APIRouter()
    bearer = HTTPBearer(auto_error=False)

    async def operator(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None:
            raise ServiceError("invalid_session", "Bearer credentials required", 401, 4)
        return platform.identify(settings, crypto, credentials.credentials)

    @router.post("/platform/auth/login", name="platform_login", response_model=Result)
    async def platform_login(body: PlatformLogin, request: Request):
        data = await platform.login(
            attempts,
            settings,
            crypto,
            body.email,
            body.password,
            body.totp,
            request.client.host if request.client else None,
        )
        return result("platform login", data)

    @router.post("/auth/setup-password", name="auth_setup_password", response_model=Result)
    async def setup_password(body: PasswordSetup):
        await platform.setup_password(db, crypto, body.token, body.password)
        return result("auth setup-password", {"password_set": True})

    @router.post("/auth/orgs", name="auth_orgs", response_model=Result)
    async def auth_orgs(body: OrgLookup, request: Request):
        return result(
            "auth orgs",
            items=await platform.user_orgs(
                attempts, body.email, body.password, request.client.host if request.client else None
            ),
        )

    @router.get("/platform/orgs", name="platform_org_list", response_model=Result)
    async def org_list(actor=Depends(operator)):
        async with db.transaction() as session:
            return result("platform org list", items=await platform.list_orgs(session))

    @router.post("/platform/orgs", name="platform_org_create", response_model=Result)
    async def org_create(body: PlatformOrgCreate, actor=Depends(operator)):
        async with db.transaction() as session:
            data = await platform.create_org(session, crypto, actor, body.name, body.admin_email)
        return result("platform org create", data)

    @router.post(
        "/platform/orgs/{org_id}/active", name="platform_org_set_active", response_model=Result
    )
    async def org_set_active(org_id: UUID, body: PlatformOrgActive, actor=Depends(operator)):
        async with db.transaction() as session:
            data = await platform.set_org_active(session, actor, org_id, body.active)
        return result("platform org set-active", data)

    @router.get("/platform/models", name="platform_model_list", response_model=Result)
    async def model_list(actor=Depends(operator)):
        async with db.transaction() as session:
            return result(
                "platform model list",
                {"currency": settings.billing_currency},
                await platform.list_models(session),
            )

    @router.post("/platform/models", name="platform_model_set", response_model=Result)
    async def model_set(body: PlatformModelSet, actor=Depends(operator)):
        async with db.transaction() as session:
            data = await platform.set_model(session, actor, body)
        return result("platform model set", data)

    @router.post(
        "/platform/models/{model_id}/test", name="platform_model_test", response_model=Result
    )
    async def model_test(model_id: str, actor=Depends(operator)):
        data = await platform.test_model(db, settings, actor, model_id, transport)
        return result("platform model test", data)

    @router.post(
        "/platform/orgs/{org_id}/balance", name="platform_org_balance", response_model=Result
    )
    async def org_balance(org_id: UUID, body: PlatformBalanceAdjust, actor=Depends(operator)):
        async with db.transaction() as session:
            data = await platform.adjust_balance(
                session, actor, org_id, body, settings.billing_currency
            )
        return result("platform org balance", data)

    @router.post("/platform/cards", name="platform_card_create", response_model=Result)
    async def card_create(body: PlatformCardCreate, actor=Depends(operator)):
        async with db.transaction() as session:
            data = await platform.create_cards(session, actor, body, settings.billing_currency)
        return result("platform card create", data)

    @router.get("/platform/cards", name="platform_card_list", response_model=Result)
    async def card_list(
        batch_id: UUID | None = None,
        status: Literal["active", "redeemed", "void"] | None = None,
        limit: int = Query(default=200, ge=1, le=1000),
        actor=Depends(operator),
    ):
        async with db.transaction() as session:
            items = await platform.list_cards(session, batch_id, status, limit)
        return result("platform card list", {"currency": settings.billing_currency}, items)

    @router.post("/platform/cards/{card_id}/void", name="platform_card_void", response_model=Result)
    async def card_void(card_id: UUID, actor=Depends(operator)):
        async with db.transaction() as session:
            data = await platform.void_card(session, actor, card_id)
        return result("platform card void", data)

    @router.get("/platform/usage", name="platform_usage", response_model=Result)
    async def usage(
        start: str | None = Query(default=None, alias="from"),
        end: str | None = Query(default=None, alias="to"),
        actor=Depends(operator),
    ):
        async with db.transaction() as session:
            data, items = await platform.usage(session, start, end)
        return result("platform usage", {**data, "currency": settings.billing_currency}, items)

    @router.get("/platform/audit", name="platform_audit", response_model=Result)
    async def audit(limit: int = Query(default=100, ge=1, le=500), actor=Depends(operator)):
        async with db.transaction() as session:
            return result("platform audit", items=await platform.audit_entries(session, limit))

    return router
