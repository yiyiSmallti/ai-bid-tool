"""Disposable PDF child-process boundaries through parsing and the real job path."""

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pymupdf
import pytest
from app.core.config import PDFSettings
from app.core.errors import ServiceError
from app.schemas.certificate_file_contracts import CertificateScanFile
from app.services.evidence_sources import render_page_async
from app.services.page_previews import pdf_page_count_async, render_pdf_page_async
from app.services.parsing import parse_document, validate_document_async
from fakes import FakeOCR
from test_api import create_document, run_job


def native_pdf(page_count: int = 1) -> bytes:
    with pymupdf.open() as document:
        for number in range(1, page_count + 1):
            page = document.new_page()
            page.insert_text((40, 60), f"Synthetic native page {number}")
        return document.tobytes()


def fault_command(pid_file: Path, statement: str):
    script = (
        "import os, time\n"
        "from pathlib import Path\n"
        f"Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        f"{statement}\n"
    )

    def command(_root: Path) -> list[str]:
        return [sys.executable, "-c", script]

    return command


def recording_command(pid_file: Path):
    script = (
        "import os, sys\n"
        "from pathlib import Path\n"
        f"with Path({str(pid_file)!r}).open('a') as stream:\n"
        "    stream.write(str(os.getpid()) + '\\n')\n"
        "os.execv(sys.executable, "
        "[sys.executable, '-m', 'app.core.pdf_child', sys.argv[1]])\n"
    )

    def command(root: Path) -> list[str]:
        return [sys.executable, "-c", script, str(root)]

    return command


def protocol_command(pid_file: Path):
    script = (
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        f"Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "(root / 'pages.jsonl').write_text('{}\\n')\n"
        "(root / 'result.json').write_text(json.dumps({'count': 1}))\n"
    )

    def command(root: Path) -> list[str]:
        return [sys.executable, "-c", script, str(root)]

    return command


def instrumented_child_command(pid_file: Path, read_document: str):
    script = (
        "import os, sys\n"
        "from pathlib import Path\n"
        "from types import ModuleType\n"
        f"Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "reading = ModuleType('app.core.pdf_reading')\n"
        f"{read_document}\n"
        "reading.read_document = read_document\n"
        "sys.modules['app.core.pdf_reading'] = reading\n"
        "from app.core.pdf_child import main\n"
        "raise SystemExit(main(Path(sys.argv[1])))\n"
    )

    def command(root: Path) -> list[str]:
        return [sys.executable, "-c", script, str(root)]

    return command


def wait_for_recorded_pid(pid_file: Path) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if pid_file.exists() and pid_file.read_text().strip():
            return int(pid_file.read_text().splitlines()[0])
        time.sleep(0.01)
    raise AssertionError("PDF child did not record its PID")


async def recorded_pid(pid_file: Path) -> int:
    return await asyncio.to_thread(wait_for_recorded_pid, pid_file)


def assert_reaped(pid: int) -> None:
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize(
    ("failure", "statement"),
    [("timeout", "time.sleep(60)"), ("crash", "os._exit(17)")],
)
@pytest.mark.skipif(os.name != "posix", reason="PID reaping assertion requires POSIX")
async def test_failed_child_is_reaped_and_next_real_parse_succeeds(
    tmp_path, monkeypatch, failure, statement
):
    pid_file = tmp_path / f"{failure}.pid"
    settings = PDFSettings(pdf_timeout_seconds=0.5)
    with monkeypatch.context() as fault:
        fault.setattr("app.core.pdf_process.child_command", fault_command(pid_file, statement))
        with pytest.raises(ServiceError) as refused:
            await parse_document(native_pdf(), ".pdf", FakeOCR(), 20, settings)
    assert refused.value.code == "pdf_resource_limits" and refused.value.exit_code != 3
    pid = await recorded_pid(pid_file)
    assert_reaped(pid)

    pages, usages, warnings = await parse_document(native_pdf(), ".pdf", FakeOCR(), 20)
    assert [page.text.strip() for page in pages] == ["Synthetic native page 1"]
    assert usages == [] and warnings == []
    (tmp_path / f"{failure}-result.json").write_text(
        json.dumps(
            {
                "failure": failure,
                "code": refused.value.code,
                "exit_code": refused.value.exit_code,
                "pid": pid,
                "reaped": True,
                "next_parse_pages": len(pages),
            }
        )
    )


