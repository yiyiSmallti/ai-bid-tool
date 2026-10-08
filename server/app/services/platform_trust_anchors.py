"""Offline public CA admission and snapshots through fixed restricted functions."""

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from cryptography.exceptions import UnsupportedAlgorithm
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import Database
from app.core.errors import ServiceError, not_found
from app.schemas.bid_signature import (
    TRUST_ANCHOR_BYTE_LIMIT,
    TrustAnchorAddResult,
    TrustAnchorLabel,
    TrustAnchorPreview,
    TrustAnchorView,
)
from app.services.bid_pdf_signatures import parse_certificate


async def get_snapshot(session: AsyncSession) -> dict[str, Any]:
    """Only enabled public CAs; persist this result before admitting the worker."""
    value = await session.scalar(text("SELECT public.platform_trust_anchor_snapshot()"))
    if not isinstance(value, dict) or set(value) != {"sha256", "anchors"}:
        raise ServiceError("trust_store_unavailable", "Offline trust store is unavailable", 503, 3)
    return value


enabled_snapshot = get_snapshot


class PlatformTrustAnchorService:
    def __init__(self, db: Database):
        self.db = db

    @asynccontextmanager
    async def transaction(self):
        try:
            async with self.db.transaction() as session:
                yield session
        except SQLAlchemyError as exc:
            constraint = getattr(
                getattr(getattr(exc, "orig", None), "diag", None), "constraint_name", None
            )
            if constraint == "platform_trust_anchor_capacity":
                raise ServiceError(
                    "trust_anchor_capacity",
                    "Offline trust store has reached its retention limit",
                    409,
                    2,
                ) from None
            # Database DETAIL can quote rows; expose only a fixed diagnostic.
            raise ServiceError(
                "trust_store_unavailable", "Offline trust store is unavailable", 503, 3
            ) from None

    async def parse(self, data: bytes, label: str) -> tuple[TrustAnchorPreview, bytes]:
        if not 0 < len(data) <= TRUST_ANCHOR_BYTE_LIMIT:
            raise ServiceError(
                "invalid_trust_anchor", "Certificate exceeds the upload limit", 400, 2
            )
        try:
            name = TypeAdapter(TrustAnchorLabel).validate_python(label)
            parsed = await asyncio.to_thread(parse_certificate, data)
            if not parsed["is_ca"]:
                raise ValueError("Not a CA")
            view = TrustAnchorPreview.model_validate(
                {
                    "label": name,
                    **{
                        key: parsed[key]
                        for key in (
                            "fingerprint_sha256",
                            "subject",
                            "issuer",
                            "not_before",
                            "not_after",
                        )
                    },
                }
            )
            der = parsed["der"]
            if not isinstance(der, bytes) or not 0 < len(der) <= TRUST_ANCHOR_BYTE_LIMIT:
                raise ValueError("Invalid DER")
        except (
            ValueError,
            KeyError,
            TypeError,
            ValidationError,
            UnsupportedAlgorithm,
            NotImplementedError,
        ):
            raise ServiceError(
                "invalid_trust_anchor",
                "Upload must contain one supported PEM or DER CA certificate",
                400,
                2,
            ) from None
        return view, der

    async def preview(self, data: bytes, label: str) -> TrustAnchorPreview:
        view, _ = await self.parse(data, label)
        return view

    async def list(self) -> list[TrustAnchorView]:
        async with self.transaction() as session:
            rows = await session.scalar(text("SELECT public.platform_trust_anchor_list()"))
        return [TrustAnchorView.model_validate(row) for row in rows]

    async def add(self, actor_email: str, data: bytes, label: str) -> TrustAnchorAddResult:
        view, der = await self.parse(data, label)
        params = view.model_dump(exclude={"is_ca"})
        params |= {"der": der, "actor": actor_email}
        async with self.transaction() as session:
            row = await session.scalar(
                text(
                    "SELECT public.platform_trust_anchor_add(:label,:der,:fingerprint_sha256,"
                    ":subject,:issuer,:not_before,:not_after,:actor)"
                ),
                params,
            )
        return TrustAnchorAddResult.model_validate(row)

    async def disable(self, actor_email: str, anchor_id: UUID) -> TrustAnchorView:
        async with self.transaction() as session:
            row = await session.scalar(
                text("SELECT public.platform_trust_anchor_disable(:id,:actor)"),
                {"id": anchor_id, "actor": actor_email},
            )
            if row is None:
                raise not_found()
        return TrustAnchorView.model_validate(row)
