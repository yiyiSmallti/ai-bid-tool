"""Real DB/API acceptance for human-only console assessment decisions.

Tokens and workers must not inherit human authority through actor_kind=session,
claimed decision scopes, or a missing rubric. Rejected actions leave saved state
and Providers unchanged. Synthetic inputs and fake Providers exercise production
APIs against the isolated PostgreSQL runtime.
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
from app.services.auth import HUMAN_SCOPES, Identity
from sqlalchemy import func, select
from test_card_generation import token_header
from test_check import check_case as check_case
from test_score_api import rubric_case as rubric_case
from test_score_api import rubric_counts
from test_score_api import rubric_input_case as rubric_input_case
from test_score_review import base, decision, replacement
from test_score_run import score_case as score_case


async def post_error(case, path, body, header, *, code="forbidden", status=403):
    response = await case["api"].post(path, headers=header, json=body)
    assert response.status_code == status, (path, response.text)
    assert response.json()["data"]["error"]["code"] == code
    return response


async def rubric_business_counts(case):
    counts = await rubric_counts(case)
    counts.pop("audit_logs")
    async with case["app"].state.db.transaction() as session:
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
    case, report, path, body, header, *, code="forbidden", status=403, actor_kind="session"
):
    """Preserve business state and distinguish its hashed denial from token invocation audit."""
    before = await rubric_business_counts(case)
    async with case["app"].state.db.transaction() as session:
        audit_ids = set(await session.scalars(select(AuditLog.id)))
        token_id = await session.scalar(select(ApiToken.id)) if actor_kind == "token" else None
        if actor_kind == "token":
            assert token_id is not None
    response = await post_error(case, path, body, header, code=code, status=status)
    command = response.json()["command"]
    assert await rubric_business_counts(case) == before
    async with case["app"].state.db.transaction() as session:
        added = list(await session.scalars(select(AuditLog).where(AuditLog.id.not_in(audit_ids))))
        expected_actions = ["score_rubric.decision_denied"]
        if actor_kind == "token":
            expected_actions.append("command.failed")
        assert sorted(entry.action for entry in added) == sorted(expected_actions)
        for entry in added:
            assert entry.actor_token_id == token_id
            assert entry.actor_kind == actor_kind
            assert entry.command == command
            assert entry.invocation_id is not None
            assert entry.initiated_by == ("external_agent" if token_id else "human")
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
            {**classify, "expected_revision": item["revision"]},
        ),
        (parent + f"/sections/{section['id']}/decisions", decision(report, section)),
        (parent + f"/items/{item['id']}/decisions", decision(report, item)),
        (
            parent + f"/coverage/{coverage['requirement_id']}/decisions",
            decision(report, coverage, "mapped", rubric_item_ids=[item["id"]]),
        ),
        (parent + "/decisions?view=console", decision(report, report["rubric"])),
    ]


async def test_token_and_worker_rubric_writes_keep_human_error_precedence(
    rubric_case, admin_engine
):
    from app.services import score

    case = rubric_case
    human = case["header"]
    token = await token_header(case["api"], human, scopes=["task:read", "score:read"])
    for path, body in rubric_writes(case, case["rubric"]):
        await rubric_denial(
            case,
            case["rubric"],
            path,
            body,
            token,
            code="human_session_required",
            actor_kind="token",
        )
    async with case["app"].state.db.transaction() as session:
        token_row = await session.scalar(select(ApiToken))
        assert token_row is not None
        actors = [
            Identity(scopes=set(HUMAN_SCOPES), token_id=token_row.id),
            Identity(scopes=set(HUMAN_SCOPES), actor_kind="worker"),
        ]
        assert actors[0].actor_kind == "token"
        for actor in actors:
            with pytest.raises(ServiceError) as denied:
                await score.human_set(
                    session, actor, UUID(case["task"]), UUID(case["rubric"]["rubric"]["id"])
                )
            assert denied.value.code == "human_session_required"
            assert denied.value.status == 403
