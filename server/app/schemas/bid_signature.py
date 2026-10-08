"""Platform-managed public CA metadata; never uploaded-bid signer identities."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints

from app.schemas.contracts import Contract
from app.schemas.screenshot_contracts import Sha256

TrustAnchorLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
TRUST_ANCHOR_BYTE_LIMIT = 65536


class TrustAnchorPreview(Contract):
    label: TrustAnchorLabel
    fingerprint_sha256: Sha256
    subject: str = Field(max_length=2000)
    issuer: str = Field(max_length=2000)
    not_before: AwareDatetime
    not_after: AwareDatetime
    is_ca: Literal[True] = True


class TrustAnchorView(TrustAnchorPreview):
    id: UUID
    enabled: bool
    revision: int = Field(strict=True, ge=1)
    created_by: str
    created_at: AwareDatetime
    disabled_by: str | None = None
    disabled_at: AwareDatetime | None = None


class TrustAnchorAddResult(TrustAnchorView):
    created: bool
