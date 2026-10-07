"""Domain-specific U01 management routes; existing all-row routes remain separate."""

import time
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.core.errors import not_found
from app.schemas.contracts import Result
from app.schemas.management_pages import (
    CertificateDetailQuery as ResourceDetailQuery,
)
from app.schemas.management_pages import (
    PageQuery,
    ResourceLifecycleSet,
    ResourceQuery,
)
from app.services import management_certificates as certificates


def create_router(context, settings):
    router = APIRouter(prefix="/management/resources/certificates")

    async def management_context(request: Request, ctx=Depends(context, scope="function")):
        if getattr(request.state, "contract_version", None) != "4.0":
            raise not_found()
        session, actor = ctx
        session.info["management_settings"] = settings
        return session, actor

    def page(command, value, started):
        return certificates.page_result(
            command, value, settings.billing_currency, round((time.monotonic() - started) * 1000)
        )

    def detail(command, value, started):
        return certificates.detail_result(
            command, value, settings.billing_currency, round((time.monotonic() - started) * 1000)
        )

    @router.post("/query", name="resource_certificate_browse", response_model=Result)
    async def query(body: ResourceQuery, ctx=Depends(management_context, scope="function")):
        started = time.monotonic()
        session, actor = ctx
        return page(
            certificates.COMMANDS["browse"], await certificates.query(session, actor, body), started
        )

    @router.get("/{certificate_id}", name="resource_certificate_show", response_model=Result)
    async def show(
        certificate_id: UUID,
        revision: int | None = Query(None, ge=1),
        as_of: date | None = Query(None),
        ctx=Depends(management_context, scope="function"),
    ):
        started = time.monotonic()
        session, actor = ctx
        return detail(
            certificates.COMMANDS["detail"],
            await certificates.detail(
                session, actor, certificate_id, ResourceDetailQuery(revision=revision, as_of=as_of)
            ),
            started,
        )

    @router.post(
        "/{certificate_id}/history/query",
        name="resource_certificate_history",
        response_model=Result,
    )
    async def history(
        certificate_id: UUID, body: PageQuery, ctx=Depends(management_context, scope="function")
    ):
        started = time.monotonic()
        session, actor = ctx
        return page(
            certificates.COMMANDS["history"],
            await certificates.history(session, actor, certificate_id, body),
            started,
        )

    @router.post(
        "/{certificate_id}/lifecycle",
        name="resource_certificate_lifecycle_set",
        response_model=Result,
    )
    async def set_state(
        certificate_id: UUID,
        body: ResourceLifecycleSet,
        ctx=Depends(management_context, scope="function"),
    ):
        started = time.monotonic()
        session, actor = ctx
        return detail(
            certificates.COMMANDS["lifecycle"],
            await certificates.set_state(session, actor, certificate_id, body),
            started,
        )

    @router.post(
        "/{certificate_id}/lifecycle/history/query",
        name="resource_certificate_lifecycle_history",
        response_model=Result,
    )
    async def lifecycle_history(
        certificate_id: UUID, body: PageQuery, ctx=Depends(management_context, scope="function")
    ):
        started = time.monotonic()
        session, actor = ctx
        return page(
            certificates.COMMANDS["lifecycle_history"],
            await certificates.lifecycle_history(session, actor, certificate_id, body),
            started,
        )

    return router
