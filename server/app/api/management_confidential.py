"""Version-four bounded confidential metadata, masked values and guarded writes."""

import time
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core.errors import not_found
from app.core.security import Secrets
from app.schemas.contracts import Result
from app.schemas.management_pages import (
    ConfidentialHistoryQuery,
    ConfidentialQuery,
    ConfidentialValueRevisionSet,
)
from app.services import management_confidential as confidential


def create_router(context, settings):
    router = APIRouter(prefix="/management")
    secrets = Secrets.for_data(settings)

    async def management_context(request: Request, ctx=Depends(context, scope="function")):
        if getattr(request.state, "contract_version", None) != "4.0":
            raise not_found()
        session, actor = ctx
        session.info["management_settings"] = settings
        return session, actor

    def page(purpose, value, started):
        return confidential.page_result(
            confidential.COMMANDS[purpose],
            value,
            settings.billing_currency,
            round((time.monotonic() - started) * 1000),
        )

    @router.post(
        "/confidential-fields/query", name="confidential_field_browse", response_model=Result
    )
    async def fields(body: ConfidentialQuery, ctx=Depends(management_context, scope="function")):
        started = time.monotonic()
        session, actor = ctx
        return page("fields", await confidential.fields(session, actor, body), started)

    @router.post("/confidential-values/query", name="confidential_browse", response_model=Result)
    async def values(body: ConfidentialQuery, ctx=Depends(management_context, scope="function")):
        started = time.monotonic()
        session, actor = ctx
        return page("values", await confidential.values(session, actor, body), started)

    @router.post(
        "/confidential-fields/{field_id}/values/history/query",
        name="confidential_history-page",
        response_model=Result,
    )
    async def history(
        field_id: UUID,
        body: ConfidentialHistoryQuery,
        ctx=Depends(management_context, scope="function"),
    ):
        started = time.monotonic()
        session, actor = ctx
        return page("history", await confidential.history(session, actor, field_id, body), started)

    @router.post(
        "/confidential-fields/{field_id}/values",
        name="confidential_set-checked",
        response_model=Result,
    )
    async def set_value(
        field_id: UUID,
        body: ConfidentialValueRevisionSet,
        ctx=Depends(management_context, scope="function"),
    ):
        started = time.monotonic()
        session, actor = ctx
        value = await confidential.set_value(session, actor, field_id, body, secrets)
        return confidential.detail_result(
            confidential.COMMANDS["set_value"],
            value,
            settings.billing_currency,
            round((time.monotonic() - started) * 1000),
        )

    return router
