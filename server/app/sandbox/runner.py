"""Fixed image entrypoint. This module executes only inside disposable containers."""

import asyncio
import hashlib
import importlib.metadata
import json
import os
import struct
import sys
import zlib
from typing import Any

from app.providers.sandbox_runtime import (
    CONTROL_LIMIT,
    KIND_LIMITS,
    MIB,
    ArtifactPayload,
    RunDescriptor,
    SandboxFailure,
    canonical,
    validate_artifact,
)


def receive(limit: int) -> tuple[dict[str, Any], bytes]:
    line = sys.stdin.buffer.readline(CONTROL_LIMIT + 1)
    if len(line) > CONTROL_LIMIT or not line.endswith(b"\n"):
        raise SandboxFailure("invalid_frame")
    header = json.loads(line)
    length = header.get("length")
    if type(length) is not int or length < 0 or length > limit:
        raise SandboxFailure("stream_limit")
    data = sys.stdin.buffer.read(length)
    if len(data) != length:
        raise SandboxFailure("invalid_frame")
    return header, data


# Under gVisor, writes larger than PIPE_BUF to the attached stdout can stall or report more
# bytes than were delivered (a 4 KiB block went missing from a 238 KiB PNG). Writes of at
# most PIPE_BUF are atomic on a pipe, so the stream is emitted in such writes.
WRITE_CHUNK = 4096


