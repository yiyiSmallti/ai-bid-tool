"""Real DB/API acceptance for console assessment task authority.

Failure inventory precedes implementation in this module: same-org nonmembers
leak parent counts/citations/history; observers and org viewers make decisions;
org roles imply unassigned task domains; admin recovery becomes review authority;
archival admits previews or writes; tokens/workers inherit human grants or hide
behind actor_kind=session. Rejected actions must leave saved state and vendors
unchanged. All task grants use the production membership service, with synthetic
users, documents and fake Providers. Requires an isolated PostgreSQL runtime.
"""

from hashlib import sha256
from uuid import UUID

import pytest
from app.core.errors import ServiceError
from app.models.entities import ApiToken, AuditLog
from app.models.score import (
    ScoreRubricClassification,
    ScoreRubricCoverageDecision,
    ScoreRubricCoverageItem,
    ScoreRubricDecision,
    ScoreRubricRevisionEvent,
)
from app.services.auth import ROLE_SCOPES, Identity
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from task_fixtures import add_member
from test_card_generation import token_header
from test_check import check_case as check_case
from test_check import row_counts
from test_console_assessments_db import published
from test_score_api import rubric_case as rubric_case
from test_score_api import rubric_counts
from test_score_api import rubric_input_case as rubric_input_case
from test_score_review import base, classify_all, decision, replacement, show
from test_score_run import counts as score_counts
from test_score_run import finish_score, preview_score, submit_score
from test_score_run import score_case as score_case
from test_team_workflow_membership import person, workflow


async def participant(case, admin_engine, *, org_role="bidder", task_role=None, domains=()):
    org = UUID(case["header"]["X-Org-Id"])
    user, header = await person(case["api"], admin_engine, org, org_role)
    if task_role is not None:
        with Session(admin_engine) as session, session.begin():
            add_member(
                session,
                org,
                UUID(case["task"]),
                user,
                role=task_role,
                review_domains=list(domains),
            )
    return user, header


async def hidden_reads(case, paths, outsider):
    for header in (outsider, case["headers"][1]):
        for path in paths:
            denied = await case["api"].get(path, headers=header)
            assert denied.status_code == 404, (path, denied.text)
            assert denied.json()["data"]["error"]["code"] == "not_found"
            assert case["task"] not in denied.text


async def post_error(case, path, body, header, *, code="forbidden", status=403):
    response = await case["api"].post(path, headers=header, json=body)
    assert response.status_code == status, (path, response.text)
    assert response.json()["data"]["error"]["code"] == code
    return response


async def rubric_business_counts(case):
    counts = await rubric_counts(case)
    counts.pop("audit_logs")
    async with case["app"].state.db.transaction(UUID(case["header"]["X-Org-Id"])) as session:
        for model in (
            ScoreRubricClassification,
            ScoreRubricCoverageDecision,
            ScoreRubricCoverageItem,
            ScoreRubricDecision,
            ScoreRubricRevisionEvent,
        ):
            counts[model.__tablename__] = await session.scalar(
                select(func.count()).select_from(model)
            )
    return counts


async def rubric_denial(
    case, report, path, body, header, user, *, code="forbidden", status=403, actor_kind="session"
):
    """Preserve business state and distinguish its hashed denial from token invocation audit."""
    before = await rubric_business_counts(case)
    org = UUID(header["X-Org-Id"])
    async with case["app"].state.db.transaction(org) as session:
        audit_ids = set(await session.scalars(select(AuditLog.id)))
        token_id = (
            await session.scalar(select(ApiToken.id).where(ApiToken.user_id == user))
            if actor_kind == "token"
            else None
        )
        if actor_kind == "token":
            assert token_id is not None
    response = await post_error(case, path, body, header, code=code, status=status)
    command = response.json()["command"]
    assert await rubric_business_counts(case) == before
    async with case["app"].state.db.transaction(org) as session:
        added = list(await session.scalars(select(AuditLog).where(AuditLog.id.not_in(audit_ids))))
        expected_actions = ["score_rubric.decision_denied"]
        if actor_kind == "token":
            # The failed request rolls back command.invoked; A02 retains a
            # separate command.failed receipt alongside the business denial.
            expected_actions.append("command.failed")
        assert sorted(entry.action for entry in added) == sorted(expected_actions)
        for entry in added:
            assert entry.org_id == org
            assert entry.actor_user_id == user
            assert entry.actor_token_id == token_id
            assert entry.actor_kind == actor_kind
            assert entry.command == command
            assert entry.invocation_id is not None
            assert entry.initiated_by == ("external_agent" if token_id else "human")
            assert entry.on_behalf_of_user_id == user
            assert entry.agent_principal_id is None
            assert entry.agent_session_id is None
            assert entry.agent_step_id is None
            assert entry.job_id is None
            assert entry.run_id is None
        entry = next(entry for entry in added if entry.action == "score_rubric.decision_denied")
        assert entry.object_id == UUID(report["rubric"]["id"])
        assert entry.details == {
            "task_id": case["task"],
            "rubric_id": report["rubric"]["id"],
            "input_hash": report["rubric"]["input_hash"],
            "reason_sha256": sha256(body["reason"].encode()).hexdigest(),
            "revision": body["expected_revision"],
            "error_code": code,
            "actor_kind": actor_kind,
        }
        if actor_kind == "token":
            invocation = next(entry for entry in added if entry.action == "command.failed")
            assert invocation.invocation_id == entry.invocation_id
            assert invocation.object_id == invocation.invocation_id
            assert invocation.details == {"command": command, "reason_code": code}


