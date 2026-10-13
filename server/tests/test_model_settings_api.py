"""DB-backed HTTP acceptance for metadata-only local model settings.

Failure inventory: metadata decrypts or calls vendors; unsafe fields escape; cursors
cross authority; CAS loses writes; preflight creates calls; failed tests retry;
audit failure commits an incomplete provider revision.
"""

import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from app.core.provider_secrets import ProviderSecrets
from app.models.entities import AuditLog, Job
from app.models.provider_configs import ProviderConfig
from app.services import provider_configs
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from test_provider_config import KEY, Vendor, configured, local_config, save, token_header

BASE = "/v5/management/providers"
FORBIDDEN = {
    "api_key",
    "encrypted_key",
    "key_last4",
    "fingerprint",
    "credential",
    "credential_id",
    "vendor_input_usd_per_mtok",
    "vendor_output_usd_per_mtok",
}


def assert_safe(value, *secrets):
    encoded = json.dumps(value, default=str)
    for secret in (KEY, *secrets):
        assert secret not in encoded
    if isinstance(value, dict):
        assert FORBIDDEN.isdisjoint(value)
        for child in value.values():
            assert_safe(child, *secrets)
    elif isinstance(value, list):
        for child in value:
            assert_safe(child, *secrets)


async def reads(api, header, revision_id):
    return [
        await api.get(BASE, headers=header),
        await api.get(f"{BASE}/revisions/{revision_id}", headers=header),
        await api.post(BASE + "/history/query", headers=header, json={"limit": 25}),
    ]


async def test_metadata_reads_do_not_decrypt_call_vendor_or_aggregate_usage(
    database, tmp_path, monkeypatch, caplog
):
    async with configured(database, tmp_path) as (app, api, headers, vendor):
        first = await save(api, headers)
        second = await save(api, headers, expected_revision=1, api_key=None)
        async with app.state.db.transaction() as session:
            ciphertexts = list(await session.scalars(select(ProviderConfig.encrypted_key)))

        def forbidden(*args, **kwargs):
            pytest.fail("Metadata read attempted decryption or usage aggregation")

        monkeypatch.setattr(ProviderSecrets, "decrypt", forbidden)
        monkeypatch.setattr(provider_configs, "monthly_usage", forbidden)
        for response in await reads(api, headers, first["id"]):
            assert response.status_code == 200, response.text
            assert_safe(response.json(), *ciphertexts)
            assert '"4321"' not in response.text
            assert response.headers["cache-control"] == "no-store"
        detail = (await api.get(f"{BASE}/revisions/{first['id']}", headers=headers)).json()["data"]
        assert detail["revision"] == 1 and detail["id"] == first["id"]
        assert detail["revised_at"] is not None
        current = (await api.get(BASE, headers=headers)).json()["data"]
        assert current["current"]["id"] == second["id"]
        assert vendor.calls == []
        async with app.state.db.transaction() as session:
            audits = list(await session.scalars(select(AuditLog.details)))
            assert_safe(audits, *ciphertexts)
            assert len(audits) == 2  # Reads add no business audit per row.
        assert_safe(caplog.text, *ciphertexts)


async def test_human_and_scoped_token_permissions(database, tmp_path, admin_engine):
    async with configured(database, tmp_path) as (_, api, headers, vendor):
        first = await save(api, headers)
        read_token = await token_header(api, headers, scopes=["provider:read"])
        unrelated = await token_header(api, headers, scopes=["task:read"])
        for header in (headers, read_token):
            for response in await reads(api, header, first["id"]):
                assert response.status_code == 200, response.text
                assert_safe(response.json())
        for response in await reads(api, unrelated, first["id"]):
            assert response.status_code == 403
        for header in (read_token, unrelated):
            assert (
                await api.post(
                    "/v5/providers", headers=header, json=local_config(expected_revision=1)
                )
            ).status_code == 403
            for dry_run in (True, False):
                assert (
                    await api.post("/v5/providers/test", headers=header, json={"dry_run": dry_run})
                ).status_code == 403
        result = await api.post(
            "/v5/providers", headers=headers, json=local_config(expected_revision=1, api_key=None)
        )
        assert result.status_code == 200
        assert vendor.calls == []


async def test_concurrent_save_key_reuse_and_append_only_history(database, tmp_path):
    async with configured(database, tmp_path) as (app, api, headers, _):
        first = await save(api, headers)
        replies = await asyncio.gather(
            *[
                api.post(
                    "/v5/providers",
                    headers=headers,
                    json=local_config(expected_revision=1, api_key=None),
                )
                for _ in range(2)
            ]
        )
        assert sorted(reply.status_code for reply in replies) == [200, 409]
        for reply in replies:
            assert KEY not in reply.text
        for change in ({"base_url": "https://new.example/v1"}, {"provider": "anthropic"}):
            response = await api.post(
                "/v5/providers",
                headers=headers,
                json=local_config(expected_revision=2, api_key=None, **change),
            )
            assert response.status_code == 400
            assert response.json()["data"]["error"]["code"] == "provider_key_required"
        history = (await api.post(BASE + "/history/query", headers=headers, json={})).json()
        assert [row["revision"] for row in history["items"]] == [2, 1]
        assert history["items"][1]["id"] == first["id"]
        for sql in (
            "UPDATE provider_configs SET revision=revision",
            "DELETE FROM provider_configs",
        ):
            with pytest.raises(DBAPIError):
                async with app.state.db.transaction() as session:
                    await session.execute(text(sql))


