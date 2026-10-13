"""Human-managed local Clef settings."""

from fastapi import APIRouter, Depends

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError
from app.schemas.clef import ClefCheckRequest, ClefSettingsSet
from app.schemas.contracts import Result
from app.services.platform_clef import PlatformClefService


def create_router(
    settings: Settings, db: Database, context, *, service: PlatformClefService | None = None
):
    router = APIRouter(prefix="/platform/clef")
    service = service or PlatformClefService(settings, db)

    async def human(ctx=Depends(context, scope="function")):
        from app.services.platform_credentials import require_human

        actor = ctx[1]
        require_human(actor, "clef:manage")
        return actor

    async def call(operation):
        try:
            return await operation
        except ServiceError:
            raise
        except Exception:
            raise ServiceError(
                "clef_configuration_unavailable", "Clef settings are unavailable", 503, 3
            ) from None

    @router.get("", name="platform_clef_show", response_model=Result)
    async def show(actor=Depends(human)):
        data = await call(service.show(actor))
        return Result(ok=True, command="platform clef show", data=data.model_dump(mode="json"))

    @router.put("", name="platform_clef_set", response_model=Result)
    async def set_settings(body: ClefSettingsSet, actor=Depends(human)):
        data = await call(service.set(actor, body))
        return Result(ok=True, command="platform clef set", data=data.model_dump(mode="json"))

    @router.post("/check", name="platform_clef_check", response_model=Result)
    async def check(body: ClefCheckRequest, actor=Depends(human)):
        data = await call(service.check(actor, body))
        return Result(ok=True, command="platform clef check", data=data.model_dump(mode="json"))

    return router
