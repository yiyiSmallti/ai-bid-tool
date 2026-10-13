---
kind: reference
---

# Local task console

## Problem

A task console must retain extraction scope, exact citations, fixed materials and
human confirmation while navigating saved requirements. A stale browser view
cannot authorize a decision or make an old draft current.

## Usage

Task discovery starts with `GET /tasks`. Select an explicit successful extraction
before reviewing requirements or assembling drafts. The browser contract and
acceptance requirements are in [the console plan](../plan/org-console.md).

## How it works

[org_console.py](../../server/app/api/org_console.py) exposes recovery reads:

| Route | Scope and content |
| --- | --- |
| `GET /tasks/{task_id}` | `task:read`; identity, descriptive fields, timestamps and model-redaction revision |
| `GET /tasks/{task_id}/documents` | `task:read`; uploaded and parsed documents |
| `GET /tasks/{task_id}/jobs?kind=parse` | `task:read` and `job:read`; persisted parse job status and results |

Task detail has no organization, creator or human attribution identifiers.
Document filters must refer to the same task. Job visibility retains scoped
agent and token restrictions. Assessment paging is defined in
[console assessments](console-assessments.md); live updates in
[task progress](task-progress.md).

Authentication follows [ADR 0012](../adr/0012-single-user-installation.md).
The console must exchange the access key for a session and send bearer credentials
to same-origin API paths. Signed material links also require current authenticated
access. Credentials never enter logs, screenshots or persistent UI state.

[Response cards](response-cards.md) defines confirmation and stale-material
rules; [model drafting](model-drafting-redaction.md) defines outbound previews.
The browser renders these rules and never substitutes local approval state.

## Pitfalls

- Task and extraction changes clear pending decisions and stale response data.
- HTTP acceptance of a job does not mean its execution completed.
- An empty page, failed request and partially completed operation remain distinct.
- Human confirmation remains unavailable to scoped tokens and agents.

## Code

- [Console API](../../server/app/api/org_console.py): recovery and assessment routes.
- [Task API](../../server/app/api/tenders.py): task creation and tender jobs.
- [Fixture provisioner](../../scripts/org_console_e2e.py): synthetic API-backed inputs
  and credential-free manifest under `data/work/` using an isolated `bid_test` database.
