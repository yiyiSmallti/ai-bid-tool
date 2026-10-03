import asyncio
import errno
import hashlib
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
import typer
from app.core.errors import ServiceError
from app.schemas.contracts import Result
from app.schemas.sandbox_contracts import (
    PrototypeSpec,
    SandboxArtifactView,
    SandboxDownloadLink,
    SandboxDownloadReceipt,
    SandboxPreview,
    SandboxRunView,
    SandboxSubmit,
    VendorSpec,
)
from pydantic import ValidationError

from bid_cli.client import Client

JSON_INPUT_LIMIT = 64 * 1024
HTML_INPUT_LIMIT = 4 * 1024 * 1024
RESULT_KEYS = {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}

sandbox_app = typer.Typer()


def invalid_output_path() -> ServiceError:
    return ServiceError(
        "invalid_output_path",
        "Download requires a new file in an unchanged nonsymlink directory",
        400,
        2,
    )


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _walk_directory(path: Path) -> tuple[int, tuple[tuple[int, int], ...]]:
    if not path.is_absolute():
        raise invalid_output_path()
    current = os.open("/", _directory_flags())
    identities = []
    transferred = False
    try:
        root = os.fstat(current)
        identities.append((root.st_dev, root.st_ino))
        for component in path.parts[1:]:
            following = os.open(component, _directory_flags(), dir_fd=current)
            os.close(current)
            current = following
            descriptor = os.fstat(current)
            identities.append((descriptor.st_dev, descriptor.st_ino))
        transferred = True
        return current, tuple(identities)
    finally:
        if not transferred:
            os.close(current)


class SandboxOutput:
    def __init__(
        self,
        path: Path,
        parent_fd: int,
        parent_identities: tuple[tuple[int, int], ...],
    ):
        self.path = path
        self.parent_fd = parent_fd
        self.parent_identities = parent_identities

    @classmethod
    def prepare(cls, output: Path):
        path = Path(os.path.abspath(os.fspath(output)))
        if path.name in {"", ".", ".."}:
            raise invalid_output_path()
        parent_fd = None
        try:
            parent_fd, identities = _walk_directory(path.parent)
            prepared = cls(path, parent_fd, identities)
            prepared.validate_parent()
            try:
                os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return prepared
            raise invalid_output_path()
        except ServiceError:
            if parent_fd is not None:
                os.close(parent_fd)
            raise
        except OSError:
            if parent_fd is not None:
                os.close(parent_fd)
            raise invalid_output_path() from None

    def validate_parent(self) -> None:
        reopened = None
        try:
            reopened, identities = _walk_directory(self.path.parent)
            held = os.fstat(self.parent_fd)
            current = os.fstat(reopened)
            if identities != self.parent_identities or (held.st_dev, held.st_ino) != (
                current.st_dev,
                current.st_ino,
            ):
                raise invalid_output_path()
        except ServiceError:
            raise
        except OSError:
            raise invalid_output_path() from None
        finally:
            if reopened is not None:
                os.close(reopened)

    def committed_file_matches(self, identity: tuple[int, int]) -> bool:
        reopened = None
        try:
            reopened, identities = _walk_directory(self.path.parent)
            held = os.fstat(self.parent_fd)
            current = os.fstat(reopened)
            if identities != self.parent_identities or (held.st_dev, held.st_ino) != (
                current.st_dev,
                current.st_ino,
            ):
                return False
            target = os.stat(self.path.name, dir_fd=reopened, follow_symlinks=False)
            return (
                (target.st_dev, target.st_ino) == identity
                and stat.S_ISREG(target.st_mode)
                and stat.S_IMODE(target.st_mode) == 0o600
            )
        except OSError:
            return False
        finally:
            if reopened is not None:
                os.close(reopened)

    def unlink_if_owned(self, identity: tuple[int, int]) -> None:
        try:
            target = os.stat(self.path.name, dir_fd=self.parent_fd, follow_symlinks=False)
            if (target.st_dev, target.st_ino) == identity:
                os.unlink(self.path.name, dir_fd=self.parent_fd)
                os.fsync(self.parent_fd)
        except FileNotFoundError:
            return

    def save(self, content: bytes | bytearray) -> None:
        temporary = f".bid-sandbox-download-{secrets.token_hex(16)}"
        temporary_created = False
        linked = False
        identity = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=self.parent_fd,
            )
            temporary_created = True
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb", closefd=False) as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                created = os.fstat(descriptor)
                identity = (created.st_dev, created.st_ino)
            finally:
                os.close(descriptor)
            self.validate_parent()
            os.link(
                temporary,
                self.path.name,
                src_dir_fd=self.parent_fd,
                dst_dir_fd=self.parent_fd,
                follow_symlinks=False,
            )
            linked = True
            os.unlink(temporary, dir_fd=self.parent_fd)
            temporary_created = False
            os.fsync(self.parent_fd)
            if identity is None or not self.committed_file_matches(identity):
                raise invalid_output_path()
        except ServiceError:
            if linked and identity is not None:
                self.unlink_if_owned(identity)
            raise
        except OSError as exc:
            if linked and identity is not None:
                self.unlink_if_owned(identity)
            if exc.errno == errno.ENOSPC:
                raise ServiceError(
                    "download_storage_unavailable",
                    "Download output storage is unavailable",
                    507,
                    3,
                ) from None
            raise invalid_output_path() from None
        finally:
            if temporary_created:
                try:
                    os.unlink(temporary, dir_fd=self.parent_fd)
                except FileNotFoundError:
                    pass

    def close(self) -> None:
        os.close(self.parent_fd)


