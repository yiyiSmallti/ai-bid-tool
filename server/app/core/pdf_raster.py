"""Allocation limits shared by every renderer of untrusted PDF pages."""

import math

from app.core.errors import ServiceError

MAX_EDGE_PX = 8192
MAX_PIXELS = 20_000_000
OCR_DPI = 180
MIN_OCR_DPI = 72


def raster_dimensions(
    width: float, height: float, scale: float, *, code: str = "pdf_raster_limits"
) -> tuple[int, int]:
    """Refuse the projected allocation, including outward pixel rounding, before rendering."""
    error = ServiceError(
        code,
        "PDF page exceeds safe raster limits (20 million pixels, 8192 pixels per edge); "
        "resize or split the page and upload it again",
        400,
        2,
    )
    if any(not math.isfinite(value) or value <= 0 for value in (width, height, scale)):
        raise error
    projected = (width * scale, height * scale)
    if any(not math.isfinite(value) or value > MAX_EDGE_PX for value in projected):
        raise error
    pixels = (math.ceil(projected[0]), math.ceil(projected[1]))
    if pixels[0] * pixels[1] > MAX_PIXELS:
        raise error
    return pixels


def ocr_dpi(width: float, height: float) -> int:
    """Use the highest integer OCR DPI in budget, refusing unreadably large pages."""
    raster_dimensions(width, height, MIN_OCR_DPI / 72)
    dpi = min(
        OCR_DPI,
        math.floor(72 * MAX_EDGE_PX / max(width, height)),
        math.floor(72 * math.sqrt(MAX_PIXELS / (width * height))),
    )
    # The area formula omits outward rounding at the two raster edges.
    while math.ceil(width * dpi / 72) * math.ceil(height * dpi / 72) > MAX_PIXELS:
        dpi -= 1
    raster_dimensions(width, height, dpi / 72)
    return dpi