def rubric_writes(case, report):
    parent = "/v4" + base(case, report)
    section, item, coverage = (report[key][0] for key in ("sections", "items", "coverage"))
    classify = decision(report, section)
    classify.pop("action")
    classify["review_domain"] = "technical"
    return [
        (parent + "/revisions?view=console", replacement(report)),
        (parent + f"/sections/{section['id']}/classification", classify),
        (
            parent + f"/items/{item['id']}/classification",
            {
                **classify,
                "expected_revision": item["revision"],
            },
        ),
        (parent + f"/sections/{section['id']}/decisions", decision(report, section)),
        (parent + f"/items/{item['id']}/decisions", decision(report, item)),
        (
            parent + f"/coverage/{coverage['requirement_id']}/decisions",
            decision(
                report,
                coverage,
                "mapped",
                rubric_item_ids=[item["id"]],
            ),
        ),
        (parent + "/decisions?view=console", decision(report, report["rubric"])),
    ]


async def test_check_console_all_parts_and_citation_hide_nonmembers(check_case, admin_engine):
    case = check_case
    report = await published(case)
    _, outsider = await participant(case, admin_engine)
    paths = [
        f"/v4/checks/{report}?view=console&part={part}&limit=1"
        for part in ("summary", "findings", "coverage", "certificates", "notices")
    ]
    coverage = (await case["api"].get(paths[2], headers=case["header"])).json()["items"][0]
    paths += [
        f"/v4/tasks/{case['task']}/assessment-inputs?job={case['extraction']}",
        f"/v4/tasks/{case['task']}/jobs?kind=check&extraction_job_id={case['extraction']}",
        f"/v4/tasks/{case['task']}/checks?view=console&extraction_job_id={case['extraction']}",
        f"/v4/tasks/{case['task']}/assessment-citation?parent_kind=check&parent_id={report}"
        f"&part=coverage&entry_id={coverage['id']}",
    ]
    before = await row_counts(case["app"], UUID(case["header"]["X-Org-Id"]))
    calls = case["provider"].calls
    await hidden_reads(case, paths, outsider)
    assert await row_counts(case["app"], UUID(case["header"]["X-Org-Id"])) == before
    assert case["provider"].calls == calls


async def test_rubric_console_replacement_and_history_hide_nonmembers(rubric_case, admin_engine):
    case = rubric_case
    old = case["rubric"]
    revised = await case["api"].post(
        "/v4" + base(case) + "/revisions?view=console",
        headers=case["header"],
        json=replacement(old),
    )
    assert revised.status_code == 200, revised.text
    new = revised.json()["data"]["id"]
    _, outsider = await participant(case, admin_engine)
    paths = [
        f"/v4/tasks/{case['task']}/score-rubrics/{new}?view=console&part={part}&limit=1"
        for part in ("summary", "sections", "items", "coverage", "blockers", "replacement")
    ]
    for part, rows in (
        ("rubric_section", "sections"),
        ("rubric_item", "items"),
        ("coverage", "coverage"),
    ):
        paths.append(
            f"/v4/tasks/{case['task']}/assessment-citation?parent_kind=rubric"
            f"&parent_id={old['rubric']['id']}&part={part}&entry_id={old[rows][0]['id']}"
        )
    paths += [
        f"/v4/tasks/{case['task']}/score-rubrics?view=console&extraction_job_id={case['extraction']}",
        "/v4" + base(case) + "/history",
        f"/v4/tasks/{case['task']}/jobs?kind=score_rubric&extraction_job_id={case['extraction']}",
        f"/v4/tasks/{case['task']}/assessment-inputs?job={case['extraction']}",
    ]
    before = await rubric_counts(case)
    calls = len(case["vendor"].requests)
    await hidden_reads(case, paths, outsider)
    for path, body in rubric_writes(case, old):
        for header in (outsider, case["headers"][1]):
            await post_error(case, path, body, header, code="not_found", status=404)
    assert await rubric_counts(case) == before
    assert len(case["vendor"].requests) == calls


