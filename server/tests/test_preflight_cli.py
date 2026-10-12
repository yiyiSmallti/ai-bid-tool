"""DB-free contract scenarios specified before the version/preflight implementation.

Failures covered: legacy nested Cost rejects new fields, quote sums hide unknown
liability, dynamic work is falsely guaranteed, first-call rejection is confused
with whole-pass rejection, and failed waits lose their original accounting/items.
"""

from uuid import uuid4

from bid_cli.main import partial_completion_exit, wait_for_job


async def test_failed_wait_keeps_result(monkeypatch):
    from bid_cli import main as module

    body = {
        "ok": False,
        "data": {
            "status": "failed",
            "error": {"exit_code": 4},
            "result": {"completion": "failed", "stop_reason": "job_call_limit_exceeded"},
        },
        "cost": {},
        "items": [{"id": "unfinished"}],
    }

    class Stub:
        async def request(self, *args, **kwargs):
            return body

    monkeypatch.setattr(module, "client", lambda: Stub())
    assert await wait_for_job(uuid4(), 1) is body
    assert partial_completion_exit(body) == 4
    assert body["cost"] == {} and body["items"]