@pytest.mark.parametrize("entry", ["parse", "validate", "preview", "page_count", "evidence"])
@pytest.mark.skipif(os.name != "posix", reason="PID reaping assertion requires POSIX")
async def test_async_entry_cancellation_reaps_the_child(tmp_path, monkeypatch, entry):
    pid_file = tmp_path / f"cancelled-{entry}.pid"
    with monkeypatch.context() as fault:
        fault.setattr(
            "app.core.pdf_process.child_command", fault_command(pid_file, "time.sleep(60)")
        )
        content = native_pdf()
        if entry == "parse":
            coroutine = parse_document(content, ".pdf", FakeOCR(), 20)
        elif entry == "validate":
            coroutine = validate_document_async(content, ".pdf", 20)
        elif entry == "preview":
            coroutine = render_pdf_page_async(content, 1, 1)
        elif entry == "page_count":
            coroutine = pdf_page_count_async(content, 20)
        else:
            original = CertificateScanFile(
                name="synthetic.pdf",
                sha256=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
                page_count=1,
            )
            coroutine = render_page_async(content, original, 1, "page.png", 40 * 1024 * 1024)
        parsing = asyncio.create_task(coroutine)
        pid = await recorded_pid(pid_file)
        parsing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await parsing
    assert_reaped(pid)
    (tmp_path / f"cancellation-{entry}-result.json").write_text(
        json.dumps(
            {"entry": entry, "pid": pid, "reaped": True, "task_cancelled": parsing.cancelled()}
        )
    )


async def test_async_render_entries_return_real_pixels_and_count(tmp_path):
    content = native_pdf(4)
    original = CertificateScanFile(
        name="synthetic.pdf",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        page_count=4,
    )
    count = await pdf_page_count_async(content, 20)
    assert count == 4
    preview = await render_pdf_page_async(content, 2, 1)
    evidence, descriptor, _ = await render_page_async(
        content, original, 2, "page.png", 40 * 1024 * 1024
    )
    assert descriptor.sha256 == hashlib.sha256(evidence).hexdigest()
    with pymupdf.open(stream=content, filetype="pdf") as document:
        for png, dpi in ((preview, 110), (evidence, 150)):
            actual = pymupdf.Pixmap(png)
            expected = document[1].get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
            assert (actual.width, actual.height, actual.samples) == (
                expected.width,
                expected.height,
                expected.samples,
            )
    (tmp_path / "render-results.json").write_text(
        json.dumps({"page_count": count, "evidence": descriptor.model_dump(mode="json")})
    )


async def test_one_child_handles_every_page_of_a_parse(tmp_path, monkeypatch):
    pid_file = tmp_path / "children.txt"
    monkeypatch.setattr("app.core.pdf_process.child_command", recording_command(pid_file))
    pages, usages, warnings = await parse_document(native_pdf(4), ".pdf", FakeOCR(), 20)
    assert [page.page for page in pages] == [1, 2, 3, 4]
    assert usages == [] and warnings == []
    pids = [int(value) for value in pid_file.read_text().splitlines()]
    assert len(pids) == 1
    (tmp_path / "multi-page-result.json").write_text(
        json.dumps({"pages": len(pages), "child_pids": pids})
    )


