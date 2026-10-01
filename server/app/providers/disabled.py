from app.providers.base import ProviderFailure
from app.schemas.contracts import LLMResult


class DisabledLLM:
    name = "unconfigured"
    model = "unconfigured"
    version = "disabled-v1"
    test_only = False

    async def extract(self, chunks: list[dict], schema: dict) -> LLMResult:
        raise ProviderFailure(
            "Real API integration is deferred. No approved LLM provider is configured."
        )
