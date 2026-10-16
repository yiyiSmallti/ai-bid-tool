import asyncio
import hashlib
import os
import stat
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
from app.core.errors import ServiceError
from app.schemas.certificate_file_contracts import (
    CertificateFileDownloadReceipt,
    CertificateScanFile,
)
from app.schemas.contracts import Result
from app.schemas.evidence_source_contracts import (
    EvidenceSourceArchive,
    EvidenceSourcePreviewReceipt,
)
from app.schemas.template_contracts import TemplateDownloadReceipt, TemplateFile
from app.services.certificate_files import WARNINGS as CERTIFICATE_FILE_WARNINGS
from app.services.evidence_sources import WARNINGS as SOURCE_WARNINGS
from app.services.evidence_sources import check_png
from app.services.template_files import WARNINGS as TEMPLATE_WARNINGS
from pydantic import ValidationError


def new_output_path(output: Path) -> Path:
    output = output.absolute()
    if (
        os.path.lexists(output)
        or not output.parent.is_dir()
        or any(parent.is_symlink() for parent in (output.parent, *output.parents))
    ):
        raise ServiceError(
            "invalid_output_path",
            "Download requires a new file in an existing nonsymlink directory",
            400,
            2,
        )
    return output


def save_download(output: Path, content: bytes | bytearray) -> None:
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".bid-download-", dir=output.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, output, follow_symlinks=False)
    except OSError as exc:
        raise ServiceError(
            "invalid_output_path",
            "Cannot save download without overwriting an existing path",
            400,
            2,
        ) from exc
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def credential() -> str:
    key = os.environ.get("BID_ACCESS_KEY")
    if key:
        return key
    filename = os.environ.get("BID_ACCESS_KEY_FILE")
    if filename:
        try:
            descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "r") as handle:
                info = os.fstat(handle.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_mode & 0o077
                ):
                    raise ValueError("unsafe access key file")
                key = handle.read().strip()
                if len(key) < 32:
                    raise ValueError("invalid access key")
                return key
        except (OSError, ValueError, UnicodeError):
            raise ServiceError(
                "invalid_access_key_file",
                "Access key file must be a regular owner-only file containing an access key",
                400,
                2,
            ) from None
    token = os.environ.get("BID_TOKEN")
    if token and token.startswith("bid_"):
        return token
    raise ServiceError(
        "access_key_required", "Set BID_ACCESS_KEY, BID_ACCESS_KEY_FILE or BID_TOKEN", 401, 4
    )


