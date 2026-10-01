from uuid import uuid4

import pytest
from app.core.errors import ServiceError
from app.providers.base import ProviderFailure
from app.providers.disabled import DisabledLLM
from app.providers.local_ocr import LocalOCR
from app.providers.storage import LocalStorage
from app.schemas.contracts import ExtractedRequirement, Extraction, Source
from app.services.extraction import merge_starred, validate_extraction
from app.services.parsing import parse_document, validate_document
from cryptography.fernet import Fernet
from fakes import FakeOCR


async def test_pdf_pages_and_text(pdf_bytes):
    pages, usages, warnings = await parse_document(pdf_bytes, ".pdf", FakeOCR(), 20)
    assert len(pages) == 2 and [page.page for page in pages] == [1, 2]
    assert all(page.citation_verified and not page.ocr for page in pages)
    assert "64 GB" in pages[0].text and usages == [] and warnings == []


@pytest.mark.parametrize(
    "content,suffix", [(b"garbage", ".pdf"), (b"garbage", ".docx"), (b"garbage", ".exe")]
)
def test_rejects_invalid_uploads(content, suffix):
    with pytest.raises(ServiceError):
        validate_document(content, suffix, 20)


async def test_word_does_not_invent_page_citations():
    import io

    from docx import Document

    document = Document()
    document.add_paragraph("Synthetic Word content")
    output = io.BytesIO()
    document.save(output)
    pages, _, warnings = await parse_document(output.getvalue(), ".docx", FakeOCR(), 20)
    assert not pages[0].citation_verified and warnings


async def test_scanned_pdf_uses_provider_and_records_usage():
    import pymupdf

    with pymupdf.open() as document:
        document.new_page()
        pages, usages, _ = await parse_document(document.tobytes(), ".pdf", FakeOCR(), 20)
    assert pages[0].ocr and usages[0].test_only and usages[0].ocr_pages == 1


async def test_disabled_provider_never_returns_fake_success():
    with pytest.raises(ProviderFailure):
        await DisabledLLM().extract([], {})
    with pytest.raises(ProviderFailure):
        await LocalOCR("eng", "/definitely-missing-tessdata").recognize(b"invalid", 1)


def test_citation_checks_every_source_and_star_rules():
    doc, chunk = uuid4(), uuid4()
    chunks = [
        {
            "id": chunk,
            "document_id": doc,
            "page": 2,
            "text": "★ Memory must be 64 GB\nA valid certificate is required",
            "citation_verified": True,
        }
    ]
    extraction = Extraction(
        items=[
            ExtractedRequirement(
                category="technical",
                text="Memory",
                source=Source(
                    document_id=doc, chunk_id=chunk, page=2, quote="★ Memory must be 64 GB"
                ),
            )
        ]
    )
    merged = merge_starred(extraction, chunks)
    assert len(merged.items) == 1 and merged.items[0].starred
    validate_extraction(merged, chunks)
    for changes in (
        {"document_id": uuid4()},
        {"chunk_id": uuid4()},
        {"page": 3},
        {"quote": "Fabricated value"},
    ):
        item = extraction.items[0].model_copy(deep=True)
        item.source = item.source.model_copy(update=changes)
        with pytest.raises(ServiceError):
            validate_extraction(Extraction(items=[item]), chunks)


async def test_local_storage_rejects_cross_tenant_and_traversal(tmp_path):
    org = uuid4()
    storage = LocalStorage(tmp_path, Fernet.generate_key().decode())
    key = f"org/{org}/task/test/file.pdf"
    await storage.put(org, key, b"test")
    assert await storage.read(org, key) == b"test"
    for scope, invalid in (
        (uuid4(), key),
        (org, f"org/{org}/../../outside"),
        (org, "/tmp/outside"),
    ):
        with pytest.raises(ServiceError):
            await storage.read(scope, invalid)


async def test_concurrent_immutable_storage_writes_are_atomic(tmp_path):
    import asyncio

    org = uuid4()
    storage = LocalStorage(tmp_path, Fernet.generate_key().decode())
    key = f"org/{org}/task/test/file.pdf"
    await asyncio.gather(*(storage.put(org, key, b"synthetic" * 10000) for _ in range(8)))
    assert await storage.read(org, key) == b"synthetic" * 10000
    assert not list(tmp_path.rglob("*.tmp"))
