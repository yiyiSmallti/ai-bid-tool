"""Template management projections over the shared authenticated v4 API."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.contracts import Result
from app.schemas.management_pages import (
    Page,
    PageQuery,
    ResourceLifecycleSet,
    ResourceQuery,
    TemplateDetailData,
    TemplateDetailQuery,
    TemplateHistoryRow,
    TemplateLifecycleData,
    TemplateLifecycleEvent,
    TemplateRow,
)
from pydantic import ValidationError

COMMAND_INPUTS = {
    "resource template browse": ResourceQuery,
    "resource template show": TemplateDetailQuery,
    "resource template history": PageQuery,
    "resource template lifecycle set": ResourceLifecycleSet,
    "resource template lifecycle history": PageQuery,
}
COMMAND_DATA = {
    "resource template show": TemplateDetailData,
    "resource template lifecycle set": TemplateLifecycleData,
}
COMMAND_ITEMS = {
    "resource template browse": TemplateRow,
    "resource template history": TemplateHistoryRow,
    "resource template lifecycle history": TemplateLifecycleEvent,
}


def validated(body: dict, command: str) -> dict:
    """Reject malformed projections before exposing them as successful CLI results."""
    try:
        if set(body) != {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}:
            raise ValueError("invalid envelope")
        result = Result.model_validate(body)
        if not result.ok or result.command != command:
            raise ValueError("invalid command result")
        if command in COMMAND_DATA:
            detail = COMMAND_DATA[command].model_validate(result.data)
            reference = detail.ref if isinstance(detail, TemplateDetailData) else detail.event.ref
            if reference.kind != "templates":
                raise ValueError("wrong resource kind")
            if result.items:
                raise ValueError("detail must have no items")
        else:
            page = Page[COMMAND_ITEMS[command]].model_validate(
                {"data": result.data, "items": result.items}
            )
            if any(item.ref.kind != "templates" for item in page.items):
                raise ValueError("wrong resource kind")
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise ServiceError(
            "invalid_server_response", "Server returned an invalid template projection", 502, 4
        ) from exc
    return body


def register(template_app: typer.Typer):
    from bid_cli import main as cli

    lifecycle_app = typer.Typer()
    template_app.add_typer(lifecycle_app, name="lifecycle")
    prefix = "/management/resources/templates"

    def send(method, path, command, json_output, **kwargs):
        cli.emit(validated(cli.call(method, path, **kwargs), command), command, json_output)

    @template_app.command("browse")
    def browse(
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        send(
            "POST",
            prefix + "/query",
            "resource template browse",
            json_output,
            json=cli.input_contract(input, ResourceQuery),
        )

    @template_app.command("show")
    def show(
        id: Annotated[UUID, typer.Option()],
        revision: Annotated[int | None, typer.Option(min=1)] = None,
        revision_id: Annotated[UUID | None, typer.Option("--revision-id")] = None,
        json_output: cli.JsonOption = False,
    ):
        query = TemplateDetailQuery(revision=revision, revision_id=revision_id)
        send(
            "GET",
            f"{prefix}/{id}",
            "resource template show",
            json_output,
            params=query.model_dump(mode="json", exclude_none=True),
        )

    @template_app.command("history")
    def history(
        id: Annotated[UUID, typer.Option()],
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 25,
        json_output: cli.JsonOption = False,
    ):
        query = PageQuery(cursor=cursor, limit=limit)
        send(
            "POST",
            f"{prefix}/{id}/history/query",
            "resource template history",
            json_output,
            json=query.model_dump(mode="json", exclude_none=True),
        )

    @lifecycle_app.command("set")
    def lifecycle_set(
        id: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        send(
            "POST",
            f"{prefix}/{id}/lifecycle",
            "resource template lifecycle set",
            json_output,
            json=cli.input_contract(input, ResourceLifecycleSet),
        )

    @lifecycle_app.command("history")
    def lifecycle_history(
        id: Annotated[UUID, typer.Option()],
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 25,
        json_output: cli.JsonOption = False,
    ):
        query = PageQuery(cursor=cursor, limit=limit)
        send(
            "POST",
            f"{prefix}/{id}/lifecycle/history/query",
            "resource template lifecycle history",
            json_output,
            json=query.model_dump(mode="json", exclude_none=True),
        )
