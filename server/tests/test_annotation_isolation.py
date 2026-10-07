"""Database acceptance failures specified before B05 implementation.

Failure inventory: missing FORCE RLS or tenant policy; empty org context reads;
cross-org task/source/card/job/actor references; wrong parent rejection masked by
business triggers; mutation of retained annotation history; token annotation
scope; legacy profile uniqueness changing; annotation profile missing renderer
identity; release pixels, approval, B02 or exact card-evidence link drifting.
The real two-org PostgreSQL fixture is required. No providers or services run.
"""

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

TABLES = ("annotation_requests", "annotation_materials", "annotation_releases")


@pytest.mark.parametrize("table", TABLES)
def test_annotation_rls_and_immutable_privileges(admin_engine, tenants, table):
    with Session(admin_engine) as db, db.begin():
        row = db.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid=to_regclass(:table)"
            ),
            {"table": table},
        ).one()
        assert row.relrowsecurity and row.relforcerowsecurity
        assert (
            db.scalar(
                text(
                    "SELECT count(*) FROM pg_policy WHERE polrelid=to_regclass(:table) AND polqual IS NOT NULL AND polwithcheck IS NOT NULL"
                ),
                {"table": table},
            )
            == 1
        )
        assert db.scalar(
            text("SELECT has_table_privilege('bid_app', :table, 'SELECT,INSERT')"), {"table": table}
        )
        assert not db.scalar(
            text("SELECT has_table_privilege('bid_app', :table, 'UPDATE,DELETE')"), {"table": table}
        )
        db.execute(text("SET LOCAL ROLE bid_app"))
        for org in ["", *map(str, tenants["orgs"])]:
            db.execute(text("SELECT set_config('app.current_org',:org,true)"), {"org": org})
            assert not db.execute(
                text(f"SELECT id FROM {table} WHERE org_id::text<>:org"), {"org": org}
            ).all()
        with pytest.raises(DBAPIError) as failure, db.begin_nested():
            db.execute(text(f"DELETE FROM {table} WHERE false"))
        assert failure.value.orig.sqlstate == "42501"


@pytest.mark.parametrize("table", TABLES)
def test_annotation_parents_use_real_immediate_composite_fks(admin_engine, table):
    with Session(admin_engine) as db:
        rows = db.execute(
            text(
                "SELECT conkey, confkey, condeferrable, pg_get_constraintdef(oid) AS definition FROM pg_constraint WHERE conrelid=to_regclass(:table) AND contype='f'"
            ),
            {"table": table},
        ).all()
        assert len(rows) >= 5
        for row in rows:
            if len(row.conkey) > 1:
                assert not row.condeferrable
                assert "org_id" in row.definition
        triggers = db.execute(
            text(
                "SELECT tgname,tgtype FROM pg_trigger WHERE tgrelid=to_regclass(:table) AND NOT tgisinternal"
            ),
            {"table": table},
        ).all()
        business = [row for row in triggers if row.tgname.endswith("binding_guard")]
        assert len(business) == 1
        assert not business[0].tgtype & 2  # AFTER checks follow actual RI triggers.
        assert business[0].tgname > "RI_ConstraintTrigger_z"


def test_annotation_token_scope_is_additive(admin_engine):
    with Session(admin_engine) as db:
        checks = " ".join(
            db.scalars(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='api_tokens'::regclass AND contype='c'"
                )
            )
        )
        for forbidden in ("evidence:annotate", "evidence:confirm", "token:create", "export"):
            assert forbidden in checks


def test_annotation_profiles_keep_legacy_partial_key(admin_engine):
    with Session(admin_engine) as db:
        indexes = " ".join(
            db.scalars(
                text("SELECT indexdef FROM pg_indexes WHERE tablename='screenshot_renditions'")
            )
        )
        assert "screenshot_rendition_legacy_identity" in indexes
        assert "screenshot_rendition_annotation_identity" in indexes
        assert "provenance_sha256" in indexes and "renderer_identity" in indexes
        checks = " ".join(
            db.scalars(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='screenshot_renditions'::regclass AND contype='c'"
                )
            )
        )
        assert "annotation-candidate-v1" in checks and "annotation-release-v1" in checks
        assert "renderer_identity IS NOT NULL" in checks


