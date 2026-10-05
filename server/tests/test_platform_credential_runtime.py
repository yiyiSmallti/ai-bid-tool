"""DB-free integration of credential resolution, outbound requests and admission hooks."""

from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from app.core.config import Settings
from app.core.errors import ServiceError
from app.core.provider_secrets import ProviderSecrets
from app.providers.calls import current_accounting
from app.providers.llm import OpenAICompatibleExtractor
from app.schemas.platform_credentials import (
    CatalogResolveTarget,
    CredentialSpec,
    PlatformCredentialEnvelope,
    ResolvedCredential,
)
from cryptography.fernet import Fernet
from pydantic import SecretStr


def config(**overrides):
    return Settings(
        database_url="postgresql+psycopg://synthetic@localhost/unused",
        encryption_key=Fernet.generate_key().decode(),
        token_key=Fernet.generate_key().decode(),
        secrets_key=Fernet.generate_key().decode(),
        **overrides,
    )


class Resolver:
    def __init__(self):
        self.calls = 0
        self.state = "active"

    async def resolve_for_call(self, target):
        self.calls += 1
        if self.state != "active":
            raise ServiceError("credential_disabled", "Credential is disabled", 503, 4)
        return ResolvedCredential(
            uuid4(),
            1,
            self.calls,
            "openai",
            "https://api.openai.com/v1",
            SecretStr("synthetic-key-version-" + str(self.calls)),
        )