async def test_output_limit_refusal_does_not_poison_next_parse(tmp_path):
    with pytest.raises(ServiceError) as refused:
        await parse_document(native_pdf(), ".pdf", FakeOCR(), 20, PDFSettings(pdf_output_bytes=32))
    assert refused.value.code == "pdf_resource_limits" and refused.value.exit_code != 3

    pages, usages, warnings = await parse_document(native_pdf(), ".pdf", FakeOCR(), 20)
    assert [page.text.strip() for page in pages] == ["Synthetic native page 1"]
    assert usages == [] and warnings == []
    (tmp_path / "output-limit-result.json").write_text(
        json.dumps(
            {
                "limit_bytes": 32,
                "code": refused.value.code,
                "exit_code": refused.value.exit_code,
                "next_parse_pages": len(pages),
            }
        )
    )


async def test_malformed_child_page_is_a_clear_resource_failure(tmp_path, monkeypatch):
    pid_file = tmp_path / "malformed.pid"
    with monkeypatch.context() as fault:
        fault.setattr("app.core.pdf_process.child_command", protocol_command(pid_file))
        with pytest.raises(ServiceError) as refused:
            await parse_document(native_pdf(), ".pdf", FakeOCR(), 20)
    assert refused.value.code == "pdf_resource_limits" and refused.value.exit_code == 4
    pid = await recorded_pid(pid_file)
    assert_reaped(pid)

    pages, usages, warnings = await parse_document(native_pdf(), ".pdf", FakeOCR(), 20)
    assert len(pages) == 1 and usages == [] and warnings == []
    (tmp_path / "malformed-protocol-result.json").write_text(
        json.dumps(
            {
                "record": {},
                "code": refused.value.code,
                "exit_code": refused.value.exit_code,
                "pid": pid,
                "reaped": True,
                "next_parse_pages": len(pages),
            }
        )
    )


@pytest.mark.parametrize("failure", ["spawn", "temporary_directory"])
async def test_parent_setup_failure_is_mapped_and_next_parse_succeeds(
    tmp_path, monkeypatch, failure
):
    def missing_command(_root: Path) -> list[str]:
        return [str(tmp_path / "missing-pdf-child")]

    def unavailable_directory(*_args, **_kwargs):
        raise OSError("synthetic unavailable temporary directory")

    with monkeypatch.context() as fault:
        if failure == "spawn":
            fault.setattr("app.core.pdf_process.child_command", missing_command)
        else:
            fault.setattr("app.core.pdf_process.tempfile.TemporaryDirectory", unavailable_directory)
        with pytest.raises(ServiceError) as refused:
            await parse_document(native_pdf(), ".pdf", FakeOCR(), 20)
    assert refused.value.code == "pdf_resource_limits" and refused.value.exit_code == 4

    pages, usages, warnings = await parse_document(native_pdf(), ".pdf", FakeOCR(), 20)
    assert len(pages) == 1 and usages == [] and warnings == []
    (tmp_path / f"parent-{failure}-result.json").write_text(
        json.dumps(
            {
                "failure": failure,
                "code": refused.value.code,
                "exit_code": refused.value.exit_code,
                "next_parse_pages": len(pages),
            }
        )
    )


@pytest.mark.skipif(os.name != "posix", reason="CPU rlimit requires POSIX")
async def test_real_child_cpu_limit_terminates_busy_parser(tmp_path, monkeypatch):
    pid_file = tmp_path / "cpu-limit.pid"
    busy_reader = "def read_document(*_args):\n    while True:\n        pass"
    settings = PDFSettings(pdf_cpu_seconds=1, pdf_timeout_seconds=10)
    started = time.monotonic()
    with monkeypatch.context() as fault:
        fault.setattr(
            "app.core.pdf_process.child_command",
            instrumented_child_command(pid_file, busy_reader),
        )
        with pytest.raises(ServiceError) as refused:
            await parse_document(native_pdf(), ".pdf", FakeOCR(), 20, settings)
    elapsed = time.monotonic() - started
    assert refused.value.code == "pdf_resource_limits" and refused.value.exit_code == 4
    assert elapsed < 8
    pid = await recorded_pid(pid_file)
    assert_reaped(pid)

    pages, usages, warnings = await parse_document(native_pdf(), ".pdf", FakeOCR(), 20)
    assert len(pages) == 1 and usages == [] and warnings == []
    (tmp_path / "cpu-limit-result.json").write_text(
        json.dumps(
            {
                "cpu_seconds": settings.pdf_cpu_seconds,
                "wall_seconds": elapsed,
                "code": refused.value.code,
                "pid": pid,
                "reaped": True,
                "next_parse_pages": len(pages),
            }
        )
    )


