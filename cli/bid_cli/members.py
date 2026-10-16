"""Gateway installation probe, member and project member management over a `bid_` token.

A one-time password is written to a new owner-only file, like a token secret,
so it never reaches stdout, the JSON receipt or a shell log.
"""

import os
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.members import (
    MemberCreate,
    MemberQuotaSet,
    MemberUpdate,
    ProjectMemberAdd,
    ProjectOwnerChange,
    ProjectSharingUpdate,
)
from pydantic import ValidationError

COMMAND_INPUTS = {
    "installation show": None,
    "member list": None,
    "member create": MemberCreate,
    "member update": MemberUpdate,
    "member reset-password": None,
    "member revoke-sessions": None,
    "member quota": MemberQuotaSet,
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


def _claim(output: Path) -> None:
    if output.exists():
        raise ServiceError("existing_password_file", "Password output file already exists", 400, 2)


def _store_password(body: dict, output: Path) -> dict:
    secret = body["data"].pop("initial_password")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(secret)
    body["data"]["password_file"] = str(output)
    return body


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

    @member_app.command("list")
    def member_list(json_output: cli.JsonOption = False):
        cli.emit(cli.call("GET", "/members"), "member list", json_output)

    @member_app.command("create")
    def member_create(
        login: Annotated[str, typer.Option()],
        display_name: Annotated[str, typer.Option()],
        output: Annotated[Path, typer.Option()],
        role: Annotated[str, typer.Option()] = "member",
        grant: Annotated[list[str] | None, typer.Option()] = None,
        json_output: cli.JsonOption = False,
    ):
        body = _body(
            MemberCreate, login=login, display_name=display_name, role=role, grants=grant or []
        )
        _claim(output)
        result = cli.call("POST", "/members", json=body)
        cli.emit(_store_password(result, output), "member create", json_output)

    @member_app.command("update")
    def member_update(
        member: Annotated[UUID, typer.Option()],
        display_name: Annotated[str | None, typer.Option()] = None,
        role: Annotated[str | None, typer.Option()] = None,
        grant: Annotated[list[str] | None, typer.Option(help="Replaces all grants")] = None,
        clear_grants: Annotated[bool, typer.Option()] = False,
        status: Annotated[str | None, typer.Option()] = None,
        json_output: cli.JsonOption = False,
    ):
        if clear_grants and grant:
            raise ServiceError("invalid_input", "Use --grant or --clear-grants, not both", 400, 2)
        grants = [] if clear_grants else grant
        body = _body(
            MemberUpdate, display_name=display_name, role=role, grants=grants, status=status
        )
        if not body:
            raise ServiceError("invalid_input", "Nothing to update", 400, 2)
        cli.emit(cli.call("PATCH", f"/members/{member}", json=body), "member update", json_output)

    @member_app.command("reset-password")
    def member_reset_password(
        member: Annotated[UUID, typer.Option()],
        output: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        _claim(output)
        result = cli.call("POST", f"/members/{member}/password-reset")
        cli.emit(_store_password(result, output), "member reset-password", json_output)

    @member_app.command("revoke-sessions")
    def member_revoke_sessions(
        member: Annotated[UUID, typer.Option()], json_output: cli.JsonOption = False
    ):
        result = cli.call("DELETE", f"/members/{member}/sessions")
        cli.emit(result, "member revoke-sessions", json_output)

    @member_app.command("quota")
    def member_quota(
        member: Annotated[UUID, typer.Option()],
        tokens: Annotated[int | None, typer.Option(help="Monthly company-model token cap")] = None,
        clear: Annotated[bool, typer.Option(help="Remove the cap")] = False,
        json_output: cli.JsonOption = False,
    ):
        if clear == (tokens is not None):
            raise ServiceError("invalid_input", "Use exactly one of --tokens or --clear", 400, 2)
        body = MemberQuotaSet(monthly_company_tokens=tokens).model_dump(mode="json")
        result = cli.call("PUT", f"/members/{member}/quota", json=body)
        cli.emit(result, "member quota", json_output)

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
