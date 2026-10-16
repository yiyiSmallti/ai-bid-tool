"""Download an assembled whole bid through its signed link."""

import asyncio
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
from app.core.errors import ServiceError
from app.schemas.contracts import Result
from app.schemas.whole_bid import WholeBidReceipt

from bid_cli.client import Client, credential, new_output_path, save_download

MAX_BYTES = 200 * 1024 * 1024


async def download(client: Client, document: UUID, output: Path) -> dict:
    output = await asyncio.to_thread(new_output_path, output)
    path = f"/documents/{document}/whole-bid"
    link = await client.request("GET", path + "-link")
    try:
        url = urlsplit(link["data"]["url"])
        query = parse_qs(url.query, strict_parsing=True)
        if url.scheme or url.netloc or url.path != path or set(query) != {"signature"}:
            raise ValueError("unsafe link")
    except (ValueError, KeyError, TypeError) as exc:
        raise ServiceError(
            "invalid_download_link", "Server returned an unsafe download link", 502, 4
        ) from exc
    prefix = "/v4" if client.contract_version == "5.0" else ""
    content = bytearray()
    try:
        async with client.transport() as http:
            async with http.stream(
                "GET",
                prefix + path,
                params=query,
                headers={"Authorization": f"Bearer {credential()}"},
                follow_redirects=False,
                timeout=300,
            ) as response:
                if not response.is_success:
                    raise ServiceError(
                        "download_unavailable",
                        "Whole bid assembly failed",
                        response.status_code,
                        3 if response.status_code >= 502 else 4,
                    )
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_BYTES:
                        raise ServiceError("download_too_large", "Whole bid is too large", 502, 4)
                headers = response.headers
    except httpx.TransportError as exc:
        raise ServiceError("network_unavailable", "Download unavailable", 503, 3) from exc
    if not content:
        raise ServiceError("invalid_server_response", "Server returned an empty file", 502, 4)
    await asyncio.to_thread(save_download, output, content)
    receipt = WholeBidReceipt(
        document_id=document,
        output_path=str(output),
        size_bytes=len(content),
        draft=headers.get("X-Whole-Bid-Draft") == "1",
        placeholders=int(headers.get("X-Whole-Bid-Placeholders", "0")),
        unconfirmed_rows=int(headers.get("X-Whole-Bid-Unconfirmed-Rows", "0")),
        copies=int(headers.get("X-Whole-Bid-Copies", "0")),
    )
    return Result(
        ok=True, command="tender whole-bid", data=receipt.model_dump(mode="json")
    ).model_dump(mode="json")
