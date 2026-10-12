"""Access-key authentication rejects wrong values uniformly under concurrent load."""

import asyncio
import json
import os

import pytest


@pytest.mark.parametrize("wrong", ["", "x", "x" * 31, "x" * 32, "x" * 4096, "错误密钥"])
async def test_wrong_access_keys_have_one_public_failure(api, wrong):
    response = await api.post("/auth/session", json={"access_key": wrong})
    assert response.status_code == 401
    assert response.json()["data"]["error"]["code"] == "invalid_access_key"
    assert "session" not in response.json()["data"]


async def test_concurrent_wrong_keys_do_not_block_valid_session(api, tmp_path, caplog):
    responses = await asyncio.gather(
        *(api.post("/auth/session", json={"access_key": f"wrong-{index}"}) for index in range(20)),
        api.post("/auth/session", json={"access_key": os.environ["BID_ACCESS_KEY"]}),
    )
    assert [response.status_code for response in responses] == [401] * 20 + [200]
    assert all(
        response.json()["data"] == responses[0].json()["data"] for response in responses[:-1]
    )
    token = responses[-1].json()["data"]["session"]
    described = await api.get("/auth/session", headers={"Authorization": "Bearer " + token})
    assert described.status_code == 200 and described.json()["data"]["actor_kind"] == "session"
    assert token not in caplog.text and os.environ["BID_ACCESS_KEY"] not in caplog.text
    (tmp_path / "access-key-concurrency.json").write_text(
        json.dumps({"rejected": 20, "accepted": 1, "actor_kind": "session"})
    )