async def test_score_console_all_parts_history_jobs_and_citations_hide_nonmembers(
    score_case, admin_engine
):
    case = score_case
    preview = await preview_score(case)
    receipt = await submit_score(case, preview)
    assert receipt.status_code == 200, receipt.text
    terminal = await finish_score(case, receipt.json()["data"])
    report = terminal["result"]["report_id"]
    _, outsider = await participant(case, admin_engine)
    paths = [
        f"/v4/tasks/{case['task']}/scores/{report}?view=console&part={part}&limit=1"
        for part in ("summary", "sections", "items", "notices")
    ]
    paths += [
        f"/v4/tasks/{case['task']}/scores?view=console&extraction_job_id={case['extraction']}",
        f"/v4/tasks/{case['task']}/jobs?kind=score&extraction_job_id={case['extraction']}",
    ]
    full = (
        await case["api"].get(
            f"/v4/tasks/{case['task']}/scores/{report}",
            headers=case["header"],
        )
    ).json()["data"]
    citation_count = 0
    for row in full["items"]:
        for index, _ in enumerate(row["citations"]):
            citation_count += 1
            paths.append(
                f"/v4/tasks/{case['task']}/assessment-citation?parent_kind=score&parent_id={report}"
                f"&part=score_item&entry_id={row['id']}&origin=citations&citation_index={index}"
            )
    assert citation_count > 0
    before = await score_counts(case)
    calls = len(case["score_vendor"].requests)
    await hidden_reads(case, paths, outsider)
    assert await score_counts(case) == before
    assert len(case["score_vendor"].requests) == calls


@pytest.mark.parametrize(
    "org_role,task_role,domains",
    [
        ("bidder", "observer", []),
        ("viewer", "observer", []),
    ],
)
async def test_observer_and_org_viewer_cannot_write_rubric(
    rubric_case,
    admin_engine,
    org_role,
    task_role,
    domains,
):
    case = rubric_case
    user, header = await participant(
        case,
        admin_engine,
        org_role=org_role,
        task_role=task_role,
        domains=domains,
    )
    allowed = await case["api"].get("/v4" + base(case) + "?view=console", headers=header)
    assert allowed.status_code == 200, allowed.text
    for path, body in rubric_writes(case, case["rubric"]):
        await rubric_denial(case, case["rubric"], path, body, header, user)


async def test_technical_review_needs_task_domain_and_org_role_intersection(
    rubric_case, admin_engine
):
    case = rubric_case
    report = await classify_all(case, domain="technical")
    user, header = await participant(
        case,
        admin_engine,
        org_role="technical",
        task_role="contributor",
    )
    row = report["items"][0]
    path = "/v4" + base(case) + f"/items/{row['id']}/decisions"
    await rubric_denial(case, report, path, decision(report, row), header, user)
    with Session(admin_engine) as session, session.begin():
        add_member(
            session,
            UUID(header["X-Org-Id"]),
            UUID(case["task"]),
            user,
            role="contributor",
            review_domains=["technical"],
        )
    accepted = await case["api"].post(path, headers=header, json=decision(report, row))
    assert accepted.status_code == 200, accepted.text
    admin_user, admin = await participant(
        case, admin_engine, org_role="admin", task_role="contributor"
    )
    # Re-read after the accepted item decision so later denials use current revisions.
    report = await show(case)
    section = report["sections"][0]
    await rubric_denial(
        case,
        report,
        "/v4" + base(case) + f"/sections/{section['id']}/decisions",
        decision(report, section),
        admin,
        admin_user,
    )
    commercial_user, commercial = await participant(
        case,
        admin_engine,
        org_role="bidder",
        task_role="reviewer",
        domains=["commercial"],
    )
    await rubric_denial(
        case,
        report,
        "/v4" + base(case) + f"/sections/{section['id']}/decisions",
        decision(report, section),
        commercial,
        commercial_user,
    )


