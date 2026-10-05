"""Noninteractive budget commands using the same human-gated tenant API."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.budget_contracts import LowBalancePolicySet, TaskBudgetSet
from pydantic import ValidationError


def _input(path: Path, schema: type[TaskBudgetSet] | type[LowBalancePolicySet]) -> dict:
    try:
        with path.open("rb") as handle:
            content = handle.read(65537)
        if len(content) > 65536:
            raise ServiceError("input_too_large", "Budget input exceeds 64 KiB", 400, 2)
        return schema.model_validate_json(content).model_dump(mode="json")
    except OSError:
        raise ServiceError("missing_file", "Budget input cannot be read", 400, 2) from None
    except ValidationError:
        raise ServiceError(
            "invalid_input", "Budget input does not match the command schema", 400, 2
        ) from None


def register(task_app: typer.Typer, billing_app: typer.Typer) -> None:
    from bid_cli import main as cli

    budget_app, alert_app = typer.Typer(), typer.Typer()
    task_app.add_typer(budget_app, name="budget")
    billing_app.add_typer(alert_app, name="alert")

    @budget_app.command("show")
    def show(task: Annotated[UUID, typer.Option()], json_output: cli.JsonOption = False):
        cli.emit(cli.call("GET", f"/tasks/{task}/budget"), "task budget show", json_output)

    @budget_app.command("set")
    def change(
        task: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call("PUT", f"/tasks/{task}/budget", json=_input(input, TaskBudgetSet)),
            "task budget set",
            json_output,
        )

    @budget_app.command("history")
    def history(
        task: Annotated[UUID, typer.Option()],
        before_revision: Annotated[int | None, typer.Option(min=1)] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 100,
        json_output: cli.JsonOption = False,
    ):
        params = {"limit": str(limit)}
        if before_revision is not None:
            params["before_revision"] = str(before_revision)
        cli.emit(
            cli.call("GET", f"/tasks/{task}/budget/history", params=params),
            "task budget history",
            json_output,
        )

    @alert_app.command("show")
    def policy_show(json_output: cli.JsonOption = False):
        cli.emit(cli.call("GET", "/billing/low-balance-policy"), "billing alert show", json_output)

    @alert_app.command("set")
    def policy_set(input: Annotated[Path, typer.Option()], json_output: cli.JsonOption = False):
        cli.emit(
            cli.call("PUT", "/billing/low-balance-policy", json=_input(input, LowBalancePolicySet)),
            "billing alert set",
            json_output,
        )

    @billing_app.command("notices")
    def notices(
        before: Annotated[UUID | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 100,
        json_output: cli.JsonOption = False,
    ):
        params = {"limit": str(limit)}
        if before is not None:
            params["before"] = str(before)
        cli.emit(cli.call("GET", "/billing/notices", params=params), "billing notices", json_output)
