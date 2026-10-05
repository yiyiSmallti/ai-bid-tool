"""Confidential fields and their values; list views never carry a value."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.common import result
from app.core.config import Settings
from app.core.security import Secrets
from app.schemas.confidential_contracts import (
    ConfidentialFieldCreate,
    ConfidentialFieldUpdate,
    ConfidentialValueSet,
)
from app.schemas.contracts import Result
from app.services import confidential


def create_router(context: Callable[..., Any], settings: Settings) -> APIRouter:
    router = APIRouter()
    secrets = Secrets.for_data(settings)

    @router.post("/confidential-fields", name="confidential_field_add", response_model=Result)
    async def field_add(body: ConfidentialFieldCreate, ctx=Depends(context, scope="function")):
        session, actor = ctx
        return result(
            "confidential field add", await confidential.create_field(session, actor, body)
        )

    @router.get("/confidential-fields", name="confidential_field_list", response_model=Result)
    async def field_list(archived: bool = False, ctx=Depends(context, scope="function")):
        session, actor = ctx
        items = await confidential.list_fields(session, actor, archived=archived)
        return result("confidential field list", {"count": len(items)}, items)

    @router.post(
        "/confidential-fields/{field_id}/revisions",
        name="confidential_field_update",
        response_model=Result,
    )
    async def field_update(
        field_id: UUID, body: ConfidentialFieldUpdate, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        return result(
            "confidential field update",
            await confidential.update_field(session, actor, field_id, body),
        )

    @router.post(
        "/confidential-fields/{field_id}/values", name="confidential_set", response_model=Result
    )
    async def value_set(
        field_id: UUID, body: ConfidentialValueSet, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        return result(
            "confidential set",
            await confidential.set_value(session, actor, field_id, body, secrets),
        )

    @router.get(
        "/confidential-fields/{field_id}/values",
        name="confidential_history",
        response_model=Result,
    )
    async def value_history(
        field_id: UUID, task_id: UUID | None = None, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        items = await confidential.history(session, actor, field_id, task_id)
        return result("confidential history", {"count": len(items)}, items)

    @router.get("/confidential-values", name="confidential_list", response_model=Result)
    async def value_list(task_id: UUID | None = None, ctx=Depends(context, scope="function")):
        session, actor = ctx
        items = await confidential.list_values(session, actor, task_id)
        return result(
            "confidential list",
            {
                "task_id": str(task_id) if task_id else None,
                "missing": sum(item["status"] == "missing" for item in items),
            },
            items,
        )

    @router.post(
        "/confidential-values/{value_id}/reveal",
        name="confidential_reveal",
        response_model=Result,
    )
    async def value_reveal(value_id: UUID, ctx=Depends(context, scope="function")):
        session, actor = ctx
        return result(
            "confidential reveal", await confidential.reveal(session, actor, value_id, secrets)
        )

    return router
