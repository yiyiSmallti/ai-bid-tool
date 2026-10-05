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
Cards and drafts refer to a value as `{{secret.<key>}}`. The card editor inserts
a placeholder at the cursor; an export dry run (`"dry_run": true` in the
prepare input) lists which fields a run will fill and which are missing. The approved contract and
its decisions are in [confidential-values.md](../plan/confidential-values.md).

## How it works

### Storage and access

[confidential.py](../../server/app/models/confidential.py) holds two FORCE RLS
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

### Cards

Saving a card refuses a placeholder whose key is unknown or archived
(`unknown_confidential_field`). Confirming refuses that and any `[REDACTED_…]`
text (`redacted_placeholder_in_response`). A model proposal naming a placeholder
it was not sent is skipped with the same code. Cards store placeholders only;
changing a value never invalidates a confirmed card.

### Export

`confidential_gate` in [exports.py](../../server/app/services/exports.py) lists
the keys named by response rows. It adds a `confidential` list of field ID,
label, status and current value row ID to the fixed manifest only when a key is
named, so runs without placeholders keep their hash. A missing value blocks a
`final_section` (`confidential_value_missing`); an unknown or archived key
blocks both modes. A review copy renders a missing value as 【label】.

The render job decrypts exactly the fixed value rows into the private render
directory. `_fill_confidential` in
[export_renderer.py](../../server/app/services/export_renderer.py) replaces the
placeholders in response text and deviation notes before rendering. The values
stay outside the manifest hash and the stored run. Release rebuilds the manifest,
so a value changed after submission fails with `export_input_changed`.

## Pitfalls

- Text values match as substrings, so a registered name inside a longer name
  (张三 in 张三丰) is also replaced.
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
- [ConfidentialPanel.vue](../../web/src/components/ConfidentialPanel.vue) and [CardEditor.vue](../../web/src/components/CardEditor.vue).
- [test_confidential_values.py](../../server/tests/test_confidential_values.py): drafting, export, permission and isolation scenarios with a repeatable DOCX artifact.
