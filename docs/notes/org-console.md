---
kind: reference
---

# Local task console

## Problem

A task console must retain extraction scope, exact citations, fixed materials and
human confirmation while navigating saved requirements. A stale page cannot
authorize a decision or make an old draft current.

## Usage

Task discovery starts with `GET /tasks`. Select an explicit successful extraction
before reviewing requirements or assembling drafts. Business acceptance requirements
remain in [the console plan](../plan/org-console.md); the native renderer is below.

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
The native client exchanges the access key for an in-memory session and sends bearer
credentials only to validated paths on its configured loopback API origin. Signed
material links also require current authenticated access. Credentials never enter
logs, screenshots or persistent UI state.
API responses must contain exactly one complete Result 5.0 JSON value; trailing
JSON values and non-JSON content are rejected before a page applies the result.

[Response cards](response-cards.md) defines confirmation and stale-material
rules; [model drafting](model-drafting-redaction.md) defines outbound previews.
The native UI renders these rules and never substitutes local approval state.

### Native rendering boundary

MyGo 0.4.5 renders `ui.View(app.View)` directly. `NativeApp` owns routing, window
lifecycle, page cancellation, dirty navigation and session loss. Pages compose
native forms, tables, image previews and dialogs; no browser renderer or JavaScript
bridge participates. `/app` prefixes normalize to the same internal routes, query
parameters retain task/extraction/revision scope, and unknown full route shapes
return to `/org/tasks`. Dynamic identifiers retain the former route semantics.

Reads and job/SSE/image work run outside `View`. Results apply through the UI
dispatcher after checking page epoch and the panel's current request generation.
Selection changes invalidate earlier image/reveal work. Window blur, hide and
minimize clear revealed confidential values; revealing again requires another
explicit action. A 30-second expiry uses the same dispatcher. Saving an inspected
original requires its expected hash, length and media type plus successful local
file replacement; canceling the save dialog does not mark it inspected.

The shared [workspace shell](../../desktop/native_app.go) separates task navigation
into preparation, response review, checking/scoring and job records. Resource and
installation groups collapse with explicit disclosure arrows. Task links preserve
the selected extraction; destinations whose `job` query names another job use
`extraction` for this context. Returning from memory feedback therefore does not
mistake a candidate job for a tender extraction.
The window hides the system title bar; the sidebar and page header leave room for
the window controls reported by MyGo and drag the window. Inside a task the sidebar
shows the task name and deadline, loaded once per task when navigation arrives
without it.

[Native controls](../../desktop/native_widgets.go) use the system font, a quiet
sidebar, bundled Lucide icons ([license](../../desktop/icons/LICENSE)) and explicit
primary, secondary and destructive actions. Status columns render as colored
badges and ISO timestamps as local times; values sent to the API stay unchanged. Button text and
user-authored names remain literal; known enum fields translate at their callers.
Select options expose named native accessibility actions. Requirement collection
choices use already-loaded document names, creation times and counts; complete IDs
remain visible in scope details.
Form labels sit above their controls in compact muted text; filter fields share
fixed widths in wrapping rows, and edit fields share a bounded maximum width.
Read-only metadata uses a definition list with an unwrapped label column and
wrapping values. Missing values display an em dash, and identifier-only list
entries use short IDs while copyable scope details retain complete values.
The shared pager shows a page number and labeled arrow buttons only when needed;
related actions wrap together. The feature library's product filter opens its
candidate list from the toolbar.
Each repeated metadata group owns a real child container. Table rows retain full
text and MyGo's dynamic row heights, with compact content-sized viewports and
explicit column widths. Tables scroll in both axes; double-click or Enter opens a
selected row. Replacing a row snapshot clears selection before it can open a
different record. Empty tables show a short explanation without reserving a large
blank table.

The task list filters locally by name or tender number and opens task creation in
a modal that stays open while it holds unsaved input; below 1180 points of window
width the tender number moves under the task name. Task detail shows a six-step
preparation flow derived from loaded documents, extractions and exports, plus named
sections. Extraction history identifies runs by local start time and explains
failures using translated error codes and an HTTP status when available. Its
actions state the preflight requirement before starting or explicitly retrying.
Library lists search on Enter. Task creation uses a bounded form, and requirement
and response review separate source content from decisions. Resource editors use
paired short fields and full-width long text, with save/cancel actions outside the
form's scrolling area. Long pages and the sidebar scroll independently at the
960 × 640 minimum window size. The theme follows native light/dark appearance and
retains MyGo keyboard focus indicators.
Task deadlines accept `YYYY-MM-DD HH:MM` and `YYYY-MM-DDTHH:MM`, interpreted in the
machine's local time zone and sent as UTC. Leaving the field empty stays valid.

### Page routes

The 39 page contracts map to these native implementations. This table identifies
implementation ownership; acceptance results and synthetic fixtures stay outside
`docs/` as required by the [development guide](../guides/desktop-development.md).

