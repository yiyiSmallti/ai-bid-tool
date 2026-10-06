"""Bounded, noninteractive task workflow commands over the shared authenticated API."""

import json
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.requirement_confirmation import RequirementBoardQuery
from app.schemas.team_workflow import (
    BoardQuery,
    CommentReplyCreate,
    CommentThreadCreate,
    CoSignOpen,
    CoSignSignRequest,
    PageQuery,
    RequirementAssignmentSet,
    RequirementCoSignPolicySet,
    TaskMemberSet,
    TaskOwnerHandover,
    TaskRuleSet,
    WorkflowMutation,
)
from pydantic import TypeAdapter, ValidationError


def _body(model, **values):
    try:
        return model.model_validate(values).model_dump(mode="json", exclude_none=True)
    except ValidationError:
        raise ServiceError(
            "invalid_input", "Input does not match the task workflow schema", 400, 2
        ) from None


def _page(cursor, limit):
    return _body(PageQuery, cursor=cursor, limit=limit)


def register(task_app: typer.Typer, card_app: typer.Typer):
    from bid_cli import main as cli

    member_app = typer.Typer()
    task_app.add_typer(member_app, name="member")
    thread_app = typer.Typer()
    comment_app = typer.Typer()
    card_app.add_typer(thread_app, name="thread")
    card_app.add_typer(comment_app, name="comment")
    rule_app = typer.Typer()
    policy_app = typer.Typer()
    signoff_app = typer.Typer()
    round_app = typer.Typer()
    task_app.add_typer(rule_app, name="review-rule")
    card_app.add_typer(policy_app, name="policy")
    card_app.add_typer(signoff_app, name="signoff")
    card_app.add_typer(round_app, name="review-round")

    @rule_app.command("show")
    def review_rule_show(
        task: Annotated[UUID, typer.Option()], json_output: cli.JsonOption = False
    ):
        cli.emit(
            cli.call("GET", f"/tasks/{task}/review-rule"), "task review-rule show", json_output
        )

    @rule_app.command("set")
    def review_rule_set(
        task: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        dry_run: Annotated[bool, typer.Option()] = False,
        json_output: cli.JsonOption = False,
    ):
        body = cli.input_contract(input, TaskRuleSet)
        if dry_run:
            body["dry_run"] = True
        cli.emit(
            cli.call("PUT", f"/tasks/{task}/review-rule", json=body),
            "task review-rule set",
            json_output,
        )

    @policy_app.command("show")
    def policy_show(
        task: Annotated[UUID, typer.Option()],
        requirement: Annotated[UUID, typer.Option()],
        job: Annotated[UUID, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call(
                "GET",
                f"/tasks/{task}/requirements/{requirement}/review-policy",
                params={"extraction_job_id": str(job)},
            ),
            "card policy show",
            json_output,
        )

    @policy_app.command("set")
    def policy_set(
        task: Annotated[UUID, typer.Option()],
        requirement: Annotated[UUID, typer.Option()],
        job: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call(
                "PUT",
                f"/tasks/{task}/requirements/{requirement}/review-policy",
                params={"extraction_job_id": str(job)},
                json=cli.input_contract(input, RequirementCoSignPolicySet),
            ),
            "card policy set",
            json_output,
        )

    @signoff_app.command("list")
    def signoff_list(
        card: Annotated[UUID, typer.Option()],
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call("GET", f"/cards/{card}/signoffs", params=_page(cursor, limit)),
            "card signoff list",
            json_output,
        )

    @round_app.command("open")
    def review_round_open(
        card: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call(
                "POST", f"/cards/{card}/review-rounds", json=cli.input_contract(input, CoSignOpen)
            ),
            "card review-round open",
            json_output,
        )

    @signoff_app.command("add")
    def signoff_add(
        card: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        if input.stat().st_size > 128 * 1024:
            raise ServiceError("input_too_large", "JSON input exceeds the command limit", 400, 2)
        adapter = TypeAdapter(CoSignSignRequest)
        body = adapter.dump_python(
            adapter.validate_python(json.loads(input.read_text(encoding="utf-8"))), mode="json"
        )
        cli.emit(
            cli.call("POST", f"/cards/{card}/signoffs", json=body),
            "card signoff add",
            json_output,
        )

    @card_app.command("assign")
    def assign(
        task: Annotated[UUID, typer.Option()],
        requirement: Annotated[UUID, typer.Option()],
        job: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call(
                "PUT",
                f"/tasks/{task}/requirements/{requirement}/assignment",
                params={"extraction_job_id": str(job)},
                json=cli.input_contract(input, RequirementAssignmentSet),
            ),
            "card assign",
            json_output,
        )

    @thread_app.command("list")
    def threads(
        card: Annotated[UUID, typer.Option()],
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call("GET", f"/cards/{card}/threads", params=_page(cursor, limit)),
            "card thread list",
            json_output,
        )

    @thread_app.command("create")
    def thread_create(
        card: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call(
                "POST",
                f"/cards/{card}/threads",
                json=cli.input_contract(input, CommentThreadCreate),
            ),
            "card thread create",
            json_output,
        )

    @comment_app.command("list")
    def comments(
        card: Annotated[UUID, typer.Option()],
        thread: Annotated[UUID, typer.Option()],
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call(
                "GET", f"/cards/{card}/threads/{thread}/comments", params=_page(cursor, limit)
            ),
            "card comment list",
            json_output,
        )

    @comment_app.command("add")
    def comment_add(
        card: Annotated[UUID, typer.Option()],
        thread: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call(
                "POST",
                f"/cards/{card}/threads/{thread}/comments",
                json=cli.input_contract(input, CommentReplyCreate),
            ),
            "card comment add",
            json_output,
        )

    @task_app.command("workflow")
    def workflow(task: Annotated[UUID, typer.Option()], json_output: cli.JsonOption = False):
        cli.emit(cli.call("GET", f"/tasks/{task}/workflow"), "task workflow", json_output)

    @task_app.command("progress")
    def progress(
        task: Annotated[UUID, typer.Option()],
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=20)] = 20,
        job: Annotated[UUID | None, typer.Option()] = None,
        view: Annotated[str | None, typer.Option()] = None,
        json_output: cli.JsonOption = False,
    ):
        if view not in {None, "requirement-review"} or (view is None) != (job is None):
            raise ServiceError(
                "invalid_input",
                "Requirement review progress requires --view requirement-review and --job",
                400,
                2,
            )
        params = _page(cursor, limit)
        if view is not None:
            params.update(view=view, extraction_job_id=str(job))
        cli.emit(
            cli.call("GET", f"/tasks/{task}/progress", params=params),
            "task progress",
            json_output,
        )

    @member_app.command("list")
    def members(
        task: Annotated[UUID, typer.Option()],
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call("GET", f"/tasks/{task}/members", params=_page(cursor, limit)),
            "task member list",
            json_output,
        )

    @member_app.command("candidates")
    def candidates(
        task: Annotated[UUID, typer.Option()],
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call("GET", f"/tasks/{task}/member-candidates", params=_page(cursor, limit)),
            "task member candidates",
            json_output,
        )

    @member_app.command("set")
    def member_set(
        task: Annotated[UUID, typer.Option()],
        user: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call(
                "PUT",
                f"/tasks/{task}/members/{user}",
                json=cli.input_contract(input, TaskMemberSet),
            ),
            "task member set",
            json_output,
        )

    @member_app.command("remove")
    def member_remove(
        task: Annotated[UUID, typer.Option()],
        user: Annotated[UUID, typer.Option()],
        expected_revision: Annotated[int, typer.Option(min=1)],
        reason: Annotated[str, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call(
                "POST",
                f"/tasks/{task}/members/{user}/remove",
                json=_body(WorkflowMutation, expected_revision=expected_revision, reason=reason),
            ),
            "task member remove",
            json_output,
        )

    @task_app.command("handover")
    def handover(
        task: Annotated[UUID, typer.Option()],
        to_user: Annotated[UUID, typer.Option()],
        previous_owner_role: Annotated[str, typer.Option()],
        expected_revision: Annotated[int, typer.Option(min=1)],
        reason: Annotated[str, typer.Option()],
        previous_domain: Annotated[list[str] | None, typer.Option()] = None,
        json_output: cli.JsonOption = False,
    ):
        body = _body(
            TaskOwnerHandover,
            expected_revision=expected_revision,
            reason=reason,
            target_user_id=to_user,
            previous_owner_role=previous_owner_role,
            previous_owner_review_domains=previous_domain or [],
        )
        cli.emit(
            cli.call("POST", f"/tasks/{task}/handover", json=body), "task handover", json_output
        )

    @task_app.command("archive")
    def archive(
        task: Annotated[UUID, typer.Option()],
        expected_revision: Annotated[int, typer.Option(min=1)],
        reason: Annotated[str, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call(
                "POST",
                f"/tasks/{task}/archive",
                json=_body(WorkflowMutation, expected_revision=expected_revision, reason=reason),
            ),
            "task archive",
            json_output,
        )

    @task_app.command("unarchive")
    def unarchive(
        task: Annotated[UUID, typer.Option()],
        expected_revision: Annotated[int, typer.Option(min=1)],
        reason: Annotated[str, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call(
                "POST",
                f"/tasks/{task}/unarchive",
                json=_body(WorkflowMutation, expected_revision=expected_revision, reason=reason),
            ),
            "task unarchive",
            json_output,
        )

    @task_app.command("board")
    def board(
        task: Annotated[UUID, typer.Option()],
        job: Annotated[UUID, typer.Option()],
        bucket: Annotated[str | None, typer.Option()] = None,
        category: Annotated[str | None, typer.Option()] = None,
        starred: Annotated[bool, typer.Option()] = False,
        owner: Annotated[UUID | None, typer.Option()] = None,
        unassigned: Annotated[bool, typer.Option()] = False,
        domain: Annotated[str | None, typer.Option()] = None,
        blocker: Annotated[str | None, typer.Option()] = None,
        mine: Annotated[bool, typer.Option()] = False,
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        view: Annotated[str | None, typer.Option()] = None,
        json_output: cli.JsonOption = False,
    ):
        if view not in {None, "requirement-review"}:
            raise ServiceError("invalid_input", "Unsupported task board view", 400, 2)
        params = _body(
            RequirementBoardQuery if view is not None else BoardQuery,
            extraction_job_id=job,
            bucket=bucket,
            category=category,
            starred=True if starred else None,
            owner_user_id=owner,
            unassigned=unassigned,
            review_domain=domain,
            blocker=blocker,
            mine=mine,
            cursor=cursor,
            limit=limit,
        )
        params = {
            key: str(value).lower() if isinstance(value, bool) else value
            for key, value in params.items()
        }
        if view is not None:
            params["view"] = view
        cli.emit(cli.call("GET", f"/tasks/{task}/board", params=params), "task board", json_output)

    @task_app.command("activity")
    def activity(
        task: Annotated[UUID, typer.Option()],
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call("GET", f"/tasks/{task}/activity", params=_page(cursor, limit)),
            "task activity",
            json_output,
        )

    @task_app.command("events")
    def events(
        task: Annotated[UUID, typer.Option()],
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 100,
        wait_seconds: Annotated[int, typer.Option(min=0, max=25)] = 0,
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call(
                "GET",
                f"/tasks/{task}/events/poll",
                params={**_page(cursor, limit), "wait_seconds": wait_seconds},
            ),
            "task events",
            json_output,
        )
