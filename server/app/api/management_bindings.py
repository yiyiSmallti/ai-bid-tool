"""Version-four management reads for immutable, human-reviewed export bindings."""

import time
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core.errors import not_found
from app.schemas.contracts import Result
from app.schemas.management_pages import BindingDetailQuery, BindingQuery
from app.services import management_bindings as bindings


def create_router(context, settings):
    router = APIRouter(prefix="/management/export-bindings")

    async def management_context(request: Request, ctx=Depends(context, scope="function")):
        if getattr(request.state, "contract_version", None) != "4.0":
            raise not_found()
        session, actor = ctx
        session.info["management_settings"] = settings
        return session, actor

    @router.post("/query", name="export_binding_browse", response_model=Result)
    async def query(body: BindingQuery, ctx=Depends(management_context, scope="function")):
        started = time.monotonic()
        session, actor = ctx
        value = await bindings.query(session, actor, body)
        return bindings.page_result(
            bindings.COMMANDS["browse"],
            value,
            settings.billing_currency,
            round((time.monotonic() - started) * 1000),
        )

    @router.get("/{binding_id}", name="export_binding_show", response_model=Result)
    async def show(
        binding_id: UUID,
        template_revision_id: UUID,
        ctx=Depends(management_context, scope="function"),
    ):
        started = time.monotonic()
        session, actor = ctx
        value = await bindings.detail(
            session,
            actor,
            binding_id,
            BindingDetailQuery(template_revision_id=template_revision_id),
        )
        return bindings.detail_result(
            bindings.COMMANDS["detail"],
            value,
            settings.billing_currency,
            round((time.monotonic() - started) * 1000),
        )

    return router
