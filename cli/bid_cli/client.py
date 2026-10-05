import asyncio
import hashlib
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
from app.core.errors import ServiceError
from app.core.security import Secrets
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


class State:
    def __init__(self, path: Path):
        self.path = path

    def cipher(self) -> Secrets:
        key = os.environ.get("BID_CLI_KEY")
        if not key:
            raise ServiceError(
                "cli_key_required",
                "BID_CLI_KEY is required to persist or read an encrypted CLI session",
                400,
                2,
            )
        return Secrets(key)

    def load(self) -> dict:
        import json

        if os.environ.get("BID_SESSION") and os.environ.get("BID_ORG"):
            return {"session": os.environ["BID_SESSION"], "org_id": os.environ["BID_ORG"]}
        if not self.path.exists():
            raise ServiceError("login_required", "Login or supply BID_SESSION and BID_ORG", 401, 4)
        return json.loads(self.cipher().decrypt(self.path.read_text()))

    def load_platform(self) -> str:
        import json

        if os.environ.get("BID_PLATFORM_SESSION"):
            return os.environ["BID_PLATFORM_SESSION"]
        if self.path.exists():
            saved = json.loads(self.cipher().decrypt(self.path.read_text()))
            if saved.get("platform_session"):
                return saved["platform_session"]
        raise ServiceError(
            "login_required", "Run bid platform login or supply BID_PLATFORM_SESSION", 401, 4
        )

    def save_platform(self, session: str) -> None:
        import json

        # Kept beside any org session in the same encrypted file.
        saved = (
            json.loads(self.cipher().decrypt(self.path.read_text())) if self.path.exists() else {}
        )
        self.save({**saved, "platform_session": session})

    def save(self, value: dict) -> None:
        import json

        encrypted = self.cipher().encrypt(json.dumps(value))
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ServiceError("unsafe_state_path", "CLI state must not be a symlink", 400, 2)
        temp = self.path.with_suffix(".tmp")
        descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w") as handle:
                handle.write(encrypted)
            os.replace(temp, self.path)
        finally:
            temp.unlink(missing_ok=True)


class Client:
    def __init__(self, mode: str, server: str, state: State, contract_version: str = "4.0"):
        if mode not in {"local", "remote"}:
            raise ServiceError("invalid_mode", "Mode must be local or remote", 400, 2)
        self.mode, self.server, self.state = mode, server.rstrip("/"), state
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
        org: UUID | None = None,
        platform=False,
        **kwargs,
    ) -> dict:
        headers = {}
        if platform:
            headers = {"Authorization": f"Bearer {self.state.load_platform()}"}
        elif authenticated:
            saved = self.state.load()
            headers = {
                "Authorization": f"Bearer {saved['session']}",
                "X-Org-Id": str(org or saved["org_id"]),
            }
        try:
            async with self.transport() as client:
                response = await client.request(
                    method,
                    "/v4" + path if self.contract_version == "4.0" else path,
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
        if platform and (
            path == "/platform/credentials" or path.startswith("/platform/credentials/")
        ):
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
            if isinstance(data, dict) and ("budget" in data or "result" in data):
                # Budget stops preserve the complete paid envelope for CLI exit mapping.
                return body
            error = data.get("error", {})
            job_id = None
            parts = path.split("?", 1)[0].split("/")
            queued_submission = (len(parts) == 4 and parts[1::2] == ["tasks", "checks"]) or (
                len(parts) == 4 and parts[1] == "tasks" and parts[3] == "score-rubrics"
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
            raise ServiceError(
                error.get("code", "server_error"),
                error.get("message", "Server request failed"),
                response.status_code,
                error.get("exit_code", 4),
                job_id=job_id,
            )
        return body

    async def download_template(self, revision_id: UUID, output: Path) -> dict:
        output = await asyncio.to_thread(new_output_path, output)
        saved = self.state.load()
        listing = await self.request("GET", "/resources/templates", params={"history": True})
        rows = [row for row in listing.get("items", []) if row.get("id") == str(revision_id)]
        if len(rows) != 1:
            raise ServiceError("not_found", "Resource not found", 404, 4)
        try:
            if rows[0].get("org_id") != saved["org_id"]:
                raise ValueError("scope mismatch")
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
        headers = {"Authorization": f"Bearer {saved['session']}", "X-Org-Id": saved["org_id"]}
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
        saved = self.state.load()
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
            if rows[0].get("org_id") != saved["org_id"]:
                raise ValueError("scope mismatch")
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
        headers = {"Authorization": f"Bearer {saved['session']}", "X-Org-Id": saved["org_id"]}
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
        saved = self.state.load()
        path = f"/evidence-sources/{source_id}/preview/download"
        link = await self.request("GET", path + "-link")
        try:
            rows = link["items"]
            if (
                len(rows) != 1
                or rows[0].get("id") != str(source_id)
                or rows[0].get("org_id") != saved["org_id"]
            ):
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
        headers = {"Authorization": f"Bearer {saved['session']}", "X-Org-Id": saved["org_id"]}
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

    async def upload(self, task_id: UUID, path: Path) -> dict:
        with path.open("rb") as content:
            return await self.request(
                "POST", f"/tasks/{task_id}/documents", files={"file": (path.name, content)}
            )
