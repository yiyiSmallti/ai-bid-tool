import asyncio
import hashlib
import json
import secrets
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from app.api.platform import create_router as create_platform_router
from app.api.providers import create_router as create_provider_router
from app.api.response_cards import create_router as create_response_router
from app.core.config import Settings
from app.core.db import Database
from app.core.errors import ServiceError, not_found
from app.core.password_attempts import PasswordAttempts
from app.core.security import Secrets, token_digest
from app.jobs.processor import Processor
from app.jobs.queue import Queue
from app.models.entities import ApiToken, Chunk, Document, Job, Requirement, Task
from app.providers.configured import model_identity
from app.providers.disabled import DisabledLLM
from app.providers.llm import (
    billable,
    reasoning_choices,
    resolve_llm,
    with_reasoning,
)
from app.providers.local_ocr import LocalOCR
from app.providers.storage import create_storage
from app.schemas.certificate_contracts import (
    CertificateCreate,
    CertificateUpdate,
    TaskCertificateSelection,
)
from app.schemas.certificate_file_contracts import CertificateFileCreate
from app.schemas.citation_repair_contracts import CitationRepairRequest
from app.schemas.contracts import (
    CONTRACT_VERSION,
    JobAction,
    Login,
    Result,
    TaskCreate,
    TokenCreate,
)
from app.schemas.evidence_source_contracts import EvidenceSourceCreate
from app.schemas.feature_contracts import FeatureCreate, FeatureUpdate, TaskFeatureSelection
from app.schemas.platform_contracts import CardRedeem
from app.schemas.profile_contracts import (
    OrgProfileCreate,
    OrgProfileUpdate,
    TaskOrgProfileSelection,
)
from app.schemas.resource_contracts import ProductCreate, ProductUpdate, TaskProductSelection
from app.schemas.template_contracts import TaskTemplateSelection, TemplateCreate, TemplateUpdate
from app.services import (
    billing,
    certificate_files,
    certificates,
    citation_repair,
    evidence_sources,
    features,
    profiles,
    resources,
    templates,
)
from app.services.auth import SCOPES, authenticate, login, set_actor_context
from app.services.extraction import EXTRACTION_VERSION, PROMPT_VERSION
from app.services.parsing import PARSER_VERSION, validate_document
from app.services.template_files import MAX_TEMPLATE_BYTES, validate_template
from app.services.template_files import WARNINGS as TEMPLATE_WARNINGS


def serial(row, fields: tuple[str, ...]) -> dict:
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder({field: getattr(row, field) for field in fields})


