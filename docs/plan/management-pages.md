---
kind: plan
---

# Management library contract

## Goal and boundary

Adapt the resource library and installation settings to the identity boundary in
[ADR 0012](../adr/0012-single-user-installation.md). Preserve exact task-selected
revisions, explicit human decisions, immutable evidence and file checks. The
management read mechanisms have one home in
[management-pages.md](../notes/management-pages.md); field/value and provider/memory
mechanisms remain in their dedicated notes.

The schema in `server/migrations/baseline/` is authoritative. Management work must
use that schema without restoring tenant, membership, personal authorship, catalog
or collaboration data. Company profile resources remain business declarations.
All provider configurations use the human's own key. Platform credential, Clef and
trust-anchor routes are human-authenticated installation settings.

## Console adaptation

The console must use the typed management endpoints below and ordinary installation
authentication. Remove organization switching, role-specific affordances, authors,
operator login and catalog selection. Render server action hints; never infer a
human identity from a token. Existing company-profile wording may describe a company
without implying a tenant or user account.

Resource screens must present exact content and lifecycle revisions, bounded search,
explicit task pinning and conflict recovery. Binding review must invalidate its
acknowledgement whenever form data or preview hashes change. A certificate revision
without an original must stay visibly empty. File readability and declaration status
must never be presented as evidence approval.

Secret forms must never prefill, persist or reveal values automatically. Reveal is an
explicit human-only request and must clear on blur, close, navigation or logout.
Provider connection checks are explicit jobs with recorded costs. Memory decisions
must preserve exact-revision approval, safety checks and task scope.

## HTTP and Pydantic interfaces

Product, feature, certificate, profile and template query, detail, history and
lifecycle paths, binding query/detail, provider metadata/current/history
reads, confidential query/history/CAS and memory browse/detail/history use the Result 5.0 envelope. Older contract versions
are rejected. Register fixed query/history
paths before UUID routes and extend the browser allowlist by exact route pattern.

### Bounded additions

`K` is `products|features|certificates|profiles|templates`; `R` is one root UUID.
These read-only POST queries put search text in the body, not browser URLs.
All page returns are `Result.data=PageData`, `Result.items=typed rows`, never a
second nested envelope or a full-library metadata map. Detail objects occupy
`Result.data` with `items=[]`.

| HTTP route | Request → data/items | Service Protocol / authority |
| --- | --- | --- |
| `POST /management/resources/{K}/query` | `ResourceQuery` → `PageData` / `ResourceRow[]` | `ManagementResourceReads.query`; kind's read scope |
| `GET /management/resources/{K}/{R}?revision=N` | `ResourceDetailQuery` → `ResourceDetailData` | `detail`; latest when revision omitted, exact historical revision otherwise; certificate detail accepts optional explicit `as_of` |
| `GET /management/resources/templates/{R}?revision_id=UUID` | `TemplateDetailQuery` → `TemplateDetailData` | Exact root/revision UUID; numeric `revision` and UUID are mutually exclusive |
| `POST /management/resources/{K}/{R}/history/query` | `PageQuery` → `PageData` / `ResourceHistoryRow[]` | `history`; one root only, fetch full content by exact detail |
| `POST /management/resources/{K}/{R}/lifecycle` | `ResourceLifecycleSet` → `ResourceLifecycleData` | `ManagementResourceLifecycle.set_state`; human + kind's write scope |
| `POST /management/resources/{K}/{R}/lifecycle/history/query` | `PageQuery` → `PageData` / `ResourceLifecycleEvent[]` | `lifecycle_history`; kind's read scope |
| `POST /management/export-bindings/query` | `BindingQuery` → `PageData` / `ExportBindingView[]` | `ManagementBindingReads.query`; human gate |
| `GET /management/export-bindings/{id}?template_revision_id=UUID` | `BindingDetailQuery` → `ExportBindingView` | `detail`; wrong template parent gives 404 |
| `GET /management/providers` | No body → `ProviderSettingsData` | No decryption, outbound requests or hidden connection test from a metadata read. |
| `GET /management/providers/revisions/{id}` | No body → `ProviderRevisionMetadata` | `revision`; exact authorized config ID, including historical configurations |
| `POST /management/providers/history/query` | `PageQuery` → `PageData` / `ProviderRevisionMetadata[]` | `history`; no per-row usage aggregation |
| `POST /management/memories/query` | `MemoryQuery` → `PageData` / `MemoryView[]` | `ManagementMemoryReads.query`; library scope, original ACL/safety/source redaction |
| `GET /management/memories/{id}?revision=N` | `ResourceDetailQuery` → `MemoryDetailData` | Exact revision, current revision number and server action hints |
| `POST /management/memories/{id}/history/query` | `PageQuery` → `PageData` / `MemoryRevisionView[]` | Bounded descending history; deleted history requires human management authority |
| `POST /management/confidential-fields/query` | `ConfidentialQuery` → `PageData` / `ConfidentialFieldView[]` | `ManagementConfidential.fields`; metadata only |
| `POST /management/confidential-values/query` | `ConfidentialQuery` → `PageData` / `ConfidentialValueView[]` | `values`; authorized library or task scope |
| `POST /management/confidential-fields/{id}/values/history/query` | `ConfidentialHistoryQuery` → `PageData` / `ConfidentialValueView[]` | `history`; never decrypt |
| `POST /management/confidential-fields/{id}/values` | `ConfidentialValueRevisionSet` → `ConfidentialValueView` | `set_value`; exact field revision/current value UUID (required null means absent), existing human/task gates |

