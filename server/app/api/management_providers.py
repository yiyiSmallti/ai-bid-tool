"""Metadata-only model management reads, available solely in Result 4.0."""

import time
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core.errors import not_found
from app.schemas.contracts import Result
from app.schemas.management_pages import PageQuery
from app.schemas.management_providers import ProviderCatalogQuery
from app.services import management_providers as service


def create_router(context, settings):
    router = APIRouter(prefix="/management/providers")

    async def management_context(request: Request, ctx=Depends(context, scope="function")):
        if getattr(request.state, "contract_version", None) != "4.0":
            raise not_found()
        session, actor = ctx
        session.info["management_settings"] = settings
        return session, actor

    def elapsed(started):
        return round((time.monotonic() - started) * 1000)

    @router.get("", name="provider_show", response_model=Result)
    async def show(ctx=Depends(management_context, scope="function")):
        started = time.monotonic()
        session, actor = ctx
        value = await service.settings(session, actor)
        return service.detail_result(
            service.COMMANDS["settings"], value, settings.billing_currency, elapsed(started)
        )

    @router.get("/revisions/{config_id}", name="provider_revision_show", response_model=Result)
    async def revision(config_id: UUID, ctx=Depends(management_context, scope="function")):
        started = time.monotonic()
        session, actor = ctx
        value = await service.revision(session, actor, config_id)
        return service.detail_result(
            service.COMMANDS["revision"], value, settings.billing_currency, elapsed(started)
        )

    @router.post("/history/query", name="provider_history_page", response_model=Result)
    async def history(body: PageQuery, ctx=Depends(management_context, scope="function")):
        started = time.monotonic()
        session, actor = ctx
        value = await service.history(session, actor, body)
        return service.page_result(
            service.COMMANDS["history"], value, settings.billing_currency, elapsed(started)
        )

    @router.post("/catalog/query", name="provider_catalog", response_model=Result)
    async def catalog(
        body: ProviderCatalogQuery, ctx=Depends(management_context, scope="function")
    ):
        started = time.monotonic()
        session, actor = ctx
        value = await service.catalog(session, actor, body)
        return service.page_result(
            service.COMMANDS["catalog"], value, settings.billing_currency, elapsed(started)
        )

    return router
