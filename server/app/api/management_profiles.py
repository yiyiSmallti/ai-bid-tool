"""Domain-specific U01 management routes; existing all-row routes remain separate."""

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
from app.services import management_profiles as profiles


def create_router(context, settings):
    router = APIRouter(prefix="/management/resources/profiles")

    async def management_context(request: Request, ctx=Depends(context, scope="function")):
        if getattr(request.state, "contract_version", None) != "4.0":
            raise not_found()
        session, actor = ctx
        session.info["management_settings"] = settings
        return session, actor

    def page(command, value, started):
        return profiles.page_result(
            command, value, settings.billing_currency, round((time.monotonic() - started) * 1000)
        )

    def detail(command, value, started):
        return profiles.detail_result(
            command, value, settings.billing_currency, round((time.monotonic() - started) * 1000)
        )

    @router.post("/query", name="resource_profile_browse", response_model=Result)
    async def query(body: ResourceQuery, ctx=Depends(management_context, scope="function")):
        started = time.monotonic()
        session, actor = ctx
        return page(
            profiles.COMMANDS["browse"], await profiles.query(session, actor, body), started
        )

    @router.get("/{profile_id}", name="resource_profile_show", response_model=Result)
    async def show(
        profile_id: UUID,
        revision: int | None = Query(None, ge=1),
        ctx=Depends(management_context, scope="function"),
    ):
        started = time.monotonic()
        session, actor = ctx
        return detail(
            profiles.COMMANDS["detail"],
            await profiles.detail(
                session, actor, profile_id, ResourceDetailQuery(revision=revision)
            ),
            started,
        )

    @router.post(
        "/{profile_id}/history/query", name="resource_profile_history", response_model=Result
    )
    async def history(
        profile_id: UUID, body: PageQuery, ctx=Depends(management_context, scope="function")
    ):
        started = time.monotonic()
        session, actor = ctx
        return page(
            profiles.COMMANDS["history"],
            await profiles.history(session, actor, profile_id, body),
            started,
        )

    @router.post(
        "/{profile_id}/lifecycle", name="resource_profile_lifecycle_set", response_model=Result
    )
    async def set_state(
        profile_id: UUID,
        body: ResourceLifecycleSet,
        ctx=Depends(management_context, scope="function"),
    ):
        started = time.monotonic()
        session, actor = ctx
        return detail(
            profiles.COMMANDS["lifecycle"],
            await profiles.set_state(session, actor, profile_id, body),
            started,
        )

    @router.post(
        "/{profile_id}/lifecycle/history/query",
        name="resource_profile_lifecycle_history",
        response_model=Result,
    )
    async def lifecycle_history(
        profile_id: UUID, body: PageQuery, ctx=Depends(management_context, scope="function")
    ):
        started = time.monotonic()
        session, actor = ctx
        return page(
            profiles.COMMANDS["lifecycle_history"],
            await profiles.lifecycle_history(session, actor, profile_id, body),
            started,
        )

    return router
