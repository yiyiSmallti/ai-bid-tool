"""Retained annotation history, real FK ordering and token/provenance gates."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

TABLES = ("annotation_requests", "annotation_materials", "annotation_releases")


@pytest.mark.parametrize("table", TABLES)
def test_annotation_history_is_readable_and_immutable(admin_engine, table):
    with Session(admin_engine) as db, db.begin():
        assert db.scalar(
            text("SELECT has_table_privilege('bid_app', :table, 'SELECT,INSERT')"), {"table": table}
        )
        assert not db.scalar(
            text("SELECT has_table_privilege('bid_app', :table, 'UPDATE,DELETE')"), {"table": table}
        )
        # id-only updates support row locking; history changes are denied by the row trigger.
        assert db.scalar(
            text("SELECT has_column_privilege('bid_app', :table, 'id', 'UPDATE')"),
            {"table": table},
        )
        assert not db.scalar(
            text("SELECT has_column_privilege('bid_app', :table, 'created_at', 'UPDATE')"),
            {"table": table},
        )
        assert (
            db.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE tgrelid=to_regclass(:table) "
                    "AND tgname='annotation_immutable' AND NOT tgisinternal "
                    "AND tgenabled IN ('O','A') AND tgtype=27 "
                    "AND tgfoid='public.response_immutable_gate()'::regprocedure"
                ),
                {"table": table},
            )
            == 1
        )
        db.execute(text("SET LOCAL ROLE bid_app"))
        for statement in (
            f"DELETE FROM {table} WHERE false",
            f"UPDATE {table} SET created_at=created_at WHERE false",
        ):
            with pytest.raises(DBAPIError) as failure, db.begin_nested():
                db.execute(text(statement))
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
                assert "org_id" not in row.definition
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
