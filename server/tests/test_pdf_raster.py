"""PDF entry-point regressions, using synthetic documents and the fake OCR provider.

Failure modes: huge media boxes allocate before refusal; page-number text hides
scanned requirements; overlapping/rotated images confuse coverage; merging duplicates
citations; empty OCR silently succeeds; large but usable pages exceed the pixel budget.
"""

import json
import math
from unittest.mock import patch

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


async def test_oversized_parse_refuses_before_raster_allocation(tmp_path):
    content = pdf_page(14400, 14400, native="")
    with patch.object(pymupdf.Page, "get_pixmap") as render:
        render.side_effect = AssertionError("Huge raster allocation attempted")
        with pytest.raises(ServiceError) as refused:
            await parse_document(content, ".pdf", FakeOCR(), 20)
    assert refused.value.code == "pdf_raster_limits" and refused.value.exit_code != 3
    assert "page" in refused.value.message.lower()
    render.assert_not_called()
    (tmp_path / "oversized-refusal.json").write_text(
        json.dumps({"bytes": len(content), "code": refused.value.code, "raster_calls": 0})
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


async def test_large_sensible_page_reduces_ocr_resolution():
    content = pdf_page(2400, 2400, native="")
    original = pymupdf.Page.get_pixmap
    requested = []

    def guarded(page, *args, **kwargs):
        dpi = kwargs["dpi"]
        width, height = (
            math.ceil(page.rect.width * dpi / 72),
            math.ceil(page.rect.height * dpi / 72),
        )
        assert 72 <= dpi < 180 and width * height <= 20_000_000 and max(width, height) <= 8192
        requested.append(dpi)
        return original(page, *args, **kwargs)

    with patch.object(pymupdf.Page, "get_pixmap", guarded):
        pages, _, _ = await parse_document(content, ".pdf", FakeOCR(), 20)
    assert pages[0].ocr and len(requested) == 1


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
    with monkeypatch.context() as legacy:
        legacy.setattr("app.services.documents.validate_document", lambda *args: None)
        task, document = await create_document(api, headers[0], content)
    with patch.object(pymupdf.Page, "get_pixmap") as render:
        render.side_effect = AssertionError("Huge raster allocation attempted")
        _, failed = await run_job(api, application, headers[0], document, "parse")
    assert failed["status"] == "failed" and failed["attempts"] == 1
    assert failed["error"]["code"] == "pdf_raster_limits"
    assert failed["error"]["exit_code"] != 3
    render.assert_not_called()
    assert (await api.get(f"/documents/{document}/chunks", headers=headers[0])).json()[
        "items"
    ] == []
    upload = await api.post(
        f"/tasks/{task}/documents", headers=headers[0], files={"file": ("oversized.pdf", content)}
    )
    assert (
        upload.status_code == 400 and upload.json()["data"]["error"]["code"] == "pdf_raster_limits"
    )
