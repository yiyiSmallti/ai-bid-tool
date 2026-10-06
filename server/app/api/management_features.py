"""Feature-only U01 management routes; existing all-row routes remain separate."""

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
from app.services import management_features as features


def create_router(context, settings):
    router = APIRouter(prefix="/management/resources/features")

    async def management_context(request: Request, ctx=Depends(context, scope="function")):
        if getattr(request.state, "contract_version", None) != "4.0":
            raise not_found()
        session, actor = ctx
        session.info["management_settings"] = settings
        return session, actor

    def page(command, value, started):
        return features.page_result(
            command, value, settings.billing_currency, round((time.monotonic() - started) * 1000)
        )

    def detail(command, value, started):
        return features.detail_result(
            command, value, settings.billing_currency, round((time.monotonic() - started) * 1000)
        )

    @router.post("/query", name="resource_feature_browse", response_model=Result)
    async def query(body: ResourceQuery, ctx=Depends(management_context, scope="function")):
        started = time.monotonic()
        session, actor = ctx
        return page(
            features.COMMANDS["browse"], await features.query(session, actor, body), started
        )

    @router.get("/{feature_id}", name="resource_feature_show", response_model=Result)
    async def show(
        feature_id: UUID,
        revision: int | None = Query(None, ge=1),
        ctx=Depends(management_context, scope="function"),
    ):
        started = time.monotonic()
        session, actor = ctx
        return detail(
            features.COMMANDS["detail"],
            await features.detail(
                session, actor, feature_id, ResourceDetailQuery(revision=revision)
            ),
            started,
        )

    @router.post(
        "/{feature_id}/history/query", name="resource_feature_history", response_model=Result
    )
    async def history(
        feature_id: UUID, body: PageQuery, ctx=Depends(management_context, scope="function")
    ):
        started = time.monotonic()
        session, actor = ctx
        return page(
            features.COMMANDS["history"],
            await features.history(session, actor, feature_id, body),
            started,
        )

    @router.post(
        "/{feature_id}/lifecycle", name="resource_feature_lifecycle_set", response_model=Result
    )
    async def set_state(
        feature_id: UUID,
        body: ResourceLifecycleSet,
        ctx=Depends(management_context, scope="function"),
    ):
        started = time.monotonic()
        session, actor = ctx
        return detail(
            features.COMMANDS["lifecycle"],
            await features.set_state(session, actor, feature_id, body),
            started,
        )

    @router.post(
        "/{feature_id}/lifecycle/history/query",
        name="resource_feature_lifecycle_history",
        response_model=Result,
    )
    async def lifecycle_history(
        feature_id: UUID, body: PageQuery, ctx=Depends(management_context, scope="function")
    ):
        started = time.monotonic()
        session, actor = ctx
        return page(
            features.COMMANDS["lifecycle_history"],
            await features.lifecycle_history(session, actor, feature_id, body),
            started,
        )

    return router
