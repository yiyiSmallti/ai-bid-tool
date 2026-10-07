"""Bounded org-memory browse over the authenticated Result 4.0 API."""

from pathlib import Path
from typing import Annotated

import typer
from app.core.errors import ServiceError
from app.schemas.contracts import Result
from app.schemas.management_pages import MemoryQuery, Page
from app.schemas.memory_contracts import MemoryView
from pydantic import ValidationError

COMMAND_INPUTS = {"memory browse": MemoryQuery}
COMMAND_ITEMS = {"memory browse": MemoryView}


def validated(body: dict) -> dict:
    """Reject malformed or non-org browse receipts before emitting success."""
    try:
        if set(body) != {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}:
            raise ValueError("invalid envelope")
        result = Result.model_validate(body)
        if not result.ok or result.command != "memory browse":
            raise ValueError("invalid command result")
        page = Page[MemoryView].model_validate({"data": result.data, "items": result.items})
        if any(item.current.target.scope != "org" for item in page.items):
            raise ValueError("unsupported memory scope")
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise ServiceError(
            "invalid_server_response", "Server returned an invalid memory projection", 502, 4
        ) from exc
    return body


def register(memory_app: typer.Typer):
    from bid_cli import main as cli

    @memory_app.command("browse")
    def browse(
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        body = cli.call(
            "POST",
            "/management/memories/query",
            json=cli.input_contract(input, MemoryQuery),
        )
        cli.emit(validated(body), "memory browse", json_output)