The runtime schema modules are the contract definitions; do not maintain a second
set of schema classes in the plan. They reuse `Contract(extra=forbid)`, `Cost`, `Result`, resource revision
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
| `POST /providers`, `POST /providers/test` | `ProviderConfigSet`; `ProviderTest`, safe job/cost receipt; never echo newly supplied key |
| `POST /memories`, `GET/PUT /memories/{id}`, `GET /memories/{id}/history`, `POST /memories/{id}/decisions`, `POST /memories/{id}/disable` | `MemoryCreate/Update/Decision/Disable`, `MemoryData` or existing paged revision history; reuse exact-revision gate |
| `GET /tasks/{T}/memory-feedback`, `POST /tasks/{T}/memory-candidates`, `GET /jobs/{J}` | Existing paged feedback, `MemoryCandidateJobRequest`, authorized job recovery |
| `POST /confidential-fields`, `POST /confidential-fields/{id}/revisions`, `POST /confidential-values/{id}/reveal` | `ConfidentialFieldCreate/Update`, explicit `ConfidentialReveal`; no new reveal API or broadened rights |

Existing unbounded library lists, task selections and legacy provider/binding/
confidential reads remain available to current API/CLI clients. New management
pages must not use them as a preload followed by browser-only slicing. A future
bounded task-selector contract is separate; this draft does not claim all existing
task pages now have bounded lists.


## Bounds and performance targets

These are acceptance targets, not measurements. Resource query searches normalized
prefix tokens in name; products also vendor/model; certificates also declared
number. Never search decrypted values, arbitrary URLs, file text or profile bodies.
Features accept product/status filters only; other kinds reject these fields.
Memory search uses sanitized current text/conflict key/tags and kind/stored status/
expiry, with explicit `include_deleted` human gate. Tag matching is all selected
tags. Confidential search covers key/label only; `task_id` establishes authorization,
not a cross-task search. Optional `field_id` restricts either browse to one exact
definition without weakening task authorization. `archived=true` includes archived definitions/values rather
than meaning archived-only; field definitions are installation-wide even with task context.

