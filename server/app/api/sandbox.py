"""Narrow sandbox routes, with bounded request bodies and authenticated downloads."""

import json
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response
from fastapi.routing import APIRoute
from procrastinate.exceptions import ConnectorException
from psycopg import OperationalError
from pydantic import ValidationError
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartParser

from app.core.errors import ServiceError, not_found
from app.core.security import Secrets
from app.models.entities import Job
from app.schemas.contracts import Result
from app.schemas.sandbox_contracts import (
    PrototypeSpec,
    SandboxDownloadLink,
    SandboxSubmit,
    VendorSpec,
)
from app.services import sandbox


def submission_openapi() -> dict:
    schema = SandboxSubmit.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(value):
        if isinstance(value, list):
            return [inline(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return inline(definitions[value["$ref"].removeprefix("#/$defs/")])
        return {
            key: (
                {"propertyName": item["propertyName"]} if key == "discriminator" else inline(item)
            )
            for key, item in value.items()
        }

    submit = inline(schema)
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "allOf": [
                            submit,
                            {"properties": {"spec": inline(VendorSpec.model_json_schema())}},
                        ]
                    }
                },
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["submit", "html"],
                        "properties": {
                            "submit": {
                                "type": "string",
                                "contentMediaType": "application/json",
                                "contentSchema": {
                                    "allOf": [
                                        submit,
                                        {
                                            "properties": {
                                                "spec": inline(PrototypeSpec.model_json_schema())
                                            }
                                        },
                                    ]
                                },
                            },
                            "html": {"type": "string", "format": "binary"},
                        },
                    }
                },
            },
        }
    }


class BoundedSandboxRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def bounded(request: Request):
            if request.method == "POST":
                content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
                limit = (
                    4 * 1024**2 + 128 * 1024 if content_type == "multipart/form-data" else 64 * 1024
                )
                content = bytearray()
                async for chunk in request.stream():
                    if len(content) + len(chunk) > limit:
                        raise ServiceError(
                            "sandbox_input_limit", "Sandbox request exceeds input limit", 413, 2
                        )
                    content.extend(chunk)
                request._body = bytes(content)
            return await handler(request)

        return bounded


class MemoryOnlyHTMLParser(MultiPartParser):
    # The enclosing route bounds the entire request. Avoid plaintext upload spill
    # into the trusted API host's temporary filesystem before storage encryption.
    spool_max_size = 4 * 1024**2 + 128 * 1024


@asynccontextmanager
async def html_form(request: Request):
    form = await MemoryOnlyHTMLParser(
        request.headers, request.stream(), max_files=1, max_fields=1, max_part_size=64 * 1024
    ).parse()
    try:
        yield form
    finally:
        await form.close()


async def parse_submission(request: Request) -> tuple[SandboxSubmit, bytes | None]:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
    try:
        if content_type == "application/json":
            body = SandboxSubmit.model_validate_json(await request.body())
            if not isinstance(body.spec, VendorSpec):
                raise ServiceError(
                    "sandbox_html_required", "Render requires multipart HTML", 422, 2
                )
            return body, None
        if content_type != "multipart/form-data":
            raise ServiceError(
                "sandbox_input_type", "Use JSON capture or multipart HTML render", 415, 2
            )
        async with html_form(request) as form:
            if set(form) != {"submit", "html"} or len(form.multi_items()) != 2:
                raise ServiceError(
                    "sandbox_input_fields", "Render requires submit and html fields", 422, 2
                )
            raw, upload = form["submit"], form["html"]
            if (
                not isinstance(raw, str)
                or len(raw.encode()) > 64 * 1024
                or not isinstance(upload, UploadFile)
            ):
                raise ServiceError("sandbox_input_fields", "Invalid render input fields", 422, 2)
            if (upload.content_type or "").split(";", 1)[0].strip().lower() != "text/html":
                raise ServiceError(
                    "sandbox_input_type", "Prototype upload must be text/html", 422, 2
                )
            body = SandboxSubmit.model_validate_json(raw)
            if not isinstance(body.spec, PrototypeSpec):
                raise ServiceError("sandbox_unexpected_html", "Capture accepts JSON only", 422, 2)
            html = await upload.read(4 * 1024**2 + 1)
            if len(html) > 4 * 1024**2:
                raise ServiceError("sandbox_input_limit", "HTML exceeds input limit", 413, 2)
            return body, html
    except ValidationError as exc:
        # Validation errors must never echo submitted HTML, URLs or model output.
        raise RequestValidationError(exc.errors(include_input=False)) from None
    except (json.JSONDecodeError, UnicodeError):
        raise ServiceError("sandbox_invalid_json", "Invalid sandbox JSON input", 422, 2) from None


