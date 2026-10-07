# Confidential fields and export-time filling

## Problem

Bid prices, identity numbers, bank accounts, contact names and phone numbers
must appear in the delivered document, but a model needs none of them to draft
a response. Sending them to a vendor exposes the org's most sensitive data, and
pattern masking alone leaves `[REDACTED_…]` text that nothing can turn back into
the right value.

## Usage

Register fields and values in the console (保密字段, and 报价与保密信息 on a task
page) or with the commands in [the CLI guide](../guides/cli.md#keep-confidential-values-out-of-the-model).
Cards, drafts and resource declarations refer to a value as `{{secret.<key>}}`.
In the console nobody types one: the card editor and the 单位资料 page show each
placeholder as a labelled block, and fields are dragged or clicked in from the
strip above the text; an export dry run (`"dry_run": true` in the
prepare input) lists which fields a run will fill and which are missing. The approved contract and
its decisions are in [confidential-values.md](../plan/confidential-values.md).

## How it works

### Storage and access

[confidential.py](../../server/app/models/confidential.py) holds the authoritative field/value FORCE RLS
tables created by migration `0030`. `confidential_fields` fixes `key`, `kind`
and `scope` (`org` or `task`); only `label`, `archived` and `revision` can be
updated. `confidential_values` is append-only: each set adds a version for the
field, or for the field and task, and the highest version is current. Values are
encrypted with the data key and listed in `ENCRYPTED_COLUMNS` in
[admin.py](../../server/app/admin.py) for key rotation. `tail` keeps the last
four characters only for identity, account and contact values of at least eight
characters; amounts and names keep none.

`confidential:read` (keys, labels, status, tails) belongs to every role and may
be granted to a token. `confidential:write` and `confidential:reveal` belong to
admin and bidder sessions only. They are absent from token `SCOPES`, refused by
the `token_forbidden_confidential_scopes` constraint, and every write or reveal
also requires a session actor. Audit rows record field, value row and task IDs,
never the value.

### Bounded management and checked writes

The org confidential page uses `management_confidential.fields/values/history`
in [management_confidential.py](../../server/app/services/management_confidential.py).
Definitions are org-wide; task context is authorized before reading even metadata.
Without a task, current values contain only org fields. With a task, each field
resolves either its org owner or that exact task owner. History requires the
field's exact scope. Foreign or unavailable parents are indistinguishable.

Queries use literal casefolded key/label prefix tokens, 25-row default keysets and
an optional exact `field_id` editor filter. Read SQL limits fields before lateral
current-value lookup and limits history before joining field metadata. Safe column
projections omit ciphertext; no list, editor hydration or history read decrypts.
Only authorized masked views may carry the existing four-character tail. Value
`set_at`/`set_by` are retained row metadata; field revision is only a concurrency
counter, with no archived labels or reconstructed metadata authors.

Cursors expire after 15 minutes and bind org, actor, live scopes, task workflow/member
authority, normalized filters and parent/order. Pages enforce complete Result byte
budgets and a two-second statement timeout. Migration
[0055_confidential_management.py](../../server/migrations/versions/0055_confidential_management.py)
adds generated search metadata, a derived lexeme projection, keyset/owner indexes
and an AFTER fixed-field guard, preserving the existing RLS, composite keys and
field/value column-level write privileges.

PostgreSQL's `tsvector @@ tsquery` function is not leakproof, so a GIN predicate
cannot be promoted ahead of the field table's RLS barrier. The derived
`confidential_field_search_tokens` table stores only `tsvector_to_array` lexemes
from key/label metadata. Its `(org_id, token, field_id)` B-tree uses `C` collation;
literal prefix bounds use leakproof text comparisons. Remaining query tokens use
bounded `(org_id, field_id, token)` probes. Materialized candidate IDs feed
parameterized field point reads, with `OFFSET 0` preventing join flattening;
archive/scope/exact-ID/keyset filters and the original `@@` semantic check precede
page LIMIT. The values path reads only that retained field page.

The projection has FORCE RLS and an org/field composite FK. An invoker-rights
AFTER trigger refreshes it on field insertion or label change; another AFTER
guard refuses direct projection writes and checks nested changes against the real
parent vector under a shared row lock. Trigger depth alone is not a provenance
check: caller-owned temporary triggers must not insert fake lexemes or remove
current ones. FK cascade cleanup is allowed when the parent no longer exists;
queued AFTER triggers do not have a fixed cascade nesting depth.
Runtime INSERT/DELETE grants serve derived maintenance, with no
privileged function owner, new bypass role or change to PostgreSQL's leakproof flags. The scale acceptance checks both root
and lexeme relation visits and the actual range index conditions.
Its stored search-column/index build needs a maintenance window sized for the
existing field table; recovery preserves rows and guards and repairs forward.

`ConfidentialValueRevisionSet` requires the field counter and an explicitly supplied
current value ID; null asserts absence for that one field/owner. The checked setter
locks task/workflow before the field, compares both preconditions, then explicitly
constructs the existing `ConfidentialValueSet`. Saving and `confidential.value.set`
audit share one transaction. A conflict clears the transient value and requires a
new deliberate entry. Legacy setters still append without CAS.

The UI and CLI send secret input once through dedicated transports. Generic checked
command serialization and repr omit it; CLI reads it only from stdin. Forms never
prefill a value or trigger reveal. Explicit reveal stays in the existing human-only
audited route and clears on close, blur, hide, navigation, logout and org switch.
No value, search text or field content enters URL state or persistent browser storage.
Secret Playwright scenarios disable traces, screenshots and video, and artifact
assertions record booleans rather than captured values. See the
[management test plan](../plan/management-pages.md#test-plan-and-repeatable-artifacts).

### Outbound substitution

`snapshot` in [card_generation.py](../../server/app/services/card_generation.py)
loads the task's current values and `library_value` in
[redaction.py](../../server/app/services/redaction.py) turns each into a
pattern. Numbers match with or without spaces and hyphens; amounts with or
without thousands separators; text matches literally. Values too short to find
without hitting unrelated text (fewer than four digits, or one character) are
not substituted, so the pattern rules still apply to them. `redact` replaces the
longest matches with `{{secret.<key>}}` first. A pattern rule that overlaps a
placeholder masks only the text outside it.

The task's model-redaction switch controls both steps. With masking off, values
are sent as typed. The request still lists `confidential_fields` (placeholder,
label, kind) and the prompt asks the model to write placeholders instead of
values. The manifest records the value row IDs used, so a changed value is a new
input with a new hash.

### Cards and declarations

Resource declarations (products, features, certificates, org profiles,
templates) may name fields too. `check_placeholders` in
[versioned.py](../../server/app/services/versioned.py) refuses unknown or
archived keys on every create and update. A declaration that names a field
reaches the model as the placeholder and needs no substitution; an evidence
quote of it is still verbatim.

Saving a card refuses a placeholder whose key is unknown or archived
(`unknown_confidential_field`). Confirming refuses that and any `[REDACTED_…]`
text (`redacted_placeholder_in_response`). A model proposal naming a placeholder
it was not sent is skipped with the same code. Cards store placeholders only;
changing a value never invalidates a confirmed card.

### Export

`confidential_gate` in [exports.py](../../server/app/services/exports.py) lists
the keys named by response rows and their evidence quotes. It adds a `confidential` list of field ID,
label, status and current value row ID to the fixed manifest only when a key is
named, so runs without placeholders keep their hash. A missing value blocks a
`final_section` (`confidential_value_missing`); an unknown or archived key
blocks both modes. A review copy (审阅件) renders a missing value as 【label】.

The render job decrypts exactly the fixed value rows into the private render
directory. `_fill_confidential` in
[export_renderer.py](../../server/app/services/export_renderer.py) replaces the
placeholders in response text, deviation notes and evidence excerpts before
rendering. The values
stay outside the manifest hash and the stored run. Release rebuilds the manifest,
so a value changed after submission fails with `export_input_changed`.

## Pitfalls

- Text values match as substrings, so a registered name inside a longer name
  (张三 in 张三丰) is also replaced.
- Substitution exists for text nobody can edit, such as certificate pages and
  tender quotes. Declarations entered in the console should name fields rather
  than carry values, so they never depend on it.
- Substitution only finds exactly registered values. A differently written
  amount or an unregistered account number falls back to the pattern rules,
  and that masked text cannot be filled at export.
- Archiving a field named by confirmed cards blocks every export of them until
  the cards are edited or the field is restored with `--active`.
- The console has no export submission page; the missing-value list shows on the
  task page, and the full list in the export dry run.

## Code

- [confidential.py](../../server/app/services/confidential.py), [confidential_contracts.py](../../server/app/schemas/confidential_contracts.py), [api/confidential.py](../../server/app/api/confidential.py) and [cli confidential.py](../../cli/bid_cli/confidential.py).
- [redaction.py](../../server/app/services/redaction.py), [card_generation.py](../../server/app/services/card_generation.py) and [providers/drafting.py](../../server/app/providers/drafting.py).
- [exports.py](../../server/app/services/exports.py), [export_render.py](../../server/app/jobs/export_render.py) and [export_renderer.py](../../server/app/services/export_renderer.py).
- [SecretTextEditor.vue](../../web/src/components/SecretTextEditor.vue) (labelled blocks, drag and click insertion), [ConfidentialPanel.vue](../../web/src/components/ConfidentialPanel.vue), [CardEditor.vue](../../web/src/components/CardEditor.vue) and [OrgProfiles.vue](../../web/src/views/OrgProfiles.vue).
- [management_confidential.py](../../server/app/api/management_confidential.py), [management CLI](../../cli/bid_cli/management_confidential.py), [OrgConfidential.vue](../../web/src/views/OrgConfidential.vue) and [confidential-management.spec.js](../../web/e2e/confidential-management.spec.js).
- [test_confidential_values.py](../../server/tests/test_confidential_values.py): drafting, export, permission and isolation scenarios with a repeatable DOCX artifact.
