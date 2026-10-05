# Organization provider configuration

## Problem

An organization can pay a vendor directly or choose a paid platform model. Both
extraction and card drafting must use that choice without exposing credentials,
changing queued work, or bypassing the shared vendor-call budget and accounting.
The delivery boundary and resolved decisions are in the
[provider contract](../plan/provider-config.md).

## Usage

The API and worker require the same independent Fernet `BID_SECRETS_KEY` for
organization credentials. It must differ from `BID_ENCRYPTION_KEY`; absence or
invalid ciphertext is an explicit error when a credential is needed. Deployment
variables are declared in [deploy/.env.example](../../deploy/.env.example).

`bid provider set --input FILE` accepts the non-secret `ProviderConfigInput` from
[provider_contracts.py](../../server/app/schemas/provider_contracts.py). The key
comes from `BID_PROVIDER_KEY` or `--key-file FILE`, never a CLI value argument or
JSON field. Key files must be regular files owned by the caller with mode 0600;
symlinks are refused. API callers send the write-only `api_key` field separately
in `ProviderConfigSet`. Omission reuses the existing key only for the same vendor
and endpoint. Update inputs require `expected_revision`.

`bid provider list` shows the active revision, selectable catalog entries and
month usage. `bid provider history` returns revisions without probing historical
keys. `bid provider test --capability llm_extract --reasoning LEVEL` waits for a
single synthetic-page call and reports its job ID, usage, cost and optional
balance. Only human org admins can set or test; every member can read with
`provider:read`. Catalog responses omit credential names, endpoints and wholesale
prices. API response keys remain the Result contract in
[contracts.py](../../server/app/schemas/contracts.py).

## How it works

### Revisions and authority

[0020_provider_configs.py](../../server/migrations/versions/0020_provider_configs.py)
creates `provider_configs`, an append-only tenant table with forced RLS and a
unique `(org_id, capability, revision)`. The latest revision is active. A
transaction advisory lock serializes writes, including first creation, and the
trigger checks the next revision number. The trigger also checks the current
human actor, active user, active admin membership and active organization.
Runtime grants allow SELECT and INSERT only; UPDATE and DELETE are rejected.
Platform selections reference catalog entries; tenant job and usage references
use composite `(org_id, provider_config_id)` foreign keys.

`ProviderSecrets` encrypts an envelope containing org ID, revision ID and the key
with the dedicated key. Decryption checks both IDs, so a copied ciphertext cannot
be rebound to another org or revision. Reusing a key produces a fresh envelope.
Views are explicit allowlists; ciphertext and raw keys are never serialized.
The service and database forbid `provider:write` on API tokens. Audit rows contain
revision, source and object IDs, excluding key and endpoint input.

### Resolution and cache

