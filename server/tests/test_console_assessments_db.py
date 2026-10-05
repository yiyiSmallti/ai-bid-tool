"""Two-org API acceptance for bounded check reads (requires isolated PostgreSQL).

Saved parents, children, citations, extraction filters and cursors must never
cross org/task boundaries; reads must create no jobs/audits/usage and invoke no
providers. A partial report remains partial across every projection page.
"""

import json
from uuid import uuid4

import pytest
from app.schemas.console_assessments import AssessmentInputsData, CheckSummaryData, PageData
from test_check import check_case as check_case
from test_check import preview_check, process_check, row_counts, submit_check


async def published(case):
    preview = await preview_check(case)
    receipt = (await submit_check(case, preview)).json()["data"]
    status = await process_check(case, receipt)
    return status["data"]["result"]["report_id"]


@pytest.mark.parametrize("part", ["summary", "findings", "coverage", "certificates", "notices"])
async def test_each_check_projection_is_bounded_and_org_isolated(check_case, tenants, part):
    case = check_case
    report = await published(case)
    before = await row_counts(case["app"], tenants["orgs"][0])
    calls = case["provider"].calls
    path = f"/v4/checks/{report}?view=console&part={part}&limit=1"
    response = await case["api"].get(path, headers=case["header"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False  # The seeded rules report is partial.
    if part == "summary":
        summary = CheckSummaryData.model_validate(body["data"])
        assert sum(group.count for group in summary.groups) == summary.finding_count
        assert body["items"] == []
    else:
        page = PageData.model_validate(body["data"])
        assert page.returned == len(body["items"]) <= 1
        assert page.filtered_total >= page.returned
    denied = await case["api"].get(path, headers=case["headers"][1])
    missing = await case["api"].get(
        f"/v4/checks/{uuid4()}?view=console&part={part}", headers=case["headers"][1]
    )
    assert denied.status_code == missing.status_code == 404
    assert denied.json()["data"]["error"] == missing.json()["data"]["error"]
    assert report not in denied.text
    assert await row_counts(case["app"], tenants["orgs"][0]) == before
    assert case["provider"].calls == calls


async def test_input_jobs_history_and_citations_hide_foreign_ids(check_case, tenants):
    case = check_case
    report = await published(case)
    task = case["task"]
    extraction = case["draft"]["extraction_job_id"]
    before = await row_counts(case["app"], tenants["orgs"][0])
    calls = case["provider"].calls
    paths = [
        f"/v4/tasks/{task}/assessment-inputs?job={extraction}",
        f"/v4/tasks/{task}/jobs?kind=check&extraction_job_id={extraction}&limit=1",
        f"/v4/tasks/{task}/checks?view=console&extraction_job_id={extraction}&limit=1",
    ]
    for path in paths:
        response = await case["api"].get(path, headers=case["header"])
        assert response.status_code == 200, response.text
        denied = await case["api"].get(path, headers=case["headers"][1])
        assert denied.status_code == 404, denied.text
        assert task not in denied.text and extraction not in denied.text
    inputs = (await case["api"].get(paths[0], headers=case["header"])).json()
    data = AssessmentInputsData.model_validate(inputs["data"])
    assert data.current_draft is not None
    assert data.task_budget.task_id == data.task_id
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
    assert (await case["api"].get(path, headers=case["headers"][1])).status_code == 404
    wrong_entry = await case["api"].get(
        path.replace(entry["id"], str(uuid4())), headers=case["header"]
    )
    assert wrong_entry.status_code == 404
    assert await row_counts(case["app"], tenants["orgs"][0]) == before
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


# These fixtures seed both orgs through the existing real-service acceptance
# paths. They are deliberately not a SQLite substitute for tenant RLS.
from test_score_api import rubric_case as rubric_case  # noqa: E402
from test_score_api import rubric_counts  # noqa: E402
from test_score_api import rubric_input_case as rubric_input_case  # noqa: E402
from test_score_review import base, confirm_contents, decision, replacement  # noqa: E402
from test_score_run import counts as score_counts  # noqa: E402
from test_score_run import finish_score, preview_score, submit_score  # noqa: E402
from test_score_run import score_case as score_case  # noqa: E402


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
    denied = await case["api"].get(path, headers=case["headers"][1])
    assert denied.status_code == 404, denied.text
    assert identifier not in denied.text
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


@pytest.mark.parametrize("part", ["summary", "sections", "items", "notices"])
async def test_each_score_projection_preserves_nulls_and_org_isolation(score_case, part):
    case = score_case
    preview = await preview_score(case)
    receipt = (await submit_score(case, preview)).json()["data"]
    terminal = await finish_score(case, receipt)
    identifier = terminal["result"]["report_id"]
    before = await score_counts(case)
    provider_calls = len(case["score_vendor"].requests)
    path = f"/v4/tasks/{case['task']}/scores/{identifier}?view=console&part={part}&limit=1"
    result = await case["api"].get(path, headers=case["header"])
    assert result.status_code == 200, result.text
    if part == "summary":
        from app.schemas.console_assessments import ScoreSummaryData

        summary = ScoreSummaryData.model_validate(result.json()["data"])
        assert (summary.total_status == "estimated") == (summary.estimated_total is not None)
    else:
        data = PageData.model_validate(result.json()["data"])
        assert data.returned == len(result.json()["items"]) <= 1
    denied = await case["api"].get(path, headers=case["headers"][1])
    assert denied.status_code == 404, denied.text
    assert identifier not in denied.text
    assert await score_counts(case) == before
    assert len(case["score_vendor"].requests) == provider_calls


async def test_rubric_and_score_source_windows_bind_exact_parent_graph(score_case):
    case = score_case
    rubric = case["rubric"]["rubric"]["id"]
    for part, collection in (
        ("rubric_section", "sections"),
        ("rubric_item", "items"),
        ("coverage", "coverage"),
    ):
        row = case["rubric"][collection][0]
        path = f"/v4/tasks/{case['task']}/assessment-citation?parent_kind=rubric&parent_id={rubric}&part={part}&entry_id={row['id']}&limit=1"
        response = await case["api"].get(path, headers=case["header"])
        assert response.status_code == 200, response.text
        assert response.json()["data"]["verified"]
        assert (await case["api"].get(path, headers=case["headers"][1])).status_code == 404
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
        assert (await case["api"].get(path, headers=case["headers"][1])).status_code == 404


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
        denied = await case["api"].get(path, headers=case["headers"][1])
        assert denied.status_code == 404, denied.text
        assert case["task"] not in denied.text
        assert (
            await case["api"].get(
                path.replace(case["extraction"], str(uuid4())), headers=case["header"]
            )
        ).status_code == 404
    assert await score_counts(case) == before


@pytest.mark.parametrize("operation", ["decisions", "revisions"])
async def test_compact_mutation_routes_hide_foreign_parent(rubric_case, operation):
    case = rubric_case
    report = case["rubric"]
    body = decision(report, report["rubric"]) if operation == "decisions" else replacement(report)
    before = await rubric_counts(case)
    response = await case["api"].post(
        "/v4" + base(case) + f"/{operation}?view=console", headers=case["headers"][1], json=body
    )
    assert response.status_code == 404, response.text
    assert report["rubric"]["id"] not in response.text
    missing = await case["api"].post(
        f"/v4/tasks/{case['task']}/score-rubrics/{uuid4()}/{operation}?view=console",
        headers=case["headers"][1],
        json=body,
    )
    assert missing.status_code == 404
    assert missing.json()["data"]["error"] == response.json()["data"]["error"]
    assert await rubric_counts(case) == before


async def test_check_cursor_snapshot_and_principal_change_require_reload(
    check_case, tenants, admin_engine
):
    from test_response_cards import set_role

    case = check_case
    set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "bidder")
    report = await published(case)
    path = f"/v4/checks/{report}?view=console&part=findings&limit=1"
    first = (await case["api"].get(path, headers=case["header"])).json()
    cursor = first["data"]["next_cursor"]
    assert cursor
    set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "viewer")
    denied_cursor = await case["api"].get(path + "&cursor=" + cursor, headers=case["header"])
    assert denied_cursor.status_code == 400
    assert denied_cursor.json()["data"]["error"]["code"] == "invalid_cursor"
    set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], "bidder")
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
    assert missing not in response.text
    other_org = await case["api"].get(
        "/v4" + base(case),
        params={"view": "console", "part": "blockers", field: missing},
        headers=case["headers"][1],
    )
    assert other_org.status_code == 404
    assert response.json()["data"]["error"] == other_org.json()["data"]["error"]