async def test_preflight_then_failed_paid_test_is_accounted_once_without_fallback(
    database, tmp_path, caplog
):
    vendor = Vendor()
    vendor.response = httpx.Response(401, json={"error": {"message": KEY}})
    async with configured(database, tmp_path, vendor=vendor) as (app, api, headers, _):
        first = await save(api, headers, base_url="https://vendor.example/v1")
        preview = await api.post("/v5/providers/test", headers=headers, json={"dry_run": True})
        assert preview.status_code == 200
        assert vendor.calls == []
        async with app.state.db.transaction() as session:
            assert await session.scalar(select(func.count()).select_from(Job)) == 0
        response = await api.post("/v5/providers/test", headers=headers, json={"dry_run": False})
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["ok"] is False
        assert receipt["data"]["provider_config_id"] == first["id"]
        assert len(vendor.calls) == 1
        assert_safe(receipt)
        assert set(receipt["cost"]) == {
            "calls",
            "llm_tokens",
            "ocr_pages",
            "search_requests",
            "browser_units",
            "unresolved_calls",
        }
        async with app.state.db.transaction() as session:
            assert await session.scalar(select(func.count()).select_from(Job)) == 1
            assert_safe(list(await session.scalars(select(AuditLog.details))))
        assert_safe(caplog.text)


async def test_validation_and_audit_failure_never_echo_or_commit_secret(
    database, tmp_path, monkeypatch, caplog
):
    async with configured(database, tmp_path) as (app, api, headers, _):
        for changes in (
            {"capability": "vision"},
            {"api_key": KEY + " "},
            {"unexpected": KEY},
            {KEY: "unknown-field-name"},
            {"reasoning": [{"name": "low", "request_options": {"extra": {"authorization": KEY}}}]},
        ):
            response = await api.post(
                "/v5/providers", headers=headers, json=local_config(**changes)
            )
            assert response.status_code == 422
            assert KEY not in response.text

        for path in (BASE + "/history/query", "/v5/providers/test"):
            response = await api.post(path, headers=headers, json={KEY: "unknown-field-name"})
            assert response.status_code == 422
            assert KEY not in response.text

        def fail_audit(*args, **kwargs):
            raise RuntimeError("Synthetic audit failure")

        monkeypatch.setattr(provider_configs, "audit", fail_audit)
        with pytest.raises(RuntimeError, match="Synthetic audit failure"):
            await api.post("/v5/providers", headers=headers, json=local_config())
        async with app.state.db.transaction() as session:
            assert await session.scalar(select(func.count()).select_from(ProviderConfig)) == 0
        assert_safe(caplog.text)


async def test_history_continuations(database, tmp_path, admin_engine):
    async with configured(database, tmp_path) as (app, api, headers, _):
        await save(api, headers)
        await save(api, headers, expected_revision=1, api_key=None)
        page = (await api.post(BASE + "/history/query", headers=headers, json={"limit": 1})).json()
        assert page["items"][0]["revision"] == 2
        following = (
            await api.post(
                BASE + "/history/query",
                headers=headers,
                json={"limit": 1, "cursor": page["data"]["next_cursor"]},
            )
        ).json()
        assert following["items"][0]["revision"] == 1
        assert following["data"]["next_cursor"] is None
        for body in (
            {"limit": 101},
            {"limit": 0},
            {"cursor": "x" * 2049},
        ):
            assert (
                await api.post(BASE + "/history/query", headers=headers, json=body)
            ).status_code == 422


async def test_metadata_unconfigured_and_default_do_not_create_revision(database, tmp_path):
    async with configured(database, tmp_path) as (app, api, headers, vendor):
        empty = (await api.get(BASE, headers=headers)).json()["data"]
        assert not empty["configured"] and empty["current"] is None
        assert vendor.calls == []
        async with app.state.db.transaction() as session:
            assert await session.scalar(select(func.count()).select_from(ProviderConfig)) == 0


@pytest.mark.parametrize("field,value", [("capability", "vision"), ("revision", 0)])
async def test_database_constraints_reject_before_human_guard(field, value, database, tmp_path):
    async with configured(database, tmp_path) as (app, _, _, _):
        fields = {
            "id": uuid4(),
            "capability": "llm_extract",
            "revision": 1,
            "data": {"provider": "openai", "model": "synthetic-model", "json_mode": "json_schema"},
            "encrypted_key": "synthetic-ciphertext",
            "key_last4": "xxxx",
        }
        fields[field] = value
        with pytest.raises(DBAPIError) as error:
            async with app.state.db.transaction() as session:
                session.add(ProviderConfig(**fields))
        assert getattr(error.value.orig, "sqlstate", None) == "23514"
