---
kind: plan
---

# Check and score in the org console

Status: **Approved, implemented.** Covers the console gaps in
[roadmap](roadmap.md) B09, B10, U01 and U02.

The approved [Pydantic contract](console-assessments/console_assessments_contracts.py)
defines read projections and service interfaces and imports existing write,
Provider and Result contracts. All recommended defaults were approved for implementation.
The merged [task budget contract](budget.md) owns enforcement and Result 4.0 costs;
the separate rubric Provider algorithm remains outside this delivery. Product terminology follows the [glossary](../glossary.md). Mechanism and code links are
in the [console assessment note](../notes/console-assessments.md).

## Goal, boundary and first vertical slice

Give ordinary bid specialists (投标专员) a structured team workflow in the cloud-hosted
org (organization/tenant; 单位) console: identify who must act, explain what prevents the
next step, and return directly to the response card (响应卡) that needs work. A task (任务)
remains scoped to one explicitly selected successful extraction of tender documents
(招标文件). Checking and scoring assess the saved, current draft (初稿), not an uploaded
complete bid (标书), exported Word file or arbitrary URL.

The scope is preview, explicit submission, background-job (后台作业) recovery, reports,
human finding decisions, and scoring-criteria (评分标准) review. Reports remain advisory;
they neither certify compliance nor satisfy human confirmation (人工确认) or export gates.
No new risk-card state machine, assignment system, notification service, automatic fixes,
manual score override, comparison with competitors, task membership, SSE, paid-call
orchestration, or new model dependency is included. Existing domain ownership is the
responsibility mechanism; there is no persisted named assignee to present as an owner.

The first vertical slice is **rules check**: task/draft entry → read-only preflight →
submit → recover/poll job → bounded risk/coverage report → responsible human dismiss or
reopen with a reason → decision history → card correction → reassemble and recheck.
It must work for two orgs and all four roles, without a model service. The next slice
adds combined checking through the same flow. Rubric generation/review is then delivered
through whole-set confirmation; scoring preview/run/report follows that prerequisite.
Each slice includes its browser acceptance before the next starts. The implementation
delivery proceeded after a browser-startup blocker so that all four approved slices
could be completed; this acceptance-order exception is recorded below. Later slices
are fully specified here, not represented by inactive production placeholders.

## Code basis and explicit differences

