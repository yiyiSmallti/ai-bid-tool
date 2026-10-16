"""DOCX to PDF conversion through an operator-run Gotenberg (LibreOffice) service, or on
the desktop through the bundled WebKit renderer for export page previews.

Only the released export file leaves the worker, and only to this private service or a
local child process. The returned PDF is untrusted until the caller has opened and
bounded it.
"""

import asyncio
import tempfile
from pathlib import Path

import httpx

from app.core.config import Settings
from app.providers.base import ProviderFailure

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# A converted export is at most a few times its DOCX size; anything larger is refused unread.
MAX_PDF_BYTES = 512 * 1024 * 1024


class GotenbergConverter:
    name = "gotenberg"

    def __init__(
        self, base_url: str, timeout: float, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    @property
    def identity(self) -> str:
        return f"{self.name}:{self.base_url}"

    async def docx_to_pdf(self, content: bytes, name: str) -> bytes:
        try:
            async with (
                httpx.AsyncClient(
                    transport=self.transport, timeout=self.timeout, trust_env=False
                ) as client,
                client.stream(
                    "POST",
                    self.base_url + "/forms/libreoffice/convert",
                    files={"files": (name, content, DOCX_MEDIA_TYPE)},
                    follow_redirects=False,
                ) as response,
            ):
                if response.status_code != 200:
                    raise ProviderFailure(
                        "Converter rejected the document",
                        code="converter_failed",
                        retryable=response.status_code >= 500,
                    )
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_PDF_BYTES:
                        raise ProviderFailure(
                            "Converted PDF exceeds the preview limit", code="preview_too_large"
                        )
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise ProviderFailure(
                "Converter service is unavailable", code="converter_unavailable", retryable=True
            ) from exc
        return b"".join(chunks)


class WebKitConverter:
    """Runs bid-docx-preview: docx-preview lays the DOCX out in WebKit, printed to A4.

    Pagination approximates Word's; the preview is for reading, not layout acceptance.
    """

    name = "webkit"

    def __init__(self, binary: str, timeout: float):
        self.binary = binary
        self.timeout = timeout

    @property
    def identity(self) -> str:
        return f"{self.name}:docx-preview"

    async def docx_to_pdf(self, content: bytes, name: str) -> bytes:
        with tempfile.TemporaryDirectory(prefix="bid-preview-") as work:
            source, target = Path(work, "in.docx"), Path(work, "out.pdf")
            await asyncio.to_thread(source.write_bytes, content)
            try:
                process = await asyncio.create_subprocess_exec(
                    self.binary,
                    str(source),
                    str(target),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except OSError as exc:
                raise ProviderFailure(
                    "Preview renderer is unavailable", code="converter_unavailable"
                ) from exc
            try:
                async with asyncio.timeout(self.timeout):
                    await process.communicate()
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise ProviderFailure(
                    "Preview renderer timed out", code="converter_failed", retryable=True
                ) from exc
            if process.returncode != 0:
                raise ProviderFailure("Converter rejected the document", code="converter_failed")
            return await asyncio.to_thread(_read_output, target)


def _read_output(path: Path) -> bytes:
    if not path.is_file():
        raise ProviderFailure("Converter rejected the document", code="converter_failed")
    if path.stat().st_size > MAX_PDF_BYTES:
        raise ProviderFailure("Converted PDF exceeds the preview limit", code="preview_too_large")
    return path.read_bytes()


def create_converter(settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
    if not settings.converter_url:
        return None
    return GotenbergConverter(settings.converter_url, settings.converter_timeout_seconds, transport)


def create_preview_converter(settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
    """Export previews also accept the desktop renderer; bid review keeps Gotenberg only."""
    if converter := create_converter(settings, transport):
        return converter
    if settings.preview_renderer:
        return WebKitConverter(settings.preview_renderer, settings.converter_timeout_seconds)
    return None
