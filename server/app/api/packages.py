"""Project package routes: export, status and download (docs/plan/enterprise-gateway.md#packages)."""

from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.api.common import result
from app.jobs.queue import dispatch_process_job
from app.schemas.contracts import Result
from app.schemas.packages import PackageExport
from app.services import packages


def create_router(context, storage, queue, settings) -> APIRouter:
    router = APIRouter()

    @router.post("/packages/export", name="package_export", response_model=Result)
    async def package_export(body: PackageExport, ctx=Depends(context, scope="function")):
        session, identity = ctx
        job = await packages.submit_export(session, identity, body.task_id, settings)
        await dispatch_process_job(session, queue, job)
        return result("package export", packages.job_view(job))

    @router.get("/packages/{job_id}", name="package_status", response_model=Result)
    async def package_status(job_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        job = await packages.package_job(session, identity, job_id)
        return result("package status", packages.job_view(job))

    @router.get("/packages/{job_id}/file", name="package_file")
    async def package_file(job_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        content, name = await packages.download(session, identity, job_id, storage, settings)
        return Response(
            content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "Cache-Control": "no-store",
            },
        )

    return router
