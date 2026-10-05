# Organization console

## Problem

A tender review must retain its extraction scope, original citations, fixed
materials and human decision boundaries while users move among thousands of
requirements. A browser must not turn a stale revision, partial job or historical
draft into an authorization to confirm or deliver a response.

## Usage

The organization workspace starts at `/app/org/tasks`. Opening a task discovers
its documents and parse jobs from the API. Review and draft routes require an
explicit successful extraction in `?job=J`; a requirement ID can locate a detail.
Source downloads and certificate previews require the organization session as
well as their short-lived signatures.

Business rules belong to [response-cards.md](response-cards.md), model preview
semantics to [model-drafting-redaction.md](model-drafting-redaction.md), and the
acceptance boundaries to [the console contract](../plan/org-console.md).
The fixture provisioner [org_console_e2e.py](../../scripts/org_console_e2e.py)
and [org-console.spec.js](../../web/e2e/org-console.spec.js) define the isolated
browser gate inputs and artifact format. The provisioner requires a `bid_test`
database and confines generated files to `data/work/` in its worktree.

## How it works

### Recovery reads

[org_console.py](../../server/app/api/org_console.py) defines three auxiliary
reads using the existing `Result` envelope and tenant transaction dependency:

| Route | Authorization and projection |
| --- | --- |
| `GET /tasks/{task_id}` | `task:read`; task identity, descriptive fields, timestamps and revisioned model-redaction settings |
| `GET /tasks/{task_id}/documents` | `task:read`; all task documents, including uploaded documents without parse or extraction jobs |
| `GET /tasks/{task_id}/jobs?kind=parse` | `task:read` and `job:read`; parse job identity, result, error, attempts, reasoning and timestamps |

The optional `document` query on the job list must identify a document belonging
to the same task. Missing and cross-organization task or document references
return 404. The required `kind` accepts only the stored `parse` type, avoiding
exposure of internal drafting submission manifests. Storage keys, queue IDs,
leases and worker run IDs are outside these projections. No new persistence,
permissions, migration or implicit CLI operation is introduced.

### Session and request boundaries

[router.js](../../web/src/router.js) obtains the live membership role from
`/org/current`. [api.js](../../web/src/api.js) keeps platform and organization
sessions separate, checks same-origin paths, omits cookies and attaches the
organization Bearer and `X-Org-Id`. Session changes abort in-flight requests and
reject late responses. Authentication failures clear only the affected session;
403 remains an authorization error rather than a sign-out.

The adapter preserves the seven `Result` fields. Only the documented draft and
job `completion=partial` forms permit a successful HTTP response with `ok=false`.
Unexpected envelopes and non-JSON errors remain visible failures. Multipart
requests let the browser supply their boundary. Authorized binary reads validate
the declared file type; PNG previews additionally check their signature, use
memory-only data URLs and discard them when closed or the session changes.

[JobPanel.vue](../../web/src/components/JobPanel.vue) polls active jobs, backs off
transient read failures, honors `Retry-After`, pauses in hidden tabs and stops at
terminal states. Cancellation and retries require separate user actions. Network
failure never resubmits a paid or decision write.

### Review state

[OrgReview.vue](../../web/src/views/OrgReview.vue) validates the chosen job against
history and joins complete requirement and card sets by ID, preserving source
order and missing-card slots. Filtering and pagination operate in memory; only
the current page and one detail are mounted. Batch selections have fixed IDs and
are cleared when filters change. Each disposition retains its expected revision
and individual reason, and the browser sends one atomic request.

[CardEditor.vue](../../web/src/components/CardEditor.vue) rereads a card when
opening it. Saved content and locally edited content remain separate. A revision
conflict preserves local edits, clears review checkboxes and shows the server
revision for explicit reconciliation. Every evidence item and warning requires
its own unchecked review control. Role-specific human actions use the server's
`review_domain`; administrators classify unknown drafts but cannot make another
profession's decision. Material and citation invalidity block confirmation.

