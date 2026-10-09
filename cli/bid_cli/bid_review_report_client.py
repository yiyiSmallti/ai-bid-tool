"""Human-only download of an immutable uploaded-bid Word report."""

import asyncio
from pathlib import Path
from uuid import UUID

from app.core.errors import ServiceError
from app.schemas.bid_review_report import BidReportDownloadLink, BidReportDownloadReceipt
from app.schemas.contracts import Result

from bid_cli.client import Client, new_output_path
from bid_cli.export_client import save_verified_docx, signed_download_route


async def download_report_artifact(client: Client, artifact_id: UUID, output: Path) -> dict:
    output = await asyncio.to_thread(new_output_path, output)
    if output.suffix.lower() != ".docx":
        raise ServiceError(
            "invalid_output_path", "Report download output must use a .docx name", 400, 2
        )
    saved = client.state.load()
    result = await client.request("GET", f"/bid-review-artifacts/{artifact_id}/download-link")
    try:
        envelope = Result.model_validate(result)
        link = BidReportDownloadLink.model_validate(envelope.data)
        artifact = link.artifact
        if (
            not envelope.ok
            or envelope.command != "review report download"
            or envelope.items
            or artifact.id != artifact_id
            or str(artifact.org_id) != saved["org_id"]
            or artifact.format != "docx"
        ):
            raise ValueError("report descriptor scope mismatch")
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceError(
            "invalid_server_response", "Server returned invalid report download metadata", 502, 4
        ) from exc
    path, query = signed_download_route(link.url, f"/bid-review-artifacts/{artifact_id}/download")
    await save_verified_docx(
        client,
        output,
        path,
        query,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        integrity_code="bid_report_file_integrity",
    )
    receipt = BidReportDownloadReceipt(
        artifact_id=artifact_id, output_path=str(output), artifact=artifact
    ).model_dump(mode="json")
    return Result(ok=True, command="review report download", data=receipt).model_dump(mode="json")
