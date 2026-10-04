"""Org resource libraries, task selections, certificate and template files, and
unconfirmed certificate-page sources."""

import asyncio
import time
from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import ValidationError

from app.api.common import attachment, check_signature, result, signed_link
from app.core.config import Settings
from app.core.errors import ServiceError
from app.core.security import TokenSigner
from app.providers.storage import Storage
from app.schemas.certificate_contracts import (
    CertificateCreate,
    CertificateUpdate,
    TaskCertificateSelection,
)
from app.schemas.certificate_file_contracts import CertificateFileCreate
from app.schemas.contracts import Result
from app.schemas.evidence_source_contracts import EvidenceSourceCreate
from app.schemas.feature_contracts import FeatureCreate, FeatureUpdate, TaskFeatureSelection
from app.schemas.profile_contracts import (
    OrgProfileCreate,
    OrgProfileUpdate,
    TaskOrgProfileSelection,
)
from app.schemas.resource_contracts import ProductCreate, ProductUpdate, TaskProductSelection
from app.schemas.template_contracts import TaskTemplateSelection, TemplateCreate, TemplateUpdate
from app.services import (
    certificate_files,
    certificates,
    evidence_sources,
    features,
    profiles,
    resources,
    templates,
)
from app.services.template_files import MAX_TEMPLATE_BYTES, validate_template
from app.services.template_files import WARNINGS as TEMPLATE_WARNINGS


