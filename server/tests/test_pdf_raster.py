"""PDF entry-point regressions, using synthetic documents and the fake OCR provider.

Failure modes: huge media boxes allocate before refusal; page-number text hides
scanned requirements; overlapping/rotated images confuse coverage; merging duplicates
citations; empty OCR silently succeeds; large but usable pages exceed the pixel budget.
"""

import json
import sys
from pathlib import Path

import pymupdf
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.core.errors import ServiceError
from app.schemas.contracts import PageText
from app.services.parsing import parse_document, validate_document
from conftest import FakeQueue
from fakes import FakeLLM, FakeOCR
from test_api import create_document, run_job
from test_job_boundaries import session_for

BODY = "Synthetic OCR fixture only"


def guarded_render_command(marker: Path):
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from app.core import pdf_child\n"
        "apply_limits = pdf_child.apply_limits\n"
        "def install_guard(request):\n"
        "    apply_limits(request)\n"
        "    import pymupdf\n"
        "    def guarded(*_args, **_kwargs):\n"
        f"        Path({str(marker)!r}).write_text('called')\n"
        "        raise AssertionError('raster allocation attempted')\n"
        "    pymupdf.Page.get_pixmap = guarded\n"
        "pdf_child.apply_limits = install_guard\n"
        "raise SystemExit(pdf_child.main(Path(sys.argv[1])))\n"
    )

    def command(root: Path) -> list[str]:
        return [sys.executable, "-c", script, str(root)]

    return command


def pdf_page(width=595, height=842, *, scanned=False, rotation=0, native="1"):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=width, height=height)
        if scanned:
            with pymupdf.open() as scan:
                body = scan.new_page(width=500, height=650)
                body.insert_text((30, 60), BODY)
                image = body.get_pixmap().tobytes("png")
            page.insert_image((30, 30, width - 30, height - 70), stream=image)
        if native:
            page.insert_text((40, height - 30), native)
        page.set_rotation(rotation)
        return pdf.tobytes()


class RecordingOCR(FakeOCR):
    def __init__(self, text=BODY):
        self.text = text
        self.images = []

    async def recognize(self, image, page):
        pixmap = pymupdf.Pixmap(image)
        self.images.append((page, pixmap.width, pixmap.height))
        result = await super().recognize(image, page)
        return result.model_copy(update={"text": self.text})


async def test_oversized_parse_refuses_before_raster_allocation(tmp_path, monkeypatch):
    content = pdf_page(14400, 14400, native="")
    ocr = RecordingOCR()
    render_marker = tmp_path / "get-pixmap-called"
    with monkeypatch.context() as guarded:
        guarded.setattr("app.core.pdf_process.child_command", guarded_render_command(render_marker))
        with pytest.raises(ServiceError) as refused:
            await parse_document(content, ".pdf", ocr, 20)
    assert refused.value.code == "pdf_raster_limits" and refused.value.exit_code != 3
    assert "page" in refused.value.message.lower()
    assert not render_marker.exists() and ocr.images == []
    (tmp_path / "oversized-refusal.json").write_text(
        json.dumps(
            {
                "bytes": len(content),
                "code": refused.value.code,
                "render_guard_called": render_marker.exists(),
                "ocr_calls": len(ocr.images),
            }
        )
    )


async def test_oversized_upload_validation():
    with pytest.raises(ServiceError, match="page"):
        validate_document(pdf_page(14400, 14400), ".pdf", 20)


@pytest.mark.parametrize("rotation", [0, 90])
async def test_mixed_parse_keeps_body_and_native_text(tmp_path, rotation):
    ocr = RecordingOCR()
    pages, usages, warnings = await parse_document(
        pdf_page(scanned=True, rotation=rotation), ".pdf", ocr, 20
    )
    assert pages[0].ocr and BODY in pages[0].text and "1" in pages[0].text.splitlines()
    assert len(ocr.images) == 1 and usages[0].ocr_pages == 1 and warnings == []
    (tmp_path / "mixed-page.json").write_text(pages[0].model_dump_json())


async def test_large_sensible_page_reduces_ocr_resolution(tmp_path):
    content = pdf_page(2400, 2400, native="")
    ocr = RecordingOCR()
    pages, _, warnings = await parse_document(content, ".pdf", ocr, 20)
    assert pages[0].ocr and ocr.images == [(1, 4467, 4467)]
    _, width, height = ocr.images[0]
    assert width * height <= 20_000_000 and max(width, height) <= 8192
    assert 72 <= round(width * 72 / 2400) < 180
    assert warnings == [
        "Page 1 OCR resolution was reduced to 134 DPI to fit raster limits; "
        "check small text manually."
    ]
    (tmp_path / "reduced-resolution.json").write_text(
        json.dumps({"page": 1, "png_width": width, "png_height": height, "warnings": warnings})
    )


