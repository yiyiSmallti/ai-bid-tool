import asyncio
import binascii
import hashlib
import json
import os
import stat
import struct
import zlib
from pathlib import Path
from typing import Any

from app.providers.base import ProviderFailure

MAX_IMAGE_BYTES = 40 * 1024 * 1024
MAX_DIMENSION = 8192
MAX_PIXELS = 20_000_000
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_DIAGNOSTIC_BYTES = 64 * 1024
MAX_REDACTIONS = 200
MAX_BOXES = 20
RENDER_TIMEOUT_SECONDS = 20.0
PROFILES = {
    "screenshot-privacy-v1",
    "screenshot-markup-v1",
    "prototype-clean-v1",
}


def _renderer_failure(message: str, *, retryable: bool = False) -> ProviderFailure:
    return ProviderFailure(
        message,
        retryable=retryable,
        code="screenshot_renderer_failure",
    )


def validate_png(content: bytes) -> dict[str, Any]:
    """Validate a complete renderer PNG and return its immutable descriptor."""
    if not isinstance(content, bytes) or not content:
        raise _renderer_failure("Screenshot renderer returned an empty image")
    if len(content) > MAX_IMAGE_BYTES:
        raise _renderer_failure("Screenshot renderer returned an image larger than 40 MiB")
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise _renderer_failure("Screenshot renderer returned a non-PNG image")

    offset = 8
    width = height = None
    compressed = bytearray()
    seen_ihdr = False
    seen_idat = False
    ended_idat = False
    seen_iend = False
    while offset < len(content):
        if len(content) - offset < 12:
            raise _renderer_failure("Screenshot renderer returned a truncated PNG")
        length = struct.unpack(">I", content[offset : offset + 4])[0]
        chunk_end = offset + 12 + length
        if chunk_end > len(content):
            raise _renderer_failure("Screenshot renderer returned a truncated PNG chunk")
        kind = content[offset + 4 : offset + 8]
        payload = content[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", content[offset + 8 + length : chunk_end])[0]
        actual_crc = binascii.crc32(kind)
        actual_crc = binascii.crc32(payload, actual_crc) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise _renderer_failure("Screenshot renderer returned a PNG with an invalid checksum")
        offset = chunk_end

        if kind == b"IHDR":
            if seen_ihdr or seen_idat or length != 13:
                raise _renderer_failure("Screenshot renderer returned an invalid PNG header")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            if depth != 8 or color != 2 or compression or filtering or interlace:
                raise _renderer_failure("Screenshot renderer must return a fixed RGB PNG")
            if (
                width < 1
                or height < 1
                or width > MAX_DIMENSION
                or height > MAX_DIMENSION
                or width * height > MAX_PIXELS
            ):
                raise _renderer_failure("Screenshot renderer returned invalid PNG dimensions")
            seen_ihdr = True
        elif kind == b"IDAT":
            if not seen_ihdr or ended_idat or seen_iend:
                raise _renderer_failure("Screenshot renderer returned invalid PNG chunk order")
            seen_idat = True
            compressed.extend(payload)
            if len(compressed) > MAX_IMAGE_BYTES:
                raise _renderer_failure("Screenshot renderer returned excessive PNG data")
        elif kind == b"IEND":
            if not seen_idat or seen_iend or length:
                raise _renderer_failure("Screenshot renderer returned an invalid PNG ending")
            seen_iend = True
            if offset != len(content):
                raise _renderer_failure("Screenshot renderer returned trailing PNG data")
        else:
            if seen_idat:
                ended_idat = True
            # The renderer emits only structural chunks. Rejecting all ancillary
            # chunks proves that input EXIF and text metadata did not survive.
            raise _renderer_failure("Screenshot renderer returned PNG metadata")

    if not seen_ihdr or not seen_idat or not seen_iend or width is None or height is None:
        raise _renderer_failure("Screenshot renderer returned an incomplete PNG")
    expected_raw_size = (width * 3 + 1) * height
    decompressor = zlib.decompressobj()
    try:
        raw = decompressor.decompress(bytes(compressed), expected_raw_size + 1)
    except zlib.error as exc:
        raise _renderer_failure("Screenshot renderer returned invalid PNG pixels") from exc
    if (
        len(raw) != expected_raw_size
        or not decompressor.eof
        or decompressor.unconsumed_tail
        or decompressor.unused_data
    ):
        raise _renderer_failure("Screenshot renderer returned an invalid PNG pixel stream")
    stride = width * 3 + 1
    if any(raw[row * stride] > 4 for row in range(height)):
        raise _renderer_failure("Screenshot renderer returned an invalid PNG filter")
    return {
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "width_px": width,
        "height_px": height,
        "media_type": "image/png",
    }


def _normalize_rect(value: Any, label: str) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != {"x", "y", "width", "height"}:
        raise _renderer_failure(f"{label} rectangle must contain x, y, width, and height")
    normalized: dict[str, int] = {}
    for key in ("x", "y", "width", "height"):
        item = value[key]
        if isinstance(item, bool) or not isinstance(item, int):
            raise _renderer_failure(f"{label} rectangle values must be integers")
        if key in {"x", "y"} and item < 0:
            raise _renderer_failure(f"{label} rectangle origin must be non-negative")
        if key in {"width", "height"} and item < 1:
            raise _renderer_failure(f"{label} rectangle size must be positive")
        if item > MAX_DIMENSION:
            raise _renderer_failure(f"{label} rectangle exceeds the dimension limit")
        normalized[key] = item
    if (
        normalized["x"] + normalized["width"] > MAX_DIMENSION
        or normalized["y"] + normalized["height"] > MAX_DIMENSION
    ):
        raise _renderer_failure(f"{label} rectangle endpoint exceeds the dimension limit")
    return normalized


def _contains(container: dict[str, int], item: dict[str, int]) -> bool:
    return (
        item["x"] >= container["x"]
        and item["y"] >= container["y"]
        and item["x"] + item["width"] <= container["x"] + container["width"]
        and item["y"] + item["height"] <= container["y"] + container["height"]
    )


def _normalize_request(request: dict[str, Any]) -> tuple[dict[str, Any], str]:
    if not isinstance(request, dict) or set(request) != {"plan", "profile", "provenance"}:
        raise _renderer_failure("Screenshot request must contain plan, profile, and provenance")
    profile = request["profile"]
    if not isinstance(profile, str) or profile not in PROFILES:
        raise _renderer_failure("Unsupported screenshot rendering profile")
    plan_value = request["plan"]
    if not isinstance(plan_value, dict) or not set(plan_value).issubset(
        {"redact", "crop", "boxes"}
    ):
        raise _renderer_failure("Screenshot plan contains unsupported fields")
    redactions_value = plan_value.get("redact", [])
    boxes_value = plan_value.get("boxes", [])
    crop_value = plan_value.get("crop")
    if not isinstance(redactions_value, list) or len(redactions_value) > MAX_REDACTIONS:
        raise _renderer_failure("Screenshot plan may contain at most 200 redaction rectangles")
    if not isinstance(boxes_value, list) or len(boxes_value) > MAX_BOXES:
        raise _renderer_failure("Screenshot plan may contain at most 20 box rectangles")
    redactions = [
        _normalize_rect(value, f"redact[{index}]") for index, value in enumerate(redactions_value)
    ]
    boxes = [_normalize_rect(value, f"boxes[{index}]") for index, value in enumerate(boxes_value)]
    crop = None if crop_value is None else _normalize_rect(crop_value, "crop")
    if crop is not None and any(not _contains(crop, box) for box in boxes):
        raise _renderer_failure("Each box rectangle must be completely inside the crop rectangle")
    provenance = request["provenance"]
    if not isinstance(provenance, dict) or any(not isinstance(key, str) for key in provenance):
        raise _renderer_failure("Screenshot provenance must be a dictionary")
    provenance_keys = {
        "source_kind",
        "source_sha256",
        "plan_sha256",
        "source_time_kind",
        "source_time",
        "source_id",
        "page",
    }
    if not set(provenance).issubset(provenance_keys):
        raise _renderer_failure("Screenshot provenance contains unsupported fields")
    for key, value in provenance.items():
        if key == "page":
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 1
            ):
                raise _renderer_failure("Screenshot provenance page must be a positive integer")
        elif value is not None and not isinstance(value, str):
            raise _renderer_failure("Screenshot provenance values must be strings")
    if profile == "screenshot-markup-v1":
        required_provenance = provenance_keys - {"page"}
        if not required_provenance.issubset(provenance):
            raise _renderer_failure("Markup screenshot provenance is incomplete")
        required_values = {
            "source_kind",
            "source_sha256",
            "plan_sha256",
            "source_time_kind",
        }
        for key in required_values:
            value = provenance[key]
            if (
                not isinstance(value, str)
                or not value
                or len(value) > 128
                or any(not character.isprintable() for character in value)
                or not value.isascii()
            ):
                raise _renderer_failure("Markup screenshot provenance is invalid")
        for key in ("source_id", "source_time"):
            value = provenance[key]
            if value is not None and (
                not value
                or len(value) > 128
                or not value.isascii()
                or any(not character.isprintable() for character in value)
            ):
                raise _renderer_failure("Markup screenshot provenance is invalid")
        for key in ("source_sha256", "plan_sha256"):
            value = provenance[key]
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise _renderer_failure("Markup screenshot provenance hash is invalid")
        if "certificate" in provenance["source_kind"] and provenance.get("page") is None:
            raise _renderer_failure("Certificate screenshot provenance requires a page")
    normalized_plan = {"redact": redactions, "crop": crop, "boxes": boxes}
    canonical_plan = json.dumps(
        normalized_plan,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    plan_sha256 = hashlib.sha256(canonical_plan).hexdigest()
    normalized = {
        "plan": normalized_plan,
        "profile": profile,
        "provenance": provenance,
    }
    try:
        encoded = json.dumps(
            normalized,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError) as exc:
        raise _renderer_failure("Screenshot request metadata is not JSON compatible") from exc
    if len(encoded) > MAX_REQUEST_BYTES:
        raise _renderer_failure("Screenshot request metadata exceeds 64 KiB")
    return normalized, plan_sha256


def _default_binary() -> Path:
    configured = os.environ.get("BID_SCREENSHOT_RENDERER")
    if configured:
        return Path(configured)
    repository = Path(__file__).resolve().parents[3]
    return repository / "data" / "work" / "bin" / "bid-screenshot-renderer"


class ScreenshotRenderer:
    """Memory-only process boundary for the independent Rust renderer."""

    def __init__(
        self,
        binary: str | os.PathLike[str] | None = None,
        *,
        timeout_seconds: float = RENDER_TIMEOUT_SECONDS,
    ):
        self.binary = Path(binary) if binary is not None else _default_binary()
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = min(float(timeout_seconds), RENDER_TIMEOUT_SECONDS)

    def _validated_binary(self) -> Path:
        if not self.binary.is_absolute():
            raise _renderer_failure("Screenshot renderer path must be absolute", retryable=True)
        if self.binary.is_symlink():
            raise _renderer_failure(
                "Screenshot renderer path must not be a symlink", retryable=True
            )
        try:
            metadata = self.binary.stat()
        except OSError as exc:
            raise _renderer_failure(
                "Screenshot renderer executable is unavailable", retryable=True
            ) from exc
        if not stat.S_ISREG(metadata.st_mode) or not os.access(self.binary, os.X_OK):
            raise _renderer_failure("Screenshot renderer executable is unavailable", retryable=True)
        return self.binary

    async def render(self, content: bytes, request: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
        if not isinstance(content, bytes) or not content:
            raise _renderer_failure("Screenshot content must be non-empty bytes")
        if len(content) > MAX_IMAGE_BYTES:
            raise _renderer_failure("Screenshot input exceeds 40 MiB")
        if not (content.startswith(b"\x89PNG\r\n\x1a\n") or content.startswith(b"\xff\xd8")):
            raise _renderer_failure("Screenshot input must be PNG or JPEG")
        normalized, plan_sha256 = _normalize_request(request)
        binary = self._validated_binary()
        request_bytes = json.dumps(
            normalized,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        frame = struct.pack(">Q", len(request_bytes)) + request_bytes + content

        process = await asyncio.create_subprocess_exec(
            os.fspath(binary),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=os.fspath(binary.parent),
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
        assert process.stdin is not None
        assert process.stdout is not None
        assert process.stderr is not None
        tasks = [
            asyncio.create_task(self._write_input(process.stdin, frame)),
            asyncio.create_task(self._read_output(process.stdout)),
            asyncio.create_task(self._read_diagnostics(process.stderr)),
        ]
        try:
            async with asyncio.timeout(self.timeout_seconds):
                _, output_result, _ = await asyncio.gather(*tasks)
                return_code = await process.wait()
        except TimeoutError as exc:
            await self._terminate(process)
            raise _renderer_failure("Screenshot renderer timed out", retryable=True) from exc
        except asyncio.CancelledError:
            await self._terminate(process)
            raise
        except (OSError, ValueError, struct.error, asyncio.IncompleteReadError) as exc:
            raise _renderer_failure("Screenshot renderer process failed") from exc
        finally:
            if process.returncode is None:
                await self._terminate(process)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        if return_code != 0:
            raise _renderer_failure("Screenshot renderer rejected the image or rectangle plan")
        response, output = output_result
        rendering = self._validate_rendering(output, response, normalized, plan_sha256)
        return output, rendering

    @staticmethod
    async def _write_input(stream: asyncio.StreamWriter, frame: bytes) -> None:
        stream.write(frame)
        await stream.drain()
        stream.close()
        await stream.wait_closed()

    @staticmethod
    async def _read_output(
        stream: asyncio.StreamReader,
    ) -> tuple[dict[str, Any], bytes]:
        header = await stream.readexactly(8)
        metadata_size = struct.unpack(">Q", header)[0]
        if metadata_size < 2 or metadata_size > MAX_RESPONSE_BYTES:
            raise _renderer_failure("Screenshot renderer returned oversized metadata")
        metadata_bytes = await stream.readexactly(metadata_size)
        try:
            metadata = json.loads(metadata_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _renderer_failure("Screenshot renderer returned malformed metadata") from exc
        if not isinstance(metadata, dict):
            raise _renderer_failure("Screenshot renderer returned malformed metadata")
        image = metadata.get("image")
        image_size = image.get("size_bytes") if isinstance(image, dict) else None
        if isinstance(image_size, bool) or not isinstance(image_size, int):
            raise _renderer_failure("Screenshot renderer omitted its image size")
        if image_size < 1 or image_size > MAX_IMAGE_BYTES:
            raise _renderer_failure("Screenshot renderer returned an invalid image size")
        output = await stream.readexactly(image_size)
        if await stream.read(1):
            raise _renderer_failure("Screenshot renderer returned trailing output")
        return metadata, output

    @staticmethod
    async def _read_diagnostics(stream: asyncio.StreamReader) -> bytes:
        output = bytearray()
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                return bytes(output)
            output.extend(chunk)
            if len(output) > MAX_DIAGNOSTIC_BYTES:
                raise _renderer_failure("Screenshot renderer diagnostics exceeded 64 KiB")

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            await process.wait()
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 1.0)
        except TimeoutError:
            process.kill()
            await process.wait()

    @staticmethod
    def _validate_rendering(
        output: bytes,
        rendering: dict[str, Any],
        request: dict[str, Any],
        plan_sha256: str,
    ) -> dict[str, Any]:
        required = {
            "image",
            "mapping",
            "source_width",
            "source_height",
            "plan_sha256",
            "profile",
        }
        if set(rendering) != required:
            raise _renderer_failure("Screenshot renderer returned an invalid rendering receipt")
        descriptor = validate_png(output)
        if rendering["image"] != descriptor:
            raise _renderer_failure("Screenshot renderer image receipt did not match its bytes")
        if rendering["profile"] != request["profile"]:
            raise _renderer_failure("Screenshot renderer profile receipt did not match its request")
        if rendering["plan_sha256"] != plan_sha256:
            raise _renderer_failure("Screenshot renderer plan receipt did not match its request")
        source_width = rendering["source_width"]
        source_height = rendering["source_height"]
        if (
            any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
                or value > MAX_DIMENSION
                for value in (source_width, source_height)
            )
            or source_width * source_height > MAX_PIXELS
        ):
            raise _renderer_failure("Screenshot renderer returned invalid source dimensions")

        requested_crop = request["plan"]["crop"]
        expected_crop = requested_crop or {
            "x": 0,
            "y": 0,
            "width": source_width,
            "height": source_height,
        }
        if not _contains(
            {"x": 0, "y": 0, "width": source_width, "height": source_height},
            expected_crop,
        ):
            raise _renderer_failure("Screenshot renderer accepted an out-of-bounds crop rectangle")
        for rectangle in request["plan"]["redact"] + request["plan"]["boxes"]:
            if not _contains(
                {"x": 0, "y": 0, "width": source_width, "height": source_height},
                rectangle,
            ):
                raise _renderer_failure("Screenshot renderer accepted an out-of-bounds rectangle")

        mapping = rendering["mapping"]
        mapping_keys = {
            "crop",
            "content_offset_x",
            "content_offset_y",
            "content_width",
            "content_height",
            "footer_height",
        }
        if not isinstance(mapping, dict) or set(mapping) != mapping_keys:
            raise _renderer_failure("Screenshot renderer returned an invalid pixel mapping")
        if mapping["crop"] != expected_crop:
            raise _renderer_failure("Screenshot renderer returned an incorrect crop mapping")
        numeric_values = [mapping[key] for key in mapping_keys - {"crop"}]
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in numeric_values
        ):
            raise _renderer_failure("Screenshot renderer returned an invalid pixel mapping")
        if (
            mapping["content_width"] != expected_crop["width"]
            or mapping["content_height"] != expected_crop["height"]
            or mapping["content_offset_y"] != 0
            or descriptor["height_px"] != mapping["content_height"] + mapping["footer_height"]
        ):
            raise _renderer_failure("Screenshot renderer returned an inconsistent pixel mapping")
        if request["profile"] == "screenshot-markup-v1":
            expected_width = max(mapping["content_width"], 1024)
            if (
                mapping["footer_height"] < 1
                or descriptor["width_px"] != expected_width
                or mapping["content_offset_x"] != (expected_width - mapping["content_width"]) // 2
            ):
                raise _renderer_failure("Screenshot renderer returned an invalid markup footer")
        elif (
            mapping["footer_height"] != 0
            or mapping["content_offset_x"] != 0
            or descriptor["width_px"] != mapping["content_width"]
        ):
            raise _renderer_failure("Clean screenshot profiles must not contain a footer")
        return rendering


async def render(content: bytes, request: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """Render through the configured Rust executable without persisting source bytes."""
    return await ScreenshotRenderer().render(content, request)
