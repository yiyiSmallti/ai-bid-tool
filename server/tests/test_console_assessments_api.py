"""DB-free end-to-end HTTP routing acceptance with typed synthetic projections."""

from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from app.api.check import create_router as check_router
from app.api.org_console import create_router as console_router
from app.api.score import create_router as score_router
from app.schemas.console_assessments import (
    CheckSummaryData,
    CitationContextData,
    PageData,
    ProjectionPage,
    RubricSummaryData,
    ScoreSummaryData,
)
from app.services.auth import Identity
from fastapi import FastAPI
from test_console_assessments_cli import check_summary, rubric_summary, score_summary


@pytest.fixture
def assessment_interface(monkeypatch):
    from app.api import org_console
    from app.services import assessment_reads, assessment_rubrics, assessment_scores

    calls = []
    actor = Identity(uuid4(), uuid4(), set(), "viewer")

    class Session:
        async def get(self, model, identifier):
            return SimpleNamespace(completion="partial", summary={"total_status": "unavailable"})

        async def scalars(self, statement):
            return []

    async def context():
        yield Session(), actor

    async def task_access(session, identity, task):
        pass

    async def checking(session, identity, parent, storage, settings):
        calls.append(("check_summary", parent))
        return CheckSummaryData.model_validate(check_summary())

    async def check_page(session, identity, parent, query, storage, settings):
        calls.append(("check_page", query.model_dump(mode="json")))
        return ProjectionPage(
            data=PageData(
                task_id=uuid4(),
                parent_id=parent,
                part=query.part,
                snapshot="signed",
                total=0,
                filtered_total=0,
                returned=0,
                next_cursor=None,
                validity="current",
            ),
            items=[],
        )

    async def rubrics(session, identity, task, parent):
        calls.append(("rubric_summary", task, parent))
        return RubricSummaryData.model_validate(rubric_summary())

    async def scoring(session, identity, task, parent, settings):
        calls.append(("score_summary", task, parent))
        return ScoreSummaryData.model_validate(score_summary())

    async def citation(session, identity, task, query, storage, settings):
        calls.append(("citation", query.model_dump(mode="json")))
        return CitationContextData.model_validate(
            {
                "parent_id": query.parent_id,
                "entry_id": query.entry_id,
                "kind": "tender",
                "verified": True,
                "document_id": uuid4(),
                "page": 1,
                "text_kind": "context",
                "window": {"text": "原", "offset": 0, "total_characters": 3, "next_offset": 1},
                "quote_start": 0,
                "quote_end": 1,
                "fix": {
                    "task_id": task,
                    "extraction_job_id": uuid4(),
                    "requirement_id": uuid4(),
                    "current_card_id": None,
                    "historical_card_revision_id": None,
                },
            }
        )

    monkeypatch.setattr(assessment_reads, "check_summary", checking)
    monkeypatch.setattr(org_console, "task_access", task_access)
    monkeypatch.setattr(assessment_reads, "check_page", check_page)
    monkeypatch.setattr(assessment_reads, "citation", citation)
    monkeypatch.setattr(assessment_rubrics, "summary", rubrics)
    monkeypatch.setattr(assessment_scores, "summary", scoring)
    app = FastAPI()
    from app.core.errors import ServiceError
    from fastapi.responses import JSONResponse

    @app.exception_handler(ServiceError)
    async def failure(request, error):
        return JSONResponse(status_code=error.status, content={"code": error.code})

    app.include_router(check_router(context, object(), object(), object(), object()))
    app.include_router(score_router(context, object(), object(), object(), object()))
    app.include_router(console_router(context, object(), object()))
    return app, calls


async def test_check_console_summary_and_page_use_only_projection_reads(assessment_interface):
    app, calls = assessment_interface
    parent = uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        summary = await client.get(f"/checks/{parent}?view=console&part=summary")
        assert summary.status_code == 200, summary.text
        assert summary.json()["items"] == []
        page = await client.get(f"/checks/{parent}?view=console&part=coverage&limit=1")
        assert page.status_code == 200, page.text
        assert page.json()["ok"] is False
        invalid = await client.get(f"/checks/{parent}?view=console&part=coverage&severity=info")
        assert invalid.status_code == 422
    assert [entry[0] for entry in calls] == ["check_summary", "check_page"]


async def test_rubric_and_unavailable_score_summaries_remain_valid_data(assessment_interface):
    app, calls = assessment_interface
    task, parent = uuid4(), uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        rubric = await client.get(f"/tasks/{task}/score-rubrics/{parent}?view=console&part=summary")
        assert rubric.status_code == 200, rubric.text
        assert rubric.json()["data"]["completeness"]["complete"] is False
        score = await client.get(f"/tasks/{task}/scores/{parent}?view=console&part=summary")
        assert score.status_code == 200, score.text
        assert score.json()["ok"] is False
        assert score.json()["data"]["estimated_total"] is None
        assert score.json()["data"]["total_status"] == "unavailable"
    assert [entry[0] for entry in calls] == ["rubric_summary", "score_summary"]