| Source and real entry point | Contract consequence |
| --- | --- |
| [check API](../../server/app/api/check.py) `create_router`; [check service](../../server/app/services/check.py) `submit_check`, `show_check`, `list_checks`, `decide_finding`, `decision_history`, `validity` | Runs, history, verified findings, staleness and decisions exist. Reuse their behavior; console routes are new. |
| [check inputs](../../server/app/services/check_inputs.py) `access`, `snapshot`, `require_dependencies`; [semantic checking](../../server/app/services/check_semantic.py) `prepare`, `preview` | Preview validates the complete fixed response/comply-only/gap partition. Combined mode sends only confirmed response text; it does not check an arbitrary whole bid. |
| [score API](../../server/app/api/score.py) `create_router`; [rubric service](../../server/app/services/score.py) `human_set`, `classify_rubric`, `decide_coverage`, `decide_section`, `decide_item`, `decide_rubric`, `revise_rubric` | Classification, coverage decisions and domain review are different operations. Admin is not a universal reviewer. |
| [rubric generation](../../server/app/services/score_generation.py) `submit_rubric`, `preview_cost`, `accept_batches`; [rubric adapter](../../server/app/providers/rubric.py) `HTTPRubricProvider.extract_rubric` | Generation currently makes one complete whole-table request. The planned two-stage generation below is not implemented. |
| [score execution](../../server/app/services/score_execution.py) `submit_score`, `preview_cost`, `run_view`, `show_score`; [aggregation](../../server/app/services/score_semantic.py) `aggregate` | Estimates are bounded by a confirmed rubric. Unavailable totals remain unavailable even when a subtotal or possible range exists. |
| [score inputs](../../server/app/services/score_inputs.py) `snapshot`, `source_original`; [score run inputs](../../server/app/services/score_run_inputs.py) `fixed_rows` | Rubric input is the selected extraction's scoring requirements and cited original blocks, not a fresh full-document scan. Scoring uses pinned card-revision text, not the latest editable card text. |
| [authentication](../../server/app/services/auth.py) `Identity`, `SCOPES`, `ROLE_SCOPES`; [response cards](../../server/app/services/response_cards.py) `access` | Revalidate active membership, token status and role/scope intersection on each operation; browser capability hints do not authorize writes. |
| [org console reads](../../server/app/api/org_console.py) `task_get`, `task_job_list`; [job service](../../server/app/services/jobs.py) `status`, `cancel` | Task metadata and single-job reads are reused. Task-job discovery additionally accepts `check`, `score_rubric` and `score` while preserving the parse projection. |
| [router](../../web/src/router.js), [API adapter](../../web/src/api.js) `checkedPath`, `parseResult`, [org helpers](../../web/src/org.js), [JobPanel](../../web/src/components/JobPanel.vue) | Reuse org identity checks, Chinese labels, precise same-origin paths and polling. Assessment paths and partial-result cases must be added explicitly to the adapter during implementation. |
| [runtime contracts](../../server/app/schemas/contracts.py) `CONTRACT_VERSION`, `Cost`, `Result` | The code uses Result **4.0**, including cost basis, platform charge, billing currency, task amount and unresolved/unpriced call counts. Console reads and preflight use these runtime types. |
| [design](../design.md#dashboard-and-agent-design) | Its pending/fixed/ignored risk cards and SSE are not current check behavior. Actual findings have open/dismissed states plus decision history, and jobs are polled. There is no “mark fixed” operation. |
| [checking plan](check.md), [checking notes](../notes/check.md), [scoring plan](score.md), [scoring notes](../notes/score.md) | Check decision-history reads currently do not attach stale warnings, despite the check plan's wording; obtain current report validity separately. Rules coverage serializes `semantic_outcome:null` and `semantic_citations:[]`, not absent fields. Score schema permits `range_only`, but `aggregate` currently emits `estimated` or `unavailable`. |

The existing [org console contract](org-console.md) excludes checking/scoring and uses
full-set reads for response review. This contract extends that scope explicitly and
requires bounded assessment projections; it does not silently change existing card APIs.

## Navigation and shared page behavior

Add task-local entries “检查风险” and “评分预估” to the task, review and draft pages. Keep the
existing task navigation shell from [App.vue](../../web/src/App.vue), with these approved
browser routes (the router's `/app/` base is included here):

| Page | Route and primary action |
| --- | --- |
| Check workspace/history | `/app/org/tasks/:taskId/checks?job=E&draft=D`; “预览检查” |
| Saved check report | `/app/org/tasks/:taskId/checks/:reportId`; “查看处理记录” / “前往响应卡修改” |
| Rubric list/generation | `/app/org/tasks/:taskId/score-rubrics?job=E`; “预览生成评分规则” |
| Rubric review | `/app/org/tasks/:taskId/score-rubrics/:rubricId`; current-role next action |
| Score workspace/history | `/app/org/tasks/:taskId/scores?job=E&draft=D&rubric=R`; “预览评分” |
| Saved score report | `/app/org/tasks/:taskId/scores/:reportId`; “查看失分原因” / “前往响应卡修改” |

`E/D/R` denote actual IDs, never client-selected org/owner identities. A report deep link
loads its pinned extraction/draft/rubric; a contradictory query selection is rejected or
replaced visibly, never applied to that report. New-run pages require an explicit
extraction. The input read identifies the most recent valid draft for that extraction,
ordered by `(created_at,id)`; preserve an explicitly selected current draft rather than
switching it silently. Show the latest draft separately if stale. “尚未生成初稿” links to
assembly; “初稿已过期，请重新组表” links to the selected extraction's drafts page.
No draft blocks checking/scoring, but not rubric generation from a successful extraction.

Every page shows task name, extraction, draft/rubric version where applicable, scope
“仅评估已保存初稿”, and the next responsible role. Use “商务负责人（投标专员）”,
“技术负责人” and “待管理员分配职责”; qualification (资格) work follows the stored commercial
domain rather than a client-side category guess. Read-only users can inspect these
instructions but cannot execute them. Do not imply a specific person has been assigned.

Keep only IDs, page size and non-text filters in org-specific session navigation state.
Unsaved reasons, responses, source text, replacement forms and signed links stay in
memory. Warn on leaving unsaved forms; org switching clears them after the existing
leave guard. Org/session change aborts requests, clears previews and authorizations,
and discards late responses by the existing epoch check. Refresh recovers jobs and
reports from server reads, not local assumptions about a previous submit.

## Preview, cost and submission

Reuse `CheckRequest`, `RubricGenerateRequest` and `ScoreRequest` without a second write
schema. The assessment date is visible/editable as “评估日期” before checking/scoring;
its exact date enters the hash and certificate-date evaluation. A human explicitly
chooses “规则检查” or “规则与语义检查”. Describe rules as deterministic coverage/date/
deviation checks; combined additionally assesses confirmed responses semantically.
Neither button claims a complete legal or submission review.

All three previews are zero-write: no Job, report, audit (审计), UsageRecord, VendorCall,
reservation or balance mutation, and no Provider call. Input locking and read-time
verification may still occur. A successful preflight with `admission_blocker` remains
HTTP success/`ok=true`; it is not permission to submit. Invalid or stale inputs can
instead return a service error before a preview exists. In that state show unavailable
cost, not a fabricated zero-cost preview.

The preview panel contains these server-derived values and plain-language explanations:

| Field | Chinese presentation and behavior |
| --- | --- |
| Input scope/counts, current draft and rubric version | “本次检查范围” / “本次评分范围”; gap (缺口) and comply-only (须遵守) counts remain visible. The UI never derives completeness from the visible page. |
| `estimated_cost` | “预计服务用量成本（美元）”; unknown `usd` is “暂无法估算”. Rules mode is “不调用模型，费用为 0”; no-model-call combined cases retain their explicit reason. |
| `estimated_charge`, `billing_currency`, `cost_basis_reason` | “预计平台扣费上限（首轮）”; show actual currency, known/unknown basis, and that retries/actual usage can differ. It is not a final invoice. |
| `max_charge` | “本次作业平台扣费上限”; for model runs require explicit confirmation of a positive cap, initially rounded upward from the estimate where known. Do not send a cap or reasoning for rules mode. An org-owned key's zero platform charge is not free vendor usage, and this cap does not bound its vendor bill. |
| Enforced task budget | “任务预算（实际执行）”; use `TaskBudgetView` limit, currency, spent, reserved and available from the API. Null limit means “未设置”; null available means unknown. Read `budget_preflight` for admission blockers and first-pass estimates; never derive remaining budget from `cost.usd` or platform charges. |
| Provider/model/revision, reasoning, redaction (遮挡) revision/counts | “使用的模型”, “外发内容已遮挡”; keep values from the actual preview. Do not send originals or render private prompt manifests. |
| `admission_blocker`, limitations, nullable duration | Persistent Chinese blocker with the next owner/action. Unknown time is “耗时暂无法估算”; no countdown prediction. |

Task budgets are enforced by the merged budget service and Result 4.0. Previews expose
`budget_preflight`, including the current `TaskBudgetView`, next-call admission blocker,
first-pass estimate and uncertainty. Display those server values without predicting
admission from a browser balance calculation. Newly added assessment reads and console
CLI variants require contract version 4.0; legacy no-option commands retain their
existing behavior. This does not claim that org monthly caps are enforced.
The existing `/billing` page is for authorized admins, so other users see “请联系管理员
补充余额”, not a forbidden balance page or an invented balance figure.

Map `redaction_required` to “尚未开启外发遮挡，请由管理员或投标专员处理后重新预览”;
stale draft/hash errors to “初稿内容已变化，请重新组表并预览”; insufficient balance to
“余额不足，暂不能提交”; unknown price/capability/context limits to the corresponding
model-configuration or input action. Preserve unrecognized codes with a safe Chinese
fallback; never hide them by changing mode automatically.

After a valid preview, require a separate submit action and, for model runs, an
unchecked “我已核对外发范围与费用上限” control. Submit its `expected_input_hash`, exact
scope/mode/date/reasoning and cap. Any input/settings/cap change clears consent and
requires a fresh preview. Recheck on submission; `*_input_changed` discards consent,
keeps nonsecret user inputs, and offers “重新预览”. Disable repeat clicks while in flight.
Do not automatically repeat paid requests after network errors. A `queue_unavailable`
response contains a durable job ID: show “任务已保存，等待重新调度”; an explicit recovery
action may resend that exact request under existing idempotency rules.

Read-only report wrappers' default `cost=0` are the cost of that read, not historical
model expense. Show actual job cost and charge from job status/result where authorized;
no cost is inferred from report `usage_record_ids`.

## Jobs and planned two-stage rubric generation

Reuse [JobPanel.vue](../../web/src/components/JobPanel.vue): poll about every two seconds,
back off transient failures up to its existing limit and honor `Retry-After`, pause in
hidden tabs, stop on terminal states, and release listeners on unmount. Polling errors
do not submit or cancel a job. Show “排队中”, “处理中”, “已完成”, “部分完成”, “处理失败”
or “已取消” with attempts, safe reason and next action. Job success, report completeness,
and report freshness are separate indicators. Cancellation is explicit and explains
that already admitted calls may still incur charges. A failed/cancelled job may offer
the existing explicit retry after revalidation; it cannot take over an active lease.

Assessment job discovery must include queued/running/failed/cancelled jobs with no
published report. A cached receipt reuses the existing job and shows “已复用相同输入的
结果”; cache keys include initiator identity, so this is not universal task-wide reuse.

The planned generation algorithm is **stage 1: derive sections and the overall rule
from the whole scoring table; stage 2: generate items in batches against those fixed
sections**. This is distinct from the existing product flow rubric → score run.
The optional `StageProgress` carries a strategy version, scheme, stage,
completed/total batch counts and the stage-two fixed-section hash. It never publishes
prompts, raw stage output or vendor call bodies.

| Server observation | Display |
| --- | --- |
| No progress field, including current workers | Indeterminate “正在生成评分规则”; no guessed stage or percentage |
| Single-pass `whole_table` | “正在分析整张评分表”; indeterminate until a real completion signal |
| Two-stage `sections` | “第 1 步：确定分节和总分规则” |
| Two-stage `items` with known counts | “第 2 步：生成评分条目，已完成 X / Y 批”; stage-local progress, not an overall percentage |
| Unknown counts | Stage label plus indeterminate progress; unknown is not zero |
| `validation` / `publication` | “正在核对引用与完整性” / “正在保存待审核规则” |
| Interrupted stage or publication | Show the stop reason and preserved terminal result if any; stage-one output alone is not a confirmed or complete rubric |

The existing [rubric adapter](../../server/app/providers/rubric.py) and
`score_generation.accept_batches` require one complete batch. Changing those interfaces,
batch validation, cache/price estimates, cancellation, partial publication and the
fixed-section fence belongs to a later Provider contract. This draft only reserves a
safe progress projection. Old workers omit it; no backfill or simulated progress.

## Check report and human decisions

Show summary counts for the complete report, then group findings by severity in order
“废标风险”, “扣分风险”, “提示”, with commercial/technical/unclassified subgroups.
Bid rejection (废标) and point deduction (扣分) are risk labels, not final determinations.
Within a group preserve stable server order. Filters include status, severity, domain
and requirement; changing a filter resets the cursor. Show both filtered and total
counts. Dismissed findings remain in history/counts and can be deliberately included;
they do not disappear from the machine finding total.

Each finding presents method, reason, responsible domain, status and revision. An
expandable split view shows the tender quote with PDF page or Word section/paragraph/
table-cell location, and the exact saved response (响应) or deviation (偏离) text with
the cited span highlighted. A draft citation has a draft/response/card-revision/field
identity, not a fabricated PDF page. Evidence (证据) citations resolve only saved,
authorized evidence. Original previews are on demand through existing protected
document/page APIs; render tender/model/user text as text, never executable HTML.

Keep coverage alongside findings: every selected requirement's response/comply-only/
gap partition, deterministic observations, `semantic_status`, uncertainty reasons and
no-risk semantic citations remain inspectable. “未发现风险” appears only with its method
and evaluated scope. No findings plus unassessed requirements is “部分内容未完成检查”,
not “全部通过”. Certificate (证照) date status shows the assessment date and affected
requirements; unknown dates remain unknown rather than valid. Rules-only rows show
“未请求语义检查” even though semantic fields serialize as null/empty.

Only a human with the stored review domain can use “忽略此风险” or “重新打开”; a required
reason dialog shows the finding, action and expected revision. Reopen is a new event,
not deletion of the dismissal. Submit `FindingDecisionRequest` with the report input
hash and finding revision. Do not optimistically hide a finding before acceptance.
After success refresh the row, summary and history. A conflict preserves the typed
reason, fetches current state, and requires a deliberate new decision.

An unclassified finding offers “请管理员到响应卡分配职责，再重新组表检查”; there is no
finding-classification endpoint. Admin cannot dismiss it on behalf of a domain.
“前往响应卡修改” navigates to the existing review route with `job=E&requirement=Q` and
the current card if present, while the report retains its historical card revision.
Missing cards open that requirement's review position. Corrections must pass the
existing human review and assembly gates before a fresh check; there is no “fixed”
shortcut, automatic dismissal or automatic confirmation.

Report history shows mode/date/draft, completion, freshness and job link. Decision
history shows actor/time/action/reason in revision order. Read parent validity beside
history because the current history endpoint lacks stale warnings. A persistent
“报告已过期，仅供追溯。请重新组表并检查” banner disables decisions/run-from-old-preview,
but retains readable historical findings when dependency authorization still permits
access. New reports do not inherit earlier dismissals.

## Rubric generation, review and full replacement

The rubric workspace starts from a successful extraction and shows its scoring
requirements. Empty input is “所选提取结果没有评分要求，请核对提取范围”, with a link to
extraction history, not a zero-score rubric. Generation preview/submit follows the
shared flow above; partial generation is a candidate requiring review, never an
automatically complete set. Rubric versions and `prior_rubric_id` stay visible.

Use a work queue with four explicit steps; each count comes from the whole server set:

1. **“分配审核职责” — administrator.** Show unclassified sections and items, sources and
   rules. Classify each with a reason. Section classification does not imply its items
   were classified. Changing a domain invalidates the affected confirmation.
2. **“核对评分要求覆盖” — bid specialist.** Each scoring requirement must map to its own
   item(s), identify a canonical duplicate, or be explicitly excluded with a reason.
   Display canonical text and target items before accepting; pending/reopened entries
   block completion. No automatic “exclude remaining” or implicit acceptance of model
   mapping. Map/duplicate/exclude all require a recorded human decision.
3. **“审核分节和条目” — responsible domain.** Review source, rule text, range, weights,
   caps, assessment mode and ambiguity/unassessable reasons. Section and item decisions
   have separate confirm/reject/reopen controls and reasons; classification is not
   confirmation. Commercial goes to `bidder`, technical to `technical`.
4. **“确认整套评分规则” — bid specialist.** Display the overall aggregation rule and the
   complete checklist below, then explicitly confirm with reason/revision/hash. This
   does not substitute for any section/item/coverage decision.

Explain `RubricCompletenessView` in these terms, with paged links to affected objects:

| Server check | Chinese instruction |
| --- | --- |
| Coverage count, pending requirements | “每条评分要求都要说明如何处理：对应评分项、重复项或有理由地排除。” |
| Duplicate fingerprints/canonical cycles | “重复评分项或循环指向尚未处理，请先核对，避免重复计分。” |
| Unconfirmed section/item IDs | “各职责负责人需逐项确认分节规则和评分条目；驳回或待审核项尚未完成。” |
| Section/overall aggregation flags | “分节如何合计、哪些分节进入总分以及总分规则都需确认。” |
| Normalization errors | “引用、条目归属、顺序、分值范围、权重或封顶值仍有冲突，请按定位提示修订。” |

`formula` and `non_additive` rules can be reviewed with their fixed original wording
and limitations even though the system cannot execute them. Say “规则已核对，但系统无法
自动合计”; do not block review solely because aggregation is unassessable, execute
arbitrary formulas, or claim confirmation guarantees an available score total.

Revision opens a structured **full replacement** editor, with sections/items/coverage
and overall rule. Load every page for one unchanged set revision/snapshot before
enabling save. Paged review must never submit just the current page as replacement.
Retain `source_section_id`/`source_item_id`, keys and pinned requirement identities.
Section `sources` selects verified `{requirement_id, quote}` pairs, retaining at least
one; the server derives each immutable Source. Items retain one requirement identity.
Quotations are selected from verified bindings, never edited. Comparison
shows added/removed/changed content. A technical reviewer may edit only their permitted
domain and must retain the overall rule and other-domain content exactly; a bid
specialist has the corresponding commercial boundary and may revise the overall rule.
Unclassified content follows the actual `revise_rubric` gate, not a client assumption.

`ConsoleRubricSectionView` inherits the complete ordered `sources` list, with each
Requirement, original Source and verified quotation, under the
[section multi-citation amendment](score.md#rubric-versions-coverage-and-human-confirmation-人工确认).
Show all entries and use `origin=sources&citation_index=N` for each section citation's
context; items and coverage retain `origin=source`. Do not infer Requirement IDs by
matching source text: more than one Requirement may cite the same location. Build the current editing baseline from
this complete same-snapshot section/item/coverage graph and current summary revision,
hash and overall rule; this works for generated first versions without revision events.
Other-domain and unclassified existing content must be preserved exactly under
`revise_rubric`; a disabled edit explains which classification/review is needed first.

Save one `RubricReviseRequest`, with all coverage requirement IDs exactly matching the
fixed scoring set and a required reason. Show “将创建新版本；所有职责分类、条目确认和覆盖
决定需要重新完成”. The new rubric and children have new IDs; prior state is superseded,
new children are unclassified candidates, and coverage resets to pending. Proposed
replacement mappings are suggestions, not carried approvals. A narrowly scoped
read of the existing revision event's `replacement_snapshot` recovers these suggestions
after refresh; the existing history DTO does not expose them. It is never replayed with
its old expected revision/hash as a new authorization, and it never replaces the
current editing baseline. Newly recorded coverage decisions take precedence over an
old proposal; applying a recovered pending proposal still requires human review.

The browser bounds replacement serialization to 512 KiB UTF-8, matching the existing
CLI file limit. If too large, retain the form and report “完整修订内容超出当前编辑上限”; no
truncation or partial save. This is a console limit, not a claim that the current HTTP
schema imposes it. A broader large-rubric replacement interface needs a separate decision.
Reopening a confirmed set precedes child changes under the existing state machine;
superseded sets are read-only and link to the replacement. Whole-set or source changes
invalidate downstream score previews/reports.

## Score run and report

Require a current draft and a confirmed rubric for the same task/extraction/document.
Show selected rubric version and “已核对评分规则” separately from current draft status.
Unconfirmed rubric links to the exact checklist, not a generic error toast. Score
preview displays selected item counts, `preflight_unassessable_item_ids`, reasons and
limitations before paid consent. No assessable items may legitimately yield a zero-call
report with unavailable scores; offer that explicitly, never label it a successful
zero-point estimate. The known IDs alone are not sufficient to calculate why an item
is unassessable; read its rubric mode/anchor state or show the actual reported reason.

Report rows show the scoring item, confirmed range, `estimated_score` when assessed,
lost-point reasons, improvement actions and verified tender/draft citations in context.
Unassessable rows show “无法评估” plus reason and next action, with no numeric zero.
Responses missing from the confirmed draft link back to their requirement. Advice
cannot fabricate supporting material or alter a confirmed score in this UI.

| Server state | Required display |
| --- | --- |
| Item `outcome=assessed` | “预估得分”; preserve range and full reasons; an actual zero is a number |
| Item `outcome=unassessable` | “无法评估”; `estimated_score=null`, never coerce null to zero |
| Section `status=estimated` | “分节预估”; use only `estimated_score` |
| Section/report `range_only` | “可能区间，不能作为得分”; only when explicitly sent by a future compatible server |
| Section/report `unavailable` | “分节暂不能合计” / “总分暂不可用”; explain unresolved items/rule/call failures |
| `assessed_subtotal` | Optional “已评估部分小计（不是总分）”, subordinate to unavailable state; never title it total or graph it as total |
| `total_status=estimated` | Only then show `estimated_total` as “总分预估”; still advisory |

Do not calculate totals in JavaScript, round intermediate weights, or convert a non-null
`possible_range` into an estimated total. Current aggregation makes any unassessable
item result in partial completion and an unavailable total, including items in sections
excluded from the overall aggregation. Preserve that behavior rather than silently
inventing a more permissive calculation. Score history and stale banners retain the
historical result with “初稿或评分规则已变化，请重新预览评分”; partial, stale and unavailable
are independent states. A stale report alone is not a failed read.

## Data model and migration outline

**No new org business table is needed.** These pages are projections of existing
assessment state; do not create console report copies, reviewer assignments, saved
decisions, progress tables or cross-org material caches. The models in the companion
file describe transport data, not SQLAlchemy persistence.

| Existing tables and source | Preserved relationships and use |
| --- | --- |
| `check_runs`, `check_items`, `check_certificates`, `check_certificate_items`, `check_findings`, `check_finding_citations`, `check_decisions`; [check models](../../server/app/models/check.py), [0032](../../server/migrations/versions/0032_check.py), [0033](../../server/migrations/versions/0033_check_semantic.py), [0036](../../server/migrations/versions/0036_check_semantic_scope.py) | Report/job/draft/extraction graph; exhaustive requirement coverage; certificates, findings, verified citations, append-only decisions. |
| `score_rubric_sets`, `score_rubric_sections`, `score_rubric_items`, `score_rubric_coverage`, `score_rubric_decisions`, `score_rubric_classifications`, `score_rubric_coverage_decisions`, `score_rubric_coverage_items`, `score_rubric_revision_events`; [score models](../../server/app/models/score.py), [0034](../../server/migrations/versions/0034_score_rubric.py) | Versioned rubric graph, domain decisions, coverage mappings and original complete replacement snapshots. |
| `score_reports`, `score_report_items`, `score_report_item_responses`, `score_item_citations`; [0035](../../server/migrations/versions/0035_score.py) | Report bound to rubric/draft/extraction/document; item support bound to saved response rows/card revisions. |
| `tasks`, `jobs`, draft/card/requirement/chunk/document/material parents, audit/usage records | Reuse the established task and immutable input graph; private job submission/encrypted input fields are never public progress. |

The assessment tables already have `org_id NOT NULL`, ENABLE and FORCE RLS, with
USING/WITH CHECK current-org policies. Preserve `(org_id,id)` uniqueness and composite
FKs including task and parent identity: findings/items/citations/decisions cannot point
outside their report; rubric children cannot point outside their set; score support
cannot swap draft/card revisions. No new global-table exception or BYPASSRLS role.
Referencing a global User ID is not proof of an authorized org reviewer.

Migration outline: first reuse the existing tables and indexes; add read projections
and SQL keyset queries without data migration or backfill. If query plans require an
index, propose an additive org/task/report/filter/order index in the implementation
change after measurement, with no RLS or ownership change. Optional future stage progress
can be a versioned safe field in existing job result JSON, written only by the current
run/lease owner; changing the Provider algorithm remains separate. Rollback removes
new routes/UI use and ignores optional progress without dropping assessment history.
If later design adds persistence, it must first amend this draft with each table's
NOT NULL org_id, FORCE RLS, composite keys, least-privilege grants and two-org tests.

## HTTP interfaces and bounded reads

All routes return the existing seven-key `Result`. No browser-specific mutation or
assessment-run orchestrator is introduced. Existing registrations in the linked APIs are:

| Existing route | Input → `data` / `items` |
| --- | --- |
| `POST /tasks/{T}/checks` | `CheckRequest`; `dry_run=true` → `CheckPreview`, otherwise `AssessmentJobAccepted`; `items=[]` |
| `GET /tasks/{T}/checks` | `AssessmentListData` / `CheckRunView[]` |
| `GET /checks/{C}` | `CheckReportData` / `FindingView[]` |
| `POST /checks/{C}/findings/{F}/decisions` | `FindingDecisionRequest` → `FindingDecisionData` / `[]` |
| `GET /checks/{C}/findings/{F}/decisions` | `AssessmentListData` / `FindingDecisionView[]` |
| `POST /tasks/{T}/score-rubrics/preview` | `RubricGenerateRequest(dry_run=true)` → `RubricPreview` / `[]` |
| `POST /tasks/{T}/score-rubrics` | Same request with `dry_run=false` → `RubricJobAccepted` / `[]` |
| `GET /tasks/{T}/score-rubrics` and `/{R}` | List: `ScoreListData` / `RubricSetView[]`; show: `RubricReportData` / `[]` |
| `POST /tasks/{T}/score-rubrics/{R}/revisions` | `RubricReviseRequest` → `RubricReportData` / `[]` |
| `POST .../{R}/sections/{S}/classification`, `.../items/{I}/classification` | `RubricClassifyRequest` → `RubricClassificationView` / `[]` |
| `POST .../{R}/sections/{S}/decisions`, `.../items/{I}/decisions` | `RubricSectionDecisionRequest` / `RubricItemDecisionRequest` → `RubricDecisionView` / `[]` |
| `POST .../{R}/coverage/{Q}/decisions`, `.../{R}/decisions` | `RubricCoverageDecisionRequest` / `RubricSetDecisionRequest` → `RubricCoverageDecisionView` / `RubricSetView` in data; Q is the requirement ID, not the coverage row ID |
| `GET .../{R}/history` | `ScoreListData` / `RubricHistoryItem[]` |
| `POST /tasks/{T}/scores/preview`, `POST /tasks/{T}/scores` | `ScoreRequest` with true/false dry_run respectively → `ScorePreview` / `ScoreJobAccepted` |
| `GET /tasks/{T}/scores` and `/{S}` | List: `ScoreListData` / `ScoreRunView[]`; show: `ScoreReportData` / `[]` |
| `GET /jobs/{J}`, `POST /jobs/{J}/cancel` | Existing status/cancel with each kind's `job_access`; no new status or cancel endpoint |

Existing paged list/history limits are 50 by default, at most 200. Keep their full-mode
JSON and command names compatible. The following **approved interfaces** are the minimum extra
reads and opt-in variants used by this console:

| Proposed route/variant | Purpose and contract |
| --- | --- |
| `GET /tasks/{T}/assessment-inputs?job=E` | `AssessmentInputsData`: explicit extraction, latest/current draft IDs and validity, enforced task budget, redaction state, run/generate capabilities. Avoid fetching every draft's response body just to start. |
| Extend `GET /tasks/{T}/jobs?kind=check\|score_rubric\|score&extraction_job_id=E&cursor=&limit=` | `AssessmentJobPageData` / `AssessmentJobView[]`; recover unfinished jobs after refresh. Each row uses the kind's existing access checks; safe result ID/error/progress only. Existing parse projection remains unchanged. |
| `GET /tasks/{T}/checks\|score-rubrics\|scores?view=console&extraction_job_id=E&cursor=&limit=` | `AssessmentHistoryQuery` → existing `AssessmentListData` / the corresponding compact summary items; these are three existing collection routes, not a literal combined path. Default full-mode results remain unchanged. |
| `GET /checks/{C}?view=console&part=summary` | `CheckSummaryData` / `[]`, no coverage/citation arrays |
| Same route with `part=findings\|coverage\|certificates\|notices` | `CheckPageRequest` → `PageData` / shared exact row views or `Notice[]`; optional severity/domain/status/requirement/entry filters |
| `GET /tasks/{T}/score-rubrics/{R}?view=console&part=summary` | `RubricSummaryData` / `[]`; bounded completeness counts, overall rule and human capabilities |
| Same route with `part=sections\|items\|coverage\|blockers` | `RubricPageRequest` → `PageData` / shared row views (section adds its persisted requirement_id) or localized `Notice[]`; each blocker has a subject/requirement link where available |
| Same route with `part=replacement` | `RubricReplacementData` / `[]`; on-demand existing revision snapshot, bound to prior/new rubric; generated rubrics without a revision event return `not_found` |
| `GET /tasks/{T}/scores/{S}?view=console&part=summary` or `sections\|items\|notices` | `ScoreSummaryData` or `ScorePageRequest` → `PageData` / shared views; preserve unavailable semantics |
| `GET /tasks/{T}/assessment-citation?...` | `CitationRequest` → `CitationContextData`; traverse an authorized saved parent/entry and source or citation index, returning a bounded original-text window and IDs for the card link. No arbitrary text/file/URL body. |

`ConsoleAssessmentReads` specifies these service methods. They take authenticated
`Identity`, never a request-supplied role/org/owner. They reuse `get_run`/`get_set`,
dependency checks, snapshot validity and existing source verification rules. Read
transactions must not issue a Provider call or write audit/usage/progress. Do not
implement the new endpoints by calling full `show_check`/`show_rubric`/`show_score`
and slicing their already materialized results.

For the new variants, default page size is 50, maximum 100. `Result.data` includes total,
filtered_total, returned, next_cursor, validity, a snapshot token, parent revision when
mutable, and actions for only the returned subjects. `Result.items` contains one row
kind selected by `part`; reject incompatible filters with `invalid_input`. A specific
`entry_id` is the same authorized detail read with at most one row. Notice text is
Chinese explanatory copy with a stable code, never a truncated substitute for rule text.
Duplicate-group notices bind one subject per row to a stable `group_id` and full
`group_member_count`; `part=blockers&group_id=G` pages the exact members of that group.
Counts of groups and counts of member rows are distinct. Do not infer group membership
from matching text or combine unrelated duplicate errors across pages.

Cursor signatures bind org, authenticated principal, task, parent, part, filters,
sort and review snapshot, with expiry. Stable child ordering uses saved order/key/ID
or requirement order/ID, and finding severity/domain/ID; history retains revision order.
A decision changes the review snapshot: reject old cursors with
`assessment_view_changed` (409/exit 2), refetch summary and return to the affected group.
Never merge pages from different rubric revisions for confirmation or replacement.
Totals describe the entire authorized graph, not only loaded pages. Use SQL keyset
pagination and batched parent checks before hydrating large text; preserve the existing
fail-closed dependency checks even for objects outside the returned page. A new read
must not expose counts for inaccessible parents.

The present full-show APIs are too heavy: check hydrates coverage/certificates/findings
and repeated citation/decision reads (up to 2,000 requirements, 20 findings per
requirement, 20 citations per finding); rubric show returns all sections/items/coverage;
score show returns all items/support/citations. Rubric/score history and list services
also load full sets before in-memory pagination. `Source.quote`, several arrays and
full completeness diagnostics are not byte-bounded just because row limits exist.
The implementation must move pagination ahead of hydration; full CLI reads remain
deliberate full reads. Apply the same SQL pagination improvement to existing lists/
histories without changing their wire shape.

`revise_rubric` also returns the whole new `RubricReportData`, and whole-set decisions
return a `RubricSetView` with full completeness diagnostics. Propose an optional
`?view=console` response projection on just those two existing POST routes, returning
`RubricSummaryData` after the same atomic operation; mutation input, gates and audit
stay unchanged. This bounds the receipt and directs the page to its paged reads.
Classification and child/coverage decisions already return their single event; no
separate console write service is needed. The 2 MiB check must be applied to a receipt
projection before commit, never turn a committed action into an ambiguous size error.

Cap each new encoded Result page at **2 MiB UTF-8**, including metadata. End a page
before the next whole row would exceed the cap and issue a continuation cursor; do
not cut verified quotes, omit items or mark the query complete. A single oversized
row returns `assessment_entry_too_large` (422/exit 2) with its authorized ID
and keeps the detail unavailable; this is a visible delivery limit, not an empty
success. The open decision below proposes compact row metadata plus separately paged
narratives if real data exceeds that limit. Summary diagnostics are counts; full
notices/blocker subjects are paged. The citation read returns at most 8,000 Unicode
characters per window, absolute quote offsets, continuation offset and total length;
it verifies the entire saved quote before displaying a window. “还有原文未展开” explicitly
marks a partial text view. There is no random access to unrelated neighboring chunks.

For tender context reuse `Source` semantics: PDF page or Word `Location`, never both.
For draft context load the pinned response text/deviation note using the saved row and
card revision. For evidence, return only the authorized cited saved text. Verify parent
task/org, immutable source hash/location and quoted text; unavailable/corrupt sources
produce an explicit error, not `verified=true` on unchecked text. Card fix IDs identify
the current editable card separately from the historical citation. Readable history
still depends on source/material access and cannot bypass revocation.

## CLI JSON, Providers and service boundary

All existing commands in [check.py](../../cli/bid_cli/check.py),
[score.py](../../cli/bid_cli/score.py) and [schema.py](../../cli/bid_cli/schema.py) remain
the write contract. Every command supports `--json`; missing flags fail without an
interactive prompt. The console calls HTTP using the same DTOs rather than spawning
the CLI inside the browser.

| Command group | Required parameters / JSON structure |
| --- | --- |
| `bid check run` | `--task T --draft D --as-of YYYY-MM-DD --mode rules\|combined`; `--dry-run` returns CheckPreview, otherwise `--expected-input-hash H` is required; optional supported `--reasoning`, model-only `--max-charge`, `--retry`, `--wait`; accepted/waited data is AssessmentJobAccepted/CheckJobResult |
| `bid check list/show/decide/history` | list: `--task`; show: `--report`; decide/history: report/finding; decision carries expected revision/hash/action/reason through existing flags; list/history support cursor/limit |
| `bid score rubric generate` | `--task T --extraction-job E`, preview via `--dry-run`, submit via `--expected-input-hash H`, optional reasoning/cap/retry/wait; RubricPreview/RubricJobAccepted/RubricGenerateResult |
| `bid score rubric list/show/history` | task plus rubric where needed; list/history cursor/limit; shared rubric views/history |
| `bid score rubric classify` | task/rubric, exactly one `--section` or `--item`, `--input FILE` → RubricClassifyRequest |
| `bid score rubric section decide/item decide/coverage decide/decide` | task/rubric and section/item/requirement where applicable, `--input FILE`; corresponding shared decision request |
| `bid score rubric revise` | task/rubric plus `--input FILE`; full RubricReviseRequest, at most 512 KiB file |
| `bid score run/list/show` | run: task/draft/rubric/as-of, dry-run or expected hash, optional reasoning/cap/retry/wait; ScorePreview/ScoreJobAccepted/ScoreJobResult; list/show share existing report structures |
| `bid job status/wait/cancel` | Existing job operations; terminal cost/warnings/charge and failure code preserved |

Proposed additive read access keeps CLI/HTTP discoverability aligned without a new
write workflow: `bid assessment inputs --task T --extraction-job E`,
`bid assessment citation --task T --input FILE` (CitationRequest), and
`bid assessment jobs --task T --kind check|score_rubric|score [--extraction-job E]`.
Extend the three existing list commands with `--view console` and optional extraction
filter, and the show commands with optional `--view console --part ...`,
cursor/limit and the corresponding typed filters. `--part replacement` uses rubric
show. Rubric revise/set-decide may use `--view console` for the compact receipt.
No flags preserves the old JSON. Register each new command/variant in `bid schema`
with its exact Pydantic input/data/item types, and provide identical remote/local
behavior using the same API/service authorization. Do not add human-only token scopes
to make a CLI example work.

The wire envelope remains exactly:

```json
{
  "ok": true,
  "command": "assessment jobs",
  "data": {
    "task_id": "00000000-0000-0000-0000-000000000001",
    "kind": "check",
    "total": 0,
    "next_cursor": null
  },
  "items": [],
  "warnings": [],
  "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0, "basis": "zero", "charge": "0", "billing_currency": "USD", "task_amount": "0", "unpriced_calls": 0, "unresolved_calls": 0},
  "duration_ms": 0
}
```

This is an illustrative empty read, not production fixture data. Runtime Cost.usd is
`float | null`; assessment Money fields use Decimal strings in JSON. A blocked preview
retains `ok=true` with its admission blocker. An unavailable score read uses `ok=false`
and retains valid data, e.g. `total_status:"unavailable", estimated_total:null`; never
replace that response with an error-only toast. `ProjectionPage` is an internal typed
pair mapped to `Result.data/items`, not a second public envelope.

| Exit | Required behavior |
| --- | --- |
| 0 | Valid read, successful preview (including a reported admission blocker), accepted job or complete available result; a stale readable report alone does not imply failure |
| 2 | Argument/shape/input errors, invalid cursor, stale hash/CAS/review conflicts and projection-size errors; correct input before retry |
| 3 | Retryable transport/rate-limit/queue failure; retain durable job ID and honor Retry-After |
| 4 | Human/role denial, terminal non-retryable failure/cancellation, explicit admission rejection or integrity failure; use actual service `data.error.exit_code`, not HTTP status guesses |
| 5 | Check/rubric/score partial completion; score show also when unassessable items exist or total_status is not estimated; preserve useful rows and warnings |

Console summary/page variants inherit their parent report's partial semantics.
Page count/next_cursor alone never means partial execution. Add only these known
check/score/rubric terminal and partial job cases to `api.js` parsing. Failed/cancelled
job status is a recognized state to render with its validated error; arbitrary
`ok=false`, malformed JSON or missing seven keys remains an error. Neither HTTP 200
nor a visible report is proof that all work completed.

Reuse `CheckProvider` from [providers/base.py](../../server/app/providers/base.py),
`RubricProvider` and `ScoreProvider` from
[score_contracts.py](../../server/app/schemas/score_contracts.py); the companion module
re-exports them without adapters. Submission stays in `submit_check`, `submit_rubric`
and `submit_score`. The new read protocol never calls these Providers. Model calls,
billing, fixed inputs and job publication remain owned by the existing services;
there is no console-only model call or second scoring implementation.

## Permissions, org isolation and audit

| Operation | Human roles and scopes |
| --- | --- |
| Read check/score/rubric and inputs | All four org roles; check:read or score:read, task:read, and actual draft/card/material parent-read scopes as applicable. The combined input helper requires both assessment read scopes; it reveals no out-of-scope material. |
| Check/run, rubric/generate, score/run | admin, bidder, technical; corresponding check:run, score:rubric:generate, score:run plus prerequisite read scopes; viewer cannot run |
| Finding dismiss/reopen | Human session only, check:decide; commercial→bidder, technical→technical; admin/viewer/token/worker denied |
| Rubric classification | Human admin with score:rubric:review |
| Rubric section/item decisions | Human score:rubric:review and stored domain match: commercial→bidder, technical→technical |
| Coverage decisions and set confirm/reopen | Human bidder with score:rubric:review |
| Full replacement | Human bidder/technical under cross-domain preservation constraints; overall rule editable by bidder only |
| Job read/cancel | job:read/job:cancel plus the kind's existing job_access; cancellation additionally requires check:run, score:rubric:generate or score:run respectively, with live dependency access |

Responsible human means a live human session in the stored review domain, not necessarily
the job initiator or the person who last dismissed a finding. API tokens may receive
explicit existing nonhuman read/run/generate scopes, subject to membership intersection
and input access. They never receive **any human-only scope**, including check:decide,
score:rubric:review, evidence:confirm or export; preserve all other existing human-only
scope exclusions. Browser buttons, a supplied role or use of the CLI cannot bypass this.
Platform sessions have no org business access through these pages.
Existing assessment job cancellation is not restricted to the original initiator;
the UI must reflect the actual scope/dependency gate rather than inventing an owner check.

Every route checks org and same-task/same-parent relationships before returning rows,
counts, cursors, capability hints or original text. Missing resources and inaccessible
cross-org IDs return the same 404. Keep 403 for a known authorized object whose human
operation is forbidden, and 409 for a known state conflict. Bind all child IDs and
source refs through their actual parent graph rather than authorizing one UUID and
then trusting its request siblings. No org override query parameter.

Reuse existing events, written by the service/DB transaction, not the Vue page:

| Existing producer | Events and retained meaning |
| --- | --- |
| `check.submit_check`, `check.publish` flow, `check.decide_finding` | `check.submit`, `check.publish`, `check.dismiss`, `check.reopen`; immutable findings remain unchanged by a decision |
| `score_generation.submit_rubric` / publication | `score_rubric.submitted`, `score_rubric.completed`, sanitized `score_rubric.failed` |
| `score.review_audit` | `score_rubric.classified`, `score_rubric.decided`, `score_rubric.revised`; keep child/coverage/set target and the exact human event |
| `score_execution.submit_score` / publication | `score.submitted`, `score.completed`, sanitized `score.failed` |
| `jobs.cancel` and score API denial wrapper | `score_rubric.cancelled`, `score.cancelled`, `score_rubric.decision_denied` where currently emitted |

Check cancellation currently has no matching check-specific audit event in `jobs.cancel`;
this UI contract does not claim otherwise or introduce a general audit query. Do not
audit a preview/page load or create duplicate events for cached receipts. Failed
submission/review audit uses the existing sanitized denial path where available; a
rejected decision creates no success event. Event metadata contains authorized IDs,
actor kind, revision and hashes/codes; business decision records retain their reason.
Do not put raw reasons, quoted prices, source/response text, keys, cookies, authorization
headers or signed links into diagnostic logs or screenshots of real data.

## Empty, partial, error and accessible states

Use persistent inline state near the affected action; a toast alone cannot explain a
blocked workflow. Empty loading skeletons never display zero counts. Empty filtered
results say “当前筛选下没有记录” with reset; a genuinely empty history says “尚未运行检查”
or “尚未运行评分”. Missing source, zero model-assessable items, partial coverage and a
failed collection read each have distinct states. A failed page does not replace an
already loaded report with an empty-success table. Historical access can fail after
dependency permission changes; show “记录不存在或无权查看” without leaking another org.

| Failure | Required next step |
| --- | --- |
| `check_stale_draft`, `score_stale_draft`, `*_input_changed` | Clear preview consent, explain changed input, link to assembly/rubric review, then preview again |
| `score_rubric_unconfirmed`, `rubric_incomplete`, `unclassified` | Show exact outstanding checklist and responsible role; do not silently switch rubric |
| `revision_conflict`, `rubric_superseded`, `invalid_transition` | Refresh authoritative revision, retain unsaved reason/form, show comparison; require a new explicit action |
| Redaction/capability/price/balance/context blockers | Disable submission and name the person/action needed; do not lower cap, disable redaction or change mode automatically |
| `queue_unavailable`, transport timeout | Retain known job ID; recover reads before a deliberate retry, without duplicate paid writes |
| Citation mismatch, input integrity, missing material | Do not display an unchecked quote as verified; retain safe error and correction link |
| Partial job/invalid model output/unassessable score | Show usable saved rows and explicit missing work/stop reason; no zero filling or inferred overall total |
| Unknown error or malformed envelope | Safe Chinese error with code and explicit reload; no blanket success fallback |

Use semantic headings, table captions/column headers, labeled form controls and native
buttons. Every severity/state has text and an icon, not color alone. Provide skip-main,
visible focus, keyboard-operable filter/page/detail navigation and a focusable scrolling
region for wide tables. Dialogs trap focus, associate reasons/errors with the input,
focus the first invalid field and return focus to the invoking control. No single-key
shortcut performs a paid submit, confirm, dismiss or reopen.

Report filters and summary updates use restrained polite live regions; blocking errors
use alerts. Polling never steals focus. Only real bounded progress exposes aria-valuenow;
unknown progress has an accessible stage label without a fabricated value. Source
locations and text alternatives remain usable without images, at 200% zoom and on narrow
screens; side-by-side sources stack in reading order. Render only the current page and
one detail, with no mass image downloads. Include all-page counts and page position in
screen-reader announcements.

## Acceptance plan

Implementation acceptance must retain existing check/score gates and add the cases below. No real external Provider
is used; mocked browser tests prove UI behavior, while database/API tests prove isolation
and authorization. Neither substitutes for the other.

### Two-org API and database acceptance

Use orgs A/B with admin/bidder/technical/viewer, active/inactive memberships, scoped
tokens and worker identities. For **each** of the seven check tables, nine rubric tables
and four score tables listed above, verify NOT NULL org_id, ENABLE/FORCE RLS, missing-org
denial, A unable to SELECT/INSERT/UPDATE/DELETE B, org/task/parent composite-FK rejection,
and retained append-only/publication constraints as applicable. Extend the existing
[check storage](../../server/tests/test_check_storage.py),
[semantic storage](../../server/tests/test_check_semantic_storage.py) and
[score storage](../../server/tests/test_score_storage.py) scenarios, rather than inventing
console-owned persistence. Any later added table requires the same matrix.

Parameterize every existing/new route and each projection part with A→B parent,
child, draft, extraction, rubric, citation, cursor and same-org/wrong-task IDs. Include
inputs/job discovery/summary/filtered pages/notices/replacement/source windows/history,
preview/submit/decisions/job status/cancel. Assert 404 equivalence with missing IDs,
no B IDs/text/counts in responses, revoked material access invalidating the entire
authorized graph, no-context denial and platform-session rejection. Cursor changes in
principal/filter/revision must fail, never reuse a cached org result.

Prove zero writes and zero Provider calls for all three previews (including blockers),
new reads and context windows. Prove hash/date/model/redaction changes between preview
and submit require re-preview; confirmed draft/evidence and parent-source gates survive
all new read paths. Human tests cover token issuance rejecting every human-only scope,
token/worker/admin wrong-domain finding decisions, rubric classification separation,
commercial/technical decisions, bidder-only coverage/set confirmation, CAS races,
cross-domain full replacement, and immutable old reports. Unconfirmed evidence still
cannot enter draft/export; no advisory action confirms evidence or exports a bid.

Current coverage starting points include [test_check.py](../../server/tests/test_check.py),
[combined checking](../../server/tests/test_check_combined.py),
[rubric review](../../server/tests/test_score_review.py) and the score API/CLI suites.
Add integration assertions that empty/partial/oversized pages never imply completeness,
pagination occurs before child-text hydration, byte ceilings include multibyte Chinese
text, and 1,200 mixed rows remain navigable without fetching full show. Normalization,
unsupported formulas, excluded sections with unassessable items, cancellations and
publication fencing must preserve the current score-total behavior.

### CLI contract snapshots

Snapshot seven-key Result and registered `bid schema` output for every existing command
used above and every new read command/variant: preview allowed/blocked, accepted,
cached, complete/partial/stale, unavailable total, empty and multipage read, complete
replacement, wrong-role human decision and each exit 0/2/3/4/5. Keep old no-option show
snapshots unchanged. Verify Decimal-string charge/ranges versus numeric/null Cost.usd,
and explicit service error exit codes rather than treating all 409 as retryable.

### Playwright with a mocked API

Use `web/e2e/console-assessments.spec.js` using the static-built-app interception
pattern in [platform-credentials.spec.js](../../web/e2e/platform-credentials.spec.js):
`E2E_STATIC_DIR` serves built assets through page.route; synthetic org sessions and
stateful route handlers supply exact typed Result envelopes. Abort unexpected requests
and all external origins; no running API/PostgreSQL or model credentials are needed.
Use numeric `cost.usd:0`, matching the runtime schema (the reference spec's string zero
is not a new contract). Assert request method/path/body, revisions, hashes and mutation
counts as well as visible output.

1. Entry from a task/draft, missing extraction/draft, explicit current selection, no
   reports and filtered-empty states; viewer can read but cannot submit.
2. Rules preview writes zero times; submit once, cached receipt, polling and refresh
   discovery; combined blocker/redaction/balance/unknown estimate/cap consent paths.
   Editing scope/date/mode/cap resets consent; queue and network errors never auto-repeat.
3. Findings grouped by severity/domain with whole-report counts; PDF and Word context,
   draft revision text, no-risk coverage citations, unknown certificate date, missing
   evidence and card deep links. Assert source text is inert HTML and window continuation
   is visible. No-findings partial is never “全部通过”.
4. Commercial/technical responsible actions, admin/viewer denial, reason required,
   revision conflict, dismiss→history→reopen, stale read-only banner; new report has no
   inherited dismissal. Switching org while a source response is delayed renders no
   old-org text and clears preview/reason/forms.
5. Rubric preview/generate, legacy indeterminate progress, single-pass progress and
   optional two-stage fixed-section/batch progress, partial interruption and no fake
   percentages. Whole-set confirmation stays disabled until every server checklist
   item is satisfied; admin classification cannot confirm domain work.
6. Coverage mapped/duplicate/excluded/reopen; canonical conflict; section/item review
   by domain; full replacement across multiple pages, missing-page/conflicting-snapshot
   save disabled, oversize save blocked, reset approvals/new IDs and recovery of saved
   proposed mappings. Unsupported formulas can be confirmed as rules without implying
   automatic score aggregation.
7. Score preview with confirmed/unconfirmed or mismatched rubric; per-item scores,
   real zero versus null, loss reasons/actions and unassessable reasons. Include
   estimated/range_only/unavailable section/report fixtures and partial/stale reports.
   Assert subtotal/possible_range never appears as a total, including the excluded-section
   unassessable case and HTTP 200/ok=false.
8. Keyboard-only traversal, reason/error focus, dialog return focus, live-region polling
   without focus jumps, 200% zoom/narrow viewport and text severity labels; 1,200 entries
   with bounded requests, byte-limit failures and no full-report fetch/image preloading.

Save a repeatable artifact under `data/work/console-assessments-validation/<run-id>/`:
`result.json` with case assertions and counts, sanitized command/environment names,
screenshots of synthetic states and the exact synthetic fixture/seed version. Retain
request shapes/revision/hash assertions, not Authorization/Cookie/signed URLs/raw traces.
Use the existing [Playwright config](../../web/playwright.config.js); a representative
command is `E2E_BASE_URL=http://console.test E2E_STATIC_DIR=dist
E2E_OUTPUT=../data/work/console-assessments-validation/browser npx playwright test
e2e/console-assessments.spec.js` from `web/`, after a normal local build. Keep artifacts
outside `docs/`. Browser acceptance cannot be claimed until those pages are implemented.

### Contract verification

Verify the companion module with the installed local tools, without a service or
dependency installation:

```sh
.venv/bin/ruff check --no-cache docs/plan/console-assessments/console_assessments_contracts.py
.venv/bin/ruff format --check --no-cache docs/plan/console-assessments/console_assessments_contracts.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pyright --project pyproject.toml --pythonpath .venv/bin/python docs/plan/console-assessments/console_assessments_contracts.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=server:cli .venv/bin/python -c 'import importlib.util; p="docs/plan/console-assessments/console_assessments_contracts.py"; s=importlib.util.spec_from_file_location("console_assessments_contracts", p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)'
```

These checks establish a loadable, typed contract only. They do not establish
runtime authorization, RLS, query performance or UI delivery.

## 已定决定

| Decision | Approved choice | Reason |
| --- | --- | --- |
| First delivery order | Rules check end to end, then combined, rubric review, score report | Gives ordinary staff a usable path without waiting on a model; preserves prerequisite order. |
| New persistence | None | Existing jobs/reports/decisions contain the business state; duplicated console state would drift. |
| Responsible human | Existing stored review domain/role, not a named assignee | Matches enforceable gates and avoids introducing unimplemented task membership. |
| Bounded reads | Opt-in console variants plus only input/citation/job discovery reads | Keeps existing CLI JSON compatible and avoids full reports on page mount. |
| Page limits and pathological single rows | 50 default/100 maximum, 2 MiB encoded Result; fail explicitly on an oversized single row | Provides measurable limits without truncating verified text. If representative data exceeds it, amend to compact row metadata and separately paged narratives before large-data release. |
| Full replacement editing | Complete same-snapshot form, 512 KiB serialized save limit, recover stored proposal separately | Matches current atomic replacement and CLI limit; visible page edits cannot erase other-domain content. |
| Budget compatibility | Enforced task budget, runtime TaskBudgetView and Result 4.0 Cost; display real budget_preflight and admission blockers | The task-budget work is merged into the implementation base; no legacy unenforced projection remains. |
| Progress transport | Existing polling, optional observed StageProgress; no SSE prerequisite | Works with current single-pass and later fixed-section two-stage workers without invented percentages. |
| Unknown/unavailable scores | Preserve backend null/state/reason; no frontend totals | Partial or unsupported calculations cannot become a misleading total. |
| Human batches and automatic repair | Individual review actions only; explicit whole-set confirmation after server checks | Makes responsibility and reason visible and avoids a second bulk-decision contract. |
| UI acceptance | Static built Vue with stateful mocked API plus separate two-org API/DB gate suite | Repeatable browser behavior without paid services; mocks do not establish RLS or authorization. |

## Implementation acceptance note

The four slices are implemented in the approved order without a migration or Provider
algorithm change. The companion module re-exports the runtime schemas so the approved
transport contract has one definition. Enforced budgets use the runtime task-budget
view and Result 4.0 rather than the earlier recorded-budget draft.

The built-app Playwright suite uses stateful mocked API responses, real versioned
request paths and synthetic fixtures. Browser startup was blocked by the execution
sandbox before any page interaction; no browser acceptance pass is claimed. Database
isolation and route tests are supplied for the integration environment because this
worktree's sandbox cannot reach PostgreSQL. Executable tests and generated artifacts
are linked from the [mechanism note](../notes/console-assessments.md#code); artifacts
remain under `data/work/`, outside the documentation tree.
