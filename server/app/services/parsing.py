import asyncio
import io
import zipfile
from itertools import pairwise
from typing import cast

import pymupdf
from docx import Document as WordDocument

from app.core.errors import ServiceError
from app.core.pdf_raster import MIN_OCR_DPI, OCR_DPI, ocr_dpi, raster_dimensions
from app.providers.base import OCRProvider
from app.schemas.contracts import PageText, ProviderUsage, SectionText
from app.services.docx_blocks import parse_docx
from app.services.extraction import at_boundary, locate_span, normalize

PARSER_VERSION = "parse-v3"
MIN_IMAGE_AREA_RATIO = 0.20
# Logos, seals and signatures stay below this share and do not warrant a review warning.
MIN_WARN_IMAGE_AREA_RATIO = 0.05


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
    try:
        if document.is_encrypted or not 0 < len(document) <= max_pages:
            raise invalid_document()
        for page in document:
            raster_dimensions(page.rect.width, page.rect.height, MIN_OCR_DPI / 72)
    except Exception:
        document.close()
        raise
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


def rectangle_area(rectangles: list[pymupdf.Rect]) -> float:
    """Union area: overlapping images/text blocks must never be counted twice."""
    edges = sorted({edge for rect in rectangles for edge in (rect.x0, rect.x1)})
    area = 0.0
    for left, right in pairwise(edges):
        intervals = sorted((r.y0, r.y1) for r in rectangles if r.x0 < right and r.x1 > left)
        bottom = float("-inf")
        for top, end in intervals:
            area += (right - left) * max(0, end - max(top, bottom))
            bottom = max(bottom, end)
    return area


def uncovered_images(page: pymupdf.Page) -> float:
    # Text/image bboxes use unrotated coordinates, unlike Page.rect. Image metadata
    # includes cropped/inline images without loading their binary pixel content.
    bounds = page.rect * page.derotation_matrix
    images = [pymupdf.Rect(info["bbox"]) & bounds for info in page.get_image_info()]
    images = [rect for rect in images if not rect.is_empty]
    if not images:
        return 0.0
    blocks = page.get_text("blocks", flags=pymupdf.TEXTFLAGS_BLOCKS & ~pymupdf.TEXT_PRESERVE_IMAGES)
    text = [
        pymupdf.Rect(block[:4]) & bounds for block in blocks if block[6] == 0 and block[4].strip()
    ]
    text = [rect for rect in text if not rect.is_empty]
    return max(0, rectangle_area(images + text) - rectangle_area(text)) / bounds.get_area()


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


def read_page(document: pymupdf.Document, index: int) -> tuple[str, bytes | None, list[str]]:
    page = document[index]
    text = cast(str, page.get_text(sort=True))
    coverage = uncovered_images(page)
    warnings = []
    image = None
    if not text.strip() or coverage >= MIN_IMAGE_AREA_RATIO:
        dpi = ocr_dpi(page.rect.width, page.rect.height)
        image = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False).tobytes("png")
        if dpi < OCR_DPI:
            warnings.append(
                f"Page {index + 1} OCR resolution was reduced to {dpi} DPI to fit raster limits; "
                "check small text manually."
            )
    elif coverage >= MIN_WARN_IMAGE_AREA_RATIO:
        warnings.append(
            f"Page {index + 1} contains image regions below the OCR area threshold; "
            "text in those images was not parsed and requires manual review."
        )
    return text, image, warnings


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
            text, image, page_warnings = await asyncio.to_thread(read_page, document, index)
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
    finally:
        document.close()
    return output, usages, warnings