@pytest.mark.skipif(sys.platform != "linux", reason="RLIMIT_AS/DATA enforcement is Linux-only")
async def test_linux_child_observes_configured_memory_limits(tmp_path, monkeypatch):
    pid_file = tmp_path / "memory-limit.pid"
    memory_bytes = 256 * 1024 * 1024
    reporting_reader = (
        "def read_document(*_args):\n"
        "    import json, resource\n"
        "    limits = {name: list(resource.getrlimit(getattr(resource, name))) "
        "for name in ('RLIMIT_AS', 'RLIMIT_DATA')}\n"
        "    yield {'page': 1, 'text': json.dumps(limits), 'image': None, 'warnings': []}"
    )
    with monkeypatch.context() as fault:
        fault.setattr(
            "app.core.pdf_process.child_command",
            instrumented_child_command(pid_file, reporting_reader),
        )
        pages, usages, warnings = await parse_document(
            native_pdf(),
            ".pdf",
            FakeOCR(),
            20,
            PDFSettings(pdf_memory_bytes=memory_bytes),
        )
    limits = json.loads(pages[0].text)
    assert usages == [] and warnings == []
    assert all(
        0 < soft <= memory_bytes and 0 < hard <= memory_bytes for soft, hard in limits.values()
    )
    pid = await recorded_pid(pid_file)
    assert_reaped(pid)
    (tmp_path / "memory-limit-result.json").write_text(
        json.dumps(
            {
                "configured_bytes": memory_bytes,
                "observed": limits,
                "pid": pid,
                "reaped": True,
            }
        )
    )


@pytest.mark.parametrize(
    ("failure", "statement"),
    [("timeout", "time.sleep(60)"), ("crash", "os._exit(17)")],
)
@pytest.mark.skipif(os.name != "posix", reason="PID reaping assertion requires POSIX")
async def test_failed_child_fails_one_job_without_harming_other_org(
    api, application, headers, pdf_bytes, tmp_path, monkeypatch, failure, statement
):
    # Upload validation must complete before the process fault is introduced.
    _, failed_document = await create_document(api, headers[0], pdf_bytes)
    _, healthy_document = await create_document(api, headers[1], pdf_bytes)
    pid_file = tmp_path / f"job-{failure}.pid"
    processor = application.state.processor
    original_settings = processor.settings

    try:
        processor.settings = original_settings.model_copy(update={"pdf_timeout_seconds": 0.5})
        with monkeypatch.context() as fault:
            fault.setattr("app.core.pdf_process.child_command", fault_command(pid_file, statement))
            _, failed = await run_job(api, application, headers[0], failed_document, "parse")
    finally:
        processor.settings = original_settings

    assert failed["status"] == "failed" and failed["attempts"] == 1
    assert failed["error"]["code"] == "pdf_resource_limits"
    assert failed["error"]["exit_code"] != 3
    assert (await api.get(f"/documents/{failed_document}/chunks", headers=headers[0])).json()[
        "items"
    ] == []
    pid = await recorded_pid(pid_file)
    assert_reaped(pid)

    _, succeeded = await run_job(api, application, headers[1], healthy_document, "parse")
    assert succeeded["status"] == "succeeded" and succeeded["attempts"] == 1
    assert (await api.get(f"/documents/{healthy_document}/chunks", headers=headers[1])).json()[
        "items"
    ]
    (tmp_path / f"job-{failure}-result.json").write_text(
        json.dumps(
            {
                "failure": failure,
                "failed_status": failed["status"],
                "attempts": failed["attempts"],
                "code": failed["error"]["code"],
                "pid": pid,
                "reaped": True,
                "other_org_status": succeeded["status"],
            }
        )
    )
