import asyncio
import binascii
import hashlib
import os
import stat
import struct
import time
import zlib
from pathlib import Path

import pymupdf
import pytest
from app.providers.base import ProviderFailure
from app.providers.screenshot_renderer import ScreenshotRenderer, validate_png


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = binascii.crc32(kind)
    checksum = binascii.crc32(payload, checksum) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _rgb_png(width: int, height: int, pixels: list[tuple[int, int, int]]) -> bytes:
    assert len(pixels) == width * height
    rows = []
    for y in range(height):
        row = b"".join(bytes(pixel) for pixel in pixels[y * width : (y + 1) * width])
        rows.append(b"\x00" + row)
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)),
            _png_chunk(b"tEXt", b"synthetic-metadata\x00must-be-removed"),
            _png_chunk(b"IDAT", zlib.compress(b"".join(rows))),
            _png_chunk(b"IEND", b""),
        )
    )


def _unfilter_rgb_png(content: bytes) -> tuple[int, int, list[tuple[int, int, int]]]:
    descriptor = validate_png(content)
    width = descriptor["width_px"]
    height = descriptor["height_px"]
    offset = 8
    compressed = bytearray()
    while offset < len(content):
        size = struct.unpack(">I", content[offset : offset + 4])[0]
        kind = content[offset + 4 : offset + 8]
        payload = content[offset + 8 : offset + 8 + size]
        offset += size + 12
        if kind == b"IDAT":
            compressed.extend(payload)
        if kind == b"IEND":
            break
    raw = zlib.decompress(bytes(compressed))
    stride = width * 3
    rows: list[bytearray] = []
    cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        source = raw[cursor + 1 : cursor + 1 + stride]
        cursor += stride + 1
        row = bytearray(stride)
        previous = rows[-1] if rows else bytearray(stride)
        for index, value in enumerate(source):
            left = row[index - 3] if index >= 3 else 0
            above = previous[index]
            upper_left = previous[index - 3] if index >= 3 else 0
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            elif filter_type == 4:
                estimate = left + above - upper_left
                distances = (
                    abs(estimate - left),
                    abs(estimate - above),
                    abs(estimate - upper_left),
                )
                predictor = (left, above, upper_left)[distances.index(min(distances))]
            else:
                raise AssertionError(f"unexpected PNG filter {filter_type}")
            row[index] = (value + predictor) & 0xFF
        rows.append(row)
    pixels = [tuple(row[index : index + 3]) for row in rows for index in range(0, len(row), 3)]
    return width, height, pixels  # type: ignore[return-value]


def _renderer_binary() -> Path:
    configured = os.environ.get("BID_SCREENSHOT_RENDERER")
    if configured:
        return Path(configured)
    repository = Path(__file__).resolve().parents[2]
    return repository / "data" / "work" / "bin" / "bid-screenshot-renderer"


@pytest.fixture(scope="module")
def renderer() -> ScreenshotRenderer:
    binary = _renderer_binary()
    if not binary.is_file():
        pytest.skip(f"build the Rust renderer first: {binary}")
    return ScreenshotRenderer(binary=binary)


@pytest.mark.asyncio
async def test_real_renderer_applies_redaction_crop_and_inner_boxes(renderer):
    source_pixels = [
        ((x * 29) % 256, (y * 47) % 256, ((x + y) * 17) % 256) for y in range(6) for x in range(8)
    ]
    source = _rgb_png(8, 6, source_pixels)
    request = {
        "profile": "screenshot-privacy-v1",
        "plan": {
            "redact": [{"x": 2, "y": 1, "width": 3, "height": 3}],
            "crop": {"x": 1, "y": 1, "width": 6, "height": 4},
            "boxes": [{"x": 4, "y": 2, "width": 2, "height": 2}],
        },
        "provenance": {},
    }

    output, rendering = await renderer.render(source, request)

    width, height, pixels = _unfilter_rgb_png(output)
    assert (width, height) == (6, 4)
    assert rendering["mapping"] == {
        "crop": {"x": 1, "y": 1, "width": 6, "height": 4},
        "content_offset_x": 0,
        "content_offset_y": 0,
        "content_width": 6,
        "content_height": 4,
        "footer_height": 0,
    }
    assert rendering["source_width"] == 8
    assert rendering["source_height"] == 6
    assert pixels[0] == source_pixels[1 + 8]
    assert pixels[1] == (0, 0, 0)
    assert pixels[3 + 6] == (255, 0, 0)
    assert pixels[4 + 2 * 6] == (255, 0, 0)
    assert b"tEXt" not in output
    assert rendering["image"] == validate_png(output)