class Client:
    def __init__(self, mode: str, server: str, contract_version: str = "5.0"):
        if mode not in {"local", "remote"}:
            raise ServiceError("invalid_mode", "Mode must be local or remote", 400, 2)
        self.mode, self.server = mode, server.rstrip("/")
        self.contract_version = contract_version

    @asynccontextmanager
    async def transport(self):
        if self.mode == "local":
            from app.api.main import create_app

            application = create_app()
            async with (
                application.router.lifespan_context(application),
                httpx.AsyncClient(
                    # Unexpected server errors arrive as the same 500 Result a remote
                    # server sends instead of propagating into the CLI as a traceback.
                    transport=httpx.ASGITransport(app=application, raise_app_exceptions=False),
                    base_url="http://local",
                ) as client,
            ):
                yield client
        else:
            # Remote transmission is an explicit user choice of server. Require HTTPS
            # except loopback development, so passwords/tokens do not travel in cleartext.
            url = httpx.URL(self.server)
            if url.scheme != "https" and url.host not in {"localhost", "127.0.0.1", "::1"}:
                raise ServiceError("insecure_server", "Remote servers require HTTPS", 400, 2)
            async with httpx.AsyncClient(
                base_url=self.server, timeout=60, follow_redirects=False
            ) as client:
                yield client

    async def request(
        self,
        method: str,
        path: str,
        *,
        authenticated=True,
        platform=False,
        **kwargs,
    ) -> dict:
        headers = {"Authorization": f"Bearer {credential()}"} if authenticated else {}
        try:
            async with self.transport() as client:
                response = await client.request(
                    method,
                    "/v4" + path if self.contract_version == "5.0" else path,
                    headers=headers,
                    **kwargs,
                )
        except httpx.TransportError as exc:
            raise ServiceError(
                "network_unavailable", "Server is unavailable or timed out", 503, 3
            ) from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise ServiceError(
                "invalid_server_response", "Server returned an invalid response", 502, 3
            ) from exc
        if path == "/platform/clef" or path.startswith("/platform/clef/"):
            from bid_cli.platform_clef import safe_result

            action = (
                "show" if method.upper() == "GET" else "set" if method.upper() == "PUT" else "check"
            )
            result = safe_result(body, "platform clef " + action)
            if response.is_success != result["ok"]:
                raise ServiceError(
                    "invalid_server_response", "Server returned invalid Clef status", 502, 4
                )
            return result
        if path == "/platform/trust-anchors" or path.startswith("/platform/trust-anchors/"):
            from bid_cli.platform_trust_anchors import safe_result

            action = (
                "list"
                if method.upper() == "GET"
                else "add"
                if path == "/platform/trust-anchors"
                else "disable"
            )
            result = safe_result(body, "platform trust-anchor " + action)
            if response.is_success != result["ok"]:
                raise ServiceError(
                    "invalid_server_response", "Server returned invalid trust status", 502, 4
                )
            return result
        if path == "/platform/credentials" or path.startswith("/platform/credentials/"):
            from bid_cli.platform_credentials import safe_result

            actions = {
                "replace": "replace",
                "active": "set-active",
                "remove": "remove",
                "test": "test",
                "import-env": "import-env",
            }
            tail = path.rsplit("/", 1)[-1]
            action = (
                ("list" if method.upper() == "GET" else "create")
                if path == "/platform/credentials"
                else actions.get(tail, "show")
            )
            result = safe_result(body, "platform credential " + action)
            if response.is_success != result["ok"]:
                raise ServiceError(
                    "invalid_server_response", "Server returned invalid credential status", 502, 4
                )
            return result
        if not response.is_success:
            data = body.get("data", {})
            if isinstance(data, dict) and ("preflight" in data or "result" in data):
                # Terminal errors preserve the complete result envelope for CLI exit mapping.
                return body
            error = data.get("error", data if isinstance(data, dict) else {})
            job_id = None
            parts = path.split("?", 1)[0].split("/")
            queued_submission = (len(parts) == 4 and parts[1::2] == ["tasks", "checks"]) or (
                len(parts) == 4
                and parts[1] == "tasks"
                and parts[3] in {"score-rubrics", "bid-reviews"}
            )
            if method.upper() == "POST" and queued_submission and "job_id" in data:
                try:
                    UUID(parts[2])
                    job_id = str(UUID(data["job_id"]))
                except (TypeError, ValueError) as exc:
                    raise ServiceError(
                        "invalid_server_response",
                        "Server returned an invalid job identifier",
                        502,
                        4,
                    ) from exc
            transmitted_exit = response.headers.get("X-Bid-Exit-Code")
            failure_exit = (
                int(transmitted_exit)
                if transmitted_exit in {"2", "3", "4", "5"}
                else error.get(
                    "exit_code",
                    3
                    if error.get("retryable")
                    else 2
                    if response.status_code in {400, 409, 422}
                    else 4,
                )
            )
            raise ServiceError(
                error.get("code", "server_error"),
                error.get("message", "Server request failed"),
                response.status_code,
                failure_exit,
                job_id=job_id,
            )
        return body

    async def download_template(self, revision_id: UUID, output: Path) -> dict:
        output = await asyncio.to_thread(new_output_path, output)
        bearer = credential()
        listing = await self.request("GET", "/resources/templates", params={"history": True})
        rows = [row for row in listing.get("items", []) if row.get("id") == str(revision_id)]
        if len(rows) != 1:
            raise ServiceError("not_found", "Resource not found", 404, 4)
        try:
            descriptor = TemplateFile.model_validate(rows[0]["file"])
        except (ValueError, KeyError, ValidationError):
            raise ServiceError(
                "invalid_server_response", "Server returned invalid file metadata", 502, 4
            ) from None
        path = f"/resources/templates/revisions/{revision_id}/download"
        link = await self.request("GET", path + "-link")
        try:
            url = urlsplit(link["data"]["url"])
            query = parse_qs(url.query, strict_parsing=True)
            if (
                url.scheme
                or url.netloc
                or url.fragment
                or url.path != path
                or set(query) != {"signature"}
                or len(query["signature"]) != 1
                or not 0 < len(query["signature"][0]) < 8192
            ):
                raise ValueError("unsafe link")
        except (ValueError, KeyError, TypeError):
            raise ServiceError(
                "invalid_download_link", "Server returned an unsafe download link", 502, 4
            ) from None
        headers = {"Authorization": f"Bearer {bearer}"}
        content = bytearray()
        try:
            async with self.transport() as http:
                async with http.stream(
                    "GET", path, params=query, headers=headers, follow_redirects=False
                ) as response:
                    if not response.is_success:
                        raise ServiceError(
                            "download_unavailable",
                            "Download request failed",
                            response.status_code,
                            3 if response.status_code >= 502 else 4,
                        )
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > descriptor.size_bytes:
                            raise ServiceError(
                                "template_integrity",
                                "Downloaded file failed integrity checks",
                                502,
                                4,
                            )
        except httpx.TransportError as exc:
            raise ServiceError(
                "network_unavailable", "Server is unavailable or timed out", 503, 3
            ) from exc
        if (
            len(content) != descriptor.size_bytes
            or hashlib.sha256(content).hexdigest() != descriptor.sha256
        ):
            raise ServiceError(
                "template_integrity", "Downloaded file failed integrity checks", 502, 4
            )
        await asyncio.to_thread(save_download, output, content)
        receipt = TemplateDownloadReceipt(
            template_revision_id=revision_id, output_path=str(output), file=descriptor
        )
        return Result(
            ok=True,
            command="resource template download",
            data=receipt.model_dump(mode="json"),
            warnings=TEMPLATE_WARNINGS,
        ).model_dump(mode="json")

    async def download_certificate_file(self, revision_id: UUID, output: Path) -> dict:
        output = await asyncio.to_thread(new_output_path, output)
        bearer = credential()
        listing = await self.request(
            "GET", "/resources/certificates/files", params={"revision_id": str(revision_id)}
        )
        rows = [
            row
            for row in listing.get("items", [])
            if row.get("certificate_revision_id") == str(revision_id)
        ]
        if len(rows) != 1:
            raise ServiceError("not_found", "Resource not found", 404, 4)
        try:
            descriptor = CertificateScanFile.model_validate(rows[0]["file"])
        except (ValueError, KeyError, ValidationError):
            raise ServiceError(
                "invalid_server_response", "Server returned invalid file metadata", 502, 4
            ) from None
        path = f"/resources/certificates/revisions/{revision_id}/file/download"
        link = await self.request("GET", path + "-link")
        try:
            url = urlsplit(link["data"]["url"])
            query = parse_qs(url.query, strict_parsing=True)
            if (
                url.scheme
                or url.netloc
                or url.fragment
                or url.path != path
                or set(query) != {"signature"}
                or len(query["signature"]) != 1
                or not 0 < len(query["signature"][0]) < 8192
            ):
                raise ValueError("unsafe link")
        except (ValueError, KeyError, TypeError):
            raise ServiceError(
                "invalid_download_link", "Server returned an unsafe download link", 502, 4
            ) from None
        headers = {"Authorization": f"Bearer {bearer}"}
        content = bytearray()
        try:
            async with self.transport() as http:
                async with http.stream(
                    "GET", path, params=query, headers=headers, follow_redirects=False
                ) as response:
                    if not response.is_success:
                        raise ServiceError(
                            "download_unavailable",
                            "Download request failed",
                            response.status_code,
                            3 if response.status_code >= 502 else 4,
                        )
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > descriptor.size_bytes:
                            raise ServiceError(
                                "certificate_file_integrity",
                                "Downloaded file failed integrity checks",
                                502,
                                4,
                            )
        except httpx.TransportError as exc:
            raise ServiceError(
                "network_unavailable", "Server is unavailable or timed out", 503, 3
            ) from exc
        if (
            len(content) != descriptor.size_bytes
            or hashlib.sha256(content).hexdigest() != descriptor.sha256
        ):
            raise ServiceError(
                "certificate_file_integrity", "Downloaded file failed integrity checks", 502, 4
            )
        await asyncio.to_thread(save_download, output, content)
        receipt = CertificateFileDownloadReceipt(
            certificate_revision_id=revision_id, output_path=str(output), file=descriptor
        )
        return Result(
            ok=True,
            command="resource certificate file download",
            data=receipt.model_dump(mode="json"),
            warnings=CERTIFICATE_FILE_WARNINGS,
        ).model_dump(mode="json")

    async def download_evidence_source(self, source_id: UUID, output: Path) -> dict:
        output = await asyncio.to_thread(new_output_path, output)
        bearer = credential()
        path = f"/evidence-sources/{source_id}/preview/download"
        link = await self.request("GET", path + "-link")
        try:
            rows = link["items"]
            if len(rows) != 1 or rows[0].get("id") != str(source_id):
                raise ValueError("source mismatch")
            archive = EvidenceSourceArchive.model_validate(rows[0])
            descriptor = archive.preview
        except (ValueError, KeyError, TypeError, ValidationError):
            raise ServiceError(
                "invalid_server_response", "Server returned invalid source metadata", 502, 4
            ) from None
        try:
            url = urlsplit(link["data"]["url"])
            query = parse_qs(url.query, strict_parsing=True)
            if (
                url.scheme
                or url.netloc
                or url.fragment
                or url.path != path
                or set(query) != {"signature"}
                or len(query["signature"]) != 1
                or not 0 < len(query["signature"][0]) < 8192
            ):
                raise ValueError("unsafe link")
        except (ValueError, KeyError, TypeError):
            raise ServiceError(
                "invalid_download_link", "Server returned an unsafe download link", 502, 4
            ) from None
        headers = {"Authorization": f"Bearer {bearer}"}
        content = bytearray()
        try:
            async with self.transport() as http:
                async with http.stream(
                    "GET", path, params=query, headers=headers, follow_redirects=False
                ) as response:
                    if not response.is_success:
                        raise ServiceError(
                            "download_unavailable",
                            "Download request failed",
                            response.status_code,
                            3 if response.status_code >= 502 else 4,
                        )
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > descriptor.size_bytes:
                            raise ServiceError(
                                "source_preview_integrity",
                                "Downloaded file failed integrity checks",
                                502,
                                4,
                            )
        except httpx.TransportError as exc:
            raise ServiceError(
                "network_unavailable", "Server is unavailable or timed out", 503, 3
            ) from exc
        if (
            len(content) != descriptor.size_bytes
            or hashlib.sha256(content).hexdigest() != descriptor.sha256
        ):
            raise ServiceError(
                "source_preview_integrity", "Downloaded file failed integrity checks", 502, 4
            )
        await asyncio.to_thread(check_png, bytes(content), descriptor)
        await asyncio.to_thread(save_download, output, content)
        receipt = EvidenceSourcePreviewReceipt(
            evidence_source_id=source_id, output_path=str(output), file=descriptor
        )
        return Result(
            ok=True,
            command="evidence source download",
            data=receipt.model_dump(mode="json"),
            warnings=SOURCE_WARNINGS,
        ).model_dump(mode="json")

    async def download_package(self, job_id: UUID, output: Path) -> dict:
        """Save a finished export package after checking its size and hash."""
        output = await asyncio.to_thread(new_output_path, output)
        status = await self.request("GET", f"/packages/{job_id}")
        try:
            report = status["data"]["report"]
            size, digest = int(report["size"]), str(report["sha256"])
        except (KeyError, TypeError, ValueError):
            raise ServiceError("package_not_ready", "The package is not ready", 409, 4) from None
        headers = {"Authorization": f"Bearer {credential()}"}
        content = bytearray()
        try:
            async with self.transport() as http:
                async with http.stream(
                    "GET", f"/packages/{job_id}/file", headers=headers, follow_redirects=False
                ) as response:
                    if not response.is_success:
                        await response.aread()
                        try:
                            error = response.json()["data"]["error"]
                            code, message = str(error["code"]), str(error["message"])
                        except (ValueError, KeyError, TypeError):
                            code, message = "download_unavailable", "Download request failed"
                        raise ServiceError(code, message, response.status_code, 4)
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > size:
                            break
        except httpx.TransportError as exc:
            raise ServiceError(
                "network_unavailable", "Server is unavailable or timed out", 503, 3
            ) from exc
        if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
            raise ServiceError(
                "package_integrity_failed", "Downloaded package failed integrity checks", 502, 4
            )
        await asyncio.to_thread(save_download, output, content)
        return Result(
            ok=True,
            command="package download",
            data={
                "job_id": str(job_id),
                "output_path": str(output),
                "sha256": digest,
                "size": size,
            },
        ).model_dump(mode="json")

    async def upload(self, task_id: UUID, path: Path) -> dict:
        with path.open("rb") as content:
            return await self.request(
                "POST", f"/tasks/{task_id}/documents", files={"file": (path.name, content)}
            )