`resolve_configured` in
[configured.py](../../server/app/providers/configured.py) resolves the latest org
revision, then an enabled platform default, then `DisabledLLM`. There is no
runtime fallback to environment-selected models. Explicit adapter injection is
reserved for tests. Standalone adapters and evals use only non-secret `BID_LLM_*`
settings; credential resolution follows [platform credential authority](platform-credentials.md#how-it-works).

The org adapter replaces provider, model, endpoint, JSON mode, cost prices and
reasoning options. It does not inherit deployment vendor request options or
Anthropic effort. Official levels use `ReasoningLevel` and `with_reasoning` from
[reasoning-levels.md](reasoning-levels.md). Output-limit validation still applies.

Submission stores `jobs.provider_config_id` and a non-secret `provider_identity`.
Worker resolution uses that immutable revision, even after the org changes its
active choice. A platform selection also fixes catalog ID and revision; a changed
or disabled catalog entry stops queued work instead of silently calling another
model. The version used by extraction and drafting caches includes the config
revision identity. Jobs created before this mechanism lack the new identity and
cannot adopt a newly configured org revision: they stop and require a fresh
submission. With no org revision they retain their platform-default resolution;
already fixed drafting manifests retain their existing identity checks.

### Calls, probes and billing

Every org-key response with valid vendor usage stores `provider_config_id`, token
counts and estimated vendor USD cost, while `platform_model_id` is null and
`charge` is zero. Platform selections keep both attribution IDs and existing
catalog prices. `platform_usage_summary` groups these calls under `org`,
`platform`, or legacy `unbilled` without granting the platform function role
access to configuration rows or credentials.

Org-key calls reserve zero platform charge and skip prepaid balance (预付余额) checks.
They still create `vendor_calls` and consume the job's cumulative call ceiling,
including split batches, retries and failures with unknown usage. Settlement
occurs before parsing or citation validation. The common admission and settlement
rules are defined in [prepaid-billing.md](prepaid-billing.md).

The synchronous test route commits a `provider_test` job and its audit before
calling Processor. Such jobs alone may have null task/document references;
ordinary job bindings remain required. Taskless usage is accepted only for these
jobs, with matching config attribution. The processor uses JobExecution and a
single adapter call, rechecking the submitting human admin at admission. No task,
document, chunk or requirement is created. Tests do not automatically retry;
explicit repeated tests create separate accounted jobs. HTTP waits hold no
request database transaction. Job model identity is immutable; usage must match
its job's configuration and task.

### Quota and balance

The HTTP error mapper preserves only a recognized error classification and a
reset timestamp. `provider_quota_exhausted` is not retried. Org-key errors direct
the organization administrator to recharge or renew with the vendor; platform
errors retain the system-administrator guidance. Drafting partial results include
that guidance in warnings. Credential echoes, including model-name echoes, are
excluded from usage metadata and cannot become output.

[balance.py](../../server/app/providers/balance.py) supports DeepSeek's
[official user balance endpoint](https://api-docs.deepseek.com/api/get-user-balance/).
It uses the fixed HTTPS URL `https://api.deepseek.com/user/balance`, only for an
OpenAI-compatible config pointing to the official host at `/` or `/v1`, with no
redirects and a five-second total deadline. It validates `is_available` and every
currency/balance entry. Other vendors report `unsupported`; HTTP, timeout,
credential or malformed-response failures report `unavailable` without disclosing
vendor text. Balance reads do not generate LLM usage or block model jobs.

Usage views aggregate this system's recorded calls from the start of the UTC
month, both per revision and for the org. Missing vendor prices produce null USD
and an unpriced-call count. These totals are not a vendor-wide consumption report.

## Pitfalls

- Keeping historical revisions also keeps encrypted historical keys. Removing or
  rotating `BID_SECRETS_KEY` without re-encrypting revisions makes queued work
  unreadable. No key rotation or revision deletion operation is provided.
- Org base URLs must be HTTPS without embedded credentials, query or fragment.
  Deployment egress controls remain responsible for which networks the API and
  worker may reach; URL syntax validation is not network isolation.
- A platform model is selected by catalog ID, not copied into an independently
  editable tenant model. History shows the selection-time display fields; the
  enabled catalog in the list gives its offered prices and reasoning choices.
- Synchronous probes are durable but not dispatched to the background queue.
  A process interruption can leave an unresolved admitted call; its accounting
  reservation follows the same manual reconciliation rule as other model jobs.
- Optional vendor cost prices are estimates in USD. They do not set a monthly
  quota or limit what the organization can spend directly with the vendor.
- Requests against org configuration must retain tenant context. Platform
  summaries expose only usage aggregates, never configuration or key material.

## Code

- [provider_contracts.py](../../server/app/schemas/provider_contracts.py): validated inputs and safe views.
- [models/provider_configs.py](../../server/app/models/provider_configs.py): revision records.
- [core/provider_secrets.py](../../server/app/core/provider_secrets.py): encryption envelope and key separation.
- [providers/configured.py](../../server/app/providers/configured.py): resolution and model identity.
- [services/provider_configs.py](../../server/app/services/provider_configs.py): revisions, permissions, usage and probe processing.
- [api/providers.py](../../server/app/api/providers.py), [cli/providers.py](../../cli/bid_cli/providers.py): API and CLI entry points.
- [test_provider_config.py](../../server/tests/test_provider_config.py): API/processor, tenant and database gates; artifact under `data/work/provider-validation/`.
- [test_provider_client.py](../../server/tests/test_provider_client.py): CLI snapshots and mock HTTP/encryption boundaries.
