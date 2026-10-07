"""Template-only U01 management routes; existing all-row routes remain separate."""

import time
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.core.errors import not_found
from app.schemas.contracts import Result
from app.schemas.management_pages import (
    PageQuery,
    ResourceLifecycleSet,
    ResourceQuery,
    TemplateDetailQuery,
)
from app.services import management_templates as templates


def create_router(context, settings):
    router = APIRouter(prefix="/management/resources/templates")

    async def management_context(request: Request, ctx=Depends(context, scope="function")):
        if getattr(request.state, "contract_version", None) != "4.0":
            raise not_found()
        session, actor = ctx
        session.info["management_settings"] = settings
        return session, actor

    def page(command, value, started):
        return templates.page_result(
            command, value, settings.billing_currency, round((time.monotonic() - started) * 1000)
        )

    def detail(command, value, started):
        return templates.detail_result(
            command, value, settings.billing_currency, round((time.monotonic() - started) * 1000)
        )

    @router.post("/query", name="resource_template_browse", response_model=Result)
    async def query(body: ResourceQuery, ctx=Depends(management_context, scope="function")):
        started = time.monotonic()
        session, actor = ctx
        return page(
            templates.COMMANDS["browse"], await templates.query(session, actor, body), started
        )

    @router.get("/{template_id}", name="resource_template_show", response_model=Result)
    async def show(
        template_id: UUID,
        revision: int | None = Query(None, ge=1),
        revision_id: UUID | None = Query(None),
        ctx=Depends(management_context, scope="function"),
    ):
        if revision is not None and revision_id is not None:
            templates.fail("invalid_input", "Choose revision or revision_id, not both", 422)
        started = time.monotonic()
        session, actor = ctx
        return detail(
            templates.COMMANDS["detail"],
            await templates.detail(
                session,
                actor,
                template_id,
                TemplateDetailQuery(revision=revision, revision_id=revision_id),
            ),
            started,
        )

    @router.post(
        "/{template_id}/history/query", name="resource_template_history", response_model=Result
    )
    async def history(
        template_id: UUID, body: PageQuery, ctx=Depends(management_context, scope="function")
    ):
        started = time.monotonic()
        session, actor = ctx
        return page(
            templates.COMMANDS["history"],
            await templates.history(session, actor, template_id, body),
            started,
        )

    @router.post(
        "/{template_id}/lifecycle", name="resource_template_lifecycle_set", response_model=Result
    )
    async def set_state(
        template_id: UUID,
        body: ResourceLifecycleSet,
        ctx=Depends(management_context, scope="function"),
    ):
        started = time.monotonic()
        session, actor = ctx
        return detail(
            templates.COMMANDS["lifecycle"],
            await templates.set_state(session, actor, template_id, body),
            started,
        )

    @router.post(
        "/{template_id}/lifecycle/history/query",
        name="resource_template_lifecycle_history",
        response_model=Result,
    )
    async def lifecycle_history(
        template_id: UUID, body: PageQuery, ctx=Depends(management_context, scope="function")
    ):
        started = time.monotonic()
        session, actor = ctx
        return page(
            templates.COMMANDS["lifecycle_history"],
            await templates.lifecycle_history(session, actor, template_id, body),
            started,
        )

    return router
