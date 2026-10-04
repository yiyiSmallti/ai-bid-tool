"""Scoped API tokens. Only the digest is stored; the secret is shown once."""

import secrets
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ServiceError
from app.core.security import token_digest
from app.models.entities import ApiToken
from app.schemas.contracts import TokenCreate
from app.services.auth import SCOPES, Identity


async def create_token(
    session: AsyncSession, identity: Identity, body: TokenCreate
) -> tuple[ApiToken, str]:
    identity.require("token:create")
    # A token cannot mint tokens, and never gains confirmation, export or the issuer's
    # missing scopes.
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
        raise ServiceError("invalid_expiry", "A future expiry with timezone is required", 400, 2)
    secret = "bid_" + secrets.token_urlsafe(32)
    token = ApiToken(
        id=uuid4(),
        org_id=identity.org_id,
        user_id=identity.user_id,
        name=body.name,
        scopes=sorted(set(body.scopes)),
        expires_at=body.expires_at,
        digest=token_digest(secret),
    )
    session.add(token)
    await session.flush()
    return token, secret
