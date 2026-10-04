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


def invalid_document() -> ServiceError:
    return ServiceError(
        "invalid_document",
        "Upload must be a readable, unencrypted PDF or DOCX within limits",
        400,
        2,
    )


def open_pdf(content: bytes, max_pages: int) -> pymupdf.Document:
    try:
        document = pymupdf.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise invalid_document() from exc
    if document.is_encrypted or not 0 < len(document) <= max_pages:
        document.close()
        raise invalid_document()
    return document


def validate_document(content: bytes, suffix: str, max_pages: int) -> None:
    if suffix == ".pdf":
        open_pdf(content, max_pages).close()
        return
    try:
        if suffix != ".docx":
            raise ValueError("unsupported extension")
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
                raise ValueError("oversized archive")
        WordDocument(io.BytesIO(content))
    except Exception as exc:
        raise invalid_document() from exc


def read_page(document: pymupdf.Document, index: int) -> tuple[str, bytes | None]:
    page = document[index]
    text = cast(str, page.get_text(sort=True))
    image = page.get_pixmap(dpi=180).tobytes("png") if not text.strip() else None
    return text, image


async def parse_document(
    content: bytes, suffix: str, ocr: OCRProvider, max_pages: int
) -> tuple[list[PageText] | list[SectionText], list[ProviderUsage], list[str]]:
    if suffix == ".docx":
        await asyncio.to_thread(validate_document, content, suffix, max_pages)
        # Word has no reliable pages; blocks are cited by section and paragraph or cell.
        sections, warnings = await asyncio.to_thread(parse_docx, content)
        return sections, [], warnings
    if suffix != ".pdf":
        raise invalid_document()
    output, usages, warnings = [], [], []
    # One open document, and at most one rendered scan page in memory at a time.
    document = await asyncio.to_thread(open_pdf, content, max_pages)
    try:
        for index in range(document.page_count):
            number = index + 1
            text, image = await asyncio.to_thread(read_page, document, index)
            scanned = image is not None
            if image is not None:
                recognized = await ocr.recognize(image, number)
                usages.append(recognized.usage)
                text = recognized.text
                if not text.strip():
                    warnings.append(
                        f"Page {number} has no recognized text and requires manual review."
                    )
            output.append(PageText(page=number, text=text, ocr=scanned))
    finally:
        document.close()
    return output, usages, warnings
