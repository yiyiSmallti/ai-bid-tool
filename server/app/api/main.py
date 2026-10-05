"""Application assembly: middleware, error envelopes, authentication context, routers."""

import json
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from app.api.account import create_router as create_account_router
from app.api.check import create_router as create_check_router
from app.api.confidential import create_router as create_confidential_router
from app.api.exports import create_router as create_export_router
from app.api.jobs import create_router as create_job_router
from app.api.org_console import create_router as create_org_console_router
from app.api.platform import create_router as create_platform_router
from app.api.providers import create_router as create_provider_router
from app.api.resources import create_router as create_resource_router
from app.api.response_cards import create_router as create_response_router
from app.api.sandbox import create_router as create_sandbox_router
from app.api.score import create_router as create_score_router
from app.api.task_board import create_router as create_task_board_router
from app.api.task_workflow import create_router as create_task_workflow_router
from app.api.tenders import create_router as create_tender_router
from app.core.config import Settings
from app.core.credential_db import close_connections, get_connections
from app.core.db import Database
from app.core.errors import ServiceError
from app.core.password_attempts import PasswordAttempts
from app.core.security import TokenSigner
from app.jobs.processor import Processor
from app.jobs.queue import Queue
from app.providers.disabled import DisabledLLM
from app.providers.llm import resolve_llm
from app.providers.local_ocr import LocalOCR
from app.providers.storage import create_storage
from app.schemas.contracts import CONTRACT_VERSION, Result
from app.services import billing
from app.services.auth import authenticate, set_actor_context

CONSOLE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def mount_console(app: FastAPI, web_dir: Path) -> None:
    root = web_dir.resolve()
    if not (root / "index.html").is_file():
        raise RuntimeError("BID_WEB_DIR must contain a built index.html")

    @app.get("/app", include_in_schema=False)
    @app.get("/app/{path:path}", include_in_schema=False)
    async def console(path: str = ""):
        target = (root / path).resolve()
        # Unknown paths are client-side routes; nothing outside the build is served.
        if not target.is_relative_to(root) or not target.is_file():
            target = root / "index.html"
        headers = dict(CONSOLE_HEADERS)
        if target.name == "index.html":
            headers["Cache-Control"] = "no-store"
        return FileResponse(target, headers=headers)


