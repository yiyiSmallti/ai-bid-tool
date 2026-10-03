"""Screenshot CLI commands and local, privacy-first preparation."""

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import typer
from app.core.errors import ServiceError
from app.providers.base import ProviderFailure
from app.providers.screenshot_renderer import validate_png
from app.schemas.contracts import Result
from app.schemas.evidence_source_contracts import EvidenceSourceArchive
from app.schemas.screenshot_contracts import (
    PNGDescriptor,
    PrototypeDecisionBatch,
    PrototypeDecisionPreviewInput,
    PrototypeGenerateInput,
    PrototypeSource,
    ScreenshotAnalyzeInput,
    ScreenshotAnnotate,
    ScreenshotIngest,
    ScreenshotPrepareInput,
    ScreenshotPreviewLink,
    ScreenshotWithdraw,
    VendorSource,
)
from app.services.evidence_sources import check_png
from pydantic import ValidationError

MAX_IMAGE_BYTES = 40 * 1024 * 1024
MAX_RECEIPT_BYTES = 4 * 1024 * 1024


def _invalid(message: str, *, code: str = "invalid_input", exit_code: int = 2) -> ServiceError:
    return ServiceError(code, message, 400, exit_code)


def _read_bounded(path: Path, limit: int, label: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise _invalid(f"{label} does not exist or cannot be read", code="missing_file") from exc
    if not path.is_file() or size < 1:
        raise _invalid(f"{label} must be a non-empty regular file", code="missing_file")
    if size > limit:
        raise _invalid(f"{label} exceeds the command limit", code="input_too_large")
    with path.open("rb") as handle:
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise _invalid(f"{label} exceeds the command limit", code="input_too_large")
    if len(content) != size:
        raise _invalid(f"{label} changed while it was read")
    return content


def _png_descriptor(content: bytes, *, downloaded: bool = False) -> dict[str, Any]:
    try:
        return validate_png(content)
    except ProviderFailure:
        raise ServiceError(
            "screenshot_integrity" if downloaded else "invalid_screenshot",
            (
                "Downloaded screenshot failed integrity checks"
                if downloaded
                else "Screenshot is not a valid bounded PNG"
            ),
            502 if downloaded else 400,
            4 if downloaded else 2,
        ) from None


def _open_directory(path: Path) -> int:
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        result = descriptor
        descriptor = -1
        return result
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _same_directory(path: Path, descriptor: int):
    current = _open_directory(path)
    try:
        left, right = os.fstat(current), os.fstat(descriptor)
        if (left.st_dev, left.st_ino) != (right.st_dev, right.st_ino):
            raise _invalid("Output parent changed during publication", code="invalid_output_path")
    finally:
        os.close(current)


def _save_files(files: list[tuple[Path, bytes]]) -> None:
    """Pin parent directories and roll back only this attempt's own file inodes."""
    prepared: list[tuple[int, str, Path]] = []
    published: list[tuple[int, str, str]] = []
    complete = False
    try:
        for target, content in files:
            parent = _open_directory(target.parent)
            temporary = ".bid-screenshot-" + uuid4().hex
            prepared.append((parent, temporary, target))
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        for parent, temporary, target in prepared:
            _same_directory(target.parent, parent)
            os.link(
                temporary, target.name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False
            )
            published.append((parent, temporary, target.name))
            _same_directory(target.parent, parent)
        complete = True
    except (OSError, ValueError) as exc:
        raise _invalid(
            "Cannot publish screenshot files into a changed or existing path",
            code="invalid_output_path",
        ) from exc
    finally:
        if not complete:
            for parent, temporary, name in published:
                try:
                    actual = os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                original = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
                if (actual.st_dev, actual.st_ino) == (original.st_dev, original.st_ino):
                    os.unlink(name, dir_fd=parent)
        for parent, temporary, _ in prepared:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass  # Creation failed before this attempt had a temporary object.
            finally:
                os.close(parent)


def _save_pair(output: Path, png: bytes, receipt: Path, receipt_bytes: bytes) -> tuple[Path, Path]:
    from bid_cli.client import new_output_path

    output, receipt = new_output_path(output), new_output_path(receipt)
    if output == receipt:
        raise _invalid("PNG and receipt need different new output paths")
    _save_files([(output, png), (receipt, receipt_bytes)])
    return output, receipt


def _signed_path(value: Any, expected_path: str) -> dict[str, list[str]]:
    try:
        url = urlsplit(value)
        query = parse_qs(url.query, strict_parsing=True)
        if (
            url.scheme
            or url.netloc
            or url.fragment
            or url.path != expected_path
            or set(query) != {"signature"}
            or len(query["signature"]) != 1
            or not 0 < len(query["signature"][0]) < 8192
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise ServiceError(
            "invalid_download_link", "Server returned an unsafe screenshot link", 502, 4
        ) from None
    return query


async def _stream_png(path: str, query: dict[str, list[str]], descriptor: PNGDescriptor) -> bytes:
    from bid_cli import main as cli

    saved = cli.client().state.load()
    headers = {
        "Authorization": f"Bearer {saved['session']}",
        "X-Org-Id": saved["org_id"],
    }
    content = bytearray()
    try:
        async with cli.client().transport() as transport:
            async with transport.stream(
                "GET", path, params=query, headers=headers, follow_redirects=False
            ) as response:
                if not response.is_success:
                    raise ServiceError(
                        "download_unavailable",
                        "Screenshot download failed",
                        response.status_code,
                        3 if response.status_code >= 502 else 4,
                    )
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > descriptor.size_bytes:
                        raise ServiceError(
                            "screenshot_integrity",
                            "Downloaded screenshot failed integrity checks",
                            502,
                            4,
                        )
    except httpx.TransportError as exc:
        raise ServiceError(
            "network_unavailable", "Server is unavailable or timed out", 503, 3
        ) from exc
    actual = _png_descriptor(bytes(content), downloaded=True)
    if actual != descriptor.model_dump(mode="json"):
        raise ServiceError(
            "screenshot_integrity",
            "Downloaded screenshot failed integrity checks",
            502,
            4,
        )
    return bytes(content)


async def _read_certificate_preview(source_id: UUID) -> bytes:
    from bid_cli import main as cli

    saved = cli.client().state.load()
    path = f"/evidence-sources/{source_id}/preview/download"
    link = await cli.client().request("GET", path + "-link")
    try:
        rows = link["items"]
        if (
            len(rows) != 1
            or rows[0].get("id") != str(source_id)
            or rows[0].get("org_id") != saved["org_id"]
        ):
            raise ValueError
        archive = EvidenceSourceArchive.model_validate(rows[0])
    except (KeyError, TypeError, ValueError, ValidationError):
        raise ServiceError(
            "invalid_server_response", "Server returned invalid source metadata", 502, 4
        ) from None
    query = _signed_path(link.get("data", {}).get("url"), path)
    descriptor = PNGDescriptor.model_validate(
        archive.preview.model_dump(mode="json", exclude={"name"})
    )
    content = await _stream_png(path, query, descriptor)
    await asyncio.to_thread(check_png, content, archive.preview)
    return content


async def _read_prototype_source(prototype_run_id: UUID) -> bytes:
    from bid_cli import main as cli

    saved = cli.client().state.load()
    shown = await cli.client().request("GET", f"/prototype-runs/{prototype_run_id}")
    try:
        data = shown["data"]
        if data["id"] != str(prototype_run_id) or data["org_id"] != saved["org_id"]:
            raise ValueError
        expected = str(data["source_image_sha256"])
    except (KeyError, TypeError, ValueError):
        raise ServiceError(
            "invalid_server_response", "Server returned invalid prototype metadata", 502, 4
        ) from None
    headers = {"Authorization": f"Bearer {saved['session']}", "X-Org-Id": saved["org_id"]}
    content = bytearray()
    try:
        async with cli.client().transport() as transport:
            async with transport.stream(
                "GET",
                f"/prototype-runs/{prototype_run_id}/source",
                headers=headers,
                follow_redirects=False,
            ) as response:
                if not response.is_success:
                    raise ServiceError(
                        "download_unavailable",
                        "Prototype image download failed",
                        response.status_code,
                        3 if response.status_code >= 502 else 4,
                    )
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_IMAGE_BYTES:
                        raise ServiceError(
                            "screenshot_integrity", "Prototype image exceeds 40 MiB", 502, 4
                        )
    except httpx.TransportError as exc:
        raise ServiceError("server_unavailable", "Server is unavailable", 503, 3) from exc
    if hashlib.sha256(content).hexdigest() != expected:
        raise ServiceError(
            "screenshot_integrity", "Downloaded prototype image failed integrity checks", 502, 4
        )
    return bytes(content)


async def _read_vendor_page(artifact_id: UUID) -> bytes:
    from bid_cli import main as cli
    from bid_cli.sandbox import fetch_artifact

    artifact, content = await fetch_artifact(cli.client(), artifact_id)
    if artifact.kind not in {"capture_png", "pdf_page_png"}:
        raise _invalid("--sandbox-artifact must name a captured page image")
    return bytes(content)


async def _download_rendition(rendition_id: UUID, output: Path) -> dict:
    from bid_cli import main as cli
    from bid_cli.client import new_output_path

    output = await asyncio.to_thread(new_output_path, output)
    path = f"/screenshot-renditions/{rendition_id}/content"
    link = await cli.client().request("POST", f"/screenshot-renditions/{rendition_id}/preview-link")
    try:
        descriptor = ScreenshotPreviewLink.model_validate(link["data"])
        if descriptor.rendition_id != rendition_id:
            raise ValueError
    except (KeyError, ValueError, ValidationError):
        raise ServiceError(
            "invalid_server_response", "Server returned invalid screenshot metadata", 502, 4
        ) from None
    query = _signed_path(descriptor.url, path)
    content = await _stream_png(path, query, descriptor.image)
    await asyncio.to_thread(_save_files, [(output, content)])
    link["data"] = {
        "rendition_id": str(rendition_id),
        "output_path": str(output),
        "image": descriptor.image.model_dump(mode="json"),
    }
    return link


def _job_result(body: dict, wait: bool, dry_run: bool, timeout: float) -> tuple[dict, int]:
    from bid_cli import main as cli

    if wait and not dry_run:
        try:
            job_id = UUID(body["data"]["job_id"])
        except (KeyError, TypeError, ValueError):
            raise ServiceError(
                "invalid_server_response", "Server did not identify the screenshot job", 502, 4
            ) from None
        terminal = asyncio.run(cli.wait_for_job(job_id, timeout))
        result = terminal["data"].get("result") or {}
        body["data"].update(result)
        body["data"]["status"] = terminal["data"]["status"]
        body["warnings"] = result.get("warnings", [])
        body["cost"] = result.get("cost", body.get("cost", {}))
    return body, cli.partial_completion_exit(body)


def register(app: typer.Typer) -> None:
    """Register screenshot commands after the main CLI helpers exist."""
    from bid_cli import main as cli

    screenshot_app = typer.Typer()
    decisions_app = typer.Typer()
    ui_app = typer.Typer()
    app.add_typer(screenshot_app, name="screenshot")
    app.add_typer(ui_app, name="ui")
    screenshot_app.add_typer(decisions_app, name="prototype-decisions")

    @ui_app.command("mock")
    def ui_mock_command(
        task: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        dry_run: Annotated[bool, typer.Option()] = False,
        retry: Annotated[bool, typer.Option()] = False,
        wait: Annotated[bool, typer.Option()] = False,
        timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 300,
        json_output: cli.JsonOption = False,
    ):
        request = PrototypeGenerateInput.model_validate(
            cli.input_contract(input, PrototypeGenerateInput)
        )
        values = request.model_dump(mode="json")
        values["dry_run"], values["retry"] = request.dry_run or dry_run, request.retry or retry
        request = PrototypeGenerateInput.model_validate(values)
        body = cli.call(
            "POST", f"/tasks/{task}/prototype-generations", json=request.model_dump(mode="json")
        )
        body, exit_code = _job_result(body, wait, request.dry_run, timeout)
        cli.emit(body, "ui mock", json_output, exit_code)

    @screenshot_app.command("prepare")
    def prepare_command(
        input: Annotated[Path, typer.Option()],
        output: Annotated[Path, typer.Option()],
        receipt: Annotated[Path, typer.Option()],
        file: Annotated[Path | None, typer.Option()] = None,
        source: Annotated[UUID | None, typer.Option()] = None,
        prototype_run: Annotated[UUID | None, typer.Option()] = None,
        sandbox_artifact: Annotated[UUID | None, typer.Option()] = None,
        json_output: cli.JsonOption = False,
    ):
        from bid_cli.client import new_output_path

        if [file, source, prototype_run, sandbox_artifact].count(None) != 3:
            raise _invalid(
                "Give exactly one of --file, --source, --prototype-run or --sandbox-artifact"
            )
        target, receipt_target = new_output_path(output), new_output_path(receipt)
        if target == receipt_target:
            raise _invalid("PNG and receipt need different new output paths")
        request = ScreenshotPrepareInput.model_validate(
            cli.input_contract(input, ScreenshotPrepareInput)
        )
        if file is not None:
            if request.source.kind != "upload":
                raise _invalid("--file requires an upload source in --input")
            content = _read_bounded(file, MAX_IMAGE_BYTES, "Screenshot input")
        elif prototype_run is not None:
            if (
                not isinstance(request.source, PrototypeSource)
                or request.source.prototype_run_id != prototype_run
            ):
                raise _invalid("--prototype-run must match the prototype source in --input")
            content = asyncio.run(_read_prototype_source(prototype_run))
        elif sandbox_artifact is not None:
            if (
                not isinstance(request.source, VendorSource)
                or request.source.sandbox_artifact_id != sandbox_artifact
            ):
                raise _invalid("--sandbox-artifact must match the vendor source in --input")
            content = asyncio.run(_read_vendor_page(sandbox_artifact))
        else:
            assert source is not None
            if (
                request.source.kind != "certificate_page"
                or request.source.evidence_source_id != source
            ):
                raise _invalid("--source must match the certificate source in --input")
            content = asyncio.run(_read_certificate_preview(source))
        from app.services import screenshots as screenshot_service

        png, prepared = asyncio.run(
            screenshot_service.prepare(content, request.source, request.plan)
        )
        descriptor = _png_descriptor(png, downloaded=True)
        if descriptor != prepared.image.model_dump(mode="json"):
            raise ServiceError(
                "screenshot_integrity", "Prepared screenshot metadata does not match", 502, 4
            )
        receipt_bytes = (prepared.model_dump_json() + "\n").encode()
        target, receipt_target = _save_pair(target, png, receipt_target, receipt_bytes)
        body = Result(
            ok=True,
            command="screenshot prepare",
            data={
                "output_path": str(target),
                "receipt_path": str(receipt_target),
                "prepared": prepared.model_dump(mode="json"),
            },
        ).model_dump(mode="json")
        cli.emit(body, "screenshot prepare", json_output)

    @screenshot_app.command("add")
    def add_command(
        task: Annotated[UUID, typer.Option()],
        file: Annotated[Path, typer.Option()],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        request = ScreenshotIngest.model_validate(cli.input_contract(input, ScreenshotIngest))
        content = _read_bounded(file, MAX_IMAGE_BYTES, "Prepared PNG")
        descriptor = _png_descriptor(content)
        if (
            descriptor != request.prepared.image.model_dump(mode="json")
            or descriptor["sha256"] != request.reviewed_upload_sha256
        ):
            raise _invalid("Prepared PNG does not match the reviewed receipt")
        body = cli.call(
            "POST",
            f"/tasks/{task}/screenshots",
            files={
                "file": (file.name, content, "image/png"),
                "input": (None, request.model_dump_json(), "application/json"),
            },
        )
        cli.emit(body, "screenshot add", json_output)

    @screenshot_app.command("list")
    def list_command(
        task: Annotated[UUID, typer.Option()],
        job: Annotated[UUID, typer.Option()],
        history: Annotated[bool, typer.Option()] = False,
        cursor: Annotated[UUID | None, typer.Option()] = None,
        json_output: cli.JsonOption = False,
    ):
        params = {"job": str(job), "history": str(history).lower()}
        if cursor:
            params["cursor"] = str(cursor)
        cli.emit(
            cli.call("GET", f"/tasks/{task}/screenshots", params=params),
            "screenshot list",
            json_output,
        )

    @screenshot_app.command("show")
    def show_command(
        asset_id: Annotated[UUID, typer.Option("--id")],
        json_output: cli.JsonOption = False,
    ):
        cli.emit(cli.call("GET", f"/screenshots/{asset_id}"), "screenshot show", json_output)

    @screenshot_app.command("annotate")
    def annotate_command(
        asset_id: Annotated[UUID, typer.Option("--id")],
        input: Annotated[Path, typer.Option()],
        dry_run: Annotated[bool, typer.Option()] = False,
        retry: Annotated[bool, typer.Option()] = False,
        wait: Annotated[bool, typer.Option()] = False,
        timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
        json_output: cli.JsonOption = False,
    ):
        request = ScreenshotAnnotate.model_validate(cli.input_contract(input, ScreenshotAnnotate))
        request = request.model_copy(
            update={"dry_run": request.dry_run or dry_run, "retry": request.retry or retry}
        )
        if request.dry_run and request.retry:
            raise _invalid("Dry-run cannot retry")
        body = cli.call(
            "POST", f"/screenshots/{asset_id}/renditions", json=request.model_dump(mode="json")
        )
        body, exit_code = _job_result(body, wait, request.dry_run, timeout)
        cli.emit(body, "screenshot annotate", json_output, exit_code)

    @screenshot_app.command("preview")
    def preview_command(
        rendition_id: Annotated[UUID, typer.Option("--id")],
        output: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            asyncio.run(_download_rendition(rendition_id, output)),
            "screenshot preview",
            json_output,
        )

    @screenshot_app.command("withdraw")
    def withdraw_command(
        asset_id: Annotated[UUID, typer.Option("--id")],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        request = ScreenshotWithdraw.model_validate(cli.input_contract(input, ScreenshotWithdraw))
        cli.emit(
            cli.call(
                "POST",
                f"/screenshots/{asset_id}/withdrawals",
                json=request.model_dump(mode="json"),
            ),
            "screenshot withdraw",
            json_output,
        )

    @screenshot_app.command("analyze")
    def analyze_command(
        task: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        dry_run: Annotated[bool, typer.Option()] = False,
        retry: Annotated[bool, typer.Option()] = False,
        wait: Annotated[bool, typer.Option()] = False,
        timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
        json_output: cli.JsonOption = False,
    ):
        request = ScreenshotAnalyzeInput.model_validate(
            cli.input_contract(input, ScreenshotAnalyzeInput)
        )
        values = request.model_dump(mode="json")
        values["dry_run"], values["retry"] = request.dry_run or dry_run, request.retry or retry
        request = ScreenshotAnalyzeInput.model_validate(values)
        body = cli.call(
            "POST", f"/tasks/{task}/screenshot-analyses", json=request.model_dump(mode="json")
        )
        body, exit_code = _job_result(body, wait, request.dry_run, timeout)
        cli.emit(body, "screenshot analyze", json_output, exit_code)

    @screenshot_app.command("suggestions")
    def suggestions_command(
        analysis: Annotated[UUID, typer.Option()],
        cursor: Annotated[UUID | None, typer.Option()] = None,
        json_output: cli.JsonOption = False,
    ):
        params = {"cursor": str(cursor)} if cursor else None
        cli.emit(
            cli.call("GET", f"/screenshot-analyses/{analysis}/suggestions", params=params),
            "screenshot suggestions",
            json_output,
        )

    @decisions_app.command("preview")
    def decisions_preview_command(
        task: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        request = PrototypeDecisionPreviewInput.model_validate(
            cli.input_contract(input, PrototypeDecisionPreviewInput, MAX_RECEIPT_BYTES)
        )
        cli.emit(
            cli.call(
                "POST",
                f"/tasks/{task}/prototype-decisions/preview",
                json=request.model_dump(mode="json"),
            ),
            "screenshot prototype-decisions preview",
            json_output,
        )

    @decisions_app.command("apply")
    def decisions_apply_command(
        task: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        request = PrototypeDecisionBatch.model_validate(
            cli.input_contract(input, PrototypeDecisionBatch, MAX_RECEIPT_BYTES)
        )
        cli.emit(
            cli.call(
                "POST",
                f"/tasks/{task}/prototype-decisions",
                json=request.model_dump(mode="json"),
            ),
            "screenshot prototype-decisions apply",
            json_output,
        )

    @decisions_app.command("list")
    def decisions_list_command(
        task: Annotated[UUID, typer.Option()],
        job: Annotated[UUID, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call(
                "GET",
                f"/tasks/{task}/prototype-decisions",
                params={"job": str(job)},
            ),
            "screenshot prototype-decisions list",
            json_output,
        )
