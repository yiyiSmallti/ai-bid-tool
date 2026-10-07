"""DB-backed HTTP acceptance for metadata-only org model settings.

Failure inventory: metadata decrypts or calls a vendor; keys/ciphertext/suffixes
escape allowlists; historical platform identity is replaced by the default;
foreign IDs/cursors cross orgs; role changes retain authority; CAS loses a write;
paid preflight creates calls; failed tests retry/fall back; audit failures commit.
Run against the integration owner's disposable two-org PostgreSQL database.
"""

import asyncio
import json
from uuid import UUID, uuid4

import httpx
import pytest
from app.core.provider_secrets import ProviderSecrets
from app.models.entities import AuditLog, Job, Membership, PlatformModel, User
from app.models.provider_configs import ProviderConfig
from app.services import provider_configs
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_card_generation import token_header
from test_provider_config import KEY, Vendor, catalog, configured, org_config, save
from test_response_cards import set_role

BASE = "/v4/management/providers"
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
        await api.post(BASE + "/catalog/query", headers=header, json={"limit": 25}),
    ]


async def test_metadata_reads_do_not_decrypt_call_vendor_or_aggregate_usage(
    tenants, tmp_path, monkeypatch, caplog
):
    async with configured(tenants, tmp_path) as (app, api, headers, vendor):
        first = await save(api, headers[0])
        second = await save(api, headers[0], expected_revision=1, api_key=None)
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            ciphertexts = list(await session.scalars(select(ProviderConfig.encrypted_key)))

        def forbidden(*args, **kwargs):
            pytest.fail("Metadata read attempted decryption, balance or usage aggregation")

        monkeypatch.setattr(ProviderSecrets, "decrypt", forbidden)
        monkeypatch.setattr(provider_configs, "balance_view", forbidden)
        monkeypatch.setattr(provider_configs, "monthly_usage", forbidden)
        for response in await reads(api, headers[0], first["id"]):
            assert response.status_code == 200, response.text
            assert_safe(response.json(), *ciphertexts)
            assert '"4321"' not in response.text
            assert response.headers["cache-control"] == "no-store"
        detail = (await api.get(f"{BASE}/revisions/{first['id']}", headers=headers[0])).json()[
            "data"
        ]
        assert detail["revision"] == 1 and detail["id"] == first["id"]
        assert detail["revised_by"] == str(tenants["users"][0])
        assert detail["revised_at"] is not None
        current = (await api.get(BASE, headers=headers[0])).json()["data"]
        assert current["current"]["id"] == second["id"]
        assert vendor.calls == [] and vendor.balance_calls == 0
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            audits = list(await session.scalars(select(AuditLog.details)))
            assert_safe(audits, *ciphertexts)
            assert len(audits) == 2  # Reads add no business audit per row.
        assert_safe(caplog.text, *ciphertexts)


async def test_every_metadata_route_org_and_cursor_isolation(tenants, tmp_path):
    async with configured(tenants, tmp_path) as (app, api, headers, _):
        first = await save(api, headers[0])
        await save(api, headers[0], expected_revision=1, api_key=None)
        foreign = await save(api, headers[1])
        for header, expected in ((headers[0], first), (headers[1], foreign)):
            result = await api.post(BASE + "/history/query", headers=header, json={})
            assert result.status_code == 200
            assert all(row["org_id"] == header["X-Org-Id"] for row in result.json()["items"])
            assert expected["id"] in {row["id"] for row in result.json()["items"]}
        for bad_id in (foreign["id"], str(uuid4())):
            response = await api.get(f"{BASE}/revisions/{bad_id}", headers=headers[0])
            assert response.status_code == 404
            assert response.json()["data"]["error"]["code"] == "not_found"
        cursor = (
            await api.post(BASE + "/history/query", headers=headers[0], json={"limit": 1})
        ).json()["data"]["next_cursor"]
        assert cursor
        response = await api.post(
            BASE + "/history/query", headers=headers[1], json={"limit": 1, "cursor": cursor}
        )
        assert response.status_code == 400
        assert response.json()["data"]["error"]["code"] == "management_cursor_invalid"
        for response in await reads(
            api, {**headers[0], "X-Org-Id": headers[1]["X-Org-Id"]}, first["id"]
        ):
            assert response.status_code == 404
        for response in await reads(api, {}, first["id"]):
            assert response.status_code in {401, 422}
        async with app.state.db.transaction() as session:
            assert list(await session.scalars(select(ProviderConfig))) == []
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            assert await session.get(ProviderConfig, UUID(foreign["id"])) is None