def invalid_server_response() -> ServiceError:
    return ServiceError(
        "invalid_server_response", "Server returned an invalid sandbox response", 502, 4
    )


def validated_result(
    body: dict,
    *,
    data_model: type[SandboxPreview | SandboxRunView | SandboxDownloadLink] | None = None,
    item_model: type[SandboxRunView] | None = None,
) -> dict:
    try:
        if set(body) != RESULT_KEYS:
            raise ValueError("unexpected result keys")
        result = Result.model_validate(body)
        value = result.model_dump(mode="json")
        if data_model is not None:
            value["data"] = data_model.model_validate(value["data"]).model_dump(mode="json")
        if item_model is not None:
            value["items"] = [
                item_model.model_validate(item).model_dump(mode="json") for item in value["items"]
            ]
        return value
    except (TypeError, ValueError, ValidationError):
        raise invalid_server_response() from None


def sandbox_job_exit(body: dict) -> int:
    data = body.get("data", {})
    if data.get("kind") != "sandbox" or data.get("status") not in {"failed", "cancelled"}:
        return 0
    body["ok"] = False
    if data.get("error") is None:
        data["error"] = {"code": "cancelled", "message": "Job was cancelled", "exit_code": 4}
    code = data["error"]["exit_code"]
    if type(code) is not int or code not in {2, 3, 4}:
        raise invalid_server_response()
    return code


def preview_exit(body: dict) -> int:
    data = body.get("data", {})
    if data.get("dry_run") is not True or data.get("ready") is not False:
        return 0
    body["ok"] = False
    return (
        3 if any(issue["code"] == "sandbox_runtime_unavailable" for issue in data["issues"]) else 4
    )


def read_spec[SpecT: (PrototypeSpec, VendorSpec)](path: Path, model: type[SpecT]) -> SpecT:
    if not path.is_file():
        raise ServiceError("missing_file", "Sandbox input file does not exist", 400, 2)
    with path.open("rb") as handle:
        content = handle.read(JSON_INPUT_LIMIT + 1)
    if len(content) > JSON_INPUT_LIMIT:
        raise ServiceError("input_too_large", "Sandbox JSON input exceeds 64 KiB", 400, 2)
    try:
        raw = json.loads(content.decode("utf-8"))
        return model.model_validate(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, ValueError):
        raise ServiceError("invalid_input", "Sandbox JSON input is invalid", 400, 2) from None


def read_html(path: Path, spec: PrototypeSpec) -> bytes:
    if not path.is_file():
        raise ServiceError("missing_file", "Prototype HTML file does not exist", 400, 2)
    with path.open("rb") as handle:
        content = handle.read(HTML_INPUT_LIMIT + 1)
    if len(content) > HTML_INPUT_LIMIT:
        raise ServiceError("input_too_large", "Prototype HTML exceeds 4 MiB", 400, 2)
    try:
        content.decode("utf-8")
    except UnicodeDecodeError:
        raise ServiceError("invalid_utf8", "Prototype HTML must be valid UTF-8", 400, 2) from None
    if len(content) != spec.html_size_bytes:
        raise ServiceError(
            "html_size_mismatch", "Prototype HTML size does not match the plan", 400, 2
        )
    if hashlib.sha256(content).hexdigest() != spec.html_sha256:
        raise ServiceError(
            "html_hash_mismatch", "Prototype HTML hash does not match the plan", 400, 2
        )
    return content


