---
kind: reference
---

# Organization memory management

## Problem

A reusable rule must expose its exact revision, owner action and server-derived
expiry without becoming evidence. Feedback recovery must preserve the human card
decision and retry the recorded job instead of inventing an event or repeating the
decision. The [management contract](../plan/management-pages.md#memory-and-feedback)
defines the page boundary; [memory mechanisms](memory.md) define approval and safety.

## Usage

The org memory library opens exact detail and separate revision history. Creation
and edits use the existing memory write contracts. Detail action hints describe
live authority; every write rechecks it. Only human org administrators approve,
reject or disable. A disabled entry requires an edit and another approval.

`bid memory browse --input QUERY.json --json` uses the shared `MemoryQuery`.
Management feedback reads explicitly select `?management=true` on the task's
feedback endpoint. The default feedback projection and existing CLI commands retain
their original contracts. The queue exposes saved job IDs, their exact recorded
event sets and resulting candidate links. Retry is an explicit action through the
existing candidate submission service; evaluation and logical deletion stay outside
the pages.

## How it works

[Management reads](../../server/app/memory/management.py) use live identity checks,
org predicates and authenticated encrypted cursors. Browse sorts immutable roots;
history sorts revisions. SQL limits before projecting content, and complete Result
bytes determine retained rows and the continuation anchor. One server `as_of`
controls each page's effective expiry. Prefix tokens cover sanitized current text,
conflict keys and tags; all selected tags must match.

The [migration](../../server/migrations/versions/0057_memory_management.py) stores
indexed search projections on roots so obsolete revisions cannot become search
candidates. The root guard retains owner, revision, tombstone and scope-epoch
checks; derived-only refreshes must equal the exact current revision. Initial
revision insertion and subsequent pointer updates maintain the projection in the
same transaction. These projections grant no approval or effectiveness.

Exact detail resolves its nullable revision author with one indexed audit query
restricted to that revision ID. Missing or ambiguous audit records yield unknown.
Historical detail labels superseded content separately from the current root's
server-derived effective status. History retains reason hashes, never promises
recoverable reason text. Source
visibility reuses `crud.visible_sources`; inaccessible task identifiers are omitted.

Feedback visibility shares `task_workflow.require_read_authority` with task access.
Its cursor also binds the task access epoch. A bounded indexed job lookup returns
only the recorded event manifest and safe receipt fields. Candidate submission and
worker publication recheck live task membership and archival in addition to the
existing event ownership and worker run fence. Human decision commit precedes
dispatch, so dispatch failure leaves a durable recovery receipt.

## Pitfalls

An active edit immediately withdraws the previous effective rule; saving a
candidate never carries approval forward. A conflict key collision requires a
human choice. Browser time cannot decide expiry. An unknown write result requires
rereading current state, not an automatic retry. Memory safety remains independent
of outbound-redaction settings. Search and content remain in volatile page state.

The migration repairs forward and preserves history. Database, SQL-plan and browser
acceptance requirements and their pending status belong to the
[contract test plan](../plan/management-pages.md#test-plan-and-repeatable-artifacts).
Test output belongs under ignored `data/work/management-pages-validation/`.

## Code

- [Schemas](../../server/app/schemas/management_pages.py): `MemoryQuery`, `MemoryDetailData`, `MemoryFeedbackRow`.
- [Read API](../../server/app/api/management_memory.py) and [existing actions](../../server/app/api/memory.py).
- [CLI](../../cli/bid_cli/management_memory.py): Result envelope and discovery schema.
- [Library](../../web/src/views/OrgMemories.vue), [detail](../../web/src/views/OrgMemory.vue), [feedback queue](../../web/src/views/OrgMemoryFeedback.vue).
- [API acceptance](../../server/tests/test_management_memory.py), [scale acceptance](../../server/tests/test_management_memory_scale.py), [browser scenarios](../../web/e2e/memory-management.spec.js).
