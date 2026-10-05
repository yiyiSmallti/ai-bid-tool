"""Typed, bounded assessment discovery and opt-in console read helpers."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.console_assessments import (
    AssessmentHistoryQuery,
    AssessmentInputsData,
    AssessmentJobPage,
    AssessmentJobQuery,
    CitationContextData,
    CitationRequest,
)
from app.schemas.contracts import Result
from pydantic import TypeAdapter, ValidationError

app = typer.Typer()
JsonOption = Annotated[
    bool, typer.Option("--json", help="Emit the versioned machine-readable result")
]


def helpers():
    from bid_cli import main

    return main


def require_console(view: str | None, **options) -> bool:
    if view is None:
        if any(value is not None for value in options.values()):
            raise ServiceError("invalid_input", "Console filters require --view console", 400, 2)
        return False
    if view != "console":
        raise ServiceError("invalid_input", "View must be console", 400, 2)
    return True


def history_params(view: str | None, extraction_job: UUID | None, cursor, limit: int) -> dict:
    if not require_console(view, extraction_job=extraction_job):
        return {"limit": limit, **({"cursor": cursor} if cursor is not None else {})}
    request = AssessmentHistoryQuery(extraction_job_id=extraction_job, cursor=cursor, limit=limit)
    return {"view": "console", **request.model_dump(mode="json", exclude_none=True)}


def show_params(
    view: str | None, part: str | None, model, *, cursor, limit, **filters
) -> dict | None:
    if not require_console(view, part=part, cursor=cursor, limit=limit, **filters):
        return None
    part = part or "summary"
    if part in {"summary", "replacement"}:
        if any(value is not None for value in (cursor, limit, *filters.values())):
            raise ServiceError(
                "invalid_input", "This projection does not accept page filters", 400, 2
            )
        return {"view": "console", "part": part}
    request = model(part=part, cursor=cursor, limit=limit or 50, **filters)
    return {"view": "console", **request.model_dump(mode="json", exclude_none=True)}


def validated(body: dict, command: str, *, data_model=None, page_model=None) -> dict:
    """Treat invalid server projections as server errors, preserving complete envelopes."""
    try:
        if (
            not isinstance(body, dict)
            or set(body) != {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
            or body["command"] != command
        ):
            raise ValueError("invalid envelope")
        Result.model_validate(body)
        if data_model is not None:
            TypeAdapter(data_model).validate_python(body["data"])
            if body["items"]:
                raise ValueError("single projection cannot contain items")
        if page_model is not None:
            TypeAdapter(page_model).validate_python({"data": body["data"], "items": body["items"]})
    except (AttributeError, KeyError, TypeError, ValueError, ValidationError) as exc:
        raise ServiceError(
            "invalid_server_response", "Server returned an invalid assessment projection", 502, 4
        ) from exc
    return body


def projection_exit(body: dict) -> int:
    """Paged projections inherit the parent's partial status from the service envelope."""
    data = body["data"]
    if not body["ok"]:
        error = data.get("error")
        if error is not None:
            code = error.get("exit_code") if isinstance(error, dict) else None
            if type(code) is not int or code not in {2, 3, 4, 5}:
                raise ServiceError(
                    "invalid_server_response",
                    "Server returned an invalid assessment exit code",
                    502,
                    4,
                )
            return code
        return 5
    report = data.get("report", {})
    if (
        report.get("completion") == "partial"
        or data.get("unassessable_items", 0) > 0
        or ("total_status" in data and data["total_status"] != "estimated")
    ):
        body["ok"] = False
        return 5
    return 0


@app.command("inputs")
def inputs(
    task: Annotated[UUID, typer.Option()],
    extraction_job: Annotated[UUID, typer.Option("--extraction-job")],
    json_output: JsonOption = False,
):
    cli = helpers()
    body = validated(
        cli.call("GET", f"/tasks/{task}/assessment-inputs", params={"job": str(extraction_job)}),
        "assessment inputs",
        data_model=AssessmentInputsData,
    )
    cli.emit(body, "assessment inputs", json_output, projection_exit(body))


@app.command("jobs")
def jobs(
    task: Annotated[UUID, typer.Option()],
    kind: Annotated[str, typer.Option()],
    extraction_job: Annotated[UUID | None, typer.Option("--extraction-job")] = None,
    cursor: Annotated[str | None, typer.Option()] = None,
    limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
    json_output: JsonOption = False,
):
    cli = helpers()
    query = AssessmentJobQuery.model_validate(
        {"kind": kind, "extraction_job_id": extraction_job, "cursor": cursor, "limit": limit}
    )
    body = validated(
        cli.call(
            "GET", f"/tasks/{task}/jobs", params=query.model_dump(mode="json", exclude_none=True)
        ),
        "assessment jobs",
        page_model=AssessmentJobPage,
    )
    cli.emit(body, "assessment jobs", json_output, projection_exit(body))


@app.command("citation")
def citation(
    task: Annotated[UUID, typer.Option()],
    input: Annotated[Path, typer.Option()],
    json_output: JsonOption = False,
):
    cli = helpers()
    query = cli.input_contract(input, CitationRequest)
    body = validated(
        cli.call("GET", f"/tasks/{task}/assessment-citation", params=query),
        "assessment citation",
        data_model=CitationContextData,
    )
    cli.emit(body, "assessment citation", json_output, projection_exit(body))
