"""Bounded masked reads and stdin-only checked writes over the shared v4 API."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.confidential_contracts import ConfidentialFieldView, ConfidentialValueView
from app.schemas.contracts import Result
from app.schemas.management_pages import (
    ConfidentialHistoryQuery,
    ConfidentialQuery,
    ConfidentialValueRevisionSet,
    Page,
)
from pydantic import ValidationError

from bid_cli.confidential import _stdin_value

COMMAND_INPUTS = {
    "confidential field browse": ConfidentialQuery,
    "confidential browse": ConfidentialQuery,
    "confidential history-page": ConfidentialHistoryQuery,
    "confidential set-checked": ConfidentialValueRevisionSet,
}
COMMAND_DATA = {"confidential set-checked": ConfidentialValueView}
COMMAND_ITEMS = {
    "confidential field browse": ConfidentialFieldView,
    "confidential browse": ConfidentialValueView,
    "confidential history-page": ConfidentialValueView,
}


def validated(body: dict, command: str) -> dict:
    """Reject malformed or plaintext-bearing projections before output."""
    try:
        if set(body) != {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}:
            raise ValueError("invalid envelope")
        result = Result.model_validate(body)
        if not result.ok or result.command != command:
            raise ValueError("invalid command result")
        if command in COMMAND_DATA:
            COMMAND_DATA[command].model_validate(result.data)
            if result.items:
                raise ValueError("write receipt must have no items")
        else:
            Page[COMMAND_ITEMS[command]].model_validate(
                {"data": result.data, "items": result.items}
            )
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise ServiceError(
            "invalid_server_response", "Server returned an invalid confidential projection", 502, 4
        ) from exc
    return body


def register(confidential_app: typer.Typer, field_app: typer.Typer) -> None:
    from bid_cli import main as cli

    def send(path, command, json_output, payload):
        cli.emit(validated(cli.call("POST", path, json=payload), command), command, json_output)

    @field_app.command("browse")
    def field_browse(
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        send(
            "/management/confidential-fields/query",
            "confidential field browse",
            json_output,
            cli.input_contract(input, ConfidentialQuery),
        )

    @confidential_app.command("browse")
    def browse(
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        send(
            "/management/confidential-values/query",
            "confidential browse",
            json_output,
            cli.input_contract(input, ConfidentialQuery),
        )

    @confidential_app.command("history-page")
    def history_page(
        field: Annotated[UUID, typer.Option()],
        task: Annotated[UUID | None, typer.Option()] = None,
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 25,
        json_output: cli.JsonOption = False,
    ):
        query = ConfidentialHistoryQuery(task_id=task, cursor=cursor, limit=limit)
        send(
            f"/management/confidential-fields/{field}/values/history/query",
            "confidential history-page",
            json_output,
            query.model_dump(mode="json", exclude_none=True),
        )

    @confidential_app.command("set-checked")
    def set_checked(
        field: Annotated[UUID, typer.Option()],
        expected_field_revision: Annotated[int, typer.Option(min=1)],
        expected_value: Annotated[UUID | None, typer.Option()] = None,
        expect_empty: Annotated[bool, typer.Option()] = False,
        value_stdin: Annotated[bool, typer.Option("--value-stdin")] = False,
        task: Annotated[UUID | None, typer.Option()] = None,
        json_output: cli.JsonOption = False,
    ):
        if (expected_value is not None) == expect_empty:
            raise ServiceError(
                "invalid_input", "Pass exactly one of --expected-value or --expect-empty", 400, 2
            )
        if not value_stdin:
            raise ServiceError(
                "value_stdin_required", "Pass --value-stdin and pipe the value", 400, 2
            )
        command = ConfidentialValueRevisionSet(
            value=_stdin_value(),
            task_id=task,
            expected_field_revision=expected_field_revision,
            expected_value_id=expected_value,
        )
        # The contract excludes the secret from generic serialization. Construct its
        # one-time transport explicitly and retain required null absence assertions.
        payload = {
            "value": command.value,
            "task_id": str(command.task_id) if command.task_id else None,
            "expected_field_revision": command.expected_field_revision,
            "expected_value_id": str(command.expected_value_id)
            if command.expected_value_id
            else None,
        }
        send(
            f"/management/confidential-fields/{field}/values",
            "confidential set-checked",
            json_output,
            payload,
        )
