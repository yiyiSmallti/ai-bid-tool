"""Bounded, noninteractive task board commands over the shared authenticated API."""

from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.requirement_confirmation import RequirementBoardQuery
from app.schemas.task_board import BoardQuery, PageQuery
from pydantic import ValidationError


def _body(model, **values):
    try:
        return model.model_validate(values).model_dump(mode="json", exclude_none=True)
    except ValidationError:
        raise ServiceError(
            "invalid_input", "Input does not match the task board schema", 400, 2
        ) from None


def _page(cursor, limit):
    return _body(PageQuery, cursor=cursor, limit=limit)


def register(task_app: typer.Typer):
    from bid_cli import main as cli

    @task_app.command("progress")
    def progress(
        task: Annotated[UUID, typer.Option()],
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=20)] = 20,
        job: Annotated[UUID | None, typer.Option()] = None,
        view: Annotated[str | None, typer.Option()] = None,
        json_output: cli.JsonOption = False,
    ):
        if view not in {None, "requirement-review"} or (view is None) != (job is None):
            raise ServiceError(
                "invalid_input",
                "Requirement review progress requires --view requirement-review and --job",
                400,
                2,
            )
        params = _page(cursor, limit)
        if view is not None:
            params.update(view=view, extraction_job_id=str(job))
        cli.emit(
            cli.call("GET", f"/tasks/{task}/progress", params=params),
            "task progress",
            json_output,
        )

    @task_app.command("board")
    def board(
        task: Annotated[UUID, typer.Option()],
        job: Annotated[UUID, typer.Option()],
        bucket: Annotated[str | None, typer.Option()] = None,
        category: Annotated[str | None, typer.Option()] = None,
        starred: Annotated[bool, typer.Option()] = False,
        domain: Annotated[str | None, typer.Option()] = None,
        blocker: Annotated[str | None, typer.Option()] = None,
        mine: Annotated[bool, typer.Option()] = False,
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        view: Annotated[str | None, typer.Option()] = None,
        json_output: cli.JsonOption = False,
    ):
        if view not in {None, "requirement-review"}:
            raise ServiceError("invalid_input", "Unsupported task board view", 400, 2)
        params = _body(
            RequirementBoardQuery if view is not None else BoardQuery,
            extraction_job_id=job,
            bucket=bucket,
            category=category,
            starred=True if starred else None,
            review_domain=domain,
            blocker=blocker,
            mine=mine,
            cursor=cursor,
            limit=limit,
        )
        params = {
            key: str(value).lower() if isinstance(value, bool) else value
            for key, value in params.items()
        }
        if view is not None:
            params["view"] = view
        cli.emit(cli.call("GET", f"/tasks/{task}/board", params=params), "task board", json_output)

    @task_app.command("activity")
    def activity(
        task: Annotated[UUID, typer.Option()],
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call("GET", f"/tasks/{task}/activity", params=_page(cursor, limit)),
            "task activity",
            json_output,
        )

    @task_app.command("events")
    def events(
        task: Annotated[UUID, typer.Option()],
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 100,
        wait_seconds: Annotated[int, typer.Option(min=0, max=25)] = 0,
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call(
                "GET",
                f"/tasks/{task}/events/poll",
                params={**_page(cursor, limit), "wait_seconds": wait_seconds},
            ),
            "task events",
            json_output,
        )
