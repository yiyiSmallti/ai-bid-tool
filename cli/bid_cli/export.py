"""Human-only export CLI commands."""

import asyncio
import json
import time
from pathlib import Path
from typing import Annotated
from uuid import UUID

import httpx
import typer
from app.core.errors import ServiceError
from app.schemas.contracts import Contract, Result
from app.schemas.export_contracts import (
    ExportBindingCreate,
    ExportBindingPreview,
    ExportBindingView,
    ExportPrepare,
    ExportPreview,
    ExportRelease,
    ExportRunView,
    ExportView,
)
from click.core import ParameterSource

from bid_cli.export_client import download_export

app = typer.Typer()
binding_app = typer.Typer()
run_app = typer.Typer()
app.add_typer(binding_app, name="binding")
app.add_typer(run_app, name="run")

JsonOption = Annotated[
    bool, typer.Option("--json", help="Emit the versioned machine-readable result")
]


def _helpers():
    from bid_cli import main

    return main.call, main.client, main.emit


def _load_input(
    path: Path,
    model: type[Contract],
    ctx: typer.Context,
    cli_values: dict[str, bool],
    max_bytes: int = 512 * 1024,
) -> dict:
    if path.stat().st_size > max_bytes:
        raise ServiceError("input_too_large", "JSON input exceeds the command limit", 400, 2)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ServiceError("invalid_input", "Export input must be a JSON object", 400, 2)
    for name, cli_value in cli_values.items():
        explicit_cli = ctx.get_parameter_source(name) == ParameterSource.COMMANDLINE
        if name in raw and explicit_cli and raw[name] != cli_value:
            raise ServiceError(
                "conflicting_input_option",
                f"{name} differs between --input and the command option",
                400,
                2,
            )
        if explicit_cli or name not in raw:
            raw[name] = cli_value
    return model.model_validate(raw).model_dump(mode="json")


def _preview_exit(body: dict, model: type[ExportPreview] | type[ExportBindingPreview]) -> int:
    data = dict(body.get("data", {}))
    data.pop("error", None)
    try:
        preview = model.model_validate(data)
    except ValueError:
        raise ServiceError(
            "invalid_server_response", "Server returned an invalid export preview", 502, 4
        ) from None
    blocked = any(issue.severity == "block" for issue in preview.issues)
    if blocked:
        body["ok"] = False
        return 2
    return 0


async def _request_preview(path: str, request: dict) -> dict:
    _, client, _ = _helpers()
    runtime = client()
    saved = runtime.state.load()
    headers = {
        "Authorization": f"Bearer {saved['session']}",
        "X-Org-Id": saved["org_id"],
    }
    try:
        async with runtime.transport() as transport:
            response = await transport.request("POST", path, headers=headers, json=request)
    except httpx.TransportError as exc:
        raise ServiceError(
            "network_unavailable", "Server is unavailable or timed out", 503, 3
        ) from exc
    try:
        body = Result.model_validate(response.json()).model_dump(mode="json")
    except (ValueError, TypeError):
        raise ServiceError(
            "invalid_server_response", "Server returned an invalid response", 502, 3
        ) from None
    if response.is_success:
        return body
    data = body.get("data", {})
    if data.get("dry_run") is True and isinstance(data.get("error"), dict):
        return body
    error = data.get("error", {})
    raise ServiceError(
        error.get("code", "server_error"),
        error.get("message", "Server request failed"),
        response.status_code,
        error.get("exit_code", 4),
    )


def _partial_exit(body: dict) -> int:
    try:
        export = ExportView.model_validate(body.get("data", {}))
    except ValueError:
        raise ServiceError(
            "invalid_server_response", "Server returned an invalid released export", 502, 4
        ) from None
    if export.completion == "partial":
        body["ok"] = False
        return 5
    return 0