| Page | Route | Native implementation |
| --- | --- | --- |
| P01 Access key | `/login` | [native_app.go](../../desktop/native_app.go) |
| P02 Service credentials | `/platform/credentials` | [native_settings_credentials.go](../../desktop/native_settings_credentials.go) |
| P03 Clef settings | `/platform/clef` | [native_settings_clef.go](../../desktop/native_settings_clef.go) |
| P04 Trust anchors | `/platform/trust-anchors` | [native_settings_trust.go](../../desktop/native_settings_trust.go) |
| P05 Tasks | `/org/tasks` | [native_tasks.go](../../desktop/native_tasks.go) |
| P06 Task detail | `/org/tasks/:taskId` | [native_task_detail.go](../../desktop/native_task_detail.go) |
| P07 Task board | `/org/tasks/:taskId/board` | [native_board.go](../../desktop/native_board.go) |
| P08 Job progress | `/org/tasks/:taskId/progress` | [native_board.go](../../desktop/native_board.go) |
| P09 Requirements | `/org/tasks/:taskId/requirements` | [native_requirements.go](../../desktop/native_requirements.go) |
| P10 Evidence annotation | `/org/tasks/:taskId/cards/:cardId/annotation` | [native_annotations.go](../../desktop/native_annotations.go) |
| P11 Response review | `/org/tasks/:taskId/review` | [native_responses.go](../../desktop/native_responses.go) |
| P12 Draft tables | `/org/tasks/:taskId/drafts` | [native_drafts.go](../../desktop/native_drafts.go) |
| P13 Bid submissions | `/org/tasks/:taskId/bid-submissions` | [native_bid_submissions.go](../../desktop/native_bid_submissions.go) |
| P14 Bid submission detail | `/org/tasks/:taskId/bid-submissions/:submissionId` | [native_bid_submissions.go](../../desktop/native_bid_submissions.go), [native_bid_runs.go](../../desktop/native_bid_runs.go) |
| P15 Bid report | `/org/tasks/:taskId/bid-reviews/:reviewId/report` | [native_bid_reports.go](../../desktop/native_bid_reports.go) |
| P16 Checks | `/org/tasks/:taskId/checks` | [native_assessments.go](../../desktop/native_assessments.go) |
| P17 Check report | `/org/tasks/:taskId/checks/:reportId` | [native_assessment_reports.go](../../desktop/native_assessment_reports.go) |
| P18 Scoring rubrics | `/org/tasks/:taskId/score-rubrics` | [native_assessments.go](../../desktop/native_assessments.go) |
| P19 Rubric review | `/org/tasks/:taskId/score-rubrics/:rubricId` | [native_assessment_reports.go](../../desktop/native_assessment_reports.go), [native_rubric_editor.go](../../desktop/native_rubric_editor.go) |
| P20 Score estimates | `/org/tasks/:taskId/scores` | [native_assessments.go](../../desktop/native_assessments.go) |
| P21 Score report | `/org/tasks/:taskId/scores/:reportId` | [native_assessment_reports.go](../../desktop/native_assessment_reports.go) |
| P22 Products | `/org/products` | [native_management_resources.go](../../desktop/native_management_resources.go) |
| P23 Product detail | `/org/products/:productId` | [native_management_resources.go](../../desktop/native_management_resources.go) |
| P24 Features | `/org/features` | [native_management_resources.go](../../desktop/native_management_resources.go) |
| P25 Feature detail | `/org/features/:featureId` | [native_management_resources.go](../../desktop/native_management_resources.go) |
| P26 Templates | `/org/templates` | [native_management_resources.go](../../desktop/native_management_resources.go) |
| P27 Template detail | `/org/templates/:templateId` | [native_management_resources.go](../../desktop/native_management_resources.go) |
| P28 Template bindings | `/org/templates/:templateId/revisions/:revisionId/bindings` | [native_management_bindings.go](../../desktop/native_management_bindings.go) |
| P29 Attachment archive | `/org/attachments` | [native_management_attachments.go](../../desktop/native_management_attachments.go) |
| P30 Attachment revision | `/org/attachments/:attachmentId` | [native_management_attachments.go](../../desktop/native_management_attachments.go) |
| P31 Task attachments | `/org/tasks/:taskId/attachments` | [native_management_task_attachments.go](../../desktop/native_management_task_attachments.go) |
| P32 Profiles and certificates | `/org/profiles` | [native_management_resources.go](../../desktop/native_management_resources.go) |
| P33 Profile detail | `/org/profiles/:resourceId` | [native_management_resources.go](../../desktop/native_management_resources.go) |
| P34 Certificate detail | `/org/certificates/:resourceId` | [native_management_resources.go](../../desktop/native_management_resources.go) |
| P35 Memory library | `/org/memory` | [native_management_memory.go](../../desktop/native_management_memory.go) |
| P36 Memory detail | `/org/memory/:memoryId` | [native_management_memory.go](../../desktop/native_management_memory.go) |
| P37 Memory feedback | `/org/tasks/:taskId/memory-feedback` | [native_management_memory.go](../../desktop/native_management_memory.go) |
| P38 Models and services | `/org/settings/models` | [native_settings_models.go](../../desktop/native_settings_models.go) |
| P39 Confidential fields | `/org/confidential` | [native_settings_confidential.go](../../desktop/native_settings_confidential.go) |

## Pitfalls

- Task and extraction changes clear pending decisions and stale response data.
- HTTP acceptance of a job does not mean its execution completed.
- An empty page, failed request and partially completed operation remain distinct.
- Human confirmation remains unavailable to scoped tokens and agents.

## Code

- [Console API](../../server/app/api/org_console.py): recovery and assessment routes.
- [Task API](../../server/app/api/tenders.py): task creation and tender jobs.
- [Native client regressions](../../desktop/native_client_test.go): partial reports,
  rejected confirmation, revoked sessions, origin boundaries and safe credential receipts.
- [Native operation regressions](../../desktop/native_consumers_test.go): verified
  original saving, cancelled downloads and navigation before pending results apply.
- [Fixture provisioner](../../scripts/org_console_e2e.py): synthetic API-backed inputs
  and credential-free manifest under `data/work/` using an isolated `bid_test` database.
