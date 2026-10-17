"""API-to-worker acceptance for the model scoring review, with a synthetic model.

Failure modes: an actor without `solution:draft` starts a review; the model's
invented item or wrong top score reaches the outline; a phone number in the rules
reaches the model; the job read leaks the encrypted input; the call has no usage
record; the outline keeps a review after the chunks it read changed; solution
drafting still sees only the rule parser's items. The artifact holds only the
synthetic request and outline.
"""

import json
import os
import re
from io import BytesIO
from typing import cast
from uuid import UUID

import httpx
from app.api.main import create_app
from app.core.config import Settings
from app.models.entities import Job, UsageRecord
from app.services import scoring_review, solution_generation
from docx import Document
from pydantic import SecretStr
from sqlalchemy import select
from tests.conftest import FakeQueue
from tests.test_check_combined import SemanticVendor, semantic_llm
from tests.test_response_cards import login

ROWS = (
    ("1", "价格", "30", "价格分按评标基准价计算。"),
    ("2", "技术服务方案", "40", "方案完整、针对性强的得40分；联系电话13800138000。"),
    ("3", "项目团队", "20", "主观分。团队配置合理、分工明确的得20分。"),
    ("4", "企业业绩", "10", "每份合同得2分。"),
)


class ReviewVendor:
    def __init__(self):
        self.texts: list[str] = []

    async def __call__(self, request):
        body = json.loads(request.content)
        assert body["response_format"]["json_schema"]["name"] == "scoring_review"
        text = body["messages"][-1]["content"]
        self.texts.append(text)

        def line(title):
            return next(
                int(found.group(1))
                for found in re.finditer(r"^L(\d+)\|(.*)$", text, re.M)
                if f" | {title} | " in found.group(2)
            )

        items = [
            {"line": line(title), "title": title, "max": float(top)} for _, title, top, _ in ROWS
        ]
        # An invented item and a wrong top score, both of which verification drops.
        items.append({"line": line("企业业绩"), "title": "售后服务承诺", "max": 10.0})
        items.append({"line": line("项目团队"), "title": "项目团队", "max": 25.0})
        output = {"total": 100.0, "multiple_packages": False, "items": items}
        return httpx.Response(
            200,
            json={
                "model": "synthetic-check-model",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}],
                "usage": {"prompt_tokens": 300, "completion_tokens": 60},
            },
        )


def tender_bytes():
    document = Document()
    document.add_paragraph("项目名称：合成评分测试项目")
    document.add_paragraph("第一章 采购需求")
    document.add_paragraph("提供信息系统运维服务。")
    document.add_paragraph("第二章 评标办法")
    document.add_paragraph("本项目采用综合评分法，满分100分。")
    table = document.add_table(rows=0, cols=4)
    for values in (("序号", "评审因素", "分值", "评分标准"), *ROWS):
        for cell, text in zip(table.add_row().cells, values, strict=True):
            cell.text = text
    document.add_paragraph("第三章 投标文件格式")
    document.add_paragraph("附件1 投标函")
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


async def test_scoring_review_keeps_only_verified_items(
    database, tmp_path, monkeypatch, token_headers
):
    vendor = ReviewVendor()
    settings = Settings(
        data_dir=tmp_path,
        database_url=SecretStr(os.environ["BID_DATABASE_URL"]),
        access_key=SecretStr(os.environ["BID_ACCESS_KEY"]),
        encryption_key=SecretStr(os.environ["BID_ENCRYPTION_KEY"]),
        token_key=SecretStr(os.environ["BID_TOKEN_KEY"]),
    )
    llm = semantic_llm(settings, cast(SemanticVendor, vendor))

    async def resolved(session, settings, transport=None, job=None, model=None):
        return llm

    monkeypatch.setattr(scoring_review.llm_providers, "resolve_llm", resolved)
    monkeypatch.setattr(solution_generation.llm_providers, "resolve_llm", resolved)
    app = create_app(settings, llm=llm, queue=FakeQueue())
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api,
    ):
        header = await login(api)
        task = (await api.post("/tasks", headers=header, json={"name": "合成评分测试"})).json()[
            "data"
        ]["id"]
        uploaded = await api.post(
            f"/tasks/{task}/documents",
            headers=header,
            files={
                "file": (
                    "synthetic.docx",
                    tender_bytes(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        document = uploaded.json()["data"]["id"]
        parsed = await api.post(f"/documents/{document}/parse", headers=header, json={})
        await app.state.processor(parsed.json()["data"]["job_id"])

        before = (await api.get(f"/documents/{document}/outline", headers=header)).json()["data"]
        assert before["scoring_origin"] == "rules"
        assert before["scoring_items"] == [] and before["scoring_warnings"] == ["no_items"]

        reader = await token_headers(api, {"task:read", "solution:read", "job:read"})
        denied = await api.post(f"/documents/{document}/outline/review", headers=reader, json={})
        assert denied.status_code == 403, denied.text

        agent = await token_headers(
            api, {"task:read", "solution:draft", "solution:read", "job:read"}
        )
        submitted = await api.post(f"/documents/{document}/outline/review", headers=agent, json={})
        assert submitted.status_code == 200, submitted.text
        job_id = submitted.json()["data"]["job_id"]
        await app.state.processor(job_id)
        status = (await api.get(f"/jobs/{job_id}", headers=agent)).json()
        assert status["data"]["status"] == "succeeded", status
        assert "submission" not in status["data"]["result"]
        assert status["data"]["result"]["item_count"] == 4
        assert status["data"]["result"]["rejected_count"] == 2
        assert len(vendor.texts) == 1 and "13800138000" not in vendor.texts[0]

        outline = (await api.get(f"/documents/{document}/outline", headers=header)).json()["data"]
        assert outline["scoring_origin"] == "model"
        assert [
            (item["title"], item["score_range"], item["subjective"])
            for item in outline["scoring_items"]
        ] == [
            ("价格", "0~30", False),
            ("技术服务方案", "0~40", True),
            ("项目团队", "0~20", True),
            ("企业业绩", "0~10", False),
        ]
        assert outline["scoring_total"] == 100 and outline["scoring_sum"] == 100
        assert outline["scoring_warnings"] == []

        drafted = await api.post(
            f"/tasks/{task}/solutions/draft",
            headers=header,
            json={"document_id": document, "titles": ["技术服务方案", "项目团队"]},
        )
        assert drafted.status_code == 200, drafted.text

        async with app.state.db.transaction() as session:
            usage = (
                await session.scalars(select(UsageRecord).where(UsageRecord.job_id == UUID(job_id)))
            ).all()
            assert len(usage) == 1 and usage[0].tokens == 360
            job = await session.get(Job, UUID(job_id))
            assert job is not None
            job.result = {**job.result, "chunks_sha256": "0" * 64}
        stale = (await api.get(f"/documents/{document}/outline", headers=header)).json()["data"]
        assert stale["scoring_origin"] == "rules" and stale["scoring_items"] == []

        (tmp_path / "scoring-review-acceptance.json").write_text(
            json.dumps(
                {"model_text": vendor.texts[0], "job": status, "outline": outline},
                ensure_ascii=False,
                indent=2,
            )
        )
