"""Gateway installation probe, a member's own usage and project members over a `bid_` token.

Accounts are managed only in an administrator's signed-in client, so no command
here creates, changes or resets a member.
"""

from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.members import (
    ProjectMemberAdd,
    ProjectOwnerChange,
    ProjectSharingUpdate,
)
from pydantic import ValidationError

COMMAND_INPUTS = {
    "installation show": None,
    "member usage": None,
    "task member list": None,
    "task member add": ProjectMemberAdd,
    "task member remove": None,
    "task owner": ProjectOwnerChange,
    "task sharing": ProjectSharingUpdate,
}


def _body(model, **values):
    try:
        return model.model_validate(values).model_dump(mode="json", exclude_none=True)
    except ValidationError:
        raise ServiceError(
            "invalid_input", "Input does not match the member schema", 400, 2
        ) from None


def register(app: typer.Typer, task_app: typer.Typer):
    from bid_cli import main as cli

    installation_app, member_app, project_app = typer.Typer(), typer.Typer(), typer.Typer()
    app.add_typer(installation_app, name="installation")
    app.add_typer(member_app, name="member")
    task_app.add_typer(project_app, name="member")

    @installation_app.command("show")
    def installation_show(json_output: cli.JsonOption = False):
        cli.emit(
            cli.call("GET", "/installation", authenticated=False), "installation show", json_output
        )

    @member_app.command("usage")
    def member_usage(
        member: Annotated[UUID, typer.Option()],
        month: Annotated[
            str | None, typer.Option(help="YYYY-MM in UTC; default this month")
        ] = None,
        json_output: cli.JsonOption = False,
    ):
        params = {"month": month} if month else None
        result = cli.call("GET", f"/members/{member}/usage", params=params)
        cli.emit(result, "member usage", json_output)

    @project_app.command("list")
    def task_member_list(
        task: Annotated[UUID, typer.Option()], json_output: cli.JsonOption = False
    ):
        cli.emit(cli.call("GET", f"/tasks/{task}/members"), "task member list", json_output)

    @project_app.command("add")
    def task_member_add(
        task: Annotated[UUID, typer.Option()],
        member: Annotated[list[UUID], typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        body = _body(ProjectMemberAdd, member_ids=[str(item) for item in member])
        result = cli.call("POST", f"/tasks/{task}/members", json=body)
        cli.emit(result, "task member add", json_output)

    @project_app.command("remove")
    def task_member_remove(
        task: Annotated[UUID, typer.Option()],
        member: Annotated[UUID, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        result = cli.call("DELETE", f"/tasks/{task}/members/{member}")
        cli.emit(result, "task member remove", json_output)

    @task_app.command("owner")
    def task_owner(
        task: Annotated[UUID, typer.Option()],
        member: Annotated[UUID, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        body = _body(ProjectOwnerChange, member_id=str(member))
        cli.emit(cli.call("POST", f"/tasks/{task}/owner", json=body), "task owner", json_output)

    @task_app.command("sharing")
    def task_sharing(
        task: Annotated[UUID, typer.Option()],
        export_to_personal: Annotated[
            bool, typer.Option("--export-to-personal/--no-export-to-personal")
        ],
        json_output: cli.JsonOption = False,
    ):
        body = _body(ProjectSharingUpdate, export_to_personal_allowed=export_to_personal)
        result = cli.call("PATCH", f"/tasks/{task}/sharing", json=body)
        cli.emit(result, "task sharing", json_output)
