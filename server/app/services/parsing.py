import asyncio
import base64
import io
import zipfile

from docx import Document as WordDocument

from app.core.config import PDFSettings
from app.core.errors import invalid_document
from app.core.pdf_process import (
    PDFOperation,
    resource_error,
    run_pdf_operation,
    run_pdf_operation_async,
)
from app.providers.base import OCRProvider
from app.providers.calls import plan_calls
from app.schemas.contracts import PageText, ProviderUsage, SectionText
from app.services.docx_blocks import parse_docx
from app.services.extraction import at_boundary, locate_span, normalize

PARSER_VERSION = "parse-v4"


def validate_document(
    content: bytes, suffix: str, max_pages: int, settings: PDFSettings | None = None
) -> None:
    if suffix == ".pdf":
        run_pdf_operation(content, "validate", {"max_pages": max_pages}, settings)
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


async def validate_document_async(
    content: bytes, suffix: str, max_pages: int, settings: PDFSettings | None = None
) -> None:
    if suffix == ".pdf":
        await run_pdf_operation_async(content, "validate", {"max_pages": max_pages}, settings)
    else:
        await asyncio.to_thread(validate_document, content, suffix, max_pages, settings)


def pdf_page_result(record: dict, number: int) -> tuple[str, bytes | None, list[str]]:
    """Validate plain child data before passing an image to the OCR provider."""
    try:
        if type(record["page"]) is not int or record["page"] != number:
            raise ValueError("unexpected page")
        text, warnings, encoded = record["text"], record["warnings"], record["image"]
        if (
            not isinstance(text, str)
            or not isinstance(warnings, list)
            or any(not isinstance(warning, str) for warning in warnings)
            or (encoded is not None and not isinstance(encoded, str))
        ):
            raise ValueError("invalid page result")
        image = base64.b64decode(encoded, validate=True) if encoded is not None else None
        if image is not None and not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("invalid page image")
        return text, image, warnings
    except (KeyError, TypeError, ValueError) as exc:
        raise resource_error() from exc


def merge_pdf_text(native: str, recognized: str) -> tuple[str, bool]:
    """Keep native spans intact and append OCR spans without duplicating native citations."""
    if not native.strip():
        return recognized, bool(recognized.strip())
    remaining = recognized
    # A full native block can wrap differently in OCR. Match using the same rules as
    # citation verification; individual lines also cover OCR-interleaved headers.
    for quote in [native, *native.splitlines()]:
        if not quote.strip():
            continue
        span, _ = locate_span(remaining, quote)
        if span is not None and at_boundary(remaining, *span):
            remaining = remaining[: span[0]] + "\n" + remaining[span[1] :]
    extra = remaining.strip()
    return (native.rstrip() + "\n\n" + extra if extra else native), bool(normalize(extra))


async def parse_document(
    content: bytes,
    suffix: str,
    ocr: OCRProvider,
    max_pages: int,
    settings: PDFSettings | None = None,
) -> tuple[list[PageText] | list[SectionText], list[ProviderUsage], list[str]]:
    if suffix == ".docx":
        await asyncio.to_thread(validate_document, content, suffix, max_pages)
        # Word has no reliable pages; blocks are cited by section and paragraph or cell.
        sections, warnings = await asyncio.to_thread(parse_docx, content)
        return sections, [], warnings
    if suffix != ".pdf":
        raise invalid_document()
    output, usages, warnings = [], [], []
    with PDFOperation(content, "parse", {"max_pages": max_pages}, settings) as document:
        await document.wait_async()
        plan_calls(sum(1 for record in document.records() if record.get("image") is not None))
        for number, record in enumerate(document.records(), 1):
            text, image, page_warnings = pdf_page_result(record, number)
            warnings.extend(page_warnings)
            scanned = image is not None
            if image is not None:
                recognized = await ocr.recognize(image, number)
                usages.append(recognized.usage)
                text, added = merge_pdf_text(text, recognized.text)
                if not added:
                    warnings.append(
                        f"Page {number} OCR returned no additional text for its image content; "
                        "the page may be incomplete and requires manual review."
                    )
            output.append(PageText(page=number, text=text, ocr=scanned))
    return output, usages, warnings
