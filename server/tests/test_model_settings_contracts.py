"""DB-free safe projections and wire-shape contracts.

Failure inventory: raw persisted data is spread into a response; secret fields
or platform request overrides are serialized; disabled catalog identity changes;
legacy prices are silently substituted; cursor is reused across actors/filters;
oversize metadata is accepted; read preconditions become write preconditions.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from app.api.main import create_app
from app.core.config import Settings
from app.core.errors import ServiceError
from app.core.security import TokenSigner
from app.schemas.management_pages import PageQuery
from app.schemas.management_providers import (
    PlatformModelChoice,
    ProviderCatalogQuery,
    ProviderRevisionMetadata,
)
from app.schemas.provider_contracts import ProviderConfigInput, ProviderConfigSet
from app.services import management_providers as service
from app.services.auth import Identity
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

CANARY = "synthetic-private-model-settings-value"
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


def actor():
    return Identity(uuid4(), uuid4(), {"provider:read", "provider:write"}, "admin")


def row(owner, source="platform", **changes):
    return {
        "id": uuid4(),
        "org_id": owner.org_id,
        "revision": 1,
        "capability": "llm_extract",
        "source": source,
        "platform_model_id": "saved-model" if source == "platform" else None,
        "updated_by": owner.user_id,
        "created_at": datetime.now(UTC),
        "catalog_enabled": False,
        "data": {
            "provider": "openai",
            "model": "Saved model",
            "base_url": None if source == "platform" else "https://api.example.test/v1",
            "json_mode": "json_schema",
            "reasoning": [
                {"name": "low", "label": "Low", "request_options": {"reasoning_effort": "low"}}
            ],
            "default_reasoning": "low",
            "input_usd_per_mtok": None,
            "output_usd_per_mtok": None,
            "catalog_revision": 3,
            "sale_input_per_mtok": 5,
            "sale_output_per_mtok": 8,
            **{key: CANARY for key in FORBIDDEN},
        },
        **{key: CANARY for key in FORBIDDEN},
        **changes,
    }


def assert_safe(value):
    assert CANARY not in json.dumps(value)
    if isinstance(value, dict):
        assert FORBIDDEN.isdisjoint(value)
        for child in value.values():
            assert_safe(child)
    elif isinstance(value, list):
        for child in value:
            assert_safe(child)


def test_projection_allowlist_disabled_identity_and_saved_terms():
    owner = actor()
    saved = row(owner)
    value = service.metadata(owner, saved, owner.user_id).model_dump(mode="json")
    assert_safe(value)
    assert value["catalog_state"] == "unavailable"
    assert value["model"] == "Saved model"
    assert value["configuration"]["platform_model_id"] == "saved-model"
    assert value["configuration"]["base_url"] is None
    assert value["configuration"]["expected_revision"] is None
    assert value["reasoning"] == [{"name": "low", "label": "Low"}]
    assert value["catalog_revision"] == 3 and value["sale_input_per_mtok"] == 5
    assert value["revised_by"] == str(owner.user_id)
    saved["data"].pop("catalog_revision")
    saved["data"].pop("sale_input_per_mtok")
    saved["data"].pop("sale_output_per_mtok")
    legacy = service.metadata(owner, saved, None)
    assert legacy.catalog_revision is None
    assert legacy.sale_input_per_mtok is None and legacy.sale_output_per_mtok is None
    assert legacy.revised_by is None


def test_byok_exposes_only_nonsecret_saved_input_and_official_choices():
    owner = actor()
    value = service.metadata(owner, row(owner, "org"), None).model_dump(mode="json")
    assert_safe(value)
    assert value["credential_state"] == "configured"
    assert value["catalog_state"] == "not_applicable"
    assert value["configuration"]["base_url"] == "https://api.example.test/v1"
    assert value["configuration"]["reasoning"][0]["request_options"] == {"reasoning_effort": "low"}
    assert value["sale_input_per_mtok"] is None and value["catalog_revision"] is None


def test_catalog_allowlist_drops_wholesale_endpoint_credentials_and_overrides():
    data = {
        "id": "catalog-a",
        "revision": 2,
        "model": "Official",
        "provider": "openai",
        "sale_input_per_mtok": 1,
        "sale_output_per_mtok": 2,
        "is_default": True,
        "reasoning": [{"name": "low", "label": "Low", "request_options": {"secret": CANARY}}],
        "default_reasoning": "low",
        "base_url": CANARY,
        **{key: CANARY for key in FORBIDDEN},
    }
    value = service.catalog_view(data).model_dump(mode="json")
    assert_safe(value)
    assert "base_url" not in value
    assert value["reasoning"] == [{"name": "low", "label": "Low"}]


@pytest.mark.parametrize("field", sorted(FORBIDDEN))
def test_projection_types_reject_new_secret_fields(field):
    owner = actor()
    value = service.metadata(owner, row(owner), None).model_dump(mode="json")
    with pytest.raises(ValidationError):
        ProviderRevisionMetadata.model_validate({**value, field: CANARY})
    catalog = {
        "id": "a",
        "revision": 1,
        "model": "Official",
        "provider": "openai",
        "sale_input_per_mtok": 1,
        "sale_output_per_mtok": 2,
        "default": False,
        "reasoning": [],
        "default_reasoning": None,
    }
    with pytest.raises(ValidationError):
        PlatformModelChoice.model_validate({**catalog, field: CANARY})


def test_secret_set_is_excluded_from_generic_serialization():
    body = ProviderConfigSet(source="org", provider="openai", model="Official", api_key=CANARY)
    assert CANARY not in body.model_dump_json()
    assert "api_key" not in body.model_dump()


@pytest.mark.parametrize(
    "name",
    [
        "api_key",
        "X-API-Key",
        "Authorization",
        "headers",
        "client_secret",
        "credential_id",
        "key_last4",
        "ciphertext",
        "fingerprint",
        "base_url",
        "vendor_input_usd_per_mtok",
    ],
)
def test_reasoning_rejects_nested_credential_transport_slots(name):
    body = {
        "source": "org",
        "provider": "openai",
        "model": "Official",
        "reasoning": [{"name": "low", "request_options": {"nested": [{name: CANARY}]}}],
        "default_reasoning": "low",
    }
    with pytest.raises(ValidationError):
        ProviderConfigInput.model_validate(body)
    owner = actor()
    saved = row(owner, "org")
    saved["data"]["reasoning"] = body["reasoning"]
    with pytest.raises(ServiceError) as error:
        service.metadata(owner, saved, None)
    assert error.value.code == "management_integrity_error"
    assert CANARY not in str(error.value)


def test_reasoning_option_tree_bounds_and_official_options():
    base = {"source": "org", "provider": "openai", "model": "Official", "default_reasoning": "low"}
    options = {
        "reasoning_effort": "low",
        "thinking": {"type": "enabled", "budget_tokens": 4096},
        "temperature": 0.1,
    }
    value = ProviderConfigInput.model_validate(
        {**base, "reasoning": [{"name": "low", "request_options": options}]}
    )
    assert value.reasoning[0].request_options == options
    deep = {}
    for _ in range(25):
        deep = {"nested": deep}
    for oversized in (deep, {"choices": list(range(5000))}):
        with pytest.raises(ValidationError):
            ProviderConfigInput.model_validate(
                {**base, "reasoning": [{"name": "low", "request_options": oversized}]}
            )


def test_metadata_sql_excludes_credential_columns():
    owner = actor()
    compiled = str(service.config_statement(owner).compile(dialect=postgresql.dialect()))
    for field in FORBIDDEN:
        assert field not in compiled
    assert "platform_models.base_url" not in compiled
    for column in service.CATALOG_COLUMNS:
        assert column.key not in FORBIDDEN
        assert column.key != "base_url"


def test_bad_stored_metadata_has_fixed_error_without_validation_input():
    owner = actor()
    saved = row(owner, "org")
    saved["data"]["base_url"] = "https://example.test/?secret=" + CANARY
    with pytest.raises(ServiceError) as error:
        service.metadata(owner, saved, None)
    assert error.value.code == "management_integrity_error"
    assert CANARY not in str(error.value)
    foreign = row(owner, org_id=uuid4())
    with pytest.raises(ServiceError) as error:
        service.metadata(owner, foreign, None)
    assert error.value.code == "management_integrity_error"


def test_cursor_is_authority_purpose_prefix_bound_and_expires():
    owner = actor()
    session = SimpleNamespace(
        info={
            "management_settings": Settings(
                database_url="postgresql+psycopg://fixture.invalid/bid_test",
                encryption_key=Fernet.generate_key().decode(),
                token_key=Fernet.generate_key().decode(),
            )
        }
    )
    signer = TokenSigner.for_tokens(service.settings_for(session))
    cursor = signer.issue(
        {**service.binding(owner, "catalog", {"id_prefix": "abc"}), "anchor": "abc-one"}, 900
    )
    query = ProviderCatalogQuery(cursor=cursor, q="abc")
    assert service.open_cursor(session, owner, query, "catalog", {"id_prefix": "abc"}) == "abc-one"
    for other, purpose, filters in [
        (actor(), "catalog", {"id_prefix": "abc"}),
        (owner, "history", {}),
        (owner, "catalog", {"id_prefix": "other"}),
    ]:
        with pytest.raises(ServiceError) as error:
            service.open_cursor(session, other, query, purpose, filters)
        assert error.value.code == "management_cursor_invalid"
    expired = signer.issue(
        {**service.binding(owner, "history", {}), "anchor": [1, str(uuid4())]}, -1
    )
    with pytest.raises(ServiceError) as error:
        service.open_cursor(session, owner, PageQuery(cursor=expired), "history", {})
    assert error.value.code == "management_cursor_expired"


@pytest.mark.parametrize(
    "body",
    [
        {"q": " "},
        {"q": "a" * 201},
        {"limit": 101},
        {"limit": True},
        {"cursor": "a" * 2049},
        {"capability": "ocr"},
    ],
)
def test_catalog_query_bounds(body):
    with pytest.raises(ValidationError):
        ProviderCatalogQuery.model_validate(body)


@pytest.mark.parametrize(
    "path,body",
    [
        (
            "/providers",
            {
                "source": "org",
                "provider": "openai",
                "model": "Official",
                "api_key": CANARY,
                CANARY: "extra",
            },
        ),
        (
            "/v4/providers",
            {
                "source": "org",
                "provider": "openai",
                "model": "Official",
                "api_key": CANARY,
                CANARY: "extra",
            },
        ),
        ("/providers/test", {CANARY: "extra"}),
        ("/v4/providers/test", {CANARY: "extra"}),
        ("/management/providers/history/query", {CANARY: "extra"}),
        ("/v4/management/providers/catalog/query", {CANARY: "extra"}),
    ],
)
async def test_provider_validation_never_reflects_dynamic_field_names(path, body, caplog):
    settings = Settings(
        database_url="postgresql+psycopg://fixture.invalid/bid_test",
        encryption_key=Fernet.generate_key().decode(),
        token_key=Fernet.generate_key().decode(),
    )
    application = create_app(settings=settings)
    try:
        # No org/auth header means dependency validation completes before any
        # transaction or vendor resolution; this is deliberately DB-free.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://fixture.test"
        ) as client:
            response = await client.post(path, json=body)
        assert response.status_code == 422, response.text
        assert response.json()["data"]["error"]["message"] == "Invalid provider input"
        assert CANARY not in response.text and CANARY not in caplog.text
        assert response.headers["Cache-Control"] == "no-store"
    finally:
        await application.state.db.engine.dispose()
        await application.state.password_attempts.close()
