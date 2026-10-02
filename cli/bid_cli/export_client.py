"""Safe client-side transfer for released DOCX exports."""

import asyncio
import hashlib
import os
import secrets
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, NoReturn
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
from app.core.errors import ServiceError
from app.schemas.contracts import Result
from app.schemas.export_contracts import (
    DOCX_MEDIA_TYPE,
    MAX_EXPORT_BYTES,
    ExportDownloadLink,
    ExportDownloadResult,
    ExportView,
)
from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from lxml.etree import XMLSyntaxError
from pydantic import ValidationError

from bid_cli.client import Client, new_output_path

MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
MAX_PACKAGE_MEMBERS = 10_000
MAX_ERROR_BYTES = 64 * 1024


def _download_error(code: str, message: str, *, retryable: bool = False) -> ServiceError:
    return ServiceError(code, message, 502, 3 if retryable else 4)


def _validate_docx(source: BinaryIO) -> None:
    try:
        source.seek(0)
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if (
                not entries
                or len(entries) > MAX_PACKAGE_MEMBERS
                or len(names) != len(set(names))
                or sum(entry.file_size for entry in entries) > MAX_UNPACKED_BYTES
                or "[Content_Types].xml" not in names
                or "word/document.xml" not in names
            ):
                raise ValueError("invalid package shape")
            for entry in entries:
                member = PurePosixPath(entry.filename)
                if (
                    entry.flag_bits & 1
                    or member.is_absolute()
                    or ".." in member.parts
                    or "\\" in entry.filename
                    or "vbaproject" in entry.filename.casefold()
                    or "vbadata" in entry.filename.casefold()
                ):
                    raise ValueError("unsafe package member")
            content_types = archive.read("[Content_Types].xml").lower()
            if b"macroenabled" in content_types or b"vba" in content_types:
                raise ValueError("macro-enabled package")
        source.seek(0)
        Document(source)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        zipfile.BadZipFile,
        PackageNotFoundError,
        XMLSyntaxError,
    ) as exc:
        raise _download_error(
            "export_file_integrity", "Downloaded export is not a safe readable DOCX"
        ) from exc


def _private_temporary(parent_fd: int) -> tuple[int, str]:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    for _ in range(10):
        name = f".bid-download-{secrets.token_hex(16)}"
        try:
            return os.open(name, flags, 0o600, dir_fd=parent_fd), name
        except FileExistsError:
            continue
    raise OSError("cannot allocate a private download file")


def _same_directory(path: Path, descriptor: int) -> bool:
    opened = os.fstat(descriptor)
    visible = os.stat(path, follow_symlinks=False)
    return stat.S_ISDIR(visible.st_mode) and (opened.st_dev, opened.st_ino) == (
        visible.st_dev,
        visible.st_ino,
    )


def _parse_signed_route(link: ExportDownloadLink) -> tuple[str, dict[str, str]]:
    parsed = urlsplit(link.url)
    try:
        query = parse_qs(parsed.query, strict_parsing=True)
    except ValueError as exc:
        raise _download_error(
            "invalid_download_link", "Server returned an unsafe export download link"
        ) from exc
    expected = f"/exports/{link.export_id}/download"
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or parsed.path != expected
        or set(query) != {"signature"}
        or len(query["signature"]) != 1
        or not 0 < len(query["signature"][0]) < 8192
    ):
        raise _download_error(
            "invalid_download_link", "Server returned an unsafe export download link"
        )
    return expected, {"signature": query["signature"][0]}


async def _raise_download_error(response: httpx.Response) -> NoReturn:
    content = bytearray()
    async for chunk in response.aiter_bytes():
        content.extend(chunk)
        if len(content) > MAX_ERROR_BYTES:
            raise _download_error(
                "invalid_server_response", "Server returned an oversized download error"
            )
    try:
        body = Result.model_validate_json(content)
        error = body.data["error"]
        code = error["code"]
        message = error["message"]
        exit_code = error["exit_code"]
        if (
            body.ok
            or not isinstance(code, str)
            or not code
            or not isinstance(message, str)
            or not message
            or exit_code not in {2, 3, 4, 5}
        ):
            raise ValueError("invalid error contract")
    except (KeyError, TypeError, ValueError, ValidationError):
        raise _download_error(
            "invalid_server_response", "Server returned an invalid download error"
        ) from None
    raise ServiceError(code, message, response.status_code, exit_code)