async def test_archived_task_blocks_rubric_mutations_but_retains_console_reads(
    rubric_case, admin_engine
):
    case = rubric_case
    revised = await case["api"].post(
        base(case) + "/revisions",
        headers=case["header"],
        json=replacement(case["rubric"]),
    )
    assert revised.status_code == 200, revised.text
    case["rubric"] = revised.json()["data"]
    report = await classify_all(case)
    case["rubric"] = report
    admin_user, admin = await participant(
        case, admin_engine, org_role="admin", task_role="contributor"
    )
    current = await workflow(case["api"], admin, case["task"])
    archived = await case["api"].post(
        f"/tasks/{case['task']}/archive",
        headers=admin,
        json={"expected_revision": current["revision"], "reason": "Synthetic console archive"},
    )
    assert archived.status_code == 200, archived.text
    before = await rubric_business_counts(case)
    for path, body in rubric_writes(case, report):
        classification = path.endswith("/classification")
        header = admin if classification else case["header"]
        user = admin_user if classification else case["tenants"]["users"][0]
        await rubric_denial(
            case, report, path, body, header, user, code="task_archived", status=409
        )
    for part in ("summary", "sections", "items", "coverage", "blockers", "replacement"):
        response = await case["api"].get(
            "/v4" + base(case) + f"?view=console&part={part}",
            headers=case["header"],
        )
        assert response.status_code == 200, response.text
    assert await rubric_business_counts(case) == before


async def test_archived_task_blocks_score_preview_and_submission(score_case, admin_engine):
    case = score_case
    preview = await preview_score(case)
    _, admin = await participant(case, admin_engine, org_role="admin")
    current = await workflow(case["api"], admin, case["task"])
    archived = await case["api"].post(
        f"/tasks/{case['task']}/archive",
        headers=admin,
        json={"expected_revision": current["revision"], "reason": "Synthetic score archive"},
    )
    assert archived.status_code == 200, archived.text
    body = {
        "draft_id": case["draft_id"],
        "rubric_id": case["rubric"]["rubric"]["id"],
        "assessment_date": "2026-10-05",
    }
    before = await score_counts(case)
    await post_error(
        case,
        f"/v4/tasks/{case['task']}/scores/preview",
        {**body, "dry_run": True},
        case["header"],
        code="task_archived",
        status=409,
    )
    await post_error(
        case,
        f"/v4/tasks/{case['task']}/scores",
        {**body, "expected_input_hash": preview["input"]["input_hash"]},
        case["header"],
        code="task_archived",
        status=409,
    )
    assert await score_counts(case) == before
    assert not case["score_vendor"].requests


async def test_token_and_worker_rubric_writes_keep_human_error_precedence(
    rubric_case, admin_engine
):
    from app.services import score

    case = rubric_case
    user, admin = await participant(case, admin_engine, org_role="admin", task_role="contributor")
    token = await token_header(case["api"], admin, scopes=["task:read", "score:read"])
    for path, body in rubric_writes(case, case["rubric"]):
        await rubric_denial(
            case,
            case["rubric"],
            path,
            body,
            token,
            user,
            code="human_session_required",
            actor_kind="token",
        )
    org = UUID(admin["X-Org-Id"])
    async with case["app"].state.db.transaction(org) as session:
        token_row = await session.scalar(select(ApiToken).where(ApiToken.user_id == user))
        assert token_row is not None
        actors = [
            Identity(user, org, set(ROLE_SCOPES["admin"]), "admin", token_row.id, "session"),
            Identity(user, org, set(ROLE_SCOPES["admin"]), "admin", actor_kind="worker"),
        ]
        assert actors[0].actor_kind == "token"
        for actor in actors:
            with pytest.raises(ServiceError) as denied:
                await score.human_set(
                    session,
                    actor,
                    UUID(case["task"]),
                    UUID(case["rubric"]["rubric"]["id"]),
                )
            assert denied.value.code == "human_session_required"
            assert denied.value.status == 403


@pytest.mark.parametrize("org_role", ["bidder", "viewer"])
async def test_check_decisions_reject_read_only_task_members(check_case, admin_engine, org_role):
    case = check_case
    report = await published(case)
    full = (await case["api"].get(f"/checks/{report}", headers=case["header"])).json()
    finding = next(row for row in full["items"] if row["review_domain"] == "commercial")
    _, header = await participant(
        case,
        admin_engine,
        org_role=org_role,
        task_role="observer",
    )
    before = await row_counts(case["app"], UUID(header["X-Org-Id"]))
    await post_error(
        case,
        f"/v4/checks/{report}/findings/{finding['id']}/decisions",
        {
            "action": "dismiss",
            "reason": "Synthetic observer cannot review",
            "expected_revision": finding["revision"],
            "expected_input_hash": full["data"]["report"]["input"]["input_hash"],
        },
        header,
    )
    assert await row_counts(case["app"], UUID(header["X-Org-Id"])) == before


