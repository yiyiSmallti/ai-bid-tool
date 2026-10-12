"""Platform operator routes; no org context and no access to org business data."""

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError

from app.api.platform_clef import create_router as create_clef_router
from app.api.platform_credentials import create_router as create_credentials_router
from app.api.platform_trust_anchors import create_router as create_trust_anchors_router
from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError
from app.core.password_attempts import PasswordAttempts
from app.core.security import TokenSigner
from app.schemas.contracts import Result
from app.schemas.platform_contracts import (
    OrgLookup,
    PlatformLogin,
    PlatformModelSet,
    PlatformModelTest,
)
from app.schemas.platform_credentials import CatalogResolveTarget
from app.services import platform
from app.services.platform_credentials import PlatformCredentialResolver, database_error


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
    processor=None,
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

    @router.post("/auth/orgs", name="auth_orgs", response_model=Result)
    async def auth_orgs(body: OrgLookup, request: Request):
        return result(
            "auth orgs",
            items=await platform.user_orgs(
                attempts, body.email, body.password, request.client.host if request.client else None
            ),
        )

    @router.get("/platform/models", name="platform_model_list", response_model=Result)
    async def model_list(actor=Depends(operator)):
        async with db.transaction() as session:
            return result(
                "platform model list",
                {},
                await platform.list_models(session, PlatformCredentialResolver(settings)),
            )

    @router.post("/platform/models", name="platform_model_set", response_model=Result)
    async def model_set(body: PlatformModelSet, actor=Depends(operator)):
        try:
            async with db.transaction() as session:
                data = await platform.set_model(session, actor, body)
        except SQLAlchemyError as exc:
            raise database_error(exc) from None
        state = await PlatformCredentialResolver(settings).readiness(
            CatalogResolveTarget(model_id=data["id"], expected_model_revision=data["revision"])
        )
        data["credential_configured"] = state.configured
        return result("platform model set", data)

    @router.post(
        "/platform/models/{model_id}/test", name="platform_model_test", response_model=Result
    )
    async def model_test(model_id: str, body: PlatformModelTest, actor=Depends(operator)):
        data = await platform.test_model(
            db, settings, actor, model_id, transport, body=body, processor=processor
        )
        return Result(
            ok=data.get("passed", True),
            command="platform model test",
            data=data,
            cost=data.get("preflight", {}).get("estimate", data.get("cost", {})),
        )

    router.include_router(create_clef_router(settings, operator))
    router.include_router(create_credentials_router(settings, operator))
    router.include_router(create_trust_anchors_router(db, operator))
    return router
