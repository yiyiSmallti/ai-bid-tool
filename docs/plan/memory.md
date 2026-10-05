---
kind: plan
---

# Layered memory storage, retrieval, and automatic candidates

Status: **Approved; the first org-layer slice is implemented, with PostgreSQL integration acceptance pending execution by the main session.** Corresponds to [roadmap](roadmap.md) M01, M02, M03, and also involves P03/C02.

The [runtime contract](../../server/app/schemas/memory_contracts.py) defines Pydantic models, Provider interfaces, and CLI JSON structures. All recommended defaults are approved; see [decisions](#decisions). Implementation entry points are in [memory mechanisms](../notes/memory.md#code). Later scopes remain subject to this page's enablement conditions.

## Goals and boundaries

The complete goal follows the [design document's memory system](../design.md#memory-system): four memory layers, explicit CRUD, human candidate approval, disablement/expiry, retrieval constrained by org (organization/tenant; 单位) and scope, usage records in results, and candidates/evaluation samples from human feedback. Memory affects working methods only. It can never serve as parameter evidence (证据), replace real materials, or satisfy evidence-confirmation/export gates.

The first complete flow is **org layer** candidate creation → human admin approval → PostgreSQL exact/keyword/tag retrieval → model response-card (响应卡) drafting → per-actual-call usage manifests → new candidates from human rejection/editing. This flow needs no Embedding; CRUD, retrieval, and feedback-to-candidate conversion make no model calls. Existing card drafting still uses billed LLM Provider calls. Human card confirmation also creates org-local evaluation samples, never automatically effective memories.

The four layers and later enablement conditions follow. The first slice rejects disabled scopes rather than silently changing them to `org` or using empty results to imply implemented layers. The full M01–M03 scope remains.

| Layer / wire value | Content, owner, and read boundary | Write/confirmation | First-slice boundary |
| --- | --- | --- | --- |
| Global / `global` | General bidding knowledge, read-only to all orgs, never org-derived | Platform maintenance only; sources/reviewers in [decisions](#decisions) | Disabled; no global table, special org, or null `org_id` bypass |
| Org / `org` | Org practices, experience, rules/default preferences; readable by org members and authorized agents | Writable members/tokens propose; human org admin approves | Complete flow |
| User / `user` | Personal preferences owned by `(org_id, user_id)`; only the owner and owner-initiated agents read | Owner manages; activation still requires owner session confirmation | Define models first; later enable private-result/job read isolation, never writing private preferences to shared card snapshots |
| Project / `project` | Task decisions, selections, clarifications, assignments; owned by `(org_id, task_id)` | Task members propose, human task members confirm; read-only after archival | Define models first; depends on F06 task membership/archival, never equating org membership with task membership |

Excluded initially: global operations, Vue memory-management pages, semantic retrieval/reranking, LLM summaries, cross-org training sets, historical-card bulk backfill, risk-card false-positive feedback (B09 has a separate approved checking contract; memory consumption remains outside this slice), and memory consumption by req extract/check/score/agent. Later consumers must follow the same retrieval/usage-record protocol rather than reading tables directly into prompts. This slice neither advances independent task membership/archival features nor claims a complete four-layer product.

## Interfaces

Below are first-slice and later vector entry points. HTTP `org_id` comes from verified org request context; retrieval bodies also explicitly carry the same `org_id`, with mismatch returning 404. Other write bodies cannot override authentication by supplying org, creator, confirmer, status, or source kind. List targets use `scope/user_id/task_id` query parameters.

| Entry point | Contract and returned `data` / `items` |
| --- | --- |
| Data | [memory_contracts.py](../../server/app/schemas/memory_contracts.py) reuses `Contract`, `Result`, `Cost`, `ProviderUsage`, `JobAction`, `ReviewDomain`, without duplicate definitions |
| `POST /memories` | `MemoryCreate` → `MemoryData` / `[]`; always candidate |
| `GET /memories` | `MemoryListRequest` → `MemoryPageData` / `MemoryView[]`; scope, pagination/filtering never alter authorization |
| `GET /memories/{id}` | → `MemoryData` / `[]`; expired/disabled objects readable, logically deleted objects hidden by default |
| `PUT /memories/{id}` | `MemoryUpdate` → `MemoryData` / `[]`; complete content/expiry replacement as new candidate revision |
| `GET /memories/{id}/history` | → `MemoryPageData` / `MemoryRevisionView[]`; explicit authorized history retains deleted-object traceability |
| `POST /memories/{id}/decisions` | `MemoryDecision` → `MemoryData` / `[]`; approve/reject by logged-in humans satisfying layer roles |
| `POST /memories/{id}/disable` | `MemoryDisable` → `MemoryData` / `[]` |
| `DELETE /memories/{id}` | `MemoryDelete` JSON body → `MemoryData` / `[]`; logical deletion, no physical history erasure |
| `POST /memories/retrieve` | `MemoryRetrievalRequest` → `MemoryRetrievalData` / `MemoryHit[]`; `?preview=true` is read-only, without persisted retrieval records |
| `GET /memory-retrievals/{id}` | → `MemoryRetrievalData` / `MemoryHit[]`; recheck all input read permissions; explicitly mark stale records, never reuse as current retrieval |
| `GET /jobs/{id}/memory` | → `MemoryCallData` / `[]`; memory refs for actually admitted job calls only, never encrypted prompts |
| `POST /tasks/{task_id}/memory-candidates` | `MemoryCandidateJobRequest` → `MemoryJobSubmissionData` / `[]`; replay existing server feedback events only, no arbitrary feedback bodies |
| `GET /tasks/{task_id}/memory-feedback` | → `MemoryPageData` / `MemoryFeedbackView[]`; locate events for recovery |
| `GET /tasks/{task_id}/memory-evaluations` | → `MemoryPageData` / `MemoryEvalSampleView[]`; org-local sample metadata only |
| `GET /memory-evaluations/{id}` | → `MemoryEvalDetailData` / `[]`; authorized admins inspect sanitized summaries/sources before review, never raw sensitive diffs |
| `POST /memory-evaluations/{id}/review` | `MemoryEvalReview` → `MemoryEvalData` / `[]`; accepting/excluding samples does not approve memory/evidence |
| Jobs | Reuse `GET /jobs/{id}`, `POST /jobs/{id}/cancel`, CLI status/wait/cancel, adding job-kind permission checks |
| Later vector step | `POST /memories/index` takes `MemoryIndexRequest`, returning a job; unregistered before enablement, never calls unconfigured Providers |

Pagination defaults to 50, maximum 100, ordered stably by `(created_at,id)`. Cursors bind org, user, and filters; cross-org/scope cursor reuse is rejected. Retrieval allows at most 50 items, defaults to 12 and 8,000 characters, and includes complete entries rather than truncating rules mid-text. Pydantic checks structure; services/DB must check ownership, membership, sensitive content, server time, and concurrency. Model validation is not authorization.

## Current code basis and differences

| Basis | Integration point or explicit difference |
| --- | --- |
| `Identity`, `authenticate`, `membership`, `SCOPES`, `ROLE_SCOPES` in [auth.py](../../server/app/services/auth.py), and `context` in [api/main.py](../../server/app/api/main.py) | Authentication/roles/scopes actually live in services, not a hypothetical core scope module. Reuse session/token/Membership intersection; memory role boundaries are in [permissions](#permissions-roles-and-provider-billing) |
| `Database.transaction` in [core/db.py](../../server/app/core/db.py); [isolation notes](../notes/tenant-isolation.md) | Transaction-level `app.current_org`, `set_actor_context`; runtime roles cannot own tables or have BYPASSRLS |
| `Task`, `Job`, `UsageRecord`, `VendorCall` in [entities.py](../../server/app/models/entities.py) | Task has no membership/archival. Job task/document nulls are provider_test-only exceptions; no fake files for taskless Embedding |
| `update_card`, `card_action`, `append_revision` in [response_cards.py](../../server/app/services/response_cards.py) | Human edit/reject and immutable revisions share a transaction; `CardUpdate` has no reason, so edits cannot be described as existing learning instructions |
| [ADR 0005](../adr/0005-human-confirmed-responses.md), [response-cards.md](../notes/response-cards.md) | Model proposals/human confirmation (人工确认)/card cache exist, explicitly excluding automatic memory; this contract adds dependencies/candidates without changing domain confirmation |
| `snapshot`, `submit_generation`, `generate`, `publish`, `check_input_access` in [card_generation.py](../../server/app/services/card_generation.py); `request_body`, `groups`, `draft` in [drafting.py](../../server/app/providers/drafting.py) | Snapshot/wire input use separate memory rules/preferences and upgraded prompt/schema versions; dual-text citation verification remains |
| `assemble`, `current_draft_inputs`, `show_draft` in [drafts.py](../../server/app/services/drafts.py) | `draft` is deterministic verbatim table assembly (组表), without LLM calls; upstream card generation consumes memory, assembly inherits usage traces/invalidation notices only |
| `LLMProvider` in [providers/base.py](../../server/app/providers/base.py); `resolve_configured` in [configured.py](../../server/app/providers/configured.py); [provider_contracts.py](../../server/app/schemas/provider_contracts.py) | Existing configuration capability is `llm_extract`, with no callable Embedding resolver; `server/app/memory/` remains the business read/write/selection package |
| [ADR 0001](../adr/0001-platform-console-access.md), [agent.md](../../agent.md#hard-rules-must-never-be-violated) | Global-table exceptions cover approved platform tables, not memory. The global layer requires separate explicit approval, never reuse of cross-org policies |

## Data models and migration outline

The first slice uses these org business tables, **each with `org_id NOT NULL`, `ENABLE ROW LEVEL SECURITY`, and `FORCE ROW LEVEL SECURITY`**. USING/WITH CHECK both constrain current org; no org context means all access denied. Entity tables have `(org_id,id)` unique constraints; epochs use composite primary keys. FKs cannot reference bare UUIDs. Tables are in [memory.py](../../server/app/models/memory.py), constraints/grants in the [memory migration](../../server/migrations/versions/0037_memory.py).

| New table / public view | Fields and constraints |
| --- | --- |
| `memories` / `MemoryView` | id, scope, user_id/task_id, current_revision_id, revision, deleted_at, source_feedback_event_id/generator_version. Non-null event/generator combinations unique per org; initial DB scope CHECK permits only org, later migrations enable three tenant scopes. Deferred current-revision composite FK `(org_id,id,current_revision_id,revision)` |
| `memory_revisions` / `MemoryRevisionView` | memory_id, revision, content(kind/conflict_key/text/tags), status, source, content_sha256, created_by/actor_kind, confirmed_by/at, expires_at, decision/reason hash; `UNIQUE(org_id,memory_id,revision)`, append-only |
| `memory_scope_epochs` / `MemoryEpochView` | `(org_id,scope,owner_id)` primary key, increasing epoch; org owner_id=org_id, future user/project binds Membership/Task; invalidates caches including zero-hit results |
| `memory_feedback_events` / `MemoryFeedbackView` | task/card, before/after revisions, actor, kind, sanitization policy version/summary; unique `(org_id,card_id,after_revision_id,kind)`; encrypted bounded sanitized feedback, atomic outbox, immutable events |
| `memory_eval_samples` / `MemoryEvalSampleView` | event/task/card/revision refs, label, policy version, summary, encrypted sanitized sample, review_state/revision/by/at; unique `(org_id,feedback_event_id,generator_version)`; immutable sample text, optimistic locks/audits for authorized human review-column updates |
| `memory_retrievals` / `MemoryRetrievalView` | actor/user/token/task, query/manifest hash, scope/epoch manifest, policy version, valid_until, created_at; encrypted query/sent-text snapshots, append-only; no preview record |
| `memory_retrieval_items` / `MemoryRetrievalItemView` | retrieval_id, memory/revision, content/sent hash, order, inclusion/exclusion reasons; `UNIQUE(org_id,retrieval_id,memory_revision_id)`, no plain text, append-only |
| `memory_call_inputs` / `MemoryCallInputView` | task/job/run/call/retrieval, actual requirement IDs/sent memory revisions, prompt/hash/version, usage ID, call state; unique `(org_id,job_id,run_id,call_id)`; write admitted before submission, settlement adds only completed/unknown and usage ID; input immutable |

All memory text passes sensitive-value rejection before storage. Searchable first-slice rules/tags use PG text/text[]; encrypted fields are not falsely claimed to support direct SQL keyword matching. Only nonsecret rules/preferences are allowed. Pending feedback/query/sent-text snapshots use `Secrets.for_data` in [core/security.py](../../server/app/core/security.py), with encrypted org/record-bound envelopes and read-time binding checks. Logs/audits/public call manifests retain IDs/hashes/counts only. Memory cannot store prices, identity numbers, bank accounts, keys, or verbatim evidence, and disabling drafting redaction cannot remove that restriction. First-slice recognition reuses [redaction.py](../../server/app/services/redaction.py) rules and confidential values in [confidential.py](../../server/app/services/confidential.py); matches reject original-text writes. Automatic feedback is sanitized first, without copying Evidence, certificate pages, material quotes, screenshots, or entire cards as memory. Detection remains bounded; candidates require human review and cannot claim all sensitive expressions are detected.

Composite FKs and additional gates:

- User/creator/approver reference `(org_id,user_id) → memberships`, never using global users to share personal memory. Tasks reference `(org_id,task_id) → tasks`; tokens `(org_id,token_id) → api_tokens`.
- Memory revisions reference `(org_id,memory_id) → memories`. Source cards bind both `(org_id,card_id,task_id)` and `(org_id,card_id,revision_id)` to existing card/revision unique keys. Feedback before/after revisions are adjacent on the same card; events/samples/candidates share org/task. Servers generate sources; client source.origin/system or feedback IDs cannot impersonate human events.
- Retrieval items bind `(org_id,memory_id,revision_id)` and parent retrieval. Call inputs bind `(org_id,job_id,run_id,call_id) → vendor_calls` and `(org_id,retrieval_id)`; usage refs include matching org/job/run/call unique keys. Triggers verify every memory/requirement array ref: only selected retrieval memories and actually sent requirements are allowed, never fabricated used manifests.
- Unique constraints deduplicate candidate/event/sample sources. Worker candidate writes verify current running job/run/lease. Human approve/disable/delete triggers verify session, valid User/Membership/Org, and layer roles. Token-table CHECK forbids `memory:approve`, `memory:manage`, `memory:eval:review`, retaining `evidence:confirm`/`export` exclusions; hidden buttons alone are not gates.
- Internal worker identities bind both submitter and restricted task. Future user/project policies add owner/task-member protection beyond org RLS; history/job/retrieval/sample endpoints use identical read checks. `app.actor_kind=worker` grants no arbitrary private-memory access.

Approval targets an exact content revision. create always makes candidate; update appends candidate and withdraws old active content, never inheriting confirmation after editing active text. approve is candidate→active with person/time; reject candidate→disabled, disable active/candidate→disabled, delete disabled revision plus tombstone. Editing disabled content yields candidate only, never direct enable. Logical deletion cannot be revived; create separately instead. All changes check expected_revision, mismatch=409. Source/ownership/layer cannot update/move/promote. Normally only one effective item exists per layer/kind/conflict_key; approval checks under scope epoch lock, conflicts=409, never automatically disabling another rule. Expiry uses server UTC immediately, with effective_status=expired, without timer rewrites of historical status. expires_at must be future at creation/approval. Future project archival blocks all writes, including candidate approval/disablement/job writes; stored memory remains member-only read access.

Migration dependencies: tables/composite keys → RLS/minimal grants/immutable and human-state triggers → token CHECK/job-kind dispatch/usage association → runtime-role grants last. Initially only org scope, no historical-card backfill/evidence-confirmation changes; an explicit new version with an empty memory manifest distinguishes old caches. Rollbacks retain history/audits without destructive downgrade. Implementation changes add two-org/missing-context tests for every table.

### pgvector and later indexing

The proposed separate `memory_embeddings` table avoids in-place backfill of append-only memory revisions. It also requires `org_id NOT NULL`/FORCE RLS, with id, memory_revision_id, scope/user_id/task_id, `embedding vector NULL`, provider/model/version/model_revision/price_revision/dimensions/metric, provider_config_id, text_sha256, job/run/usage ID, created_at. Public `MemoryEmbeddingView` excludes vectors. Use `(org_id,memory_revision_id) → memory_revisions` and scope/owner consistency gates; ProviderConfig/Usage/Job references use org composite FKs. Uniqueness binds org, memory revision, model configuration revision, and model version.

The first migration may retain this **empty table/nullable vector column**, without Provider calls, vector indexes, or zero-vector placeholders. Non-null vectors require complete metadata, finite nonzero values, and `vector_dims(embedding)=dimensions`; only completed, settled matching jobs publish. Nulls never participate in distances. Actual dimensions, indexable dimension limits, model, and price are confirmed through P03 before matching indexes/partitions. Different model versions/dimensions never mix in ranking. Invalid old vectors remain for traceability; new models write new records, never overwrite old revision vectors. Extension installation is determined by implementation migration preflight, never assumed.

Later enable task-bound `memory_index`/`memory_query` jobs before taskless org bulk indexing. The latter requires explicit Job/Usage task/document constraint extensions and capability in [provider configuration](../notes/provider-config.md); never reuse provider_test or fabricate Document. All new jobs retain leases/billing.

## Retrieval and precedence

`MemoryRetrievalProvider.retrieve` requires org_id/nonempty scopes and binds authenticated Identity/org transaction, never actors constructed from request bodies. Services verify Identity first; DB queries explicitly constrain `org_id`, scope, owner, current revision, active, undeleted, unexpired before scoring/sorting/limit. Unauthorized/missing IDs uniformly return 404; disabled layers return `memory_scope_unavailable`. User filters allow only the authenticated owner; agents derive the owner from initiating identity, never arbitrary `user_id`. Task filters require membership permissions, not merely same-org task:read.

Initial `keyword-v1` adds no full-text tokenizer: text/query use NFKC, casefold, whitespace normalization. Exact full-text equality scores 100; deduplicated query/explicit keywords score 10 each for substring hits, maximum 20 terms; exact tag intersection scores 5 each, maximum 20. Zero scores are excluded. Unspaced Chinese queries are whole terms; callers may provide keywords/tags; this makes no semantic-recall claim. SQL uses bound parameters/escaped LIKE wildcards, searching within sets indexed by `(org_id,scope,status)`/ownership first; tags may use GIN.

Layer scan order is project, user, org, global; final ranking never mixes all four into one score:

- Fact/rule `rule`: project > org > global; lower-priority same-conflict_key results record `shadowed`. User layers store no rules and cannot override org compliance rules.
- `preference`: user > org defaults; project/global do not supply personal preferences. Rule blocks precede preference blocks, with prompts explicitly constraining preferences by rules; preferences cannot change evidence gates/business facts.
- Resolve explicit conflict keys first, then sort stably by kind, priority, relevance descending, memory UUID ascending. Exclude all same-level legacy conflicts with `same_priority_conflict`, never choosing facts by “latest.” Natural-language contradictions across different keys cannot be deterministically identified; warn for review, never claim complete semantic conflict resolution.
- top_k/character budgets retain whole items with public exclusion reasons and rule priority; no model compression on budget exhaustion. Index/query resource-limit overflow fails explicitly, never fetching cross-org top_k then filtering.

Vector queries also constrain org_id, scope, owner, active/expiry, and embedding identity **before** SQL distance ordering. RLS alone or post-query filtering cannot replace explicit double filtering. Query vectors/caches are org/scope isolated. Unconfigured `vector`/`hybrid` returns `embedding_unconfigured` without keyword fallback; explicitly requested keyword remains independently usable. Hybrid normalized scores/recall targets await evaluation; similarity never overrides rule precedence.

## Drafting consumption, usage audit, and C02 cache invalidation

After target requirement/material checks, `card_generation.snapshot` builds retrieval requests from current-batch requirements/restricted tags; initial scopes=[org]. `MemoryPromptContext` is a separate `memory_rules`/`memory_preferences` input to `LLMProvider.draft` and `providers/drafting.request_body`. `MemoryAwareLLMProvider.draft(..., memory=...)` defines the exact new optional keyword argument and reuses `DraftingOutput`; empty context leaves other consumers unchanged. Business read/write/selection lives in reserved `server/app/memory/`; vendor/Embedding adapters only in providers. Memory never enters material ref dictionaries or citeable EvidenceInput. `usable_as_evidence=false` is a protocol field; server unknown-ref rejection remains the final boundary.

`snapshot` expands both text-free public manifests and encrypted secrets, pinning scope/owner, retrieval/priority versions, scope epochs, query hash, ordered selected memory IDs/revisions/hashes, expiry, exclusion summaries, and context hash. Secrets save the sanitized text actually sent. Complete input size enters `estimate`, `groups`, and `HTTPExtractor.reservation`, with no under-reservation after adding memory. Dry-run retrieves/estimates identically but creates no records/audits/usage/external calls.

Retrieved does not mean actually used by the model. Every actual HTTP batch writes `memory_call_inputs` under its generated call_id in the same transaction as `JobExecution.admit`, linking vendor_calls, job/run, requirement IDs, retrieval/memory revisions, sent/prompt hashes. Empty memory still records an empty list. Retries/split batches get distinct call_id; one whole-job manifest is insufficient. Model claims cannot alter server records. Settlement atomically adds usage ID; cancellation/refusal/invalid output/unknown usage retain admitted records. These records mean “sent in context,” never proof of internal causal use. Admitted calls that crash before dispatch remain unknown, never falsely completed.

Existing `CardGenerationRun` manifests add retrieval/call refs. Each generated card traces to its successful batch, including precise partial-result mapping. job/status/card/history/draft expose ACL-authorized ID/hash lists only; detailed text uses memory reads. Future personal layers cannot leak preference text/IDs through shared jobs/cards.

| Change | Requirements for new calls, cache, and existing results |
| --- | --- |
| New active/disable/delete/update withdrawing active/approve | Lock/increment scope epoch in the same transaction, reevaluating all related retrievals, including zero-hit/not-top_k results, rather than invalidating only used IDs |
| Expiry | Retrieval valid_until is earliest expiry among accessible active candidates, including shadowed items; cache reads/every admission check current time, never relying on cleanup punctuality |
| Candidate changes without effective-content change | Effective sets for other calls stay intact; candidates never reach models. Withdrawing active through update uses the epoch behavior above |
| Retrieval/priority/sanitization/prompt/schema/adapter/embedding identity change | Enters input_hash/cache_key; mismatched old queued jobs fail and require new submission, never new parsers consuming old snapshots |
| Input invalidated after submission | `generate.before_admit`/`publish` recheck epochs, expiry, permissions, exact revisions; fail `memory_input_changed`, never secretly retrieving replacement text |
| Invalidation after dispatch or lost lease | Settle sent calls normally; old attempts cannot publish, traces remain |
| Memory changed for unconfirmed model drafts | Show `memory_input_stale`, block confirmation of old drafts; explicitly edit/regenerate before review |
| Memory changed after human card confirmation | Retain exact human decisions/traces with `memory_changed_after_review`, without automatic rewrites/revocation; humans may reopen. Existing evidence/citation/material invalidation gates remain independent |

Identical effective input/order/versions reuse original paid jobs; cache hits create no new vendor usage/calls and return original lineage. New epochs do not implicitly redraft protected confirmed/pending cards. `drafts.assemble` still copies confirmed content only; `show_draft/current_draft_inputs` aggregate notices/lineage without turning valid human confirmation into gaps. Unreviewed model-draft gates must extend both Python confirmation and DB confirmation predicates, not just cache. `generate` checks prompt/redaction/schema/memory-policy versions together; new parsers never silently consume old snapshots.

## Automatic candidates, samples, and jobs

Under [ADR 0005](../adr/0005-human-confirmed-responses.md)'s single-human domain confirmation, only successfully committed human `card_action(reject)` or `update_card` on model-derived content triggers candidate events; confirm creates samples only. Check previous.model_job_id, not just origin: human reject revisions have human origin but retain model_job_id; edits clear it. Capture before/after revision IDs before clearing. Ignore model generation/worker-written revisions to prevent feedback loops. Edits without substantive content changes create no candidates.

After task/card locks, expected_revision checks, and successful `append_revision`, the same transaction writes feedback events/unique evaluation samples; reject/edit also writes `memory_candidate` Job, confirm does not. Task/document bind the source card's real extraction job. Recovery batches accept same-task/same-file events only; cross-file input fails. Save initiating identity/scopes; workers revalidate current membership/memory/card/task permissions at start and before publication. Without permission, human feedback facts remain but no candidate publishes; workers never elevate privilege. Dispatch occurs after commit using `Queue.enqueue` in [queue.py](../../server/app/jobs/queue.py) and `Processor` in [processor.py](../../server/app/jobs/processor.py). Post-commit enqueue failure retains human decisions/outbox/job and returns explicit retry instructions/recovery IDs, never pretending successful decisions rolled back or automatically replaying them. Replay schedules only unprocessed events. Existing direct card-action return contracts remain; recovery IDs use bounded warnings encodings/audit associations.

First-slice `feedback-copy-v1` is a deterministic `MemoryCandidateProvider`:

- reject wraps sanitized human reasons as feedback with applicability pending review, linked to source card/task. edit saves bounded field-change summaries (response_text/deviation/deviation_note), explicitly human changes with generalizability unspecified. Exclude evidence fields, quote, secret values; never infer general rules from adjacent words.
- At most one org candidate per event, kind=rule, with default event-derived conflict_key and nonsensitive category tags. Admins may edit applicability/rule/conflict_key before approve. Automatic proposals always have origin=system, status=candidate, confirmed_by=null. Overlong text is not silently cut into out-of-context rules: mark skipped/no_reusable_feedback. Sensitive-only content becomes skipped/sensitive_only, with no original text saved.
- Unique constraints bind feedback/candidate sources/samples. Same event/generator replay returns duplicate. Later generator changes cannot silently duplicate effective memories; explicit reproposal retains provenance. Card rejection is separate from candidate rejection and approval.

`memory_candidate` retains queued/running/succeeded/failed/cancelled, lease heartbeats, new run_id per claim, and pre-publication ownership checks. Expensive sanitization/candidate computation occurs outside transactions; prepared successful events publish atomically under current attempts. duplicate/normal skip is not partial failure. Some successes with event failures produce completion=partial/exit 5; all failures produce failed. Cancellation/lost leases are never partial success. Committed sample events survive cancellation. Retry requires terminated/expired old attempts, never live-lease takeover. Cache keys include org/task/document, ordered event IDs/sanitized hashes, generator_version; duplicate batches reuse durable jobs. Successful partial jobs are not reset; failed-event subsets create new jobs, with candidate unique-key deduplication. New-kind status/cancel extends [services/jobs.py](../../server/app/services/jobs.py): reads require job:read + memory:read + card:read + task:read; cancellation also job:cancel and execution permission. Automatic dispatch/recovery share implementation, never a separate bare asyncio queue.

Sample labels record actual human confirm/reject/edit, not factual correctness. Keep both revisions, source, and sanitization hash, default unreviewed; admin accept/exclude review events store hashes/IDs only. Samples stay org-local, never automatically approving memory or entering public evaluations/vendor training. “Human-derived” describes candidate provenance, not human approval of generalized rules.

## Permissions, roles, and Provider billing

| New scope | Human roles/resource conditions | API token / agent |
| --- | --- | --- |
| `memory:read` | Four roles read org; user owner only, project task members only | Grantable/intersected with current role; user requires owner initiation; no automatic grants to old tokens |
| `memory:write` | admin/bidder/technical propose candidates; non-admin org members edit only self-created ineffective candidates, admin manages | Propose/edit only candidates created by this token and not effective; no approve/disable/delete |
| `memory:approve` | Org admin only; future user owner/project authorized non-viewer human task members | Forbidden, actor_kind=session; internal agents cannot borrow user scopes to confirm |
| `memory:manage` | Org admin disable/delete; future user owner/project human members; archived project rejects writes | Forbidden |
| `memory:retrieve` | Also requires read; drafting retains card/task/material permissions | Grantable, never beyond specified scopes |
| `memory:candidate:run` | admin/bidder/technical plus card:read/task:read/memory:read/write; accessible events only | Grantable, no fabricated human feedback |
| `memory:eval:read` / `memory:eval:review` | Org admin plus card:read/task:read, for sample read/review | Neither grantable to tokens initially; DB forbids review scope |
| Future `memory:index` | Org admin with enabled embedding configuration | Initially human admins only, subject to configuration-contract approval |

No new evidence:confirm/export authority; [tokens.create_token](../../server/app/services/tokens.py) and DB still deny both scopes to tokens. Platform sessions cannot become org sessions. Global administration must use separate authentication in [platform.py](../../server/app/api/platform.py), without org-memory/feedback access. Resource-level denial is uniform 404; invalid identity 401, missing feature scope 403, never revealing another person's memory existence through errors.

`MemoryRetrievalProvider` is a PG read/write boundary, not a vendor SDK. Initially `MemoryCandidateProvider` is local deterministic conversion with usages=[], never zero-cost UsageRecords. Future approved semantic candidates require providers adapters and `ProviderUsage`, never bypassing job admission.

`EmbeddingProvider.embed(EmbeddingRequest) → EmbeddingOutput` and `reservation → Decimal` are defined in the runtime contract. Inputs explicitly carry org/scope/job/run, model configuration revision, bounded text batches; output contains dimensions/model identity/vectors/per-call ProviderUsage. Services check result count/dimensions/nonzero finite values/text order; returned scopes cannot alter boundaries. Unconfigured production implementations fail explicitly, never random/zero vectors.

All external calls (existing drafting, later Embedding/semantic candidates) use `accounted_call` in [calls.py](../../server/app/providers/calls.py) and `JobExecution.activate/admit/complete/unknown` in [execution.py](../../server/app/jobs/execution.py). Reserve using actual capability prices/input bounds before requests. Embedding has no output-token limit, so the LLM reservation formula in [llm.py](../../server/app/providers/llm.py) cannot be reused unchanged. Call/parse failures, refusals, and cancellation remain immediately metered; unknown usage retains reservations, and retries cannot clear historical charges.

`UsageRecord` saves provider/model/version, duration_ms, tokens/input_tokens/output_tokens, usd, provider_config_id, platform_model_id, charge, job/run/call. Platform calls use `require_funds` preflight and `charge_usage` settlement in [billing.py](../../server/app/services/billing.py). Admission locks balance and subtracts all outstanding reservations; usage/ledger/call-state settlement is atomic/idempotent. Org-owned keys have charge=0 but retain usage/call ceilings. Result.cost.usd is vendor cost, never platform-currency charge. Details live in [prepaid billing](../notes/prepaid-billing.md). Vector queries require committed jobs/accounting contexts, never bare vendor calls in synchronous retrieve routes. Asynchronous CLI/API behavior awaits vector-enablement approval, preserving initial synchronous keyword semantics.

## CLI JSON and errors

All commands support `--json`, without interaction, using existing login/token org context. Remote APIs/local PostgreSQL share services/permissions, never file databases. Source/text/multi-field edits use `--input FILE`, avoiding large feedback/sensitive values in command history. Output reuses seven-key `Result`, with no top-level schema_version. Contract version uses `CONTRACT_VERSION` in [contracts.py](../../server/app/schemas/contracts.py) and `X-Bid-Contract-Version`; [CLI schema registry](../../cli/bid_cli/schema.py) registers only after implementation approval.

| CLI (all allow `--json`) | Input / `data` / `items` |
| --- | --- |
| `bid memory add --input FILE` | MemoryCreate / MemoryData / [] |
| `bid memory list --scope org [--status candidate] [--cursor C] [--limit N]` | MemoryListRequest / MemoryPageData / MemoryView[] |
| `bid memory show --id UUID` | ID / MemoryData / [] |
| `bid memory update --id UUID --input FILE` | MemoryUpdate / MemoryData / [] |
| `bid memory history --id UUID [--cursor C] [--limit N]` | ID / MemoryPageData / MemoryRevisionView[] |
| `bid memory approve` or `reject --id UUID --input FILE` | MemoryDecision; action must match command / MemoryData / [] |
| `bid memory disable` or `delete --id UUID --input FILE` | MemoryDisable/MemoryDelete / MemoryData / [] |
| `bid memory retrieve --input FILE [--dry-run]` | MemoryRetrievalRequest; dry-run maps to preview / MemoryRetrievalData / MemoryHit[] |
| `bid memory retrieval show --id UUID` | Authorized history / MemoryRetrievalData / MemoryHit[] |
| `bid memory used --job UUID` | MemoryCallData / [] |
| `bid memory feedback list --task UUID` | MemoryPageData / MemoryFeedbackView[] |
| `bid memory candidates run --task UUID --input FILE [--dry-run] [--retry] [--wait]` | event_ids + JobAction / MemoryJobSubmissionData; after wait, MemoryCandidateJobResult / [] |
| `bid memory samples list --task UUID` | MemoryPageData / MemoryEvalSampleView[] |
| `bid memory samples show --id UUID` | MemoryEvalDetailData / [] |
| `bid memory samples review --id UUID --input FILE` | MemoryEvalReview / MemoryEvalData / [] |
| `bid job status/wait/cancel` | Existing job contracts plus authorized memory-kind views, without submission leaks |

Future user/project lists require `--user UUID`/`--task UUID`; disabled scopes fail explicitly. New registered commands only add schema entries without incompatible changes to old JSON. Candidate-job dry-run returns job_id=null/counts with no writes/billing; submission returns durable job IDs, and only `--wait` reflects final completion.

Seven-key empty-retrieval example (hashes are synthetic illustrations, not execution records):

```json
{
  "ok": true,
  "command": "memory retrieve",
  "data": {
    "retrieval_id": null,
    "org_id": "00000000-0000-0000-0000-000000000001",
    "mode": "keyword",
    "retrieval_version": "keyword-v1",
    "priority_version": "memory-priority-v1",
    "query_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "manifest_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "scopes": ["org"],
    "epochs": [{"org_id": "00000000-0000-0000-0000-000000000001", "scope": "org", "owner_id": "00000000-0000-0000-0000-000000000001", "epoch": 0}],
    "valid_until": null,
    "omitted": [],
    "context_chars": 0,
    "preview": true,
    "currently_valid": true,
    "stale_reasons": []
  },
  "items": [],
  "warnings": [],
  "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0},
  "duration_ms": 3
}
```

| Exit code | HTTP / failure | Behavior |
| --- | --- | --- |
| 0 | 200/201/202; valid CRUD, no hits, normal skip/duplicate, successful submission | `ok=true`; claim produced results only after completed waits |
| 2 | 400/422 invalid input/sensitive-value rejection; 409 expected_revision/conflict_key conflict or archived | `ok=false`, no partial revision/candidate; correct input before resubmitting |
| 3 | 503 temporary DB/dispatch unavailability, retryable Provider fault | Retain committed durable jobs/outbox/charges; explicit retry, no repeated human decisions |
| 4 | 401/403/404; disabled scope, embedding_unconfigured, input changes, content refusal, budget block, metering failure | Explicit codes, reject new calls/publication, retain incurred usage |
| 5 | HTTP 200 terminal completion=partial, failed event subsets or inherited card-generation partial results | `ok=false`; success/failure IDs, stop_reason, warnings, cumulative cost; never false complete success |

`data=MemoryErrorData` matches existing `api/main.error_response`: `{"error":{"code":"…","message":"…","exit_code":4}}`. Never echo inputs, queries, candidate text, keys, or raw vendor text. Timeout/unknown usage is not zero cost; cancellation/takeover cannot publish old-attempt results.

## Audit events

Reuse `AuditLog` in [entities.py](../../server/app/models/entities.py) and actor context. Events are `memory.create/update/approve/reject/disable/delete`, `memory.retrieve`, `memory.feedback.record`, `memory.candidate.publish`, `memory.eval.review`, `memory.call.attach`, later `memory.index.publish`. Record org, actor/user/token, object/source/job/run/call, before/after revisions/states, policy versions, hashes/counts/reason hash, never text/raw diffs. memory.call.attach must resolve to vendor_calls/UsageRecord. Expiry creates no fake human action and reads as expired. Role refusal never exposes target text. Global publication will use platform audits only after approval of global-table exceptions.

## Post-approval test plan

These are implementation acceptance requirements, not claims of unexecuted PostgreSQL tests passing. Validate actual API→PostgreSQL RLS→worker→fake Provider→CLI flows, without post-implementation unit tests repeating code.

1. Orgs A/B: test SELECT/INSERT/UPDATE/DELETE for every listed table and `memory_embeddings`, missing context, fake composite FKs, cross-owner/task/card binding, epoch/vector side channels. Immutable tables deny UPDATE/DELETE even within one org. Every HTTP endpoint, including list/cursor/history/used/job status/wait/cancel/feedback/eval/preview, tests A→B returning 404/no B rows, beyond the main memory table. Later global read-only publication separately tests no org write/source ingress.
2. Human gates: human admin org-memory approval grants no technical/commercial evidence-confirmation authority. Tokens requesting evidence:confirm/export/memory approval-management fail; token/agent/worker cannot forge active/confirmed_by, through API/direct SQL. candidate/disabled/expired/deleted never reach prompts; memory refs never become Evidence. Concurrent approve/update/disable/conflict-key approvals/optimistic locks are atomic. Cross-org same-user, others' preferences, project nonmembership/archival become mandatory gates when enabled; initial disabled layers are rejected.
3. Real local flow without vectors: create→approve→exact/Chinese keyword/tag→fake drafting→reject/edit→one system candidate/sample→human approval influences next draft. Test reject/edit/confirm provenance, model_job_id inheritance, no-op edits, sensitive-only/overlong feedback, duplicate dispatch, enqueue interruption, partial/cancel/retry/lease takeover, without real vendors.
4. Retrieval/cache: synthetic rules establish ordering, two precedence classes, lower-layer shadowing, same-level conflicts, complete-item budgets. Cover zero-hit additions, unselected-top_k changes, expiry/fallback candidates, ownership-change attempts, scope/model changes, cache reuse/concurrent disable. Inject changes before admission/in-flight/before publication, record exact call manifests, prevent old-worker publication and charge sent usage once. Confirmed cards receive notices only; existing evidence invalidation still blocks.
5. Provider/billing: fake Embedding checks org+scope constraints before distance/limit, rejects count/dimension/NaN/zero-vector/model mismatches, and no implicit unconfigured fallback. Added drafting-memory characters enter cost bounds; insufficient balance, billed-invalid output, unknown usage, duplicate settlement, and concurrent workers preserve prepaid rules.
6. CLI/API: seven-key snapshots for each new command covering success/empty/invalid/404/409/503/partial and exits 0/2/3/4/5. Local/remote match without interaction, schema matches. Cover wait/status/cancel access and cumulative costs. Human-action dispatch recovery is a separate end-to-end scenario.
7. Artifacts/evaluation: reproducible synthetic JSON reports in `data/work/memory-validation/`, with sample versions, commands, result hashes, associated IDs/assertions, without credentials or docs writes. `MemoryEvalCase` pins relevant/forbidden IDs, expected order/errors. Report recall@k, MRR, priority/isolation violations, human candidate adoption, sensitive-content rejection, incorrect cache hits, cost/duration. Isolation/human-gate violations must be 0; recall targets await baseline. Real Embedding/LLM calls only explicitly in evals. Public synthetic samples may be versioned; org feedback is never automatically outbound and real materials require separate consent.

## Decisions

Approved recommended defaults follow; later capabilities still require enablement conditions.

| Decision | Approved choice/boundary |
| --- | --- |
| First complete flow | Org keyword, deterministic candidates, drafting traces; user/project await private outputs/task membership |
| Global sources | Independent public sources, item-level provenance/license-applicability/version; no org-feedback aggregation/promotion/ingress |
| Global reviewers | Two platform human identities for editing/domain review; platform identities cannot read org content |
| Global storage exception | Later ADR/global-table exception amendment before enablement; initially global disabled with no storage |
| Human additions | All candidate first; org admin session explicitly approves exact revisions |
| Automatic candidates | Deterministic sanitized feedback copies marked pending generalization, without extra model calls/automatic activation |
| Confirmed responses | Retain exact human decisions/lineage with notices; unreviewed model drafts require edit/regeneration; evidence gates remain |
| User preferences/shared cards | Later default private-output use only; resolve job/card/manifest ACL first |
| Default expiry/deletion | Explicit expires_at allowed, no default TTL; logical deletion retains history/audit |
| Embedding | No initial configuration/calls/vector indexes; interface retained, separately enabled after P03 model/dimensions/price/residency choices |
| Automatic evaluation samples | Org-local/unreviewed by default; acceptance permits org-local evaluation only, never implied sharing |

The first slice does not create the optional empty `memory_embeddings` table; migrations therefore neither install nor assume pgvector. Before future vector columns, verify extension installation under [index preflight requirements](#pgvector-and-later-indexing).

Implementation checks follow the [development guide](../guides/development.md#run-the-checks). Runtime contracts are covered by [CLI contract tests](../../server/tests/test_memory_cli.py). DB/call boundaries are in [storage tests](../../server/tests/test_memory_storage.py), [API tests](../../server/tests/test_memory_api.py), [feedback tests](../../server/tests/test_memory_feedback.py), [drafting tests](../../server/tests/test_memory_drafting.py), and [retrieval evaluation](../../server/tests/test_memory_evaluation.py). Verification artifacts go only in `data/work/memory-validation/`, never `docs/` documentation directories.