async def test_archived_task_blocks_check_preview_run_and_decision(check_case, admin_engine):
    case = check_case
    report = await published(case)
    full = (await case["api"].get(f"/checks/{report}", headers=case["header"])).json()
    finding = next(row for row in full["items"] if row["review_domain"] == "commercial")
    _, bidder = await participant(
        case,
        admin_engine,
        org_role="bidder",
        task_role="contributor",
        domains=["commercial"],
    )
    _, admin = await participant(case, admin_engine, org_role="admin")
    current = await workflow(case["api"], admin, case["task"])
    archived = await case["api"].post(
        f"/tasks/{case['task']}/archive",
        headers=admin,
        json={"expected_revision": current["revision"], "reason": "Synthetic check archive"},
    )
    assert archived.status_code == 200, archived.text
    before = await row_counts(case["app"], UUID(admin["X-Org-Id"]))
    body = {
        "draft_id": case["draft"]["id"],
        "assessment_date": "2026-10-05",
        "mode": "rules",
    }
    for extra in (
        {"dry_run": True},
        {"expected_input_hash": full["data"]["report"]["input"]["input_hash"]},
    ):
        await post_error(
            case,
            f"/v4/tasks/{case['task']}/checks",
            {**body, **extra},
            bidder,
            code="task_archived",
            status=409,
        )
    await post_error(
        case,
        f"/v4/checks/{report}/findings/{finding['id']}/decisions",
        {
            "action": "dismiss",
            "reason": "Synthetic archived risk decision",
            "expected_revision": finding["revision"],
            "expected_input_hash": full["data"]["report"]["input"]["input_hash"],
        },
        bidder,
        code="task_archived",
        status=409,
    )
    allowed = await case["api"].get(
        f"/v4/checks/{report}?view=console&part=summary",
        headers=bidder,
    )
    assert allowed.status_code == 200, allowed.text
    assert await row_counts(case["app"], UUID(admin["X-Org-Id"])) == before


async def test_archived_task_blocks_rubric_generation_preview_and_run(rubric_case, admin_engine):
    case = rubric_case
    _, admin = await participant(case, admin_engine, org_role="admin")
    current = await workflow(case["api"], admin, case["task"])
    archived = await case["api"].post(
        f"/tasks/{case['task']}/archive",
        headers=admin,
        json={"expected_revision": current["revision"], "reason": "Synthetic rubric archive"},
    )
    assert archived.status_code == 200, archived.text
    before = await rubric_counts(case)
    calls = len(case["vendor"].requests)
    await post_error(
        case,
        f"/v4/tasks/{case['task']}/score-rubrics/preview",
        {"extraction_job_id": case["extraction"], "dry_run": True},
        case["header"],
        code="task_archived",
        status=409,
    )
    await post_error(
        case,
        f"/v4/tasks/{case['task']}/score-rubrics",
        {
            "extraction_job_id": case["extraction"],
            "expected_input_hash": case["preview"]["input"]["input_hash"],
        },
        case["header"],
        code="task_archived",
        status=409,
    )
    assert await rubric_counts(case) == before
    assert len(case["vendor"].requests) == calls


async def test_tokens_do_not_inherit_admin_recovery_or_missing_read_scopes(
    rubric_case, admin_engine
):
    case = rubric_case
    _, admin = await participant(case, admin_engine, org_role="admin")
    assert (
        await case["api"].get(
            "/v4" + base(case) + "?view=console",
            headers=admin,
        )
    ).status_code == 200
    token = await token_header(case["api"], admin, scopes=["task:read", "score:read"])
    missing = await case["api"].get("/v4" + base(case) + "?view=console", headers=token)
    assert missing.status_code == 404, missing.text
    _, joined_admin = await participant(
        case,
        admin_engine,
        org_role="admin",
        task_role="contributor",
    )
    limited = await token_header(case["api"], joined_admin, scopes=["task:read"])
    denied = await case["api"].get("/v4" + base(case) + "?view=console", headers=limited)
    assert denied.status_code == 403, denied.text
    assert denied.json()["data"]["error"]["code"] == "forbidden"
