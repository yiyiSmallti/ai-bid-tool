from uuid import UUID

from app.providers.base import ProviderFailure
from app.providers.drafting import DraftingOutput
from app.schemas.contracts import LLMResult


class DisabledLLM:
    provider_config_id: UUID | None = None
    name = "unconfigured"
    model = "unconfigured"
    version = "disabled-v1"
    test_only = False

    async def extract(self, chunks: list[dict], schema: dict) -> LLMResult:
        raise ProviderFailure(
            "No organization provider or enabled platform default model is configured."
        )

    async def draft(self, requirements: list[dict], materials: list[dict]) -> DraftingOutput:
        raise ProviderFailure("No approved drafting model is configured")
