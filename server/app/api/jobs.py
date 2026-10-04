"""Job status and cancellation across every job kind."""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.common import result
from app.providers.storage import Storage
from app.schemas.contracts import Result
from app.services import jobs


def create_router(context: Callable[..., Any], storage: Storage) -> APIRouter:
    router = APIRouter()

    @router.get("/jobs/{job_id}", name="job_status", response_model=Result)
    async def job_status(job_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        return await jobs.status(session, identity, job_id, storage)

    @router.post("/jobs/{job_id}/cancel", name="job_cancel", response_model=Result)
    async def job_cancel(job_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        job = await jobs.cancel(session, identity, job_id, storage)
        return result("job cancel", {"id": str(job.id), "status": job.status})

    return router
