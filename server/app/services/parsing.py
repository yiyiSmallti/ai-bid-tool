import asyncio
import io
import zipfile
from typing import cast

import pymupdf
from docx import Document as WordDocument

from app.core.errors import ServiceError
from app.providers.base import OCRProvider
from app.schemas.contracts import PageText, ProviderUsage, SectionText
from app.services.docx_blocks import parse_docx

PARSER_VERSION = "parse-v2"


def validate_document(content: bytes, suffix: str, max_pages: int) -> None:
    try:
        if suffix == ".pdf":
            with pymupdf.open(stream=content, filetype="pdf") as document:
                if document.is_encrypted or not 0 < len(document) <= max_pages:
                    raise ValueError("encrypted or too many pages")
        elif suffix == ".docx":
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
                    raise ValueError("oversized archive")
            WordDocument(io.BytesIO(content))
        else:
            raise ValueError("unsupported extension")
    except Exception as exc:
        raise ServiceError(
            "invalid_document",
            "Upload must be a readable, unencrypted PDF or DOCX within limits",
            400,
            2,
        ) from exc


async def parse_document(
    content: bytes, suffix: str, ocr: OCRProvider, max_pages: int
) -> tuple[list[PageText] | list[SectionText], list[ProviderUsage], list[str]]:
    await asyncio.to_thread(validate_document, content, suffix, max_pages)
    if suffix == ".docx":
        # Word has no reliable pages; blocks are cited by section and paragraph or cell.
        sections, warnings = await asyncio.to_thread(parse_docx, content)
        return sections, [], warnings

    def read_pdf():
        pages = []
        with pymupdf.open(stream=content, filetype="pdf") as document:
            for index in range(document.page_count):
                page = document[index]
                text = cast(str, page.get_text(sort=True))
                image = page.get_pixmap(dpi=180).tobytes("png") if not text.strip() else None
                pages.append((index + 1, text, image))
        return pages

    pages = await asyncio.to_thread(read_pdf)
    output, usages, warnings = [], [], []
    for number, text, image in pages:
        if image is not None:
            recognized = await ocr.recognize(image, number)
            usages.append(recognized.usage)
            text = recognized.text
            if not text.strip():
                warnings.append(f"Page {number} has no recognized text and requires manual review.")
        output.append(PageText(page=number, text=text, ocr=image is not None))
    return output, usages, warnings