async def _wait_for_run(run_id: UUID, limit_seconds: float) -> dict:
    _, client, _ = _helpers()
    deadline = time.monotonic() + limit_seconds
    while time.monotonic() < deadline:
        body = await client().request("GET", f"/export-runs/{run_id}")
        try:
            run = ExportRunView.model_validate(body.get("data", {}))
        except ValueError:
            raise ServiceError(
                "invalid_server_response", "Server returned an invalid export run", 502, 4
            ) from None
        if run.state in {"awaiting_release", "released"}:
            return body
        if run.state == "invalidated":
            raise ServiceError(
                "export_input_changed",
                "Export inputs changed while the candidate was rendering",
                409,
                3,
            )
        if run.state in {"failed", "cancelled"}:
            job = await client().request("GET", f"/jobs/{run.render_job_id}")
            error = job.get("data", {}).get("error")
            if isinstance(error, dict):
                raise ServiceError(
                    error.get("code", f"export_render_{run.state}"),
                    error.get("message", f"Export rendering {run.state}"),
                    400,
                    error.get("exit_code", 4),
                )
            raise ServiceError(
                f"export_render_{run.state}",
                f"Export rendering {run.state}",
                400,
                4,
            )
        await asyncio.sleep(0.3)
    raise ServiceError(
        "wait_timeout", "Export rendering is still active; inspect the run again", 408, 3
    )


@binding_app.command("create")
def binding_create(
    ctx: typer.Context,
    input: Annotated[Path, typer.Option()],
    dry_run: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    call, _, emit = _helpers()
    request = _load_input(input, ExportBindingCreate, ctx, {"dry_run": dry_run})
    body = (
        asyncio.run(_request_preview("/export-template-bindings", request))
        if request["dry_run"]
        else call("POST", "/export-template-bindings", json=request)
    )
    if request["dry_run"]:
        exit_code = _preview_exit(body, ExportBindingPreview)
    else:
        try:
            ExportBindingView.model_validate(body.get("data", {}))
        except ValueError:
            raise ServiceError(
                "invalid_server_response", "Server returned an invalid export binding", 502, 4
            ) from None
        exit_code = 0
    emit(body, "export binding create", json_output, exit_code)


@binding_app.command("list")
def binding_list(
    template_revision: Annotated[UUID, typer.Option("--template-revision")],
    json_output: JsonOption = False,
):
    call, _, emit = _helpers()
    emit(
        call(
            "GET",
            "/export-template-bindings",
            params={"template_revision_id": str(template_revision)},
        ),
        "export binding list",
        json_output,
    )


@app.command("prepare")
def prepare(
    ctx: typer.Context,
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    dry_run: Annotated[bool, typer.Option()] = False,
    retry: Annotated[bool, typer.Option()] = False,
    wait: Annotated[bool, typer.Option()] = False,
    timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
    json_output: JsonOption = False,
):
    call, _, emit = _helpers()
    request = _load_input(
        input,
        ExportPrepare,
        ctx,
        {"dry_run": dry_run, "retry": retry},
    )
    body = (
        asyncio.run(_request_preview(f"/tasks/{task}/export-runs", request))
        if request["dry_run"]
        else call("POST", f"/tasks/{task}/export-runs", json=request)
    )
    if request["dry_run"]:
        emit(body, "export prepare", json_output, _preview_exit(body, ExportPreview))
        return
    try:
        run = ExportRunView.model_validate(body.get("data", {}))
    except ValueError:
        raise ServiceError(
            "invalid_server_response", "Server returned an invalid export run", 502, 4
        ) from None
    if wait and run.state not in {"awaiting_release", "released"}:
        body = asyncio.run(_wait_for_run(run.id, timeout))
    emit(body, "export prepare", json_output)


@run_app.command("show")
def run_show(
    id: Annotated[UUID, typer.Option("--id")],
    json_output: JsonOption = False,
):
    call, _, emit = _helpers()
    emit(call("GET", f"/export-runs/{id}"), "export run show", json_output)


@app.command("release")
def release(
    ctx: typer.Context,
    run: Annotated[UUID, typer.Option("--run")],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    call, _, emit = _helpers()
    request = _load_input(input, ExportRelease, ctx, {})
    body = call("POST", f"/export-runs/{run}/release", json=request)
    emit(body, "export release", json_output, _partial_exit(body))


@app.command("list")
def export_list(
    task: Annotated[UUID, typer.Option()],
    json_output: JsonOption = False,
):
    call, _, emit = _helpers()
    emit(call("GET", f"/tasks/{task}/exports"), "export list", json_output)


@app.command("show")
def export_show(
    id: Annotated[UUID, typer.Option("--id")],
    json_output: JsonOption = False,
):
    call, _, emit = _helpers()
    emit(call("GET", f"/exports/{id}"), "export show", json_output)


@app.command("download")
def export_download(
    id: Annotated[UUID, typer.Option("--id")],
    output: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    _, client, emit = _helpers()
    body = asyncio.run(download_export(client(), id, output))
    exit_code = 5 if body.get("data", {}).get("completion") == "partial" else 0
    emit(body, "export download", json_output, exit_code)