| Bound | Requirement |
| --- | --- |
| Input | `limit=25`, range 1–100; search 1–200 chars, nonblank after trim; at most 20 memory tags; cursor <=2048 chars; complete query body <=16 KiB; service rejects feature-only filters on other kinds |
| Ordering | Resources by immutable root `(created_at DESC,id DESC)`; resource/lifecycle/config history by `(revision DESC,id DESC)`; bindings by `(reviewed_at DESC,id DESC)`; memory by immutable root `(created_at DESC,id DESC)`; fields by `(key ASC,id ASC)`; value history by `(version DESC,id DESC)` |
| Current values | Keyset over field `(key,id)`, resolve only the page's current value for the authorized owner; one bounded join/lateral lookup, not an all-fields loop |
| Cursor | Authenticated/encrypted opaque cursor, 15-minute expiry, bound to actor kind, token/agent session, live authority fingerprint, resource/parent/task, normalized filters, order and keyset anchor. Foreign or invalid cursor fails without revealing its contents |
| Read consistency | Live pages with per-page `as_of`, not a frozen multi-page snapshot. SQL applies visibility/filter before LIMIT; concurrent changes can alter later matches. After a successful write or changed filters restart at page 1; never claim an exact total |
| Materialization | SQL `LIMIT limit+1` before projection; validate each projected row’s kind/root/revision against the authenticated request; no all-history load, no full `current_revisions/active_snapshot_ids`, no per-row queries. Target <=6 SQL round trips for a list, <=8 for a detail including authority |
| Payload | Complete Result page <=256 KiB, detail <=1 MiB. Reads fit the complete envelope and sign the retained last-row continuation before constructing `Page`; the Result adapter only validates final encoded bytes and never drops rows. Oversize details fail; a single oversize row fails `management_result_too_large`. No chopped JSON/text that could be mistaken for complete data |
| Concurrency | Maximum two in-flight reads per screen; debounce search 300 ms, cancel obsolete reads; no list polling. Job pages reuse terminal-aware foreground/backoff polling |
| Server | On the fixed CI fixture (10,000 roots and 100,000 revisions), serial warmed p95 <=500 ms list, <=750 ms detail/history; statement timeout 2 s, excluding file transfer/explicit provider jobs. Record query plans, rows visited, payload bytes and fixture/hardware parameters |
| Browser | First actionable list <=1.5 s on a fixed local built-app fixture, <=100 rendered rows, <=2 MiB accumulated page data, previous pages evicted. Detail loads content/files only when opened; maintain focus and keyboard navigation |

Indexes must demonstrate these bounds for the approved prefix search semantics;
unsupported arbitrary substring scans are not an alternative implementation.
Memory proposal/approval has separate existing whole-library safety/conflict reads;
provider legacy history has per-revision aggregation. This contract's read targets
do not certify those operations as bounded. Run scale acceptance for actual
write paths; an unresolved latency failure blocks that slice rather than removing
confidential-value safety checks. Memory list expiry must evaluate one server
`as_of` per request; browser clock does not decide effective status.


## Failure and recovery requirements

Authentication failure clears protected state. Missing or unavailable resources use
one unavailable view. Human-only denial must not offer token escalation. Revision,
lifecycle and confidential-value conflicts require a fresh read and deliberate save;
secret input and review acknowledgements must be cleared. Network loss during a write
requires reading the resulting revision or job before another write.

Lifecycle mutation and its audit share a transaction. Audit content is limited to
identifiers, revisions, hashes, fixed codes and timing; never record values, secret
suffixes, input text, signed links or raw provider responses. Result-size and query
timeout failures remain explicit errors rather than truncated data.

## Test plan and repeatable artifacts

Extend existing API/PostgreSQL and CLI acceptance scenarios for each owned domain.
Keep human-versus-token/agent denials, immutable revisions, exact parent bindings,
concurrent CAS conflicts, file integrity, bounded queries and secret non-disclosure.
Remove scenarios whose only subject is a removed tenant, role, member or catalog.

Run scale acceptance on the installation dataset and retain query plans, response
sizes and reproducible synthetic input parameters under `data/work/`. Browser
acceptance must exercise real entry points and identify mocked modes explicitly.
Secret scenarios disable screenshots, traces and video; receipts record booleans and
hashes instead of confidential input. Browser and database checks prove different
boundaries and neither replaces the other.

Rollback/recovery preserves immutable versions,
selections, lifecycle history and encrypted files; repairs move forward against the
authoritative baseline.
