"""Confidential field commands. Values come only from stdin and are never printed."""

import sys
from typing import Annotated, Literal
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.confidential_contracts import (
    ConfidentialFieldCreate,
    ConfidentialFieldUpdate,
    ConfidentialValueSet,
)
from pydantic import ValidationError

MAX_VALUE_BYTES = 8 * 1024


def _validated(model, payload: dict) -> dict:
    try:
        return model.model_validate(payload).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        raise ServiceError("invalid_input", "Input does not match the contract", 400, 2) from exc


def _stdin_value() -> str:
    # Reading from a terminal would be an interactive prompt; a pipe or file is required.
    if sys.stdin is None or sys.stdin.isatty():
        raise ServiceError(
            "value_stdin_required", "Pipe the value on stdin; prompts are unsupported", 400, 2
        )
    value = sys.stdin.read(MAX_VALUE_BYTES + 1)
    if len(value.encode()) > MAX_VALUE_BYTES:
        raise ServiceError("input_too_large", "The value exceeds the command limit", 400, 2)
    return value.rstrip("\r\n")


def register(app: typer.Typer) -> None:
    """Register `bid confidential` after the main CLI helpers exist."""
    from bid_cli import main as cli

    confidential_app, field_app = typer.Typer(), typer.Typer()
    app.add_typer(confidential_app, name="confidential")
    confidential_app.add_typer(field_app, name="field")

    def field_id(key: str) -> UUID:
        fields = cli.call("GET", "/confidential-fields", params={"archived": "true"})["items"]
        for field in fields:
            if field["key"] == key:
                return UUID(field["id"])
        raise ServiceError("not_found", "Resource not found", 404, 4)

    @field_app.command("add")
    def field_add(
        key: Annotated[str, typer.Option()],
        label: Annotated[str, typer.Option()],
        kind: Annotated[
            Literal["amount", "contact", "identity", "bank_account", "other"], typer.Option()
        ],
        scope: Annotated[Literal["org", "task"], typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        body = _validated(
            ConfidentialFieldCreate, {"key": key, "label": label, "kind": kind, "scope": scope}
        )
        cli.emit(
            cli.call("POST", "/confidential-fields", json=body),
            "confidential field add",
            json_output,
        )

    @field_app.command("list")
    def field_list(
        archived: Annotated[bool, typer.Option()] = False,
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call("GET", "/confidential-fields", params={"archived": str(archived).lower()}),
            "confidential field list",
            json_output,
        )

    @field_app.command("update")
    def field_update(
        key: Annotated[str, typer.Option()],
        expected_revision: Annotated[int, typer.Option()],
        label: Annotated[str | None, typer.Option()] = None,
        archived: Annotated[bool | None, typer.Option("--archived/--active")] = None,
        json_output: cli.JsonOption = False,
    ):
        body = _validated(
            ConfidentialFieldUpdate,
            {"expected_revision": expected_revision, "label": label, "archived": archived},
        )
        cli.emit(
            cli.call("POST", f"/confidential-fields/{field_id(key)}/revisions", json=body),
            "confidential field update",
            json_output,
        )

    @confidential_app.command("set")
    def value_set(
        key: Annotated[str, typer.Option()],
        value_stdin: Annotated[bool, typer.Option("--value-stdin")] = False,
        task: Annotated[UUID | None, typer.Option()] = None,
        json_output: cli.JsonOption = False,
    ):
        if not value_stdin:
            raise ServiceError(
                "value_stdin_required", "Pass --value-stdin and pipe the value", 400, 2
            )
        body = _validated(
            ConfidentialValueSet,
            {"value": _stdin_value(), "task_id": str(task) if task else None},
        )
        cli.emit(
            cli.call("POST", f"/confidential-fields/{field_id(key)}/values", json=body),
            "confidential set",
            json_output,
        )

    @confidential_app.command("list")
    def value_list(
        task: Annotated[UUID | None, typer.Option()] = None,
        json_output: cli.JsonOption = False,
    ):
        params = {"task_id": str(task)} if task else {}
        cli.emit(
            cli.call("GET", "/confidential-values", params=params),
            "confidential list",
            json_output,
        )

    @confidential_app.command("history")
    def value_history(
        key: Annotated[str, typer.Option()],
        task: Annotated[UUID | None, typer.Option()] = None,
        json_output: cli.JsonOption = False,
    ):
        params = {"task_id": str(task)} if task else {}
        cli.emit(
            cli.call("GET", f"/confidential-fields/{field_id(key)}/values", params=params),
            "confidential history",
            json_output,
        )
