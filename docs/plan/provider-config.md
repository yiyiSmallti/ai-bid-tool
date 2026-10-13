---
kind: plan
---

# Local provider configuration contract

## Delivery boundary

Implement the model-settings boundary of
[ADR 0012](../adr/0012-single-user-installation.md): the human configures its own
Anthropic or OpenAI-compatible key for `llm_extract`. Extraction and response-card
drafting share immutable revisions, pinned job identity, and accounted calls.
[Provider configuration](../notes/provider-config.md) owns the mechanism;
[model settings](../notes/model-settings.md) owns metadata projection and paging.

## Contract

Authoritative inputs are `ProviderConfigInput`, `ProviderConfigSet`, and
`ProviderTest` in [provider_contracts.py](../../server/app/schemas/provider_contracts.py).
Provider and model are required. Key-free CLI JSON supplies endpoint, JSON mode,
and reasoning schema; the write-only API key is supplied separately. Every
command returns the seven-field Result envelope.

| Command | Route | Authority and behavior |
| --- | --- | --- |
| `provider list` | `GET /providers` | `provider:read`; effective config and recorded month usage |
| `provider history` | `GET /providers?history=true` | `provider:read`; immutable revision projections |
| `provider show` | `GET /management/providers` | `provider:read`; metadata without decryption |
| `provider revision show` | `GET /management/providers/revisions/{id}` | `provider:read`; saved revision identity |
| `provider history-page` | `GET /management/providers/history` | `provider:read`; bounded keyset page |
| `provider set` | `POST /providers` | Human `provider:write`; append revision with compare-and-swap |
| `provider test` | `POST /providers/test` | Human `provider:write`; accounted taskless test job |

## Requirements

- The latest saved revision is effective. Without one, return unconfigured.
- Require a key for first creation or a changed provider or endpoint. Omitted
  keys may be re-encrypted only for the unchanged provider and endpoint.
- Bind key encryption and queued model identity to the immutable config UUID.
- Preserve revision history; reject runtime UPDATE/DELETE and nonhuman writes.
- Resolve queued work against its saved revision and include that identity in
  extraction/drafting cache keys.
- Keep preflight cost estimates separate from actual test receipts. Every explicit
  test is a new admitted/accounted job and creates no tender data.
- Record only key-free audits, safe model identity, and actual resource usage.

## Acceptance

Existing [API/processor tests](../../server/tests/test_provider_config.py),
[metadata API tests](../../server/tests/test_model_settings_api.py), and
[CLI tests](../../server/tests/test_provider_client.py) must verify human/token
boundaries, stale-write conflicts, immutable history, pinned queued revisions,
key reuse and encryption binding, output/error redaction, accounted tests, quota
behavior, and unconfigured resolution. The
[scale scenario](../../server/tests/test_model_settings_scale.py) measures bounded
read work, complete-envelope byte limits, and warm latency against PostgreSQL.
Acceptance artifacts belong under `data/work`, never under `docs`.
