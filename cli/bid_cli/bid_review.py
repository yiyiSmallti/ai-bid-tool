"""Immutable uploaded-bid submissions and explicit local preparation commands."""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas import bid_review as models
from app.schemas.check_contracts import AssessmentJobAccepted, AssessmentListData
from app.schemas.contracts import Result
from pydantic import TypeAdapter

app = typer.Typer()
submissions = typer.Typer()
app.add_typer(submissions, name="submission")
JsonOption = Annotated[bool, typer.Option("--json")]
COMMAND_INPUTS = {
    "review upload": models.BidSubmissionCreate,
    "review prepare": models.BidPrepareRequest,
    "review submission list": models.BidReviewListQuery,
    "review submission show": None,
}
COMMAND_DATA = {
    "review upload": models.BidSubmissionUploaded | models.BidUploadPreview,
    "review prepare": models.BidPreparePreview | AssessmentJobAccepted,
    "review submission list": AssessmentListData,
    "review submission show": models.BidSubmissionDetail,
}
COMMAND_ITEMS = {
    "review submission list": models.BidSubmissionUploaded | models.BidSubmissionView,
}


def _helpers():
    from bid_cli import main

    return main


def _input(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            content = handle.read(128 * 1024 + 1)
        if len(content) > 128 * 1024:
            raise ValueError("oversize metadata")
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("object required")
        return data
    except (OSError, ValueError, UnicodeError) as exc:
        raise ServiceError("invalid_input", "Cannot read bounded JSON metadata", 400, 2) from exc


def validated(body: dict, command: str) -> dict:
    try:
        value = Result.model_validate(body)
        if value.command != command or not value.ok:
            raise ValueError("unexpected result")
        TypeAdapter(COMMAND_DATA[command]).validate_python(value.data)
        if command in COMMAND_ITEMS:
            if len(value.items) > 100:
                raise ValueError("oversize page")
            for item in value.items:
                TypeAdapter(COMMAND_ITEMS[command]).validate_python(item)
        elif value.items:
            raise ValueError("unexpected items")
    except (ValueError, TypeError, KeyError) as exc:
        raise ServiceError(
            "invalid_server_response", "Server returned invalid submission metadata", 502, 4
        ) from exc
    return body


@app.command("upload")
def upload(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    file: Annotated[list[Path], typer.Option()],
    dry_run: Annotated[bool, typer.Option()] = False,
    json_output: JsonOption = False,
):
    cli = _helpers()
    raw = _input(input)
    raw["dry_run"] = dry_run or raw.get("dry_run", False)
    try:
        body = models.BidSubmissionCreate.model_validate(raw)
    except ValueError as exc:
        raise ServiceError("invalid_input", "Invalid submission metadata", 400, 2) from exc
    if len(file) != len(body.files):
        raise ServiceError("invalid_input", "Files must match ordered metadata entries", 400, 2)
    uploads = []
    total = 0
    for path, descriptor in zip(file, body.files, strict=True):
        expected_suffix = ".pdf" if descriptor.media_type == "application/pdf" else ".docx"
        if path.suffix.lower() != expected_suffix:
            raise ServiceError("invalid_document", "File type differs from metadata", 400, 2)
        try:
            with path.open("rb") as handle:
                content = handle.read(descriptor.size_bytes + 1)
        except OSError as exc:
            raise ServiceError("invalid_input", "Cannot read an input file", 400, 2) from exc
        total += len(content)
        if (
            len(content) != descriptor.size_bytes
            or hashlib.sha256(content).hexdigest() != descriptor.sha256
            or total > models.SUBMISSION_BYTE_LIMIT
        ):
            raise ServiceError("invalid_input", "File bytes differ from metadata", 400, 2)
        uploads.append(("files", (path.name, content, descriptor.media_type)))
    result = cli.call(
        "POST",
        f"/tasks/{task}/bid-submissions",
        data={"metadata": body.model_dump_json()},
        files=uploads,
    )
    cli.emit(validated(result, "review upload"), "review upload", json_output)


@app.command("prepare")
def prepare(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    dry_run: Annotated[bool, typer.Option()] = False,
    expected_input_hash: Annotated[str | None, typer.Option()] = None,
    preflight_token: Annotated[str | None, typer.Option()] = None,
    retry: Annotated[bool, typer.Option()] = False,
    wait: Annotated[bool, typer.Option()] = False,
    timeout: Annotated[float, typer.Option(min=0.1, max=3600)] = 120,
    json_output: JsonOption = False,
):
    cli = _helpers()
    raw = _input(input)
    raw["dry_run"] = dry_run or raw.get("dry_run", False)
    if expected_input_hash is not None:
        raw["expected_input_hash"] = expected_input_hash
    if preflight_token is not None:
        raw["preflight_token"] = preflight_token
    raw["retry"] = retry or raw.get("retry", False)
    try:
        body = models.BidPrepareRequest.model_validate(raw)
    except ValueError as exc:
        raise ServiceError(
            "invalid_input", "Invalid preparation metadata or receipt", 400, 2
        ) from exc
    if body.dry_run and wait:
        raise ServiceError("invalid_input", "A preview creates no job to wait for", 400, 2)
    result = validated(
        cli.call(
            "POST",
            f"/tasks/{task}/bid-submissions/{body.submission_id}/prepare",
            json=body.model_dump(mode="json"),
        ),
        "review prepare",
    )
    if wait:
        job_id = AssessmentJobAccepted.model_validate(result["data"]).job_id
        try:
            terminal = asyncio.run(cli.wait_for_job(job_id, timeout))
        except ServiceError as exc:
            exc.job_id = str(job_id)
            raise
        if terminal["data"].get("status") in {"failed", "cancelled"}:
            code = (terminal["data"].get("error") or {}).get("exit_code", 4)
            code = code if type(code) is int and code in {2, 3, 4} else 4
            cli.emit(terminal, "review prepare", json_output, code)
        result = validated(
            cli.call("GET", f"/bid-submissions/{body.submission_id}"), "review submission show"
        )
        try:
            result["data"] = models.BidSubmissionView.model_validate(
                result["data"]["submission"]
            ).model_dump(mode="json")
        except (KeyError, ValueError) as exc:
            raise ServiceError(
                "invalid_response", "Completed preparation has no fixed inventory", 502, 4
            ) from exc
        result["cost"] = terminal["cost"]
    cli.emit(result, "review prepare", json_output)


@submissions.command("list")
def list_submissions(
    task: Annotated[UUID, typer.Option()],
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
    json_output: JsonOption = False,
):
    cli = _helpers()
    params = models.BidReviewListQuery(cursor=cursor, limit=limit).model_dump(exclude_none=True)
    body = cli.call("GET", f"/tasks/{task}/bid-submissions", params=params)
    cli.emit(validated(body, "review submission list"), "review submission list", json_output)


@submissions.command("show")
def show(
    id: Annotated[UUID, typer.Option("--id")],
    json_output: JsonOption = False,
):
    cli = _helpers()
    body = cli.call("GET", f"/bid-submissions/{id}")
    cli.emit(validated(body, "review submission show"), "review submission show", json_output)