def result(command: str, data=None, items=None, warnings=None) -> dict:
    return Result(
        ok=True, command=command, data=data or {}, items=items or [], warnings=warnings or []
    ).model_dump(mode="json")


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
    db, crypto = Database(settings), Secrets(settings.encryption_key.get_secret_value())
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

    app.include_router(create_response_router(context, db, storage, queue, settings, llm, resolve))
    app.include_router(
        create_provider_router(context, db, settings, llm, resolve, processor, llm_transport)
    )

    @app.get("/health", name="health", response_model=Result)
    async def health():
        active = llm
        if resolve is not None:
            async with db.transaction() as session:
                active = await resolve(session)
        configured = not active.test_only and active.name != "unconfigured"
        return result(
            "health",
            {"status": "ok", "version": CONTRACT_VERSION, "real_llm_configured": configured},
        )

    @app.post("/auth/login", name="login", response_model=Result)
    async def auth_login(body: Login, request: Request):
        user = await login(
            password_attempts,
            body.email,
            body.password,
            body.org_id,
            request.client.host if request.client else None,
        )
        token = crypto.issue({"kind": "session", "user_id": str(user.id)}, settings.session_seconds)
        return result(
            "login",
            {
                "session": token,
                "org_id": str(body.org_id),
                "expires_in": settings.session_seconds,
            },
        )

    @app.get("/org/current", name="org_use", response_model=Result)
    async def org_use(ctx=Depends(context, scope="function")):
        _, identity = ctx
        return result("org use", {"org_id": str(identity.org_id), "role": identity.role})

    @app.post("/tasks", name="task_create", response_model=Result)
    async def task_create(body: TaskCreate, ctx=Depends(context, scope="function")):
        session, identity = ctx
        identity.require("task:create")
        task = Task(org_id=identity.org_id, created_by=identity.user_id, **body.model_dump())
        session.add(task)
        await session.flush()
        return result(
            "task create",
            serial(
                task,
                ("id", "name", "org_id", "model_redaction_enabled", "model_redaction_revision"),
            ),
        )

    @app.get("/tasks", name="task_list", response_model=Result)
    async def task_list(ctx=Depends(context, scope="function")):
        session, identity = ctx
        identity.require("task:read")
        tasks = (await session.scalars(select(Task).order_by(Task.created_at))).all()
        return result(
            "task list",
            items=[
                serial(
                    task,
                    ("id", "name", "org_id", "model_redaction_enabled", "model_redaction_revision"),
                )
                for task in tasks
            ],
        )

    @app.post("/resources/products", name="resource_product_add", response_model=Result)
    async def product_add(body: ProductCreate, ctx=Depends(context, scope="function")):
        session, actor = ctx
        return result("resource product add", await resources.create_product(session, actor, body))

    @app.get("/resources/products", name="resource_product_list", response_model=Result)
    async def product_list(
        history: bool = False,
        product_id: UUID | None = None,
        ctx=Depends(context, scope="function"),
    ):
        session, actor = ctx
        data, items = await resources.list_products(
            session, actor, history=history, product_id=product_id
        )
        return result("resource product list", data, items)

    @app.post(
        "/resources/products/{product_id}/revisions",
        name="resource_product_update",
        response_model=Result,
    )
    async def product_update(
        product_id: UUID, body: ProductUpdate, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        return result(
            "resource product update",
            await resources.update_product(session, actor, product_id, body),
        )

    @app.post("/tasks/{task_id}/products", name="task_resource_add", response_model=Result)
    async def task_product_add(
        task_id: UUID, body: TaskProductSelection, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        return result(
            "task resource add", await resources.select_product(session, actor, task_id, body)
        )

    @app.get("/tasks/{task_id}/products", name="task_resource_list", response_model=Result)
    async def task_product_list(
        task_id: UUID, history: bool = False, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        data, items = await resources.list_selections(session, actor, task_id, history=history)
        return result("task resource list", data, items)

    feature_warnings = ["Implementation status is a declaration; evidence has not been verified"]

    @app.post("/resources/features", name="resource_feature_add", response_model=Result)
    async def feature_add(body: FeatureCreate, ctx=Depends(context, scope="function")):
        session, actor = ctx
        return result(
            "resource feature add",
            await features.create_feature(session, actor, body),
            warnings=feature_warnings,
        )

    @app.get("/resources/features", name="resource_feature_list", response_model=Result)
    async def feature_list(
        history: bool = False,
        feature_id: UUID | None = None,
        ctx=Depends(context, scope="function"),
    ):
        session, actor = ctx
        data, items = await features.list_features(
            session, actor, history=history, feature_id=feature_id
        )
        return result("resource feature list", data, items, feature_warnings)

    @app.post(
        "/resources/features/{feature_id}/revisions",
        name="resource_feature_update",
        response_model=Result,
    )
    async def feature_update(
        feature_id: UUID, body: FeatureUpdate, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        return result(
            "resource feature update",
            await features.update_feature(session, actor, feature_id, body),
            warnings=feature_warnings,
        )

    @app.post("/tasks/{task_id}/features", name="task_feature_add", response_model=Result)
    async def task_feature_add(
        task_id: UUID, body: TaskFeatureSelection, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        return result(
            "task feature add",
            await features.select_feature(session, actor, task_id, body),
            warnings=feature_warnings,
        )

    @app.get("/tasks/{task_id}/features", name="task_feature_list", response_model=Result)
    async def task_feature_list(
        task_id: UUID, history: bool = False, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        data, items = await features.list_selections(session, actor, task_id, history=history)
        return result("task feature list", data, items, feature_warnings)

    certificate_warnings = [
        "Certificate metadata and dates are declarations; authenticity, legality and compliance have not been verified"
    ]

    @app.post("/resources/certificates", name="resource_certificate_add", response_model=Result)
    async def certificate_add(body: CertificateCreate, ctx=Depends(context, scope="function")):
        session, actor = ctx
        return result(
            "resource certificate add",
            await certificates.create_certificate(session, actor, body),
            warnings=certificate_warnings,
        )

    @app.get("/resources/certificates", name="resource_certificate_list", response_model=Result)
    async def certificate_list(
        history: bool = False,
        certificate_id: UUID | None = None,
        as_of: date | None = None,
        ctx=Depends(context, scope="function"),
    ):
        session, actor = ctx
        data, items = await certificates.list_certificates(
            session, actor, history=history, certificate_id=certificate_id, as_of=as_of
        )
        return result("resource certificate list", data, items, certificate_warnings)

    @app.post(
        "/resources/certificates/{certificate_id}/revisions",
        name="resource_certificate_update",
        response_model=Result,
    )
    async def certificate_update(
        certificate_id: UUID, body: CertificateUpdate, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        return result(
            "resource certificate update",
            await certificates.update_certificate(session, actor, certificate_id, body),
            warnings=certificate_warnings,
        )

    @app.post("/tasks/{task_id}/certificates", name="task_certificate_add", response_model=Result)
    async def task_certificate_add(
        task_id: UUID, body: TaskCertificateSelection, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        return result(
            "task certificate add",
            await certificates.select_certificate(session, actor, task_id, body),
            warnings=certificate_warnings,
        )

    @app.get("/tasks/{task_id}/certificates", name="task_certificate_list", response_model=Result)
    async def task_certificate_list(
        task_id: UUID,
        history: bool = False,
        as_of: date | None = None,
        ctx=Depends(context, scope="function"),
    ):
        session, actor = ctx
        data, items = await certificates.list_selections(
            session, actor, task_id, history=history, as_of=as_of
        )
        return result("task certificate list", data, items, certificate_warnings)

    profile_warnings = [
        "Company metadata is a declaration; authenticity, performance and qualification have not been verified"
    ]

    @app.post("/resources/profiles", name="resource_profile_add", response_model=Result)
    async def profile_add(body: OrgProfileCreate, ctx=Depends(context, scope="function")):
        session, actor = ctx
        return result(
            "resource profile add",
            await profiles.create_profile(session, actor, body),
            warnings=profile_warnings,
        )

    @app.get("/resources/profiles", name="resource_profile_list", response_model=Result)
    async def profile_list(
        history: bool = False,
        profile_id: UUID | None = None,
        ctx=Depends(context, scope="function"),
    ):
        session, actor = ctx
        data, items = await profiles.list_profiles(
            session, actor, history=history, profile_id=profile_id
        )
        return result("resource profile list", data, items, profile_warnings)

    @app.post(
        "/resources/profiles/{profile_id}/revisions",
        name="resource_profile_update",
        response_model=Result,
    )
    async def profile_update(
        profile_id: UUID, body: OrgProfileUpdate, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        return result(
            "resource profile update",
            await profiles.update_profile(session, actor, profile_id, body),
            warnings=profile_warnings,
        )

    @app.post("/tasks/{task_id}/profiles", name="task_profile_add", response_model=Result)
    async def task_profile_add(
        task_id: UUID, body: TaskOrgProfileSelection, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        return result(
            "task profile add",
            await profiles.select_profile(session, actor, task_id, body),
            warnings=profile_warnings,
        )

    @app.get("/tasks/{task_id}/profiles", name="task_profile_list", response_model=Result)
    async def task_profile_list(
        task_id: UUID, history: bool = False, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        data, items = await profiles.list_selections(session, actor, task_id, history=history)
        return result("task profile list", data, items, profile_warnings)

    @app.post(
        "/resources/certificates/{certificate_id}/file-revisions",
        name="resource_certificate_file_add",
        response_model=Result,
    )
    async def certificate_file_add(
        certificate_id: UUID,
        metadata: str = Form(...),
        file: UploadFile = File(...),
        ctx=Depends(context, scope="function"),
    ):
        session, actor = ctx
        actor.require("certificate:write")
        actor.require("certificate:file:write")
        if len(metadata.encode("utf-8")) > 128 * 1024:
            raise ServiceError("invalid_input", "Certificate metadata exceeds input limit", 413, 2)
        try:
            body = CertificateFileCreate.model_validate_json(metadata)
        except (ValueError, ValidationError):
            raise ServiceError(
                "invalid_input", "Invalid certificate file metadata", 422, 2
            ) from None
        limit = min(settings.max_upload_bytes, certificate_files.MAX_FILE_BYTES)
        content = await file.read(limit + 1)
        if len(content) > limit:
            raise ServiceError("file_too_large", "File exceeds upload limit", 413, 2)
        descriptor = await asyncio.to_thread(
            certificate_files.validate_file, content, file.filename or ""
        )
        return result(
            "resource certificate file add",
            await certificate_files.create_file(
                session, actor, certificate_id, body, descriptor, content, storage
            ),
            warnings=certificate_files.WARNINGS,
        )

    @app.get(
        "/resources/certificates/files",
        name="resource_certificate_file_list",
        response_model=Result,
    )
    async def certificate_file_list(
        certificate_id: UUID | None = None,
        revision_id: UUID | None = None,
        history: bool = False,
        ctx=Depends(context, scope="function"),
    ):
        session, actor = ctx
        data, items, warnings = await certificate_files.list_files(
            session, actor, certificate_id=certificate_id, revision_id=revision_id, history=history
        )
        return result("resource certificate file list", data, items, warnings)

    @app.get(
        "/tasks/{task_id}/certificate-files",
        name="task_certificate_file_list",
        response_model=Result,
    )
    async def task_certificate_file_list(
        task_id: UUID, history: bool = False, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        data, items, warnings = await certificate_files.list_task_files(
            session, actor, task_id, history=history
        )
        return result("task certificate file list", data, items, warnings)

    @app.get(
        "/resources/certificates/revisions/{revision_id}/file/download-link",
        name="resource_certificate_file_download_link",
        response_model=Result,
    )
    async def certificate_file_download_link(
        revision_id: UUID, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        await certificate_files.require_file(session, actor, revision_id)
        signed = crypto.issue(
            {
                "kind": "certificate-file-download",
                "org_id": str(actor.org_id),
                "revision_id": str(revision_id),
            },
            300,
        )
        return result(
            "resource certificate file download link",
            {
                "url": f"/resources/certificates/revisions/{revision_id}/file/download?signature={signed}",
                "expires_in": 300,
            },
            warnings=certificate_files.WARNINGS,
        )

    @app.get(
        "/resources/certificates/revisions/{revision_id}/file/download",
        name="resource_certificate_file_download",
    )
    async def certificate_file_download(
        revision_id: UUID, signature: str, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        await certificate_files.require_file(session, actor, revision_id)
        try:
            payload = crypto.open(signature)
        except ServiceError as exc:
            raise not_found() from exc
        if (
            payload.get("kind") != "certificate-file-download"
            or payload.get("org_id") != str(actor.org_id)
            or payload.get("revision_id") != str(revision_id)
        ):
            raise not_found()
        content, descriptor = await certificate_files.read_revision(
            session, actor, revision_id, storage
        )
        return Response(
            content,
            media_type=descriptor.media_type,
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(descriptor.name, safe="")
            },
        )

    async def template_upload(metadata, file, model):
        if len(metadata.encode("utf-8")) > 128 * 1024:
            raise ServiceError("invalid_input", "Template metadata exceeds input limit", 413, 2)
        try:
            body = model.model_validate_json(metadata)
        except (ValueError, ValidationError):
            raise ServiceError("invalid_input", "Invalid template metadata", 422, 2) from None
        limit = min(settings.max_upload_bytes, MAX_TEMPLATE_BYTES)
        content = await file.read(limit + 1)
        if len(content) > limit:
            raise ServiceError("file_too_large", "File exceeds upload limit", 413, 2)
        descriptor = await asyncio.to_thread(validate_template, content, file.filename or "")
        return body, descriptor, content

    @app.post("/resources/templates", name="resource_template_add", response_model=Result)
    async def template_add(
        metadata: str = Form(...),
        file: UploadFile = File(...),
        ctx=Depends(context, scope="function"),
    ):
        session, actor = ctx
        actor.require("template:write")
        body, descriptor, content = await template_upload(metadata, file, TemplateCreate)
        return result(
            "resource template add",
            await templates.create_template(session, actor, body, descriptor, content, storage),
            warnings=TEMPLATE_WARNINGS,
        )

    @app.get("/resources/templates", name="resource_template_list", response_model=Result)
    async def template_list(
        history: bool = False,
        template_id: UUID | None = None,
        ctx=Depends(context, scope="function"),
    ):
        session, actor = ctx
        data, items = await templates.list_templates(
            session, actor, history=history, template_id=template_id
        )
        return result("resource template list", data, items, TEMPLATE_WARNINGS)

    @app.post(
        "/resources/templates/{template_id}/revisions",
        name="resource_template_update",
        response_model=Result,
    )
    async def template_update(
        template_id: UUID,
        metadata: str = Form(...),
        file: UploadFile = File(...),
        ctx=Depends(context, scope="function"),
    ):
        session, actor = ctx
        actor.require("template:write")
        body, descriptor, content = await template_upload(metadata, file, TemplateUpdate)
        return result(
            "resource template update",
            await templates.update_template(
                session, actor, template_id, body, descriptor, content, storage
            ),
            warnings=TEMPLATE_WARNINGS,
        )

    @app.post("/tasks/{task_id}/templates", name="task_template_add", response_model=Result)
    async def task_template_add(
        task_id: UUID, body: TaskTemplateSelection, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        return result(
            "task template add",
            await templates.select_template(session, actor, task_id, body),
            warnings=TEMPLATE_WARNINGS,
        )

    @app.get("/tasks/{task_id}/templates", name="task_template_list", response_model=Result)
    async def task_template_list(
        task_id: UUID, history: bool = False, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        data, items = await templates.list_selections(session, actor, task_id, history=history)
        return result("task template list", data, items, TEMPLATE_WARNINGS)

    @app.get(
        "/resources/templates/revisions/{revision_id}/download-link",
        name="resource_template_download_link",
        response_model=Result,
    )
    async def template_download_link(revision_id: UUID, ctx=Depends(context, scope="function")):
        session, actor = ctx
        await templates.require_revision(session, actor, revision_id)
        signed = crypto.issue(
            {
                "kind": "template-download",
                "org_id": str(actor.org_id),
                "revision_id": str(revision_id),
            },
            300,
        )
        return result(
            "resource template download link",
            {
                "url": f"/resources/templates/revisions/{revision_id}/download?signature={signed}",
                "expires_in": 300,
            },
            warnings=TEMPLATE_WARNINGS,
        )

    @app.get(
        "/resources/templates/revisions/{revision_id}/download", name="resource_template_download"
    )
    async def template_download(
        revision_id: UUID, signature: str, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        await templates.require_revision(session, actor, revision_id)
        try:
            payload = crypto.open(signature)
        except ServiceError as exc:
            raise not_found() from exc
        if (
            payload.get("kind") != "template-download"
            or payload.get("org_id") != str(actor.org_id)
            or payload.get("revision_id") != str(revision_id)
        ):
            raise not_found()
        content, descriptor = await templates.read_revision(session, actor, revision_id, storage)
        return Response(
            content,
            media_type=descriptor.media_type,
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(descriptor.name, safe="")
            },
        )

    @app.post(
        "/tasks/{task_id}/evidence-sources", name="evidence_source_add", response_model=Result
    )
    async def evidence_source_add(
        task_id: UUID, body: EvidenceSourceCreate, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        started = time.monotonic()
        value = result(
            "evidence source add",
            await evidence_sources.create_source(
                session, actor, task_id, body, storage, settings.max_upload_bytes
            ),
            warnings=evidence_sources.WARNINGS,
        )
        value["duration_ms"] = round((time.monotonic() - started) * 1000)
        return value

    @app.get(
        "/tasks/{task_id}/evidence-sources", name="evidence_source_list", response_model=Result
    )
    async def evidence_source_list(
        task_id: UUID, history: bool = False, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        data, items, warnings = await evidence_sources.list_sources(
            session, actor, task_id, history=history
        )
        return result("evidence source list", data, items, warnings)

    @app.get(
        "/evidence-sources/{source_id}/preview/download-link",
        name="evidence_source_preview_link",
        response_model=Result,
    )
    async def evidence_source_preview_link(source_id: UUID, ctx=Depends(context, scope="function")):
        session, actor = ctx
        row, snapshot, original = await evidence_sources.require_source(session, actor, source_id)
        signed = crypto.issue(
            {"kind": "source-preview", "org_id": str(actor.org_id), "source_id": str(source_id)},
            300,
        )
        return result(
            "evidence source preview link",
            {
                "url": f"/evidence-sources/{source_id}/preview/download?signature={signed}",
                "expires_in": 300,
            },
            [evidence_sources.source_data(row, snapshot, original)],
            evidence_sources.WARNINGS,
        )

    @app.get(
        "/evidence-sources/{source_id}/preview/download",
        name="evidence_source_preview_download",
        response_class=Response,
        responses={
            200: {"content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}}
        },
    )
    async def evidence_source_preview_download(
        source_id: UUID, signature: str, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        await evidence_sources.require_source(session, actor, source_id)
        try:
            payload = crypto.open(signature)
        except ServiceError as exc:
            raise not_found() from exc
        if (
            payload.get("kind") != "source-preview"
            or payload.get("org_id") != str(actor.org_id)
            or payload.get("source_id") != str(source_id)
        ):
            raise not_found()
        content, descriptor = await evidence_sources.read_preview(
            session, actor, source_id, storage
        )
        return Response(
            content,
            media_type=descriptor.media_type,
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(descriptor.name, safe="")
            },
        )

    async def require_document(session, document_id):
        document = await session.get(Document, document_id)
        if document is None:
            raise not_found()
        return document

    @app.post("/tasks/{task_id}/documents", name="tender_upload", response_model=Result)
    async def tender_upload(
        task_id: UUID, file: UploadFile = File(...), ctx=Depends(context, scope="function")
    ):
        session, identity = ctx
        identity.require("tender:upload")
        if await session.get(Task, task_id) is None:
            raise not_found()
        content = await file.read(settings.max_upload_bytes + 1)
        if len(content) > settings.max_upload_bytes:
            raise ServiceError("file_too_large", "File exceeds upload limit", 413, 2)
        name = Path(file.filename or "").name
        if not name or len(name) > 200:
            raise ServiceError("invalid_filename", "Valid file name required", 400, 2)
        suffix = Path(name).suffix.lower()
        await asyncio.to_thread(validate_document, content, suffix, settings.max_pages)
        digest = hashlib.sha256(content).hexdigest()
        key = f"org/{identity.org_id}/task/{task_id}/{digest}{suffix}"
        await storage.put(identity.org_id, key, content)
        identifier = await session.scalar(
            insert(Document)
            .values(
                org_id=identity.org_id,
                task_id=task_id,
                name=name,
                sha256=digest,
                storage_key=key,
                media_type="application/pdf"
                if suffix == ".pdf"
                else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            .on_conflict_do_nothing(index_elements=["org_id", "task_id", "sha256"])
            .returning(Document.id)
        )
        document = await session.scalar(
            select(Document).where(Document.task_id == task_id, Document.sha256 == digest)
        )
        return result(
            "tender upload",
            {
                **serial(document, ("id", "name", "sha256", "task_id")),
                "duplicate": identifier is None,
            },
        )

    @app.get("/documents/{document_id}", name="document_get", response_model=Result)
    async def document_get(document_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        identity.require("task:read")
        document = await require_document(session, document_id)
        return result(
            "document get",
            serial(document, ("id", "name", "task_id", "status", "page_count", "citation_mode")),
        )

    @app.get(
        "/documents/{document_id}/download-link",
        name="document_download_link",
        response_model=Result,
    )
    async def download_link(document_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        identity.require("task:read")
        await require_document(session, document_id)
        signed = crypto.issue(
            {"kind": "download", "org_id": str(identity.org_id), "document_id": str(document_id)},
            300,
        )
        return result(
            "document download link",
            {"url": f"/documents/{document_id}/download?signature={signed}", "expires_in": 300},
        )

    @app.get("/documents/{document_id}/download", name="document_download")
    async def download(document_id: UUID, signature: str, ctx=Depends(context, scope="function")):
        session, identity = ctx
        identity.require("task:read")
        document = await require_document(session, document_id)
        try:
            payload = crypto.open(signature)
        except ServiceError as exc:
            raise not_found() from exc
        if (
            payload.get("kind") != "download"
            or payload.get("org_id") != str(identity.org_id)
            or payload.get("document_id") != str(document_id)
        ):
            raise not_found()
        return Response(
            await storage.read(identity.org_id, document.storage_key),
            media_type=document.media_type,
        )

    async def start_job(document_id: UUID, kind: str, body: JobAction, ctx):
        session, identity = ctx
        identity.require("tender:parse" if kind == "parse" else "req:extract")
        document = await require_document(session, document_id)
        count = await session.scalar(
            select(Chunk.id).where(Chunk.document_id == document_id).limit(1)
        )
        command = "tender parse" if kind == "parse" else "req extract"
        if kind == "parse" and body.reasoning is not None:
            raise ServiceError("invalid_input", "Reasoning levels apply only to extraction", 400, 2)
        model = await resolve(session) if resolve is not None and kind == "extract" else llm
        level, level_warnings = None, []
        if kind == "extract":
            model, level, level_warnings = with_reasoning(model, body.reasoning)
        if body.dry_run:
            data: dict[str, Any] = {
                "dry_run": True,
                "document_id": str(document_id),
                "parsed": count is not None,
                "estimated_cost_usd": None,
            }
            warnings = []
            if kind == "extract":
                data |= {"reasoning": level, "reasoning_levels": reasoning_choices(model)}
                warnings = [
                    *level_warnings,
                    "Cost estimate is unavailable without an approved provider.",
                ]
            return result(command, data, warnings=warnings)
        if kind == "extract" and document.status != "parsed":
            raise ServiceError("not_parsed", "Parse the document before extraction", 400, 2)
        # A missing credential fails as provider_unavailable, not as a balance problem.
        if kind == "extract" and billable(model):
            await billing.require_funds(session, settings.billing_currency)
        version = (
            f"{PARSER_VERSION}:{ocr.name}:{ocr.version}:{settings.ocr_language}"
            if kind == "parse"
            else f"{PROMPT_VERSION}:{EXTRACTION_VERSION}:{model.name}:{model.model}:{model.version}"
        )
        if level is not None:
            # Each level is its own job; repeating the same level returns the same job.
            version += f":reasoning={level}"
        cache_key = hashlib.sha256(
            f"{document_id}:{document.sha256}:{kind}:{version}".encode()
        ).hexdigest()
        identifier = await session.scalar(
            insert(Job)
            .values(
                org_id=identity.org_id,
                task_id=document.task_id,
                document_id=document_id,
                kind=kind,
                cache_key=cache_key,
                reasoning=level,
                provider_config_id=getattr(model, "provider_config_id", None)
                if kind == "extract"
                else None,
                provider_identity=model_identity(model) if kind == "extract" else None,
            )
            .on_conflict_do_nothing(index_elements=["org_id", "cache_key"])
            .returning(Job.id)
        )
        job = await session.scalar(select(Job).where(Job.cache_key == cache_key).with_for_update())
        if body.retry and (
            job.status in {"failed", "cancelled"}
            or (job.status == "running" and job.lease_until and job.lease_until < datetime.now(UTC))
        ):
            job.status, job.error, job.result, job.queue_id = "queued", None, {}, None
            job.attempts, job.lease_until, job.finished_at, job.run_id = 0, None, None, None
        if job.status in {"failed", "cancelled"}:
            return result(
                command,
                {
                    "job_id": str(job.id),
                    "status": job.status,
                    "cached": True,
                    "reasoning": job.reasoning,
                },
                warnings=[
                    *level_warnings,
                    "This identical job is terminal. Inspect its status; use --retry explicitly to run it again.",
                ],
            )
        if job.status == "queued" and job.queue_id is None:
            # Commit the durable job before making it visible to the independent queue.
            await session.commit()
            try:
                queue_id = await queue.enqueue(str(identity.org_id), str(job.id))
            except Exception as exc:
                raise ServiceError(
                    "queue_unavailable",
                    "Job is saved but queue is unavailable; repeat the request to schedule it",
                    503,
                    3,
                ) from exc
            async with db.transaction(identity.org_id) as update:
                saved = await update.get(Job, job.id)
                if saved is not None:
                    saved.queue_id = queue_id
        return result(
            command,
            {
                "job_id": str(job.id),
                "status": job.status,
                "cached": identifier is None,
                "reasoning": job.reasoning,
            },
            warnings=level_warnings,
        )

    @app.post("/documents/{document_id}/parse", name="tender_parse", response_model=Result)
    async def tender_parse(
        document_id: UUID, body: JobAction, ctx=Depends(context, scope="function")
    ):
        return await start_job(document_id, "parse", body, ctx)

    @app.post("/documents/{document_id}/extract", name="req_extract", response_model=Result)
    async def req_extract(
        document_id: UUID, body: JobAction, ctx=Depends(context, scope="function")
    ):
        return await start_job(document_id, "extract", body, ctx)

    @app.get("/documents/{document_id}/chunks", name="chunk_list", response_model=Result)
    async def chunk_list(document_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        identity.require("task:read")
        await require_document(session, document_id)
        chunks = (
            await session.scalars(
                select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.seq)
            )
        ).all()
        return result(
            "chunk list",
            items=[
                serial(
                    row,
                    (
                        "id",
                        "document_id",
                        "seq",
                        "page",
                        "text",
                        "ocr",
                        "citation_verified",
                        "blocks",
                    ),
                )
                for row in chunks
            ],
        )

    def latest_extractions(task_id: UUID):
        # The most recent succeeded extraction of each document in the task.
        return (
            select(Job.id)
            .where(Job.task_id == task_id, Job.kind == "extract", Job.status == "succeeded")
            .order_by(Job.document_id, Job.finished_at.desc().nulls_last(), Job.created_at.desc())
            .distinct(Job.document_id)
        )

    @app.get("/tasks/{task_id}/requirements", name="req_list", response_model=Result)
    async def req_list(
        task_id: UUID, job: UUID | None = None, ctx=Depends(context, scope="function")
    ):
        session, identity = ctx
        identity.require("task:read")
        if await session.get(Task, task_id) is None:
            raise not_found()
        if job is not None:
            chosen = await session.get(Job, job)
            if chosen is None or chosen.task_id != task_id or chosen.kind != "extract":
                raise not_found()
            jobs = select(Job.id).where(Job.id == job)
        else:
            jobs = latest_extractions(task_id)
        pairs = (
            await session.execute(
                select(Requirement, Chunk, Job.reasoning)
                .join(
                    Chunk, (Chunk.org_id == Requirement.org_id) & (Chunk.id == Requirement.chunk_id)
                )
                .join(Job, (Job.org_id == Requirement.org_id) & (Job.id == Requirement.job_id))
                .where(Requirement.task_id == task_id, Requirement.job_id.in_(jobs))
            )
        ).all()

        def reading_order(pair):
            # Source order: chunk sequence, then the cited block's position inside the chunk.
            requirement, chunk, _ = pair
            block_ids = [block["block_id"] for block in chunk.blocks or []]
            block_id = (requirement.location or {}).get("block_id")
            position = block_ids.index(block_id) if block_id in block_ids else 0
            return (
                str(requirement.document_id),
                chunk.seq,
                position,
                requirement.created_at,
                str(requirement.id),
            )

        items = [
            {
                **serial(
                    row, ("id", "text", "category", "starred", "condition", "job_id", "model_quote")
                ),
                "reasoning": reasoning,
                "source": serial(row, ("document_id", "chunk_id", "page", "location", "quote")),
            }
            for row, _, reasoning in sorted(pairs, key=reading_order)
        ]
        return result("req list", items=items)

    @app.get(
        "/tasks/{task_id}/requirements/repair", name="req_repair_preview", response_model=Result
    )
    async def req_repair_preview(task_id: UUID, job: UUID, ctx=Depends(context, scope="function")):
        data, items = await citation_repair.repair_citations(ctx[0], ctx[1], task_id, job)
        return result("req repair-citations", data=data, items=items)

    @app.post(
        "/tasks/{task_id}/requirements/repair", name="req_repair_execute", response_model=Result
    )
    async def req_repair_execute(
        task_id: UUID, body: CitationRepairRequest, ctx=Depends(context, scope="function")
    ):
        data, items = await citation_repair.repair_citations(
            ctx[0], ctx[1], task_id, body.extraction_job_id, body
        )
        return result("req repair-citations", data=data, items=items)

    @app.get("/tasks/{task_id}/extractions", name="req_history", response_model=Result)
    async def req_history(
        task_id: UUID, document: UUID | None = None, ctx=Depends(context, scope="function")
    ):
        session, identity = ctx
        identity.require("task:read")
        if await session.get(Task, task_id) is None:
            raise not_found()
        query = select(Job).where(Job.task_id == task_id, Job.kind == "extract")
        if document is not None:
            query = query.where(Job.document_id == document)
        jobs = (await session.scalars(query.order_by(Job.created_at.desc(), Job.id))).all()
        latest = set((await session.scalars(latest_extractions(task_id))).all())
        items = []
        for job in jobs:
            outcome = job.result or {}
            items.append(
                {
                    "job_id": str(job.id),
                    "document_id": str(job.document_id),
                    "reasoning": job.reasoning,
                    "model": outcome.get("model"),
                    "status": job.status,
                    "created_at": job.created_at.isoformat(),
                    "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                    "saved": outcome.get("created"),
                    "rejected": len(outcome["rejected"]) if "rejected" in outcome else None,
                    "tokens": (outcome.get("cost") or {}).get("llm_tokens"),
                    "error": job.error,
                    # Shown by default in req list.
                    "latest": job.id in latest,
                }
            )
        return result("req history", items=items)

    @app.get("/jobs/{job_id}", name="job_status", response_model=Result)
    async def job_status(job_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        identity.require("job:read")
        job = await session.get(Job, job_id)
        if job is None:
            raise not_found()
        payload = result(
            "job status",
            serial(job, ("id", "kind", "status", "result", "error", "attempts", "reasoning")),
        )
        if job.kind == "provider_test":
            identity.require("provider:read")
            payload["data"]["result"] = {
                key: value for key, value in job.result.items() if key != "submission"
            }
            payload["cost"] = job.result.get("cost", payload["cost"])
        if job.kind in {"draft", "card_generate"}:
            identity.require("draft:read" if job.kind == "draft" else "card:read")
            identity.require("task:read")
            if job.kind == "card_generate":
                from app.services.card_generation import check_input_access

                await check_input_access(
                    session, identity, job.task_id, job.result["submission"]["input_manifest"]
                )
            if job.result.get("draft_id"):
                from app.services.drafts import show_draft

                await show_draft(session, identity, UUID(job.result["draft_id"]))
            if job.result.get("completion") == "partial":
                payload["ok"] = False
            payload["warnings"] = job.result.get("warnings", [])
            payload["cost"] = job.result.get("cost", payload["cost"])
            # Submission authorization data is internal worker state.
            payload["data"]["result"] = {
                key: value for key, value in job.result.items() if key != "submission"
            }
        return payload

    @app.post("/jobs/{job_id}/cancel", name="job_cancel", response_model=Result)
    async def job_cancel(job_id: UUID, ctx=Depends(context, scope="function")):
        session, identity = ctx
        identity.require("job:cancel")
        job = await session.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job is None:
            raise not_found()
        if job.status not in {"cancelled", "queued", "running"}:
            raise ServiceError("terminal_job", "Completed jobs cannot be cancelled", 409, 2)
        job.status, job.finished_at = "cancelled", datetime.now(UTC)
        return result("job cancel", {"id": str(job.id), "status": job.status})

    @app.get("/billing", name="billing_balance", response_model=Result)
    async def billing_balance(ctx=Depends(context, scope="function")):
        session, identity = ctx
        identity.require("billing:read")
        data, items = await billing.overview(session, settings.billing_currency)
        return result("billing balance", data, items)

    @app.post("/billing/redeem", name="billing_redeem", response_model=Result)
    async def billing_redeem(body: CardRedeem, ctx=Depends(context, scope="function")):
        _, identity = ctx
        data = await billing.redeem(db, identity, body.code, settings.billing_currency)
        return result("billing redeem", data)

    @app.post("/tokens", name="token_create", response_model=Result)
    async def token_create(body: TokenCreate, ctx=Depends(context, scope="function")):
        session, identity = ctx
        identity.require("token:create")
        if (
            identity.token_id is not None
            or not set(body.scopes) <= SCOPES
            or not set(body.scopes) <= identity.scopes
        ):
            raise ServiceError(
                "forbidden_scopes",
                "Token scopes must be allowed and cannot include confirmation or export",
                403,
                4,
            )
        if body.expires_at.tzinfo is None or body.expires_at <= datetime.now(UTC):
            raise ServiceError(
                "invalid_expiry", "A future expiry with timezone is required", 400, 2
            )
        secret = "bid_" + secrets.token_urlsafe(32)
        token = ApiToken(
            id=uuid4(),
            org_id=identity.org_id,
            user_id=identity.user_id,
            name=body.name,
            scopes=sorted(set(body.scopes)),
            expires_at=body.expires_at,
            digest=token_digest(secret),
            encrypted_secret=crypto.encrypt(secret),
        )
        session.add(token)
        await session.flush()
        return result(
            "token create",
            {
                "id": str(token.id),
                "token": secret,
                "scopes": token.scopes,
                "expires_at": body.expires_at.isoformat(),
            },
        )

    return app
