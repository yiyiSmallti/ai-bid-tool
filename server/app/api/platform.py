"""Platform operator routes; no org context and no access to org business data."""

from fastapi import APIRouter, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import Field

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError
from app.core.security import Secrets
from app.schemas.contracts import Contract, Result
from app.services import platform


class PlatformLogin(Contract):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)
    totp: str = Field(min_length=6, max_length=6)


class PasswordSetup(Contract):
    token: str = Field(min_length=1, max_length=4096)
    password: str = Field(min_length=1, max_length=1024)


def result(command: str, data=None, items=None) -> dict:
    return Result(ok=True, command=command, data=data or {}, items=items or []).model_dump(
        mode="json"
    )


def create_router(settings: Settings, db: Database, crypto: Secrets) -> APIRouter:
    router = APIRouter()
    bearer = HTTPBearer(auto_error=False)

    async def operator(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if credentials is None:
            raise ServiceError("invalid_session", "Bearer credentials required", 401, 4)
        return platform.identify(settings, crypto, credentials.credentials)

    @router.post("/platform/auth/login", name="platform_login", response_model=Result)
    async def platform_login(body: PlatformLogin):
        data = await platform.login(db, settings, crypto, body.email, body.password, body.totp)
        return result("platform login", data)

    @router.post("/auth/setup-password", name="auth_setup_password", response_model=Result)
    async def setup_password(body: PasswordSetup):
        await platform.setup_password(db, crypto, body.token, body.password)
        return result("auth setup-password", {"password_set": True})

    return router
