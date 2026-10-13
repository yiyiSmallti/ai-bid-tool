"""Offline trust-anchor settings managed by the installation human."""

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.core.db import Database
from app.core.errors import ServiceError
from app.schemas.bid_signature import TRUST_ANCHOR_BYTE_LIMIT
from app.schemas.contracts import Result
from app.services.platform_trust_anchors import PlatformTrustAnchorService


def create_router(db: Database, context):
    router = APIRouter(prefix="/platform/trust-anchors")
    service = PlatformTrustAnchorService(db)

    async def human(ctx=Depends(context, scope="function")):
        from app.services.platform_credentials import require_human

        actor = ctx[1]
        require_human(actor, "trust-anchor:manage")
        return actor

    async def read_certificate(file: UploadFile) -> bytes:
        try:
            data = await file.read(TRUST_ANCHOR_BYTE_LIMIT + 1)
        finally:
            await file.close()
        if len(data) > TRUST_ANCHOR_BYTE_LIMIT:
            raise ServiceError(
                "invalid_trust_anchor", "Certificate exceeds the upload limit", 400, 2
            )
        return data

    @router.get("", name="platform_trust_anchor_list", response_model=Result)
    async def list_anchors(actor=Depends(human)):
        rows = await service.list()
        return Result(
            ok=True,
            command="platform trust-anchor list",
            items=[row.model_dump(mode="json") for row in rows],
        )

    @router.post("/preview", name="platform_trust_anchor_preview", response_model=Result)
    async def preview(certificate: UploadFile = File(), label: str = Form(), actor=Depends(human)):
        row = await service.preview(await read_certificate(certificate), label)
        return Result(
            ok=True, command="platform trust-anchor preview", data=row.model_dump(mode="json")
        )

    @router.post("", name="platform_trust_anchor_add", response_model=Result)
    async def add(certificate: UploadFile = File(), label: str = Form(), actor=Depends(human)):
        row = await service.add(actor, await read_certificate(certificate), label)
        return Result(
            ok=True, command="platform trust-anchor add", data=row.model_dump(mode="json")
        )

    @router.post(
        "/{anchor_id}/disable", name="platform_trust_anchor_disable", response_model=Result
    )
    async def disable(anchor_id: UUID, actor=Depends(human)):
        row = await service.disable(actor, anchor_id)
        return Result(
            ok=True, command="platform trust-anchor disable", data=row.model_dump(mode="json")
        )

    return router
