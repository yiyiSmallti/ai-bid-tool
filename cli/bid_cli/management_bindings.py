"""Bounded binding inspection over the existing human-authorized v4 API."""

from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.contracts import Result
from app.schemas.export_contracts import ExportBindingView
from app.schemas.management_pages import BindingDetailQuery, BindingQuery, Page

COMMAND_INPUTS = {
    "export binding browse": BindingQuery,
    "export binding show": BindingDetailQuery,
}
COMMAND_DATA = {"export binding show": ExportBindingView}
COMMAND_ITEMS = {"export binding browse": ExportBindingView}


def validated(body: dict, command: str, parent: UUID, binding_id: UUID | None = None) -> dict:
    try:
        if set(body) != {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}:
            raise ValueError("invalid envelope")
        result = Result.model_validate(body)
        if not result.ok or result.command != command:
            raise ValueError("invalid command result")
        if command == "export binding show":
            row = ExportBindingView.model_validate(result.data)
            if result.items or row.id != binding_id:
                raise ValueError("invalid detail")
            rows = [row]
        else:
            rows = (
                Page[ExportBindingView]
                .model_validate({"data": result.data, "items": result.items})
                .items
            )
        if any(row.template_revision_id != parent for row in rows):
            raise ValueError("wrong template revision")
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceError(
            "invalid_server_response", "Server returned an invalid binding projection", 502, 4
        ) from exc
    return body


def register():
    from bid_cli import main as cli
    from bid_cli.export import binding_app

    prefix = "/management/export-bindings"

    @binding_app.command("browse")
    def browse(
        template_revision: Annotated[UUID, typer.Option("--template-revision")],
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 25,
        json_output: cli.JsonOption = False,
    ):
        query = BindingQuery(template_revision_id=template_revision, cursor=cursor, limit=limit)
        command = "export binding browse"
        body = cli.call(
            "POST", prefix + "/query", json=query.model_dump(mode="json", exclude_none=True)
        )
        cli.emit(validated(body, command, template_revision), command, json_output)

    @binding_app.command("show")
    def show(
        id: Annotated[UUID, typer.Option()],
        template_revision: Annotated[UUID, typer.Option("--template-revision")],
        json_output: cli.JsonOption = False,
    ):
        query = BindingDetailQuery(template_revision_id=template_revision)
        command = "export binding show"
        body = cli.call("GET", f"{prefix}/{id}", params=query.model_dump(mode="json"))
        cli.emit(validated(body, command, template_revision, id), command, json_output)
