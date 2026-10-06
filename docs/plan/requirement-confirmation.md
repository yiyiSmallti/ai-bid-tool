---
kind: plan
---

# B02 requirement confirmation and manual entry contract

Status: **approved; first slice implemented**. This contract covers the
B02 requirement-review and manual-entry scope in [the roadmap](roadmap.md), satisfying the interface-first requirement in
[agent.md](../../agent.md#workflow). Its importable
[Pydantic v2 models and service protocols](requirement-confirmation/requirement_confirmation_contracts.py)
re-export the implemented runtime payloads; importing them registers no handlers or database objects.
The [mechanism note](../notes/requirement-confirmation.md) names the implementation.
Database and browser acceptance remain separate execution checks; implementing the
acceptance suites does not assert that those suites have run.

## Goal and boundary

A bid specialist (投标专员) must be able to inspect an extracted requirement against
the tender documents (招标文件), accept that exact requirement as a working basis,
and see who must act next. Human confirmation (人工确认) of a requirement accepts its
text, category, starred flag, structured condition and verified source together. It
does not confirm a response (响应), evidence (证据), or the completeness of extraction.
An org (organization/tenant; 单位) remains the isolation boundary, and a task (任务)
remains the collaboration boundary.

Recommended policy: allow provisional preparation, but gate acceptance and consumption
of completed work. A response card (响应卡) may be prepared before requirement review;
it cannot be confirmed or classified as comply-only (须遵守) until its requirement
is currently confirmed. Table assembly (组表) retains unconfirmed requirements as
gaps (缺口). Checking can diagnose these gaps; scoring criteria (评分标准) must be
confirmed before a rubric can be accepted or used for scoring. The
[consumer matrix](#consumer-gates-and-freshness) defines each boundary precisely.

The first slice includes manual recovery of rejected items and requirements the model
missed. Every saved manual requirement has a verified `Source`. There is no uncited
requirement kind, arbitrary source URL, uploaded replacement text, or override of an
invalid citation. Unverified notes may remain ordinary task discussion; this contract
does not convert discussion into requirements or model inputs. This avoids an easy
path from an informal note to an apparently sourced bid (标书) statement.

The slice excludes cross-extraction merging, inferred equivalence, automatic confirmation,
new OCR, typed parameter algebra (B03), extraction-completeness certification, requirement
deletion/exclusion, general requirement-content editing, and response co-sign changes.
A disputed saved requirement can remain unconfirmed, with a review reason; it cannot be
silently removed from coverage. General correction/supersession of saved semantics needs
a later contract. Existing quote repair remains available with its actual authority.

## Integration points and design differences

These integration requirements identify the existing mechanisms that B02 extends. Historical baseline paragraphs in approved plans do not
override the runtime modules listed here.

| Existing mechanism | B02 integration |
| --- | --- |
| `Source`, `ExtractedRequirement`, `Result`, `Cost`, `CONTRACT_VERSION` in [schemas/contracts.py](../../server/app/schemas/contracts.py); `Requirement` in [models/entities.py](../../server/app/models/entities.py) | Source requires exactly one PDF page or Word `Location`; separate `requirement_reviews` records hold review state/revision/confirmer. Result is **4.0**, including the task-budget cost fields. No duplicate Source or Result definition is needed. |
| `source_text`, `locate_span`, `locate_spans`, `split_cited`, `merge_starred`, `fingerprint` in [services/extraction.py](../../server/app/services/extraction.py) | Verification binds a parsed page/block. Normalized matching resolves a unique original span, with a boundary preference; saved quote is literal original text. Fingerprint deduplicates normalized quote and position within a job, not a full reviewed meaning or explicit occurrence. |
| `attach` in [providers/llm.py](../../server/app/providers/llm.py), `Processor.__call__` in [jobs/processor.py](../../server/app/jobs/processor.py) | Rejected records contain `position`, at most 200 quote characters and `reason`, without complete Source/category/text/condition. All rejected and no kept items fails `invalid_citation` before the starred-rule union. Mixed results succeed with warnings. There is no `jobs/extract*.py` module in this checkout: extraction execution lives in Processor. |
| `submit` in [services/tender_jobs.py](../../server/app/services/tender_jobs.py), `latest_extractions`, `list_requirements`, `extraction_history` in [services/requirements.py](../../server/app/services/requirements.py) | Fixed model/reasoning inputs reuse a cached job. Successful different jobs keep separate requirements; default lists select the latest successful job per document. Original `created` counts describe model publication, not subsequent manual additions. |
| `repair_citations` in [services/citation_repair.py](../../server/app/services/citation_repair.py) | Existing repair previews/updates quote and model_quote for saved requirements only. It cannot re-enter rejected items or accept a new position. Its human admin check also requires `evidence:confirm`; B02 must not silently widen this endpoint. |
| `citation_valid_in_chunk`, `citation_validity_batch`, `extraction_scope` in [services/response_cards.py](../../server/app/services/response_cards.py) | Current consumers recheck literal presence and unique normalized location. `extraction_scope` requires a successful extract job with saved requirements; it is also used by historical reads and must not become a global confirmation gate. |
| [api/tenders.py](../../server/app/api/tenders.py), `req extract/list/history/repair-citations` in [cli/bid_cli/main.py](../../cli/bid_cli/main.py), [schema.py](../../cli/bid_cli/schema.py) | These entry points retain their meanings; [requirement confirmation routes](../../server/app/api/requirement_confirmation.py) add review and manual creation. `partial_completion_exit` does not return 5 merely because a successful extraction reports rejected items. |
| `access`, `live_actor` in [task_workflow.py](../../server/app/services/task_workflow.py), [team workflow](team-workflow.md#membership-ownership-and-authorization) | Live org/task grants and archive guards already exist. The older code-basis statements about absent task membership/SSE in that plan are superseded by these functions, [documents.list_tasks](../../server/app/services/documents.py) and [api/task_board.py](../../server/app/api/task_board.py). Co-sign remains a separate response policy. |
| `row_projection`, `matches`, `board`, `progress`, `activity` in [task_board.py](../../server/app/services/task_board.py), `requirements_with_collaboration`, `load` in [task_board_projection.py](../../server/app/services/task_board_projection.py) | The default projection retains response enums with requirement gaps; [the opt-in overlay](../../server/app/services/requirement_board.py) adds requirement buckets and a live next actor. Citation repair remains admin-only. |

[The design's typical flow](../design.md#users-and-core-scenarios) includes “confirm
requirements”; its human-gate prose largely describes evidence confirmation. These are
different decisions. The runtime keeps independent decisions for both. Likewise, the design's
rerun wording does not mean equivalent requirements inherit human decisions: extraction
jobs are separate scopes under [ADR 0004](../adr/0004-extractions-per-reasoning-level.md).
Old budget examples in [org-console.md](org-console.md) and [check.md](check.md) predate
the enforced task budget and Result 4.0; this contract reuses the current schemas.

Manual verification uses `extraction.locate_source_citation_span`, integrated from the
merged PR #18 helper, with the requested Source quote as both the pinned quote and
candidate. Effective-state batches retain `locate_spans` and reuse unchanged, verified
pins without repeated per-item location calls. Both preserve the literal equality guard.
The input accepts no caller offsets: a longer exact quote must resolve ambiguity within
one page/block. `assessment_bounds.text_window` is a display helper, never the verifier.

## First vertical slice

One active task, one document and one explicit extraction scope must support this path:

1. Open extracted requirements with a bounded source view, independent review state,
   current set revision, and an eligible next human. Keep original model and rejected
   counts visible without calling them completeness measures.
2. Inspect a rejected summary or identify an omission, select an existing verified
   PDF page/Word block, supply complete content and verbatim quote, preview verification,
   and save an **unconfirmed** manual requirement. The all-rejected case uses the
   explicitly manual scope below, preserving the failed extraction receipt.
3. A human owner/contributor confirms an exact review revision and hash. Another user
   sees the board update through existing durable task events. The same person may
   enter and confirm a requirement; there is no invented two-person gate.
4. Prepare/review a response using existing domain authority; assemble confirmed rows
   and explicit gaps. Exercise check, rubric/score and export freshness so no consumer
   can use old approvals after reopen or source mutation.
5. Reopen a requirement or repair its quote; the board points to requirement review,
   old artifacts remain readable as stale, and new acceptance requires current review.

Database predicates, service authorization, CLI, console and consumer gates belong in
this same slice. Do not ship a confirm button before its consumption boundaries.

## Review identity, state and source verification

The stable identity is `(org_id, task_id, extraction_job_id, requirement_id)`. Add a
review revision independent of response-card and assignment revisions. Effective states
are `unconfirmed`, `legacy_unconfirmed`, `confirmed`, `invalidated`; only `confirmed`
is consumable. Missing review metadata fails closed as `legacy_unconfirmed`, including
during migration, and never means approval.

| Operation | State and concurrency contract |
| --- | --- |
| Model publication / manual add | Start unconfirmed with server-generated origin; literal source verification does not confirm meaning. Seed legacy rows as legacy_unconfirmed, without invented actors/times. |
| `confirm` | Human reviews content and source; expected revision and review hash must match, current citation must pass. Append immutable decision, set current confirmer/time, increment review and set revisions. |
| `reopen` | Human supplies a reason; append decision, clear current approval, retain its historical actor/time. Confirmed becomes unconfirmed. It does not reopen or overwrite a response card or Evidence. |
| Source/content/policy changes | Invalidate a previous confirmation; unconfirmed/legacy rows remain unconfirmed with a new review revision/hash. Retain immutable prior decision. Rechecking hashes is mandatory even before invalidation events are materialized. |
| Reconfirmation | Verify the current full input again. Old confirmation never becomes effective merely because bytes are changed back. |

Confirming an already confirmed revision or reopening an unconfirmed/legacy/invalidated
revision with a new request ID returns 409 `invalid_review_transition`; it does not append
a meaningless decision. Identical request replay remains successful. Invalidated inputs
can be confirmed only after refreshing and verifying their current review hash/source.

`review_hash` is SHA-256 over canonical JSON with a version: org/task/job/requirement IDs,
text/category/starred/condition and verified source binding. Source binding pins document
content SHA-256, chunk ID and complete parsed chunk content hash (text, blocks, sequence,
page, citation_verified), complete Source location, exact quote, selected location-text
hash, start/end and verifier-policy version. Full chunk hashing is conservative: unrelated
changes in that chunk also invalidate review. Origin/model_quote are retained provenance;
a model_quote-only repair is audited but does not change reviewed semantics.

Verification resolves the document/chunk under the authenticated task/org, requires a
parsed citation-verified chunk, checks whole Word Location equality or exact PDF page,
and runs the shared unique-span locator. The supplied manual quote must equal the
literal source slice at the returned span, not only normalize to it. Never trim or
rewrite the quote silently. Offsets in `VerifiedRequirementSource` are server-produced
Python character indices `[start,end)`, local to that page/block. Hashes and offsets are
derived facts; a caller cannot assert them to bypass verification.

`ambiguous_quote` requires a longer exact quote within the **same** page/block. Wrong
location requires selecting the correct existing page/block and a new preview. Adjacent
blocks cannot be concatenated; unverified OCR text is not a valid source. If the original
has no uniquely locatable literal span, save nothing and keep the recovery action open.
Do not accept a free-text fallback. Existing normalization/boundary/Unicode behavior and
the database's `response_citation_valid` rules must remain equivalent.

No general saved-content update route is introduced. Existing `repair_citations` must
update the review baseline and invalidate affected confirmation in its transaction when
its quote changes. A different source document/location or changed text/category/condition
from any future authorized writer or maintenance path has the same invalidation contract.
Parser retention in `Processor` normally preserves old chunks; a new parse or newer
uploaded document does not itself repoint existing requirements.

## Manual entry, rejected receipts and extraction lineage

`ManualRequirementInput` reuses `Category` and `Source`; it supplies text, starred,
condition and a nonblank reason. Text/quote are bounded to 20,000 characters each, the
request including condition to 256 KiB; oversize input fails, never truncates. Condition
remains descriptive JSON under B03, not executable code or an approved scoring formula.
Manual text may summarize the quote; verification establishes source location, while
human review judges whether that summary and classification are faithful.

Preview is read-only, zero cost and returns a canonical preview hash plus verified
source pin. Creation rechecks source bytes, authority, archival and set revision under
locks; a changed preview is 409. Every mutation requires a caller-generated UUID
`request_id`. `(org, authenticated user, request_id)` binds the complete request hash
including route/action. An identical authorized retry returns the original operation's
request/event IDs with `replayed=true`, but renders **current** requirement/set views;
a historical confirmation receipt must not display a subsequently reopened item as
currently confirmed. No decision is reapplied. Different input is 409
`idempotency_conflict`. Authorization is rechecked
before replay, so old receipts cannot bypass removal or archival. Request receipt storage
is described with the new tables below.

Two explicit target choices avoid hidden merging:

- With `extraction_job_id`, add to that successful model/manual extraction scope after
  confirming it belongs to the same task/document. Additive manual membership is explicit
  in preview. Increment set revision and invalidate membership-dependent drafts/checks/
  rubric coverage/cache manifests. Other requirements' confirmations remain valid. Do not
  rewrite the model job's `created`, rejected list, original cost, terminal status or cache
  key. History exposes original model counts and separate current/manual counts.
- Without it, create an explicitly **manual-origin** extraction scope in the same
  transaction as its first verified requirement. Persist a real terminal `Job(kind=extract,
  status=succeeded)` against the actual parsed document, with `origin=manual` in the review
  set/public receipt, model/reasoning/provider/run/lease/queue fields null and attempts=0.
  Success means manual verification and persistence completed; it never reclassifies a
  failed model call as success. No queue, Provider, VendorCall or UsageRecord is created.
  This supports all-rejected or never-extracted documents without a fabricated document,
  relaxing `extraction_scope`, or modifying the failed source job.

Manual-scope cache keys use a separate `manual-extract-v1` namespace with org/task/document
and request ID. `tender_jobs.submit`, queue recovery and `Processor` must never schedule,
claim, retry or model-cache-match manual scopes. They are immutable terminal receipts;
later adds are review-set membership changes, not model retries. `jobs.cancel` retains
the terminal-job restriction. New extraction calls still use the normal Provider path.

Manual scopes are explicitly selectable in history/board/CLI; they do **not** silently
replace the default latest model extraction or merge with it. `latest_extractions` and
`response_cards.scope_warnings` must share this origin rule and deterministic ordering
`finished_at DESC NULLS LAST, created_at DESC, id DESC`. If only manual scopes exist,
default `req list` returns no implicit set and warns to select one with `--job`; the console
opens the new scope from the creation receipt and labels it “人工补录集”. A single manually
entered item never implies full-document coverage.

Rejected recovery uses `(source job ID, zero-based result.rejected index, SHA-256 of that
exact summary)` as a receipt reference. Reads require task:read and job:read including
`jobs.read_access`'s agent-owned job rules; the referenced job must have the same task and
document as the target. The summary is a hint, never prevalidated content; the human
supplies the missing fields and full source. `manual_missing` has no rejected reference.
`manual_rejected` retains one; one rejection may lead to more than one legitimate requirement.

Review/history projections redact the rejected reference to null if the reader cannot
read its source job. The shared manual requirement itself remains readable under task
access; a provenance link never grants access to another person's private agent job.

Do not retrofit full candidates from the truncated summaries or remove original rejected
counts when recovered. The new rejected view reports bounded associated saved IDs and a
link to filter the full review list if there are more than 100. An unavailable/changed
legacy summary returns `rejected_reference_changed`; entering independently as a missed
requirement remains possible with explicit new input, not an automatic fallback.
Exact existing `extraction.fingerprint` collision returns `duplicate_requirement` and
the authorized existing ID, never silently changes its text/category/condition or creates
a second approval. Different clauses may need a longer quote; splitting one quote into
multiple semantically different requirements is deferred.

Re-extraction under different model/reasoning/version creates a distinct set with all
new requirements unconfirmed. Same-job cache hits retain review metadata and manual adds
but do not recalculate the original publication counts. Confirmations, assignments and
response history stay on their original IDs; even identical fingerprints across jobs
do not transfer approval. Merely discovering a newer set warns about history, not source
invalidation. Explicitly selecting it changes the working scope. Multi-job union remains B08.

## Data model and migration outline

All four new business tables have **NOT NULL org_id**, `ENABLE ROW LEVEL SECURITY` and
`FORCE ROW LEVEL SECURITY`. USING and WITH CHECK bind `app.current_org`; missing context
denies access. Every entity has `UNIQUE(org_id,id)`; parent references use org composite
keys, never bare UUIDs. No new global table, privileged cross-org function or file store
is introduced. `Requirement`, `Source`, `Job`, Membership and task-member definitions
remain in their runtime homes.

| New table | Required fields, keys and invariants |
| --- | --- |
| `requirement_review_sets` | id, org_id, task_id, extraction_job_id, document_id, origin=model/manual, revision>=1, membership_sha256, confirmation_sha256. Unique `(org_id,extraction_job_id)` and `(org_id,id,task_id,extraction_job_id)`; job FK `(org_id,extraction_job_id,task_id,document_id)` to the existing Job scope key. Origin immutable; model origin seeded by publication/backfill, manual origin by the human-entry service. |
| `requirement_reviews` | id, org_id, task_id, extraction_job_id, requirement_id, origin, revision, current_event_id, state, current review_hash/source pin, encrypted baseline snapshot, confirmed_by_user_id/at, optional rejected_job_id/index/summary hash. Unique `(org_id,requirement_id)` and `(org_id,id,task_id,extraction_job_id,requirement_id)`. FK `(org_id,requirement_id,task_id,extraction_job_id)` to Requirement; FK to same review set; source job FK plus same-task/document trigger; confirmer references `(org_id,user_id)` Membership. Confirmed requires both confirmer/time and a current verified binding; all other states have neither. |
| `requirement_review_events` | id, org_id, task_id, extraction_job_id, requirement_id, review_id, revision, action, state_after, canonical content/source snapshot hash, encrypted snapshot/reason, verified pin, authenticated actor kind/user, occurred_at, request_id. Append-only, unique `(org_id,review_id,revision)` and `(org_id,review_id,id,revision)`; composite FK to the matching review/requirement scope. Reviews' current pointer uses deferred FK `(org_id,id,current_event_id,revision)` to that full event key. Seed/invalidate events may have no human actor; confirm/reopen/manual_add require a live human session. |
| `requirement_review_requests` | id, org_id, task_id, actor_user_id, request_id, action, request_sha256, encrypted result receipt, created_at. Unique `(org_id,actor_user_id,request_id)` across actions, composite task and Membership FKs; append-only. Stores atomic replay receipts for single/batch/manual operations, not just the last request on a review row. No raw request body, token or source text in indexes/audit. |

Add supporting unique keys to existing parents only where a referenced full org/task/
document tuple is not already unique. Stored pin Source/chunk/document references and
rejected references must be checked by database triggers against actual parents; JSON
fields cannot replace same-org foreign-key enforcement. Actor user references Membership,
not the global User table alone. Encrypted snapshots/receipts use `Secrets.for_data` with
org/review binding for content/reasons and org/receipt binding for replay receipts.
Source-trigger invalidation events copy the encrypted prior baseline and hash to identify
the input being invalidated; current views derive the changed source independently.
A later human decision stores the new encrypted baseline. Public history returns authorized source snapshots but not raw reasons
or encrypted data. Audit/event streams contain metadata only.

Migration order:

1. Add parent keys, four tables, RLS, indexes and deferred event pointers without runtime
   grants. Backfill before enabling human/archive mutation triggers in the same migration
   transaction; otherwise `task_archived_guard` rejects seed INSERTs for archived tasks.
2. Backfill all saved Requirement rows, per succeeded extraction scope, as
   `legacy_unconfirmed`; retain invalid legacy sources visibly without manufacturing a
   verified pin or confirmation. Seed events identify migration, not a fictional reviewer.
   New model publication creates review metadata atomically, including on-conflict
   recovery without resetting existing human state.
3. Install immutable-history/human-state triggers and explicitly register
   `task_archived_guard` for the new tables. The historical loop in
   [0041_team_workflow.py](../../server/migrations/versions/0041_team_workflow.py) does not
   cover future tables automatically. Reuse `task_write_authority` with `p_human=true`,
   `p_decision=false` for human writes, not domain-review authority. Install
   source/content invalidation producers and current-confirmation DB predicates;
   exclude tokens from new human-only scopes in the ApiToken CHECK. Add task-event
   producers, response/draft/scoring DB gates and consumer manifest versions before
   exposing writes. Minimal runtime grants come last.
4. In the same release, enable review reads/writes and revised consumers. Existing
   confirmations of responses/evidence stay historical facts, but old drafts/reports/
   released downloads cannot bypass missing B02 bindings: mark legacy manifests stale,
   offer owner bulk confirmation and explicit reassembly/reassessment. No blanket
   migration approval based on a previously confirmed card or successful export.

Use task/workflow lock → review set → requirements/chunks/reviews in UUID order → dependent
card locks → event sequence lock consistently, including quote repair. Source writers
must acquire the same task lock before chunk changes; readers must never hash outside
the transaction and publish against different bytes. The current predicate recomputes
bindings under locks, not only cached state columns. Archive/member revocation and a
simultaneous confirm/add must serialize; losers return an explicit conflict/denial.
Seed publication is limited to the authorized extract worker's current execution fence;
deterministic invalidation is limited to trusted source-change producers. Neither path
can set confirmed state or fabricate a human actor. Effective invalidation detected on a
read does not authorize a write into an archived task; it still fails closed immediately.
Rollback preserves review history and receipts; rolling back to a runtime that ignores
these gates is not a safe downgrade. Stop affected writes and restore a gate-aware release.

## Permissions and human ownership

Use `task_workflow.access`, `live_actor`, `Identity.require` and the caller's org transaction.
Require actual task membership for writes, including org admins; admin management recovery
can repair membership under the existing contract but cannot impersonate confirmation.
`req:confirm` and `req:manual` are **new human-only** capabilities for org admin/bidder/
technical roles intersected with active task owner/contributor. They are excluded from
`SCOPES`, included in `HUMAN_ONLY_SCOPES` and the token database CHECK, stripped from
agent/worker delegation, and never granted to platform identities. Leave them out of
`task_workflow.DECISIONS`: that set deliberately permits domain reviewers, while this
contract requires contributor/owner rights. Keep `evidence:confirm` and `export` forbidden
to tokens as before.

| Action | Authority |
| --- | --- |
| Review/list/show/history/source read | `task:read`, current task read access; rejected receipts additionally `job:read` and job-origin access. Existing original-file/page gates still apply. Authorized tokens may read. |
| Manual preview/add | Human session, `req:manual`, owner/contributor, active task; same-document rejected reference access when supplied. No token/agent/manual-add delegation in this slice. |
| Confirm/reopen one | Human session, `req:confirm`, owner/contributor, active task. Assignment suggests who acts; it neither grants rights nor prevents another authorized contributor from acting. |
| Confirm explicit batch | Same human scope plus **task owner**; max 100 explicit IDs/revisions/hashes, reviewed_each=true and reason. Atomic all-or-nothing. No “all unseen rows” or automatic browser write loops. |
| Source repair | Keep the existing admin human `req:extract`/`evidence:confirm` predicate of `repair_citations`; invalidate B02 binding when needed. A technical contributor may request repair but cannot execute that existing handler. |
| Response/evidence decisions, co-sign, rubric decisions/export | Existing domain, role, task and human-only gates plus B02 acceptance predicates; requirement confirmation grants none of those powers. |

No review domain (职责) or co-sign rule is added to requirement confirmation, even for
a starred (★) mandatory clause (★条款). The action checks what the tender requires;
specialist authority for the proposed response stays with the existing domain reviewer.
Requiring two domain decisions at both stages would duplicate work and leave task owners
unable to resolve extraction errors. A starred badge still calls attention to the source;
it never weakens later response co-sign policy. Archived tasks retain reads/history and
reject all add/confirm/reopen/repair writes until authorized restoration.

## Consumer gates and freshness

Do not filter unconfirmed requirements out of counts or lists. Each consumer pins the
review policy version, complete requirement membership and applicable review hashes.
`RequirementConsumptionManifest` describes accepted/gap/preparation-only entries; a
manifest over 2,000 items fails explicitly rather than silently dropping requirements.
Preparation caches bind content/source/membership; a confirmation-only change need not
discard a paid candidate. Acceptance/assembly/check/score/export additionally bind current
decision revision/hash. New input-version namespaces distinguish old caches.

| Consumer and actual integration points | Before confirmation; after reopen/invalidation |
| --- | --- |
| `response_cards.create_card/update_card`, `card_generation.snapshot/submit_generation/generate/publish` | Allow preparation and submission for review with visible requirement state. Never manufacture approval. Recheck content/source and live task grants before model admission/publication. Confirmation-only changes leave candidate content usable; a source/content change rejects stale publication. Existing protected-card rules still apply. |
| `response_cards.card_action`, `dispose_cards`, `card_eligibility` | Gate `confirm` and `comply_only` on current requirement confirmation. Reject/needs-material/withdraw/reopen and returning disposition to respond remain recovery operations. No requirement gate may trap an old pending/confirmed card. After a source change, existing response quote/reconfirmation gates apply independently; reapproving the requirement alone does not approve the changed response. |
| `drafts.assemble/current_draft_inputs/complete_draft/draft_view` | Always partition every saved requirement. Unconfirmed/legacy → gap `requirement_unconfirmed`; invalidated → gap `requirement_invalidated`, with invalid_citation when appropriate. No candidate response/Evidence enters these rows. Confirmed/comply-only cards whose requirement is reopened also become gaps. Historical drafts stay readable with explicit stale reasons; reassembly creates a new draft. |
| `check_inputs.snapshot`, `check.validity`, check worker publication | Allow current partial drafts and deterministic gap diagnostics. Reports expose requirement-review coverage and say conclusions on unconfirmed tender interpretations are provisional. Combined checks still send response text only for accepted rows; gap-only metadata/source cannot become an accepted response or a passing finding. Changed review/membership inputs mark old reports stale and require a new preview/run. |
| `score_inputs.snapshot`, `score_generation.submit_rubric` | Candidate rubric generation/revision/classification remain preparation, with visible provisional scoring sources. No requirement approval is inferred from rubric edits. Content/membership changes invalidate fixed rubric inputs; mere confirmation can unlock review without repeating unchanged model work. |
| `score.decide_rubric`, `score_run_inputs.snapshot`, `score_execution.submit_score/publish/run_view` | Whole-set rubric confirmation and every score admission/publication require **all fixed scoring requirements**, including duplicate/ignored coverage entries, currently confirmed. Only response rows/comply-only whose requirements are confirmed are accepted. Other requirements can remain gaps; the whole task need not be complete to estimate a partial draft. Reopen invalidates score consumption/read validity without deleting historical rubric decisions. |
| `exports.build_manifest/fresh_manifest/release/download_gate` | Current B02-bound partial drafts may produce review copies (审阅件) under existing rules. Final sections (正式件) retain gap blockers. Old released artifacts fail freshness after requirement changes, including legacy manifests; human-only export and evidence/prototype gates remain independent. |

Add a separate current-confirmation predicate at service and DB layers. Do not redefine
“valid citation” to mean “human confirmed”: unconfirmed sourced requirements remain valid
inputs for preparation. In particular, extend the final `response_item_gate` definition
from [0018_generation_dependencies.py](../../server/migrations/versions/0018_generation_dependencies.py):
its eligible-card-cannot-be-hidden-as-gap rule and exact gap-reason array must include B02,
not only Python enums. Extend response confirmation/disposition triggers, rubric final
confirmation, `score_inputs_current`, `score_report_gate` and `score_publication_complete`
from [0039_fast_citation_locate.py](../../server/migrations/versions/0039_fast_citation_locate.py).
Preserve later `rubric_shape_complete` changes in
[0044_score_rubric_candidates.py](../../server/migrations/versions/0044_score_rubric_candidates.py).
Preparation-time `rubric_current_inputs` cannot globally require confirmation, since it
also validates unconfirmed rubric preparation. Extend `GapReason`, `CardEligibility`,
board enums and client renderers in one compatible rollout.

For asynchronous work, preview is not authority. Recheck live membership/archive, pinned
content and the consumer-specific review manifest at submission, each paid admission
and publication. Changes after dispatch still settle sent calls through `JobExecution`;
they cannot publish stale accepted output or erase actual cost. Historical reads return
validity metadata, not blanket 404 because an input is now stale. Actual authorization
loss still follows current 404/403 boundaries.

## Dashboard, progress and console outline

Extend the dashboard (看板) with one explicit `requirement_review` bucket, alongside the
existing six response buckets. Unconfirmed, legacy and invalidated requirements go there
before response-state bucketing; response state stays a separate visible field. A source
failure adds `invalid_citation` and repair guidance within that bucket. Once confirmed,
the existing response projection chooses the bucket. Counts partition saved requirements
once; rejected summaries are a separate diagnostic queue, not saved requirements or
denominator entries. Confirmed requirement count is not completed-response count and
never claims that the extractor found everything.

Expose the extended board/progress projection through `/v4/tasks/{T}/board` and
`/v4/tasks/{T}/progress` with opt-in `view=requirement-review`; CLI equivalents are
`bid task board/progress --task T --job J --view requirement-review --json`. Both require
the explicit `extraction_job_id=J` query in this view. The new console uses this view.
Default v4 and v3 board projections retain their closed enums: pending B02 work
projects as existing bucket `gap`, blocker `unconfirmed` (or `invalid_citation`), action
`view`, plus a warning to open requirement review in the updated console/CLI. They must
still use the new eligibility predicate, never call a reopened requirement complete.
The opt-in board returns `RequirementBoardData` / `RequirementBoardItem[]`; its nested
`board` and `response` preserve legacy-safe metadata, while `buckets` and `requirement`
are the canonical extended projection. Its response subcounts cover only confirmed
requirements, and their total plus requirement_review equals the scope total. Progress
returns `RequirementProgressData` / existing `BoardJobView[]`, retaining existing job
pagination alongside the selected set's review summary. Requirement next_actor is null
after confirmation; outstanding response next actions remain in the response projection.
The opt-in view is unavailable on v3, matching other v4-only console projections.

`RequirementActorHint` gives one accountable next actor, not a truncated list pretending
to enumerate all authority. Choose an active eligible assignee, otherwise the eligible
task owner. If neither is currently eligible, return owner-recovery with the named owner
and a membership/ownership action; if the owner is unavailable, show human org-admin
recovery without inventing an admin ID. Compute eligibility from live Membership, task
role and scope. Other authorized contributors can act; the chosen actor is responsibility
guidance, not an ACL. A viewer/token sees that person and action but no executable button.

`row_projection` must prefer archive recovery first, then source repair, then
`confirm_requirement`, then response work. Budget blockers cannot replace a zero-cost
requirement action. Existing `repair_citation` actions must use the real admin predicate;
an ordinary contributor instead sees “请任务负责人协调管理员修复引用”. Update `matches(mine)`
and assignment filters for the named next actor, and `progress` for independent confirmed/
unconfirmed/legacy/invalidated/manual/rejected counts. A response that was previously
complete cannot continue counting complete while its requirement is unconfirmed.

The console adds `/app/org/tasks/:taskId/requirements?job=J`, linked from the existing
task/extraction history and review board. The implemented console follows this outline:

- Header: selected document/scope, origin/model/reasoning, original extraction receipt,
  current saved/manual/review counts, “已确认不代表无遗漏”, and named next owner/action.
- List: server pages of 50 (max 100), state/starred/category/manual/rejected-link filters;
  show independent requirement and response state. No eager loading of all tender pages.
- Detail: pinned original page or full Word block location, exact highlighted quote,
  extracted text/category/condition, provenance and immutable decision history. “确认要求”
  is separate from “确认响应”; confirm posts the displayed revision/hash and reason.
- Rejected panel: original truncated summary explicitly labelled incomplete, plus
  “从原文补录”; omissions use “补录遗漏要求”. Both open the same bounded source picker and
  content form. Preview shows the exact verified span and target set or “新建人工补录集”.
  Save never auto-confirms and never fills absent source fields from a guess.
- Owner batch review: explicit selected items and full inspectable sources, max 100,
  no default select-all, reviewed_each and reason required. Conflict clears stale approval
  controls, retains unsaved text and asks for a new read; never auto-resubmits decisions.

Use existing authenticated originals/page previews and short-lived download rules; never
put tokens into URLs. Navigation/refetch/filtering makes no paid calls. Archived state,
404 authorization changes, empty results, failed manual preview, stale cursors and offline
recovery have explicit read-only/error views. Tokens cannot turn the console into a human
session. The existing response-review page links back to the outstanding requirement.

## HTTP, CLI JSON and service interfaces

All routes are relative to the existing versioned API base, with session/org context
resolved by `api.main.context`; no mutation accepts actor, org_id or confirmation time.
Resource existence/access is established before revealing scoped errors. Multi-ID bodies
are same-task/same-scope or fail atomically. Request payloads and envelopes use strict
shared `Contract` (`extra=forbid`). The runtime exports `RequirementCitationVerifier`
(deterministic local provider) and `RequirementReviewService` Protocols; no new vendor
Provider method or dependency is needed. No LLM/OCR/billing work happens in these methods.

| HTTP | CLI, all with `--json` | Request → Result.data / Result.items |
| --- | --- | --- |
| `GET /tasks/{T}/extractions/{J}/requirement-reviews` | `bid req review-list --task T --job J [--state STATE] [--cursor C] [--limit N]` | ReviewPageQuery → ReviewPageData / RequirementReviewView[] |
| `GET /requirements/{R}/review` | `bid req show --id R` | → RequirementReviewData / [] |
| `GET /requirements/{R}/review-history` | `bid req review-history --id R [--cursor C] [--limit N]` | PageQuery → PageData / RequirementReviewEvent[] |
| `GET /tasks/{T}/extractions/{J}/rejected-items` | `bid req rejected --task T --job J [--cursor C] [--limit N]` | PageQuery → PageData / RejectedItemView[] |
| `POST /tasks/{T}/requirements/manual-preview` | `bid req add --task T --input FILE --dry-run` | ManualRequirementInput → ManualEntryPreview / [] |
| `POST /tasks/{T}/requirements/manual` | `bid req add --task T --input FILE` | ManualEntryCreate → ManualEntryData / [] |
| `POST /requirements/{R}/review-decisions` | `bid req confirm --id R --input FILE`; `bid req reopen --id R --input FILE` | RequirementDecision (action must match CLI) → RequirementReviewData / [] |
| `POST /tasks/{T}/extractions/{J}/requirement-confirmations` | `bid req confirm-batch --task T --job J --input FILE` | RequirementConfirmBatch → ConfirmationBatchData / ConfirmationReceiptItem[] |

GET/manual-preview/reads return HTTP 200; a new manual entry returns 201 and an idempotent
replay 200; decisions return 200. Existing `req list/history/extract/repair-citations`
retain their names; append origin/review summaries and navigation IDs to existing views
without repurposing `req history` (extraction history). `bid schema` registers the approved implementation and keeps a separate legacy projection. Remote and local CLI call the same services and
PostgreSQL/RLS, without interactive questions. `--input FILE` avoids putting quoted
business text and reasons into shell history. No asynchronous wait/retry flag is needed
for these new synchronous commands.

Batch output is bounded receipt metadata, one item per selected requirement, not 100
copies of full quoted content. It identifies the committed confirmation event/revision
and current review revision/state; the console reloads detailed rows. `changed` describes
the original operation even on replay, while current state may since have changed.

Cursors bind org, actor access fingerprint, task/job or requirement, query, set revision
and stable sort keys. Review-list additionally supports `--category`, `--starred`,
`--origin`, and paired `--rejected-job`/`--rejected-index`, matching ReviewPageQuery.
Other reads accept only PageQuery; unsupported filters fail 422. Reads are bounded to
100 items and 512 KiB per response, with cursor continuation
before an additional full item exceeds the byte cap. One oversize legacy record returns
`legacy_item_too_large`, never truncates a quote. Review-list order follows source order
in `list_requirements` with requirement UUID as tie breaker; history uses revision, rejected
receipts use index. Changed set or ACL cursors return a reload error, not mixed snapshots.

Result keeps exactly `ok, command, data, items, warnings, cost, duration_ms`. Its cost is
the shared 4.0 `Cost`: llm_tokens/ocr_pages/usd/basis/charge/billing_currency/task_amount/
unpriced_calls/unresolved_calls. New local deterministic operations use `basis=zero`,
all amounts/counts zero, and configured billing currency; Decimal amounts serialize as
strings. Original failed extraction cost stays on its original job. Contract negotiation
uses `/v4` and `X-Bid-Contract-Version`, not an added JSON version key. Existing `/v3`
projection remains as defined by the runtime; new routes/enum members target v4 only,
and must not leak into legacy/default board responses. The opt-in projection above is
an additive interface; both its schema and the legacy projection must be registered and
snapshot-tested. Existing strict clients never receive a new enum without opting in.

Example successful rejected-item read with no reported entries; identifiers and timing
are illustrative, not an execution record:

```json
{
  "ok": true,
  "command": "req rejected",
  "data": {
    "task_id": "00000000-0000-0000-0000-000000000001",
    "extraction_job_id": "00000000-0000-0000-0000-000000000002",
    "next_cursor": null,
    "total": 0
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
  "duration_ms": 3
}
```

| Exit | HTTP and Result behavior |
| --- | --- |
| 0 | Successful read/preview/add/decision/replay. A visible unconfirmed item or rejected receipt is not failure of the read. `--dry-run` success does not authorize later creation. |
| 2 | 400/422 malformed input, missing parameters, nonverbatim/ambiguous/wrong-location quote, unsupported filter/size; 409 expected revision/hash/cursor/duplicate/idempotency/archive or requirement-acceptance conflict. `ok=false`, `data.error.{code,message,exit_code}`, `items=[]`; reload/correct before retry. |
| 3 | Retryable transport, database availability/serialization exhaustion or service timeout. Never claim failure proves no commit: retry the same mutation request_id and input to recover its receipt. |
| 4 | 401 unauthenticated, 403 missing scope/human/task-role authority on a visible object, uniform 404 nonexistent/cross-org/inaccessible parent, or detected stored-source integrity failure. No other-org IDs/quotes in errors. |
| 5 | Reserved for existing commands' partial-success contract; new batches are atomic and never partially confirm/save. B02 does not change the existing mixed-extraction rejection exit behavior. Any later change to that behavior needs explicit CLI compatibility approval. |

Error `data` keeps the runtime nested `error` object with `code`, `message` and `exit_code`, with authorized conflict
IDs/revisions only when safe. Shared new codes include `requirement_unconfirmed`,
`requirement_invalidated`, `review_changed`, `manual_preview_changed`,
`rejected_reference_changed`, `duplicate_requirement`, `idempotency_conflict`,
`invalid_review_transition`, `nonverbatim_quote`, `ambiguous_quote`, `quote_not_at_position`, `unverified_location`,
`legacy_item_too_large`. Cross-org manual source or rejection refs return 404 before
hash/verification details. Existing task_archived and budget errors retain their contracts.

## Audit and durable task events

Each committed manual add, confirm, reopen, quote repair/invalidation and batch records
actor kind/user, org/task/job/requirement, before/after review revision/hash, request/correlation
ID and reason hash through `versioned.audit`. Proposed actions are
`requirement.manual_added`, `requirement.confirmed`, `requirement.reopened`,
`requirement.invalidated`; keep existing `requirement.repair_citation`. Batch decisions
have one event/audit per item plus one bounded summary. Previews write nothing; failed
transactions create no successful-decision event; identical replay creates no duplicates.
No source quote, requirement text, raw reason, encrypted receipt, token or cost secret
enters audit or task-event payloads.

Use the existing metadata-only `board_changed` event with bounded requirement IDs and
extraction scope plus the existing `invalidate_all` flag, produced in the same transaction via
`task_events.append`/the statement producers in
[task_event_sql.py](../../server/app/services/task_event_sql.py). Preserve its existing
field allowlist and 1,800-byte bound: large batches use invalidate_all with empty ID lists.
The action detail stays in review history/audit, not a new SSE field. For human review events,
`source_id=None`: `task_events.replay` treats a non-null source_id as a **Job ID**, so a
review-event ID would incorrectly hide the event. Register producers for all new tables
and source invalidations; update the board watermark, not task access_epoch for an
ordinary review. Existing permission/archival transitions still change access_epoch.

Extend `task_board.activity` with a requirement-action allowlist for the opt-in board's
bounded `requirement_activity: RequirementActivityView[]`; legacy/default activity retains
its existing action enum. Validate real requirement parent linkage, never an arbitrary
audit.details.task_id. SSE/poll replay rechecks task access
and never exposes other-org/membership-revoked events. Lost connectivity reloads the
server snapshot; the browser cannot reconstruct approval from locally cached clicks.

## Failure modes and implementation acceptance plan

The acceptance suites use the existing two-org PostgreSQL/API/CLI/browser harness
and fake Providers. Database-backed tests are in
[test_requirement_confirmation.py](../../server/tests/test_requirement_confirmation.py),
[test_requirement_source_acceptance.py](../../server/tests/test_requirement_source_acceptance.py)
and [test_requirement_consumption.py](../../server/tests/test_requirement_consumption.py).
The [mocked browser flow](../../web/e2e/requirement-confirmation.spec.js) writes reproducible
artifacts under `data/work/requirement-confirmation/`. The following matrix defines the
required evidence; test definitions and discovery do not establish executed acceptance.

| Failure family | Required acceptance evidence |
| --- | --- |
| Isolation for each new table | Org A/B fixtures for review_sets, reviews, events and requests independently: SELECT/INSERT/UPDATE/DELETE, missing org context, cross-org parent/current-event/confirmer/source/rejected links, immutable history, no bypass role. RLS FORCE and org composite keys inspected with runtime role. Same-org nonmember and removed/inactive member also denied. |
| Isolation for each route | Parameterized A→B reads and writes for **every** HTTP route above, plus manual-preview, batch mixed-org IDs, all pagination cursors, existing req reads/repair and board/progress/events. Uniform 404 for inaccessible objects; errors, timing-independent predicates, counts and duplicate/replay responses cannot leak B. |
| Human gates and authority | Owner/contributor across eligible org roles succeed; reviewer/observer/viewer, nonmember admin, token/agent/worker/platform fail writes. Token issuance and direct DB insertion reject req:confirm/req:manual, evidence:confirm and export. Revoke role/membership/archive between preview and execute; atomically reject. Confirmed metadata cannot be inserted by constructing Pydantic views or raw SQL. |
| Citations and recovery | Real parsed synthetic PDF/Word fixtures: full Location mismatch, wrong task/document/chunk/page, unverified OCR, blank/oversize, quote absent, repeated literal/normalized ambiguity, Unicode expansion and boundary preference; normalize-only manual input rejected. Manual missing and truncated rejected recovery, stale summary hash, same-source different-job refs, all-rejected failed job preserved with cost, new manual-only scope, duplicate fingerprint. SQL/Python verifier parity. |
| State, concurrency and replay | Confirm exact revision, reopen, repair/changed source, changed text/category/starred/condition, verifier-version invalidation, change-away-and-back, manual-add versus draft publication, archive/revocation races. Stale batch rejects all; repeated request returns one receipt, cross-action input conflict, response lost after commit, no double audit/event. Source repair cannot silently inherit approval. |
| Consumer gates | Prepare card/rubric while unconfirmed; block response confirm, comply-only and whole-rubric confirmation; draft creates exhaustive gaps without candidate text; check diagnoses gaps; score rejects any unconfirmed fixed scoring requirement, including ignored/duplicate coverage. Accept remaining partial-draft gaps. Test service **and direct SQL** response/score gates, exact gap reasons, queued/admitted job invalidation, preserved call charges and no stale publication. |
| Migration and history | Existing extraction/card/evidence remains readable; all legacy requirements start unconfirmed; owner explicit bounded batch works without fabricated approval. Old draft/report/download manifests stale; regenerate after review. Cache hit preserves same-job decisions/manual adds; new extraction copies none; new manual scope does not replace latest model/default selection. Model counts/cost and rejected receipts never rewritten. |
| Board and events | Exactly one bucket per saved requirement, no rejected-count denominator, no completed response when its requirement is pending, eligible assignee→owner→recovery routing, actual admin-only quote repair, mine filter, archive and zero-cost action priority over budget blockers. Transaction rollback yields no SSE/audit; source_id semantics, cursor watermark and reconnect reload hold. |
| CLI contracts | Snapshot all new commands with `--json`, shared Result 4.0 full zero Cost, manual create/replay receipts, input-file validation and exits 0/2/3/4. Verify existing partial-extract exit unchanged, atomic batches never 5, discovery schema matches models, v3 projection cannot bypass gates or receive unrecognized v4 enums. |
| Browser end-to-end | Human owner A reviews, contributor A adds an omitted/rejected item from a real source picker, confirms, domain reviewer accepts response, draft/check/score gates follow the matrix; viewer/token and org B cannot mutate/read A. Cover all-rejected manual scope, 409 with retained edits, stale source/reopen, owner batch, archived recovery, next-actor links and SSE reconnect. |

Extend the behavior covered by `test_citation_repair.py`, `test_adversarial_citations.py`,
`test_citation_batch_equivalence.py`, `test_docx_extraction.py`, `test_reasoning_levels.py`,
`test_cli_snapshots.py` and the existing workflow/assessment acceptance suites without
weakening their gates. Produce reproducible Playwright trace, sanitized fixture/job/review
IDs, CLI snapshots and a command/outcome manifest under ignored
`data/work/requirement-confirmation/`, never under `docs/`. Screenshots/traces use synthetic
content and redact session headers. Tests demonstrate the actual human/role entry points,
not merely model validation. Static checks, DB-free suites, console build and browser test discovery can run in
a restricted sandbox; PostgreSQL and browser execution must establish runtime acceptance.

## Decisions

The owner approved every recommended default; the models implement these decisions.

| Decision | Approved default | Reason |
| --- | --- | --- |
| Who confirms requirements? | Human task owner/contributor with org admin/bidder/technical and req:confirm; no domain or co-sign; same-person entry/confirm allowed | Accepting the extracted basis is coordination work. Professional response and evidence approval keep their separate domain gates. |
| Hard stop before any preparation? | Allow labelled preparation; gate accepted response/comply-only, accepted draft rows, rubric set confirmation and score inputs | Preserves useful work while keeping an unambiguous next-human action and preventing provisional text becoming accepted output. |
| Uncited notes as requirements? | Do not add a free-text requirement kind | A parallel unsourced kind adds accidental consumption and coverage ambiguity; discussion already holds informal notes. |
| Ambiguous quotation override? | No offsets/override in the initial input; request a longer unique literal quote in its page/block | Reuses verifier guarantees without weakening SQL parity or silently selecting the first occurrence. |
| Manual target / all-rejected path? | Explicitly append to a chosen successful set, or create a clearly manual-origin extract receipt on the real document | Existing successful-scope/FK consumers remain coherent; failed model history and costs remain truthful. Membership-changing appends explicitly invalidate affected downstream manifests. |
| Latest/default manual visibility? | Manual scopes require explicit selection and never silently replace latest model extraction | A one-item recovery set must not hide the full extracted requirement list. |
| Re-extraction inheritance? | No automatic approval/assignment transfer; identical cache hit retains the same scope | Same words are insufficient proof of identical source and human intent across runs. |
| Migration and bulk confirmation? | Legacy-unconfirmed; owner-only explicit max-100 atomic batches | No fabricated historical human act; bounded bulk review makes migration manageable. |
| Source invalidation granularity? | Pin full chunk/location/content/review hashes and verifier policy; invalidate on any mismatch, including change-back | Conservative invalidation is understandable and protects immutable human decisions. |
| Board presentation? | Add requirement_review bucket, preserve separate response state and named assignee/owner/recovery | Staff can see who must act next without interpreting a second response-review badge. |
| Saved false positives/corrections? | Remain unconfirmed; no deletion/exclusion/general-edit API in this slice | Keeps the first contract bounded and prevents omission from coverage. This limitation must be explicit in the console; a later supersession contract can resolve it. |
| Mixed extraction exit 5? | Preserve current exit behavior; rejected reporting and recovery are explicit reads | Avoid unrelated CLI behavior changes under a confirmation contract. |
| Provider/dependency changes? | Local deterministic verifier protocol only, zero vendor usage | Existing source bytes and verifier are sufficient; no paid model judgment is needed to establish a literal location. |
