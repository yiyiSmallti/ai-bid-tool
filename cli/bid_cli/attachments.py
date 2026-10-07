"""Human attachment archive commands over the authenticated Result 4.0 API."""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
import typer
from app.core.errors import ServiceError
from app.schemas import attachment_contracts as models
from app.schemas.contracts import Result
from pydantic import TypeAdapter

from bid_cli.client import new_output_path, save_download

COMMAND_INPUTS = {
    "resource attachment upload": models.AttachmentCreate,
    "resource attachment revise": models.AttachmentRevise,
    "resource attachment list": models.AttachmentListQuery,
    "resource attachment browse": models.AttachmentBrowseQuery,
    "resource attachment show": None,
    "resource attachment history": models.PageQuery,
    "resource attachment revision show": None,
    "resource attachment assign": models.AttachmentAssignment,
    "resource attachment review": models.AttachmentReviewInput,
    "resource attachment reviews": models.PageQuery,
    "resource attachment deactivate": models.AttachmentDeactivate,
    "resource profile attachment link": models.ProfileAttachmentLinkInput,
    "resource profile attachment list": models.PageQuery,
    "resource profile attachment deactivate": models.AttachmentDeactivate,
    "task attachment select": models.TaskAttachmentSelect,
    "task attachment list": models.PageQuery,
    "task attachment deactivate": models.AttachmentDeactivate,
    "evidence attachment source create": models.AttachmentSourceCreate,
    "evidence attachment source list": models.AttachmentSourceListQuery,
    "evidence attachment source show": None,
    "evidence attachment privacy review": models.AttachmentPrivacyInput,
    "resource attachment file download": None,
    "resource attachment part download": None,
    "resource attachment page preview": None,
    "evidence attachment source preview": None,
}
COMMAND_DATA = {
    "resource attachment upload": models.AttachmentWriteReceipt | models.AttachmentUploadPreview,
    "resource attachment revise": models.AttachmentWriteReceipt | models.AttachmentUploadPreview,
    **{
        name: models.AttachmentShowData
        for name in (
            "resource attachment show",
            "resource attachment assign",
            "resource attachment deactivate",
        )
    },
    "resource attachment revision show": models.AttachmentRevisionView,
    "resource attachment review": models.AttachmentReviewView,
    **{
        name: models.ProfileAttachmentLinkView
        for name in ("resource profile attachment link", "resource profile attachment deactivate")
    },
    **{
        name: models.TaskAttachmentView
        for name in ("task attachment select", "task attachment deactivate")
    },
    "evidence attachment source create": models.AttachmentSourceReceipt,
    "evidence attachment source show": models.AttachmentSourceSummary,
    "evidence attachment privacy review": models.AttachmentPrivacyReceipt
    | models.AttachmentPrivacyHoldView,
    **{
        name: models.AttachmentDownloadReceipt
        for name in ("resource attachment file download", "resource attachment part download")
    },
    **{
        name: models.AttachmentDownloadLink
        for name in ("resource attachment page preview", "evidence attachment source preview")
    },
}
COMMAND_ITEMS = {
    "resource attachment list": models.AttachmentSummary,
    "resource attachment browse": models.AttachmentSummary,
    "resource attachment history": models.AttachmentRevisionSummary,
    "resource attachment reviews": models.AttachmentReviewView,
    "resource profile attachment list": models.ProfileAttachmentLinkView,
    "task attachment list": models.TaskAttachmentView,
    "evidence attachment source list": models.AttachmentSourceSummary,
}


def validated(body: dict, command: str) -> dict:
    try:
        value = Result.model_validate(body)
        if (
            set(body) != {"ok", "command", "data", "items", "warnings", "cost", "duration_ms"}
            or not value.ok
            or value.command != command
            or value.cost.basis != "zero"
            or value.cost.llm_tokens != 0
            or value.cost.ocr_pages != 0
            or value.cost.usd != 0
            or value.cost.charge != 0
            or value.cost.task_amount != 0
            or value.cost.unpriced_calls != 0
            or value.cost.unresolved_calls != 0
        ):
            raise ValueError("invalid attachment result")
        if command in COMMAND_ITEMS:
            page = models.PageData.model_validate(value.data)
            if len(value.items) > page.limit:
                raise ValueError("oversize attachment page")
            for item in value.items:
                TypeAdapter(COMMAND_ITEMS[command]).validate_python(item)
        else:
            TypeAdapter(COMMAND_DATA[command]).validate_python(value.data)
            if value.items:
                raise ValueError("unexpected items")
    except (ValueError, TypeError, KeyError) as exc:
        raise ServiceError(
            "invalid_server_response", "Server returned invalid attachment metadata", 502, 4
        ) from exc
    return body


