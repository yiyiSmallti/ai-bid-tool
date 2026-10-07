"""Result envelopes and short-lived signed download links shared by the org routes."""

from typing import Any
from urllib.parse import quote
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response

from app.core.errors import ServiceError, not_found
from app.core.security import TokenSigner
from app.schemas.contracts import Result

LINK_SECONDS = 300


def serial(row: Any, fields: tuple[str, ...]) -> dict:
    return jsonable_encoder({field: getattr(row, field) for field in fields})


def result(command: str, data=None, items=None, warnings=None) -> dict:
    return Result(
        ok=True, command=command, data=data or {}, items=items or [], warnings=warnings or []
    ).model_dump(mode="json")


def signed_link(
    crypto: TokenSigner, path: str, kind: str, org_id: UUID, **identifiers: UUID | int | str
) -> dict:
    """A download URL for `path` that only this org can open for LINK_SECONDS."""
    signature = crypto.issue(
        {"kind": kind, "org_id": str(org_id), **{k: str(v) for k, v in identifiers.items()}},
        LINK_SECONDS,
    )
    return {"url": f"{path}?signature={signature}", "expires_in": LINK_SECONDS}


def check_signature(
    crypto: TokenSigner, signature: str, kind: str, org_id: UUID, **identifiers: UUID | int | str
) -> None:
    """Accept only an unexpired link of this kind for this org and these identifiers;
    anything else is indistinguishable from a missing resource."""
    try:
        payload = crypto.open(signature)
    except ServiceError as exc:
        raise not_found() from exc
    expected = {"kind": kind, "org_id": str(org_id), **{k: str(v) for k, v in identifiers.items()}}
    if any(payload.get(key) != value for key, value in expected.items()):
        raise not_found()


def attachment(content: bytes, media_type: str, name: str) -> Response:
    return Response(
        content,
        media_type=media_type,
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(name, safe="")},
    )
