from collections.abc import Sequence
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from app.schemas.contracts import LLMResult, OCRText, ProviderUsage

if TYPE_CHECKING:
    from app.providers.checking import CheckProviderRequest, CheckProviderResult
    from app.providers.drafting import DraftingOutput


class ProviderFailure(Exception):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        refused: bool = False,
        code: str = "provider_unavailable",
        usage: list[ProviderUsage] | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.refused = refused
        self.code = code
        # Calls that completed before the failure were still billed by the vendor.
        self.usage = usage or []


class MalformedOutput(ProviderFailure):
    """The model answered with text that is not the extraction schema; a smaller batch may parse."""

    def __init__(self, usage: list[ProviderUsage]):
        super().__init__(
            "Model output did not match the extraction schema",
            code="invalid_provider_output",
            usage=usage,
        )


class TruncatedOutput(ProviderFailure):
    """The model hit its output limit; a smaller batch may still fit."""

    def __init__(self, usage: list[ProviderUsage]):
        super().__init__(
            "Model output was truncated even for a single line; raise "
            "BID_LLM_MAX_OUTPUT_TOKENS or turn off model thinking with BID_LLM_REQUEST_OPTIONS",
            code="invalid_provider_output",
            usage=usage,
        )


class LLMProvider(Protocol):
    name: str
    model: str
    version: str
    test_only: bool

    async def extract(self, chunks: list[dict], schema: dict) -> LLMResult: ...

    async def draft(
        self, requirements: list[dict], materials: list[dict], fields: Sequence[dict] = ()
    ) -> "DraftingOutput": ...


@runtime_checkable
class CheckProvider(Protocol):
    """One semantic-check batch against server-assigned local identifiers."""

    name: str
    model: str
    version: str
    test_only: bool

    def request_body(self, request: "CheckProviderRequest") -> dict: ...

    def reservation(self, request: "CheckProviderRequest") -> Decimal: ...

    async def check(self, request: "CheckProviderRequest") -> "CheckProviderResult": ...


class OCRProvider(Protocol):
    name: str
    version: str

    async def recognize(self, image: bytes, page: int) -> OCRText: ...
