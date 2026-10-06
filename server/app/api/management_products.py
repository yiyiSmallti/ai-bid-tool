"""Product-only U01 management routes; existing all-row routes remain separate."""

import time
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.core.errors import not_found
from app.schemas.contracts import Result
from app.schemas.management_pages import (
    PageQuery,
    ResourceDetailQuery,
    ResourceLifecycleSet,
    ResourceQuery,
)
from app.services import management_products as products


def create_router(context, settings):
    router = APIRouter(prefix="/management/resources/products")

    async def management_context(request: Request, ctx=Depends(context, scope="function")):
        if getattr(request.state, "contract_version", None) != "4.0":
            raise not_found()
        session, actor = ctx
        session.info["management_settings"] = settings
        return session, actor

    def page(command, value, started):
        return products.page_result(
            command, value, settings.billing_currency, round((time.monotonic() - started) * 1000)
        )

    def detail(command, value, started):
        return products.detail_result(
            command, value, settings.billing_currency, round((time.monotonic() - started) * 1000)
        )

    @router.post("/query", name="resource_product_browse", response_model=Result)
    async def query(body: ResourceQuery, ctx=Depends(management_context, scope="function")):
        started = time.monotonic()
        session, actor = ctx
        return page(
            products.COMMANDS["browse"], await products.query(session, actor, body), started
        )

    @router.get("/{product_id}", name="resource_product_show", response_model=Result)
    async def show(
        product_id: UUID,
        revision: int | None = Query(None, ge=1),
        ctx=Depends(management_context, scope="function"),
    ):
        started = time.monotonic()
        session, actor = ctx
        return detail(
            products.COMMANDS["detail"],
            await products.detail(
                session, actor, product_id, ResourceDetailQuery(revision=revision)
            ),
            started,
        )

    @router.post(
        "/{product_id}/history/query", name="resource_product_history", response_model=Result
    )
    async def history(
        product_id: UUID, body: PageQuery, ctx=Depends(management_context, scope="function")
    ):
        started = time.monotonic()
        session, actor = ctx
        return page(
            products.COMMANDS["history"],
            await products.history(session, actor, product_id, body),
            started,
        )

    @router.post(
        "/{product_id}/lifecycle", name="resource_product_lifecycle_set", response_model=Result
    )
    async def set_state(
        product_id: UUID,
        body: ResourceLifecycleSet,
        ctx=Depends(management_context, scope="function"),
    ):
        started = time.monotonic()
        session, actor = ctx
        return detail(
            products.COMMANDS["lifecycle"],
            await products.set_state(session, actor, product_id, body),
            started,
        )

    @router.post(
        "/{product_id}/lifecycle/history/query",
        name="resource_product_lifecycle_history",
        response_model=Result,
    )
    async def lifecycle_history(
        product_id: UUID, body: PageQuery, ctx=Depends(management_context, scope="function")
    ):
        started = time.monotonic()
        session, actor = ctx
        return page(
            products.COMMANDS["lifecycle_history"],
            await products.lifecycle_history(session, actor, product_id, body),
            started,
        )

    return router
