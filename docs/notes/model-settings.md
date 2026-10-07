---
kind: reference
---

# Org model settings

## Problem

The org (organization/tenant, 单位) model-settings page needs current and historical
configuration metadata without resolving credentials or making a provider call.
The legacy provider list can query vendor balances and includes a key suffix.
The [management contract](../plan/management-pages.md#provider-and-model-configuration)
defines the page boundary; [ADR 0006](../adr/0006-platform-credentials.md) defines
platform credential authority.

## Usage

`/app/org/settings/models` displays the effective `llm_extract` configuration.
All active org roles and tokens with `provider:read` can inspect metadata. Only
human org administrators with `provider:write` can save and test configurations.
The Result 4.0 reads are `provider show`, `provider revision show --id UUID`,
`provider history-page` and `provider catalog`; the latter accepts `--q` for a
literal catalog-ID prefix. Existing provider commands retain their contracts.

Save creates a new immutable revision using `expected_revision`. A successful
save is followed by a metadata reread. Connection testing requires an explicit
cost preflight and a separate start against the saved effective configuration.
The actual test receipt identifies the tested config ID; exact revision detail
resolves that ID even if another administrator changed the current revision.
A null config ID denotes platform-default resolution, not a preview-pinned
catalog revision. Preflight and actual costs have separate meanings.

## How it works

[Metadata schemas](../../server/app/schemas/management_providers.py) explicitly
allowlist output fields. SQL selects metadata columns without ciphertext, suffix,
platform credential reference, endpoint or wholesale prices. Platform reasoning
choices expose only name/label. BYOK metadata retains its nonsecret HTTPS endpoint
and editable reasoning schema. Reasoning request options reject credential and
transport fields recursively, with structural bounds; an old unsafe revision
fails its projection without exposing validation input. Reads do not instantiate a credential resolver,
decrypt keys, query vendor balances or aggregate usage per revision.

The saved provider/model identity is authoritative. New platform selections also
snapshot public sale prices and catalog revision in immutable configuration data.
Older revisions without these snapshots return null; they do not borrow current
prices or an enabled default. Catalog availability is an enabled-state check,
not a credential-readiness or successful-connection claim. A disabled selection
retains its identity and displays unavailable.

History orders by revision and UUID descending; enabled catalog choices order by
ID ascending, with an indexed literal ID-prefix filter. Both use `limit + 1`
keysets, complete-envelope byte limits and encrypted cursors bound to live org,
actor, authority and filters. Author lookup is one indexed aggregate restricted
to the returned revision IDs and `provider.set` audit action. Missing or ambiguous
associations produce a null author. Read routes add no per-row business audits.

The browser builds the nonsecret save body from explicit input fields. A dedicated
transport consumes a fresh password control once, clears it before awaiting the
request, discards the legacy write view and rereads safe metadata. The server
alone can reuse an old BYOK key, and only for unchanged provider and endpoint.
The [existing save service](../../server/app/services/provider_configs.py) locks
the org sequence, compares `expected_revision`, encrypts a new revision and
records the key-free audit in the same transaction.

[Migration 0056](../../server/migrations/versions/0056_model_settings.py) adds
bounded read indexes and orders the existing provider write guard after RLS,
foreign-key and CHECK rejection. It preserves the existing human-admin and
append-only rules, including ADR 0006's narrowly scoped owner rewrapping path.
No credential table, scope, deletion or reset operation is added. Recovery must
retain configuration history and repair forward.

## Pitfalls

- Configured does not mean tested. BYOK zero platform charge does not mean free
  vendor usage. A preflight does not pin the subsequent paid test's selection.
- Unknown save/test outcomes require a metadata/job reread before a deliberate
  next action; never automatically resubmit a paid test. Queued jobs retain their
  prior configuration identity.
- Org change, logout or authority loss must clear forms and password controls,
  cancel reads and discard late responses. Search text and credentials cannot
  enter URLs, persistent storage or telemetry.
- Neither source selection nor failure permits an anonymous, environment-key or
  alternate-model fallback. Org pages cannot administer platform credentials.
- Database isolation and latency acceptance require the disposable PostgreSQL
  integration runtime. Mocked browser scenarios do not prove database guards.
  Validation artifacts belong beneath `data/work/management-pages-validation/models`,
  outside documentation; secret entry must not be recorded in screenshots/traces.

## Code

- [Metadata HTTP routes](../../server/app/api/management_providers.py) and
  [bounded reads](../../server/app/services/management_providers.py).
- [Save and test API](../../server/app/api/providers.py),
  [configuration service](../../server/app/services/provider_configs.py) and
  [BudgetProviderTest](../../server/app/schemas/budget_contracts.py).
- [CLI reads](../../cli/bid_cli/management_providers.py) and
  [model-settings page](../../web/src/views/OrgModels.vue).
- [DB/API acceptance](../../server/tests/test_model_settings_api.py),
  [fixed-scale acceptance](../../server/tests/test_model_settings_scale.py),
  [CLI acceptance](../../server/tests/test_management_providers_cli.py) and
  [mocked-browser scenarios](../../web/e2e/model-settings.spec.js).
