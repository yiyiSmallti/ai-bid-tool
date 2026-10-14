"""Shared API fault injection for transactional delivery acceptance."""

import json

import pytest
from app.models.entities import AuditLog, Base
from sqlalchemy import func, select, text


async def assert_dispatch_rollback(
    api, app, header, path, body, artifact, *, expected_failure_receipt=False
):
    """A failed dispatch must leave no business rows behind."""

    async def counts():
        # Tables the app role cannot read it cannot write either, so a request
        # cannot leave rows there.
        readable = text("SELECT has_table_privilege(current_user, :table, 'SELECT')")
        async with app.state.db.transaction() as session:
            return {
                table.name: await session.scalar(select(func.count()).select_from(table))
                for table in Base.metadata.tables.values()
                if await session.scalar(readable, {"table": table.name})
            }

    async def unavailable(*args, **kwargs):
        raise OSError("Synthetic unavailable queue")

    before = await counts()
    async with app.state.db.transaction() as session:
        prior_audits = set(await session.scalars(select(AuditLog.id)))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(app.state.queue, "enqueue", unavailable)
        response = await api.post(path, headers=header, json=body)
    assert response.status_code == 503, response.text
    assert response.json()["data"]["error"]["code"] == "queue_unavailable"
    assert "job_id" not in response.json()["data"]
    after = await counts()
    expected = dict(before)
    if expected_failure_receipt:
        # Agent command failure receipts intentionally survive business rollback.
        async with app.state.db.transaction() as session:
            receipts = list(
                await session.scalars(select(AuditLog).where(AuditLog.id.not_in(prior_audits)))
            )
            assert len(receipts) == 1
            assert receipts[0].action == "command.failed"
            assert receipts[0].details["reason_code"] == "queue_unavailable"
        expected["audit_logs"] += 1
    artifact.write_text(
        json.dumps(
            {"path": path, "status": 503, "before": before, "expected": expected, "after": after},
            indent=2,
        )
    )
    assert after == expected, {
        name: (expected[name], count) for name, count in after.items() if count != expected[name]
    }
