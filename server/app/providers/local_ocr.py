import asyncio
import time
from typing import cast

import pymupdf

from app.providers.base import ProviderFailure
from app.schemas.contracts import OCRText, ProviderUsage


class LocalOCR:
    name = "tesseract"
    version = "pymupdf-ocr-v1"

    def __init__(self, language: str, data_dir: str | None):
        self.language = language
        self.data_dir = data_dir

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

        try:
            text, boxes = await asyncio.to_thread(recognize)
        except Exception as exc:
            raise ProviderFailure(
                "Local OCR is unavailable; configure approved Tesseract language data"
            ) from exc
        return OCRText(
            text=text,
            boxes=boxes,
            usage=ProviderUsage(
                provider=self.name,
                model=self.language,
                version=self.version,
                duration_ms=int((time.monotonic() - started) * 1000),
                ocr_pages=1,
                usd=0,
            ),
        )
