from app.schemas.contracts import (
    Category,
    ExtractedRequirement,
    Extraction,
    LLMResult,
    OCRText,
    ProviderUsage,
    Source,
)


class FakeLLM:
    name = "test-fake"
    model = "fixture-extraction"
    version = "test-v1"
    test_only = True

    def __init__(self):
        self.calls = 0

    async def extract(self, chunks, schema):
        self.calls += 1
        return LLMResult(
            extraction=Extraction(
                items=[
                    ExtractedRequirement(
                        category=Category.technical,
                        text="Synthetic fixture requirement",
                        source=Source(
                            document_id=chunk["document_id"],
                            chunk_id=chunk["id"],
                            page=chunk["page"],
                            quote=chunk["text"].splitlines()[-1],
                        ),
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
    name = "test-ocr"
    version = "test-v1"

    async def recognize(self, image, page):
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