def create_app(
    settings: Settings | None = None, *, llm=None, ocr=None, queue=None, llm_transport=None
) -> FastAPI:
    settings = settings or Settings.load()
    settings.assert_vendor_credentials_absent()
    db, crypto = Database(settings), TokenSigner.for_tokens(settings)
    password_attempts = PasswordAttempts(db)
    storage = create_storage(settings)
    # Test injection is explicit; runtime jobs resolve org configuration then the catalog.
    resolve: Callable[..., Awaitable[Any]] | None = None
    if llm is None:

        async def resolve_default(session, job=None):
            return await resolve_llm(session, settings, llm_transport, job)

        resolve, llm = resolve_default, DisabledLLM()
    ocr = ocr or LocalOCR(settings.ocr_language, settings.ocr_data_dir)
    queue = queue or Queue(settings)
    processor = Processor(settings, db, storage, llm, ocr, resolve)
    queue.processor = processor

    @asynccontextmanager
    async def lifespan(_app):
        await db.verify_role()
        await get_connections(settings).management.verify()
        await get_connections(settings).reader.verify()
        await billing.verify_currency(db, settings.billing_currency)
        try:
            yield
        finally:
            await password_attempts.close()
            stream_caps = getattr(_app.state, "workflow_stream_caps", None)
            if stream_caps is not None:
                await stream_caps.close()
            await db.engine.dispose()
            await close_connections(settings)

    app = FastAPI(
        title="Local API",
        version=CONTRACT_VERSION,
        lifespan=lifespan,
        responses={code: {"model": Result} for code in (400, 401, 403, 404, 409, 413, 422, 503)},
    )
    app.state.db, app.state.processor, app.state.storage, app.state.queue = (
        db,
        processor,
        storage,
        queue,
    )
    app.state.crypto = crypto
    app.state.password_attempts = password_attempts

    @app.middleware("http")
    async def bound_source_input(request: Request, call_next):
        parts = request.url.path.split("/")
        credential_route = request.url.path.startswith("/platform/credentials")
        if credential_route:
            from app.services.platform import identify

            header = request.headers.get("authorization", "")
            try:
                scheme, _, token = header.partition(" ")
                if scheme.lower() != "bearer" or not token:
                    raise ServiceError("invalid_session", "Invalid platform session", 401, 4)
                request.state.credential_actor = identify(settings, crypto, token)
            except ServiceError:
                # The ordinary pool records a fixed rejection category; it never invokes
                # privileged credential functions or creates probe authority.
                from app.services.platform import audit

                try:
                    async with db.transaction() as session:
                        audit(
                            session,
                            "unauthenticated@localhost",
                            "platform.credential_denied",
                            "denied",
                            details={"error_code": "invalid_session"},
                        )
                except Exception:
                    return error_response(
                        request,
                        ServiceError(
                            "credential_audit_unavailable",
                            "Credential audit could not be saved",
                            503,
                            3,
                        ),
                    )
                return error_response(
                    request, ServiceError("invalid_session", "Invalid platform session", 401, 4)
                )
        if credential_route or (
            request.method == "POST"
            and len(parts) == 4
            and parts[1] == "tasks"
            and parts[3] == "evidence-sources"
        ):
            content = bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > (512 * 1024 if credential_route else 128 * 1024):
                    if credential_route:
                        from app.services.platform_credentials import (
                            PlatformCredentialService,
                            credential_error,
                        )

                        try:
                            await PlatformCredentialService(settings).record_failure(
                                request.state.credential_actor,
                                "read",
                                credential_error("invalid_input"),
                            )
                        except ServiceError as exc:
                            return error_response(request, exc)
                    return error_response(
                        request,
                        ServiceError("invalid_input", "Credential input exceeds JSON limit", 422, 2)
                        if credential_route
                        else ServiceError(
                            "input_too_large", "Source input exceeds JSON limit", 413, 2
                        ),
                    )
            # Starlette's wrapped request replays its cached body to FastAPI.
            request._body = bytes(content)
        return await call_next(request)

    @app.middleware("http")
    async def contract_header(request: Request, call_next):
        start = time.monotonic()
        version = "4.0" if request.url.path.startswith("/v4/") else "3.0"
        if version == "4.0":
            request.scope["path"] = request.scope["path"][3:]
            request.scope["raw_path"] = request.scope["path"].encode()
        request.state.contract_version = version
        path = request.scope["path"]
        budget_route = path in {"/billing/low-balance-policy", "/billing/notices"} or (
            path.startswith("/tasks/") and "/budget" in path
        )
        parts = path.strip("/").split("/")
        assessment_route = (
            (
                len(parts) == 3
                and parts[0] == "tasks"
                and parts[2] in {"assessment-inputs", "assessment-citation"}
            )
            or (
                len(parts) == 3
                and parts[0] == "tasks"
                and parts[2] == "jobs"
                and request.query_params.get("kind") in {"check", "score_rubric", "score"}
            )
            or (
                request.query_params.get("view") == "console"
                and (
                    path.startswith("/checks/")
                    or (
                        path.startswith("/tasks/")
                        and any(
                            segment in {"checks", "score-rubrics", "scores"}
                            for segment in parts[2:]
                        )
                    )
                )
            )
        )
        response = (
            error_response(request, ServiceError("not_found", "Resource not found", 404, 4))
            if version == "3.0" and (budget_route or assessment_route)
            else await call_next(request)
        )
        response.headers["X-Bid-Contract-Version"] = version
        response.headers["X-Duration-Ms"] = str(int((time.monotonic() - start) * 1000))
        response.headers["Cache-Control"] = "no-store"
        if response.headers.get("content-type", "").startswith("application/json"):
            raw = (
                bytes(response.body)
                if isinstance(response, JSONResponse)
                else b"".join([part async for part in response.body_iterator])
            )
            payload = json.loads(raw)
            if isinstance(payload, dict) and "command" in payload and "duration_ms" in payload:
                payload["duration_ms"] = int((time.monotonic() - start) * 1000)
                if "budget_preflight" in payload.get("data", {}):
                    payload["cost"] = payload["data"]["budget_preflight"]["estimate"]
                cost = payload.get("cost", {})
                if cost.get("basis") in {"zero", "cache_hit"}:
                    cost["billing_currency"] = settings.billing_currency
                if version == "3.0":
                    from app.schemas.compatibility import legacy_projection

                    payload = legacy_projection(payload)
                raw = json.dumps(payload, ensure_ascii=False).encode()
            headers = {
                key: value for key, value in response.headers.items() if key != "content-length"
            }
            response = Response(raw, status_code=response.status_code, headers=headers)
        return response

    def error_response(request: Request, error: ServiceError):
        command = request.scope.get("route")
        name = command.name.replace("_", " ") if command else "request"
        if command is None and request.url.path.startswith("/platform/credentials"):
            tail = request.url.path.removeprefix("/platform/credentials").strip("/")
            action = (
                {
                    "active": "set-active",
                    "replace": "replace",
                    "remove": "remove",
                    "test": "test",
                }.get(tail.rsplit("/", 1)[-1], "show")
                if tail
                else "list"
                if request.method == "GET"
                else "create"
            )
            if tail == "import-env":
                action = "import-env"
            name = "platform credential " + action
        body = Result(
            ok=False,
            command=name,
            data={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "exit_code": error.exit_code,
                }
            },
        )
        retry = {
            "auth_busy": "1",
            "too_many_attempts": "900",
            "stream_limit": "1",
            "board_busy": "1",
        }.get(error.code)
        return JSONResponse(
            status_code=error.status,
            content=body.model_dump(mode="json"),
            headers={"Retry-After": retry} if retry is not None else None,
        )

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, error: ServiceError):
        return error_response(request, error)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        if request.url.path.startswith("/platform/credentials"):
            from app.services.platform_credentials import (
                PlatformCredentialService,
                credential_error,
            )

            actor = getattr(request.state, "credential_actor", None)
            dry_import = (
                request.url.path == "/platform/credentials/import-env"
                and isinstance(error.body, dict)
                and error.body.get("dry_run") is True
            )
            if actor is not None and not dry_import:
                try:
                    await PlatformCredentialService(settings).record_failure(
                        actor, "read", credential_error("invalid_input")
                    )
                except ServiceError as exc:
                    return error_response(request, exc)
            return error_response(
                request, ServiceError("invalid_input", "Invalid credential input", 422, 2)
            )
        # Name the offending fields but never echo submitted values.
        fields = sorted(
            {
                ".".join(str(part) for part in item["loc"][1:]) or str(item["loc"][0])
                for item in error.errors()
                if item.get("loc")
            }
        )
        message = "Invalid or missing request parameters"
        if fields:
            message += ": " + ", ".join(fields[:10])
        return error_response(request, ServiceError("invalid_input", message, 422, 2))

    @app.exception_handler(IntegrityError)
    async def integrity_error(request: Request, error: IntegrityError):
        return error_response(
            request, ServiceError("conflict", "Input conflicts with existing records", 409, 2)
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return error_response(
            request,
            ServiceError(
                "http_error",
                "Request is unavailable",
                error.status_code,
                4 if error.status_code == 404 else 2,
            ),
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception):
        # Starlette re-raises after this response, so the server log keeps the traceback;
        # the client only learns that the request failed, never the exception text.
        return error_response(
            request, ServiceError("internal_error", "Unexpected server error", 500, 4)
        )

    app.include_router(
        create_platform_router(settings, db, crypto, password_attempts, llm_transport, processor)
    )
    if settings.web_dir is not None:
        mount_console(app, settings.web_dir)

    bearer = HTTPBearer(auto_error=False)

    async def context(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
        x_org_id: UUID = Header(...),
    ):
        if credentials is None:
            raise ServiceError("invalid_session", "Bearer credentials required", 401, 4)
        # Candidate scope is used only for the membership check; handlers receive a
        # tenant transaction only after identity and active membership are verified.
        async with db.transaction(x_org_id) as session:
            identity = await authenticate(session, credentials.credentials, x_org_id, crypto)
            await set_actor_context(session, identity)
            session.info["memory_settings"] = settings
            yield session, identity

    from app.api.budgets import create_router as create_budget_router

    app.include_router(create_budget_router(context, settings))
    app.include_router(create_org_console_router(context, storage, settings))
    app.include_router(create_task_workflow_router(context, settings))
    task_board_router = create_task_board_router(context, db, storage, queue, settings)
    app.state.workflow_stream_caps = task_board_router.stream_caps
    app.include_router(task_board_router)
    app.include_router(create_response_router(context, db, storage, queue, settings, llm, resolve))
    app.include_router(
        create_provider_router(context, db, settings, llm, resolve, processor, llm_transport)
    )
    app.include_router(create_export_router(context, db, storage, queue, settings, crypto))
    app.include_router(create_sandbox_router(context, db, storage, queue, crypto, processor))
    from app.api.screenshots import create_router as create_screenshot_router

    app.include_router(
        create_screenshot_router(context, db, storage, queue, settings, llm, resolve, processor)
    )

    app.include_router(
        create_account_router(context, settings, db, crypto, password_attempts, llm, resolve)
    )
    app.include_router(
        create_tender_router(context, settings, db, storage, queue, crypto, llm, resolve, ocr)
    )
    app.include_router(create_resource_router(context, settings, storage, crypto))
    app.include_router(create_confidential_router(context, settings))
    app.include_router(create_check_router(context, db, storage, queue, settings))
    app.include_router(create_score_router(context, db, storage, queue, settings))
    from app.api.memory import create_router as create_memory_router

    app.include_router(create_memory_router(context, db, queue, settings, storage))
    app.include_router(create_job_router(context, storage))
    return app