async def post_submit(
    api: Client,
    task_id: UUID,
    submit: SandboxSubmit,
    *,
    html: bytes | None = None,
) -> dict:
    payload = submit.model_dump(mode="json")
    path = f"/tasks/{task_id}/sandbox-runs"
    if html is None:
        body = await api.request("POST", path, json=payload)
    else:
        body = await api.request(
            "POST",
            path,
            data={"submit": json.dumps(payload, separators=(",", ":"))},
            files={"html": ("prototype.html", html, "text/html; charset=utf-8")},
        )
    return validated_result(body, data_model=SandboxPreview if submit.dry_run else SandboxRunView)


async def submit_with_preflight(
    api: Client,
    task_id: UUID,
    spec: PrototypeSpec | VendorSpec,
    *,
    dry_run: bool,
    retry: bool,
    html: bytes | None = None,
) -> dict:
    if dry_run:
        request = SandboxSubmit(spec=spec, dry_run=True, retry=retry)
        return await post_submit(api, task_id, request, html=html)
    preview_body = await post_submit(
        api,
        task_id,
        SandboxSubmit(spec=spec, dry_run=True),
        html=html,
    )
    preview = SandboxPreview.model_validate(preview_body["data"])
    if not preview.ready:
        code = next(
            (issue.code for issue in preview.issues if issue.severity == "block"),
            "sandbox_preflight_blocked",
        )
        raise ServiceError(
            code,
            "Sandbox preflight is blocked",
            503,
            3 if code == "sandbox_runtime_unavailable" else 4,
        )
    request = SandboxSubmit(
        spec=spec,
        expected_request_hash=preview.request_hash,
        retry=retry,
    )
    return await post_submit(api, task_id, request, html=html)


async def wait_and_refresh(api: Client, body: dict, wait_seconds: float) -> dict:
    from bid_cli.main import wait_for_job

    run = SandboxRunView.model_validate(body["data"])
    await wait_for_job(run.job_id, wait_seconds)
    refreshed = await api.request("GET", f"/sandbox-runs/{run.id}")
    return validated_result(refreshed, data_model=SandboxRunView)


async def fetch_artifact(api: Client, artifact_id: UUID) -> tuple[SandboxArtifactView, bytearray]:
    """Follow the signed same-origin link and verify length and SHA-256 in memory."""
    path = f"/sandbox-artifacts/{artifact_id}/download"
    link_body = validated_result(
        await api.request("GET", path + "-link"), data_model=SandboxDownloadLink
    )
    link = SandboxDownloadLink.model_validate(link_body["data"])
    if link.artifact.id != artifact_id:
        raise invalid_server_response()
    try:
        url = urlsplit(link.url)
        query = parse_qs(url.query, strict_parsing=True)
        if (
            url.scheme
            or url.netloc
            or url.fragment
            or url.path != path
            or set(query) != {"token"}
            or len(query["token"]) != 1
            or not 0 < len(query["token"][0]) <= 4096
        ):
            raise ValueError("unsafe link")
    except (TypeError, ValueError):
        raise ServiceError(
            "invalid_download_link", "Server returned an unsafe download link", 502, 4
        ) from None

    saved = api.state.load()
    headers = {
        "Authorization": f"Bearer {saved['session']}",
        "X-Org-Id": saved["org_id"],
    }
    content = bytearray()
    try:
        async with api.transport() as http:
            async with http.stream(
                "GET", path, params=query, headers=headers, follow_redirects=False
            ) as response:
                if not response.is_success:
                    raise ServiceError(
                        "download_unavailable",
                        "Sandbox artifact download failed",
                        response.status_code,
                        3 if response.status_code in {429, 502, 503, 504} else 4,
                    )
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > link.artifact.size_bytes:
                        raise ServiceError(
                            "sandbox_artifact_integrity",
                            "Downloaded artifact failed integrity checks",
                            502,
                            4,
                        )
    except httpx.TransportError as exc:
        raise ServiceError(
            "network_unavailable", "Server is unavailable or timed out", 503, 3
        ) from exc
    if (
        len(content) != link.artifact.size_bytes
        or hashlib.sha256(content).hexdigest() != link.artifact.sha256
    ):
        raise ServiceError(
            "sandbox_artifact_integrity",
            "Downloaded artifact failed integrity checks",
            502,
            4,
        )
    return link.artifact, content


