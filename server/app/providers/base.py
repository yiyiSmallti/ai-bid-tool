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
