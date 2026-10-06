"""Real-source batch refresh after an in-transaction source invalidation.

Failure inventory: saving queries must not trust stale ORM Requirement/Review/
Chunk/Document objects, turn source drift into current approval, or add one read
per requirement. Keep the existing full draft-read <=40 statement budget intact.
"""

import json
from pathlib import Path
from uuid import UUID

from app.models.entities import Requirement
from app.services import requirement_consumption
from sqlalchemy import event, select, text
from test_response_cards import create_tender, phase_one_client


def record(value):
    target = Path(__file__).resolve().parents[2] / "data/work/b02-residual/review-batch.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, indent=2) + "\n")


async def test_bulk_review_reload_sees_raw_source_changes_in_the_same_session(tenants, tmp_path):
    async with phase_one_client(tenants, tmp_path) as (api, app, headers, _):
        task, _, extraction, requirements = await create_tender(
            api, app, headers[0], tmp_path, confirmed=True
        )
        org = tenants["orgs"][0]
        async with app.state.db.transaction(org) as session:
            rows = list(
                await session.scalars(
                    select(Requirement).where(
                        Requirement.task_id == UUID(task), Requirement.job_id == UUID(extraction)
                    )
                )
            )
            before = await requirement_consumption.effective(session, rows)
            assert all(value.confirmed for value in before.values())
            changed = UUID(requirements[0]["id"])
            before_hash = before[changed].review_hash
            before_revision = before[changed].revision
            await session.execute(
                text(
                    "UPDATE chunks SET text=text||E'\\nSynthetic additional paragraph' WHERE id=:id"
                ),
                {"id": UUID(requirements[0]["source"]["chunk_id"])},
            )
            statements = []

            def counted(conn, cursor, statement, parameters, context, executemany):
                statements.append(statement)

            engine = app.state.db.engine.sync_engine
            event.listen(engine, "before_cursor_execute", counted)
            try:
                after = await requirement_consumption.effective(session, rows)
            finally:
                event.remove(engine, "before_cursor_execute", counted)
            assert set(after) == set(before)
            assert after[changed].state == "invalidated"
            assert after[changed].revision > before_revision
            assert after[changed].review_hash != before_hash
            assert after[changed].citation_valid
            assert all(value.confirmed for key, value in after.items() if key != changed)
            assert len(statements) <= 3
        record(
            {
                "task_id": task,
                "extraction_job_id": extraction,
                "requirement_count": len(rows),
                "batch_statements": len(statements),
                "changed_requirement_id": str(changed),
                "state": after[changed].state,
            }
        )
