import asyncio
import time
from typing import cast

import pymupdf

from app.providers.base import ProviderFailure
from app.providers.calls import accounted_call
from app.providers.quotes import call_plan, serialized_request
from app.schemas.contracts import OCRText, ProviderUsage
from app.schemas.preflight_contracts import CallPlan


class LocalOCR:
    name = "tesseract"
    version = "pymupdf-ocr-v1"
    records_calls = True

    def __init__(self, language: str, data_dir: str | None):
        self.language = language
        self.data_dir = data_dir

    def quote(self, image: bytes, page: int) -> CallPlan:
        request = serialized_request({"page": page, "language": self.language}) + b"\x00" + image
        return call_plan("ocr", self.name, self.language, self.version, request, ocr_pages=1)

    async def recognize(self, image: bytes, page: int) -> OCRText:
        started = time.monotonic()

        def recognize():
            # Actual local OCR, never a guessed replacement for absent text.
            pixmap = pymupdf.Pixmap(image)
            pdf = pixmap.pdfocr_tobytes(language=self.language, tessdata=self.data_dir)
            with pymupdf.open(stream=pdf, filetype="pdf") as document:
                text = cast(str, document[0].get_text())
                boxes = [
                    {"bbox": list(block[:4]), "text": block[4]}
                    for block in document[0].get_text("blocks")
                ]
            return text, boxes

        async def operation():
            try:
                result = await asyncio.to_thread(recognize)
            except Exception as exc:
                result = exc
            return result, ProviderUsage(
                provider=self.name,
                model=self.language,
                version=self.version,
                duration_ms=int((time.monotonic() - started) * 1000),
                ocr_pages=1,
            )

        result, usage = await accounted_call(self.quote(image, page), operation)
        if isinstance(result, Exception):
            raise ProviderFailure(
                "Local OCR is unavailable; configure approved Tesseract language data"
            ) from result
        text, boxes = result
        return OCRText(
            text=text,
            boxes=boxes,
            usage=usage,
        )
