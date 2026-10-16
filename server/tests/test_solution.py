"""Solution review through API and runtime-role database gates.

Failure matrix: non-human confirmations (including forged scopes), stale writes,
editing after confirmation, mutable revision history and pointer skipping must fail.
Synthetic initial chapter is seeded as a human draft; provider publication is covered
by the solution job acceptance test.
"""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.models.entities import ApiToken, Document, Task
from app.models.solution import SolutionChapter, SolutionRevision
from app.services.auth import HUMAN_SCOPES, Identity, set_actor_context
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


async def seed(application):
    task_id, document_id, chapter_id = uuid4(), uuid4(), uuid4()
    async with application.state.db.transaction() as session:
        await set_actor_context(session, Identity(scopes=HUMAN_SCOPES))
        session.add(Task(id=task_id, name="Synthetic solution project"))
        await session.flush()
        session.add(
            Document(
                id=document_id,
                task_id=task_id,
                name="tender.pdf",
                sha256="a" * 64,
                storage_key="synthetic",
                media_type="application/pdf",
                status="parsed",
            )
        )
        await session.flush()
        chapter = SolutionChapter(
            id=chapter_id,
            task_id=task_id,
            document_id=document_id,
            title="整体服务方案",
            score_range="0~25",
            current_revision=0,
        )
        session.add(chapter)
        await session.flush()
        session.add(
            SolutionRevision(
                chapter_id=chapter_id,
                revision=1,
                state="draft",
                origin="human",
                sections=[
                    {"heading": "项目组织", "paragraphs": ["我方安排【待填：负责人】检查。"]}
                ],
                actor_kind="session",
            )
        )
        await session.flush()
        chapter.current_revision = 1
    return task_id, document_id, chapter_id


@pytest.mark.asyncio
async def test_review_edit_stale_and_token_gate(application, api, headers, token_headers, tmp_path):
    task, document, chapter = await seed(application)
    path = f"/solutions/{chapter}"
    shown = await api.get(path, headers=headers)
    assert shown.status_code == 200, shown.text
    assert shown.json()["data"]["placeholder_count"] == 1
    token = await token_headers(api, {"task:read", "solution:read", "solution:draft"})
    denied = await api.post(path + "/confirm", headers=token, json={"expected_revision": 1})
    assert denied.status_code == 403
    confirmed = await api.post(path + "/confirm", headers=headers, json={"expected_revision": 1})
    assert confirmed.status_code == 200, confirmed.text
    current = confirmed.json()["data"]["current"]
    assert current["state"] == "confirmed"
    stale = await api.put(
        path,
        headers=headers,
        json={
            "expected_revision": 1,
            "sections": [{"heading": "新标题", "paragraphs": ["新方案"]}],
        },
    )
    assert stale.status_code == 409
    edited = await api.put(
        path,
        headers=headers,
        json={
            "expected_revision": current["revision"],
            "sections": [{"heading": "检查", "paragraphs": ["我方每周检查执行情况。"]}],
        },
    )
    assert edited.status_code == 200, edited.text
    result = edited.json()["data"]
    assert result["current"]["state"] == "draft"
    assert result["current"]["origin"] == "human"
    assert result["current"]["confirmed_at"] is None
    listed = await api.get(
        f"/tasks/{task}/solutions", params={"document": str(document)}, headers=token
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"][0] == result
    (tmp_path / "solution-review.json").write_text(
        json.dumps(
            {
                "confirmed": current["state"],
                "edited": result["current"]["state"],
                "stale_status": stale.status_code,
                "token_status": denied.status_code,
            }
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["agent", "token", "worker"])
async def test_db_rejects_nonhuman_confirmation(application, kind):
    _, _, chapter = await seed(application)
    token_id = None
    if kind == "token":
        token_id = uuid4()
        async with application.state.db.transaction() as session:
            await set_actor_context(session, Identity(scopes=HUMAN_SCOPES))
            session.add(
                ApiToken(
                    id=token_id,
                    name="synthetic",
                    digest=uuid4().hex * 2,
                    scopes=["task:read", "solution:read", "solution:draft"],
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
    with pytest.raises(DBAPIError, match="Human decision context required"):
        async with application.state.db.transaction() as session:
            # Even a forged confirmation grant cannot turn an agent into a human.
            actor = Identity(actor_kind=kind, token_id=token_id, scopes={"solution:confirm"})
            await set_actor_context(session, actor)
            session.add(
                SolutionRevision(
                    chapter_id=chapter,
                    revision=2,
                    state="confirmed",
                    origin="human",
                    sections=[
                        {"heading": "项目组织", "paragraphs": ["我方安排【待填：负责人】检查。"]}
                    ],
                    actor_kind=kind,
                    actor_token_id=token_id,
                    confirmed_at=datetime.now(UTC),
                )
            )
            await session.flush()


@pytest.mark.asyncio
async def test_runtime_cannot_rewrite_history_or_skip_revision(application):
    _, _, chapter = await seed(application)
    for statement, message in (
        ("UPDATE solution_revisions SET sections='[]' WHERE chapter_id=:id", "immutable"),
        ("DELETE FROM solution_revisions WHERE chapter_id=:id", "immutable"),
        ("UPDATE solution_chapters SET current_revision=3 WHERE id=:id", "immutable"),
        ("UPDATE solution_chapters SET current_revision=2 WHERE id=:id", "latest stored revision"),
    ):
        with pytest.raises(DBAPIError, match=message):
            async with application.state.db.transaction() as session:
                await set_actor_context(session, Identity(scopes=HUMAN_SCOPES))
                await session.execute(text(statement), {"id": chapter})


@pytest.mark.asyncio
async def test_whole_bid_download_cannot_bypass_solution_read(
    application, api, token_headers, tmp_path
):
    from app.models.entities import Chunk

    task, document, _ = await seed(application)
    async with application.state.db.transaction() as session:
        await set_actor_context(session, Identity(scopes=HUMAN_SCOPES))
        stored = await session.get_one(Document, document)
        stored.media_type = (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        session.add(
            Chunk(
                task_id=task,
                document_id=document,
                page=1,
                seq=1,
                text=(
                    "第一章 评标办法\n整体服务方案\t0~25\t根据服务方案评分\n"
                    "第二章 采购需求\n每周检查服务落实情况。\n"
                    "第三章 投标文件格式\n附件1 技术文件\n合成测试用技术封面\n"
                ),
            )
        )
    for scopes, status in (({"task:read"}, 403), ({"task:read", "solution:read"}, 200)):
        headers = await token_headers(api, scopes)
        link = await api.get(f"/documents/{document}/whole-bid-link", headers=headers)
        assert link.status_code == 200, link.text
        response = await api.get(link.json()["data"]["url"], headers=headers)
        assert response.status_code == status, response.text if status != 200 else ""
        if status == 200:
            assert response.headers["X-Whole-Bid-Unconfirmed-Chapters"] == "1"
            (tmp_path / "solution-review.docx").write_bytes(response.content)
