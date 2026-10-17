"""Draft and review solution chapters through the shared API."""

import json
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from app.core.errors import ServiceError
from app.schemas.solution import (
    SolutionConfirm,
    SolutionDraftRequest,
    SolutionEdit,
    SolutionFiguresRequest,
)

MAX_FIGURE_BYTES = 15 * 1024 * 1024


def register(app: typer.Typer):
    from bid_cli import main as cli

    solution = typer.Typer(no_args_is_help=True)
    app.add_typer(solution, name="solution")

    @solution.command("draft")
    def draft(
        task: Annotated[UUID, typer.Option()],
        document: Annotated[UUID, typer.Option()],
        title: Annotated[list[str] | None, typer.Option("--title")] = None,
        model: Annotated[UUID | None, typer.Option()] = None,
        reasoning: Annotated[str | None, typer.Option()] = None,
        json_output: cli.JsonOption = False,
    ):
        request = SolutionDraftRequest(
            document_id=document, titles=title, model=model, reasoning=reasoning
        )
        cli.emit(
            cli.call(
                "POST",
                f"/tasks/{task}/solutions/draft",
                json=request.model_dump(mode="json", exclude_none=True),
            ),
            "solution draft",
            json_output,
        )

    @solution.command("list")
    def list_chapters(
        task: Annotated[UUID, typer.Option()],
        document: Annotated[UUID | None, typer.Option()] = None,
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            cli.call(
                "GET",
                f"/tasks/{task}/solutions",
                params={"document": str(document)} if document is not None else {},
            ),
            "solution list",
            json_output,
        )

    @solution.command("show")
    def show(
        chapter: Annotated[UUID, typer.Argument()],
        json_output: cli.JsonOption = False,
    ):
        cli.emit(cli.call("GET", f"/solutions/{chapter}"), "solution show", json_output)

    @solution.command("edit")
    def edit(
        chapter: Annotated[UUID, typer.Argument()],
        expected_revision: Annotated[int, typer.Option(min=1)],
        sections: Annotated[Path, typer.Option(help="JSON file containing an array of sections")],
        json_output: cli.JsonOption = False,
    ):
        # Bound the read itself, including files that grow after they are opened.
        with sections.open("rb") as source:
            content = source.read(128 * 1024 + 1)
        if len(content) > 128 * 1024:
            raise ServiceError("input_too_large", "JSON input exceeds the command limit", 400, 2)
        request = SolutionEdit(expected_revision=expected_revision, sections=json.loads(content))
        cli.emit(
            cli.call(
                "PUT",
                f"/solutions/{chapter}",
                json=request.model_dump(mode="json", exclude_defaults=True),
            ),
            "solution edit",
            json_output,
        )

    @solution.command("confirm")
    def confirm(
        chapter: Annotated[UUID, typer.Argument()],
        expected_revision: Annotated[int, typer.Option(min=1)],
        json_output: cli.JsonOption = False,
    ):
        request = SolutionConfirm(expected_revision=expected_revision)
        cli.emit(
            cli.call("POST", f"/solutions/{chapter}/confirm", json=request.model_dump(mode="json")),
            "solution confirm",
            json_output,
        )

    @solution.command("figures")
    def figures(
        chapter: Annotated[UUID, typer.Argument()],
        expected_revision: Annotated[int, typer.Option(min=1)],
        model: Annotated[UUID | None, typer.Option()] = None,
        reasoning: Annotated[str | None, typer.Option()] = None,
        json_output: cli.JsonOption = False,
    ):
        """Draw the chapter's system pages as figures in a new draft revision."""
        request = SolutionFiguresRequest(
            expected_revision=expected_revision, model=model, reasoning=reasoning
        )
        cli.emit(
            cli.call(
                "POST",
                f"/solutions/{chapter}/figures",
                json=request.model_dump(mode="json", exclude_none=True),
            ),
            "solution figures",
            json_output,
        )

    @solution.command("figure-upload")
    def figure_upload(
        chapter: Annotated[UUID, typer.Argument()],
        expected_revision: Annotated[int, typer.Option(min=1)],
        subsection: Annotated[str, typer.Option(help="Subsection number, such as 2.3")],
        caption: Annotated[str, typer.Option()],
        file: Annotated[Path, typer.Option(help="PNG or JPEG screenshot")],
        json_output: cli.JsonOption = False,
    ):
        """Attach a real screenshot to one subsection."""
        with file.open("rb") as source:
            content = source.read(MAX_FIGURE_BYTES + 1)
        if len(content) > MAX_FIGURE_BYTES:
            raise ServiceError("input_too_large", "A figure is at most 15 MiB", 413, 2)
        cli.emit(
            cli.call(
                "POST",
                f"/solutions/{chapter}/figures/upload",
                data={
                    "expected_revision": str(expected_revision),
                    "subsection": subsection,
                    "caption": caption,
                },
                files={"file": (file.name, content)},
            ),
            "solution figure-upload",
            json_output,
        )
