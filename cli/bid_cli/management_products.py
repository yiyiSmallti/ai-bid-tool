"""Product management projections over the shared authenticated v4 API."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.contracts import Result
from app.schemas.management_pages import (
    Page,
    PageQuery,
    ResourceDetailData,
    ResourceDetailQuery,
    ResourceHistoryRow,
    ResourceLifecycleData,
    ResourceLifecycleEvent,
    ResourceLifecycleSet,
    ResourceQuery,
    ResourceRow,
)
from pydantic import ValidationError

COMMAND_INPUTS = {
    "resource product browse": ResourceQuery,
    "resource product show": ResourceDetailQuery,
    "resource product history": PageQuery,
    "resource product lifecycle set": ResourceLifecycleSet,
    "resource product lifecycle history": PageQuery,
}
COMMAND_DATA = {
    "resource product show": ResourceDetailData,
    "resource product lifecycle set": ResourceLifecycleData,
}
COMMAND_ITEMS = {
    "resource product browse": ResourceRow,
    "resource product history": ResourceHistoryRow,
    "resource product lifecycle history": ResourceLifecycleEvent,
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
            COMMAND_DATA[command].model_validate(result.data)
            if result.items:
                raise ValueError("detail must have no items")
        else:
            Page[COMMAND_ITEMS[command]].model_validate(
                {"data": result.data, "items": result.items}
            )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise ServiceError(
            "invalid_server_response", "Server returned an invalid product projection", 502, 4
        ) from exc
    return body


def register(product_app: typer.Typer):
    from bid_cli import main as cli

    lifecycle_app = typer.Typer()
    product_app.add_typer(lifecycle_app, name="lifecycle")
    prefix = "/management/resources/products"

    def send(method, path, command, json_output, **kwargs):
        cli.emit(validated(cli.call(method, path, **kwargs), command), command, json_output)

    @product_app.command("browse")
    def browse(
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        send(
            "POST",
            prefix + "/query",
            "resource product browse",
            json_output,
            json=cli.input_contract(input, ResourceQuery),
        )

    @product_app.command("show")
    def show(
        id: Annotated[UUID, typer.Option()],
        revision: Annotated[int | None, typer.Option(min=1)] = None,
        json_output: cli.JsonOption = False,
    ):
        query = ResourceDetailQuery(revision=revision)
        send(
            "GET",
            f"{prefix}/{id}",
            "resource product show",
            json_output,
            params=query.model_dump(mode="json", exclude_none=True),
        )

    @product_app.command("history")
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
            "resource product history",
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
            "resource product lifecycle set",
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
            "resource product lifecycle history",
            json_output,
            json=query.model_dump(mode="json", exclude_none=True),
        )
