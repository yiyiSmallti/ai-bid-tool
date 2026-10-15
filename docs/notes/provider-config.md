---
kind: reference
---

# Local provider configuration

## Usage

The installation human configures an Anthropic or OpenAI-compatible model using
its own key. Extraction and response-card drafting share `llm_extract`.
[ADR 0012](../adr/0012-single-user-installation.md) defines the identity boundary;
[the provider plan](../plan/provider-config.md) defines delivery requirements.

`bid provider set --input FILE` accepts the nonsecret `ProviderConfigInput` in
[provider_contracts.py](../../server/app/schemas/provider_contracts.py). The key
comes from `BID_PROVIDER_KEY` or `--key-file FILE`, never a CLI value argument or
input JSON field. Key files must be caller-owned regular files with mode 0600;
symlinks are refused. API writes use the excluded, write-only `api_key` field.
An omitted key can reuse an existing key only for the same provider and endpoint.
Updates require `expected_revision`.

`bid provider list` returns the current configuration and recorded month usage;
`bid provider history` returns immutable revisions. Metadata-only page reads are
defined in [model settings](model-settings.md). Read access requires
`provider:read`; save and test require the human identity and `provider:write`.

## Several models

An installation can save up to 20 models. Each model is a slot with its own
revision chain, name, provider, endpoint, and key; one model is the default.
`provider_slots` in
[0002_provider_models.py](../../server/migrations/versions/0002_provider_models.py)
records the default and removed models. Revisions saved before that migration
belong to the legacy slot, which becomes the default.

`ProviderConfigInput` with `new: true` adds a model; with `slot` it revises that
model; with neither it revises the default model. An omitted key reuses the key
of any live model with the same provider and endpoint. `bid provider default
--model SLOT` and `bid provider remove --model SLOT` change the default and
remove a model; removing the default promotes the most recently saved model.
Extraction, drafting, and provider tests accept `model`; an omitted model means
the default, and a removed or unknown model fails with
`provider_model_unavailable`. Queued jobs keep their pinned revision, so
removing a model does not strand them. The desktop settings page fills new
models from vendor templates in
[native_settings_templates.go](../../desktop/native_settings_templates.go).

## Revisions and encryption

The authoritative table, triggers, and grants are in the
[single-user baseline](../../server/migrations/baseline/).
`provider_configs` is an append-only sequence per capability. An advisory
transaction lock serializes writes, including first creation; compare-and-swap
rejects stale revisions. Runtime writes cannot update or delete history, and
human actor checks also guard inserts and provider-test job admission.

`ProviderSecrets` requires an independent Fernet `BID_SECRETS_KEY`, different
from `BID_ENCRYPTION_KEY`. Envelopes bind the key to the immutable config UUID;
decryption checks that binding. Key reuse encrypts a fresh envelope for the new
UUID. Missing keys and unreadable ciphertext fail explicitly. Responses use
allowlists and save audits contain no key material.

## Resolution and cache

`resolve_configured` in
[configured.py](../../server/app/providers/configured.py) uses the saved local
revision. An installation without one is unconfigured. Deployment adapter
settings do not select an implicit fallback model. Standalone and eval adapters
resolve their service credentials through
[service credential settings](platform-credentials.md#resolution).

Submission fixes `jobs.provider_config_id` and a nonsecret `provider_identity`.
Workers resolve that immutable revision, so later saves do not change queued
jobs. Model cache identity includes the config UUID and revision. The adapter
uses that revision's provider, model, endpoint, JSON mode, and reasoning schema;
it does not inherit deployment vendor request options or Anthropic effort.
[Reasoning levels](reasoning-levels.md) defines level selection.

## Calls and tests

Every actual call records provider, model, duration, and resource usage.
`vendor_calls` admission consumes the cumulative job call ceiling, including
split batches and failures with unknown usage. Usage persists before parsing
or citation validation. UTC-month totals describe this installation's recorded
calls and are not vendor account totals.

The test route commits a taskless `provider_test` job and key-free audit before
processing it. Admission rechecks the saved human actor and pinned configuration.
`JobExecution` and the provider layer account for calls without creating tender
records. Explicit repeated tests create separate jobs. Tests do not retry
automatically; an unresolved admitted call prevents unsafe replay. HTTP waits
hold no request database transaction. Quota exhaustion is not retried, and
vendor error bodies cannot become public metadata.

## Recovery

Historical revisions retain encrypted historical vendor keys. Root-key rotation
must preserve those bindings; see
[credential recovery](platform-credentials.md#root-key-rotation).
Repair configuration by appending a new revision, without deleting history.
Endpoints require HTTPS without embedded credentials, queries, or fragments;
deployment egress controls decide which networks calls may reach.

## Implementation

- [Schemas](../../server/app/schemas/provider_contracts.py),
  [revision model](../../server/app/models/provider_configs.py), and
  [cipher](../../server/app/core/provider_secrets.py).
- [Service](../../server/app/services/provider_configs.py),
  [HTTP routes](../../server/app/api/providers.py), and
  [CLI commands](../../cli/bid_cli/providers.py).
- [API/processor acceptance](../../server/tests/test_provider_config.py) and
  [CLI boundaries](../../server/tests/test_provider_client.py).