@pytest.mark.parametrize("role", ["admin", "bidder", "technical", "viewer"])
async def test_role_matrix_including_scoped_tokens(role, tenants, tmp_path, admin_engine):
    async with configured(tenants, tmp_path) as (_, api, headers, vendor):
        first = await save(api, headers[0])
        read_token = await token_header(api, headers[0], scopes=["provider:read"])
        unrelated = await token_header(api, headers[0], scopes=["task:read"])
        set_role(admin_engine, tenants["orgs"][0], tenants["users"][0], role)
        for header in (headers[0], read_token):
            for response in await reads(api, header, first["id"]):
                assert response.status_code == 200, response.text
                assert_safe(response.json())
        for response in await reads(api, unrelated, first["id"]):
            assert response.status_code == 403
        for header in (read_token, unrelated):
            assert (
                await api.post(
                    "/v4/providers", headers=header, json=org_config(expected_revision=1)
                )
            ).status_code == 403
            for dry_run in (True, False):
                assert (
                    await api.post("/v4/providers/test", headers=header, json={"dry_run": dry_run})
                ).status_code == 403
        result = await api.post(
            "/v4/providers", headers=headers[0], json=org_config(expected_revision=1, api_key=None)
        )
        assert result.status_code == (200 if role == "admin" else 403)
        if role != "admin":
            for dry_run in (True, False):
                assert (
                    await api.post(
                        "/v4/providers/test", headers=headers[0], json={"dry_run": dry_run}
                    )
                ).status_code == 403
        assert vendor.calls == [] and vendor.balance_calls == 0


@pytest.mark.parametrize("change", ["membership", "user", "role"])
async def test_live_authority_invalidates_cursor(change, tenants, tmp_path, admin_engine):
    async with configured(tenants, tmp_path) as (_, api, headers, _):
        first = await save(api, headers[0])
        await save(api, headers[0], expected_revision=1, api_key=None)
        cursor = (
            await api.post(BASE + "/history/query", headers=headers[0], json={"limit": 1})
        ).json()["data"]["next_cursor"]
        with Session(admin_engine) as session, session.begin():
            if change == "user":
                session.get(User, tenants["users"][0]).active = False
            else:
                member = session.scalar(
                    select(Membership).where(Membership.org_id == tenants["orgs"][0])
                )
                if change == "role":
                    member.role = "viewer"
                else:
                    member.active = False
        response = await api.post(
            BASE + "/history/query", headers=headers[0], json={"limit": 1, "cursor": cursor}
        )
        assert response.status_code in ({400} if change == "role" else {401, 403, 404})
        if change != "role":
            for response in await reads(api, headers[0], first["id"]):
                assert response.status_code in {401, 403, 404}


async def test_concurrent_save_key_reuse_and_append_only_history(tenants, tmp_path):
    async with configured(tenants, tmp_path) as (app, api, headers, _):
        first = await save(api, headers[0])
        replies = await asyncio.gather(
            *[
                api.post(
                    "/v4/providers",
                    headers=headers[0],
                    json=org_config(expected_revision=1, api_key=None),
                )
                for _ in range(2)
            ]
        )
        assert sorted(reply.status_code for reply in replies) == [200, 409]
        for reply in replies:
            assert KEY not in reply.text
        for change in ({"base_url": "https://new.example/v1"}, {"provider": "anthropic"}):
            response = await api.post(
                "/v4/providers",
                headers=headers[0],
                json=org_config(expected_revision=2, api_key=None, **change),
            )
            assert response.status_code == 400
            assert response.json()["data"]["error"]["code"] == "provider_key_required"
        history = (await api.post(BASE + "/history/query", headers=headers[0], json={})).json()
        assert [row["revision"] for row in history["items"]] == [2, 1]
        assert history["items"][1]["id"] == first["id"]
        for sql in (
            "UPDATE provider_configs SET revision=revision",
            "DELETE FROM provider_configs",
        ):
            with pytest.raises(DBAPIError):
                async with app.state.db.transaction(tenants["orgs"][0]) as session:
                    await session.execute(text(sql))


