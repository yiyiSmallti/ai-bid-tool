from typing import Protocol

from app.schemas.contracts import LLMResult, OCRText, ProviderUsage


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


class OCRProvider(Protocol):
    name: str
    version: str

    async def recognize(self, image: bytes, page: int) -> OCRText: ...
