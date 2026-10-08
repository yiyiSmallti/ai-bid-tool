"""Disposable PDF processes with bounded, non-executable result data."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType

from app.core.config import PDFSettings
from app.core.errors import ServiceError

SERVER_ROOT = Path(__file__).resolve().parents[2]
# Bound a single parent allocation as well as the total on-disk result spool.
MAX_RECORD_BYTES = 64 * 1024 * 1024


def resource_error() -> ServiceError:
    return ServiceError(
        "pdf_resource_limits",
        "PDF processing exceeded resource limits or the isolated PDF process failed; "
        "simplify or split the document before uploading it again",
        400,
        4,
    )


def child_command(root: Path) -> list[str]:
    """An internal launch seam; tests replace the command, never PDF-controlled flags."""
    return [sys.executable, "-m", "app.core.pdf_child", str(root)]


def private_write(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)


class PDFOperation:
    """Keep temporary data private until a whole child operation has succeeded.

    Waiting can run in a thread, but no PyMuPDF work takes place in that thread.
    The owning context kills and reaps on cancellation, including an async wait.
    """

    def __init__(
        self,
        content: bytes,
        operation: str,
        arguments: dict,
        settings: PDFSettings | None = None,
        *,
        temporary_root: Path | None = None,
    ):
        self.content = content
        self.operation = operation
        self.arguments = arguments
        self.settings = settings or PDFSettings()
        self.temporary_root = temporary_root
        self.child: subprocess.Popen | None = None
        self.directory: tempfile.TemporaryDirectory | None = None
        self.count: int | None = None

    def __enter__(self):
        self.started = time.monotonic()
        try:
            self.directory = tempfile.TemporaryDirectory(prefix="bid-pdf-", dir=self.temporary_root)
            self.root = Path(self.directory.name)
            private_write(self.root / "input.pdf", self.content)
            private_write(
                self.root / "request.json",
                json.dumps(
                    {
                        "operation": self.operation,
                        "arguments": self.arguments,
                        "memory_bytes": self.settings.pdf_memory_bytes,
                        "cpu_seconds": self.settings.pdf_cpu_seconds,
                        "output_bytes": self.settings.pdf_output_bytes,
                        "record_bytes": MAX_RECORD_BYTES,
                    }
                ).encode(),
            )
            env = {
                key: os.environ[key]
                for key in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")
                if key in os.environ
            }
            env.update(PYTHONPATH=str(SERVER_ROOT), PYTHONDONTWRITEBYTECODE="1")
            self.child = subprocess.Popen(
                child_command(self.root),
                cwd=self.root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self.close()
            raise resource_error() from exc
        except BaseException:
            self.close()
            raise
        return self

    def close(self) -> None:
        if self.child is not None:
            if self.child.poll() is None:
                self.child.kill()
            self.child.wait()
        if self.directory is not None:
            self.directory.cleanup()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def wait(self) -> None:
        assert self.child is not None
        remaining = self.settings.pdf_timeout_seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            self.child.kill()
            self.child.wait()
            raise resource_error()
        try:
            code = self.child.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            # Kill here as well as at context exit so synchronous callers cannot
            # observe a timeout while the offending process is still running.
            self.child.kill()
            self.child.wait()
            raise resource_error() from exc
        if code != 0:
            raise resource_error()
        try:
            with (self.root / "result.json").open("rb") as receipt:
                raw = receipt.read(16 * 1024 + 1)
            if len(raw) > 16 * 1024:
                raise ValueError("oversized receipt")
            result = json.loads(raw)
            if "error" in result:
                error = result["error"]
                if (
                    not isinstance(error["code"], str)
                    or not isinstance(error["message"], str)
                    or not isinstance(error["status"], int)
                    or error["exit_code"] not in (2, 4)
                ):
                    raise ValueError("invalid error receipt")
                raise ServiceError(
                    error["code"], error["message"], error["status"], error["exit_code"]
                )
            self.count = result["count"]
            if type(self.count) is not int or self.count < 1:
                raise ValueError("invalid result count")
            maximum = (
                self.arguments["max_pages"] if self.operation in {"parse", "bid_prepare"} else 1
            )
            if self.count > maximum:
                raise ValueError("unexpected result count")
            if (self.root / "pages.jsonl").stat().st_size > self.settings.pdf_output_bytes:
                raise ValueError("oversized results")
        except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
            raise resource_error() from exc

    async def wait_async(self) -> None:
        # Only waitpid/receipt IO is offloaded. Cancellation leaves cleanup with
        # the caller's context, which kills before removing any private files.
        await asyncio.to_thread(self.wait)

    def records(self) -> Iterator[dict]:
        if self.count is None:
            raise RuntimeError("Wait for the child before consuming PDF results")
        try:
            with (self.root / "pages.jsonl").open("rb") as pages:
                for _ in range(self.count):
                    line = pages.readline(MAX_RECORD_BYTES + 1)
                    if len(line) > MAX_RECORD_BYTES or not line.endswith(b"\n"):
                        raise ValueError("invalid result record")
                    record = json.loads(line)
                    del line
                    if not isinstance(record, dict):
                        raise ValueError("invalid result record")
                    yield record
                if pages.read(1):
                    raise ValueError("unexpected result record")
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            raise resource_error() from exc


def run_pdf_operation(
    content: bytes, operation: str, arguments: dict, settings: PDFSettings | None = None
) -> list[dict]:
    """Small synchronous operations return one record; parses stream their spool."""
    with PDFOperation(content, operation, arguments, settings) as child:
        child.wait()
        return list(child.records())


async def run_pdf_operation_async(
    content: bytes, operation: str, arguments: dict, settings: PDFSettings | None = None
) -> list[dict]:
    """Own the child in the async caller so cancellation also kills and reaps it."""
    with PDFOperation(content, operation, arguments, settings) as child:
        await child.wait_async()
        return list(child.records())