async def download_export(client: Client, export_id: UUID, output: Path) -> dict:
    output = await asyncio.to_thread(new_output_path, output)
    if output.suffix.lower() != ".docx":
        raise ServiceError(
            "invalid_output_path", "Export download output must use a .docx name", 400, 2
        )

    saved = client.state.load()
    view_body = await client.request("GET", f"/exports/{export_id}")
    link_body = await client.request("GET", f"/exports/{export_id}/download-link")
    try:
        view = ExportView.model_validate(view_body["data"])
        link = ExportDownloadLink.model_validate(link_body["data"])
        if (
            view.id != export_id
            or link.export_id != export_id
            or str(view.org_id) != saved["org_id"]
            or link.file != view.file
            or view.validity != "current"
        ):
            raise ValueError("download metadata mismatch")
    except (KeyError, TypeError, ValueError, ValidationError):
        raise _download_error(
            "invalid_server_response", "Server returned invalid export metadata"
        ) from None

    path, query = _parse_signed_route(link)
    headers = {"Authorization": f"Bearer {saved['session']}", "X-Org-Id": saved["org_id"]}
    parent_fd: int | None = None
    temporary_name: str | None = None
    linked = False
    try:
        directory_flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            directory_flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        parent_fd = os.open(output.parent, directory_flags)
        descriptor, temporary_name = _private_temporary(parent_fd)
        digest = hashlib.sha256()
        received = 0
        with os.fdopen(descriptor, "w+b") as handle:
            try:
                async with client.transport() as http:
                    async with http.stream(
                        "GET", path, params=query, headers=headers, follow_redirects=False
                    ) as response:
                        if not response.is_success:
                            await _raise_download_error(response)
                        media_type = response.headers.get("content-type", "").split(";", 1)[0]
                        if media_type.strip().lower() != DOCX_MEDIA_TYPE:
                            raise _download_error(
                                "export_file_integrity",
                                "Downloaded export has the wrong media type",
                            )
                        declared_length = response.headers.get("content-length")
                        if declared_length is not None:
                            try:
                                length = int(declared_length)
                            except ValueError:
                                raise _download_error(
                                    "export_file_integrity",
                                    "Downloaded export has an invalid length header",
                                ) from None
                            if length != link.file.size_bytes:
                                raise _download_error(
                                    "export_file_integrity",
                                    "Downloaded export length does not match its descriptor",
                                )
                        async for chunk in response.aiter_bytes():
                            received += len(chunk)
                            if received > link.file.size_bytes or received > MAX_EXPORT_BYTES:
                                raise _download_error(
                                    "export_file_integrity",
                                    "Downloaded export exceeded its declared limit",
                                )
                            digest.update(chunk)
                            handle.write(chunk)
            except httpx.TransportError as exc:
                raise _download_error(
                    "network_unavailable",
                    "Server is unavailable or the export transfer was interrupted",
                    retryable=True,
                ) from exc
            handle.flush()
            os.fsync(handle.fileno())
            if received != link.file.size_bytes or digest.hexdigest() != link.file.sha256:
                raise _download_error(
                    "export_file_integrity", "Downloaded export failed length or SHA-256 checks"
                )
            await asyncio.to_thread(_validate_docx, handle)
        if not _same_directory(output.parent, parent_fd):
            raise OSError("download directory changed")
        os.link(
            temporary_name,
            output.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        linked = True
        visible = os.stat(output, follow_symlinks=False)
        stored = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_directory(output.parent, parent_fd) or (
            visible.st_dev,
            visible.st_ino,
        ) != (stored.st_dev, stored.st_ino):
            os.unlink(output.name, dir_fd=parent_fd)
            linked = False
            raise OSError("download destination changed")
    except OSError as exc:
        if linked and parent_fd is not None and temporary_name is not None:
            try:
                temporary_stat = os.stat(temporary_name, dir_fd=parent_fd, follow_symlinks=False)
                output_stat = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
                if (temporary_stat.st_dev, temporary_stat.st_ino) == (
                    output_stat.st_dev,
                    output_stat.st_ino,
                ):
                    os.unlink(output.name, dir_fd=parent_fd)
                    linked = False
            except FileNotFoundError:
                pass
        raise ServiceError(
            "invalid_output_path",
            "Cannot save export without overwriting an existing path",
            400,
            2,
        ) from exc
    finally:
        if parent_fd is not None:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            os.close(parent_fd)

    receipt = ExportDownloadResult(
        export_id=export_id,
        output_path=str(output),
        file=link.file,
        mode=view.mode,
        completion=view.completion,
        validity=view.validity,
        issues=view.issues,
    ).model_dump(mode="json")
    return Result(
        ok=view.completion == "complete",
        command="export download",
        data=receipt,
        warnings=[issue.code for issue in view.issues],
    ).model_dump(mode="json")