async def download(client, revision: UUID, output: Path, part: int | None) -> dict:
    output = await asyncio.to_thread(new_output_path, output)
    body = await client.request("GET", f"/resources/attachments/revisions/{revision}")
    detail = models.AttachmentRevisionView.model_validate(body["data"])
    saved = client.state.load()
    if detail.id != revision or str(detail.org_id) != saved["org_id"]:
        raise ServiceError("invalid_server_response", "File metadata scope mismatch", 502, 4)
    path = f"/resources/attachments/revisions/{revision}/" + (
        "file/download" if part is None else f"parts/{part}/download"
    )
    link = await client.request("GET", path + "-link")
    try:
        url = urlsplit(models.AttachmentDownloadLink.model_validate(link["data"]).url)
        query = parse_qs(url.query, strict_parsing=True)
        if (
            url.scheme
            or url.netloc
            or url.fragment
            or url.path != path
            or set(query) != {"signature"}
            or len(query["signature"]) != 1
            or not 0 < len(query["signature"][0]) < 8192
        ):
            raise ValueError("unsafe link")
    except (ValueError, KeyError, TypeError) as exc:
        raise ServiceError(
            "invalid_download_link", "Server returned an unsafe download link", 502, 4
        ) from exc
    descriptor = (
        detail.original
        if part is None
        else next((item for item in detail.parts if item.ordinal == part), None)
    )
    if descriptor is None:
        raise ServiceError("not_found", "Resource not found", 404, 4)
    content = bytearray()
    try:
        async with client.transport() as http:
            async with http.stream(
                "GET",
                "/v4" + path,
                params=query,
                headers={
                    "Authorization": f"Bearer {saved['session']}",
                    "X-Org-Id": saved["org_id"],
                },
                follow_redirects=False,
            ) as response:
                if not response.is_success:
                    raise ServiceError(
                        "download_unavailable",
                        "Download request failed",
                        response.status_code,
                        3 if response.status_code >= 502 else 4,
                    )
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > descriptor.size_bytes:
                        raise ServiceError(
                            "attachment_integrity",
                            "Downloaded file failed integrity checks",
                            502,
                            4,
                        )
                    content.extend(chunk)
    except httpx.TransportError as exc:
        raise ServiceError("network_unavailable", "Download unavailable", 503, 3) from exc
    if (
        len(content) != descriptor.size_bytes
        or hashlib.sha256(content).hexdigest() != descriptor.sha256
    ):
        raise ServiceError(
            "attachment_integrity", "Downloaded file failed integrity checks", 502, 4
        )
    await asyncio.to_thread(save_download, output, content)
    command = "resource attachment " + ("file" if part is None else "part") + " download"
    receipt = models.AttachmentDownloadReceipt(
        attachment_revision_id=revision,
        file_id=detail.file_id,
        part_ordinal=part,
        output_path=str(output),
        file=descriptor,
    )
    return Result(
        ok=True,
        command=command,
        data=receipt.model_dump(mode="json"),
        cost=Result.model_validate(link).cost,
    ).model_dump(mode="json")