def write_all(data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(1, view[:WRITE_CHUNK])
        view = view[written:]


def emit(header: dict[str, Any], data: bytes = b"") -> None:
    raw = canonical({**header, "length": len(data)}) + b"\n"
    if len(raw) > CONTROL_LIMIT:
        raise SandboxFailure("control_limit")
    sys.stdout.flush()
    write_all(raw)
    write_all(data)


def artifact_header(artifact: ArtifactPayload, ordinal: int) -> dict[str, Any]:
    return {
        "type": "artifact",
        "ordinal": ordinal,
        "kind": artifact.kind,
        "width": artifact.width,
        "height": artifact.height,
        "page": artifact.page,
        "sha256": artifact.sha256,
    }


def check_dimensions(width: int, height: int) -> None:
    if width < 1 or height < 1 or max(width, height) > 8192 or width * height > 20_000_000:
        raise SandboxFailure("image_limit")


async def fetch(url: str, method: str = "GET") -> tuple[dict[str, Any], bytes]:
    emit({"type": "fetch", "url": url, "method": method})
    response, data = await asyncio.to_thread(receive, 32 * MIB)
    if response.get("type") == "error":
        raise SandboxFailure("network_denied")
    if response.get("type") != "fetch_result" or not 200 <= response["status"] < 300:
        raise SandboxFailure("source_http_error")
    return response, data


async def render_pdf(descriptor: RunDescriptor, source: bytes) -> list[ArtifactPayload]:
    import pymupdf  # Only installed in the isolated image.

    _, data = await fetch(source.decode("utf-8"))
    if not data.startswith(b"%PDF-"):
        raise SandboxFailure("invalid_pdf")
    document = pymupdf.open(stream=data, filetype="pdf")
    if document.needs_pass:
        raise SandboxFailure("encrypted_pdf")
    results = [ArtifactPayload("source_pdf", data)]
    try:
        for number in descriptor.pdf_pages:
            if number > document.page_count:
                raise SandboxFailure("pdf_page_missing")
            page = document.load_page(number - 1)
            rectangle = page.rect * (150 / 72)
            check_dimensions(int(rectangle.width + 1), int(rectangle.height + 1))
            pixmap = page.get_pixmap(dpi=150, alpha=False)
            check_dimensions(pixmap.width, pixmap.height)
            results.append(
                ArtifactPayload(
                    "pdf_page_png", pixmap.tobytes("png"), pixmap.width, pixmap.height, number
                )
            )
    finally:
        document.close()
    return results


async def render_web(
    descriptor: RunDescriptor, source: bytes
) -> tuple[list[ArtifactPayload], tuple[str, ...]]:
    from playwright.async_api import (  # pyright: ignore[reportMissingImports]
        Error as PlaywrightError,
    )
    from playwright.async_api import async_playwright  # pyright: ignore[reportMissingImports]

    offline = descriptor.purpose == "prototype_offline"
    denied: list[str] = []

    def deny(code: str) -> None:
        if not denied:
            denied.append(code)

    fetch_lock = asyncio.Lock()
    redirect_responses: dict[str, tuple[dict[str, Any], bytes]] = {}
    async with async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch(
                headless=True,
                chromium_sandbox=True,
                args=[
                    "--disable-extensions",
                    "--disable-background-networking",
                    "--disable-sync",
                    "--disable-breakpad",
                    "--disable-crash-reporter",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--disable-features=WebRtcHideLocalIpsWithMdns",
                ],
            )
        except PlaywrightError as exc:
            # A refused Chromium sandbox (seccomp, namespaces) must surface as a fixed code,
            # not as a silent exit the supervisor can only report as a broken frame.
            raise SandboxFailure("browser_launch_failed") from exc
        context = await browser.new_context(
            viewport={"width": descriptor.viewport_width, "height": descriptor.viewport_height},
            device_scale_factor=1,
            accept_downloads=False,
            service_workers="block",
            permissions=[],
            java_script_enabled=True,
        )
        context.set_default_timeout(10_000)

        async def handle_route(route):
            request = route.request
            if (
                offline
                or request.method not in ("GET", "HEAD")
                or not request.url.startswith("https://")
            ):
                deny("network_denied")
                await route.abort("blockedbyclient")
                return
            async with fetch_lock:
                try:
                    cached = redirect_responses.pop(request.url, None)
                    response, body = (
                        cached if cached is not None else await fetch(request.url, request.method)
                    )
                    if response["url"] != request.url:
                        redirect_responses[response["url"]] = (response, body)
                        await route.fulfill(
                            status=302, headers={"location": response["url"]}, body=b""
                        )
                        return
                    # A fresh context has no credentials; proxy response headers exclude cookies.
                    await route.fulfill(
                        status=response["status"], headers=response["headers"], body=body
                    )
                except SandboxFailure as exc:
                    deny(exc.code)
                    await route.abort("blockedbyclient")

        await context.route("**/*", handle_route)

        async def block_websocket(socket):
            deny("network_denied")
            await socket.close()

        await context.route_web_socket("**/*", block_websocket)
        page = await context.new_page()

        async def reject_popup(popup):
            deny("popup_denied")
            await popup.close()

        page.on("popup", reject_popup)
        page.on("download", lambda _: deny("download_denied"))
        await page.add_init_script("""
            Object.defineProperty(window, 'RTCPeerConnection', {value: undefined});
            Object.defineProperty(window, 'webkitRTCPeerConnection', {value: undefined});
        """)
        if offline:
            await page.set_content(source.decode("utf-8"), wait_until="load")
        else:
            await page.goto(source.decode("utf-8"), wait_until="load")
        if denied and not offline:
            raise SandboxFailure(denied[0])
        if (
            not offline
            and await page.locator(
                'input[type=password],iframe[src*="recaptcha"],iframe[src*="hcaptcha"]'
            ).count()
        ):
            raise SandboxFailure("source_login_or_challenge")
        # Deterministic viewport capture; full-page expansion could allocate attacker-sized canvases.
        png = await page.screenshot(
            type="png", full_page=False, animations="disabled", timeout=10_000
        )
        dom = (await page.content()).encode("utf-8")
        if denied and not offline:
            raise SandboxFailure(denied[0])
        await context.close()
        await browser.close()
    return [
        ArtifactPayload(
            "prototype_png" if offline else "capture_png",
            png,
            descriptor.viewport_width,
            descriptor.viewport_height,
        ),
        ArtifactPayload("rendered_html", dom),
    ], (("offline_resources_blocked",) if denied else ())


def validate_png_chunks(data: bytes) -> None:
    """Validate complete static PNG framing before the isolated image decoder."""
    offset, saw_idat, ordinal = 8, False, 0
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        end = offset + 12 + length
        if end > len(data):
            raise SandboxFailure("invalid_png")
        kind = data[offset + 4 : offset + 8]
        if ordinal == 0 and (kind != b"IHDR" or length != 13):
            raise SandboxFailure("invalid_png")
        if kind in (b"acTL", b"fcTL", b"fdAT"):
            raise SandboxFailure("invalid_png")
        if kind and 65 <= kind[0] <= 90 and kind not in (b"IHDR", b"PLTE", b"IDAT", b"IEND"):
            raise SandboxFailure("invalid_png")
        expected = int.from_bytes(data[end - 4 : end], "big")
        if zlib.crc32(data[offset + 4 : end - 4]) & 0xFFFFFFFF != expected:
            raise SandboxFailure("invalid_png")
        if kind == b"IDAT":
            saw_idat = True
        if kind == b"IEND":
            if length or end != len(data) or not saw_idat:
                raise SandboxFailure("invalid_png")
            return
        offset, ordinal = end, ordinal + 1
    raise SandboxFailure("invalid_png")


def validate_payload(artifact: ArtifactPayload) -> None:
    if artifact.kind.endswith("_png"):
        import pymupdf

        validate_png_chunks(artifact.data)
        pixmap = pymupdf.Pixmap(artifact.data)
        check_dimensions(pixmap.width, pixmap.height)
        if (pixmap.width, pixmap.height) != (artifact.width, artifact.height):
            raise SandboxFailure("invalid_png")
        if artifact.kind == "capture_png":
            samples = pixmap.samples
            pixel = samples[: pixmap.n]
            if samples.count(pixel) * len(pixel) == len(samples):
                raise SandboxFailure("blank_capture")
    elif artifact.kind == "source_pdf":
        import pymupdf

        with pymupdf.open(stream=artifact.data, filetype="pdf") as pdf:
            if pdf.needs_pass or pdf.page_count < 1:
                raise SandboxFailure("invalid_pdf")


async def main() -> None:
    header, source = receive(32 * MIB)
    descriptor = RunDescriptor.from_wire(header["descriptor"])
    issues: tuple[str, ...] = ()
    if sys.argv[1:] == ["validate"]:
        if source:
            raise SandboxFailure("invalid_frame")
        artifacts = []
        total = 0
        while True:
            metadata, data = receive(min(40 * MIB, descriptor.output_limit - total))
            if metadata.get("type") == "end":
                break
            if len(artifacts) >= 32 or metadata.get("ordinal") != len(artifacts):
                raise SandboxFailure("artifact_sequence")
            artifact = validate_artifact(metadata, data)
            validate_payload(artifact)
            artifacts.append(artifact)
            total += len(data)
    elif sys.argv[1:] == ["render"]:
        if hashlib.sha256(source).hexdigest() != descriptor.input_sha256:
            raise SandboxFailure("input_hash_mismatch")
        if descriptor.format == "pdf":
            artifacts = await render_pdf(descriptor, source)
        else:
            artifacts, issues = await render_web(descriptor, source)
    else:
        raise SandboxFailure("invalid_stage")
    if sum(len(x.data) for x in artifacts) > descriptor.output_limit:
        raise SandboxFailure("output_limit")
    for ordinal, artifact in enumerate(artifacts):
        if len(artifact.data) > KIND_LIMITS[artifact.kind]:
            raise SandboxFailure("artifact_limit")
        emit(artifact_header(artifact, ordinal), artifact.data)
    emit(
        {
            "type": "done",
            "issues": issues,
            "versions": {
                "driver": importlib.metadata.version("playwright"),
                "pdf": importlib.metadata.version("PyMuPDF"),
                "protocol": "bid-sandbox-v1",
            },
        }
    )
    # Keep the cgroup alive until the supervisor obtains terminal daemon counters.
    # No further content is executed while waiting for this fixed acknowledgement.
    acknowledgement, tail = receive(0)
    if acknowledgement.get("type") != "finish" or tail:
        raise SandboxFailure("invalid_frame")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SandboxFailure as exc:
        emit({"type": "error", "code": exc.code})
        raise SystemExit(1) from None
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, struct.error):
        # No attacker-controlled exception or traceback is emitted to host logs.
        emit({"type": "error", "code": "sandbox_parser_failure"})
        raise SystemExit(1) from None
