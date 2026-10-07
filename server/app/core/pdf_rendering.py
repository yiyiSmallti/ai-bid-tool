"""Pure PDF operations executed by the resource-limited PDF child process."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime

import pymupdf
import pymupdf.mupdf as mupdf

from app.core.errors import ServiceError
from app.core.pdf_raster import MAX_EDGE_PX, MAX_PIXELS, raster_dimensions

ZOOM_DPI = {1: 110, 2: 200}
MAX_CERTIFICATE_BYTES = 40 * 1024 * 1024
PDF_INPUT_ERRORS = (
    ValueError,
    pymupdf.FileDataError,
    mupdf.FzErrorArgument,
    mupdf.FzErrorFormat,
    mupdf.FzErrorSyntax,
    mupdf.FzErrorUnsupported,
)


def _image(png: bytes) -> str:
    return base64.b64encode(png).decode("ascii")


def _preview(content: bytes, arguments: dict) -> dict:
    page_number = arguments["page_number"]
    dpi = ZOOM_DPI.get(arguments["zoom"])
    if dpi is None:
        raise ServiceError("invalid_input", "Zoom must be 1 or 2", 422, 2)
    try:
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if page_number < 1 or page_number > pdf.page_count:
                raise ServiceError("invalid_page", "Page is outside the document", 400, 2)
            page = pdf[page_number - 1]
            raster_dimensions(
                page.rect.width,
                page.rect.height,
                dpi / 72,
                code="preview_limits",
            )
            png = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False).tobytes("png")
    except ServiceError:
        raise
    except PDF_INPUT_ERRORS as exc:
        raise ServiceError("preview_render_failed", "Cannot render the page", 422, 2) from exc
    if len(png) > arguments["max_png_bytes"]:
        raise ServiceError("preview_limits", "Preview exceeds file limit", 413, 2)
    return {"image": _image(png)}


def _page_count(content: bytes, arguments: dict) -> dict:
    try:
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if not pdf.is_pdf or pdf.needs_pass or pdf.is_encrypted:
                raise ValueError("not a plain PDF")
            count = pdf.page_count
    except PDF_INPUT_ERRORS as exc:
        raise ServiceError(
            "converter_invalid_output", "Converter returned an unreadable PDF", 502, 4
        ) from exc
    if count < 1 or count > arguments["max_pages"]:
        raise ServiceError("preview_too_large", "Converted PDF has too many pages", 413, 4)
    return {"page_count": count}


def _safe_pdf_name(name: object) -> bool:
    return (
        isinstance(name, str)
        and 1 <= len(name) <= 200
        and bool(name.strip())
        and name.lower().endswith(".pdf")
        and not any(char in name for char in "/\\")
        and not any(ord(char) < 32 or ord(char) == 127 for char in name)
    )


def _invalid_certificate_file() -> ServiceError:
    return ServiceError(
        "invalid_certificate_file",
        "File must be a readable unencrypted PDF within file/page limits",
        400,
        2,
    )


def _evidence(content: bytes, arguments: dict) -> dict:
    original = arguments["original"]
    if (
        len(content) != original["size_bytes"]
        or hashlib.sha256(content).hexdigest() != original["sha256"]
    ):
        raise ServiceError("source_original_integrity", "Original failed integrity checks", 502, 4)
    try:
        if (
            not content
            or len(content) > MAX_CERTIFICATE_BYTES
            or not content.lstrip().startswith(b"%PDF-")
            or not _safe_pdf_name(original.get("name"))
        ):
            raise ValueError("invalid PDF")
        pdf = pymupdf.open(stream=content, filetype="pdf")
    except PDF_INPUT_ERRORS as exc:
        raise _invalid_certificate_file() from exc
    with pdf:
        try:
            if (
                not pdf.is_pdf
                or pdf.is_repaired
                or pdf.is_encrypted
                or pdf.needs_pass
                or pdf.xref_get_key(-1, "Encrypt")[0] != "null"
                or not 1 <= len(pdf) <= 200
            ):
                raise ValueError("unsupported PDF")
            for candidate in pdf:
                if candidate.rect.is_empty:
                    raise ValueError("invalid page")
        except PDF_INPUT_ERRORS as exc:
            raise _invalid_certificate_file() from exc

        actual = {
            "name": original["name"],
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "media_type": "application/pdf",
            "page_count": len(pdf),
        }
        if actual != original:
            raise ServiceError("source_original_integrity", "Original descriptor changed", 502, 4)
        page_number = arguments["page_number"]
        if page_number < 1 or page_number > actual["page_count"]:
            raise ServiceError("invalid_source_page", "Page is outside original PDF", 400, 2)
        try:
            page = pdf[page_number - 1]
            raster_dimensions(
                page.rect.width,
                page.rect.height,
                150 / 72,
                code="source_preview_limits",
            )
            pixmap = page.get_pixmap(dpi=150, colorspace=pymupdf.csRGB, alpha=False)
            if (
                max(pixmap.width, pixmap.height) > MAX_EDGE_PX
                or pixmap.width * pixmap.height > MAX_PIXELS
            ):
                raise ServiceError(
                    "source_preview_limits", "Page exceeds preview pixel limits", 400, 2
                )
            png = pixmap.tobytes("png")
        except ServiceError:
            raise
        except PDF_INPUT_ERRORS as exc:
            raise ServiceError(
                "source_render_failed", "Cannot render original page", 400, 2
            ) from exc
    if len(png) > min(arguments["max_preview_bytes"], arguments["max_bytes"]):
        raise ServiceError("source_preview_limits", "Preview exceeds file limit", 413, 2)
    return {
        "image": _image(png),
        "preview": {
            "name": arguments["name"],
            "sha256": hashlib.sha256(png).hexdigest(),
            "size_bytes": len(png),
            "media_type": "image/png",
            "width_px": pixmap.width,
            "height_px": pixmap.height,
        },
        "rendered_at": datetime.now(UTC).isoformat(),
    }


def _attachment_validate(content: bytes, arguments: dict) -> dict:
    # Reuse the established validator only inside this resource-limited child.
    from app.core.pdf_files import validate_file

    descriptor = validate_file(content, arguments["name"])
    return descriptor.model_dump(mode="json")


def run(operation: str, content: bytes, arguments: dict) -> dict:
    """Dispatch one operation after the child process has installed resource limits."""
    handlers = {
        "preview": _preview,
        "page_count": _page_count,
        "evidence": _evidence,
        "attachment_validate": _attachment_validate,
    }
    try:
        handler = handlers[operation]
    except KeyError:
        raise ServiceError("invalid_pdf_operation", "Unknown PDF operation", 500, 4) from None
    return handler(content, arguments)