def register(resource_app, profile_app, task_app, evidence_app):
    from bid_cli import main as cli

    attachment, revision, file_app, part_app, page_app = (typer.Typer() for _ in range(5))
    profile, task, evidence, source, privacy = (typer.Typer() for _ in range(5))
    resource_app.add_typer(attachment, name="attachment")
    for name, group in (
        ("revision", revision),
        ("file", file_app),
        ("part", part_app),
        ("page", page_app),
    ):
        attachment.add_typer(group, name=name)
    profile_app.add_typer(profile, name="attachment")
    task_app.add_typer(task, name="attachment")
    evidence_app.add_typer(evidence, name="attachment")
    evidence.add_typer(source, name="source")
    evidence.add_typer(privacy, name="privacy")

    def send(method, path, command, json_output, **kwargs):
        cli.emit(validated(cli.call(method, path, **kwargs), command), command, json_output)

    def upload(path, command, input, files, dry_run, json_output):
        body = cli.input_contract(input, COMMAND_INPUTS[command])
        body["dry_run"] = dry_run or body.get("dry_run", False)
        uploads = []
        total = 0
        for item in files:
            with item.open("rb") as handle:
                content = handle.read(models.FILE_BYTE_LIMIT + 1)
            total += len(content)
            if total > models.FILE_BYTE_LIMIT:
                raise ServiceError("file_too_large", "Uploads exceed 40 MiB", 413, 2)
            uploads.append(
                (
                    "files",
                    (
                        item.name,
                        content,
                        "application/pdf"
                        if item.suffix.lower() == ".pdf"
                        else "application/octet-stream",
                    ),
                )
            )
        send("POST", path, command, json_output, data={"metadata": json.dumps(body)}, files=uploads)

    @attachment.command("upload")
    def create(
        input: Annotated[Path, typer.Option()],
        file: Annotated[list[Path], typer.Option()],
        dry_run: Annotated[bool, typer.Option()] = False,
        json_output: cli.JsonOption = False,
    ):
        upload(
            "/resources/attachments",
            "resource attachment upload",
            input,
            file,
            dry_run,
            json_output,
        )

    @attachment.command("revise")
    def revise(
        id: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        file: Annotated[list[Path], typer.Option()],
        dry_run: Annotated[bool, typer.Option()] = False,
        json_output: cli.JsonOption = False,
    ):
        upload(
            f"/resources/attachments/{id}/revisions",
            "resource attachment revise",
            input,
            file,
            dry_run,
            json_output,
        )

    def mutation(group, action, command, path):
        def invoke(
            id: Annotated[UUID, typer.Option()],
            input: Annotated[Path, typer.Option()],
            json_output: cli.JsonOption = False,
        ):
            model = COMMAND_INPUTS[command]
            if command == "evidence attachment privacy review":
                with input.open("rb") as handle:
                    content = handle.read(models.HTTP_JSON_LIMIT + 1)
                if len(content) > models.HTTP_JSON_LIMIT:
                    raise ServiceError("invalid_input", "Metadata exceeds 128 KiB", 413, 2)
                body = json.loads(content)
            else:
                body = cli.input_contract(input, model)
            if command == "evidence attachment privacy review":
                body = TypeAdapter(model).validate_python(body).model_dump(mode="json")
            send("POST", path.format(id=id), command, json_output, json=body)

        group.command(action)(invoke)

    for group, action, command, path in (
        (
            attachment,
            "assign",
            "resource attachment assign",
            "/resources/attachments/{id}/assignment",
        ),
        (
            attachment,
            "review",
            "resource attachment review",
            "/resources/attachments/revisions/{id}/reviews",
        ),
        (
            attachment,
            "deactivate",
            "resource attachment deactivate",
            "/resources/attachments/{id}/deactivate",
        ),
        (
            profile,
            "deactivate",
            "resource profile attachment deactivate",
            "/profile-attachment-links/{id}/deactivate",
        ),
        (task, "deactivate", "task attachment deactivate", "/task-attachments/{id}/deactivate"),
        (
            privacy,
            "review",
            "evidence attachment privacy review",
            "/attachment-sources/{id}/privacy",
        ),
    ):
        mutation(group, action, command, path)

    def show(group, command, path, action="show"):
        def invoke(id: Annotated[UUID, typer.Option()], json_output: cli.JsonOption = False):
            send("GET", path.format(id=id), command, json_output)

        group.command(action)(invoke)

    show(attachment, "resource attachment show", "/resources/attachments/{id}")
    show(revision, "resource attachment revision show", "/resources/attachments/revisions/{id}")
    show(source, "evidence attachment source show", "/attachment-sources/{id}")

    @source.command("preview")
    def source_preview(
        id: Annotated[UUID, typer.Option()],
        history: Annotated[bool, typer.Option()] = False,
        json_output: cli.JsonOption = False,
    ):
        send(
            "GET",
            f"/attachment-sources/{id}/preview/download-link",
            "evidence attachment source preview",
            json_output,
            params={"history": history},
        )

    @attachment.command("browse")
    def browse(input: Annotated[Path, typer.Option()], json_output: cli.JsonOption = False):
        send(
            "POST",
            "/management/resources/attachments/query",
            "resource attachment browse",
            json_output,
            json=cli.input_contract(input, models.AttachmentBrowseQuery),
        )

    @attachment.command("list")
    def listing(
        kind: Annotated[str | None, typer.Option()] = None,
        history: Annotated[bool, typer.Option()] = False,
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        json_output: cli.JsonOption = False,
    ):
        query = models.AttachmentListQuery.model_validate(
            {"kind": kind, "history": history, "cursor": cursor, "limit": limit}
        )
        send(
            "GET",
            "/resources/attachments",
            "resource attachment list",
            json_output,
            params=query.model_dump(mode="json", exclude_none=True),
        )

    def history_command(action, command, path):
        def invoke(
            id: Annotated[UUID, typer.Option()],
            cursor: Annotated[str | None, typer.Option()] = None,
            limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
            json_output: cli.JsonOption = False,
        ):
            send(
                "GET",
                path.format(id=id),
                command,
                json_output,
                params=models.PageQuery(cursor=cursor, limit=limit).model_dump(
                    mode="json", exclude_none=True
                ),
            )

        attachment.command(action)(invoke)

    history_command(
        "history", "resource attachment history", "/resources/attachments/{id}/revisions"
    )
    history_command(
        "reviews", "resource attachment reviews", "/resources/attachments/revisions/{id}/reviews"
    )

    @profile.command("link")
    def link(
        profile_revision: Annotated[UUID, typer.Option()],
        input: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        send(
            "POST",
            f"/resources/profiles/revisions/{profile_revision}/attachments",
            "resource profile attachment link",
            json_output,
            json=cli.input_contract(input, models.ProfileAttachmentLinkInput),
        )

    @profile.command("list")
    def links(
        profile_revision: Annotated[UUID, typer.Option()],
        history: Annotated[bool, typer.Option()] = False,
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        json_output: cli.JsonOption = False,
    ):
        send(
            "GET",
            f"/resources/profiles/revisions/{profile_revision}/attachments",
            "resource profile attachment list",
            json_output,
            params=models.PageQuery(history=history, cursor=cursor, limit=limit).model_dump(
                mode="json", exclude_none=True
            ),
        )

    def task_mutation(group, action, command, tail):
        def invoke(
            task: Annotated[UUID, typer.Option()],
            input: Annotated[Path, typer.Option()],
            json_output: cli.JsonOption = False,
        ):
            send(
                "POST",
                f"/tasks/{task}/{tail}",
                command,
                json_output,
                json=cli.input_contract(input, COMMAND_INPUTS[command]),
            )

        group.command(action)(invoke)

    task_mutation(task, "select", "task attachment select", "attachments")
    task_mutation(source, "create", "evidence attachment source create", "attachment-sources")

    @task.command("list")
    def selections(
        task: Annotated[UUID, typer.Option()],
        history: Annotated[bool, typer.Option()] = False,
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        json_output: cli.JsonOption = False,
    ):
        send(
            "GET",
            f"/tasks/{task}/attachments",
            "task attachment list",
            json_output,
            params=models.PageQuery(history=history, cursor=cursor, limit=limit).model_dump(
                mode="json", exclude_none=True
            ),
        )

    @source.command("list")
    def sources(
        task: Annotated[UUID, typer.Option()],
        selection: Annotated[UUID | None, typer.Option()] = None,
        history: Annotated[bool, typer.Option()] = False,
        cursor: Annotated[str | None, typer.Option()] = None,
        limit: Annotated[int, typer.Option(min=1, max=100)] = 50,
        json_output: cli.JsonOption = False,
    ):
        send(
            "GET",
            f"/tasks/{task}/attachment-sources",
            "evidence attachment source list",
            json_output,
            params=models.AttachmentSourceListQuery(
                selection_id=selection, history=history, cursor=cursor, limit=limit
            ).model_dump(mode="json", exclude_none=True),
        )

    @file_app.command("download")
    def file_download(
        revision: Annotated[UUID, typer.Option()],
        output: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            asyncio.run(download(cli.client(), revision, output, None)),
            "resource attachment file download",
            json_output,
        )

    @part_app.command("download")
    def part_download(
        revision: Annotated[UUID, typer.Option()],
        part: Annotated[int, typer.Option(min=1, max=20)],
        output: Annotated[Path, typer.Option()],
        json_output: cli.JsonOption = False,
    ):
        cli.emit(
            asyncio.run(download(cli.client(), revision, output, part)),
            "resource attachment part download",
            json_output,
        )

    @page_app.command("preview")
    def preview(
        revision: Annotated[UUID, typer.Option()],
        page: Annotated[int, typer.Option(min=1, max=200)],
        zoom: Annotated[int, typer.Option(min=1, max=2)] = 1,
        json_output: cli.JsonOption = False,
    ):
        send(
            "GET",
            f"/resources/attachments/revisions/{revision}/pages/{page}/preview-link",
            "resource attachment page preview",
            json_output,
            params={"zoom": zoom},
        )
