"""DOCX to PDF conversion through an operator-run Gotenberg (LibreOffice) service.

Only the released export file leaves the worker, and only to this private service. The
returned PDF is untrusted until the caller has opened and bounded it.
"""

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


def create_converter(settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
    if not settings.converter_url:
        return None
    return GotenbergConverter(settings.converter_url, settings.converter_timeout_seconds, transport)
