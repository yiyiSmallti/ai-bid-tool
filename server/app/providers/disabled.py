from app.providers.base import ProviderFailure
from app.providers.drafting import DraftingOutput
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

    async def draft(self, requirements: list[dict], materials: list[dict]) -> DraftingOutput:
        raise ProviderFailure("No approved drafting model is configured")
