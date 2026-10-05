---
kind: plan
---

# Manage service credentials in the platform console

Status: **approved and implemented; all recommended defaults accepted.** Covers F10/P04 in the [roadmap](roadmap.md). Deployment cutover requires the acceptance gates below.

[agent.md](../../agent.md#workflow) requires confirmation of Pydantic models, Provider interfaces, and CLI JSON first. [Approved contract types](platform-credentials/platform_credentials_contracts.py) define inputs, outputs, and internal interfaces; runtime types live in [platform_credentials.py](../../server/app/schemas/platform_credentials.py). Accepted [ADR 0006](../adr/0006-platform-credentials.md) owns the global-table exception, trust boundary, and tradeoffs. See [Platform credential authority](../notes/platform-credentials.md) for implementation entry points/mechanisms.

## Goal and scope

After platform TOTP login, operators create/enable credentials at `/app/platform/credentials`, select matching credentials for catalog models, inspect consumers, replace keys, disable, remove, and test authentication. API, worker, and standalone/eval share database authority without editing vendor-key env files. Org (organization/tenant, 单位) jobs retain Provider selection, model versions, cost admission, and RLS; no org-facing platform-key read interface.

Exact migration scope follows. Unlisted vendor capabilities require later contracts; arbitrary secret names are not accepted automatically.

| Configuration / existing entry | Ownership and integration |
| --- | --- |
| `BID_PLATFORM_CREDENTIAL_<NAME>`; [llm.py](../../server/app/providers/llm.py) `credential_value/platform_llm` | Database, `purpose=catalog_llm`; retain `PlatformModel.credential` names/case mapping |
| `BID_PERPLEXITY_API_KEY`; [search.py](../../server/app/providers/search.py) `create_search_provider` | Database, `purpose=vendor_search`; fixed Perplexity endpoint, one nonremoved service credential |
| `BID_LLM_API_KEY`; [evals/extract_tender.py](../../evals/extract_tender.py) `settings/run`, `create_llm` | Database, `purpose=standalone_llm`; standalone tools use restricted service connection, never real keys from env/Settings. Provider/model/reasoning stay nonsecret; match credential provider/endpoint |
| Org `ProviderConfig.encrypted_key`; [provider-config.md](../notes/provider-config.md) | Existing org permissions/immutable revisions; add restricted rewrapping only for shared encryption root, not global-table migration |
| `BID_DATABASE_URL`, `BID_MIGRATION_DATABASE_URL`, `BID_DATABASE_PASSWORD`, `BID_OWNER_PASSWORD`, `BID_BOOTSTRAP_PASSWORD` | Deployment injection; new `BID_PLATFORM_DATABASE_URL`/`BID_CREDENTIAL_DATABASE_URL` are bootstrap secrets, never stored in the database they access |
| `BID_ENCRYPTION_KEY`, `BID_ENCRYPTION_KEY_PREVIOUS`, `BID_TOKEN_KEY`, `BID_SECRETS_KEY`, `BID_SECRETS_KEY_PREVIOUS`, `BID_CLI_KEY` | External injection; root/retired keys excluded from credential table |
| `BID_PLATFORM_TOTP_SECRETS`, `BID_PLATFORM_ADMIN_EMAILS` | Deployment-controlled identity trust roots; application cannot add operators |
| `BID_S3_ACCESS_KEY`, `BID_S3_SECRET_KEY`, MinIO root identity | Initially deployment secrets; [storage.py](../../server/app/providers/storage.py) `S3Storage` creates clients at startup; Compose also uses these to start MinIO. Separate migration needs a contract |
| `BID_SANDBOX_TLS_KEY/CERT/CA`, both SHA256 pins; `SEARXNG_SECRET` | Sandbox/SearXNG infrastructure trust configuration; no private-key files collected in this table |
| `BID_SEARCH_URL`, `BID_LLM_BASE_URL`, S3 endpoint/bucket, conversion/OCR parameters | Nonsecret; reject authentication in URLs. SearXNG/Gotenberg/local OCR have no outbound API keys in scope |

Previously, `platform_llm` allowed anonymous OpenAI calls at custom endpoints without keys. Catalog models now require valid credentials; missing/disabled is not anonymous permission. Anonymous local models need a separate explicit contract. Remove env fallback from every real call path. Tests may explicitly inject fake Providers but cannot register a production bypass.

## Existing mechanisms and required integration

This table records the preimplementation differences that the contract requires resolving.

| Source | Required integration |
| --- | --- |
| [ProviderSecrets](../../server/app/core/provider_secrets.py) | Reuse Fernet/SecretStr/identity binding/explicit failure; add platform domain without fake org UUIDs. Original mechanism had one secrets key and no rotation |
| [admin.py](../../server/app/admin.py) `rotate_encryption/ENCRYPTED_COLUMNS` | Original rotation covered only `BID_ENCRYPTION_KEY`; do not add credentials to data-domain columns. BYOK UPDATE trigger needs dedicated maintenance |
| [platform.py](../../server/app/services/platform.py) `identify/model_view/test_model/audit_entries` | Reuse platform session; query safe DB state for `credential_configured`; strict write-side audit allowlist because details are returned unchanged |
| [configured.py](../../server/app/providers/configured.py) `resolve_configured/model_identity` | Preserve BYOK revision/catalog ID/revision job binding; replacement does not increment catalog revision |
| [llm.py](../../server/app/providers/llm.py) `HTTPExtractor`, [structured.py](../../server/app/providers/structured.py), [prototyping.py](../../server/app/providers/prototyping.py) | Replace job-long adapter key ownership with resolution before every HTTP call: extraction/drafting/check/score/Vision/prototypes/retries |
| [screenshots.py](../../server/app/api/screenshots.py) `search_provider`, [processor.py](../../server/app/jobs/processor.py), [product_simulation.py](../../server/app/services/product_simulation.py) | Unified search prechecks/jobs/simulated proposals (模拟拟投); precheck metadata only, no decryption |
| [prepaid-billing.md](../notes/prepaid-billing.md#admission-and-the-spending-bound) | Credential checks before each outbound call, preserving `JobExecution.admit/accounted_call`, UsageRecord, settlement |
| [providers.py](../../cli/bid_cli/providers.py), [client.py](../../cli/bid_cli/client.py) | Reuse secure key-file reads/`platform=True` sessions, no X-Org-Id; no vendor-key env input for ordinary CLI |

## Data and migration outline

### Table and constraints

Add only global `platform_credentials`, not binding/history/platform Job/global usage tables. [CredentialSpec/View](platform-credentials/platform_credentials_contracts.py) defines relationships/values.

| Column | Constraint / meaning |
| --- | --- |
| `id uuid`, `name varchar(40)` | Primary key; UNIQUE name `^[a-z0-9_]{1,40}$`, immutable/nonreusable even after tombstone |
| `purpose`, `provider`, `endpoint` | Immutable: purpose catalog_llm / standalone_llm / vendor_search; provider anthropic / openai / perplexity. Normalize HTTPS base URL, no userinfo/query/fragment, default port only |
| `encrypted_key text`, `envelope_version` | Ciphertext required except removed, where NULL required. ADR identity-bound envelope; no plaintext `api_key` column |
| `fingerprint`, `last_four` | First 16 hex characters of key SHA-256 prefixed `sha256:`, last four ASCII characters. Visual identification only, not authorization/uniqueness/deduplication. Key: 16–4096 nonwhitespace printable ASCII characters; no password/short PIN |
| `state`, `revision bigint`, `secret_version bigint` | active / disabled / removed. revision starts 1, +1 per business change; secret_version +1 only for key replacement. Root rewrapping changes neither |
| `created_at`, `updated_at`, `updated_by` | Timezone-aware time and verified platform actor email; no client-supplied values |

For noncatalog purposes, a partial unique index on purpose where `state != removed` ensures one nonremoved credential per service; disabled occupies the slot. After removal, new name/ID may replace it, but old jobs never rebind automatically. Catalog credentials can serve multiple models; add `platform_models.credential` foreign key to name and reject physical deletion. Catalog writes/resolutions check matching purpose/provider/normalized endpoint. Normalize empty Anthropic base_url to the existing adapter's official default. Endpoint changes need a new credential and explicit catalog update; never send old-key headers to a new service by editing an address.

State machine: create defaults disabled, optionally active; replace retains state; set-active toggles active/disabled; remove enters terminal removed and clears ciphertext. All changes require expected_revision, lock the row, then compare; conflicts 409. Removed cannot replace/enable. Same-state requests retain revision and record no-change audit. Disable/remove may retain catalog references, returning an impact list; later calls fail explicitly, without model switching or usage deletion. Enable verifies decryptability; connection testing is not mandatory.

### Database roles and functions

| Role / connection | Allowed access |
| --- | --- |
| `bid_app` (ordinary org API/worker transactions), existing `bid_platform_fn` | No new-table SELECT/INSERT/UPDATE/DELETE, function EXECUTE, or new-role membership; org RLS unchanged |
| `bid_platform_credentials_fn`, NOLOGIN/NOSUPERUSER/NOBYPASSRLS/NOINHERIT | Fixed-function owner; SELECT/INSERT/UPDATE on new table, limited consumer-reference columns on platform_models, INSERT/limited rate-limit/probe reads on platform_audit_logs; no org tables or DELETE |
| `bid_platform_app`, LOGIN/NOSUPERUSER/NOBYPASSRLS/NOINHERIT | Dedicated `BID_PLATFORM_DATABASE_URL` pool; list/show/create/replace/set-active/remove/import, probe-begin/finish, metadata-access audit functions only; no ciphertext-returning functions/org tables |
| `bid_credential_reader`, LOGIN/NOSUPERUSER/NOBYPASSRLS/NOINHERIT | Dedicated `BID_CREDENTIAL_DATABASE_URL` pool; consumer-bound readiness metadata and resolve-catalog/service/probe/operator-check single-ciphertext functions; no enumeration/writes/general plaintext API |
| Migration owner | Tables/grants/rewrapping; never API/worker request pools |

Revoke PUBLIC EXECUTE; fixed `search_path=pg_catalog`, fully qualified tables, no dynamic SQL/schema CREATE/role management. Catalog resolver accepts catalog ID/expected_model_revision and reads name from trusted catalog, never org HTTP name parameters. Service resolver accepts a fixed service enum and server-selected credential_id; probe resolver only an unfinished, unexpired, revision-bound probe_id. resolve-operator-check accepts target UUID/expected_revision/import-or-activate purpose through verified platform service paths only; disabled allowed, removed not. No HTTP/CLI registration. It relies on resolver connection/application authorization, not database TOTP verification; dry-run may compare read-only without audit. Resolver role holders are trusted services, not users; decrypt only one explicit target. Ordinary management pool cannot return ciphertext; session authorization precedes internal resolution for checks/probes.

Construct platform operators only through `platform.identify`, never body/email fields. Reject org role/token requests before dedicated pools. Functions cannot verify web TOTP or treat actor GUC as authentication; dedicated EXECUTE-capable connections are trusted, actor is audit attribution only.

### Migration and deployment order

1. Include approved exception in `agent.md` hard rule 1; migration `0038_platform_credentials.py` follows memory migration `0037`. Create tables/roles/functions/audit constraints, deny all ungranted access.
2. Initially create catalog-name foreign key NOT VALID; new writes still validate. Existing same-name/provider-or-endpoint mismatches are migration conflicts requiring explicit separation, not automatic key authorization across endpoints.
3. Maintenance window: stop relevant paid submissions and drain in-flight work; prepare consistent API/worker/standalone versions, roots, dedicated connections. Old env-reading workers and new console cannot coexist because disable would be ineffective. This plan performs no service operations.
4. Use existing bootstrap login to start operator-only new version without old vendor-key env. Explicitly import protected old environment export; this temporary file is not long-term authority. Check references/states, validate FK, then reopen tasks. Resolve old anonymous catalog entries explicitly; do not fabricate placeholder ciphertext for missing keys.
5. Update templates/guides/real injection source; remove three legacy key classes, explicitly select search provider, verify every replica rejects old env. Check key-free business configuration and fake-provider E2E before traffic cutover.
6. No destructive downgrade. Preserve tables/audit/roots and repair forward in maintenance mode. Returning to env-based versions violates disable/remove states. Disaster recovery rebuilds from retained DB/root backups and rechecks vendor revocation. Never store secret backups in docs/validation artifacts.

## API and CLI contract

Use existing unversioned `/platform/*`, platform `operator` dependency, no org_id. Nonplatform sessions (org admins/expired/API tokens) return `invalid_session` / 401; missing IDs for authenticated operators return `not_found` / 404. No plaintext-view/export/ciphertext-download route.

| HTTP / Result.command | Request | Successful data / items |
| --- | --- | --- |
| `GET /platform/credentials` / `platform credential list` | `CredentialListQuery`; ascending name cursor | `CredentialListData` / `CredentialView[]` |
| `GET /platform/credentials/{id}` / `platform credential show` | UUID | `CredentialData` / `[]`, catalog/service consumers |
| `POST /platform/credentials` / `platform credential create` | `CredentialCreate` | `CredentialData` / `[]` |
| `POST /platform/credentials/{id}/replace` / `platform credential replace` | `CredentialReplace` | `CredentialData` / `[]` |
| `POST /platform/credentials/{id}/active` / `platform credential set-active` | `CredentialSetActive` | `CredentialData` / `[]` |
| `POST /platform/credentials/{id}/remove` / `platform credential remove` | `CredentialRemove` | tombstone `CredentialData` / `[]` |
| `POST /platform/credentials/{id}/test` / `platform credential test` | `CredentialTest` | `CredentialProbeData` / `[]`; safe probe result even on failure |
| `POST /platform/credentials/import-env` / `platform credential import-env` | `CredentialImportRequest`; <=100 entries, body<=512 KiB | `CredentialImportData` / `CredentialImportItem[]` |

Register fixed import-env before UUID routes. Create/change successes HTTP 200; list/detail successes HTTP 200; no sensitive Location information. Add `Cache-Control: no-store`. Existing model outputs retain `credential`/`credential_configured`; the latter means active reference/ciphertext present, not successful decryption/network/vendor authorization. Saves add DB reference checks; listing never decrypts.

Commands all support `--json`, no prompts, no new vendor keys through argv/JSON configuration/env:

```text
bid platform credential list [--state STATE] [--purpose PURPOSE] [--after-name NAME] [--limit 100] --json
bid platform credential show --id UUID --json
bid platform credential create --input METADATA.json --key-file FILE --json
bid platform credential replace --id UUID --expected-revision N --reason REASON --key-file FILE --json
bid platform credential set-active --id UUID --expected-revision N --active|--inactive --reason REASON --json
bid platform credential remove --id UUID --expected-revision N --reason REASON --json
bid platform credential test --id UUID --expected-revision N --json
bid platform credential import-env --manifest MANIFEST.json --env-file FILE [--dry-run] --json
```

Create metadata uses CredentialCreateInput without api_key; the command reads key-file to construct CredentialCreate. Require caller ownership, 0600, regular files, no file/parent symlinks, bounded size, BYOK secure-open pattern. Remove at most one trailing newline, never silently strip key whitespace. No `--key VALUE`/`BID_PROVIDER_KEY` for ordinary commands. API api_key is the only one-time secret field. SecretStr/`exclude=True` prevents generic model_dump leaks; extract only in dedicated transport construction. Errors expose no Pydantic input/context, file contents, argv/session. HTTP/proxy logs cannot capture bodies.

Reuse `bid platform login`/encrypted CLI session, `platform=True`, no org header. Local mode also requires explicit login/PostgreSQL boundary; no owner URL/local-flag TOTP bypass. Register commands/inputs/outputs/errors in [schema.py](../../cli/bid_cli/schema.py).

### Result, errors, and exit codes

Reuse [Result/Cost](../../server/app/schemas/contracts.py) seven keys/published version without changing existing field types. Commands are additive. No api_key/ciphertext/envelope/auth headers in data/items/warnings. Use `data.error = {code,message,exit_code}` with fixed server messages. Existing Client.request non-2xx ServiceError drops extra data.probe; credential transport must validate/retain CredentialErrorData safe probe projections in the same seven-key Result. Never pass arbitrary provider/HTTP bodies or change other commands' error projections. Management/auth-metadata probes have zero LLM/OCR usage, `cost.usd=0.0`; billed endpoints cannot masquerade as free probes. duration_ms is measured.

```json
{
  "ok": false,
  "command": "platform credential test",
  "data": {
    "error": {"code": "credential_removed", "message": "Credential has been removed", "exit_code": 4}
  },
  "items": [],
  "warnings": [],
  "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0},
  "duration_ms": 1
}
```

Details use `data.credential: CredentialView`; lists use items. Contract `RESULT_EXAMPLES` builds complete secret-free examples. Probe failures may include redacted data.probe, never remote response content.

| Condition / code | HTTP / exit | Meaning |
| --- | --- | --- |
| Success/import dry-run/same-value replay | 200 / 0 | Dry-run: no records/audit/network probes |
| Missing/invalid key/file/endpoint/unknown fields `invalid_input` | 422 (no HTTP for local CLI) / 2 | Correct without echoing values |
| `revision_conflict`, name/purpose/import conflict, `credential_reference_mismatch` | 409 / 2 | Refresh/fix binding, no overwrite |
| `invalid_session` / `not_found` | 401 / 4; 404 / 4 | No ID-existence disclosure to unauthorized users |
| `credential_missing/disabled/removed` | 409 / 4 | No fallback; org jobs use existing provider_unavailable without global name/fingerprint |
| `credential_unreadable` | 503 / 4 | Binding/ciphertext/root error needs repair, not network retry |
| `credential_backend_unavailable`, `credential_audit_unavailable` | 503 / 3 | No outbound/false save; reread revision if commit outcome unknown |
| `credential_probe_auth_failed` | 422 / 4 | Vendor rejects authentication; no alternate key |
| `credential_probe_unsupported`, `credential_probe_endpoint_rejected` | 422 / 4 | No safe probe/outbound policy violation; no request |
| `credential_probe_timeout/unavailable/interrupted` | 503 / 3 | Authentication unproved; explicit operator retry |
| `credential_probe_rate_limited` | 429 / 3 | Only safe bounded retry_after_seconds, no vendor header |
| `credential_env_forbidden` | Startup/standalone exit 4 | Variable names only, no values; not ready |
| Partial success | No exit 5 in credential API/CLI | Changes/import batches atomic; admin root rewrap may report separate exit 5 |

## Console pages

Reuse platform area/session in [App.vue](../../web/src/App.vue)/[router.js](../../web/src/router.js), add “凭据” navigation, no org entry. Hidden routes are not API authorization.

| Page / action | Content and behavior |
| --- | --- |
| `/app/platform/credentials` | name/purpose/provider/state/fingerprint/last-four/update time/consumer count; purpose/state filters/cursor paging; “已配置” is not “连接已验证” |
| `/app/platform/credentials/new` | Name/purpose/provider/endpoint, one-time password input, disabled default; explain enable/immutable-name impact |
| `/app/platform/credentials/:id` | Safe metadata/catalog ID/revision/enabled/default/fixed service; link model pages; no org/task/BYOK |
| Replace dialog | New value/reason code/expected_revision only; no old prefill; show consumers; clear after success; refresh after conflict |
| Enable/disable/remove | Same-revision impact list; disable blocks next resolution; terminal removal erases ciphertext. Confirm concrete impact in that action, no extra approval chain |
| Connection test | Auth metadata only; tested_revision/secret_version/result/latency; concurrent updates label “旧版本结果”; no raw output or unsupported-as-pass |
| Catalog edit | Select active matching provider/endpoint credentials; broken references show unavailable/manage link; no default-model switch |

Never store keys in browser session/localStorage/URL/errors/analytics/drafts/downloads. Only input and one TLS request to this site briefly hold plaintext; clear on save/cancel/navigation. Render fingerprint/last_four as text, never v-html. No production screenshots/HAR with input values.

## Resolution, cache, and job boundaries

1. Prioritize fixed org ProviderConfig revision. source=org decrypts original org/config envelope only; failure has no platform fallback. Explicit platform selection uses fixed catalog ID/revision; no org config uses enabled platform default, otherwise DisabledLLM. No env model substitute.
2. Submit/precheck/display query only references/state and save nonsecret identities. Catalog revision fixes name; search/standalone fix credential_id/provider/endpoint. Old search jobs lacking this field require resubmission, not inferred binding.
3. `PlatformCredentialResolver.resolve_for_call` reads latest committed row through dedicated connection, verifies consumer/state/envelope, returns transient ResolvedCredential. Preserve billing admission: invalid credentials mean no HTTP/vendor call. If a reservation exists but nothing sent, close/release under provably-unsent rules, not unknown charges. Recheck state after admission wait, then construct headers. Never hold DB transaction/row lock during remote HTTP.
4. Resolve anew for every retry/batch/structured/search call. Shared adapters retain references only, no secrets in Settings copies/default headers/long-lived httpx/boto clients/job parameters. Cache neither plaintext nor missing/disabled across calls; initially no Redis/TTL/invalidation notifications.
5. Read-committed resolution is the effective boundary: resolutions starting after update commit see new state; old resolved in-flight calls may finish. No guarantee of immediate remote-call cancellation after commit. Internal metered-call diagnostics may record credential_id/secret_version; org outputs keep existing authorized projections without platform metadata.
6. Key replacement preserves catalog revision/provider_identity/cache keys; queued old-catalog jobs use new keys next call. Changing catalog binding/service ID changes identity, fails old jobs, and requires resubmission. Disable/remove blocks new calls, not authorized completed-cache reads, which perform no outbound calls.

Add nonsecret `BID_SEARCH_PROVIDER=perplexity|searxng|disabled`, default disabled; migration manifest requires explicit selection. Perplexity resolves the sole nonremoved vendor_search credential; SearXNG uses BID_SEARCH_URL without platform decryption. Missing/disabled Perplexity never falls back to SearXNG. standalone disabled makes no call; enabled resolves only standalone_llm, matching DB identity/nonsecret model settings. Database-free standalone online calls cease; fake/offline tests remain. Search charges remain an [open roadmap decision](roadmap.md#open-decisions); no passing costs to orgs or billing-rule changes here.

## Connection testing and audit

Probes use saved credentials only; disabled allowed, removed not. expected_revision binds tests. probe-begin validates revision and audits first; resolve-probe permits that authorized revision only. Replacement before resolution conflicts; afterward old probe may finish with explicit tested_revision and no “current passed” flag. Authorization expires after 30 seconds and cannot resolve after completion.

Provider interface permits auth metadata only. Confirm exact authenticated model-list/identity paths, authentication, and free nature from vendor primary documentation before implementation. Unconfirmed adapters/Perplexity default unsupported; no generation/search disguised as free probe. At most 5 seconds/64 KiB, no retries/redirects, only bound endpoint. Reject loopback/private/link-local/metadata/DNS rebinding; private endpoints require deployment allowlist. HTTP 200 must match allowlisted authenticated structure, not public health pages/HTML. Return no body/headers/model names/URL/remote request IDs.

Limit 10 probes/operator/minute and 5/credential/minute across replicas using audit/transaction locks; excess sends no request. probe-start/finish share probe_id; interruption leaves start, displayed interrupted/unknown, no fabricated finish. Failed finish audit yields audit_unavailable, never passed. Real generation uses existing model/org tests; platform-model test metering gap remains in [budget decisions](budget.md#待决定), without extending org-free paid probes.

Audit actions: `credential.create/replace/set_active/remove/import/read/probe_start/probe_finish` and `credential.rewrap`. CredentialAuditDetails allowlists details; actor/object_id/outcome/time use existing columns. List-read audit stores no arbitrary query; failures only approved codes/known UUIDs. Changes/success audit share transaction; rejected/failed events commit outside failed transaction. Import dry-run remains read-only without audit; even audit failures cannot log keys in exceptions. Permissions/triggers enforce append-only, including maintenance paths.

Reject unauthenticated requests before dedicated credential connections. Ordinary audit connection appends only `platform.credential_denied`, fixed internal actor/invalid_session classification, not `credential.*` probe authorization. Authenticated invalid inputs use credential audit functions with fixed codes.

## One-time import and forbidden env fallback

import-env is explicit migration/recovery, not a startup hook. CLI reads only manifest-named variables from three source classes in protected env-file. No shell/source/`$()`/variable expansion; never upload entire env files/paths/unrelated bootstrap secrets. Ambiguous assignments fail input validation. API source_env is a validation/provenance label, not permission to read remote OS environment; secrets remain write-only fields.

CredentialImportManifest explicitly lists name/purpose/provider/endpoint/active/source_env. A key used by different endpoints gets no automatic duplicated authorization. `--dry-run` checks formats/catalog mappings/conflicts; data/items contain target names/planned actions, no fingerprint/key. Each actual batch creates/audits in one transaction. Existing identical name/identity/state/decrypted key is skipped, never compared by truncated fingerprint. Removed/different value/metadata causes import_conflict and zero batch writes. Multiple batches are not globally atomic; replay interrupted manifests without default overwrites. Explicitly list missing sources/uncovered catalog entries as safe errors rather than silently skip.

Import sends encrypted pending rows only to management connection. Trusted resolve-operator-check compares existing values, returning no plaintext to clients, only equality to import transaction. The write transaction locks/rechecks that comparison's revision to avoid skipping concurrent changes.

After cutover, API/worker/standalone checks old env names and nonempty direct Settings fields at startup, failing credential_env_forbidden. Detect full `BID_PLATFORM_CREDENTIAL_*` prefix, not only MAIN. Reject even if DB values exist; no mere DB priority/silent ignore. Bootstrap-only import clients may read selected env-file without injecting it into services. Clear service env/config/old secret mounts/import temporary files before reopening tasks. Until old replicas exit, do not claim disable covers all callers.

## Encryption-root rotation and recovery

Use replace for vendor keys; extend the existing admin entry for encryption roots:

```text
uv run python -m app.admin rotate-encryption --scope provider-secrets
```

Add `--scope data|provider-secrets`, default data preserves behavior/output. provider-secrets reads BID_SECRETS_KEY/BID_SECRETS_KEY_PREVIOUS, scans active/disabled platform rows and all org BYOK historical revisions, not removed empty ciphertext or data-key decryption. Validate/separate current/retired data/token/secrets roots. Old BYOK envelopes retain org/config validation/semantics; adding platform domain cannot break history.

Distribute new key as readable previous while writing old current. After all replicas read both, switch current individually and retain old previous; only then rewrap to avoid unreadable new ciphertext during rolling updates. Lock rows or compare-and-swap original ciphertext against concurrent replace. Platform rewrap/success audit share transaction. BYOK maintenance is migration-owner-only with org context/composite FKs; equivalent ciphertext only, no key/business-column/id/revision change, deletion, or whole-table trigger disabling.

`RotationReport` counts platform_checked/rewritten, org_revisions_checked/rewritten, failed; no ciphertext/key/bad payload. Stable-ID failures enter restricted operations reports. Resumable; current-key rows validate without rewriting. failed=0 → exit 0; any successful validation/rewrap plus failures → 5; zero success/all retryable → 3; zero success/any nonretryable → 4; startup configuration errors → 4. Bound current-key validation counts as success; rewritten=0 does not mean all failed. Remove old online keys only after zero failures and a second pass with rewritten=0; retain for backup lifecycle. This is not vendor-key rollback: replaced/removed online values require resubmission, with no historical plaintext API.

## Implementation order and test plan

Dependencies: migration/permissions → encryption/rotation/resolver → API/CLI → console/import cutover. Every real entry uses resolver without env branches. Scenarios below define gates; the main integration session runs isolated PostgreSQL database suites, never substitutes static checks.

| Scenario | Required proof |
| --- | --- |
| Orgs A/B + admin/ordinary user/API token/no-context SQL | Org credential routes fail without ID enumeration; bid_app/existing bid_platform_fn denied table queries/DML/functions/SET ROLE; forged actor GUC ineffective; org RLS unchanged |
| Least-privilege roles | Management cannot read ciphertext; reader cannot enumerate/write; NOLOGIN owner cannot log in; no PUBLIC EXECUTE/schema CREATE/membership; catalog queries verify fixed functions/columns |
| Platform TOTP | Real platform login works; expired/removed allowlist users rejected; tokens cannot request scopes; local CLI no bypass |
| Create→list→detail→model→worker | UI/API/CLI only fingerprint/last_four; correct consumers; two fake vendors verify new key only in request headers, not files/queue/cache/audit |
| Errors/probe leakage | Fake vendor echoes key in body/header/model/error/oversized response/redirect URL; final data/items/warnings/logs/audit/errors have no key/ciphertext. Invalid key-file permissions/symlinks/length/control characters fail |
| Encryption/root binding | Cross-row/name/provider/endpoint/BYOK/org/revision ciphertext fails; bad roots/ciphertext unreadable; all current/retired cross-domain reuse rejected |
| Concurrent replace/disable | Only one expected_revision succeeds; workers see new key next call; in-flight boundary reproducible; disable/remove sends nothing on next call, no env/old-key/SearXNG/anonymous fallback |
| Identity/cache | Replace preserves catalog/cache identity; queued calls use new key; changed reference/service ID fails old jobs; completed caches readable; cover LLM batches/retry/structured/prototype/Vision/search/eval |
| Audit faults/atomicity | Audit failure rolls back changes, prevents probes or success claims; failed events survive rollback; interrupted probes retain start; UPDATE/DELETE rejected |
| Probe limits | Auth metadata only; unsupported no network; shared replica limits/time/size/DNS/redirect/private rules; no org content/vendor charges |
| Import/cutover | Dry-run zero writes; identical replay skipped/conflict rolls back; unknown fields/bootstrap secrets rejected; startup rejects old env/direct settings, then DB works after removal; explicit SearXNG never substitutes for failed Perplexity |
| Root rotation E2E | A/B BYOK history/platform credentials; read old/new, rewrap/concurrent replace/failure recovery/second-pass zero changes/remove old online key/calls; ordinary BYOK UPDATE rejected; backups recover with retained keys |
| Result/CLI/pages | Seven-key/nested exit_code snapshots, full schema discovery; UI list/create/replace/disable/test/consumers/conflicts; no real-secret screenshots/HAR |

Use fake Providers/temporary synthetic credentials for repeatable E2E; main session owns PostgreSQL lifecycle. Keep reproduction commands/redacted Result snapshots/JUnit/check summaries in `data/work/platform-credentials-validation/`, not docs logs/screenshots/evidence; no request bodies/secrets/decrypted values/raw HAR. Share only safe views/role assertions/pass-fail summaries.

Static contract checks:

```sh
uv run ruff check docs/plan/platform-credentials/
uv run ruff format --check docs/plan/platform-credentials/
uv run pyright docs/plan/platform-credentials/platform_credentials_contracts.py
uv run python -c "import runpy; runpy.run_path('docs/plan/platform-credentials/platform_credentials_contracts.py')"
```

## Decisions

| Decision | Approved choice | Reason / impact |
| --- | --- | --- |
| Global table/connection isolation | ADR 0006; separate management/resolver roles/pools | Real org DB-role proof of no ciphertext access; two bootstrap connections; shared bid_app/actor GUC insufficient |
| Initial scope | All three key classes including standalone/eval; S3/sandbox/roots stay deployment | Online standalone needs DB; exclude infrastructure recovery identities |
| Default activation/anonymous models | Create disabled, explicit enable; missing keys fail; separate anonymous-endpoint contract | Disable cannot become anonymous calls; old catalog handled before cutover |
| Same-name authority | Explicit one-time import, reject old env/config | Exposes missed migration, no dual authority |
| Search selection | Explicit nonsecret BID_SEARCH_PROVIDER, default disabled | No silent Perplexity→SearXNG; set at migration |
| Keys/job identity | DB resolution per outbound, no cross-call key cache, replacement preserves model revision | New value/disable visible next resolution; per-call reads/in-flight window accepted |
| Removal/recovery | Tombstone/erase online ciphertext/nonreusable names/no key history | Traceability/no resurrection; reentry needed, backups/revocation separate |
| Root rotation | BID_SECRETS_KEY; extend rotate-encryption across platform/BYOK history | Rotate entire shared domain; restricted BYOK rewrap |
| Tests | Auth metadata only; Perplexity/unverified unsupported; no paid synthetic search | Preserve metering/org isolation; paid tests need approved dedicated org/budget, no org-free usage exception |
| Admin CLI | Same TOTP session, read-only key-file, explicit import-env | Repeatable operations, no key argv/general JSON/token authority |

All recommended defaults are approved. Deployment cutover follows database and end-to-end acceptance, not approval alone.