async def test_native_text_parse_never_calls_ocr(pdf_bytes):
    ocr = RecordingOCR()
    pages, usages, warnings = await parse_document(pdf_bytes, ".pdf", ocr, 20)
    assert all(not page.ocr for page in pages)
    assert ocr.images == [] and usages == [] and warnings == []


async def test_empty_mixed_ocr_reports_incomplete_page():
    pages, _, warnings = await parse_document(pdf_page(scanned=True), ".pdf", RecordingOCR("1"), 20)
    assert pages[0].ocr and pages[0].text.strip() == "1"
    assert warnings and "Page 1" in warnings[0] and "manual review" in warnings[0]


@pytest.mark.parametrize(
    "layout, expected_ocr", [("overlap", False), ("tiles", True), ("native", False)]
)
async def test_parse_uses_visible_image_union_minus_native_blocks(layout, expected_ocr):
    image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2, 2), False)
    image.clear_with(240)
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=400, height=400)
        if layout == "native":
            page.insert_text((20, 160), "Text", fontsize=150)
            regions = [pymupdf.Rect(page.get_text("blocks")[0][:4])]
        else:
            page.insert_text((20, 390), "1")
            regions = (
                [pymupdf.Rect(0, 0, 100, 160)] * 4
                if layout == "overlap"
                else [pymupdf.Rect(x, 20, x + 100, 140) for x in (0, 100, 200, 300)]
            )
        for region in regions:
            page.insert_image(
                region, stream=image.tobytes("png"), keep_proportion=False, overlay=False
            )
        content = pdf.tobytes()
    ocr = RecordingOCR()
    pages, _, warnings = await parse_document(content, ".pdf", ocr, 20)
    assert pages[0].ocr == expected_ocr and bool(ocr.images) == expected_ocr
    if layout == "overlap":
        assert warnings and "not parsed" in warnings[0]
    elif layout == "native":
        assert warnings == []


async def test_dense_native_text_suppresses_full_page_background_image(tmp_path):
    image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2, 2), False)
    image.clear_with(240)
    native = "\n".join(
        f"Searchable native line {number:02d} fills this page." for number in range(16)
    )
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=400, height=400)
        page.insert_image(page.rect, stream=image.tobytes("png"), keep_proportion=False)
        assert (
            page.insert_textbox(
                pymupdf.Rect(20, 20, 380, 380), native, fontsize=16, lineheight=1.25
            )
            >= 0
        )
        blocks = [
            pymupdf.Rect(block[:4])
            for block in page.get_text("blocks")
            if block[6] == 0 and block[4].strip()
        ]
        text_ratio = sum(block.get_area() for block in blocks) / page.rect.get_area()
        assert text_ratio >= 0.25
        content = pdf.tobytes()

    ocr = RecordingOCR()
    pages, usages, warnings = await parse_document(content, ".pdf", ocr, 20)
    assert pages[0].text.strip() == native and not pages[0].ocr
    assert ocr.images == [] and usages == [] and warnings == []
    (tmp_path / "background-image.json").write_text(
        json.dumps({"native_text_ratio": text_ratio, "ocr": pages[0].ocr, "warnings": warnings})
    )


@pytest.mark.parametrize("rotation", [0, 90])
async def test_background_filter_is_per_image_and_keeps_a_separate_scan(tmp_path, rotation):
    background = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 2, 2), False)
    background.clear_with(240)
    with pymupdf.open() as scan_pdf:
        scan_page = scan_pdf.new_page(width=220, height=320)
        scan_page.insert_text((20, 60), BODY)
        scan = scan_page.get_pixmap().tobytes("png")

    native = "\n".join(
        f"Native background line {number:02d} remains searchable." for number in range(16)
    )
    background_rect = pymupdf.Rect(0, 0, 360, 400)
    scan_rect = pymupdf.Rect(380, 40, 600, 360)
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=400)
        page.insert_image(background_rect, stream=background.tobytes("png"), keep_proportion=False)
        page.insert_image(scan_rect, stream=scan, keep_proportion=False)
        assert (
            page.insert_textbox(
                pymupdf.Rect(20, 20, 340, 380), native, fontsize=15, lineheight=1.25
            )
            >= 0
        )
        page.insert_text((390, 350), "7")
        blocks = [
            pymupdf.Rect(block[:4])
            for block in page.get_text("blocks")
            if block[6] == 0 and block[4].strip()
        ]
        background_ratio = sum((block & background_rect).get_area() for block in blocks) / (
            background_rect.get_area()
        )
        scan_ratio = sum((block & scan_rect).get_area() for block in blocks) / scan_rect.get_area()
        assert background_ratio >= 0.25 and scan_ratio < 0.25
        page.set_rotation(rotation)
        content = pdf.tobytes()

    ocr = RecordingOCR("7\n" + BODY)
    pages, usages, warnings = await parse_document(content, ".pdf", ocr, 20)
    assert pages[0].ocr and len(ocr.images) == 1 and usages[0].ocr_pages == 1
    # Sorted native text preserves layout padding for the separate right-hand column.
    assert native in pages[0].text
    assert [line.strip() for line in pages[0].text.splitlines()].count("7") == 1
    assert BODY in pages[0].text and warnings == []
    (tmp_path / f"per-image-background-filter-{rotation}.json").write_text(
        json.dumps(
            {
                "rotation": rotation,
                "background_text_ratio": background_ratio,
                "scan_text_ratio": scan_ratio,
                "ocr": pages[0].ocr,
                "warnings": warnings,
            }
        )
    )