[MaterialPanel.vue](../../web/src/components/MaterialPanel.vue) reads fixed task
selections rather than substituting resource-library revisions. It offers only
the scalar fields allowed by `RESOURCE_FIELD_PATHS` in
[response_card_contracts.py](../../server/app/schemas/response_card_contracts.py)
and archived certificate pages. Replacing a fixed selection explains dependency
invalidation. Plain text rendering preserves original quotes and does not
execute tender or model content.

Only IDs, page size and non-text filters are stored in the current tab under an
organization-specific key. Search text, response edits, materials and signed
links remain in memory. Navigation warns before discarding unsaved response
edits. Native controls, focusable table scrolling and modal dialogs preserve a
keyboard path through review without single-key human decisions.

### Drafting and assembly

[GenerationPanel.vue](../../web/src/components/GenerationPanel.vue) reads official
reasoning choices through extraction dry-run and displays the actual generation
preview. Changes to scope, settings or selected materials invalidate it and clear
the authorization. A paid run needs a charge cap (prefilled with the estimate
rounded up to cents) and an explicit authorization checkbox; it submits the
preview's `input_hash` and the cap, as defined in
[drafting-binding.md](../plan/drafting-binding.md). `generation_input_changed`
discards the preview without retrying, and the job is tracked with
[JobPanel.vue](../../web/src/components/JobPanel.vue).

[OrgDrafts.vue](../../web/src/views/OrgDrafts.vue) separates deterministic assembly
preview from submission. It retains partial outcomes and historical snapshots,
shows the three tables separately from comply-only (须遵守) decisions and gaps, and keeps
negative-deviation counts visible across filters. A stale draft retains its
snapshot and links affected requirements back to review; it is not a deliverable.

Draft reads use `load_draft_reads` in
[drafts.py](../../server/app/services/drafts.py) and `CardReadBatch` in
[response_cards.py](../../server/app/services/response_cards.py) to load current
cards, historical revisions, evidence links, source chunks and fixed material
parents in batches. The detail and history-list paths share this request-local
graph and compute validity in memory; statement count depends on material types,
not requirement or draft count. Existing exact-citation, eligibility and evidence
projection rules are shared with single-card reads. Historical evidence and
uncited model-input grants are still checked before returning any snapshot.
[test_draft_batch_reads.py](../../server/tests/test_draft_batch_reads.py) compares
the complete API envelopes to the retained per-item implementation and records
SQL counts from fresh application transactions.

## Pitfalls

- Complete review coverage describes only saved requirements of the selected job.
  It cannot establish extraction completeness or completion of a whole bid.
- Browser role hints and checkboxes never replace API authorization or the human
  decision transaction. Local sign-out does not revoke the server session.
- Full-set APIs have a scale limit. The performance gate in
  [the console contract](../plan/org-console.md#large-lists-and-accessibility) must pass with real
  API responses before claiming large-list acceptance; truncation is not a fix.
- A signed URL alone is insufficient for material access. Do not place session
  credentials in a URL or embed signed downloads in an unauthenticated iframe.
- A declaration, commitment or unconfirmed source archive is not proof of an
  original document's authenticity. Scanned pages without extractable text cannot
  acquire an exact evidence quote through this console.
- Synthetic fixtures must remain in isolated test storage; browser test traces
  must not record sessions, passwords, signed URLs or vendor request bodies.

## Code

- [OrgTasks.vue](../../web/src/views/OrgTasks.vue), [OrgTask.vue](../../web/src/views/OrgTask.vue): creation, upload, parse recovery and extraction history.
- [org.js](../../web/src/org.js), [App.vue](../../web/src/App.vue): workspace identity, safe navigation state and shared display helpers.
- [SourcePreview.vue](../../web/src/components/SourcePreview.vue): authorized, on-demand certificate page review.
- [test_org_console.py](../../server/tests/test_org_console.py): recovery reads, real parse processor and two-organization isolation gates.
