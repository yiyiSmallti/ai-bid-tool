"""Synchronous requirement review commands using the shared authenticated routes."""

import json
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.requirement_confirmation import (
    ManualEntryCreate,
    ManualRequirementInput,
    PageQuery,
    RequirementConfirmBatch,
    RequirementDecision,
    ReviewPageQuery,
)
from app.schemas.team_workflow import TaskProgressQuery
from pydantic import ValidationError

INPUT_BYTE_LIMIT = 256 * 1024


def _input_contract(path, model):
    if not path.is_file():
        raise ServiceError("invalid_input", "Input must be an existing JSON file", 400, 2)
    with path.open("rb") as handle:
        content = handle.read(INPUT_BYTE_LIMIT + 1)
    if len(content) > INPUT_BYTE_LIMIT:
        raise ServiceError("input_too_large", "JSON input exceeds the command limit", 400, 2)
    return model.model_validate(json.loads(content)).model_dump(mode="json")


class RequirementProgressInvocation(TaskProgressQuery):
    extraction_job_id: UUID
    view: Literal["requirement-review"] = "requirement-review"


def _query(model, **values):
    try:
        body = model.model_validate(values).model_dump(mode="json", exclude_none=True)
    except ValidationError:
        raise ServiceError(
            "invalid_input", "Input does not match the requirement review schema", 400, 2
        ) from None
    return {
        key: str(value).lower() if isinstance(value, bool) else value for key, value in body.items()
    }


def register(req_app: typer.Typer):
    from bid_cli import main as cli

    @req_app.command("review-list")
    def review_list(
        task: Annotated[UUID, typer.Option()],
        job: Annotated[UUID, typer.Option()],
        state: Annotated[str | None, typer.Option()] = None,
        category: Annotated[str | None, typer.Option()] = None,
        starred: Annotated[bool | None, typer.Option("--starred/--no-starred")] = None,
        origin: Annotated[str | None, typer.Option()] = None,
        rejected_job: Annotated[UUID | None, typer.Option()] = None,
        rejected_index: Annotated[int | None, typer.Option(min=0)] = None,
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        json_output: cli.JsonOption = False,
    ):
        params = _query(
            ReviewPageQuery,
            state=state,
            category=category,
            starred=starred,
            origin=origin,
            rejected_job_id=rejected_job,
            rejected_index=rejected_index,
            cursor=cursor,
            limit=limit,
        )
        cli.emit(
            cli.call("GET", f"/tasks/{task}/extractions/{job}/requirement-reviews", params=params),
            "req review-list",
            json_output,
        )

    @req_app.command("show")
    def show(id: Annotated[UUID, typer.Option("--id")], json_output: cli.JsonOption = False):
        cli.emit(cli.call("GET", f"/requirements/{id}/review"), "req show", json_output)

    @req_app.command("review-history")
    def review_history(
        id: Annotated[UUID, typer.Option("--id")],
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call(
                "GET",
                f"/requirements/{id}/review-history",
                params=_query(PageQuery, cursor=cursor, limit=limit),
            ),
            "req review-history",
            json_output,
        )

    @req_app.command("rejected")
    def rejected(
        task: Annotated[UUID, typer.Option()],
        job: Annotated[UUID, typer.Option()],
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call(
                "GET",
                f"/tasks/{task}/extractions/{job}/rejected-items",
                params=_query(PageQuery, cursor=cursor, limit=limit),
            ),
            "req rejected",
            json_output,
        )

    @req_app.command("add")
    def add(
        task: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        dry_run: Annotated[bool, typer.Option()] = False,
        json_output: cli.JsonOption = False,
    ):
        body = _input_contract(input, ManualRequirementInput if dry_run else ManualEntryCreate)
        suffix = "manual-preview" if dry_run else "manual"
        cli.emit(
            cli.call("POST", f"/tasks/{task}/requirements/{suffix}", json=body),
            "req add",
            json_output,
        )

    def decision(id, input, action, json_output):
        body = _input_contract(input, RequirementDecision)
        if body["action"] != action:
            raise ServiceError(
                "invalid_input", "Decision action must match the CLI command", 400, 2
            )
        cli.emit(
            cli.call("POST", f"/requirements/{id}/review-decisions", json=body),
            "req " + action,
            json_output,
        )

    @req_app.command("confirm")
    def confirm(
        id: Annotated[UUID, typer.Option("--id")],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        decision(id, input, "confirm", json_output)

    @req_app.command("reopen")
    def reopen(
        id: Annotated[UUID, typer.Option("--id")],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        decision(id, input, "reopen", json_output)

    @req_app.command("confirm-batch")
    def confirm_batch(
        task: Annotated[UUID, typer.Option()],
        job: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        body = _input_contract(input, RequirementConfirmBatch)
        cli.emit(
            cli.call(
                "POST", f"/tasks/{task}/extractions/{job}/requirement-confirmations", json=body
            ),
            "req confirm-batch",
            json_output,
        )