async def test_each_outbound_resolves_new_secret_and_refuses_disabled():
    seen = []
    resolver = Resolver()
    adapter = OpenAICompatibleExtractor(
        config(llm_model="synthetic"),
        credential_resolver=resolver,
        credential_target=CatalogResolveTarget(model_id="synthetic", expected_model_revision=1),
    )

    def handler(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(
            200, json={"model": "synthetic", "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        for _ in range(2):
            await adapter.post(client, "https://api.openai.com/v1/chat/completions", {}, {})
        resolver.state = "disabled"
        with pytest.raises(ServiceError, match="disabled"):
            await adapter.post(client, "https://api.openai.com/v1/chat/completions", {}, {})
    assert seen == ["Bearer synthetic-key-version-1", "Bearer synthetic-key-version-2"]
    assert adapter.settings.llm_api_key is None


async def test_admission_wait_rechecks_and_releases_proven_unsent_call():
    resolver = Resolver()
    events = []

    class Accounting:
        async def admit(self, reserved, billed):
            resolver.state = "disabled"
            events.append("admit")
            return uuid4()

        async def not_sent(self, call_id):
            events.append("not_sent")

        async def unknown(self, call_id):
            events.append("unknown")

        async def complete(self, call_id, usage):
            events.append("complete")

    adapter = OpenAICompatibleExtractor(
        config(llm_model="synthetic"),
        credential_resolver=resolver,
        credential_target=CatalogResolveTarget(model_id="synthetic", expected_model_revision=1),
    )
    token = current_accounting.set(Accounting())
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: pytest.fail("sent"))
        ) as client:
            with pytest.raises(ServiceError):
                await adapter.post(
                    client,
                    "https://api.openai.com/v1/chat/completions",
                    {},
                    {},
                    reserved_charge=Decimal(0),
                )
    finally:
        current_accounting.reset(token)
    assert events == ["admit", "not_sent"]


def test_crypto_rotation_preserves_binding_and_rejects_other_domain():
    old = Fernet.generate_key().decode()
    original = config(secrets_key_previous=[]).model_copy(update={"secrets_key": SecretStr(old)})
    current = original.model_copy(
        update={
            "secrets_key": SecretStr(Fernet.generate_key().decode()),
            "secrets_key_previous": [SecretStr(old)],
        }
    )
    envelope = PlatformCredentialEnvelope(
        name="synthetic",
        purpose="catalog_llm",
        provider="openai",
        endpoint="https://api.openai.com/v1",
        credential_id=uuid4(),
        secret_version=1,
        api_key="synthetic-key-only-for-tests",
    )
    spec = CredentialSpec(
        **envelope.model_dump(include={"name", "purpose", "provider", "endpoint"})
    )
    encrypted = ProviderSecrets(original).encrypt_platform(envelope)
    cipher = ProviderSecrets(current)
    rewritten = cipher.rewrap_platform(
        encrypted, expected=spec, credential_id=envelope.credential_id, secret_version=1
    )
    assert rewritten and rewritten != encrypted
    assert (
        cipher.decrypt_platform(
            rewritten, expected=spec, credential_id=envelope.credential_id, secret_version=1
        ).get_secret_value()
        == "synthetic-key-only-for-tests"
    )
    assert (
        cipher.rewrap_platform(
            rewritten, expected=spec, credential_id=envelope.credential_id, secret_version=1
        )
        is None
    )
    with pytest.raises(ServiceError):
        cipher.decrypt_platform(rewritten, expected=spec, credential_id=uuid4(), secret_version=1)
    with pytest.raises(ServiceError):
        cipher.decrypt(rewritten, uuid4(), uuid4())


@pytest.mark.parametrize(
    "name", ["BID_PLATFORM_CREDENTIAL_UNKNOWN", "BID_LLM_API_KEY", "BID_PERPLEXITY_API_KEY"]
)
def test_startup_refuses_legacy_environment_names_without_values(monkeypatch, name):
    settings = config()
    monkeypatch.setenv(name, "synthetic-secret-must-not-appear")
    with pytest.raises(ServiceError) as caught:
        settings.assert_vendor_credentials_absent()
    assert caught.value.code == "credential_env_forbidden"
    assert name in str(caught.value)
    assert "synthetic-secret" not in str(caught.value)


async def test_search_explicit_selection_and_per_request_resolution():
    from app.providers.search import create_search_provider
    from app.schemas.platform_credentials import ServiceResolveTarget

    resolver = Resolver()
    target = ServiceResolveTarget(service="vendor_search", credential_id=uuid4())

    async def select(service):
        return target

    resolver.select_service = select

    async def resolve(target):
        resolver.calls += 1
        return ResolvedCredential(
            target.credential_id,
            1,
            resolver.calls,
            "perplexity",
            "https://api.perplexity.ai",
            SecretStr("synthetic-search-key-" + str(resolver.calls)),
        )

    resolver.resolve_for_call = resolve
    seen = []

    def handler(req):
        seen.append(req.headers["Authorization"])
        return httpx.Response(200, json={"results": []})

    provider = await create_search_provider(
        config(search_provider="perplexity"),
        httpx.MockTransport(handler),
        credential_resolver=resolver,
    )
    await provider.search("synthetic model")
    await provider.search("synthetic model")
    assert seen == ["Bearer synthetic-search-key-1", "Bearer synthetic-search-key-2"]
    assert str(target.credential_id) in provider.identity
    assert str(target.credential_id) not in provider.public_identity
    assert await create_search_provider(config(search_url="https://search.example.test")) is None


@pytest.mark.parametrize(
    "root_field", ["secrets_key", "secrets_key_previous", "encryption_key_previous"]
)
def test_noncanonical_fernet_alias_cannot_cross_root_domains(root_field):
    settings = config()
    raw = settings.encryption_key.get_secret_value()
    alias = raw[:10] + "\n" + raw[10:]
    overrides = {root_field: [alias] if root_field.endswith("previous") else alias}
    # Settings validation and direct cipher defense must both reject aliases.
    from pydantic import ValidationError

    values = settings.model_dump()
    values.update(overrides)
    with pytest.raises(ValidationError):
        Settings(**values)
    overrides[root_field] = (
        [SecretStr(alias)] if root_field.endswith("previous") else SecretStr(alias)
    )
    with pytest.raises(ServiceError):
        ProviderSecrets(settings.model_copy(update=overrides))


@pytest.mark.parametrize("echo_field", ["url", "title", "snippet"])
async def test_search_rejects_credential_echo_before_returning_candidates(echo_field):
    from app.providers.base import ProviderFailure
    from app.providers.search import PerplexitySearch
    from app.schemas.platform_credentials import ServiceResolveTarget

    target = ServiceResolveTarget(service="vendor_search", credential_id=uuid4())
    key = "synthetic-search-sensitive-key-1234"

    class SearchResolver:
        async def resolve_for_call(self, target):
            return ResolvedCredential(
                target.credential_id,
                1,
                1,
                "perplexity",
                "https://api.perplexity.ai",
                SecretStr(key),
            )

    item = {"url": "https://vendor.example.test/spec", "title": "synthetic", "snippet": "spec"}
    item[echo_field] = "https://vendor.example.test/" + key if echo_field == "url" else key
    provider = PerplexitySearch(
        SearchResolver(),
        target,
        httpx.MockTransport(lambda req: httpx.Response(200, json={"results": [item]})),
    )
    with pytest.raises(ProviderFailure) as caught:
        await provider.search("synthetic model")
    assert key not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("exit_code", [3, 4])
async def test_org_search_failure_projects_only_fixed_provider_error(exit_code):
    from app.providers.search import PerplexitySearch, create_search_provider
    from app.schemas.platform_credentials import ServiceResolveTarget

    target = ServiceResolveTarget(service="vendor_search", credential_id=uuid4())

    class BrokenResolver:
        async def select_service(self, service):
            raise ServiceError(
                "credential_missing", "internal platform credential identity", 503, exit_code
            )

        async def resolve_for_call(self, target):
            raise ServiceError(
                "credential_disabled", "internal platform credential identity", 503, exit_code
            )

    provider = PerplexitySearch(
        BrokenResolver(), target, httpx.MockTransport(lambda req: pytest.fail("sent"))
    )
    for operation in (
        provider.search("synthetic"),
        create_search_provider(
            config(search_provider="perplexity"), credential_resolver=BrokenResolver()
        ),
    ):
        with pytest.raises(ServiceError) as caught:
            await operation
        assert caught.value.code == "provider_unavailable"
        assert caught.value.exit_code == exit_code
        assert "credential" not in str(caught.value)
