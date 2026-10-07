"""Cloud B05 annotation commands; pixels and rendering remain on the server."""

import asyncio
import json
import time
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.annotation_contracts import (
    HTTP_INPUT_LIMIT,
    AnnotationInput,
    AnnotationListQuery,
    AnnotationPreflightRequest,
    AnnotationReleaseRetry,
    AnnotationSubmit,
)


def read_input(path: Path, model):
    try:
        with path.open("rb") as handle:
            content = handle.read(HTTP_INPUT_LIMIT + 1)
    except OSError:
        raise ServiceError("invalid_input", "Input must be a readable JSON file", 400, 2) from None
    if len(content) > HTTP_INPUT_LIMIT:
        raise ServiceError("input_too_large", "JSON input exceeds the command limit", 413, 2)
    return model.model_validate(json.loads(content))


async def completed(accepted: dict, limit_seconds: float) -> tuple[dict, int]:
    from bid_cli import main as cli

    job_id = accepted["data"]["job_id"]
    deadline = time.monotonic() + limit_seconds
    while time.monotonic() < deadline:
        try:
            terminal = await cli.client().request("GET", f"/jobs/{job_id}")
        except ServiceError as exc:
            exc.job_id = str(job_id)
            raise
        data = terminal["data"]
        if data["status"] in {"failed", "cancelled"}:
            error = data.get("error") or {
                "code": "job_cancelled",
                "message": "Annotation job was cancelled",
                "exit_code": 4,
            }
            terminal["ok"] = False
            terminal["data"] = {"job_id": job_id, "status": data["status"], "error": error}
            return terminal, error.get("exit_code", 4)
        if data["status"] == "succeeded":
            result = data.get("result") or {}
            annotation_id = result.get("annotation_id")
            if not annotation_id:
                raise ServiceError(
                    "invalid_server_response", "Published annotation ID is missing", 502, 4
                )
            try:
                body = await cli.client().request("GET", f"/annotations/{annotation_id}")
            except ServiceError as exc:
                exc.job_id = str(job_id)
                raise
            body["data"] = body["data"]["candidate"]
            body["cost"] = terminal["cost"]
            body["warnings"] = terminal.get("warnings", [])
            return body, 0
        await asyncio.sleep(0.3)
    accepted["ok"] = False
    accepted["data"]["error"] = {
        "code": "wait_timeout",
        "message": "Job is still active; query status or cancel explicitly",
        "exit_code": 3,
    }
    return accepted, 3


def register(evidence_app: typer.Typer):
    from bid_cli import main as cli

    annotation_app, release_app = typer.Typer(), typer.Typer()
    evidence_app.add_typer(annotation_app, name="annotation")
    annotation_app.add_typer(release_app, name="release")

    @evidence_app.command("stamp")
    def stamp(
        task: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        dry_run: Annotated[bool, typer.Option()] = False,
        expected_input_hash: Annotated[str | None, typer.Option()] = None,
        reviewed_source_png_sha256: Annotated[str | None, typer.Option()] = None,
        request_id: Annotated[UUID | None, typer.Option()] = None,
        retry: Annotated[bool, typer.Option()] = False,
        wait: Annotated[bool, typer.Option()] = False,
        timeout: Annotated[float, typer.Option(min=0.01, max=3600)] = 60,
        json_output: cli.JsonOption = False,
    ):
        if dry_run and (
            retry or wait or expected_input_hash or reviewed_source_png_sha256 or request_id
        ):
            raise ServiceError("invalid_input", "Dry-run rejects submit-only flags", 400, 2)
        if not dry_run and not all((expected_input_hash, reviewed_source_png_sha256, request_id)):
            raise ServiceError(
                "invalid_input",
                "Submit requires reviewed source hash, input hash and request ID",
                400,
                2,
            )
        plan = read_input(input, AnnotationInput)
        if dry_run:
            body = AnnotationPreflightRequest(input=plan)
        else:
            if (
                expected_input_hash is None
                or reviewed_source_png_sha256 is None
                or request_id is None
            ):
                raise ServiceError(
                    "invalid_input", "Submit requires exact reviewed bindings", 400, 2
                )
            body = AnnotationSubmit(
                input=plan,
                expected_input_hash=expected_input_hash,
                reviewed_source_png_sha256=reviewed_source_png_sha256,
                request_id=request_id,
                retry=retry,
            )
        result = cli.call("POST", f"/tasks/{task}/annotations", json=body.model_dump(mode="json"))
        if wait:
            result, exit_code = asyncio.run(completed(result, timeout))
        else:
            exit_code = 0
        cli.emit(result, "evidence stamp", json_output, exit_code)

    @annotation_app.command("list")
    def list_candidates(
        task: Annotated[UUID, typer.Option()],
        card: Annotated[UUID | None, typer.Option()] = None,
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        json_output: cli.JsonOption = False,
    ):
        query = AnnotationListQuery(card_id=card, cursor=cursor, limit=limit)
        cli.emit(
            cli.call(
                "GET",
                f"/tasks/{task}/annotations",
                params=query.model_dump(mode="json", exclude_none=True),
            ),
            "evidence annotation list",
            json_output,
        )

    @annotation_app.command("show")
    def show(id: Annotated[UUID, typer.Option("--id")], json_output: cli.JsonOption = False):
        cli.emit(cli.call("GET", f"/annotations/{id}"), "evidence annotation show", json_output)

    @annotation_app.command("preview")
    def preview(id: Annotated[UUID, typer.Option("--id")], json_output: cli.JsonOption = False):
        cli.emit(
            cli.call("GET", f"/annotations/{id}/preview"),
            "evidence annotation preview",
            json_output,
        )

    @annotation_app.command("releases")
    def releases(
        id: Annotated[UUID, typer.Option("--id")],
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        json_output: cli.JsonOption = False,
    ):
        query = AnnotationListQuery(cursor=cursor, limit=limit)
        cli.emit(
            cli.call(
                "GET",
                f"/annotations/{id}/releases",
                params=query.model_dump(mode="json", exclude_none=True),
            ),
            "evidence annotation releases",
            json_output,
        )

    @release_app.command("preview")
    def release_preview(
        id: Annotated[UUID, typer.Option("--id")], json_output: cli.JsonOption = False
    ):
        cli.emit(
            cli.call("GET", f"/annotation-releases/{id}/preview"),
            "evidence annotation release preview",
            json_output,
        )

    @release_app.command("retry")
    def release_retry(
        id: Annotated[UUID, typer.Option("--id")],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        body = read_input(input, AnnotationReleaseRetry)
        cli.emit(
            cli.call("POST", f"/annotations/{id}/releases", json=body.model_dump(mode="json")),
            "evidence annotation release retry",
            json_output,
        )