async def test_citation_query_converts_numeric_window_fields(assessment_interface):
    app, calls = assessment_interface
    task, parent, entry = uuid4(), uuid4(), uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/tasks/{task}/assessment-citation",
            params={
                "parent_kind": "check",
                "parent_id": str(parent),
                "part": "finding",
                "entry_id": str(entry),
                "limit": "1",
                "offset": "0",
                "citation_index": "0",
            },
        )
        assert response.status_code == 200, response.text
    assert calls[-1][1]["limit"] == 1
    assert UUID(calls[-1][1]["parent_id"]) == parent


@pytest.mark.parametrize(
    "path",
    [
        "/tasks/00000000-0000-0000-0000-000000000001/assessment-inputs?job=00000000-0000-0000-0000-000000000001",
        "/tasks/00000000-0000-0000-0000-000000000001/assessment-citation?parent_kind=check",
        "/tasks/00000000-0000-0000-0000-000000000001/jobs?kind=check",
        "/tasks/00000000-0000-0000-0000-000000000001/jobs?kind=score_rubric",
        "/tasks/00000000-0000-0000-0000-000000000001/jobs?kind=score",
        "/checks/00000000-0000-0000-0000-000000000001?view=console&part=summary",
        "/tasks/00000000-0000-0000-0000-000000000001/score-rubrics?view=console",
        "/tasks/00000000-0000-0000-0000-000000000001/scores?view=console",
        "/tasks/00000000-0000-0000-0000-000000000001/score-rubrics/00000000-0000-0000-0000-000000000001/decisions?view=console",
    ],
)
async def test_new_assessment_reads_require_v4_before_authentication(path, monkeypatch):
    from app.api.main import create_app
    from app.core.config import Settings
    from cryptography.fernet import Fernet

    settings = Settings(
        database_url="postgresql+psycopg://synthetic@localhost/bid_test_unused",
        encryption_key=Fernet.generate_key().decode(),
        token_key=Fernet.generate_key().decode(),
        data_dir="data/work/console-assessments-validation/unused-api-storage",
    )
    app = create_app(settings)

    def never_connect(*args, **kwargs):
        raise AssertionError("Version gate must precede any database access")

    monkeypatch.setattr(app.state.db, "transaction", never_connect)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(path)
        assert response.status_code == 404, response.text
        assert response.json()["data"]["error"]["code"] == "not_found"
        assert response.headers["X-Bid-Contract-Version"] == "3.0"


async def test_rubric_section_context_accepts_indexed_sources_only(assessment_interface):
    app, calls = assessment_interface
    task, parent, entry = uuid4(), uuid4(), uuid4()
    path = f"/tasks/{task}/assessment-citation?parent_kind=rubric&parent_id={parent}&part=rubric_section&entry_id={entry}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        indexed = await client.get(path + "&origin=sources&citation_index=1")
        assert indexed.status_code == 200, indexed.text
        assert calls[-1][1]["origin"] == "sources"
        assert calls[-1][1]["citation_index"] == 1
        for suffix in (
            "",
            "&origin=source",
            "&origin=citations",
            "&origin=sources&citation_index=-1",
        ):
            assert (await client.get(path + suffix)).status_code == 422
        item = await client.get(
            path.replace("part=rubric_section", "part=rubric_item") + "&origin=sources"
        )
        assert item.status_code == 422
    assert len(calls) == 1


@pytest.mark.parametrize(
    "suffix",
    [
        "",
        "&origin=source",
        "&origin=citations",
        "&origin=sources&citation_index=1",
        "&origin=sources&citation_index=-1",
        "&origin=sources&citation_index=invalid",
        "&origin=invalid",
        "&limit=8001",
    ],
)
async def test_citation_task_access_precedes_query_validation(
    assessment_interface, monkeypatch, suffix
):
    # Nonmembers must not reach either model-level or FastAPI field validation.
    from app.api import org_console
    from app.core.errors import not_found

    app, calls = assessment_interface
    task, parent, entry = uuid4(), uuid4(), uuid4()
    checked = []

    async def deny_task(session, identity, task_id):
        checked.append(task_id)
        raise not_found()

    monkeypatch.setattr(org_console, "task_access", deny_task)
    path = f"/tasks/{task}/assessment-citation?parent_kind=rubric&parent_id={parent}&part=rubric_section&entry_id={entry}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(path + suffix)
    assert response.status_code == 404, response.text
    assert response.json() == {"code": "not_found"}
    assert checked == [task]
    assert calls == []
