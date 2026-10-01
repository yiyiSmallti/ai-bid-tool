"""Word tenders end to end: upload, located parsing, block-cited extraction and its failures."""

import io
import json

import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.providers.llm import AnthropicExtractor
from conftest import FakeQueue
from docx import Document
from fakes import FakeLLM
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_api import run_job
from test_job_boundaries import session_for
from test_llm_providers import Vendor, anthropic_reply, settings_for


def word_tender() -> bytes:
    document = Document()
    document.add_heading("第一章 总则", level=1)  # p1
    document.add_paragraph("投标人须具备有效的营业执照。")  # p2
    document.add_heading("一、技术参数", level=2)  # p3
    document.add_paragraph("服务器内存不低于 64GB。")  # p4
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "参数", "要求"
    table.cell(1, 0).text, table.cell(1, 1).text = "CPU", "★ 核心数不少于 32 核"
    document.add_paragraph("投标文件须注明“响应内容”及具体数值。")  # p5, curly quotes
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


async def upload_word(api, header) -> tuple[str, str]:
    task = (await api.post("/tasks", headers=header, json={"name": "Word tender"})).json()["data"][
        "id"
    ]
    response = await api.post(
        f"/tasks/{task}/documents", headers=header, files={"file": ("tender.docx", word_tender())}
    )
    assert response.status_code == 200, response.text
    return task, response.json()["data"]["id"]


def item(ref, quote, category="technical", starred=False):
    return {
        "category": category,
        "starred": starred,
        "text": quote,
        "ref": ref,
        "quote": quote,
        "condition": None,
    }


async def test_word_tender_parses_into_located_chunks(tenants, tmp_path):
    app = create_app(Settings(data_dir=tmp_path), llm=FakeLLM(), queue=FakeQueue())
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        _, document = await upload_word(api, header)
        _, status = await run_job(api, app, header, document, "parse")
        assert status["status"] == "succeeded", status
        assert status["result"]["sections"] == 2 and status["result"]["pages"] == 0
        meta = (await api.get(f"/documents/{document}", headers=header)).json()["data"]
        assert (meta["citation_mode"], meta["page_count"]) == ("block", None)
        chunks = (await api.get(f"/documents/{document}/chunks", headers=header)).json()["items"]
        assert [(c["seq"], c["page"], c["citation_verified"]) for c in chunks] == [
            (1, None, True),
            (2, None, True),
        ]
        labels = [b["label"] for c in chunks for b in c["blocks"]]
        assert "第一章 总则 > 第 1 段" in labels
        assert "第一章 总则 > 一、技术参数 > 表 1 第 2 行第 2 列" in labels

        # The fake provider cites each chunk's last block through the same pipeline.
        _, extracted = await run_job(api, app, header, document, "extract")
        assert extracted["status"] == "succeeded", extracted


async def test_word_extraction_cites_blocks_and_adds_starred_cells(tenants, tmp_path):
    vendor = Vendor(
        anthropic_reply([item("p2", "投标人须具备有效的营业执照。", "qualification")]),
        anthropic_reply(
            [item("p4", "服务器内存不低于 64GB。"), item("t1r2c2", "核心数不少于 32 核")]
        ),
    )
    settings = settings_for(tmp_path, "anthropic")
    # A tiny batch budget (below the configurable minimum) sends each section separately.
    tiny = settings.model_copy(update={"llm_batch_chars": 40, "llm_concurrency": 1})
    app = create_app(
        settings,
        llm=AnthropicExtractor(tiny, transport=vendor.transport()),
        queue=FakeQueue(),
    )
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        task, document = await upload_word(api, header)
        await run_job(api, app, header, document, "parse")
        _, status = await run_job(api, app, header, document, "extract")
        assert status["status"] == "succeeded", status
        rows = (await api.get(f"/tasks/{task}/requirements", headers=header)).json()["items"]

    sources = [
        (
            r["source"]["page"],
            r["source"]["location"]["block_id"],
            r["source"]["quote"],
            r["starred"],
        )
        for r in rows
    ]
    assert sources == [
        (None, "p2", "投标人须具备有效的营业执照。", False),
        (None, "p4", "服务器内存不低于 64GB。", False),
        (None, "t1r2c2", "核心数不少于 32 核", True),
    ]
    cell = rows[2]["source"]["location"]
    assert (cell["kind"], cell["table"], cell["row"], cell["column"]) == ("cell", 1, 2, 2)
    assert cell["label"] == "第一章 总则 > 一、技术参数 > 表 1 第 2 行第 2 列"
    first = json.loads(vendor.requests[0].content)["messages"][0]["content"]
    assert '<block id="p2">' in first and '<section path="第一章 总则">' in first
    assert "<page" not in first


@pytest.mark.parametrize(
    "items,code",
    [
        # Quote from another block in the same chunk, so chunk-level checks cannot pass it.
        ([item("p4", "核心数不少于 32 核")], "invalid_citation"),
        ([item("p4", "服务器内存不低于 64GB。参数")], "invalid_citation"),  # spans two blocks
        (
            [item("p999", "投标人须具备有效的营业执照。")],
            "invalid_provider_output",
        ),  # unknown block
    ],
)
async def test_word_citations_must_sit_inside_the_cited_block(items, code, tenants, tmp_path):
    vendor = Vendor(anthropic_reply(items))
    settings = settings_for(tmp_path, "anthropic")
    app = create_app(
        settings, llm=AnthropicExtractor(settings, transport=vendor.transport()), queue=FakeQueue()
    )
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        task, document = await upload_word(api, header)
        await run_job(api, app, header, document, "parse")
        _, status = await run_job(api, app, header, document, "extract")
        assert (status["status"], status["error"]["code"]) == ("failed", code)
        assert (await api.get(f"/tasks/{task}/requirements", headers=header)).json()["items"] == []


async def test_reparse_replaces_the_old_unverified_word_chunk(tenants, tmp_path, admin_engine):
    app = create_app(Settings(data_dir=tmp_path), llm=FakeLLM(), queue=FakeQueue())
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        task, document = await upload_word(api, header)
        org = tenants["orgs"][0]
        with Session(admin_engine) as session, session.begin():
            session.execute(
                text(
                    "INSERT INTO chunks (id, org_id, task_id, document_id, page, seq, text, ocr, citation_verified) "
                    "VALUES (gen_random_uuid(), :o, :t, :d, 1, 1, 'legacy flat text', false, false)"
                ),
                {"o": org, "t": task, "d": document},
            )
        await run_job(api, app, header, document, "parse")
        chunks = (await api.get(f"/documents/{document}/chunks", headers=header)).json()["items"]
        assert [c["text"] for c in chunks if c["text"] == "legacy flat text"] == []
        assert all(c["blocks"] and c["page"] is None for c in chunks)


async def test_straight_quotes_match_curly_source_quotes(tenants, tmp_path):
    vendor = Vendor(anthropic_reply([item("p5", '投标文件须注明"响应内容"及具体数值。')]))
    settings = settings_for(tmp_path, "anthropic")
    app = create_app(
        settings, llm=AnthropicExtractor(settings, transport=vendor.transport()), queue=FakeQueue()
    )
    async with app.router.lifespan_context(app), session_for(app, tenants) as (api, header):
        task, document = await upload_word(api, header)
        await run_job(api, app, header, document, "parse")
        _, status = await run_job(api, app, header, document, "extract")
        assert status["status"] == "succeeded", status
        rows = (await api.get(f"/tasks/{task}/requirements", headers=header)).json()["items"]
        assert [r["source"]["location"]["block_id"] for r in rows] == ["t1r2c2", "p5"]  # source order
