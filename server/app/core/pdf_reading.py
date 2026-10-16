"""PyMuPDF validation and page reading; imported only by the limited child."""

import base64
import re
import unicodedata
from collections import Counter
from collections.abc import Iterator
from itertools import pairwise
from typing import Any, cast

import pymupdf

from app.core.errors import ServiceError, invalid_document
from app.core.pdf_raster import MIN_OCR_DPI, OCR_DPI, ocr_dpi, raster_dimensions

MIN_IMAGE_AREA_RATIO = 0.20
MIN_WARN_IMAGE_AREA_RATIO = 0.05
MIN_BACKGROUND_TEXT_RATIO = 0.25
# A cell line ending here, or a line opening with a list marker, starts a new line.
LINE_END = frozenset("。；！？：.;!?:")
LIST_MARKER = re.compile(r"[(（]?[0-9一二三四五六七八九十]+[)）、.．]|[①-⑳]|[a-zA-Z][.)]")
# An opening bracket or quote and the narrow text after it, up to the first space or
# wide character, cannot be broken across lines.
LEADING_WORD = re.compile(r"[（“《(\[]?[^\s\u2e80-\uffef]+")

pymupdf.no_recommend_layout()


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


def _lines(
    page: pymupdf.Page, clip: pymupdf.Rect | None = None
) -> list[tuple[pymupdf.Rect, float, str]]:
    """Text lines in reading order; justified segments on one baseline become one line.

    Inside a clip (one table cell) every segment on a baseline belongs to that line;
    elsewhere only segments closer than two characters do, keeping columns apart.
    """
    found: list[tuple[pymupdf.Rect, float, str]] = []
    for block in cast(dict, page.get_text("dict", clip=clip, sort=True))["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"]).strip()
            if not text:
                continue
            rect, size = pymupdf.Rect(line["bbox"]), max(span["size"] for span in line["spans"])
            if found:
                left, left_size, before = found[-1]
                if (
                    abs(rect.y0 - left.y0) < size / 2
                    and abs(rect.y1 - left.y1) < size / 2
                    and 0 <= rect.x0 - left.x1 + 1 < (clip.width if clip else 2 * size)
                ):
                    joint = "" if _wide(before[-1]) and _wide(text[0]) else " "
                    found[-1] = (left | rect, max(size, left_size), before + joint + text)
                    continue
            found.append((rect, size, text))
    return found


def _paragraphs(
    lines: list[tuple[pymupdf.Rect, float, str]], right: float
) -> list[tuple[pymupdf.Rect, str, bool]]:
    """Rejoin lines a PDF wrapped at `right`; each paragraph keeps its first line's box.

    A line wraps into the next when the next line's first word would not have fit
    before the right edge, the line ends no sentence or clause, it sits directly above
    a next line of the same size, indent or centre, and the next opens no list item.
    The flag marks a paragraph set off by vertical space, as layout text shows with a
    blank line.
    """
    paragraphs: list[tuple[pymupdf.Rect, str, bool]] = []
    previous: tuple[pymupdf.Rect, float, str] | None = None
    for rect, size, line in lines:
        spaced = False
        if previous is not None:
            above, height, before = previous
            below = above.y1 - height / 2 <= rect.y0
            spaced = rect.y0 > above.y1 + height / 2
            wrapped = (
                right - above.x1 < max(2 * height, _leading_width(line, size))
                and before[-1] not in LINE_END
                and below
                and rect.y0 <= above.y1 + height
                and (
                    rect.x0 <= above.x0 + 2 * height
                    or abs(rect.x0 + rect.x1 - above.x0 - above.x1) < 2 * height
                )
                and abs(size - height) < 1
                and not LIST_MARKER.match(line)
            )
            if wrapped:
                start, text, gap = paragraphs[-1]
                joint = "" if _wide(text[-1]) and _wide(line[0]) else " "
                paragraphs[-1] = (start, text + joint + line, gap)
                previous = rect, size, line
                continue
        paragraphs.append((rect, line, spaced))
        previous = rect, size, line
    return paragraphs


def _cell_text(page: pymupdf.Page, cell: tuple | None) -> str:
    if cell is None:
        return ""
    rect = pymupdf.Rect(cell)
    return "\n".join(text for _, text, _ in _paragraphs(_lines(page, rect), rect.x1))


def _leading_width(line: str, size: float) -> float:
    """Rough width of the line's unbreakable start, narrow characters taken as half wide."""
    word = LEADING_WORD.match(line)
    return sum(size if _wide(char) else size / 2 for char in word[0]) if word else size


def _wide(char: str) -> bool:
    return unicodedata.east_asian_width(char) in "WFA"


def page_text(page: pymupdf.Page) -> str:
    """Native text in paragraphs, each ruled table one row per line with tab-separated cells.

    Layout-sorted text pads table columns into one visual line and breaks every line
    at its wrap, so a model reading it cites single-line fragments. Rows are rebuilt
    from cells and wrapped lines rejoined, inside and outside tables. A page whose
    rebuilt text would not hold exactly the same characters keeps the layout-sorted text.
    """
    layout = cast(str, page.get_text(sort=True))
    tables = cast(Any, page.find_tables()).tables
    cells = [
        pymupdf.Rect(cell) for table in tables for row in table.rows for cell in row.cells if cell
    ]
    outside = [
        line
        for line in _lines(page)
        if not any(
            pymupdf.Point((line[0].x0 + line[0].x1) / 2, (line[0].y0 + line[0].y1) / 2) in cell
            for cell in cells
        )
    ]
    right = max((rect.x1 for rect, _, _ in outside), default=0.0)
    parts = [
        (rect.y0, rect.x0, text, "\n\n" if spaced else "\n")
        for rect, text, spaced in _paragraphs(outside, right)
    ]
    for table in tables:
        rows = ["\t".join(_cell_text(page, cell) for cell in row.cells) for row in table.rows]
        box = pymupdf.Rect(table.bbox)
        parts.append((box.y0, box.x0, "\n".join(row for row in rows if row.strip()), "\n\n"))
    text = "".join(
        separator + part for _, _, part, separator in sorted(parts, key=lambda part: part[:2])
    ).lstrip("\n")
    if Counter("".join(layout.split())) != Counter("".join(text.split())):
        return layout
    return text


def read_page(document: pymupdf.Document, index: int) -> tuple[str, bytes | None, list[str]]:
    page = document[index]
    text = page_text(page)
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