@pytest.mark.parametrize("recognized", ["1\n" + BODY, BODY + "\n1", BODY + "\n128 GB"])
async def test_mixed_parse_merges_without_duplicate_or_truncated_citations(recognized):
    pages, _, _ = await parse_document(pdf_page(scanned=True), ".pdf", RecordingOCR(recognized), 20)
    text = pages[0].text
    assert text.splitlines().count("1") == 1 and text.count(BODY) == 1
    if "128 GB" in recognized:
        assert "128 GB" in text


async def test_reparse_job_reports_preserved_historical_citations(tenants, tmp_path, monkeypatch):
    app = create_app(Settings(data_dir=tmp_path), ocr=RecordingOCR(), queue=FakeQueue())

    async def legacy_parse(*args):
        return [PageText(page=1, text="1")], [], []

    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        _, document = await create_document(api, header, pdf_page(scanned=True))
        with monkeypatch.context() as legacy:
            legacy.setattr("app.services.tender_jobs.PARSER_VERSION", "parse-v2")
            legacy.setattr("app.jobs.processor.parse_document", legacy_parse)
            _, old = await run_job(api, app, header, document, "parse")
        assert old["status"] == "succeeded"
        _, parsed = await run_job(api, app, header, document, "parse")
        assert parsed["status"] == "succeeded"
        assert any("previously stored" in warning for warning in parsed["result"]["warnings"])
        chunks = (await api.get(f"/documents/{document}/chunks", headers=header)).json()["items"]
        assert chunks[0]["text"] == "1"


async def test_native_text_job_records_no_ocr_usage(tenants, tmp_path, pdf_bytes):
    ocr = RecordingOCR()
    app = create_app(Settings(data_dir=tmp_path), ocr=ocr, queue=FakeQueue())
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        _, document = await create_document(api, header, pdf_bytes)
        _, parsed = await run_job(api, app, header, document, "parse")
        assert parsed["status"] == "succeeded" and parsed["result"]["cost"]["ocr_pages"] == 0
        chunks = (await api.get(f"/documents/{document}/chunks", headers=header)).json()["items"]
        assert all(not chunk["ocr"] for chunk in chunks) and ocr.images == []


async def test_mixed_page_job_and_extraction_verify_body_citation(tenants, tmp_path):
    ocr = RecordingOCR("1\n" + BODY)
    app = create_app(Settings(data_dir=tmp_path), ocr=ocr, llm=FakeLLM(), queue=FakeQueue())
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        task, document = await create_document(api, header, pdf_page(scanned=True))
        _, parsed = await run_job(api, app, header, document, "parse")
        assert parsed["status"] == "succeeded" and parsed["result"]["cost"]["ocr_pages"] == 1
        chunks = (await api.get(f"/documents/{document}/chunks", headers=header)).json()["items"]
        assert chunks[0]["ocr"] and chunks[0]["text"].splitlines().count("1") == 1
        _, extracted = await run_job(api, app, header, document, "extract")
        assert extracted["status"] == "succeeded" and extracted["result"]["created"] == 1
        items = (await api.get(f"/tasks/{task}/requirements", headers=header)).json()["items"]
        assert items[0]["source"]["quote"] == BODY
        assert items[0]["source"]["quote"] in chunks[0]["text"]
        (tmp_path / "parse-extract.json").write_text(
            json.dumps({"parsed": parsed, "chunks": chunks, "requirements": items})
        )


async def test_oversized_legacy_job_fails_cleanly(api, application, headers, monkeypatch):
    content = pdf_page(14400, 14400, native="")

    # Model a file accepted by the previous upload validator; the worker must defend itself.
    async def legacy_validation(*args):
        return None

    with monkeypatch.context() as legacy:
        legacy.setattr("app.services.documents.validate_document_async", legacy_validation)
        task, document = await create_document(api, headers[0], content)
    _, failed = await run_job(api, application, headers[0], document, "parse")
    assert failed["status"] == "failed" and failed["attempts"] == 1
    assert failed["error"]["code"] == "pdf_raster_limits"
    assert failed["error"]["exit_code"] != 3
    assert (await api.get(f"/documents/{document}/chunks", headers=headers[0])).json()[
        "items"
    ] == []
    upload = await api.post(
        f"/tasks/{task}/documents", headers=headers[0], files={"file": ("oversized.pdf", content)}
    )
    assert (
        upload.status_code == 400 and upload.json()["data"]["error"]["code"] == "pdf_raster_limits"
    )
