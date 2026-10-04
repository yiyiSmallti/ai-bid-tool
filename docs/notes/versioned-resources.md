# Versioned products and task selections

## Problem

Editing shared metadata must not silently alter a task that selected an earlier
version. Concurrent edits must not overwrite each other, and repeating a request
must not create duplicate selections.

## Usage

Create metadata, select its returned product ID for a task, and update using the
current `expected_revision`. Read history with `--history`; replace the selection
explicitly with a new `task resource add`. See the workflow in
[cli.md](../guides/cli.md#maintain-versioned-resources).
URLs are stored as metadata and are never fetched.

## How it works

`products` points to an append-only `product_revisions` row. The deferred composite
foreign key requires a valid current revision at transaction commit. The update
service locks one product and checks the caller's expected revision; stale writes
return 409 without appending history. An omitted selection revision resolves the
current revision while holding a shared product lock in the same transaction.

`task_resources` references the immutable revision, rather than duplicating JSON.
A task lock serializes selection changes. The active slot is `(org, task, product,
lot)`: different products and lots are allowed. An identical active revision returns
the same ID with `duplicate=true`. Replacement closes the previous selection and
appends a new one. History includes closed rows and separately lists active IDs.
Replacement with an older explicit revision is allowed and recorded; no automatic
upgrade occurs when shared metadata changes.

All four new tables have NOT NULL `org_id`, composite tenant foreign keys and FORCE
RLS. Runtime grants allow inserts and reads, updates only to products and the
selection's active flag, and no deletes. Revisions and audit records are immutable
under the runtime role. Audit details contain actor and old/new IDs, not copied
metadata. New API dependencies commit before sending a success response; any
commit failure rolls back the metadata and audit together.

## Pitfalls and limits

There is no evidence validation, URL retrieval, confirmation, export or real AI
call here. Product IDs remain distinct even if their metadata matches; the design
does not define a unique vendor/model normalization rule. Unknown model versions
stay null. New scopes must be explicitly granted to new tokens; old tokens do not
gain them. Production owners remain responsible for privileged database access,
backup, encryption and audit retention. The migration refuses destructive downgrade.

## Code pointers

- `server/app/schemas/resource_contracts.py`: shared API/CLI validation.
- `server/app/services/versioned.py`: locks, revision conflict, selection and audit shared by
  products, features, certificates, profiles and templates; `VersionedKind` names each
  one's tables, scopes and audit actions.
- `server/app/services/resources.py`: the product kind and its views.
- `server/migrations/versions/0003_versioned_products.py`: isolation and privileges.
- `server/tests/test_resources.py`, `test_resource_rls.py`: functional/concurrent/DB tests.
- `server/tests/test_runtime_integration.py`: genuine local and remote CLI transports.
