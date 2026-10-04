# Declared feature metadata and retained task versions

## Problem

A shared software feature description can change product association or declared
implementation state while an active task must retain its selected version.
Declarations must never be presented as verified screenshots or proof of delivery.

## Usage

Create a feature under an existing same-org product. Supply a nonblank name and
description and an explicit `planned`, `developing` or `implemented` state. Updates
require the current expected revision. Select the feature for a task; replace it
explicitly to change the task. `--history` retains every revision and selection.
Every result warns that status is a declaration and evidence is not verified.

## How it works

Three FORCE RLS tables extend the previously verified versioning pattern:
`features`, `feature_revisions`, `task_features`. The current pointer is a deferred
composite foreign key. Each revision has a tenant-scoped product foreign key and
an immutable JSON payload. Database checks require the JSON product ID to equal
the keyed product ID and reject missing/null/unrecognized implementation states.
Pydantic normalizes UUIDs before serialization. A later association change belongs
to a new revision; saved task selections retain their previous product association.

Updates use a feature lock and expected-revision comparison. Selections lock the
task and share-lock the feature while resolving a current revision, making repeat
selection idempotent and concurrent stale updates explicit conflicts. The partial
active index is scoped by org/task/feature/lot. Old selections close, never delete.
The shared append-only audit records actor/token and old/new IDs in the same
transaction. Function-scoped API dependencies commit before returning success.

## Pitfalls

SQL CHECK accepts UNKNOWN; a mere `json ? key AND json->>key = value` is insufficient
for JSON null. Explicit `IS NOT NULL` guards are required and directly tested.
The first synthetic instance exposed that defect; the corrected unpublished
migration was replayed in a new empty instance, retaining the failed test data.
Runtime cannot update revisions/audit or delete history; owners still require
appropriate backup and access controls. Downgrade refuses destructive drops.

Statuses are user declarations. There is no screenshot input, capture,
watermark, evidence confirmation or draft/export. Extra screenshot fields are
rejected. Feature endpoints reuse the resource scopes; task-only tokens gain no
permission. No provider, external URL retrieval or AI call is involved.

## Code

- `schemas/feature_contracts.py`, `services/features.py`, `services/versioned.py`, `api/resources.py` under server/app.
- Migration `0004_versioned_features.py`, resource RLS tests and `test_features.py`.
- Both real CLI modes in `test_runtime_integration.py`; all command JSON snapshots.
