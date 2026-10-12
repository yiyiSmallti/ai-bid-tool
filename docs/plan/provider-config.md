---
kind: plan
---

# Contract for org BYOK models and platform model selection

Status: **backend, CLI and llm_extract org model-settings page implemented; integrated page acceptance pending.** The org (organization/tenant, 单位) page follows the [management-page contract](management-pages.md#provider-and-model-configuration); see [model settings](../notes/model-settings.md). See [provider-config.md](../notes/provider-config.md) for the mechanism; this page records scope, decisions, and acceptance requirements.

## Delivery scope

- Org administrators select a platform catalog model or configure a BYOK Anthropic/OpenAI-compatible model.
- Extraction and response card (响应卡) drafting share `llm_extract` configuration, admission, and immediate usage accounting.
- Configuration uses immutable revisions and `expected_revision` to prevent concurrent overwrites while retaining historical job references.
- The server encrypts keys with independent `BID_SECRETS_KEY`; responses show only the last four characters. CLI reads keys from `BID_PROVIDER_KEY` or a current-user-owned `--key-file` with mode 0600.
- `provider set/list/history/test` API and CLI; tests use synthetic connection-check content without tender documents (招标文件).

Exclude task-level model overrides, OCR/vision/search configuration, key-rotation tools, .

## Inputs and outputs

The authoritative models are `ProviderConfigInput`, `ProviderConfigSet`, `ProviderConfigView`, and `ProviderTest` in [provider_contracts.py](../../server/app/schemas/provider_contracts.py). CLI JSON files use `ProviderConfigInput` and cannot contain keys. API writes keys through `ProviderConfigSet.api_key`, which has no output representation.

| Command | Route | Permission and behavior |
| --- | --- | --- |
| `bid provider list` | `GET /providers` | All roles with provider:read; current configuration, catalog and recorded month usage. |
| `bid provider history` | `GET /providers?history=true` | All roles with provider:read; immutable historical metadata without provider requests. |
| `bid provider set --input FILE [--key-file FILE]` | `POST /providers` | Human admin `provider:write`; append a revision; conflicts return 409 |
| `bid provider test --capability llm_extract [--reasoning LEVEL]` | `POST /providers/test` | Human admin `provider:write`; perform one admitted/accounted probe call and wait for the result |

All commands retain Result's seven-field structure and register in `bid schema`.
Insufficient permissions return 403; cross-org identities and objects remain uniformly invisible.

## Decisions

| Question | Decision |
| --- | --- |
| Model resolution order | Latest org revision → enabled platform-default model → unconfigured; API/worker no longer use `BID_LLM_*` as implicit model fallback; standalone adapter/eval settings were permitted by this contract, then superseded by [ADR 0006](../adr/0006-platform-credentials.md) |
| Capability name | Keep `llm_extract` for both extraction and model card drafting; no separate drafting configuration |
| Reasoning levels | Reuse platform `ReasoningLevel` and request-option validation; default level must exist; adapter output limits cannot be overridden |
| Job binding time | Fix configuration revision/model identity at submission; workers resolve that revision. Org configuration changes do not change queued jobs; catalog revision changes explicitly fail old jobs and require resubmission |
| Old cache | Model version includes configuration revision identity; new configurations create new extraction/drafting caches; old jobs retain old references |
| Omitted key | Only BYOK updates with the same provider and base URL can reuse and re-encrypt the old key. First configuration, provider changes, and endpoint changes require a key |
| Effective configuration | Latest revision per capability is effective; selecting a platform model also creates an org revision. No history deletion or reset to automatically follow platform defaults |
| Test jobs | `provider_test` has no task/document, still uses Processor/JobExecution; no retries or business material; every explicit test is a new job |
| Monthly usage | UTC month; aggregate recorded calls, tokens, pages and requests by configuration and org. No vendor-wide consumption claim. |
| Historical credentials | Ciphertext binds org/revision; retain keys needed for historical calls. Losing the independent encryption key fails explicitly, without another key/model fallback |

## Data and permission boundaries

The configuration migration is [0020_provider_configs.py](../../server/migrations/versions/0020_provider_configs.py), with `down_revision="0019"`. See the [mechanism note](../notes/provider-config.md#how-it-works) for migration details and code locations.

All existing roles may use `provider:read`, and API tokens may receive it. Only human admins receive `provider:write`; both token issuance and database CHECK constraints forbid it for tokens. Database triggers also verify human-admin context for configuration writes and probe-job creation. Revisions cannot be updated/deleted. Configuration and tests both write key-free audit entries.

## Acceptance

[API/processor gate tests](../../server/tests/test_provider_config.py) and [CLI/external HTTP boundary tests](../../server/tests/test_provider_client.py) cover:

1. Two-org isolation for configuration, history, jobs, and usage; no-context reads return nothing; cross-org references fail.
2. BYOK, explicit platform selection, platform default, and unconfigured resolution; extraction/drafting share configuration.
3. Human admins, other roles, and token permissions; the database rejects illegal configuration writes and token scopes.
4. Revision conflicts, immutable history, queued jobs bound to old revisions, and new caches after updates.
5. Encryption/context binding, private CLI files, and no plaintext keys in outputs/errors.
6. No retry for quota errors; guidance follows the configured provider.

Migration, FORCE RLS, and the database flow from API to processor require the maintainer's isolated PostgreSQL test instance. Nondatabase checks cannot replace these acceptance checks; the original implementation report recorded a sandbox connection limitation rather than a database-test pass.
