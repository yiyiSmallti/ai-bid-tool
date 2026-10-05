from collections.abc import Sequence
from typing import TYPE_CHECKING
from uuid import UUID

from app.providers.base import ProviderFailure
from app.providers.drafting import DraftingOutput
from app.schemas.contracts import LLMResult

if TYPE_CHECKING:
    from app.schemas.memory_contracts import MemoryPromptContext


class DisabledLLM:
    provider_config_id: UUID | None = None
    name = "unconfigured"
    model = "unconfigured"
    version = "disabled-v1"
    test_only = False
    # This adapter never dispatches; preserve its explicit unavailable diagnostic
    # instead of treating the intentional negative capability as an unmetered call.
    records_calls = True

    async def extract(self, chunks: list[dict], schema: dict) -> LLMResult:
        raise ProviderFailure(
            "No organization provider or enabled platform default model is configured."
        )

    async def draft(
        self,
        requirements: list[dict],
        materials: list[dict],
        fields: Sequence[dict] = (),
        *,
        memory: "MemoryPromptContext | None" = None,
    ) -> DraftingOutput:
        raise ProviderFailure("No approved drafting model is configured")
