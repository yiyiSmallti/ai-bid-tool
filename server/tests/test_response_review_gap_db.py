"""Preserve persisted input-failure causes for single-human response consumers."""

import json
from pathlib import Path

import pytest
from app.core.config import Settings
from app.core.db import Database
from response_review_fixtures import attach_material, confirm, response_scope, submit
from sqlalchemy import text
from test_check import publish_draft

ARTIFACT = Path(__file__).resolve().parents[2] / "data/work/response-review-acceptance/gap-db.json"


@pytest.mark.parametrize(
    "confirmed,mutation,reasons",
    [
        (False, None, ["unconfirmed"]),
        (False, "requirement", ["requirement_invalidated", "unconfirmed", "needs_reconfirmation"]),
        (True, "requirement", ["requirement_invalidated", "needs_reconfirmation"]),
        (True, "citation", ["requirement_invalidated", "invalid_citation"]),
        (True, "material", ["stale_material"]),
    ],
)
async def test_draft_database_reasons_match_current_domain_and_input_failure(
    api,
    application,
    headers,
    tenants,
    admin_engine,
    tmp_path,
    confirmed,
    mutation,
    reasons,
):
    scope = await response_scope(api, headers, tenants, admin_engine)
    if mutation == "material":
        await attach_material(api, headers[0], scope, tmp_path)
    await submit(api, headers[0], scope)
    if confirmed:
        result = await confirm(api, scope)
        assert result.status_code == 200, result.text
    old_draft = None
    if mutation == "material":
        from test_exports import setup_template

        selection, binding = await setup_template(api, headers[0], str(scope["task_id"]))
        old_draft = await publish_draft(
            api,
            application,
            scope["members"]["commercial"]["headers"],
            str(scope["task_id"]),
            str(scope["job_id"]),
        )
    if mutation:
        with admin_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.current_org',:org,true)"),
                {"org": str(scope["org_id"])},
            )
            if mutation == "requirement":
                connection.execute(
                    text(
                        "UPDATE requirements SET text=text||' (changed requirement)' WHERE id=:id"
                    ),
                    {"id": scope["requirement_ids"][0]},
                )
            elif mutation == "citation":
                connection.execute(
                    text(
                        "UPDATE chunks SET text='Synthetic replaced citation source' WHERE id="
                        "(SELECT chunk_id FROM requirements WHERE id=:id)"
                    ),
                    {"id": scope["requirement_ids"][0]},
                )
            else:
                connection.execute(
                    text(
                        "UPDATE task_certificates SET active=false WHERE org_id=:org AND task_id=:task"
                    ),
                    {"org": scope["org_id"], "task": scope["task_id"]},
                )
    draft = await publish_draft(
        api,
        application,
        scope["members"]["commercial"]["headers"],
        str(scope["task_id"]),
        str(scope["job_id"]),
    )
    if mutation != "citation":
        from datetime import date
        from uuid import UUID

        from app.models.response_cards import DraftRun
        from app.services import check_inputs, response_cards, score_run_inputs
        from test_team_workflow_stream_acceptance import authenticated

        async with authenticated(application, scope["members"]["commercial"]["headers"]) as (
            session,
            actor,
        ):
            actor = await check_inputs.access(session, actor, "check:run")
            checked = await check_inputs.snapshot(
                session,
                actor,
                scope["task_id"],
                UUID(draft["draft_id"]),
                date(2026, 10, 5),
                application.state.storage,
            )
            assert checked.secret["items"][0]["gap_reasons"] == reasons
            assert checked.secret["items"][0]["partition"] == "gap"
            assert "response_text" not in checked.secret["items"][0]
            actor = await response_cards.access(session, actor, "score:run")
            saved_draft = await session.get(DraftRun, UUID(draft["draft_id"]))
            assert saved_draft is not None
            assessed, _ = await score_run_inputs.fixed_rows(
                session, actor, saved_draft, require_current=True
            )
            assert assessed[0]["gap_reasons"] == reasons
            assert assessed[0]["partition"] == "gap" and "response_text" not in assessed[0]
    if old_draft is not None:
        old_view = await api.get(f"/drafts/{old_draft['draft_id']}", headers=headers[0])
        assert old_view.status_code == 200, old_view.text
        assert old_view.json()["data"]["gaps"][0]["reasons"] == reasons
        refused = await api.post(
            f"/tasks/{scope['task_id']}/export-runs",
            headers=scope["members"]["commercial"]["headers"],
            json={
                "draft_id": old_draft["draft_id"],
                "task_template_id": selection["id"],
                "binding_id": binding["id"],
                "mode": "review_copy",
                "dry_run": True,
            },
        )
        assert refused.status_code == 400, refused.text
        assert refused.json()["data"]["error"]["code"] == "export_blocked"
        blocked_codes = {
            issue["code"]
            for issue in refused.json()["data"]["issues"]
            if issue["severity"] == "block"
        }
        assert "export_stale_draft" in blocked_codes
        assert "export_cosign_required" not in blocked_codes
        assert sum(refused.json()["data"]["table_rows"].values()) == 0
    db = Database(Settings())
    try:
        async with db.transaction(scope["org_id"]) as session:
            stored = (
                await session.execute(
                    text("SELECT kind,gap_reasons FROM response_items WHERE draft_id=:draft"),
                    {"draft": draft["draft_id"]},
                )
            ).one()
            assert stored.kind == "gap" and stored.gap_reasons == reasons
        artifact = ARTIFACT.parent / (f"gap-reasons-{confirmed}-{mutation or 'pending'}.json")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(
            json.dumps({"draft_id": draft["draft_id"], "gap_reasons": reasons}, indent=2) + "\n"
        )
    finally:
        await db.engine.dispose()
