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
from app.api.exports import create_router as create_export_router
from app.api.jobs import create_router as create_job_router
from app.api.org_console import create_router as create_org_console_router
from app.api.platform import create_router as create_platform_router
from app.api.providers import create_router as create_provider_router
from app.api.resources import create_router as create_resource_router
from app.api.response_cards import create_router as create_response_router
from app.api.sandbox import create_router as create_sandbox_router
from app.api.tenders import create_router as create_tender_router
from app.core.config import Settings
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
        await billing.verify_currency(db, settings.billing_currency)
        try:
            yield
        finally:
            await password_attempts.close()
            await db.engine.dispose()

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
        if (
            request.method == "POST"
            and len(parts) == 4
            and parts[1] == "tasks"
            and parts[3] == "evidence-sources"
        ):
            content = bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > 128 * 1024:
                    return error_response(
                        request,
                        ServiceError("input_too_large", "Source input exceeds JSON limit", 413, 2),
                    )
            # Starlette's wrapped request replays its cached body to FastAPI.
            request._body = bytes(content)
        return await call_next(request)

    @app.middleware("http")
    async def contract_header(request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        response.headers["X-Bid-Contract-Version"] = CONTRACT_VERSION
        response.headers["X-Duration-Ms"] = str(int((time.monotonic() - start) * 1000))
        response.headers["Cache-Control"] = "no-store"
        if response.headers.get("content-type", "").startswith("application/json"):
            raw = b"".join([part async for part in response.body_iterator])
            payload = json.loads(raw)
            if isinstance(payload, dict) and "command" in payload and "duration_ms" in payload:
                payload["duration_ms"] = int((time.monotonic() - start) * 1000)
                raw = json.dumps(payload, ensure_ascii=False).encode()
            headers = {
                key: value for key, value in response.headers.items() if key != "content-length"
            }
            response = Response(raw, status_code=response.status_code, headers=headers)
        return response

    def error_response(request: Request, error: ServiceError):
        command = request.scope.get("route")
        name = command.name.replace("_", " ") if command else "request"
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
        retry = {"auth_busy": "1", "too_many_attempts": "900"}.get(error.code)
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
        create_platform_router(settings, db, crypto, password_attempts, llm_transport)
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
            yield session, identity

    app.include_router(create_org_console_router(context))
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
    app.include_router(create_job_router(context, storage))
    return app