def create_router(
    context: Callable[..., Any], settings: Settings, storage: Storage, crypto: TokenSigner
) -> APIRouter:
    router = APIRouter()

    @router.post("/resources/products", name="resource_product_add", response_model=Result)
    async def product_add(body: ProductCreate, ctx=Depends(context, scope="function")):
        session, actor = ctx
        return result("resource product add", await resources.create_product(session, actor, body))

    @router.get("/resources/products", name="resource_product_list", response_model=Result)
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

    @router.post(
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

    @router.post("/tasks/{task_id}/products", name="task_resource_add", response_model=Result)
    async def task_product_add(
        task_id: UUID, body: TaskProductSelection, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        return result(
            "task resource add", await resources.select_product(session, actor, task_id, body)
        )

    @router.get("/tasks/{task_id}/products", name="task_resource_list", response_model=Result)
    async def task_product_list(
        task_id: UUID, history: bool = False, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        data, items = await resources.list_selections(session, actor, task_id, history=history)
        return result("task resource list", data, items)

    feature_warnings = ["Implementation status is a declaration; evidence has not been verified"]

    @router.post("/resources/features", name="resource_feature_add", response_model=Result)
    async def feature_add(body: FeatureCreate, ctx=Depends(context, scope="function")):
        session, actor = ctx
        return result(
            "resource feature add",
            await features.create_feature(session, actor, body),
            warnings=feature_warnings,
        )

    @router.get("/resources/features", name="resource_feature_list", response_model=Result)
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

    @router.post(
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

    @router.post("/tasks/{task_id}/features", name="task_feature_add", response_model=Result)
    async def task_feature_add(
        task_id: UUID, body: TaskFeatureSelection, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        return result(
            "task feature add",
            await features.select_feature(session, actor, task_id, body),
            warnings=feature_warnings,
        )

    @router.get("/tasks/{task_id}/features", name="task_feature_list", response_model=Result)
    async def task_feature_list(
        task_id: UUID, history: bool = False, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        data, items = await features.list_selections(session, actor, task_id, history=history)
        return result("task feature list", data, items, feature_warnings)

    certificate_warnings = [
        "Certificate metadata and dates are declarations; authenticity, legality and compliance have not been verified"
    ]

    @router.post("/resources/certificates", name="resource_certificate_add", response_model=Result)
    async def certificate_add(body: CertificateCreate, ctx=Depends(context, scope="function")):
        session, actor = ctx
        return result(
            "resource certificate add",
            await certificates.create_certificate(session, actor, body),
            warnings=certificate_warnings,
        )

    @router.get("/resources/certificates", name="resource_certificate_list", response_model=Result)
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

    @router.post(
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

    @router.post(
        "/tasks/{task_id}/certificates", name="task_certificate_add", response_model=Result
    )
    async def task_certificate_add(
        task_id: UUID, body: TaskCertificateSelection, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        return result(
            "task certificate add",
            await certificates.select_certificate(session, actor, task_id, body),
            warnings=certificate_warnings,
        )

    @router.get(
        "/tasks/{task_id}/certificates", name="task_certificate_list", response_model=Result
    )
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

    @router.post("/resources/profiles", name="resource_profile_add", response_model=Result)
    async def profile_add(body: OrgProfileCreate, ctx=Depends(context, scope="function")):
        session, actor = ctx
        return result(
            "resource profile add",
            await profiles.create_profile(session, actor, body),
            warnings=profile_warnings,
        )

    @router.get("/resources/profiles", name="resource_profile_list", response_model=Result)
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

    @router.post(
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

    @router.post("/tasks/{task_id}/profiles", name="task_profile_add", response_model=Result)
    async def task_profile_add(
        task_id: UUID, body: TaskOrgProfileSelection, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        return result(
            "task profile add",
            await profiles.select_profile(session, actor, task_id, body),
            warnings=profile_warnings,
        )

    @router.get("/tasks/{task_id}/profiles", name="task_profile_list", response_model=Result)
    async def task_profile_list(
        task_id: UUID, history: bool = False, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        data, items = await profiles.list_selections(session, actor, task_id, history=history)
        return result("task profile list", data, items, profile_warnings)

    @router.post(
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

    @router.get(
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

    @router.get(
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

    @router.get(
        "/resources/certificates/revisions/{revision_id}/file/download-link",
        name="resource_certificate_file_download_link",
        response_model=Result,
    )
    async def certificate_file_download_link(
        revision_id: UUID, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        await certificate_files.require_file(session, actor, revision_id)
        return result(
            "resource certificate file download link",
            signed_link(
                crypto,
                f"/resources/certificates/revisions/{revision_id}/file/download",
                "certificate-file-download",
                actor.org_id,
                revision_id=revision_id,
            ),
            warnings=certificate_files.WARNINGS,
        )

    @router.get(
        "/resources/certificates/revisions/{revision_id}/file/download",
        name="resource_certificate_file_download",
    )
    async def certificate_file_download(
        revision_id: UUID, signature: str, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        await certificate_files.require_file(session, actor, revision_id)
        check_signature(
            crypto, signature, "certificate-file-download", actor.org_id, revision_id=revision_id
        )
        content, descriptor = await certificate_files.read_revision(
            session, actor, revision_id, storage
        )
        return attachment(content, descriptor.media_type, descriptor.name)

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

    @router.post("/resources/templates", name="resource_template_add", response_model=Result)
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

    @router.get("/resources/templates", name="resource_template_list", response_model=Result)
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

    @router.post(
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

    @router.post("/tasks/{task_id}/templates", name="task_template_add", response_model=Result)
    async def task_template_add(
        task_id: UUID, body: TaskTemplateSelection, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        return result(
            "task template add",
            await templates.select_template(session, actor, task_id, body),
            warnings=TEMPLATE_WARNINGS,
        )

    @router.get("/tasks/{task_id}/templates", name="task_template_list", response_model=Result)
    async def task_template_list(
        task_id: UUID, history: bool = False, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        data, items = await templates.list_selections(session, actor, task_id, history=history)
        return result("task template list", data, items, TEMPLATE_WARNINGS)

    @router.get(
        "/resources/templates/revisions/{revision_id}/download-link",
        name="resource_template_download_link",
        response_model=Result,
    )
    async def template_download_link(revision_id: UUID, ctx=Depends(context, scope="function")):
        session, actor = ctx
        await templates.require_revision(session, actor, revision_id)
        return result(
            "resource template download link",
            signed_link(
                crypto,
                f"/resources/templates/revisions/{revision_id}/download",
                "template-download",
                actor.org_id,
                revision_id=revision_id,
            ),
            warnings=TEMPLATE_WARNINGS,
        )

    @router.get(
        "/resources/templates/revisions/{revision_id}/download", name="resource_template_download"
    )
    async def template_download(
        revision_id: UUID, signature: str, ctx=Depends(context, scope="function")
    ):
        session, actor = ctx
        await templates.require_revision(session, actor, revision_id)
        check_signature(
            crypto, signature, "template-download", actor.org_id, revision_id=revision_id
        )
        content, descriptor = await templates.read_revision(session, actor, revision_id, storage)
        return attachment(content, descriptor.media_type, descriptor.name)

    @router.post(
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

    @router.get(
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

    @router.get(
        "/evidence-sources/{source_id}/preview/download-link",
        name="evidence_source_preview_link",
        response_model=Result,
    )
    async def evidence_source_preview_link(source_id: UUID, ctx=Depends(context, scope="function")):
        session, actor = ctx
        row, snapshot, original = await evidence_sources.require_source(session, actor, source_id)
        return result(
            "evidence source preview link",
            signed_link(
                crypto,
                f"/evidence-sources/{source_id}/preview/download",
                "source-preview",
                actor.org_id,
                source_id=source_id,
            ),
            [evidence_sources.source_data(row, snapshot, original)],
            evidence_sources.WARNINGS,
        )

    @router.get(
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
        check_signature(crypto, signature, "source-preview", actor.org_id, source_id=source_id)
        content, descriptor = await evidence_sources.read_preview(
            session, actor, source_id, storage
        )
        return attachment(content, descriptor.media_type, descriptor.name)

    return router