async def test_annotation_rows_are_isolated_and_fk_rejection_precedes_business_guards(
    tenants, tmp_path, admin_engine, monkeypatch
):
    """Exercise the real source -> candidate -> review -> release SQL graph."""
    from uuid import UUID, uuid4

    from annotation_test_renderer import configure_renderer
    from app.models import Base
    from app.models.entities import Job
    from sqlalchemy import insert, select
    from test_annotation_api import preview_submit, seed_annotation
    from test_response_cards import phase_one_client, require_action, set_role

    configure_renderer(monkeypatch, real=False)
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        org_a, org_b = tenants["orgs"]
        task, card, _, _, target = await seed_annotation(
            api, app, headers[1], tmp_path, confirmed=True
        )
        _, _, accepted = await preview_submit(api, headers[1], task, target)
        await app.state.processor(str(org_b), accepted["job_id"])
        status = await api.get(f"/v4/jobs/{accepted['job_id']}", headers=headers[1])
        assert status.json()["data"]["status"] == "succeeded", status.text
        material_id = status.json()["data"]["result"]["annotation_id"]
        shown = await api.get(f"/v4/annotations/{material_id}", headers=headers[1])
        candidate = shown.json()["data"]["candidate"]
        response = await api.put(
            f"/cards/{card['id']}",
            headers=headers[1],
            json={
                "expected_revision": card["revision"],
                "content": {
                    **card["content"],
                    "evidence": [
                        {
                            "kind": "image_region",
                            "asset_id": candidate["asset_id"],
                            "rendition_id": candidate["rendition_id"],
                            "expected_image_sha256": candidate["rendering"]["image"]["sha256"],
                            "region": {"x": 1, "y": 1, "width": 40, "height": 20},
                            "claim_scope": "document_excerpt",
                            "visual_observation": "The archived certificate region is legible.",
                        }
                    ],
                },
            },
        )
        assert response.status_code == 200, response.text
        card = await require_action(api, headers[1], response.json()["data"], "submit")
        set_role(admin_engine, org_b, tenants["users"][1], "bidder")
        card = await require_action(
            api,
            headers[1],
            card,
            "confirm",
            reviewed_evidence_ids=[item["id"] for item in card["evidence"]],
            reviewed_warning_codes=card["warning_codes"],
            reason="Inspected archived pixels.",
        )
        async with app.state.db.transaction(org_b) as db:
            job_id = await db.scalar(
                select(Job.id).where(Job.task_id == UUID(task), Job.kind == "annotation_release")
            )
        assert job_id is not None
        await app.state.processor(str(org_b), str(job_id))
        released = await api.get(f"/v4/jobs/{job_id}", headers=headers[1])
        assert released.json()["data"]["status"] == "succeeded", released.text
        receipt = {}
        with Session(admin_engine) as db, db.begin():
            records = {
                table: dict(
                    db.execute(
                        select(Base.metadata.tables[table]).where(
                            Base.metadata.tables[table].c.org_id == org_b
                        )
                    )
                    .mappings()
                    .one()
                )
                for table in TABLES
            }
            db.execute(text("SET LOCAL ROLE bid_app"))
            for table, record in records.items():
                relation = Base.metadata.tables[table]
                for org in (org_b, org_a, None):
                    db.execute(
                        text("SELECT set_config('app.current_org',:org,true)"),
                        {"org": str(org) if org else ""},
                    )
                    visible = db.execute(
                        select(relation.c.id).where(relation.c.id == record["id"])
                    ).all()
                    assert bool(visible) is (org == org_b)
                with pytest.raises(DBAPIError) as missing_context, db.begin_nested():
                    db.execute(insert(relation).values(**{**record, "id": uuid4()}))
                assert missing_context.value.orig.sqlstate == "42501"
                db.execute(
                    text("SELECT set_config('app.current_org',:org,true)"), {"org": str(org_a)}
                )
                foreign = {**record, "id": uuid4(), "org_id": org_a}
                # Each parent exists in B. No fake user context is installed:
                # the real immediate FK must reject before the human/job guard.
                with pytest.raises(DBAPIError) as failure, db.begin_nested():
                    db.execute(insert(relation).values(**foreign))
                assert failure.value.orig.sqlstate == "23503", str(failure.value.orig)
                db.execute(
                    text("SELECT set_config('app.current_org',:org,true)"), {"org": str(org_b)}
                )
                with pytest.raises(DBAPIError) as immutable, db.begin_nested():
                    db.execute(text(f"UPDATE {table} SET id=id WHERE id=:id"), {"id": record["id"]})
                assert immutable.value.orig.sqlstate == "42501"
                receipt[table] = {
                    "org_b_visible": True,
                    "org_a_hidden": True,
                    "missing_context_hidden": True,
                    "foreign_key_sqlstate": "23503",
                    "history_mutation_sqlstate": "42501",
                }
        await asyncio.to_thread(_write_isolation_receipt, receipt)


def _write_isolation_receipt(receipt):
    artifact = Path(__file__).resolve().parents[2] / "data/work/annotation-acceptance"
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / "database-isolation.json").write_text(json.dumps(receipt, indent=2) + "\n")
