from app.providers.base import ProviderFailure
from app.providers.calls import accounted_call, current_accounting
from app.providers.quotes import serialized_request, zero_quote
from app.schemas.contracts import (
    Category,
    ExtractedRequirement,
    Extraction,
    LLMResult,
    OCRText,
    ProviderUsage,
    Source,
)
from app.services.extraction import location_of


async def fixture_call(quote, operation):
    """Meter one fake dispatch while preserving its result or provider failure."""
    if current_accounting.get() is None:
        return await operation()

    async def dispatched():
        try:
            result = await operation()
            return result, result.usage
        except ProviderFailure as failure:
            # A fixture hook represents one call. Missing or multiple receipts do not
            # prove that this call completed; preserve the error and unknown hold.
            if len(failure.usage) != 1:
                raise
            return failure, failure.usage[0]

    result, usage = await accounted_call(quote, dispatched)
    if isinstance(result, ProviderFailure):
        result.usage = [usage]
        raise result
    updates = {"usage": usage}
    if isinstance(result, LLMResult):
        updates["usages"] = [usage]
    return result.model_copy(update=updates)


def source_for(chunk) -> Source:
    # Word chunks cite their last block; PDF chunks cite their page.
    if chunk.get("blocks"):
        block = chunk["blocks"][-1]
        return Source(
            document_id=chunk["document_id"],
            chunk_id=chunk["id"],
            location=location_of(block),
            quote=block["text"].splitlines()[-1],
        )
    return Source(
        document_id=chunk["document_id"],
        chunk_id=chunk["id"],
        page=chunk["page"],
        quote=chunk["text"].splitlines()[-1],
    )


class FakeLLM:
    name = "test-fake"
    model = "fixture-extraction"
    version = "test-v1"
    test_only = True
    records_calls = True

    def __init__(self):
        self.calls = 0

    async def extract(self, chunks, schema):
        quote = zero_quote(
            "llm",
            "local_free",
            self.name,
            self.model,
            self.version,
            serialized_request(
                {
                    "chunks": [
                        {**chunk, "id": str(chunk["id"]), "document_id": str(chunk["document_id"])}
                        for chunk in chunks
                    ],
                    "schema": schema,
                }
            ),
        )
        return await fixture_call(quote, lambda: self._extract(chunks, schema))

    async def _extract(self, chunks, schema):
        self.calls += 1
        return LLMResult(
            extraction=Extraction(
                items=[
                    ExtractedRequirement(
                        category=Category.technical,
                        text="Synthetic fixture requirement",
                        source=source_for(chunk),
                    )
                    for chunk in chunks
                ]
            ),
            usage=ProviderUsage(
                provider=self.name,
                model=self.model,
                version=self.version,
                duration_ms=1,
                tokens=10,
                usd=0,
                test_only=True,
            ),
        )


class FakeOCR:
    records_calls = True
    test_only = True
    name = "test-ocr"
    version = "test-v1"

    async def recognize(self, image, page):
        quote = zero_quote(
            "ocr",
            "local_free",
            self.name,
            "fixture",
            self.version,
            image + str(page).encode(),
            ocr_pages=1,
        )
        return await fixture_call(quote, lambda: self._recognize(image, page))

    async def _recognize(self, image, page):
        return OCRText(
            text="Synthetic OCR fixture only",
            usage=ProviderUsage(
                provider=self.name,
                model="fixture",
                version=self.version,
                duration_ms=1,
                ocr_pages=1,
                usd=0,
                test_only=True,
            ),
        )
