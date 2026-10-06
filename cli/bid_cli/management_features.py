"""Feature management projections over the shared authenticated v4 API."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.contracts import Result
from app.schemas.management_pages import (
    FeatureDetailData,
    FeatureHistoryRow,
    FeatureLifecycleData,
    FeatureLifecycleEvent,
    FeatureRow,
    Page,
    PageQuery,
    ResourceDetailQuery,
    ResourceLifecycleSet,
    ResourceQuery,
)
from pydantic import ValidationError

COMMAND_INPUTS = {
    "resource feature browse": ResourceQuery,
    "resource feature show": ResourceDetailQuery,
    "resource feature history": PageQuery,
    "resource feature lifecycle set": ResourceLifecycleSet,
    "resource feature lifecycle history": PageQuery,
}
COMMAND_DATA = {
    "resource feature show": FeatureDetailData,
    "resource feature lifecycle set": FeatureLifecycleData,
}
COMMAND_ITEMS = {
    "resource feature browse": FeatureRow,
    "resource feature history": FeatureHistoryRow,
    "resource feature lifecycle history": FeatureLifecycleEvent,
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
            reference = detail.ref if isinstance(detail, FeatureDetailData) else detail.event.ref
            if reference.kind != "features":
                raise ValueError("wrong resource kind")
            if result.items:
                raise ValueError("detail must have no items")
        else:
            page = Page[COMMAND_ITEMS[command]].model_validate(
                {"data": result.data, "items": result.items}
            )
            if any(item.ref.kind != "features" for item in page.items):
                raise ValueError("wrong resource kind")
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise ServiceError(
            "invalid_server_response", "Server returned an invalid feature projection", 502, 4
        ) from exc
    return body


def register(feature_app: typer.Typer):
    from bid_cli import main as cli

    lifecycle_app = typer.Typer()
    feature_app.add_typer(lifecycle_app, name="lifecycle")
    prefix = "/management/resources/features"

    def send(method, path, command, json_output, **kwargs):
        cli.emit(validated(cli.call(method, path, **kwargs), command), command, json_output)

    @feature_app.command("browse")
    def browse(
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        send(
            "POST",
            prefix + "/query",
            "resource feature browse",
            json_output,
            json=cli.input_contract(input, ResourceQuery),
        )

    @feature_app.command("show")
    def show(
        id: Annotated[UUID, typer.Option()],
        revision: Annotated[int | None, typer.Option(min=1)] = None,
        json_output: cli.JsonOption = False,
    ):
        query = ResourceDetailQuery(revision=revision)
        send(
            "GET",
            f"{prefix}/{id}",
            "resource feature show",
            json_output,
            params=query.model_dump(mode="json", exclude_none=True),
        )

    @feature_app.command("history")
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
            "resource feature history",
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
            "resource feature lifecycle set",
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
            "resource feature lifecycle history",
            json_output,
            json=query.model_dump(mode="json", exclude_none=True),
        )
