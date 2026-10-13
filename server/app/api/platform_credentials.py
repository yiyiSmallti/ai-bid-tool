"""Encrypted service settings are available only to the installation human."""

from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError
from app.schemas.contracts import Result
from app.schemas.platform_credentials import (
    CredentialCreate,
    CredentialErrorData,
    CredentialImportRequest,
    CredentialListQuery,
    CredentialRemove,
    CredentialReplace,
    CredentialSetActive,
    CredentialTest,
)
from app.services.platform_credentials import PlatformCredentialService, credential_error


def create_router(
    settings: Settings, db: Database, context, *, service: PlatformCredentialService | None = None
):
    router = APIRouter(prefix="/platform/credentials")
    credentials = service or PlatformCredentialService(settings, db)

    async def human(ctx=Depends(context, scope="function")):
        from app.services.platform_credentials import require_human

        actor = ctx[1]
        require_human(actor)
        return actor

    async def call(
        action: str,
        operation: Callable[[], Awaitable[Any]],
    ):
        try:
            value = await operation()
        except ServiceError:
            raise
        except Exception:
            # Unexpected adapter/database errors can contain keys in their exception text.
            # Convert inside the route to prevent Starlette re-raising them to server logs.
            error = credential_error("credential_backend_unavailable")
            raise error from None
        command = "platform credential " + {"set_active": "set-active", "import": "import-env"}.get(
            action, action
        )
        if isinstance(value, CredentialErrorData):
            return JSONResponse(
                status_code=credential_error(value.error.code).status,
                content=Result(
                    ok=False, command=command, data=value.model_dump(mode="json", exclude_none=True)
                ).model_dump(mode="json"),
            )
        if isinstance(value, tuple):
            data, items = value
            return Result(
                ok=True,
                command=command,
                data=data.model_dump(mode="json"),
                items=[item.model_dump(mode="json") for item in items],
            )
        return Result(ok=True, command=command, data=value.model_dump(mode="json"))

    @router.get("", name="platform_credential_list", response_model=Result)
    async def list_credentials(
        state: Literal["active", "disabled", "removed"] | None = None,
        purpose: Literal["standalone_llm", "vendor_search", "clef_workers_ai", "clef_gateway"]
        | None = None,
        after_name: str | None = Query(default=None, pattern=r"^[a-z0-9_]{1,40}$"),
        limit: int = Query(default=100, ge=1, le=100),
        actor=Depends(human),
    ):
        query = CredentialListQuery(
            state=state, purpose=purpose, after_name=after_name, limit=limit
        )
        return await call("list", lambda: credentials.list_credentials(actor, query))

    @router.post("", name="platform_credential_create", response_model=Result)
    async def create(body: CredentialCreate, actor=Depends(human)):
        return await call("create", lambda: credentials.create(actor, body))

    @router.post("/import-env", name="platform_credential_import-env", response_model=Result)
    async def import_env(body: CredentialImportRequest, actor=Depends(human)):
        return await call("import", lambda: credentials.import_env(actor, body))

    @router.get("/{credential_id}", name="platform_credential_show", response_model=Result)
    async def show(credential_id: UUID, actor=Depends(human)):
        return await call(
            "show",
            lambda: credentials.show(actor, credential_id),
        )

    @router.post(
        "/{credential_id}/replace", name="platform_credential_replace", response_model=Result
    )
    async def replace(credential_id: UUID, body: CredentialReplace, actor=Depends(human)):
        return await call(
            "replace",
            lambda: credentials.replace(actor, credential_id, body),
        )

    @router.post(
        "/{credential_id}/active", name="platform_credential_set-active", response_model=Result
    )
    async def set_active(credential_id: UUID, body: CredentialSetActive, actor=Depends(human)):
        return await call(
            "set_active",
            lambda: credentials.set_active(actor, credential_id, body),
        )

    @router.post(
        "/{credential_id}/remove", name="platform_credential_remove", response_model=Result
    )
    async def remove(credential_id: UUID, body: CredentialRemove, actor=Depends(human)):
        return await call(
            "remove",
            lambda: credentials.remove(actor, credential_id, body),
        )

    @router.post("/{credential_id}/test", name="platform_credential_test", response_model=Result)
    async def test(credential_id: UUID, body: CredentialTest, actor=Depends(human)):
        return await call(
            "test",
            lambda: credentials.test(actor, credential_id, body),
        )

    return router