async def test_platform_unavailable_retains_saved_identity_prices_and_reasoning(
    tenants, tmp_path, admin_engine
):
    async with configured(tenants, tmp_path) as (app, api, headers, vendor):
        await catalog(app, tenants["orgs"][0])
        response = await api.post(
            "/v4/providers",
            headers=headers[0],
            json={"source": "platform", "platform_model_id": "paid", "expected_revision": None},
        )
        assert response.status_code == 200
        revision_id = response.json()["data"]["id"]
        before = (await api.get(f"{BASE}/revisions/{revision_id}", headers=headers[0])).json()[
            "data"
        ]
        with Session(admin_engine) as session, session.begin():
            row = session.get(PlatformModel, "paid")
            # The platform default must stay enabled, so an operator retires it first.
            row.is_default = False
            row.enabled = False
            row.model = "changed-catalog-name"
            row.sale_input_per_mtok = 91
            row.sale_output_per_mtok = 92
        for result in (
            await api.get(BASE, headers=headers[0]),
            await api.get(f"{BASE}/revisions/{revision_id}", headers=headers[0]),
        ):
            assert result.status_code == 200
            assert_safe(result.json(), "https://vendor.example/v1", "provider_test")
            current = result.json()["data"].get("current", result.json()["data"])
            assert current["configuration"]["platform_model_id"] == "paid"
            assert current["catalog_state"] == "unavailable"
            for field in (
                "provider",
                "model",
                "reasoning",
                "default_reasoning",
                "sale_input_per_mtok",
                "sale_output_per_mtok",
            ):
                assert current[field] == before[field]
        choices = (await api.post(BASE + "/catalog/query", headers=headers[0], json={})).json()
        assert choices["items"] == []
        assert vendor.calls == [] and vendor.balance_calls == 0


async def test_preflight_then_failed_paid_test_is_accounted_once_without_fallback(
    tenants, tmp_path, caplog
):
    vendor = Vendor()
    vendor.response = httpx.Response(401, json={"error": {"message": KEY}})
    async with configured(tenants, tmp_path, vendor=vendor) as (app, api, headers, _):
        first = await save(api, headers[0], base_url="https://vendor.example/v1")
        preview = await api.post("/v4/providers/test", headers=headers[0], json={"dry_run": True})
        assert preview.status_code == 200
        assert vendor.calls == []
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            assert await session.scalar(select(func.count()).select_from(Job)) == 0
        response = await api.post("/v4/providers/test", headers=headers[0], json={"dry_run": False})
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["ok"] is False
        assert receipt["data"]["provider_config_id"] == first["id"]
        assert len(vendor.calls) == 1
        assert_safe(receipt)
        assert len(receipt["cost"]) == 9
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            assert await session.scalar(select(func.count()).select_from(Job)) == 1
            assert_safe(list(await session.scalars(select(AuditLog.details))))
        assert_safe(caplog.text)


async def test_validation_and_audit_failure_never_echo_or_commit_secret(
    tenants, tmp_path, monkeypatch, caplog
):
    async with configured(tenants, tmp_path) as (app, api, headers, _):
        for changes in (
            {"capability": "vision"},
            {"api_key": KEY + " "},
            {"unexpected": KEY},
            {KEY: "unknown-field-name"},
            {"reasoning": [{"name": "low", "request_options": {"extra": {"authorization": KEY}}}]},
        ):
            response = await api.post(
                "/v4/providers", headers=headers[0], json=org_config(**changes)
            )
            assert response.status_code == 422
            assert KEY not in response.text

        for path in (BASE + "/history/query", BASE + "/catalog/query", "/v4/providers/test"):
            response = await api.post(path, headers=headers[0], json={KEY: "unknown-field-name"})
            assert response.status_code == 422
            assert KEY not in response.text

        def fail_audit(*args, **kwargs):
            raise RuntimeError("Synthetic audit failure")

        monkeypatch.setattr(provider_configs, "audit", fail_audit)
        with pytest.raises(RuntimeError, match="Synthetic audit failure"):
            await api.post("/v4/providers", headers=headers[0], json=org_config())
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            assert await session.scalar(select(func.count()).select_from(ProviderConfig)) == 0
        assert_safe(caplog.text)


