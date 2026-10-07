"""Bounded B05 protocol over the existing standalone screenshot renderer."""

import asyncio
import binascii
import hashlib
import json
import os
import struct
import zlib
from typing import Any

import pymupdf
from pydantic import ValidationError

from app.providers.annotation_process import (
    CONFIGURATION_EXIT,
    AnnotationProcessUnavailable,
    launch_arguments,
)
from app.providers.base import ProviderFailure
from app.providers.screenshot_renderer import (
    MAX_IMAGE_BYTES,
    MAX_REQUEST_BYTES,
    ScreenshotRenderer,
    validate_png,
)
from app.schemas.annotation_contracts import (
    AnnotationApprovalBinding,
    AnnotationCanvas,
    AnnotationRendererIdentity,
    AnnotationRendering,
    AnnotationRenderRequest,
)
from app.schemas.screenshot_contracts import ContentMapping, PixelRect

VERSION = "0.1.0"
FONT_BUNDLE_SHA256 = "43e5b1d50225fc87b07bddb01d87416d4eb445aa085682688cae710344f33c23"


def _failure(
    message: str, *, retryable: bool = False, code: str = "annotation_renderer_failure"
) -> ProviderFailure:
    return ProviderFailure(message, code=code, retryable=retryable)


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(_dump(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def approval_binding_sha256(approval: AnnotationApprovalBinding | dict[str, Any]) -> str:
    normalized = dict(_dump(approval))
    normalized.pop("approval_binding_sha256", None)
    return canonical_sha256(normalized)


def _crop(request: AnnotationRenderRequest) -> PixelRect:
    authorized = request.source.authorized_content
    return request.plan.crop or PixelRect(
        x=0, y=0, width=authorized.width, height=authorized.height
    )


def root_mapping_sha256(source: Any, crop: PixelRect | dict[str, int]) -> str:
    source = _dump(source)
    crop = dict(_dump(crop))
    vendor = source["kind"] == "vendor_rendition"
    if vendor:
        parent = source["source_crop_mapping"]["crop"]
        crop["x"] += parent["x"]
        crop["y"] += parent["y"]
    return canonical_sha256(
        {
            "source_kind": source["kind"],
            "root_source_png_sha256": source["root_source_png_sha256"]
            if vendor
            else source["source_png"]["sha256"],
            "source_root_mapping_sha256": source["root_mapping_sha256"] if vendor else None,
            "crop": crop,
        }
    )


def footer_pairs(request: AnnotationRenderRequest | dict[str, Any]) -> list[list[str]]:
    """Only server-normalized, fixed fields can become visible footer text."""
    normalized: dict[str, Any] = _dump(request)
    source = normalized["source"]
    archive = source["archive"]
    certificate = source["kind"] == "certificate_page"
    release = normalized.get("approval") is not None
    renderer = normalized["renderer"]
    status = "CONFIRMED" if release else "UNCONFIRMED"
    label = "USER-SUPPLIED CERTIFICATE PAGE" if certificate else "ARCHIVED VENDOR PAGE"
    pairs = [
        ["STATUS", f"{status} {label}"],
        ["SOURCE_KIND", archive["source_kind"] if certificate else "archived_vendor_page"],
        ["SOURCE_ID", archive["id"] if certificate else source["asset_id"]],
        ["TASK_ID", archive["task_id"] if certificate else source["task_id"]],
        ["SELECTION_ID", source["selection_id"]],
        ["SOURCE_REVISION_ID", source["resource_revision_id"]],
        [
            "SOURCE_FILE_ID" if certificate else "VENDOR_ARCHIVE_ID",
            archive["certificate_file_id"] if certificate else archive["id"],
        ],
        ["PAGE_KIND", source["page_kind"] if certificate else source["page"]["kind"]],
        ["PAGE", str(archive["page"] if certificate else source["page"]["page"])],
        ["DPI", "150" if certificate else "NOT_APPLICABLE"],
        ["SOURCE_PROFILE", archive["render_profile"] if certificate else source["source_profile"]],
        ["SOURCE_TIME_KIND", source["source_time_kind"]],
        ["SOURCE_TIME", source["source_time"]],
        ["ORIGINAL_SHA256", source["original_sha256"]],
        ["SOURCE_PNG_SHA256", source["source_png"]["sha256"]],
        ["PLAN_SHA256", normalized["plan_sha256"]],
        ["RENDERER_PROFILE", renderer["profile"]],
        ["RENDERER_VERSION", renderer["version"]],
    ]
    if release:
        pairs.append(["APPROVAL_SHA256", normalized["approval"]["approval_binding_sha256"]])
    return pairs


def provenance_sha256(request: AnnotationRenderRequest | dict[str, Any]) -> str:
    normalized = _dump(request)
    return canonical_sha256(
        {"footer": footer_pairs(normalized), "renderer": normalized["renderer"]}
    )


def _rgb(content: bytes, *, allow_input_metadata: bool = False) -> tuple[int, int, bytes]:
    # Validate framing, CRC, dimensions and a bounded decompression before handing
    # the PNG to the existing native decoder. It has no filesystem/network access.
    try:
        descriptor = validate_png(content, allow_input_metadata=allow_input_metadata)
    except ProviderFailure as exc:
        raise _failure("Annotation PNG violates the fixed RGB8 PNG contract") from exc
    try:
        pixmap = pymupdf.Pixmap(content)
    except (RuntimeError, ValueError) as exc:
        raise _failure("Annotation PNG pixels cannot be decoded") from exc
    if (
        pixmap.alpha
        or pixmap.n != 3
        or (pixmap.width, pixmap.height) != (descriptor["width_px"], descriptor["height_px"])
    ):
        raise _failure("Annotation PNG must preserve opaque RGB8 pixels")
    return pixmap.width, pixmap.height, pixmap.samples


def content_pixel_sha256(width: int, height: int, pixels: bytes) -> str:
    if len(pixels) != width * height * 3:
        raise _failure("Annotation RGB content length differs from its dimensions")
    digest = hashlib.sha256(b"annotation-rgb-v1\n" + struct.pack(">II", width, height))
    digest.update(pixels)
    return digest.hexdigest()


def decoded_content_sha256(content: bytes) -> str:
    width, height, pixels = _rgb(content)
    return content_pixel_sha256(width, height, pixels)


def _extract(width: int, height: int, pixels: bytes, rect: PixelRect) -> bytes:
    if not rect.within(width, height):
        raise _failure("Annotation content rectangle exceeds PNG bounds")
    stride = width * 3
    return b"".join(
        pixels[y * stride + rect.x * 3 : y * stride + (rect.x + rect.width) * 3]
        for y in range(rect.y, rect.y + rect.height)
    )


def _chunk(kind: bytes, value: bytes) -> bytes:
    crc = binascii.crc32(value, binascii.crc32(kind)) & 0xFFFFFFFF
    return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", crc)


def strip_candidate_content(content: bytes, mapping: ContentMapping | dict[str, Any]) -> bytes:
    """Strip generated margins only, preserving already annotated RGB pixels."""
    mapping = ContentMapping.model_validate(_dump(mapping))
    width, height, pixels = _rgb(content)
    if (
        mapping.content_offset_y != 0
        or width != max(mapping.content_width, 1024)
        or mapping.content_offset_x != (width - mapping.content_width) // 2
        or height != mapping.content_height + mapping.footer_height
        or mapping.footer_height <= 0
        or (mapping.content_width, mapping.content_height)
        != (mapping.crop.width, mapping.crop.height)
    ):
        raise _failure("Candidate mapping cannot identify its exact generated margins")
    rect = PixelRect(
        x=mapping.content_offset_x,
        y=0,
        width=mapping.content_width,
        height=mapping.content_height,
    )
    marked = _extract(width, height, pixels, rect)
    stride = rect.width * 3
    raw = b"".join(b"\x00" + marked[y * stride : (y + 1) * stride] for y in range(rect.height))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", rect.width, rect.height, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )
    validate_png(png)
    return png


class AnnotationRenderer(ScreenshotRenderer):
    """Independent request/receipt verification for B05 over the same binary."""

    def _validated_binary(self):
        try:
            return super()._validated_binary()
        except ProviderFailure as exc:
            raise _failure(
                "Annotation renderer executable is unavailable or untrusted",
                code="annotation_renderer_unavailable",
            ) from exc

    def _process_arguments(self, *, describe: bool = False) -> list[str]:
        """Production always enters the fail-closed Linux process sandbox."""
        try:
            return launch_arguments(self._validated_binary(), describe=describe)
        except AnnotationProcessUnavailable as exc:
            raise _failure(
                "Annotation renderer process sandbox is unavailable",
                code=exc.code,
            ) from exc

    def identity(self, profile: str = "annotation-candidate-v1") -> AnnotationRendererIdentity:
        if profile not in {"annotation-candidate-v1", "annotation-release-v1"}:
            raise _failure("Unsupported annotation rendering profile", code="unsupported_protocol")
        binary = self._validated_binary()
        try:
            binary_sha256 = hashlib.sha256(binary.read_bytes()).hexdigest()
        except OSError as exc:
            raise _failure(
                "Annotation renderer build could not be identified",
                code="annotation_renderer_unavailable",
            ) from exc
        return AnnotationRendererIdentity(
            profile="annotation-candidate-v1"
            if profile == "annotation-candidate-v1"
            else "annotation-release-v1",
            version=VERSION,
            binary_sha256=binary_sha256,
            font_bundle_sha256=FONT_BUNDLE_SHA256,
        )

    def _request(self, request: AnnotationRenderRequest) -> AnnotationRenderRequest:
        normalized = _dump(request)
        if isinstance(normalized, dict):
            identity = normalized.get("renderer")
            if normalized.get("protocol") != "annotation-render-v1" or (
                isinstance(identity, dict)
                and identity.get("protocol_version") != "annotation-render-v1"
            ):
                raise _failure(
                    "Annotation renderer protocol is unsupported", code="unsupported_protocol"
                )
        try:
            request = AnnotationRenderRequest.model_validate(normalized)
        except (ValueError, TypeError, ValidationError) as exc:
            raise _failure("Annotation request differs from the fixed contract") from exc
        if request.renderer != self.identity(request.renderer.profile):
            raise _failure(
                "Annotation renderer build/font identity changed",
                code="annotation_renderer_unavailable",
            )
        if request.provenance_sha256 != provenance_sha256(request):
            raise _failure("Annotation provenance hash differs from fixed footer fields")
        crop = _crop(request)
        authorized = request.source.authorized_content
        if not crop.within(authorized.width, authorized.height):
            raise _failure(
                "Annotation crop exceeds authorized source content", code="output_limit_exceeded"
            )
        for box in request.plan.boxes:
            if (
                box.x < crop.x
                or box.y < crop.y
                or not box.within(crop.x + crop.width, crop.y + crop.height)
            ):
                raise _failure(
                    "Annotation box exceeds authorized crop", code="output_limit_exceeded"
                )
        if request.approval is not None:
            approval = request.approval
            if approval.approval_binding_sha256 != approval_binding_sha256(approval):
                raise _failure("Annotation approval hash differs from exact human decision")
            if approval.root_mapping_sha256 != root_mapping_sha256(request.source, crop):
                raise _failure("Annotation release source mapping differs from approval")
            if approval.candidate_content_mapping.crop != crop:
                raise _failure("Annotation release crop differs from approved candidate")
        return request

    async def predict(self, request: AnnotationRenderRequest) -> AnnotationCanvas:
        request = self._request(request)
        response, output = await self._exchange(b"", request, describe=True)
        if output:
            raise _failure("Read-only annotation prediction returned image bytes")
        try:
            canvas = AnnotationCanvas.model_validate(response)
        except ValidationError as exc:
            raise _failure("Annotation prediction returned an invalid canvas") from exc
        self._mapping(canvas, request)
        return canvas

    @staticmethod
    def _mapping(canvas: AnnotationCanvas, request: AnnotationRenderRequest) -> None:
        if canvas.mapping.crop != _crop(request):
            raise _failure("Annotation receipt mapping differs from requested crop")
        if request.approval is not None:
            candidate = request.approval.candidate_content_mapping.model_dump(
                exclude={"footer_height"}
            )
            if canvas.mapping.model_dump(exclude={"footer_height"}) != candidate:
                raise _failure("Annotation release moved approved content")

    async def render(
        self, content: bytes, request: AnnotationRenderRequest
    ) -> tuple[bytes, AnnotationRendering]:
        request = self._request(request)
        width, height, pixels = _rgb(content, allow_input_metadata=True)
        if hashlib.sha256(content).hexdigest() != request.input_content_sha256:
            raise _failure("Annotation input PNG hash differs from request")
        crop = _crop(request)
        if request.approval is None:
            if validate_png(
                content, allow_input_metadata=True
            ) != request.source.source_png.model_dump(mode="json"):
                raise _failure("Annotation input differs from archived authorized source")
            authorized = request.source.authorized_content
            expected = bytearray(
                _extract(
                    width,
                    height,
                    pixels,
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
                    row = ((box.y - crop.y + y) * crop.width + box.x - crop.x) * 3
                    if y < 2 or y >= box.height - 2:
                        expected[row : row + box.width * 3] = b"\xff\x00\x00" * box.width
                    else:
                        border = min(2, box.width) * 3
                        expected[row : row + border] = b"\xff\x00\x00" * min(2, box.width)
                        end = row + box.width * 3
                        expected[end - border : end] = b"\xff\x00\x00" * min(2, box.width)
            expected_pixels = bytes(expected)
        else:
            if (width, height) != (crop.width, crop.height):
                raise _failure("Annotation release dimensions differ from approved content")
            expected_pixels = pixels
        expected_digest = content_pixel_sha256(crop.width, crop.height, expected_pixels)
        if (
            request.approval is not None
            and expected_digest != request.approval.content_pixel_sha256
        ):
            raise _failure("Annotation release input differs from approved RGB content")
        response, output = await self._exchange(content, request)
        try:
            receipt = AnnotationRendering.model_validate(response)
        except ValidationError as exc:
            raise _failure("Annotation renderer returned an invalid receipt") from exc
        if (
            receipt.renderer != request.renderer
            or receipt.plan_sha256 != request.plan_sha256
            or receipt.provenance_sha256 != request.provenance_sha256
            or receipt.image.model_dump(mode="json") != validate_png(output)
            or receipt.root_mapping_sha256 != root_mapping_sha256(request.source, crop)
            or receipt.content_pixel_sha256 != expected_digest
        ):
            raise _failure("Annotation receipt differs from independently verified inputs")
        self._mapping(receipt.canvas, request)
        actual_width, actual_height, actual_pixels = _rgb(output)
        m = receipt.canvas.mapping
        extracted = _extract(
            actual_width,
            actual_height,
            actual_pixels,
            PixelRect(
                x=m.content_offset_x,
                y=m.content_offset_y,
                width=m.content_width,
                height=m.content_height,
            ),
        )
        if extracted != expected_pixels:
            raise _failure("Annotation output changed independently verified content pixels")
        # Rust is deterministic; compare its measured canvas against the metadata-only
        # operation so a malformed receipt cannot claim a different footer geometry.
        if receipt.canvas != await self.predict(request):
            raise _failure("Annotation rendered canvas differs from fixed-font prediction")
        return output, receipt

    async def _exchange(
        self, content: bytes, request: AnnotationRenderRequest, *, describe: bool = False
    ) -> tuple[dict[str, Any], bytes]:
        encoded = json.dumps(
            request.model_dump(mode="json"),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if len(encoded) > MAX_REQUEST_BYTES or len(content) > MAX_IMAGE_BYTES:
            raise _failure(
                "Annotation framed input exceeds fixed limits", code="output_limit_exceeded"
            )
        frame = struct.pack(">Q", len(encoded)) + encoded + content
        arguments = self._process_arguments(describe=describe)
        try:
            process = await asyncio.create_subprocess_exec(
                *arguments,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.fspath(self.binary.parent),
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
            )
        except OSError as exc:
            raise _failure(
                "Annotation renderer could not be started", code="annotation_renderer_unavailable"
            ) from exc
        assert (
            process.stdin is not None and process.stdout is not None and process.stderr is not None
        )

        async def framed_result(operation):
            try:
                return await operation
            except (BrokenPipeError, ConnectionResetError, asyncio.IncompleteReadError) as exc:
                # A rejecting binary closes its frames. Keep draining capped stderr
                # to classify its structured diagnostic without relying on message text.
                return exc

        tasks = [
            asyncio.create_task(framed_result(self._write_input(process.stdin, frame))),
            asyncio.create_task(framed_result(self._read_receipt(process.stdout, describe))),
            asyncio.create_task(self._read_diagnostics(process.stderr)),
        ]
        try:
            async with asyncio.timeout(self.timeout_seconds):
                input_result, result, diagnostics = await asyncio.gather(*tasks)
                status = await process.wait()
            if status == CONFIGURATION_EXIT:
                raise _failure(
                    "Annotation renderer process sandbox is unavailable",
                    code="annotation_sandbox_unavailable",
                )
            if status:
                raise self._rejection(diagnostics, describe=describe)
            if isinstance(input_result, Exception) or isinstance(result, Exception):
                raise _failure("Annotation renderer process returned an incomplete frame")
            return result
        except TimeoutError as exc:
            raise _failure("Annotation renderer timed out", retryable=True) from exc
        except (OSError, ValueError, struct.error, asyncio.IncompleteReadError) as exc:
            raise _failure("Annotation renderer process returned an invalid frame") from exc
        finally:
            if process.returncode is None:
                await self._terminate(process)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _rejection(diagnostics: bytes, *, describe: bool) -> ProviderFailure:
        try:
            diagnostic = json.loads(diagnostics)
        except (ValueError, UnicodeError):
            return _failure("Annotation renderer rejected the bound request")
        if (
            not isinstance(diagnostic, dict)
            or set(diagnostic) != {"code", "message"}
            or not isinstance(diagnostic["code"], str)
            or not isinstance(diagnostic["message"], str)
        ):
            return _failure("Annotation renderer returned an invalid diagnostic")
        # Describe has no image input or decoder allocation. Its invalid_image
        # diagnostic can therefore only identify expanded footer/canvas limits.
        if describe and diagnostic["code"] == "invalid_image":
            return _failure(
                "Annotation complete canvas exceeds fixed limits", code="output_limit_exceeded"
            )
        if describe and diagnostic["code"] == "invalid_request":
            return _failure(
                "Annotation renderer does not accept the pinned protocol",
                code="unsupported_protocol",
            )
        return _failure("Annotation renderer rejected the bound request")

    @staticmethod
    async def _read_receipt(
        stream: asyncio.StreamReader, describe: bool
    ) -> tuple[dict[str, Any], bytes]:
        size = struct.unpack(">Q", await stream.readexactly(8))[0]
        if size < 2 or size > MAX_REQUEST_BYTES:
            raise _failure("Annotation renderer receipt exceeds the fixed JSON limit")
        try:
            metadata = json.loads(await stream.readexactly(size))
        except (ValueError, UnicodeError) as exc:
            raise _failure("Annotation renderer returned malformed JSON") from exc
        if not isinstance(metadata, dict):
            raise _failure("Annotation renderer receipt must be an object")
        image = metadata.get("image")
        image_size = 0 if describe else image.get("size_bytes") if isinstance(image, dict) else None
        if (
            isinstance(image_size, bool)
            or not isinstance(image_size, int)
            or image_size < (0 if describe else 1)
            or image_size > MAX_IMAGE_BYTES
        ):
            raise _failure("Annotation renderer returned an invalid PNG size")
        output = await stream.readexactly(image_size)
        if await stream.read(1):
            raise _failure("Annotation renderer returned trailing output")
        return metadata, output
