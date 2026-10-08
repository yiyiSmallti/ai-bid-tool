"""Bounded Gaussian presence derivative using the installed MuPDF raster codec.

The coarse sampling and Gaussian kernel intentionally discard text detail. Human
review is still mandatory: a numerical proxy cannot certify arbitrary page privacy.
"""

import hashlib
import math

import pymupdf

PROFILE = {
    "version": "presence-gaussian-v1",
    "max_side_px": 256,
    "sigma_px": 4.0,
    "radius_px": 12,
    "jpeg_quality": 80,
    "metadata": "removed",
}


def derivative(content: bytes) -> tuple[bytes, dict]:
    if not content.startswith(b"\x89PNG\r\n\x1a\n") or len(content) > 32 * 1024 * 1024:
        raise ValueError("bounded source PNG required")
    source = pymupdf.Pixmap(content)
    if source.width * source.height > 16_000_000:
        raise ValueError("source raster exceeds pixel bound")
    if source.alpha:
        source = pymupdf.Pixmap(source, 0)
    if source.n != 3:
        source = pymupdf.Pixmap(pymupdf.csRGB, source)
    scale = min(1.0, PROFILE["max_side_px"] / max(source.width, source.height))
    raster = pymupdf.Pixmap(
        source, max(1, int(source.width * scale)), max(1, int(source.height * scale))
    )
    width, height = raster.width, raster.height
    values = raster.samples
    radius, sigma = PROFILE["radius_px"], PROFILE["sigma_px"]
    weights = [math.exp(-(i * i) / (2 * sigma * sigma)) for i in range(-radius, radius + 1)]
    total = sum(weights)
    kernel = [weight / total for weight in weights]
    horizontal = bytearray(len(values))
    blurred = bytearray(len(values))
    for y in range(height):
        for x in range(width):
            for channel in range(3):
                horizontal[(y * width + x) * 3 + channel] = round(
                    sum(
                        values[(y * width + min(width - 1, max(0, x + i - radius))) * 3 + channel]
                        * weight
                        for i, weight in enumerate(kernel)
                    )
                )
    for y in range(height):
        for x in range(width):
            for channel in range(3):
                blurred[(y * width + x) * 3 + channel] = round(
                    sum(
                        horizontal[
                            (min(height - 1, max(0, y + i - radius)) * width + x) * 3 + channel
                        ]
                        * weight
                        for i, weight in enumerate(kernel)
                    )
                )
    output = pymupdf.Pixmap(pymupdf.csRGB, width, height, bytes(blurred), False)
    jpeg = output.tobytes("jpeg", jpg_quality=PROFILE["jpeg_quality"])
    if len(jpeg) > 4 * 1024 * 1024:
        raise ValueError("presence JPEG exceeds image limit")
    return jpeg, {
        "sha256": hashlib.sha256(jpeg).hexdigest(),
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(jpeg),
        "width_px": width,
        "height_px": height,
        "blur": dict(PROFILE),
    }