async def test_catalog_prefix_keysets_and_history_continuations(tenants, tmp_path, admin_engine):
    async with configured(tenants, tmp_path) as (app, api, headers, _):
        await catalog(app, tenants["orgs"][0])
        with Session(admin_engine) as session, session.begin():
            for index in range(28):
                session.add(
                    PlatformModel(
                        id=f"page-{index:03}",
                        capability="llm_extract",
                        provider="openai",
                        model=f"Model {index}",
                        base_url="https://vendor.example/v1",
                        credential="provider_test",
                        is_default=False,
                        enabled=True,
                        vendor_input_usd_per_mtok=1,
                        vendor_output_usd_per_mtok=2,
                        sale_input_per_mtok=3,
                        sale_output_per_mtok=4,
                        reasoning=[],
                        revision=1,
                        updated_by="ops@example.test",
                    )
                )
        first = await api.post(BASE + "/catalog/query", headers=headers[0], json={"q": "page-"})
        assert first.status_code == 200, first.text
        page = first.json()
        assert len(page["items"]) == 25 and page["data"]["has_more"]
        assert_safe(page, "https://vendor.example/v1", "provider_test")
        cursor = page["data"]["next_cursor"]
        second = await api.post(
            BASE + "/catalog/query", headers=headers[0], json={"q": "page-", "cursor": cursor}
        )
        assert second.status_code == 200
        assert [row["id"] for row in second.json()["items"]] == ["page-025", "page-026", "page-027"]
        assert not second.json()["data"]["has_more"]
        for header, body in (
            (headers[1], {"q": "page-", "cursor": cursor}),
            (headers[0], {"q": "paid", "cursor": cursor}),
            (headers[0], {"q": "page-", "cursor": "invalid"}),
        ):
            reply = await api.post(BASE + "/catalog/query", headers=header, json=body)
            assert reply.status_code == 400
        await save(api, headers[0])
        await save(api, headers[0], expected_revision=1, api_key=None)
        page = (
            await api.post(BASE + "/history/query", headers=headers[0], json={"limit": 1})
        ).json()
        assert page["items"][0]["revision"] == 2
        following = (
            await api.post(
                BASE + "/history/query",
                headers=headers[0],
                json={"limit": 1, "cursor": page["data"]["next_cursor"]},
            )
        ).json()
        assert following["items"][0]["revision"] == 1
        assert following["data"]["next_cursor"] is None
        for body in (
            {"limit": 101},
            {"limit": 0},
            {"q": " "},
            {"q": "x" * 201},
            {"cursor": "x" * 2049},
        ):
            assert (
                await api.post(BASE + "/catalog/query", headers=headers[0], json=body)
            ).status_code == 422


async def test_author_is_exact_unique_audit_not_current_viewer(tenants, tmp_path):
    from app.services.auth import Identity
    from app.services.versioned import audit

    async with configured(tenants, tmp_path) as (app, api, headers, _):
        first = await save(api, headers[0])
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            audit(
                session,
                Identity(tenants["users"][0], tenants["orgs"][0], {"provider:read"}, "admin"),
                "provider.set",
                UUID(first["id"]),
                {"revision": 1},
            )
        value = (await api.get(f"{BASE}/revisions/{first['id']}", headers=headers[0])).json()[
            "data"
        ]
        assert value["revised_by"] is None  # Multiple candidate associations are ambiguous.
        assert value["revised_at"] is not None


async def test_metadata_unconfigured_and_default_do_not_create_revision(tenants, tmp_path):
    async with configured(tenants, tmp_path) as (app, api, headers, vendor):
        empty = (await api.get(BASE, headers=headers[0])).json()["data"]
        assert empty["effective_source"] == "unconfigured"
        assert empty["current"] is None and empty["default_model"] is None
        await catalog(app, tenants["orgs"][0])
        value = (await api.get(BASE, headers=headers[0])).json()["data"]
        assert value["effective_source"] == "platform"
        assert value["current"] is None and value["default_model"]["id"] == "paid"
        assert_safe(value, "https://vendor.example/v1", "provider_test")
        assert vendor.calls == [] and vendor.balance_calls == 0
        async with app.state.db.transaction(tenants["orgs"][0]) as session:
            assert await session.scalar(select(func.count()).select_from(ProviderConfig)) == 0


@pytest.mark.parametrize(
    "case,sqlstate",
    [("foreign_actor", "23503"), ("missing_catalog", "23503"), ("bad_source", "23514")],
)
async def test_database_constraints_reject_before_human_guard(case, sqlstate, tenants, tmp_path):
    async with configured(tenants, tmp_path) as (app, _, _, _):
        fields = {
            "org_id": tenants["orgs"][0],
            "id": uuid4(),
            "capability": "llm_extract",
            "revision": 1,
            "source": "org",
            "platform_model_id": None,
            "data": {"provider": "openai", "model": "synthetic-model", "json_mode": "json_schema"},
            "encrypted_key": "synthetic-ciphertext",
            "key_last4": "xxxx",
            "updated_by": tenants["users"][1] if case == "foreign_actor" else tenants["users"][0],
        }
        if case == "missing_catalog":
            fields.update(
                source="platform", platform_model_id="missing", encrypted_key=None, key_last4=None
            )
        if case == "bad_source":
            fields["source"] = "unknown"
        # No human actor is set: the declarative constraint must still win.
        with pytest.raises(DBAPIError) as error:
            async with app.state.db.transaction(tenants["orgs"][0]) as session:
                session.add(ProviderConfig(**fields))
        assert getattr(error.value.orig, "sqlstate", None) == sqlstate