async def download_artifact(api: Client, artifact_id: UUID, output: Path) -> dict:
    # Anchor the destination before any network wait can give another process time
    # to replace a checked directory with a symlink.
    target = SandboxOutput.prepare(output)
    try:
        artifact, content = await fetch_artifact(api, artifact_id)
        saving = asyncio.create_task(asyncio.to_thread(target.save, content))
        try:
            await asyncio.shield(saving)
        except asyncio.CancelledError as cancellation:
            # to_thread cannot stop a running filesystem transaction. Keep the
            # directory descriptor alive until it has committed or rolled back.
            try:
                await saving
            except ServiceError as failure:
                raise cancellation from failure
            raise
        receipt = SandboxDownloadReceipt(artifact=artifact, output_path=str(target.path))
        return Result(
            ok=True,
            command="sandbox download",
            data=receipt.model_dump(mode="json"),
        ).model_dump(mode="json")
    finally:
        target.close()


@sandbox_app.command("render")
def render_command(
    task: Annotated[UUID, typer.Option()],
    html: Annotated[Path, typer.Option()],
    input: Annotated[Path, typer.Option()],
    dry_run: Annotated[bool, typer.Option()] = False,
    retry: Annotated[bool, typer.Option()] = False,
    wait: Annotated[bool, typer.Option()] = False,
    timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the versioned machine-readable result")
    ] = False,
):
    from bid_cli.main import client, emit

    spec = read_spec(input, PrototypeSpec)
    content = read_html(html, spec)
    body = asyncio.run(
        submit_with_preflight(
            client(),
            task,
            spec,
            dry_run=dry_run,
            retry=retry,
            html=content,
        )
    )
    if wait and not dry_run:
        body = asyncio.run(wait_and_refresh(client(), body, timeout))
    emit(body, "sandbox render", json_output, preview_exit(body))


@sandbox_app.command("capture")
def capture_command(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    dry_run: Annotated[bool, typer.Option()] = False,
    retry: Annotated[bool, typer.Option()] = False,
    wait: Annotated[bool, typer.Option()] = False,
    timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the versioned machine-readable result")
    ] = False,
):
    from bid_cli.main import client, emit

    spec = read_spec(input, VendorSpec)
    body = asyncio.run(submit_with_preflight(client(), task, spec, dry_run=dry_run, retry=retry))
    if wait and not dry_run:
        body = asyncio.run(wait_and_refresh(client(), body, timeout))
    emit(body, "sandbox capture", json_output, preview_exit(body))


@sandbox_app.command("list")
def list_command(
    task: Annotated[UUID, typer.Option()],
    offset: Annotated[int, typer.Option(min=0)] = 0,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the versioned machine-readable result")
    ] = False,
):
    from bid_cli.main import call, emit

    body = validated_result(
        call(
            "GET",
            f"/tasks/{task}/sandbox-runs",
            params={"offset": offset, "limit": limit},
        ),
        item_model=SandboxRunView,
    )
    emit(body, "sandbox list", json_output)


@sandbox_app.command("show")
def show_command(
    id: Annotated[UUID, typer.Option("--id")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the versioned machine-readable result")
    ] = False,
):
    from bid_cli.main import call, emit

    body = validated_result(call("GET", f"/sandbox-runs/{id}"), data_model=SandboxRunView)
    emit(body, "sandbox show", json_output)


@sandbox_app.command("download")
def download_command(
    artifact: Annotated[UUID, typer.Option()],
    output: Annotated[Path, typer.Option()],
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit the versioned machine-readable result")
    ] = False,
):
    from bid_cli.main import client, emit

    body = asyncio.run(download_artifact(client(), artifact, output))
    emit(body, "sandbox download", json_output)
