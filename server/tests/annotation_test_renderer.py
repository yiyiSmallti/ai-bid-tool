"""CI-only deterministic byte-valid renderer for annotation lifecycle tests."""

import hashlib
import struct
import sys
import zlib

import pytest
from app.providers import annotation_renderer as protocol
from app.schemas.annotation_contracts import AnnotationCanvas, AnnotationRendererIdentity
from app.schemas.screenshot_contracts import PixelRect


def _encode(width, height, pixels):
    stride = width * 3
    raw = b"".join(b"\x00" + pixels[y * stride : (y + 1) * stride] for y in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + protocol._chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + protocol._chunk(b"IDAT", zlib.compress(raw))
        + protocol._chunk(b"IEND", b"")
    )


class FakeAnnotationRenderer(protocol.AnnotationRenderer):
    """Use actual RGB bytes/hashes; replace only fixed-font footer rasterization."""

    def identity(self, profile="annotation-candidate-v1"):
        return AnnotationRendererIdentity(
            profile=profile,
            version="test-renderer-v1",
            binary_sha256=hashlib.sha256(b"CI annotation renderer").hexdigest(),
            font_bundle_sha256=protocol.FONT_BUNDLE_SHA256,
        )

    async def predict(self, request):
        request = self._request(request)
        crop = protocol._crop(request)
        width = max(1024, crop.width)
        footer = 80 if request.approval is None else 104
        return AnnotationCanvas(
            width_px=width,
            height_px=crop.height + footer,
            mapping={
                "crop": crop.model_dump(),
                "content_offset_x": (width - crop.width) // 2,
                "content_offset_y": 0,
                "content_width": crop.width,
                "content_height": crop.height,
                "footer_height": footer,
            },
        )

    async def _exchange(self, content, request, *, describe=False):
        canvas = await self.predict(request)
        if describe:
            return canvas.model_dump(mode="json"), b""
        crop = protocol._crop(request)
        width, height, source = protocol._rgb(content, allow_input_metadata=True)
        if request.approval is None:
            authorized = request.source.authorized_content
            pixels = bytearray(
                protocol._extract(
                    width,
                    height,
                    source,
                    PixelRect(
                        x=authorized.x + crop.x,
                        y=authorized.y + crop.y,
                        width=crop.width,
                        height=crop.height,
                    ),
                )
            )
            for box in request.plan.boxes:
                for y in range(box.height):
                    for x in range(box.width):
                        if x < 2 or y < 2 or x >= box.width - 2 or y >= box.height - 2:
                            i = ((box.y - crop.y + y) * crop.width + box.x - crop.x + x) * 3
                            pixels[i : i + 3] = b"\xff\x00\x00"
        else:
            pixels = bytearray(source)
        output = bytearray(b"\xff" * (canvas.width_px * canvas.height_px * 3))
        for y in range(crop.height):
            target = (y * canvas.width_px + canvas.mapping.content_offset_x) * 3
            output[target : target + crop.width * 3] = pixels[
                y * crop.width * 3 : (y + 1) * crop.width * 3
            ]
        png = _encode(canvas.width_px, canvas.height_px, output)
        return {
            "renderer": request.renderer.model_dump(mode="json"),
            "plan_sha256": request.plan_sha256,
            "provenance_sha256": request.provenance_sha256,
            "image": protocol.validate_png(png),
            "canvas": canvas.model_dump(mode="json"),
            "content_pixel_sha256": protocol.content_pixel_sha256(
                crop.width, crop.height, bytes(pixels)
            ),
            "root_mapping_sha256": protocol.root_mapping_sha256(request.source, crop),
        }, png


def configure_renderer(monkeypatch, real=False):
    from app.services import annotations

    if real:
        if sys.platform != "linux":
            pytest.skip("production annotation sandbox acceptance requires supported Linux")
        renderer = protocol.AnnotationRenderer()
        if not renderer.binary.is_file():
            pytest.skip("production annotation sandbox acceptance requires installed renderer")
        # Linux deployment/configuration failures are real failures. Never replace
        # this path with the raw-protocol renderer used by portable pixel tests.
        renderer._process_arguments()
    else:
        renderer = FakeAnnotationRenderer()
    monkeypatch.setattr(annotations, "renderer_for", lambda processor=None: renderer)
    return renderer
