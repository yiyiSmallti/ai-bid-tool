---
kind: plan
---

# Contract: U01 org management pages

Status: **approved; product, feature, certificate/profile, template, model-settings, confidential and memory slices implemented**.
This contract covers the implemented management-page scope of
[roadmap U01](roadmap.md#coverage-matrix-providers-memory-dashboard-agent-and-cli).
The implemented resource slices supply bounded browse/search, creation, exact
revision detail, revision history, human deactivate/restore with independent
lifecycle events, and explicit task pinning. Features add same-org product and
implementation-state filters. Certificates/profiles add exact original-file
inspection, explicit certificate date advisories and bidder/admin lifecycle
authority. Templates add original DOCX access and human-reviewed immutable export
bindings. Model settings add metadata-only current/exact/history reads, separate
catalog pages and explicit saved-configuration tests. Confidential fields add bounded
metadata/current/history reads and guarded value writes. Memory adds bounded org
browse, exact revision management and task-authorized feedback-job recovery.
The [Pydantic v2 models and service interfaces](management-pages/management_pages_contracts.py)
remain approval artifacts; [runtime management contracts](../../server/app/schemas/management_pages.py)
and the [mechanism note](../notes/management-pages.md) define the implemented path.
Merged database, real-browser and fixed-scale acceptance remains pending. Earlier
slice checks do not establish acceptance of the integrated migration chain;
[shipped changes](../changelog.md#2026-10-06-confidential-field-management-slice)
record the confidential scope and its acceptance limitation.

## Goal and boundary

Give bid specialists (投标专员) and technical staff without engineering or
project-management habits a clear way to maintain reusable materials for a bid
(标书). An org (organization/tenant, 单位) library should answer: who can maintain
this item, which version is current, what prevents its next use, and what action
comes next. A task (任务) continues to own its selected versions and team decisions.

Provide products, features, certificates (证书), org profiles (单位资料), Word
templates and export bindings, org model configuration, org memory (记忆), and
confidential fields (保密字段). Extend existing profile/certificate/confidential
pages rather than creating competing copies. File or declaration maintenance
never establishes evidence (证据) authenticity, human confirmation (人工确认),
or readiness for final export.

The primary sources are [agent.md](../../agent.md#hard-rules-must-never-be-violated),
[design](../design.md#org-resources-and-task-creation),
[org console](org-console.md), [console assessments](console-assessments.md),
[team workflow](team-workflow.md), [memory](memory.md),
[provider configuration](provider-config.md),
[platform credentials](platform-credentials.md),
[export](export.md), and [confidential values](confidential-values.md).
This contract adds page projections and the explicitly proposed interfaces below;
it does not replace their business gates.

Exclude bulk spreadsheet import, generated certificates/vendor (厂家) pages,
electronic bidding submission, pricing strategy, public template sharing, resource
ownership assignment, automatic task resource replacement, and new model
capabilities. Library pages do not run drafting or grant export permission.
Existing task screens own review, assignments, budgets, processing jobs and
released documents. No external calls occur merely by opening a new management
page, searching, loading history or editing a form.

## Code basis and design differences

| Area | Current code and reusable behavior | Contract gap or design difference |
| --- | --- | --- |
| Resource routes | `create_router` in [api/resources.py](../../server/app/api/resources.py) registers products/features/certificates/profiles/templates, revision writes, task selections and file access | No `resources*` split module is assumed. Legacy library lists retain full revisions and a full `current_revisions` map; the implemented management slices add bounded projections and lifecycle without changing these outputs |
| Product/feature/profile/certificate services | [resources.py](../../server/app/services/resources.py) `create_product/update_product/list_products`; [features.py](../../server/app/services/features.py) `create_feature/update_feature/list_features`; [certificates.py](../../server/app/services/certificates.py) `create_certificate/update_certificate/inspect_dates`; [profiles.py](../../server/app/services/profiles.py) `create_profile/update_profile` | Preserve declaration warnings. Product/feature data is not proof; feature status literals are `implemented/developing/planned`. Certificate date checks require explicit `as_of` and do not certify authenticity |
| Shared revisions | [versioned.py](../../server/app/services/versioned.py) `create/update/list_revisions/select_revision/list_selections/check_placeholders` | Root pointers, immutable revisions and task pins already exist. Management adds independent root lifecycle state for all five library kinds. Existing updates can fail `affected_task_limit` above 100 associated tasks, including historical selections; the UI must not promise arbitrary fan-out |
| Certificate originals | [certificate_files.py](../../server/app/services/certificate_files.py) `create_file/list_files/read_revision`; [certificate_file_contracts.py](../../server/app/schemas/certificate_file_contracts.py) | File replacement creates a new metadata revision plus original/parts. A metadata-only revision does not inherit the old file |
| Templates and bindings | [templates.py](../../server/app/services/templates.py), [template_files.py](../../server/app/services/template_files.py) `validate_template`; [exports.py](../../server/app/services/exports.py) `human_access/create_binding/list_bindings/build_manifest/release/download_gate` | DOCX revisions and immutable bindings are separate. Template upload does not create a reviewed binding. Old adapter bindings remain visible with `current=false`; legacy lists are unbounded, while management routes add bounded template/binding reads |
| Configuration | [api/providers.py](../../server/app/api/providers.py); [provider_configs.py](../../server/app/services/provider_configs.py) `require_access/set_config/list_configs/config_view/catalog_view/balance_view/submit_test/preview_test` | Only `llm_extract`, org BYOK and platform model selection exist. The design's OCR/vision/search/embedding configuration and task provider profiles are deferred. Existing `GET /providers` can query vendor balance; history includes unbounded per-revision usage aggregation |
| Platform secrets | [platform_credentials.py](../../server/app/services/platform_credentials.py), [configured.py](../../server/app/providers/configured.py), [ADR 0006](../adr/0006-platform-credentials.md) | Separate platform operator console (平台运营后台), TOTP and restricted connection roles. No org-facing credential resolver or platform-key management. No env fallback |
| Memory | [api/memory.py](../../server/app/api/memory.py), [memory/access.py](../../server/app/memory/access.py), [crud.py](../../server/app/memory/crud.py), [feedback.py](../../server/app/memory/feedback.py), [candidates.py](../../server/app/memory/candidates.py), [retrieval.py](../../server/app/memory/retrieval.py), [safety.py](../../server/app/memory/safety.py) | Org scope and keyword retrieval only; four-scope schema enums do not enable the other scopes or embeddings. Management browse adds current-text/conflict-key/tag prefix search, kind/stored status and expiry filters. Retrieval remains separate from complete library search |
| Confidential data | [api/confidential.py](../../server/app/api/confidential.py), [confidential.py](../../server/app/services/confidential.py) `update_field/set_value/list_values/history/reveal` | Field label/archive updates mutate a metadata row with a revision counter; there is no field-metadata revision archive. Value versions are immutable and encrypted. Legacy value writes serialize under a field lock without an optimistic precondition; management writes add owner-specific field/value CAS |
| Console | [router.js](../../web/src/router.js), [OrgProfiles.vue](../../web/src/views/OrgProfiles.vue), [OrgConfidential.vue](../../web/src/views/OrgConfidential.vue), [OrgTaskBoard.vue](../../web/src/views/OrgTaskBoard.vue), [api.js](../../web/src/api.js), [task-authority.js](../../web/src/task-authority.js) | Profiles, embedded certificates and confidential fields already have pages. Product, feature, certificate/profile and template management pages are implemented; org model settings, bounded confidential management, org memory and task feedback pages are implemented. The org path allowlist must gain exact new paths, not an unrestricted API proxy |
| Team and cost | [task_workflow.py](../../server/app/services/task_workflow.py) `live_actor/access`; [task_authorization.py](../../server/app/services/task_authorization.py) `task_authorized`; [contracts.py](../../server/app/schemas/contracts.py) `CONTRACT_VERSION/Cost/Result`; [budget](budget.md) | Task membership/archival and Result 4.0 budgets are present. Earlier passages in memory/assessment plans saying task membership is absent, or in check plans saying budgets are not enforced, cannot describe these pages' gates |

The design says every resource change is versioned. Confidential field metadata
and the proposed independent lifecycle state are explicit exceptions to *content*
revision history; neither may be presented as a historical copy of all fields.
The design's certificate-expiry reminders remain deferred; this page offers an
explicit-date inspection, not scheduled notifications.

Memory currently consumes reviewed org rules in response-card (响应卡) drafting.
It does not claim that extraction, check, score or the built-in agent all consume
memory. Automatic proposals copy sanitized feedback from human reject/edit of
model-derived cards; risk false positives, general LLM learning and historical
backfill are outside the implemented path. The absent
`docs/plan/memory/memory_contracts.py` is not an import target in this checkout;
the shared types live in [schemas/memory_contracts.py](../../server/app/schemas/memory_contracts.py).

## Pages, ownership and flows

Paths include the router's `/app` base. Lists have search/filter controls,
25-row pages, empty/loading/error states and a clear next action. Detail shows
current or explicitly selected revision, lifecycle state, author/time, declaration
warnings and separate content/lifecycle histories. Show “maintained by technical
staff / bid specialists / org administrators” from the permission matrix and
“last revised by” from a verified exact-revision audit (审计) association. Existing
revision rows have timestamps but no author column: use null/“unknown” when no
unique author can be recovered, never the root creator or current viewer as a
substitute. Include certificate-file and simulated-resource creation audit paths.
Resolve authors for only the page's revision IDs through one indexed audit query.
Do not invent an assigned person or derive ownership from the last editor. Task
owners/assignees remain in team workflow.

The implemented resource detail projections add `revised_at` and nullable
`revised_by` for the exact viewed revision. This avoids walking content-history
pages to find a historical author; the existing resource revision write receipts
and legacy list outputs remain unchanged.

| Page | Flows and next-step cues | Deactivation/history behavior |
| --- | --- | --- |
| `/app/org/products`, `/:id` | Current products; name/vendor/model search; create; detail; revise full `ProductData`; exact model/version and source URLs; link to task selection only in an authorized task context | Proposed root deactivate/restore. Distinguish declared from simulated proposal (模拟拟投) provenance through the root-level `SimulatedResource` marker, including old revisions |
| `/app/org/features`, `/:id` | Filter by same-org product and implementation state; create/revise description; show “implementation declared, material still needed” when appropriate | Same lifecycle. Changing a feature does not replace screenshots or confirm a prototype (原型) |
| `/app/org/profiles`, `/:id`; certificate section and `/app/org/certificates/:id` | Extend `OrgProfiles.vue` and its `CertificateSection` for detail/history and bounded reads. Certificate flow is metadata → upload original if needed → inspect exact revision → select in task | Full declaration revisions; root lifecycle. File step has ordered PDF/PNG/JPEG parts and rotation, a new revision receipt, and an explicit “no original on this revision” state |
| `/app/org/templates`, `/:id` | Admin uploads DOCX and declared chapters/project types; every revision needs a DOCX. Detail separates file validation, binding inspection and task selection; authorized humans open the original | Root lifecycle; immutable content/file and binding history. Upload validation is not export approval |
| `/app/org/templates/:id/revisions/:revisionId/bindings` | Human admin configures six fixed sections, previews binding, reviews static content and explicitly creates binding against the returned hashes. Human bidder may inspect existing bindings | No binding edit/delete/enable. Create another binding; legacy `current=false` binding cannot export |
| `/app/org/settings/models` | Read effective source/current revision; admin selects platform catalog model or enters BYOK configuration; save → reread safe metadata → optional explicit cost preflight and connection test | Immutable config history. No delete/reset-to-default/deactivate action is invented; switch to another valid config explicitly |
| `/app/org/memory`, `/:id` | Candidate (候选), active, disabled, expired filters; org rules/preferences; propose; exact-revision edit; human admin approve/reject/disable; history and authorized source links | Editing active content withdraws its old effectiveness immediately and creates a candidate. Disabled entries require a new edit and approval; no direct enable |
| `/app/org/tasks/:taskId/memory-feedback` | Task-authorized feedback queue; retry a recorded candidate job from exact events; link to resulting candidate and current owner action | No event editing, free-text invented feedback or historical backfill; evaluations remain an advanced API/CLI workflow |
| `/app/org/confidential` | Extend existing field definitions, archive/unarchive, masked current values and value history; org/task scope shown explicitly; edit uses a blank value control, never prefilled secret text | Key/kind/scope fixed. Field metadata revision is a concurrency counter; value history is append-only. Reveal is a separate explicit existing human action |

No delete action for ordinary library resources. Proposed deactivation is
reversible and affects future selection, not past pins or released documents.
Authorized maintainers may revise an inactive resource; saving content does not
restore it. Historical content is read-only; copying it into a new edit still
requires the current head's revision precondition and a deliberate save.
The confirmation dialog states that continuing tasks retain their selected
versions; it does not claim to know how many inaccessible tasks use the resource.
A matching no-op selection of an already active pin remains idempotent even when
the root is inactive; any new pin or revision replacement is refused.

Existing task selection endpoints pin by **task + resource root + normalized
lot**, not “one item of each kind per lot.” Selecting a different root does not
remove the old root. Keep that distinction in the selection dialog. Resolve the
revision explicitly before posting; display the exact returned selection/revision
receipt. Library saves never post selection writes in the background. Changing a
task selection uses existing invalidation and re-review behavior, including
confirmed responses (响应); no checkbox in a library can clear those gates.

Unsaved edits stay in memory; navigation warns before discarding them. Retain only
org-namespaced nonsecret IDs and nontext filters in current-tab state. Never put
search text, resource bodies, memory content, keys, values, file bytes or signed
links in URL query text, localStorage, IndexedDB, telemetry or persistent caches.
On org switch/logout/authority loss, abort requests, discard late results through
`api.js`'s org epoch, clear forms and release previews. Use authenticated same-org
fetch for short-lived file links; a signature alone is not authorization. Preserve
`DocumentPreview` and existing CSP/image handling, not arbitrary iframe URLs.

### First vertical slice

The implemented first slice is **product list → create → exact revision detail → revise → history
→ explicit task selection**, including product deactivate/restore and lifecycle
history, using the product read projection and existing content/selection writes.
The completion example is two tasks selecting revision 1, creation of revision 2,
then explicit replacement in one task: the other still uses revision 1. Show the
current maintainer role, real author/time, selected revision and next action at
each step. Test with human technical/admin writers, bidder/viewer readers, a task
observer, an archived task and another org.

This slice includes bounded read indexes and product lifecycle storage. Existing
products start at `active/revision=0` without fabricated events. No provider calls
or new UI framework are introduced. Each slice must complete its API/CLI/browser
and isolation acceptance before release.

### Second vertical slice

The feature slice implements `/app/org/features` and `/app/org/features/:id`,
including bounded name-prefix search, same-org product and implementation-state
filters, creation, full-description revision, exact revision detail, content and
lifecycle history, and explicit task pinning. Implemented declarations still need
material; feature maintenance never replaces screenshots or confirms evidence.

Migration [0050](../../server/migrations/versions/0050_feature_library.py) adds the
feature lifecycle baseline and extends lifecycle events with nullable product and
feature arms, exactly one root per event, composite root/revision foreign keys and
per-arm unique sequence indexes. Certificate/profile and template arms are added
by their respective slices below. An inactive
parent product blocks new feature associations and new/replacement feature pins;
existing exact active pins remain idempotent and historical pins stay intact.

### Third vertical slice

The certificate/profile slice extends `/app/org/profiles` and `CertificateSection`
with bounded prefix search and lifecycle filtering, plus `/app/org/profiles/:id`
and `/app/org/certificates/:id` for exact declarations, complete revisions, separate
histories and explicit authorized task pins. Certificate detail includes only its
selected revision's original/parts. Metadata-only revisions explicitly have no
original; uploads return a new revision for deliberate inspection. Date advice uses
an explicit `as_of`, and original downloads preserve the existing same-org signature,
authentication and file scopes. No confidential values are loaded into these forms.

Migration [0052](../../server/migrations/versions/0052_certificate_profile_library.py)
extends the retained lifecycle table with certificate/profile arms, exactly-one-root
and composite revision constraints, per-arm indexes, FORCE RLS and human bidder/admin
guards. It adds safe prefix indexes and page-revision audit indexes, including
certificate-file creation authors. New human-only lifecycle scopes are guarded in
both application identity checks and the database token CHECK. Selection guards keep
task-first locking and run business checks after RLS/composite foreign keys. Existing
pins, file bytes and evidence-source archives are retained.

### Template vertical slice

The template slice implements `/app/org/templates`, `/app/org/templates/:id` and
the exact revision's bindings page. Each content revision requires a real DOCX;
the detail keeps file validation, original inspection, immutable binding review
and explicit task selection separate. Human administrators configure six ordered
sections, preview against the template hash, inspect the original's static content
and explicitly create against the returned static hash. Human bidders inspect
existing bindings; legacy bindings remain visible with `current=false`.

Migration [0054](../../server/migrations/versions/0054_template_library.py) adds the
template lifecycle arm, bounded-read indexes and original-file human scope guard.
Inactive templates reject new or replacement task pins while preserving an exact
active-pin replay and historical selections. Direct binding links resolve their path revision UUID with the additive bounded
`GET /v4/management/resources/templates/{id}?revision_id=UUID`; `TemplateDetailQuery`
accepts either this UUID or the existing numeric `revision`, never both. The CLI
`resource template show` also accepts `--revision-id`. Wrong-parent UUIDs return 404.
Original reads require
`template:read` and human-only `template:file:read`; template upload retains its
existing token eligibility. Binding preview/create retain their existing human gates.

Database, real-browser and measured fixed-scale acceptance of the integrated
slices remains pending; DB-free checks do not complete that acceptance.

### Model-settings vertical slice

The model-settings slice implements `/app/org/settings/models` and the provider
section below. Read interfaces use explicit secret-free metadata projections,
exact revision author/time and independent keyset history/catalog pages. Optional
`ProviderCatalogQuery.q` searches a literal, casefolded catalog-ID prefix; it
extends the original `PageQuery` proposal to apply the approved prefix-search
browse behavior without exposing endpoints or credentials.

New platform saves retain catalog revision and published sale prices alongside
saved provider/model/reasoning identity. Older immutable revisions without price
snapshots expose null prices; no live default supplies missing history. Migration
[0056](../../server/migrations/versions/0056_model_settings.py) adds read indexes
and preserves declarative constraint rejection before provider business guards.
Existing human-only `provider:write` and token CHECK remain authoritative; this
slice introduces no new scope or secret-history table.

[The mechanism note](../notes/model-settings.md) owns implementation details.
DB/API isolation, fixed-scale and mocked-browser scenarios are implemented;
database execution, actual browser execution and integrated performance acceptance
remain pending in the integration runtime. No real provider or paid test is used
for implementation validation.

### Confidential management slice

The confidential slice extends `/app/org/confidential` with field registration,
label revision, archive/unarchive, separate bounded definition/current-value views,
masked value history and owner-specific CAS. Task pages link to this same screen
with an explicit task ID. Legacy task-panel and CLI append writes remain compatible.
Field metadata revisions remain counters; there is no metadata revision archive,
historical-label restore or invented revision author. Value rows retain their real
`set_at`/`set_by` metadata. Reveal uses the existing audited human action.

Migration [0055](../../server/migrations/versions/0055_confidential_management.py)
adds generated key/label search metadata, owner-specific bounded-read indexes and
an AFTER metadata-update guard. Existing FORCE RLS, composite keys, column grants,
append-only value grants and human-only token checks remain in force.
`ConfidentialQuery.field_id` is an additive exact filter for editor hydration:
prefix matches can exceed one page, so a selected field must not be resolved by
searching only the first 25 matches. It filters both definitions and masked current
values and binds continuation cursors, without adding a new detail route.

Database, real-browser and measured fixed-scale acceptance remain pending.
[Confidential mechanism](../notes/confidential-values.md#bounded-management-and-checked-writes)
defines the storage, read and concurrency boundaries.

### Memory vertical slice

The memory slice implements `/app/org/memory`, `/app/org/memory/:id` and
`/app/org/tasks/:taskId/memory-feedback`. It reuses candidate creation and exact
revision mutations, with server action hints, explicit active-edit withdrawal,
hash-only decision history, server expiry and task-redacted sources. The task
queue recovers saved jobs from their exact event manifests and links their
resulting candidates; dispatch warnings retain the successful human card decision.

The additional management detail/history routes preserve existing memory show and
history outputs. `GET /v4/tasks/{T}/memory-feedback?management=true` selects bounded
`PageData` / `MemoryFeedbackRow[]`; omitting the flag retains existing feedback
API/CLI schemas. No new scope or token grant is added. Candidate submission and
worker checks now enforce the existing task membership and archival boundary.

Migration [0057](../../server/migrations/versions/0057_memory_management.py) adds
current-root search projections, prefix/tag/filter/keyset indexes and a dedicated
indexed memory audit revision column. The memory root guard preserves existing
state and scope-epoch checks while validating derived search refreshes after
row constraints. No feedback backfill, approval rewrite or new memory table is
introduced. The [mechanism note](../notes/memory-management.md) describes the reads.
Database, real-browser and fixed-scale acceptance must run against the integrated
migration chain; static checks and mocked test discovery do not establish them.

## Permission and task boundaries

Authority comes from [auth.py](../../server/app/services/auth.py)
`SCOPES/ROLE_SCOPES/HUMAN_ONLY_SCOPES/Identity.require`, current Membership, and
service checks. No writable payload accepts org/actor/approver/time fields.
Read-only `org_id` in projections comes from the authenticated context.

| Operation | admin | bidder | technical | viewer | API tokens |
| --- | --- | --- | --- | --- | --- |
| Read product/feature/certificate/profile/template libraries | Yes | Yes | Yes | Yes | Corresponding granted read scopes |
| Create/revise product or feature | Yes | No | Yes | No | `resource:write`, intersect issuer's current role |
| Create/revise certificate and upload original | Yes | Yes | No | No | `certificate:write`, plus `certificate:file:write` for files |
| Create/revise org profile | Yes | Yes | No | No | `profile:write` |
| Create/revise template file | Yes | No | No | No | Existing `template:write` may be issued; upload itself is not human-only |
| Open template original | Yes | Yes | Yes | Yes | Never; human `template:file:read` plus `template:read` |
| Library deactivate/restore | Yes | Certificates/profiles only | Products/features only | No | Refused by explicit human-session gate, even with a resource write scope |
| Read/create export binding | Both | Read | Neither | Neither | Neither; preserve `exports.human_access` |
| Read model config/catalog/history | Yes | Yes | Yes | Yes | `provider:read`; no platform secrets |
| Set provider config/test connection | Yes | No | No | No | Never; human `provider:write` |
| Read/propose org memory | Both | Both | Both | Read | `memory:read/write`; see edit ownership below |
| Approve/reject org memory | Yes | No | No | No | Never; `memory:approve` |
| Disable/logically delete/read deleted memory | Yes | No | No | No | Never; `memory:manage` |
| Read masked confidential metadata | Yes | Yes | Yes | Yes | `confidential:read` |
| Define/archive fields, set/reveal values | Yes | Yes | No | No | Never; `confidential:write/reveal` |

The same service rules apply from UI, CLI and API. Tokens never receive **any**
human-only scope, including `evidence:confirm`, `export`, `template:file:read`, `provider:write`,
`confidential:write/reveal`, `memory:approve/manage/eval:read/eval:review`, or team
human scopes. Token creation and database CHECK protections remain in force.
Lifecycle uses a session gate plus the resource's existing write scope;
it does not add a broadly issuable management scope. Certificate/profile lifecycle
also requires the matching human-only `certificate:lifecycle` or `profile:lifecycle`
role scope; both are refused by token issuance and the database CHECK. Built-in agents have their
own narrower `AGENT_SCOPES`; token eligibility does not expand that allowlist.

Org library permission confers no task permission. Any source link, feedback,
task confidential value, selection or return-to-task action checks
`task_workflow.access` and its parent relationship. Owner/contributor writes are
limited by org scopes; reviewers cannot select resources or run ordinary jobs;
observers read and discuss only. Archived tasks reject business writes. Org admin
recovery reads/member management do not grant task writes without membership or
a review domain (职责). Assignment alone is not authority. Export remains subject
to the human bidder gate and actual task access; even an admin resource maintainer
does not gain a commercial or technical confirmation domain.

A missing, foreign-org or inaccessible task/object returns indistinguishable 404.
After visibility is established, a forbidden operation returns 403. Recheck live
Membership, token issuer/scopes and task authority on every page and mutation;
action hints are display data only. Redact inaccessible task provenance through
`memory.crud.visible_sources` rather than exposing source IDs with disabled links.

## Revision, approval and secret flows

### Resource and file revisions

Reuse the `*Create`, `*Update` and `Task*Selection` types in
[resource_contracts.py](../../server/app/schemas/resource_contracts.py),
[feature_contracts.py](../../server/app/schemas/feature_contracts.py),
[certificate_contracts.py](../../server/app/schemas/certificate_contracts.py),
[profile_contracts.py](../../server/app/schemas/profile_contracts.py) and
[template_contracts.py](../../server/app/schemas/template_contracts.py).
Updates send the complete declared data and `expected_revision`; no UI-only patch
semantics. On conflict retain unsaved nonsecret edits, reread the exact server
revision, and require a new deliberate save. Never auto-merge/retry.

A certificate file upload uses `CertificateFileCreate`, ordered parts, existing
20-part/200-page/40-MiB bounds and any stricter deployment limit. Metadata and file
belong to the same new revision. Do not quietly attach a previous PDF to a new
metadata-only version. Certificate download needs both `certificate:read` and
`certificate:file:read`; file upload also needs both write scopes. Human evidence
confirmation is still downstream of original inspection and task selection.

Templates preserve their DOCX hash and declared chapter bounds (200 nodes, six
levels). Binding uses `ExportBindingCreate(dry_run=True)`, then the exact
`expected_template_sha256` and returned `expected_static_content_hash`, with the
six ordered sections defined by `export_contracts.SECTION_ORDER`. Four-column
response layouts and widths follow the existing schema. Review template content
before the human admin creates the immutable binding. A new template revision
needs its own binding; no binding is copied as implicitly reviewed. Export
preflight/release and prototype keep/replace decisions stay on task screens.

### Memory and feedback

Reuse `MemoryCreate/Update/Decision/Disable/Delete` and view types; the plan module
does not redefine their approval logic. Creation is always candidate. Nonadmin
writers can edit only their own (and, for a token, same-token) never-effective
candidates; admins retain the service's management authority. An edit appends a
candidate and withdraws any previous active content immediately. Show that effect
before saving. Approval applies only to exact `expected_revision`; conflicts for
an unexpired active entry with the same kind/conflict key require a human decision,
not last-write-wins. Reasons are required but only their hashes are retained;
history must not promise readable original decision text.

Reject creates disabled state. Disabled → edited candidate → human approval is
the only reactivation flow. Expiry derives from server time without rewriting old
revisions; distinguish stored `status=active` from `effective_status=expired`.
Logical delete preserves history and is irreversible through this UI; keep it
API/CLI-only initially. No memory content is evidence or a source of confirmation.

`feedback.record_feedback` records successful human edits/rejections of
model-derived cards and evaluation samples transactionally. Confirm events can
produce samples but cannot become memory candidates. `candidates.submit_candidates`
uses exact existing events and a durable job; `feedback-copy-v1` is deterministic,
sanitized, free of model calls and never automatic approval. Preserve a human
card decision if dispatch fails; show the saved job and explicit recovery action.
Source task access is required. Memory safety rejects sensitive prices/identity/
bank/credential patterns and registered confidential values, including historical
values, independently of outbound-redaction settings. Do not suggest disabling
redaction (遮挡) to save a rejected memory.

### Provider and model configuration

Org pages follow [provider-config.md](provider-config.md) and
[platform-credentials.md](platform-credentials.md), not the platform credential
UI. Limit capability to `llm_extract`; do not offer task profiles or unsupported
OCR/vision/search/embedding switches. Display effective platform/org/unconfigured
source, config revision, model, official reasoning levels and published sale
prices. Current/history retain the saved nonsecret provider/model identity even
when a platform model is no longer enabled; show unavailable and retain its ID.
Never replace that identity, price or reasoning with the enabled default. Platform
reasoning choices expose only the catalog’s name/label, while BYOK inputs retain
their existing editable reasoning schema. A configured key is not proof of a successful call.

Use an explicit allowlist projection: no existing key, ciphertext, `key_last4`,
fingerprint, credential ID, platform endpoint or wholesale prices. Existing
`ProviderConfigView` contains a suffix, so the proposed metadata projection is a
new type, not a generic serialization of the database row. BYOK's nonsecret
HTTPS endpoint is visible; platform endpoint metadata is not.

The only credential input is a fresh, blank password control for a one-time
`ProviderConfigSet.api_key` write. No show/copy toggle, prefill, value echo,
validation-input dump, autocomplete storage, logging, screenshot or trace of that
value. Construct `ProviderConfigInput` fields explicitly, add the new secret only
in the dedicated submit transport, clear it immediately, and never serialize an
output view back into a request. `SecretStr/exclude=True` does not itself build
the wire request: the dedicated transport handles the transient input once.
Discard the legacy write response's suffix and reread the safe projection.

Omitting the key means reuse only when the existing service permits the same
provider and endpoint. First BYOK setup or provider/endpoint change requires a
new key; platform selection refuses any key/overrides. This is server-side reuse,
not browser round-tripping. Use `expected_revision` (null only for initial setup).
Queued jobs retain their selected config revision; changing a default is not a
repair of old jobs. Platform credentials are resolved per call under ADR 0006;
org pages cannot read, replace, disable, test or import those secrets. Key failure
must not fall back to another model, anonymous access or an environment key.

Existing `GET /providers` may query DeepSeek balance, so these pages use the new
metadata-only read. Vendor balance refresh and detailed monthly per-revision
usage remain CLI/API-only initially. No decryption or vendor call is hidden in a
new metadata endpoint. Platform catalog choices are separately paginated.

Connection test uses existing `BudgetProviderTest` (including `dry_run`) in
[budget_contracts.py](../../server/app/schemas/budget_contracts.py), not only the
older `ProviderTest`. Test the **saved effective** configuration using synthetic
input, show preflight and actual Result cost, and require an explicit start.
Unsaved edits must be saved or discarded first. A test may incur vendor/platform
charges; BYOK zero platform charge does not mean free usage. This endpoint does
not provide a binding hash for the intervening save race: show the actual tested
config ID from its receipt and resolve that exact ID through the proposed safe
revision-detail read. The existing receipt has no config revision field. A null
config ID means platform default; show actual usage/job model identity when
available, without claiming a catalog revision was pinned by the preview. Flag
a changed selection instead of claiming the previously displayed revision was tested. A network timeout never
automatically repeats a paid test.

### Confidential fields

Reuse `ConfidentialFieldCreate/Update`, masked `ConfidentialValueView` and the
existing `POST /confidential-values/{value_id}/reveal`. Field key/kind/scope cannot
change. Archive blocks new placeholder use under `check_placeholders`, unlike
library deactivation; it can affect later draft/export gates and must be labeled
accordingly. No historical field-label restore is promised.

List/detail/history never carry plaintext values. Preserve existing masked-tail
policy only for authorized confidential views; amounts and short values have no
tail. New values enter a blank write-only control. `ConfidentialValueRevisionSet` adds exact field/value compare-and-swap (CAS)
preconditions under the existing field lock, comparing the current value for the exact field and
org/task owner. A required null expected value asserts absence for that owner,
not absence across all tasks. The dedicated submit transport must send the
transient value once even though generic model serialization excludes it; after
CAS the service explicitly constructs `ConfidentialValueSet(value=command.value,
task_id=command.task_id)` for the existing setter, without the new precondition
fields. Existing API calls remain append-only
without that precondition; the new console uses the guarded route and does not
claim all old clients now reject concurrent writes.

Reveal is explicit, one value at a time, reauthorized and audited by the existing
service; it is never triggered for form hydration, search, preview or history.
Clear revealed text on close/blur, page hide, navigation, logout or org switch.
No automatic clipboard operation or persisted revealed content. Task-scoped values
retain actual task access checks; org-wide values do not bypass those checks for
task content. No raw values enter memory, model settings, resource search or audit.

## Data model and migration outline

The first read-only projections introduce no duplicate business entity. Existing
storage remains authoritative:

| Data | Existing tables / migration sources |
| --- | --- |
| Products/features/certificates/profiles/templates | Roots, `*_revisions`, task selection tables in [0003](../../server/migrations/versions/0003_versioned_products.py) through [0007](../../server/migrations/versions/0007_versioned_templates.py); exact roots/revisions/selections are in [models/entities.py](../../server/app/models/entities.py) |
| Certificate files/parts and simulation provenance | `CertificateFile/CertificateFilePart/SimulatedResource` in [models/entities.py](../../server/app/models/entities.py); keep revision and root associations |
| Export bindings | `export_template_bindings`, [0021_exports.py](../../server/migrations/versions/0021_exports.py) |
| Provider configs | `provider_configs`, [0020_provider_configs.py](../../server/migrations/versions/0020_provider_configs.py); immutable encrypted revisions |
| Memory | `memories`, `memory_revisions`, `memory_scope_epochs`, `memory_feedback_events`, `memory_eval_samples`, `memory_retrievals`, `memory_retrieval_items`, `memory_call_inputs`, [0037_memory.py](../../server/migrations/versions/0037_memory.py) |
| Confidential fields/values | [models/confidential.py](../../server/app/models/confidential.py); retain encrypted immutable values and existing scoped uniqueness |
| Audit/identity/task authority | `audit_logs`, Membership, TaskWorkflow/TaskMember, existing [team workflow tables](team-workflow.md#data-model-and-migration-outline) |

These are org business tables; preserve NOT NULL `org_id`, enabled **FORCE RLS**,
USING/WITH CHECK tenant policies and org composite relationships. Approved global
`platform_models` and `platform_credentials` exceptions stay in their existing
contracts; neither becomes a tenant-owned copy or a newly exposed org table.

### Proposed lifecycle storage

Add `lifecycle_state` (`active|inactive`, NOT NULL, default active) and
`lifecycle_revision` (NOT NULL integer >= 0, default 0) to each of the five roots.
Zero is the existing baseline, not an invented historical event. Content revision
numbers and payloads do not change when lifecycle changes.

Add one **`resource_lifecycle_events`** table, with NOT NULL `org_id`, UUID `id`,
`revision >= 1`, `resource_revision >= 1`, before/after states, fixed `reason_code`,
`actor_user_id`, `actor_kind='session'`, and timezone-aware `created_at`. It has
`UNIQUE(org_id,id)`, ENABLE/FORCE RLS and tenant USING/WITH CHECK. It stores no
resource body, free-text reason, secret, task IDs or counts.

Avoid an unconstrained polymorphic foreign key: use five nullable columns
`product_id/feature_id/certificate_id/profile_id/template_id`, a CHECK requiring
exactly one, and individual composite FKs `(org_id, <root_id>)` to each real root.
For the selected root, bind `(org_id, <root_id>, resource_revision)` to that kind's
existing immutable revision key. Five partial unique indexes enforce
`UNIQUE(org_id, <root_id>, revision)` when that root column is non-null. Actor
references `(org_id,user_id)` Membership. The public `ResourceRef` normalizes this
storage shape; `kind/resource_id` alone is not the database referential boundary.

Runtime gets SELECT/INSERT only on events; triggers reject UPDATE/DELETE and
verify current human/role authority, sequential lifecycle revision, real transition
and the matching root state. A controlled transition atomically updates root,
appends event and writes audit. Revisions/snapshots remain immutable. No new
BYPASSRLS role or global table is introduced.

Selection must lock task/workflow first, then the resource root (matching existing
`versioned.select_revision` order) and reject an inactive root before any new or
replacement pin is published, including selection of an older content revision.
Only an exact existing active pin may return the existing duplicate receipt
without a write. Add a database selection guard for all five selection tables;
validate active inserts and any activation, not only the UI route. Lifecycle
changes lock only the root and never traverse/lock tasks, avoiding a reversed
lock order. Feature create/revise must reference an active same-org product root;
an existing pinned feature remains valid if its product is later deactivated.
A feature linked to an inactive product is not newly selectable, even if its own
root remains active; check both roots in stable ID order after task locks.

### Migration sequence

1. Add bounded read indexes, including org/root/created-at keysets and revision
   indexes. Index normalized safe search fields using PostgreSQL built-in text
   search/prefix support; do not add an embedding service or third-party extension.
   Inspect query plans before promising substring search. `q` matches casefolded
   prefix tokens in the fields defined below; no wildcard/regex syntax.
2. For lifecycle slice only, add baseline columns/event table, all composite
   constraints, RLS policies, immutable/event/selection guards and grants in one
   migration. Validate all existing roots as active with revision 0; retain all
   selections and history. Do not fabricate events or reselect tasks.
3. Deploy services and guards before enabling lifecycle actions. Mixed versions
   that can bypass lifecycle must not serve selection writes. Backfill/index
   construction batch sizes and lock duration require the migration review;
   no business data repair is implied by approving this page draft.
4. Confidential CAS and metadata projections reuse existing tables/locks; no
   plaintext copy or new credential-history table. No new memory or ownership
   tables. If a later feature needs one, it requires its own org composite
   constraints and isolation contract.
5. Recover by disabling the affected page/write entry while preserving guards,
   rows and event history; repair forward. Never downgrade by dropping audit or
   restoring a service that silently allows inactive selections.

## HTTP and Pydantic interfaces

Product, feature, certificate, profile and template query, detail, history and
lifecycle paths, binding query/detail, provider metadata/current/history/catalog
reads, confidential query/history/CAS and memory browse/detail/history are implemented
under `/v4`. Paths in existing tables omit `/v4` for readability; new pages request
version 4 explicitly through `orgRequest`/`api.request(contractVersion:4)`. Existing
unprefixed routes retain compatibility projections. Register fixed query/history
paths before UUID routes and extend the browser allowlist by exact route pattern.

### Bounded additions

`K` is `products|features|certificates|profiles|templates`; `R` is one root UUID.
These read-only POST queries put search text in the body, not browser URLs.
All page returns are `Result.data=PageData`, `Result.items=typed rows`, never a
second nested envelope or a full-library metadata map. Detail objects occupy
`Result.data` with `items=[]`.

| Proposed HTTP route | Request → data/items | Service Protocol / authority |
| --- | --- | --- |
| `POST /v4/management/resources/{K}/query` | `ResourceQuery` → `PageData` / `ResourceRow[]` | `ManagementResourceReads.query`; kind's read scope |
| `GET /v4/management/resources/{K}/{R}?revision=N` | `ResourceDetailQuery` → `ResourceDetailData` | `detail`; latest when revision omitted, exact historical revision otherwise; certificate detail accepts optional explicit `as_of` |
| `GET /v4/management/resources/templates/{R}?revision_id=UUID` | `TemplateDetailQuery` → `TemplateDetailData` | Exact same-org root/revision UUID; numeric `revision` and UUID are mutually exclusive |
| `POST /v4/management/resources/{K}/{R}/history/query` | `PageQuery` → `PageData` / `ResourceHistoryRow[]` | `history`; one root only, fetch full content by exact detail |
| `POST /v4/management/resources/{K}/{R}/lifecycle` | `ResourceLifecycleSet` → `ResourceLifecycleData` | `ManagementResourceLifecycle.set_state`; human + kind's write scope |
| `POST /v4/management/resources/{K}/{R}/lifecycle/history/query` | `PageQuery` → `PageData` / `ResourceLifecycleEvent[]` | `lifecycle_history`; kind's read scope |
| `POST /v4/management/export-bindings/query` | `BindingQuery` → `PageData` / `ExportBindingView[]` | `ManagementBindingReads.query`; existing human admin/bidder gate |
| `GET /v4/management/export-bindings/{id}?template_revision_id=UUID` | `BindingDetailQuery` → `ExportBindingView` | `detail`; wrong template parent gives 404 |
| `GET /v4/management/providers` | No body → `ProviderSettingsData` | `ManagementProviderReads.settings`; `provider:read`, no balance or decryption |
| `GET /v4/management/providers/revisions/{id}` | No body → `ProviderRevisionMetadata` | `revision`; exact authorized config ID, including historical/disabled catalog identity |
| `POST /v4/management/providers/history/query` | `PageQuery` → `PageData` / `ProviderRevisionMetadata[]` | `history`; no per-row usage aggregation |
| `POST /v4/management/providers/catalog/query` | `ProviderCatalogQuery` → `PageData` / `PlatformModelChoice[]` | `catalog`; enabled org-visible catalog fields only |
| `POST /v4/management/memories/query` | `MemoryQuery` → `PageData` / `MemoryView[]` | `ManagementMemoryReads.query`; org scope, original ACL/safety/source redaction |
| `GET /v4/management/memories/{id}?revision=N` | `ResourceDetailQuery` → `MemoryDetailData` | Exact revision, current revision number, nullable audit author and server action hints |
| `POST /v4/management/memories/{id}/history/query` | `PageQuery` → `PageData` / `MemoryRevisionView[]` | Bounded descending history; deleted history requires human admin management authority |
| `POST /v4/management/confidential-fields/query` | `ConfidentialQuery` → `PageData` / `ConfidentialFieldView[]` | `ManagementConfidential.fields`; metadata only |
| `POST /v4/management/confidential-values/query` | `ConfidentialQuery` → `PageData` / `ConfidentialValueView[]` | `values`; authorized org or task owner context |
| `POST /v4/management/confidential-fields/{id}/values/history/query` | `ConfidentialHistoryQuery` → `PageData` / `ConfidentialValueView[]` | `history`; never decrypt |
| `POST /v4/management/confidential-fields/{id}/values` | `ConfidentialValueRevisionSet` → `ConfidentialValueView` | `set_value`; exact field revision/current value UUID (required null means absent), existing human/task gates |

The module reuses `Contract(extra=forbid)`, `Cost`, `Result`, resource revision
schemas, file views, `ExportBindingView`, provider inputs/reasoning and memory and
confidential views from `server/app/schemas/`. No alternate Provider SDK interface
is needed: management reads call no external capability; existing provider tests
continue through `providers/` and Processor/JobExecution. Protocols describe
server-side service boundaries, not permission to trust an arbitrary `Identity`
constructed by a client.

### Existing writes and reads retained

| Existing route family | Authoritative request/output and proposed page use |
| --- | --- |
| `POST /resources/{products|features|certificates|profiles}` and `POST /resources/{kind}/{id}/revisions` | Existing `Product/Feature/Certificate/OrgProfile Create/Update`; no new mutation wrapper |
| `POST /resources/certificates/{id}/file-revisions` | Multipart `metadata: CertificateFileCreate`, files; render exact returned file revision |
| `POST /resources/templates`, `POST /resources/templates/{id}/revisions` | Multipart `TemplateCreate/Update` + DOCX |
| `POST/GET /tasks/{T}/{products|features|certificates|profiles|templates}` | Existing `Task*Selection` and snapshot views; task screens own writes; library pages do not load all task selections |
| Certificate/template revision download-link/download and certificate page preview | Existing signed and authenticated binary routes in `api/resources.py`; open only selected revision/page |
| `POST /export-template-bindings` | `ExportBindingCreate` → `ExportBindingPreview` for dry-run, `ExportBindingView` when created; existing immutable/human gates |
| `POST /providers`, `POST /providers/test` | `ProviderConfigSet`; `BudgetProviderTest`, safe job/cost receipt; never echo newly supplied key |
| `POST /memories`, `GET/PUT /memories/{id}`, `GET /memories/{id}/history`, `POST /memories/{id}/decisions`, `POST /memories/{id}/disable` | `MemoryCreate/Update/Decision/Disable`, `MemoryData` or existing paged revision history; reuse exact-revision gate |
| `GET /tasks/{T}/memory-feedback`, `POST /tasks/{T}/memory-candidates`, `GET /jobs/{J}` | Existing paged feedback, `MemoryCandidateJobRequest`, authorized job recovery |
| `POST /confidential-fields`, `POST /confidential-fields/{id}/revisions`, `POST /confidential-values/{id}/reveal` | `ConfidentialFieldCreate/Update`, explicit `ConfidentialReveal`; no new reveal API or broadened rights |

Existing unbounded library lists, task selections and legacy provider/binding/
confidential reads remain available to current API/CLI clients. New management
pages must not use them as a preload followed by browser-only slicing. A future
bounded task-selector contract is separate; this draft does not claim all existing
task pages now have bounded lists.

## CLI and Result 4.0

[contracts.py](../../server/app/schemas/contracts.py) declares 4.0;
[compatibility.py](../../server/app/schemas/compatibility.py) and
[cli/main.py](../../cli/bid_cli/main.py) preserve legacy behavior. Every new command
supports `--json`, has a `bid schema` entry and calls the same service as HTTP.
No interactive prompt, local filesystem bypass of RLS or browser-only mutation.
Missing required input fails immediately. Existing command names/JSON are not
silently changed to paginated output.

| Proposed CLI command (append `--json`) | Interface mapping |
| --- | --- |
| `bid resource <product|feature|certificate|profile|template> browse --input QUERY.json` | `ResourceQuery` and resource query route; file holds bounded search filters/cursor/limit |
| `bid resource <kind> show --id R [--revision N]` | Exact resource detail |
| `bid resource <kind> history --id R [--cursor C] [--limit N]` | Bounded content revision history; distinct from legacy `list --history` |
| `bid resource <kind> lifecycle set --id R --input STATE.json` | `ResourceLifecycleSet`, human session only |
| `bid resource <kind> lifecycle history --id R [--cursor C] [--limit N]` | Bounded lifecycle history |
| `bid export binding browse --template-revision UUID [--cursor C] [--limit N]`, `bid export binding show --id UUID --template-revision UUID` | Bounded binding reads, original human gate |
| `bid provider show`, `bid provider revision show --id UUID`, `bid provider history-page [--cursor C] [--limit N]`, `bid provider catalog [--q PREFIX] [--cursor C] [--limit N]` | Safe metadata reads, never implicit balance queries |
| `bid memory browse --input QUERY.json` | `MemoryQuery`; complete management search, not relevance retrieval |
| `bid confidential field browse --input QUERY.json`, `bid confidential browse --input QUERY.json` | Bounded definitions/masked current values |
| `bid confidential history-page --field UUID [--task UUID] [--cursor C] [--limit N]` | Masked value history |
| `bid confidential set-checked --field UUID --expected-field-revision N (--expected-value UUID | --expect-empty) [--task UUID]` | Read value through existing stdin mechanism; build `ConfidentialValueRevisionSet`. Never place the value in argv or JSON files |

Retain existing `resource <kind> add/list/update`, certificate file commands,
`task resource/feature/certificate/profile/template add/list`, `export binding
create/list`, `provider set/list/history/test`, `memory add/list/show/update/
approve/reject/disable/history`, and confidential field/set/list/history commands.
Provider configuration JSON uses `ProviderConfigInput`, no key. Use the existing
protected `--key-file` path for BYOK; do not introduce a key argv option or expand
the platform contract's forbidden env fallback. Existing provider CLI compatibility
is not rewritten by this UI draft.

**CLI/API-only initially:** legacy all-row dumps, batch automation/import scripts,
template sample generation, vendor balance and detailed usage diagnostics, memory
retrieval inspection/used-call manifests/evaluation accept-exclude/logical delete,
credential migration and encryption-root rotation. Platform credential management
stays in its existing operator console/CLI, outside org management. User/project/
global memory and additional provider capabilities are unavailable, not secretly
available through a CLI fallback. Confidential reveal remains existing API/UI only;
there is no existing reveal CLI command to advertise.

A successful new read produces the shared seven-key envelope, for example an
empty product page (UUIDs are synthetic contract examples):

```json
{
  "ok": true,
  "command": "resource product browse",
  "data": {
    "org_id": "11111111-1111-4111-8111-111111111111",
    "as_of": "2026-10-06T12:00:00Z",
    "returned": 0,
    "next_cursor": null,
    "has_more": false
  },
  "items": [],
  "warnings": [],
  "cost": {
    "llm_tokens": 0,
    "ocr_pages": 0,
    "usd": 0.0,
    "basis": "zero",
    "charge": "0",
    "billing_currency": "USD",
    "task_amount": "0",
    "unpriced_calls": 0,
    "unresolved_calls": 0
  },
  "duration_ms": 12
}
```

Use the configured billing currency, not an unconditional USD literal. `usd` is
vendor cost, `charge` is platform debit, and `task_amount` is task liability;
Decimal amounts serialize as strings. Unknown estimates/prices remain null,
with unpriced/unresolved counts; provider tests display their actual cost rather
than a read wrapper's zero. No `contract_version` eighth top-level key.

| Exit | Meaning for these commands |
| --- | --- |
| 0 | Read or mutation succeeded; successful dry-run may still report a blocker |
| 2 | Invalid input/cursor/limit, proposed CAS/lifecycle conflict, forbidden filter combination, stale page; most existing resource revision conflicts |
| 3 | Retryable timeout/rate/queue failure; honor `Retry-After`; no automatic write retry |
| 4 | Authentication/authorization/not-found, unreadable/integrity failure, unsupported configuration or permanent provider failure |
| 5 | Existing durable candidate job partial outcome, or another existing command's explicitly partial receipt; inspect items/errors, never label complete |

Preserve real existing differences: template revision and certificate-file revision
conflicts currently use HTTP 409 with exit **4**, while ordinary resource updates
use 2. This draft adds new operations with consistent 2 without silently changing
old snapshots. Failures use `data.error={code,message,exit_code}` and existing safe
job/cost fields when present. Never expose raw validation input or vendor errors.
Read pagination is a successful bounded page, not exit 5. Integrity failure is not
permission to silently omit an item and call the page complete.

## Bounds and performance targets

These are acceptance targets, not measurements. Resource query searches normalized
prefix tokens in name; products also vendor/model; certificates also declared
number. Never search decrypted values, arbitrary URLs, file text or profile bodies.
Features accept product/status filters only; other kinds reject these fields.
Memory search uses sanitized current text/conflict key/tags and kind/stored status/
expiry, with explicit `include_deleted` admin gate. Tag matching is all selected
tags. Confidential search covers key/label only; `task_id` establishes authorization,
not a cross-task search. Optional `field_id` restricts either browse to one exact
same-org definition without weakening task authorization. `archived=true` includes archived definitions/values rather
than meaning archived-only; field definitions are org-wide even with task context.

| Bound | Requirement |
| --- | --- |
| Input | `limit=25`, range 1–100; search 1–200 chars, nonblank after trim; at most 20 memory tags; cursor <=2048 chars; complete query body <=16 KiB; service rejects feature-only filters on other kinds |
| Ordering | Resources by immutable root `(created_at DESC,id DESC)`; resource/lifecycle/config history by `(revision DESC,id DESC)`; bindings by `(reviewed_at DESC,id DESC)`; catalog by `id ASC`; memory by immutable root `(created_at DESC,id DESC)`; fields by `(key ASC,id ASC)`; value history by `(version DESC,id DESC)` |
| Current values | Keyset over field `(key,id)`, resolve only the page's current value for the authorized owner; one bounded join/lateral lookup, not an all-fields loop |
| Cursor | Authenticated/encrypted opaque cursor, 15-minute expiry, bound to org, user/token/actor kind, live authority fingerprint, resource/parent/task, normalized filters, order and keyset anchor. Foreign or invalid cursor fails without revealing its contents |
| Read consistency | Live pages with per-page `as_of`, not a frozen multi-page snapshot. SQL applies visibility/filter before LIMIT; concurrent changes can alter later matches. After a successful write or changed filters restart at page 1; never claim an exact total |
| Materialization | SQL `LIMIT limit+1` before projection; validate each projected row’s org/kind/root/revision against the authenticated request; no all-history load, no full `current_revisions/active_snapshot_ids`, no per-row queries. Target <=6 SQL round trips for a list, <=8 for a detail including authority |
| Payload | Complete Result page <=256 KiB, detail <=1 MiB. Reads fit the complete envelope and sign the retained last-row continuation before constructing `Page`; the Result adapter only validates final encoded bytes and never drops rows. Oversize details fail; a single oversize row fails `management_result_too_large`. No chopped JSON/text that could be mistaken for complete data |
| Concurrency | Maximum two in-flight reads per screen; debounce search 300 ms, cancel obsolete reads; no list polling. Job pages reuse terminal-aware foreground/backoff polling |
| Server | On the fixed CI fixture (two orgs, 10,000 roots and 100,000 revisions per org), serial warmed p95 <=500 ms list, <=750 ms detail/history; statement timeout 2 s, excluding file transfer/explicit provider jobs. Record query plans, rows visited, payload bytes and fixture/hardware parameters |
| Browser | First actionable list <=1.5 s on a fixed local built-app fixture, <=100 rendered rows, <=2 MiB accumulated page data, previous pages evicted. Detail loads content/files only when opened; maintain focus and keyboard navigation |

Indexes must demonstrate these bounds for the approved prefix search semantics;
unsupported arbitrary substring scans are not an alternative implementation.
Memory proposal/approval has separate existing whole-org safety/conflict reads;
provider legacy history has per-revision aggregation. This contract's read targets
do not certify those operations as bounded. Run scale acceptance for actual
write paths; an unresolved latency failure blocks that slice rather than removing
confidential-value safety checks. Memory list expiry must evaluate one server
`as_of` per request; browser clock does not decide effective status.

## Audit and failure handling

Reuse `versioned.audit` and existing org audit storage. Preserve actions such as
`resource.product.create/update`, the corresponding kind actions,
`resource.certificate.file.create`, task selection actions, provider configuration
and test actions, memory create/update/decision/manage actions, and
`confidential.field.*` / `confidential.value.set/reveal`. Verify exact existing
strings in each service at implementation; do not rename old audit events.

New lifecycle events use `resource.<singular-kind>.deactivate` or `.restore`, with
root ID, old/new lifecycle revisions, content revision, fixed reason code and real
session actor. Guarded confidential writes use the existing value-set audit in
the same transaction; no duplicate “frontend save” event. New query/detail reads
produce ordinary safe operational metrics only, not a new business event per row.
Binding review remains `export.binding_created`; test failures retain their real
job/cost audit. No client-supplied approver identity.

Audit records contain IDs, revisions, hashes, fixed codes and timing, never body
text, search strings, raw reasons, secret suffixes, values, keys, file bytes,
signed URLs or vendor response bodies. Commit mutation and audit together; audit
failure rolls back the mutation. Error telemetry includes a safe correlation ID
and fixed error code without request/response capture.

| Failure | Page behavior and server boundary |
| --- | --- |
| 401 / inactive org or Membership | Clear org session/content and require sign-in; stale tabs cannot save |
| 404 foreign/missing/inaccessible parent | Uniform unavailable view; do not show cached names or leaked counts |
| 403 role/human/task denial | Show required maintainer role; never manufacture a human identity or offer token escalation |
| `revision_conflict`, proposed `lifecycle_conflict` / `confidential_value_conflict` | 409; retain only nonsecret unsaved edits; refresh server state, clear approval checks and require deliberate resubmission |
| Proposed `resource_inactive` | 409/2 on new/replacement selection; retain existing pin. Show an authorized library detail link, not a hidden restore write |
| `affected_task_limit` | Preserve current library version; explain maintenance is blocked. No split update loop or frontend task enumeration |
| File limit/format/hash/unreadable-original failure | No usable new version claimed; exact file error, old version stays available. No placeholder original or fabricated certificate |
| Missing/legacy binding or changed static hash | Block binding completion/export handoff, refresh exact preview; never auto-acknowledge static content |
| Provider unavailable/disabled credential/quota/budget/unpriced | Show safe blocker and actual accounted costs. No key display, alternative provider fallback or automatic paid retry |
| Memory safety/conflict/unsupported scope | Keep nonsecret proposal for correction when safe; no force-approve/global promotion/redaction-off workaround |
| Candidate dispatch failure | Preserve human decision and durable job receipt; explicit existing recovery path, no duplicate proposal loops |
| Invalid/expired proposed management cursor | `management_cursor_invalid` 400/2 or `management_cursor_expired` 409/2; reset list; foreign-object direct reads stay 404 |
| Proposed result-size/query timeout | `management_result_too_large` 413/2 or `management_query_timeout` 503/3; narrow filters/retry reads, never silently truncate |
| Network loss during save/test | Outcome unknown; reread version/job before another deliberate action. Never infer failure means no write or no charge |

## Test plan and repeatable artifacts

The product, feature, certificate/profile, template, model-settings, confidential and memory slices have API/PostgreSQL,
CLI, fixed-scale and mocked-browser acceptance tests. The following remain implementation acceptance requirements for their
respective slices; neither code inspection nor mocked
Playwright proves database authorization. Use the project's fake Providers and
synthetic files; no real external services or secrets. The main integration
session owns any disposable database/service lifecycle.

### Database, HTTP and role acceptance

Use orgs A and B with the same names, similar keys, all four org roles, active and
removed Memberships, API tokens and real task owner/contributor/reviewer/observer
roles. Test every new route in the table independently for A success, B ID/cursor
misuse, absent org context, inactive identity, wrong same-org parent, and direct
HTTP bypass of a hidden button. Resource kind substitutions cannot evade scopes.

| Table/route set | Required proof |
| --- | --- |
| Five roots, revision tables and all five task selection tables; resource query/detail/history/lifecycle routes | A cannot read/write B via API or SQL; identical content does not combine results; immutable revisions; org/root/revision composite FKs reject mismatches; separate current and lifecycle versions; pin history preserved |
| New `resource_lifecycle_events`, each of its five root arms | NOT NULL org, FORCE RLS read/write/no-context behavior; wrong-org root/actor/revision fails; exactly-one-root and per-root sequence uniqueness; UPDATE/DELETE refused; raw illegal root/lifecycle changes rejected |
| All five selection guards, including feature's product | Race deactivate vs new/old-revision selection and restore; deterministic lock order; exact duplicate accepted without write; inactive insert/reactivation refused in SQL; existing confirmed material/export gates unchanged |
| `certificate_files` and retained parts, template files | Org paths and purpose/expiry signatures; authentication still needed; mismatched revision/file refused; upload new revision and metadata-only no-file state; retained bytes/hash unchanged |
| `export_template_bindings`, bounded binding read/create/preview | B binding IDs hidden; same-org wrong template hidden; token/technical/viewer refused; human admin static hash required; bidder read only; legacy adapter stays unusable |
| `provider_configs`, safe config/history/catalog/test | Two-org revisions, actor changes, expected_revision race, no N+1; no decryption/balance/vendor call from metadata reads; plaintext/ciphertext/suffix absent; platform credential/table/function denial retained; failed key test never falls back |
| All eight memory tables named above and management search plus existing action routes | Two-org SQL and HTTP boundaries per table/route; task-inaccessible provenance redacted; own-candidate edits only; exact approval, conflict key, expiry, active-edit withdrawal, disabled edit/reapprove, tombstone history, safety/feedback ownership and worker run fence |
| `confidential_fields`, `confidential_values`, bounded fields/current/history/CAS/reveal routes | Both org/task owners; wrong task 404; raw values absent until explicit authorized reveal; field archive gates; no-body logging; value CAS race, required-null first write and old API append semantics remain distinguishable |
| `audit_logs`, active Membership and TaskWorkflow/TaskMember joins | No A/B event leak, removed user cannot act via old cursor, archived task blocks writes, mutation+audit atomicity, role downgrade while dialog open |

Gate regression must prove unconfirmed evidence never enters draft/export, tokens
cannot obtain or invoke `evidence:confirm`, `export` or any human-only scope, memory
approval does not confirm evidence, and resource/template maintenance does not
satisfy prototype keep/replace or review-domain requirements. Library deactivation
must not erase historical sources or silently reconfirm dependent cards.

Exercise service HTTP/CLI flows end to end with fixed fake providers, then snapshot
`--json` for each new command and successful/failing existing writes used by these
pages. Cover exits 0/2/3/4/5, exact nine cost fields, Decimal strings, null unknowns,
version 4 routing, legacy version rejection for new-only commands, no extra keys,
noninteractive missing input, secure key-file/stdin handling and no secret echoes.
Use a canary input only in memory and assert its absence from outputs/logs/artifacts.

### Playwright mocked-API and real integration

Use [product scenarios](../../web/e2e/management-pages.spec.js),
[feature scenarios](../../web/e2e/feature-management.spec.js),
[certificate/profile scenarios](../../web/e2e/qualification-management.spec.js) and
[template scenarios](../../web/e2e/template-management.spec.js),
[model-settings scenarios](../../web/e2e/model-settings.spec.js),
[confidential scenarios](../../web/e2e/confidential-management.spec.js) and
[memory scenarios](../../web/e2e/memory-management.spec.js), following
[console-assessments.spec.js](../../web/e2e/console-assessments.spec.js) and
[team-workflow.spec.js](../../web/e2e/team-workflow.spec.js), using the built app and
stateful intercepted `/v4` API calls. Fixtures are test-only; production pages
must have no mock/sample/fixture fallback.

Cover product, feature, certificate/profile, template, model-settings, confidential
and memory slices and old task pins; filters/cursors/cancelled responses;
empty/error/oversize pages; two org switch with late response; all role/task matrices;
conflict with unsaved edits; revision/file/history distinction; inactive selection;
binding preview and static-hash mismatch; provider key submission once and absence
from DOM/storage/subsequent requests; explicit paid-test preflight/race/failure;
memory exact-revision approval/active-edit withdrawal/source redaction; confidential
CAS and explicit reveal clearing; keyboard/narrow-screen dialogs. Unknown paid-job
outcomes must not cause automatic resubmission. UI ownership/blocker/next-action
messages must match actual response authority and state.

Artifacts go under ignored `data/work/management-pages-validation/<run>/`, never
under `docs/`: sanitized screenshots, Playwright report, response-schema/permission
assertion summary and `result.json` with scenario IDs, pass/fail, fixture/schema
versions, tested browser/build identity and exact rerun command. Include no
screenshots/traces/HAR during secret entry or reveal; synthetic secret canaries
are asserted without being stored in artifacts. Keep traces only for sanitized
nonsecret cases. Artifacts must state `mocked_api` versus `real_api` explicitly.

After implementation, a representative browser rerun against an already serving
built app is:

```sh
cd web
E2E_BASE_URL=http://127.0.0.1:8000 E2E_OUTPUT=../data/work/management-pages-validation/browser npx playwright test e2e/management-pages.spec.js e2e/feature-management.spec.js e2e/qualification-management.spec.js e2e/template-management.spec.js e2e/memory-management.spec.js e2e/model-settings.spec.js e2e/confidential-management.spec.js
```

Each spec must validate that its resolved output directory is inside the
worktree's `data/work`, like existing console specs. It does not start services.
Separate real-API/browser acceptance uses disposable two-org fixtures and produces
an independent receipt proving actual API/DB gates. Run serial latency acceptance
with the fixed dataset, recording query plans/payload sizes and environment, then
repeat only failed/affected scenarios. Mocked-only success leaves database and
performance acceptance open.

## Decisions

The owner approved every recommended default; implementation follows these decisions.

| Decision | Approved default | Reason |
| --- | --- | --- |
| First slice | Product bounded browse/create/revise/history, product lifecycle and explicit task pinning | Small existing service surface demonstrates reusable material ownership and stable task inputs |
| Library lifecycle | Human maintainers deactivate/restore with independent lifecycle CAS/events; no deletion | Reversible withdrawal from future use preserves immutable content and ongoing tasks |
| Inactive duplicate selection | Allow exact existing active pin as no-op; reject new/replacement pins, including old revisions | Keeps idempotency without creating a path around deactivation |
| Feature whose product is inactive | Preserve pins; block new selection and new/revised feature association until restore | Avoids offering a newly selectable child of a withdrawn product |
| Ownership | Maintainer role and last editor; no new per-resource assignee table | Communicates responsibility without duplicating task ownership and assignment rules |
| Search and pagination | Explicit new browse routes/commands, live keysets, safe prefix tokens, no total count | Bounds work without breaking current all-row JSON or implying stable cross-page snapshots |
| Existing list compatibility | Keep legacy API/CLI commands; new pages never preload them | Avoids hidden truncation and makes bounded acceptance independently testable |
| Provider entry and secrets | Org `llm_extract` only, safe metadata reads, blank write-only BYOK field, separate operator credentials | Preserves approved scope and prevents secret round-tripping or hidden balance calls |
| Provider test race | Use current BudgetProviderTest; resolve exact receipt config ID through safe revision detail, label platform-default identity separately and explicitly retest if changed | Avoids pretending a nonbinding preview pins a later call; adding a revision-bound test is a separate interface decision |
| Memory scope and deletion | Org only; show approval/disable; keep logical deletion and evaluation review API/CLI-only | Focuses staff on actionable rules while retaining exact approval and evaluation boundaries |
| Confidential concurrent edits | Add guarded CAS route; leave legacy append API semantics intact | Prevents a stale browser from replacing a newer value without breaking existing clients |
| Template experience | Upload real DOCX, guided six-section binding and static-content review; no visual Word editor | Reuses validation and human gates with a manageable first UI |
| Conflict exit-code inconsistency | Preserve existing 4 for template/file revisions; new CAS uses 2; separately version any cleanup | Avoids silently changing published CLI snapshots |
| Performance sign-off | Fixed two-org scale fixture, bounded SQL/read bytes, real API plus browser artifacts | Measurable page targets distinguish mocked UX proof from isolation/performance proof |