@pytest.mark.asyncio
async def test_real_renderer_profiles_are_deterministic_and_markup_is_visible(renderer):
    source = _rgb_png(16, 10, [(240, 240, 240)] * 160)
    clean_request = {
        "profile": "prototype-clean-v1",
        "plan": {"redact": [], "crop": None, "boxes": []},
        "provenance": {},
    }
    empty_plan_hash = hashlib.sha256(b'{"boxes":[],"crop":null,"redact":[]}').hexdigest()
    markup_request = {
        "profile": "screenshot-markup-v1",
        "plan": {"redact": [], "crop": None, "boxes": []},
        "provenance": {
            "source_kind": "browser_capture",
            # This is the archived original-source hash, deliberately different
            # from the already privacy-processed PNG passed to the renderer.
            "source_sha256": "1" * 64,
            "plan_sha256": empty_plan_hash,
            "source_time_kind": "unknown",
            "source_time": None,
            "source_id": None,
        },
    }

    first_clean, clean_rendering = await renderer.render(source, clean_request)
    second_clean, _ = await renderer.render(source, clean_request)
    first_markup, markup_rendering = await renderer.render(source, markup_request)
    second_markup, second_rendering = await renderer.render(source, markup_request)

    assert first_clean == second_clean
    assert clean_rendering["mapping"]["footer_height"] == 0
    assert clean_rendering["image"]["width_px"] == 16
    assert clean_rendering["image"]["height_px"] == 10
    assert first_markup == second_markup
    assert markup_rendering == second_rendering
    assert markup_rendering["mapping"]["footer_height"] > 0
    assert markup_rendering["mapping"]["content_offset_x"] == (1024 - 16) // 2
    width, height, pixels = _unfilter_rgb_png(first_markup)
    assert width == 1024
    assert height == 10 + markup_rendering["mapping"]["footer_height"]
    footer = pixels[10 * width :]
    assert any(pixel != (255, 255, 255) for pixel in footer)


@pytest.mark.asyncio
async def test_real_renderer_normalizes_jpeg_exif_before_coordinates(renderer):
    png = _rgb_png(
        2,
        3,
        [
            (255, 0, 0),
            (0, 255, 0),
            (0, 0, 255),
            (255, 255, 0),
            (255, 0, 255),
            (0, 255, 255),
        ],
    )
    pixmap = pymupdf.Pixmap(png)
    jpeg = pixmap.tobytes("jpeg", jpg_quality=100)
    tiff = b"II*\x00\x08\x00\x00\x00\x01\x00\x12\x01\x03\x00\x01\x00\x00\x00\x06\x00\x00\x00\x00\x00\x00\x00"
    exif = b"Exif\x00\x00" + tiff
    oriented_jpeg = jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + jpeg[2:]

    output, rendering = await renderer.render(
        oriented_jpeg,
        {
            "profile": "screenshot-privacy-v1",
            "plan": {"redact": [], "crop": None, "boxes": []},
            "provenance": {},
        },
    )

    assert (rendering["source_width"], rendering["source_height"]) == (3, 2)
    assert (validate_png(output)["width_px"], validate_png(output)["height_px"]) == (3, 2)


@pytest.mark.asyncio
async def test_real_renderer_rejects_coordinates_instead_of_clipping(renderer):
    source = _rgb_png(4, 4, [(255, 255, 255)] * 16)

    with pytest.raises(ProviderFailure, match="rectangle"):
        await renderer.render(
            source,
            {
                "profile": "screenshot-privacy-v1",
                "plan": {
                    "redact": [],
                    "crop": {"x": 0, "y": 0, "width": 3, "height": 3},
                    "boxes": [{"x": 2, "y": 2, "width": 2, "height": 2}],
                },
                "provenance": {},
            },
        )


@pytest.mark.asyncio
async def test_provider_times_out_and_reaps_the_subprocess(tmp_path):
    pid_file = tmp_path / "renderer.pid"
    executable = tmp_path / "slow-renderer"
    executable.write_text(f"#!/bin/sh\nprintf %s $$ > {pid_file!s}\nexec /bin/sleep 10\n")
    executable.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    provider = ScreenshotRenderer(binary=executable, timeout_seconds=3.0)
    source = _rgb_png(1, 1, [(0, 0, 0)])
    started = time.monotonic()

    with pytest.raises(ProviderFailure, match="timed out"):
        await provider.render(
            source,
            {
                "profile": "screenshot-privacy-v1",
                "plan": {"redact": [], "crop": None, "boxes": []},
                "provenance": {},
            },
        )

    assert time.monotonic() - started < 5
    for _ in range(20):
        if pid_file.exists():
            break
        await asyncio.sleep(0.01)
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
