"""PyMuPDF validation and page reading; imported only by the limited child."""

import base64
from collections.abc import Iterator
from itertools import pairwise
from typing import Any, cast

import pymupdf

from app.core.errors import ServiceError, invalid_document
from app.core.pdf_raster import MIN_OCR_DPI, OCR_DPI, ocr_dpi, raster_dimensions

MIN_IMAGE_AREA_RATIO = 0.20
MIN_WARN_IMAGE_AREA_RATIO = 0.05
MIN_BACKGROUND_TEXT_RATIO = 0.25


def open_pdf(content: bytes, max_pages: int) -> pymupdf.Document:
    try:
        document = pymupdf.open(stream=content, filetype="pdf")
    except (pymupdf.FileDataError, pymupdf.EmptyFileError) as exc:
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
    # A readable text layer over a paper texture / letterhead leaves large
    # margins uncovered. Exclude that image before calculating page coverage.
    # Use union intersections so duplicate or overlapping text is not inflated.
    images = [
        image
        for image in images
        if rectangle_area([part for rect in text if not (part := rect & image).is_empty])
        < MIN_BACKGROUND_TEXT_RATIO * image.get_area()
    ]
    return max(0, rectangle_area(images + text) - rectangle_area(text)) / bounds.get_area()


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


def read_document(content: bytes, max_pages: int, parse: bool) -> Iterator[dict]:
    with open_pdf(content, max_pages) as document:
        if not parse:
            yield {"page_count": document.page_count}
            return
        for index in range(document.page_count):
            text, image, warnings = read_page(document, index)
            yield {
                "page": index + 1,
                "text": text,
                "image": base64.b64encode(image).decode("ascii") if image is not None else None,
                "warnings": warnings,
            }


def read_bid_document(content: bytes, max_pages: int, prepare: bool) -> Iterator[dict]:
    """Uploaded-bid pages preserve native text and render without OCR or signature checks."""
    with open_pdf(content, max_pages) as document:
        # Repair can silently change the signed source or lose objects. Ordinary
        # tender parsing retains its historical behavior; this branch is strict.
        if (
            not document.is_pdf
            or document.needs_pass
            or document.is_repaired
            or document.xref_get_key(-1, "Encrypt")[0] != "null"
        ):
            raise ServiceError(
                "bid_pdf_invalid", "Uploaded PDF must be unrepaired and unencrypted", 400, 2
            )
        if not prepare:
            yield {"page_count": document.page_count}
            return
        for index in range(document.page_count):
            page = document[index]
            text = cast(str, page.get_text(sort=True))
            coverage = uncovered_images(page)
            # A scan with just a page number or header is still an image page.
            # Native text is retained independently, without claiming a full parse.
            kind = "text" if text.strip() and coverage < MIN_IMAGE_AREA_RATIO else "image"
            warnings = []
            if kind == "image":
                warnings.append("image_page_not_transcribed")
            elif coverage >= MIN_WARN_IMAGE_AREA_RATIO:
                warnings.append("unparsed_image_regions")
            dpi = ocr_dpi(page.rect.width, page.rect.height)
            if dpi < OCR_DPI:
                warnings.append("render_resolution_reduced")
            pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
            # Field names, subjects and values are restricted. Count field
            # presence only; this does not establish that any field is signed.
            signature_fields = sum(
                1
                for widget in (page.widgets() or ())
                if cast(Any, widget).field_type == cast(Any, pymupdf).PDF_WIDGET_TYPE_SIGNATURE
            )
            yield {
                "page": index + 1,
                "text": text,
                "page_kind": kind,
                "image": base64.b64encode(pixmap.tobytes("png")).decode("ascii"),
                "width": pixmap.width,
                "height": pixmap.height,
                "dpi": dpi,
                "warnings": warnings,
                "signature_field_count": signature_fields,
                "structure": {
                    "page": index + 1,
                    "width_points": page.rect.width,
                    "height_points": page.rect.height,
                    "rotation": page.rotation,
                },
                "renderer_identity": "pymupdf:" + pymupdf.VersionBind,
            }
