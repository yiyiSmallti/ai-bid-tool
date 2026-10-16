"""Project package export, import, status and download over a `bid_` token."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from app.schemas.packages import PackageExport

COMMAND_INPUTS = {
    "package export": PackageExport,
    "package import": None,
    "package status": None,
    "package download": None,
}


def register(app: typer.Typer):
    import asyncio

    from bid_cli import main as cli

    package_app = typer.Typer()
    app.add_typer(package_app, name="package")

    @package_app.command("export")
    def package_export(task: Annotated[UUID, typer.Option()], json_output: cli.JsonOption = False):
        body = PackageExport(task_id=task).model_dump(mode="json")
        cli.emit(cli.call("POST", "/packages/export", json=body), "package export", json_output)

    @package_app.command("import")
    def package_import(file: Annotated[Path, typer.Option()], json_output: cli.JsonOption = False):
        from app.core.errors import ServiceError

        try:
            content = file.read_bytes()
        except OSError as exc:
            raise ServiceError("missing_file", "Cannot read the package file", 400, 2) from exc
        files = {"file": (file.name, content, "application/zip")}
        cli.emit(cli.call("POST", "/packages/import", files=files), "package import", json_output)

    @package_app.command("status")
    def package_status(job: Annotated[UUID, typer.Option()], json_output: cli.JsonOption = False):
        cli.emit(cli.call("GET", f"/packages/{job}"), "package status", json_output)

    @package_app.command("download")
    def package_download(
        job: Annotated[UUID, typer.Option()],
        output: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        result = asyncio.run(cli.client().download_package(job, output))
        cli.emit(result, "package download", json_output)
