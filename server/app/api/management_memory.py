"""Version-four bounded memory projections; original mutation gates are reused."""

import time
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.core.errors import not_found
from app.memory import management
from app.schemas.contracts import Result
from app.schemas.management_pages import MemoryQuery, PageQuery, ResourceDetailQuery


def create_router(context, settings):
    router = APIRouter(prefix="/management/memories")

    async def management_context(request: Request, ctx=Depends(context, scope="function")):
        if getattr(request.state, "contract_version", None) != "4.0":
            raise not_found()
        return ctx

    @router.post("/query", name="memory_browse", response_model=Result)
    async def query(body: MemoryQuery, ctx=Depends(management_context, scope="function")):
        started = time.monotonic()
        page = await management.query(ctx[0], ctx[1], body, settings)
        return management.page_result(
            "memory browse",
            page,
            settings.billing_currency,
            round((time.monotonic() - started) * 1000),
        )

    @router.get("/{memory_id}", name="management_memory_show", response_model=Result)
    async def show(
        memory_id: UUID,
        revision: int | None = Query(None, ge=1),
        ctx=Depends(management_context, scope="function"),
    ):
        started = time.monotonic()
        value = await management.detail(
            ctx[0], ctx[1], memory_id, ResourceDetailQuery(revision=revision), settings
        )
        return management.detail_result(
            "memory show",
            value,
            settings.billing_currency,
            round((time.monotonic() - started) * 1000),
        )

    @router.post(
        "/{memory_id}/history/query", name="management_memory_history", response_model=Result
    )
    async def history(
        memory_id: UUID, body: PageQuery, ctx=Depends(management_context, scope="function")
    ):
        started = time.monotonic()
        page = await management.history(ctx[0], ctx[1], memory_id, body, settings)
        return management.page_result(
            "memory history",
            page,
            settings.billing_currency,
            round((time.monotonic() - started) * 1000),
        )

    return router
