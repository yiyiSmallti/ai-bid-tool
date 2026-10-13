"""Single-user API acceptance for bounded check reads (requires isolated PostgreSQL).

Saved parents, children, citations, extraction filters and cursors must never
cross task/parent boundaries; reads must create no jobs/audits/usage and invoke no
providers. A partial report remains partial across every projection page.
"""

import json
from uuid import uuid4

import pytest
from app.schemas.console_assessments import AssessmentInputsData, CheckSummaryData, PageData
from test_check import check_case as check_case
from test_check import preview_check, process_check, row_counts, submit_check
from test_score_api import rubric_case as rubric_case
from test_score_api import rubric_counts
from test_score_api import rubric_input_case as rubric_input_case
from test_score_review import base, classify_all, confirm_contents, decision, replacement
from test_score_review import many_rubric_case as many_rubric_case
from test_score_run import counts as score_counts
from test_score_run import finish_score, preview_score, submit_score
from test_score_run import score_case as score_case


async def published(case):
    preview = await preview_check(case)
    receipt = (await submit_check(case, preview)).json()["data"]
    status = await process_check(case, receipt)
    return status["data"]["result"]["report_id"]


@pytest.mark.parametrize("part", ["summary", "findings", "coverage", "certificates", "notices"])
async def test_each_check_projection_is_bounded_and_read_only(check_case, database, part):
    case = check_case
    report = await published(case)
    before = await row_counts(case["app"])
    calls = case["provider"].calls
    path = f"/v4/checks/{report}?view=console&part={part}&limit=1"
    response = await case["api"].get(path, headers=case["header"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    if part == "summary":
        summary = CheckSummaryData.model_validate(body["data"])
        assert sum(group.count for group in summary.groups) == summary.finding_count
        assert body["items"] == []
    else:
        page = PageData.model_validate(body["data"])
        assert page.returned == len(body["items"]) <= 1
        assert page.filtered_total >= page.returned
    assert await row_counts(case["app"]) == before
    assert case["provider"].calls == calls


async def test_input_jobs_history_and_citations_hide_foreign_ids(check_case, database):
    case = check_case
    report = await published(case)
    task = case["task"]
    extraction = case["draft"]["extraction_job_id"]
    before = await row_counts(case["app"])
    calls = case["provider"].calls
    paths = [
        f"/v4/tasks/{task}/assessment-inputs?job={extraction}",
        f"/v4/tasks/{task}/jobs?kind=check&extraction_job_id={extraction}&limit=1",
        f"/v4/tasks/{task}/checks?view=console&extraction_job_id={extraction}&limit=1",
    ]
    for path in paths:
        response = await case["api"].get(path, headers=case["header"])
        assert response.status_code == 200, response.text
    inputs = (await case["api"].get(paths[0], headers=case["header"])).json()
    data = AssessmentInputsData.model_validate(inputs["data"])
    assert data.current_draft is not None
    assert str(data.task_id) == task
    coverage = (
        await case["api"].get(
            f"/v4/checks/{report}?view=console&part=coverage&limit=1", headers=case["header"]
        )
    ).json()
    entry = coverage["items"][0]
    path = f"/v4/tasks/{task}/assessment-citation?parent_kind=check&parent_id={report}&part=coverage&entry_id={entry['id']}&limit=1"
    cited = await case["api"].get(path, headers=case["header"])
    assert cited.status_code == 200, cited.text
    assert cited.json()["data"]["verified"] is True
    assert len(cited.json()["data"]["window"]["text"]) <= 1
    wrong_entry = await case["api"].get(
        path.replace(entry["id"], str(uuid4())), headers=case["header"]
    )
    assert wrong_entry.status_code == 404
    assert await row_counts(case["app"]) == before
    assert case["provider"].calls == calls


async def test_check_keyset_does_not_fetch_full_show_and_cursors_bind_filters(
    check_case, monkeypatch
):
    from app.services import check

    async def forbidden_full_show(*args, **kwargs):
        raise AssertionError("Console must never fetch full show")

    case = check_case
    report = await published(case)
    monkeypatch.setattr(check, "show_check", forbidden_full_show)
    path = f"/v4/checks/{report}?view=console&part=coverage&limit=1"
    first = (await case["api"].get(path, headers=case["header"])).json()
    seen = {first["items"][0]["id"]}
    cursor = first["data"]["next_cursor"]
    assert cursor
    replay = await case["api"].get(
        f"/v4/checks/{report}?view=console&part=findings&cursor={cursor}", headers=case["header"]
    )
    assert replay.status_code == 400
    assert replay.json()["data"]["error"]["code"] == "invalid_cursor"
    while cursor:
        response = await case["api"].get(path + "&cursor=" + cursor, headers=case["header"])
        assert response.status_code == 200, response.text
        body = response.json()
        for row in body["items"]:
            assert row["id"] not in seen
            seen.add(row["id"])
        cursor = body["data"]["next_cursor"]
    assert len(seen) == first["data"]["total"]
    assert len(json.dumps(first).encode()) < 2 * 1024 * 1024


@pytest.mark.parametrize(
    "part", ["summary", "sections", "items", "coverage", "blockers", "replacement"]
)
async def test_each_rubric_projection_and_replacement_isolated(rubric_case, part):
    case = rubric_case
    old = case["rubric"]
    if part == "replacement":
        revised = await case["api"].post(
            "/v4" + base(case) + "/revisions?view=console",
            headers=case["header"],
            json=replacement(old),
        )
        assert revised.status_code == 200, revised.text
        identifier = revised.json()["data"]["id"]
    else:
        identifier = old["rubric"]["id"]
    before = await rubric_counts(case)
    provider_calls = len(case["vendor"].requests)
    path = f"/v4/tasks/{case['task']}/score-rubrics/{identifier}?view=console&part={part}&limit=1"
    result = await case["api"].get(path, headers=case["header"])
    assert result.status_code == 200, result.text
    if part not in {"summary", "replacement"}:
        data = PageData.model_validate(result.json()["data"])
        assert len(result.json()["items"]) == data.returned <= 1
    assert await rubric_counts(case) == before
    assert len(case["vendor"].requests) == provider_calls


async def test_rubric_compact_receipts_match_authoritative_review(rubric_case):
    from app.schemas.console_assessments import RubricSummaryData

    case = rubric_case
    complete = await confirm_contents(case)
    result = await case["api"].post(
        "/v4" + base(case) + "/decisions?view=console",
        headers=case["header"],
        json=decision(complete, complete["rubric"]),
    )
    assert result.status_code == 200, result.text
    summary = RubricSummaryData.model_validate(result.json()["data"])
    assert summary.state == "confirmed" and summary.completeness.complete
    assert "sections" not in result.json()["data"]
    reopened = await case["api"].post(
        "/v4" + base(case) + "/decisions?view=console",
        headers=case["header"],
        json={
            "expected_revision": summary.revision,
            "expected_input_hash": summary.input_hash,
            "action": "reopen",
            "reason": "Synthetic review correction",
        },
    )
    assert reopened.status_code == 200, reopened.text
    saved = (await case["api"].get(base(case), headers=case["header"])).json()["data"]
    revised = await case["api"].post(
        "/v4" + base(case) + "/revisions?view=console",
        headers=case["header"],
        json=replacement(saved),
    )
    assert revised.status_code == 200, revised.text
    replacement_summary = RubricSummaryData.model_validate(revised.json()["data"])
    assert replacement_summary.prior_rubric_id == summary.id
    assert replacement_summary.state == "candidate"
    assert replacement_summary.completeness.unconfirmed_items == replacement_summary.item_count


async def test_rubric_and_score_source_windows_bind_exact_parent_graph(score_case):
    case = score_case
    rubric = case["rubric"]["rubric"]["id"]
    for part, collection in (
        ("rubric_section", "sections"),
        ("rubric_item", "items"),
        ("coverage", "coverage"),
    ):
        row = case["rubric"][collection][0]
        origin = "sources" if part == "rubric_section" else "source"
        path = f"/v4/tasks/{case['task']}/assessment-citation?parent_kind=rubric&parent_id={rubric}&part={part}&entry_id={row['id']}&origin={origin}&limit=1"
        response = await case["api"].get(path, headers=case["header"])
        assert response.status_code == 200, response.text
        assert response.json()["data"]["verified"]
    preview = await preview_score(case)
    receipt = (await submit_score(case, preview)).json()["data"]
    terminal = await finish_score(case, receipt)
    report = terminal["result"]["report_id"]
    full = (
        await case["api"].get(f"/v4/tasks/{case['task']}/scores/{report}", headers=case["header"])
    ).json()["data"]
    row = full["items"][0]
    for index, citation in enumerate(row["citations"]):
        path = f"/v4/tasks/{case['task']}/assessment-citation?parent_kind=score&parent_id={report}&part=score_item&entry_id={row['id']}&origin=citations&citation_index={index}&limit=1"
        response = await case["api"].get(path, headers=case["header"])
        assert response.status_code == 200, response.text
        assert response.json()["data"]["kind"] == citation["kind"]
    from sqlalchemy import text
    from test_response_cards import require_action

    await require_action(
        case["api"],
        case["header"],
        case["support_card"],
        "reopen",
        reason="Synthetic support review reopened to exercise historical source authorization",
    )
    report_path = f"/v4/tasks/{case['task']}/scores/{report}"
    stale = await case["api"].get(report_path, headers=case["header"])
    assert stale.status_code == 200, stale.text
    assert stale.json()["data"]["report"]["validity"] == "stale"
    section = case["rubric"]["sections"][0]
    sources = [*section["sources"], {**section["sources"][0], "requirement_id": str(uuid4())}]
    with case["admin_engine"].begin() as connection:
        connection.execute(
            text("ALTER TABLE score_rubric_sections DISABLE TRIGGER rubric_immutable")
        )
        connection.execute(
            text("UPDATE score_rubric_sections SET sources=CAST(:sources AS jsonb) WHERE id=:id"),
            {"sources": json.dumps(sources), "id": section["id"]},
        )
        connection.execute(
            text("ALTER TABLE score_rubric_sections ENABLE TRIGGER rubric_immutable")
        )
    for suffix in (
        "",
        "?view=console&part=summary",
        "?view=console&part=sections",
        "?view=console&part=items",
    ):
        denied = await case["api"].get(report_path + suffix, headers=case["header"])
        assert denied.status_code == 404, denied.text
    (case["tmp_path"] / "stale-score-section-authorization.json").write_text(
        json.dumps(
            {"stale_read": stale.json(), "missing_section_source_status": 404},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    "kind,collection", [("score_rubric", "score-rubrics"), ("score", "scores")]
)
async def test_job_and_history_collection_each_kind_fail_closed(score_case, kind, collection):
    case = score_case
    if kind == "score":
        preview = await preview_score(case)
        receipt = (await submit_score(case, preview)).json()["data"]
        await finish_score(case, receipt)
    paths = [
        f"/v4/tasks/{case['task']}/jobs?kind={kind}&extraction_job_id={case['extraction']}&limit=1",
        f"/v4/tasks/{case['task']}/{collection}?view=console&extraction_job_id={case['extraction']}&limit=1",
    ]
    before = await score_counts(case)
    for path in paths:
        allowed = await case["api"].get(path, headers=case["header"])
        assert allowed.status_code == 200, allowed.text
        assert len(allowed.json()["items"]) == 1
        assert (
            await case["api"].get(
                path.replace(case["extraction"], str(uuid4())), headers=case["header"]
            )
        ).status_code == 404
    assert await score_counts(case) == before


async def test_check_cursor_snapshot_changes_require_reload(check_case, database, admin_engine):
    case = check_case
    report = await published(case)
    path = f"/v4/checks/{report}?view=console&part=findings&limit=1"
    first = (await case["api"].get(path, headers=case["header"])).json()
    cursor = first["data"]["next_cursor"]
    assert cursor
    unchanged_cursor = await case["api"].get(path + "&cursor=" + cursor, headers=case["header"])
    assert unchanged_cursor.status_code == 200, unchanged_cursor.text
    full = (await case["api"].get(f"/checks/{report}", headers=case["header"])).json()
    finding = next(row for row in full["items"] if row["review_domain"] == "commercial")
    response = await case["api"].post(
        f"/checks/{report}/findings/{finding['id']}/decisions",
        headers=case["header"],
        json={
            "action": "dismiss",
            "reason": "Synthetic reviewed risk",
            "expected_revision": finding["revision"],
            "expected_input_hash": full["data"]["report"]["input"]["input_hash"],
        },
    )
    assert response.status_code == 200, response.text
    changed = await case["api"].get(path + "&cursor=" + cursor, headers=case["header"])
    assert changed.status_code == 409
    assert changed.json()["data"]["error"]["code"] == "assessment_view_changed"


@pytest.mark.parametrize("field", ["entry_id", "requirement_id"])
async def test_rubric_blocker_filters_cannot_name_objects_outside_parent(rubric_case, field):
    """A foreign/missing subject filter must not return an authorized parent's counts."""
    case = rubric_case
    missing = str(uuid4())
    response = await case["api"].get(
        "/v4" + base(case),
        params={"view": "console", "part": "blockers", field: missing},
        headers=case["header"],
    )
    assert response.status_code == 404, response.text
    assert "total" not in response.json()["data"]


async def test_section_sources_projection_and_indexed_context_bind_pinned_graph(many_rubric_case):
    case = many_rubric_case
    reviewed = await classify_all(case)
    proposal = replacement(reviewed)
    selected = [
        {"requirement_id": row["requirement_id"], "quote": row["source"]["quote"]}
        for row in reviewed["coverage"][:2]
    ]
    assert len(selected) == 2
    proposal["sections"][0]["sources"] = selected
    revised = await case["api"].post(
        "/v4" + base(case) + "/revisions?view=console", headers=case["header"], json=proposal
    )
    assert revised.status_code == 200, revised.text
    parent = revised.json()["data"]["id"]
    page_path = (
        f"/v4/tasks/{case['task']}/score-rubrics/{parent}?view=console&part=sections&limit=1"
    )
    page = await case["api"].get(page_path, headers=case["header"])
    assert page.status_code == 200, page.text
    section = page.json()["items"][0]
    assert [
        dict(requirement_id=row["requirement_id"], quote=row["quote"]) for row in section["sources"]
    ] == selected
    assert "source" not in section and "requirement_id" not in section
    for index, saved in enumerate(selected):
        path = f"/v4/tasks/{case['task']}/assessment-citation?parent_kind=rubric&parent_id={parent}&part=rubric_section&entry_id={section['id']}&origin=sources&citation_index={index}&text=quote"
        response = await case["api"].get(path, headers=case["header"])
        assert response.status_code == 200, response.text
        assert response.json()["data"]["window"]["text"] == saved["quote"]
        assert response.json()["data"]["fix"]["requirement_id"] == saved["requirement_id"]
    invalid = await case["api"].get(
        path.replace("citation_index=1", "citation_index=2"), headers=case["header"]
    )
    assert invalid.status_code == 404
    missing_entry = await case["api"].get(
        path.replace(section["id"], str(uuid4())), headers=case["header"]
    )
    assert missing_entry.status_code == 404
    artifact = case["tmp_path"] / "console-section-sources.json"
    artifact.write_text(
        json.dumps(
            {
                "projection": page.json(),
                "selectors": selected,
                "verified_contexts": len(selected),
                "foreign_org_status": 404,
                "out_of_range_status": invalid.status_code,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