def create_router(context, db, storage, queue, crypto, processor):
    router = APIRouter(route_class=BoundedSandboxRoute)

    @router.post(
        "/tasks/{task_id}/sandbox-runs",
        name="sandbox_submit",
        response_model=Result,
        openapi_extra=submission_openapi(),
    )
    async def submit(task_id: UUID, request: Request, ctx=Depends(context, scope="function")):
        session, actor = ctx
        from app.services.task_workflow import access as task_access

        _, workflow, member = await task_access(session, actor, task_id)
        if member is None or member.role not in {"owner", "contributor"}:
            raise ServiceError("forbidden", "Task role does not permit submission", 403, 4)
        if workflow.state == "archived":
            raise ServiceError("task_archived", "Task is archived", 409, 2)
        body, html = await parse_submission(request)
        if body.retry:
            sandbox.require_access(
                actor,
                "sandbox:render" if isinstance(body.spec, PrototypeSpec) else "sandbox:capture",
            )
            from sqlalchemy import select

            from app.models.sandbox import SandboxRun

            previous = await session.scalar(
                select(SandboxRun).where(SandboxRun.request_hash == body.expected_request_hash)
            )
            if previous is not None and previous.task_id != task_id:
                from app.core.errors import not_found

                raise not_found()
            if previous is not None:
                await sandbox.reconcile_run(
                    db, previous.id, actor.org_id, sandbox.browser_for(processor)
                )
                await session.refresh(previous)
        data, job = await sandbox.submit(
            session,
            actor,
            task_id,
            body,
            html,
            storage,
            Secrets.for_data(processor.settings),
            sandbox.browser_for(processor),
        )
        if job is not None and job.status == "queued" and job.queue_id is None:
            await session.commit()
            try:
                queue_id = await queue.enqueue_sandbox(str(actor.org_id), str(job.id))
            except (OSError, ConnectorException, OperationalError) as exc:
                raise ServiceError(
                    "queue_unavailable", "Job saved; repeat request to schedule it", 503, 3
                ) from exc
            async with db.transaction(actor.org_id) as update:
                saved = await update.get(Job, job.id)
                if saved is not None:
                    saved.queue_id = queue_id
        return Result(
            ok=True,
            command="sandbox render" if isinstance(body.spec, PrototypeSpec) else "sandbox capture",
            data=data,
        )

    @router.get("/tasks/{task_id}/sandbox-runs", name="sandbox_list", response_model=Result)
    async def list_runs(
        task_id: UUID,
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=100),
        ctx=Depends(context, scope="function"),
    ):
        items = await sandbox.list_runs(ctx[0], ctx[1], task_id, offset, limit)
        return Result(
            ok=True, command="sandbox list", data={"offset": offset, "limit": limit}, items=items
        )

    @router.get("/sandbox-runs/{run_id}", name="sandbox_show", response_model=Result)
    async def show_run(run_id: UUID, ctx=Depends(context, scope="function")):
        return Result(
            ok=True, command="sandbox show", data=await sandbox.show_run(ctx[0], ctx[1], run_id)
        )

    @router.get(
        "/sandbox-artifacts/{artifact_id}/download-link",
        name="sandbox_download",
        response_model=Result,
    )
    async def download_link(artifact_id: UUID, ctx=Depends(context, scope="function")):
        session, actor = ctx
        artifact, attempt, run = await sandbox.require_artifact(session, actor, artifact_id)
        token = crypto.issue(
            {
                "kind": "sandbox_artifact",
                "org_id": str(actor.org_id),
                "artifact_id": str(artifact.id),
                "sha256": artifact.plaintext_sha256,
            },
            300,
        )
        data = SandboxDownloadLink(
            artifact=sandbox.artifact_view(artifact, attempt),
            url=f"/sandbox-artifacts/{artifact.id}/download?token={token}",
        )
        sandbox.event(
            session,
            actor,
            "artifact_download_link_issued",
            run,
            artifact_id=str(artifact.id),
            sha256=artifact.plaintext_sha256,
        )
        return Result(ok=True, command="sandbox download", data=data.model_dump(mode="json"))

    @router.get("/sandbox-artifacts/{artifact_id}/download", name="sandbox_download_file")
    async def download(
        artifact_id: UUID,
        token: str = Query(..., max_length=4096),
        ctx=Depends(context, scope="function"),
    ):
        session, actor = ctx
        artifact, attempt, run = await sandbox.require_artifact(session, actor, artifact_id)
        signed = crypto.open(token)
        if any(
            (
                signed.get("kind") != "sandbox_artifact",
                signed.get("org_id") != str(actor.org_id),
                signed.get("artifact_id") != str(artifact.id),
                signed.get("sha256") != artifact.plaintext_sha256,
            )
        ):
            raise not_found()
        content = await storage.read_bounded(actor.org_id, artifact.object_key, artifact.size_bytes)
        if len(content) != artifact.size_bytes or sandbox.sha(content) != artifact.plaintext_sha256:
            raise ServiceError(
                "sandbox_integrity_failed", "Stored artifact failed integrity checks", 409, 4
            )
        sandbox.event(
            session,
            actor,
            "download_served",
            run,
            artifact_id=str(artifact.id),
            sha256=artifact.plaintext_sha256,
        )
        extension = "png" if artifact.media_type == "image/png" else "bin"
        return Response(
            content,
            media_type=artifact.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="sandbox-{artifact.id}.{extension}"',
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
                "Content-Security-Policy": "sandbox; default-src 'none'; frame-ancestors 'none'",
            },
        )

    return router
