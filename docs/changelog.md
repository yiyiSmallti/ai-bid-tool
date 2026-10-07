---
kind: changelog
---

# Changelog

Delivered scope by date. See [mechanism notes](README.md#mechanism-notes) for each scope's mechanism.

## 2026-10-06: Confidential field management slice

- Extended the org confidential page with bounded definitions/current values,
  archive/unarchive, masked history, blank value entry and explicit audited reveal.
  Task links carry a nonsecret task ID; blur, hide, navigation and session changes
  clear revealed text. Value entry is cleared after submission and on conflict.
- Added v4 query/history and checked-value routes with Result 4.0 CLI commands.
  Compare field revision and the exact owner's current value ID under the existing
  field lock; legacy append clients retain their behavior. Exact `field_id` query
  filtering prevents editor hydration from missing a field beyond a prefix page.
- Added migration `0055` for generated key/label prefix search, field/current/history
  indexes and fixed-field metadata guards. Read projections never load ciphertext.
  Existing token scopes, org isolation and encrypted append-only values are retained.
  Field-create audits now contain revisions and fixed kind/scope codes, not keys.
- Added DB-backed role/isolation/CAS/non-disclosure and fixed-scale tests, CLI
  snapshots and mocked Playwright scenarios. PostgreSQL, real-browser and measured
  scale acceptance remain pending; DB-free checks do not establish those gates.
  Scope and mechanisms are in the [contract](plan/management-pages.md#confidential-management-slice)
  and [confidential note](notes/confidential-values.md#bounded-management-and-checked-writes).

## 2026-10-06: Template and binding management slice

- Added template browse/search, exact revision detail, content/lifecycle histories
  and bounded binding inspection to the v4 API, CLI and org console. Legacy list
  outputs and template revision-conflict exit code 4 remain unchanged.
- Added the template lifecycle event arm, tenant composite keys, page-only indexed
  audit author lookup and task selection guards in migration `0054`. Existing pins
  remain unchanged; inactive roots reject new/replacement selections.
- Reject retired template-pin reactivation even after root restoration, after
  declarative database constraints. Align scope regression expectations with all
  four human original-file readers, compare binding timestamps as aware instants,
  and require composite-FK rejection before binding actor/hash checks.
- Added DOCX-required upload/revision forms and authenticated original inspection
  under human-only `template:file:read`, enforced in token issuance and storage.
  Binding creation uses six fixed sections, explicit preview, static-content review
  and exact template/static hashes through the existing export service.
- Remove the duplicate accessible label on the static-content review checkbox.
  Review stays disabled during original download and integrity validation; browser
  scenarios wait for that gate and verify corrupt originals cannot enable review.
- Added DB-backed API/service, role/isolation, fixed-scale, CLI snapshot and mocked
  browser acceptance scenarios. See the
  [template scope](plan/management-pages.md#template-vertical-slice) and
  [mechanism](notes/management-pages.md).

## 2026-10-06: Production deployment plan

- Add the [production deployment plan](plan/production-deployment.md) covering
  environment decisions, repository deployment prerequisites, recovery and release
  acceptance; link it from roadmap F10. The owner approved the plan; environment
  decisions remain open and no deployment has been executed.

## 2026-10-06: Annotation regression fixes

- Pinned `team_cosign_snapshot` to the UTC TimeZone. It hashes whole requirement,
  chunk and evidence rows with `to_jsonb`, which renders timestamps in the session
  TimeZone, so connections with different settings invalidated open review rounds.
- Normalized approval timestamps to UTC before hashing, retaining exact approval
  comparisons across confirmation and worker transactions with different database
  time zones. See [approval binding](notes/annotation.md#how-it-works).
- Aligned role-preservation expectations with the approved human-only annotation
  grant. Token and privacy-release fixtures now reach the intended authority gate
  with valid inputs and distinct assets; existing token CHECKs remain cumulative.
- Kept automatically classified response cards in their assigned domain during
  export acceptance, submitting through a contributor and confirming through the
  corresponding reviewer.
- Corrected the annotation browser fixture's running-job Result envelope and the
  stale-release button selector, retaining cancellation and no-preview assertions.

## 2026-10-06: Cloud certificate-page annotation slice

- Added the approved B05 runtime contract and certificate-page API/CLI path:
  explicit source/geometry preview, reviewed hash admission, immutable server-rendered
  candidates, bounded history and signed preview downloads. Vendor renditions and
  arbitrary profile/contract attachments remain deferred.
- Extended the trusted screenshot-renderer binary with annotation description and
  candidate/release profiles. Read-only `--annotation-describe` predicts the complete
  canvas without producing pixels; confirmed releases preserve the reviewed content
  and canonical Evidence hash while binding the exact current human approval.
- Added source-bound storage and migration guards, worker publication/cancellation,
  task events and board recovery actions, current B02/co-sign approval binding, and
  draft/export release dependencies. Workers never attach material or confirm Evidence.
- Added a fail-closed Linux renderer launcher with child resource limits and
  architecture-checked seccomp network/process/write restrictions. The Docker
  build packages the same Rust binary root-owned and executable, and the worker
  definition caps CPU. Unsupported host configurations reject rendering explicitly.
- Added the console's certificate source picker, numeric crop/box controls, explicit
  preview and submission, actual-candidate inspection, manual `ImageEvidenceInput`
  staging in the response editor, and separate requirement/co-sign/release states.
- Added compatible `--id` aliases to job status/wait/cancel while retaining positional
  UUIDs; ambiguous or missing IDs fail before requests. Added CLI transport snapshots
  and mocked Playwright acceptance with bounded reads,
  conflicts, read-only roles, failed/cancelled jobs and org-reset cases. Added durable
  pre-upload staging and age-gated orphan reconciliation without requeuing cancelled
  rendering. The repeatable cases and remaining acceptance are defined in the
  [annotation plan](plan/annotation.md#failure-modes-and-test-plan).
  See the [mechanism note](notes/annotation.md).

## 2026-10-06: Certificate and org-profile management slice

- Keep qualification list tables mounted across page loads so clearing their data
  releases the previous page; table-column teardown must not retain old pages in
  rearmed layout callbacks.
- Enforce the raw 16 KiB HTTP body limit on certificate/profile management POSTs
  and reject profile historical-pin reactivation after database constraints.
  Align acceptance setup with authorized pin creation, the established member
  removal route and the approved human-only lifecycle role grants; keep source
  constraint and removed-member access assertions intact.
- Extended profile and certificate management with bounded prefix browse/search,
  exact revision details, separate content/lifecycle history, complete declaration
  editing and explicit authorized task pins. Certificate originals remain bound to
  their upload revision, with ordered parts/rotation, signed same-org inspection,
  explicit date advisories and a metadata-only no-original state.
- Added certificate/profile lifecycle arms, exactly-one-root and org composite
  constraints, indexed page-revision authors, task selection guards and human-only
  bidder/admin lifecycle scopes in migration `0052`. RLS and composite keys reject
  forged relationships before business guards; existing pins and source archives
  remain intact. See [management mechanisms](notes/management-pages.md).
- Added v4 CLI browse/show/history/lifecycle commands and schema snapshots without
  changing legacy command output. Added API/service, storage, role/token, isolation,
  fixed-scale and mocked Playwright acceptance coverage with artifacts under
  `data/work`. Database, Chromium and measured scale acceptance remain for the main
  integration environment, as tracked in the [contract](plan/management-pages.md).

## 2026-10-06: Drafting identity, enum serialization and sandbox queue recovery

- Bind no-card drafting inputs directly to the pinned source chunk/block hash,
  supplementing the existing indirect requirement-preparation binding. Preserve
  existing card cache identities and carry the new hash through model-generated
  cards so unchanged resubmissions still reuse their result. The input manifest
  changes invalidate old no-card keys without a prompt or schema version bump.
- Preserve validated category enums in response-card string trimming, preventing
  draft result serialization warnings for technical and scoring response rows.
- Reconcile abandoned sandbox queue deliveries at worker startup and on the
  existing recovery schedule. Check application jobs under their tenant context,
  retain live workers and nonterminal jobs, and finish terminal or missing jobs'
  deliveries through Procrastinate's terminal-state function. Retain application
  data and queue history, and log reconciliation counts.
- Add source identity and warning regressions, plus PostgreSQL scenarios for
  terminal/missing jobs, live work, tenant isolation, renewed heartbeats and
  concurrent/idempotent recovery. PostgreSQL execution remains an integration
  check; no migration is required.

## 2026-10-06: Bounded product and feature revision authors

- Resolve product and feature authors with explicit page-revision filters on a
  stored audit revision ID, with separate action indexes on org, revision ID and
  resource root in migration `0050`. Replace the product expression index there
  without changing merged migrations `0048` or `0049`.
- Preserve exact root/content revision association, ambiguous-author handling,
  one query per page and the existing fixed-scale acceptance bounds. Add the
  feature suite's history-row visit bound to the product suite, including rows
  discarded by filters and index rechecks. See
  [author lookup](notes/management-pages.md#how-it-works).

## 2026-10-06: Token scope and feature acceptance fixes

- Added database rejection of `token:create` in migration `0051`, preserving the
  existing human-only scope checks. This closes a gap that predates the feature
  library slice; the API already rejected that scope for tokens.
- Corrected feature storage acceptance to distinguish forbidden historical-pin
  reactivation from an inactive resource's new-pin rejection, without changing
  either production guard. See [selection guards](notes/management-pages.md#how-it-works).
- Bound fixed-scale fixture suffixes as data so SQLAlchemy does not interpret
  quoted `:parent` text as a missing parameter. Scale bounds and assertions remain
  unchanged.

## 2026-10-06: Feature library management slice

- Added feature prefix browse/search, same-org product and implementation-state
  filters, exact revision detail and separate content/lifecycle histories through
  additive v4 API and CLI commands. Legacy all-row outputs remain unchanged.
- Extended lifecycle storage with the feature arm, exactly-one-root constraints,
  org composite revision bindings, human lifecycle CAS and database guards in
  migration `0050`. RLS and composite keys precede business authority rejection.
- Added feature creation/revision, bounded product choices and explicit task pins.
  Inactive parent products block new associations and selections; existing pins
  and exact active-pin replays are preserved. Declarations do not confirm material
  or replace screenshots.
- Added API/service, role/isolation, storage, CLI snapshot, fixed-scale and mocked
  browser scenarios. PostgreSQL, Chromium and measured scale acceptance require
  the main integration environment; see the [contract](plan/management-pages.md)
  and [mechanism](notes/management-pages.md).

## 2026-10-06: Product library guard and browser regressions

- Restore RLS-first rejection of foreign product writes and composite-FK-first
  rejection of forged task/product revision pointers in migration `0049`.
  Valid parent relationships still require task authority and active product
  lifecycle before the statement completes; existing isolation assertions remain
  unchanged. See [guard ordering](notes/management-pages.md#how-it-works).
- Bind the synthetic simulation provenance job to an uploaded document and its
  real task, preserving the existing job document constraint.
- Keep product search in a loading state throughout debounce and pending reads;
  show an empty state only after a successful empty response. Synchronize the
  membership-downgrade browser scenario with the open selection dialog before
  revoking its task role.

## 2026-10-06: Product library management slice

- Added bounded product browse/search, exact revision detail and content/lifecycle
  histories through additive v4 API and CLI commands. Existing all-row lists retain
  their output and are not preloaded by the new console pages.
- Added human product deactivate/restore with independent lifecycle CAS, immutable
  tenant-scoped events and database selection guards in migration `0048`. Existing
  roots begin active without fabricated lifecycle events; no delete action exists.
- Added product creation/revision forms and explicit task revision selection under
  task role and archival checks. Library revisions preserve task pins; inactive
  products allow only exact existing active-pin replays.
- Added API/PostgreSQL, role/isolation, CLI, fixed-scale and mocked-browser acceptance
  scenarios. PostgreSQL, Chromium and measured scale acceptance remain integration
  checks; see the [plan](plan/management-pages.md#test-plan-and-repeatable-artifacts)
  and [mechanism](notes/management-pages.md).

## 2026-10-06: Bounded requirement-review work on board reads

- Project fail-closed review states for the default board without deriving unused
  pins for unconfirmed requirements. Revalidate confirmed inputs fully; explicit
  requirement-review views and consumer manifests retain complete current hashes.
- Reuse the citation batch's exact source spans during review evaluation and
  preserve canonical numeric semantics while accelerating ordinary JSON encoding.
  See [Requirement confirmation](notes/requirement-confirmation.md#how-it-works).

## 2026-10-06: Score request evidence scope

- Correct the dev real-model score preview blocker observed after rubric completion:
  a confirmed rubric with 86 items against a DraftRun containing 16 confirmed response
  rows (1108 bid-side characters) and 1530 text-free gaps returned
  `admission_blocker=score_context_limit`, `cost_basis_reason=context_limit` before any
  call. Every item inherited all gap context and reused extraction's 8000-character budget.
- Bind gap/comply-only context refs to each score item's own Requirement while
  preserving every confirmed response as candidate bid-side support. Batch splitting
  and acceptance retain the exact selected ref union; tender/rule refs and verbatim
  citation verification remain unchanged. See the [score input contract](plan/score.md#scoring-input).
- Add the independent `BID_SCORE_BATCH_CHARS` setting, default `64000`, with the same
  grouping used for preview costs and execution. Complete items that still cannot fit
  retain the explicit preview blocker. Pin the budget and advance scoring request
  identities so changed inputs require a fresh preview; see
  [configuration steps](guides/development.md#configure-score-requests).
- Add synthetic Provider/request-scope regressions and a database-backed preview/run
  scenario matching the dev draft shape, with fake Provider transport and unchanged
  citation acceptance checks.

## 2026-10-06: Rubric revision regression fixture corrections

- Deep-copy the read report in the shared rubric replacement test helper before constructing
  requests. Nested range edits previously mutated the expected baseline before the HTTP request,
  falsely reporting candidate changes after rejection. Retain full report equality checks.
- Set an explicit initial assessment date before previewing in the console date-invalidation
  regression, then edit to a distinct date so the test is independent of the current day.

## 2026-10-06: Rubric revision across domains with normalization errors

- Fix the dev real-model rubric v2 deadlock observed after #21: generation retained 8 sections,
  86 items and 8 normalization-error kinds, but after admin classification neither domain could
  submit a full replacement. Commercial weights such as `35` blocked the technical reviewer;
  technical model-assessable items without bounds blocked the bidder. Candidate storage from #16
  tolerated these declarations while revision input still applied confirmed-rule validation.
- Use candidate decimal/range semantics in complete replacements, allowing both unchanged entries
  and partial repairs to retain errors. Compare protected content against stored declarations,
  recompute errors for each version, and retain strict human confirmation and source-selection
  gates. The [scoring contract](plan/score.md#rubric-versions-coverage-and-human-confirmation-人工确认)
  defines incremental repair and renewed classification/decisions.
- Add local normalization codes to section/item reads and console review/replacement entries, so
  reviewers can locate affected rules. Aggregate errors remain in completeness and blockers.
- Add DB-free schema regressions, database-backed two-domain repair/rejection scenarios and mocked
  Playwright replacement flows. The [mechanism note](notes/score.md#pitfalls) describes the failure
  mode and retained gates.

## 2026-10-06: Requirement review batch reads and savepoint audit binding

- Refresh requirement/review and distinct chunk/document dependencies in two
  bounded queries, retaining live ORM refresh, citation checks and the existing
  draft-read statement budget.
- Bind source-invalidation audits to the current backend's actual writer
  transaction, including savepoint subtransactions. Preserve immutable-event,
  org, action and metadata checks, and reject committed-history replay.
- Keep source-changing citation fixtures behind real human reconfirmation,
  distinguish human repair from system invalidation audits, and compare rubric
  CLI/report output under the same contract version. See
  [Requirement confirmation](notes/requirement-confirmation.md).

## 2026-10-06: Requirement-review publication and console regression fixes

- Preserve tenant event context during privileged source maintenance without
  granting runtime roles permission to infer another org. Keep human requirement
  decisions under the live org-role, task-role and scope checks.
- Bind selected response generation to the complete extraction preparation scope
  at submission, admission and publication; send only selected requirement content
  to the provider. Return explicit zero counts for empty response buckets.
- Give requirement-review and manual-entry fields stable accessible names, restore
  rejected batch selections to their accepted state, and retain the 100-item limit.
  See [Requirement confirmation](notes/requirement-confirmation.md).
- Update accepted-source fixtures through the real review service and current
  publication manifest. Preserve source verification, deferred gates and exact
  citation-location count assertions.

## 2026-10-05: Requirement confirmation and verified manual entry

- Added independent requirement review, source pins, legacy-unconfirmed migration,
  immutable encrypted decisions, atomic owner batches and replay receipts with
  human-only task authority and FORCE RLS.
- Added verified manual recovery for omitted and rejected requirements, explicit
  manual extraction sets and preserved original model receipts, costs and lineage.
- Bound response acceptance, draft gaps, rubric confirmation, scoring and artifact
  freshness to requirement review while retaining provisional preparation and
  existing evidence/domain/co-sign gates.
- Added API/CLI Result 4.0 commands, explicit requirement-review board/progress,
  next-human responsibility, durable metadata events and the console review/manual
  workspace. See [Requirement confirmation](notes/requirement-confirmation.md).

## 2026-10-05: Rubric section-source regression corrections

- Authorize citation-window tasks before validating query fields or section source selectors.
  Nonmembers and cross-org callers retain 404 responses even for malformed queries; authorized
  callers retain normal input validation. See [task authority](plan/team-workflow.md#membership-ownership-and-authorization).
- Keep section binding checks separate from expensive citation location during score publication.
  The existing shared draft/rubric batch verifies each distinct Source pair once; insertion still
  verifies every new section quotation strictly. See [scoring pitfalls](notes/score.md#pitfalls).
- Correct the historical-report fixture to supply the required card-reopen reason, and align
  source-drift regression checks with the existing immutable-input 404 behavior for both anchor
  and additional section sources, as specified in [scoring citations](plan/score.md#citations-outbound-calls-and-confidentiality).

## 2026-10-05: Multiple verified sources per rubric section

- Apply owner-approved option A after the dev real-model rubric run reached `invalid_section_citation`
  following #20: the `price` section separately cited the benchmark-price rule and price formula,
  but the single-Requirement section gate rejected them as `cross_requirement_citation`. Sections
  now preserve and independently verify every citation across pinned scoring Requirements; items
  retain their single-Requirement contract, and joined or unverifiable citations still fail.
- Add the [section-source migration](../server/migrations/versions/0046_score_section_sources.py)
  for ordered section sources, retaining legacy single-source reads, existing
  FORCE RLS and immutable history, with database and application checks over every binding. Complete
  human replacement may select/remove only verified pinned citations and cannot rewrite Source.
- Update bounded console/assessment reads, indexed source context, rubric review/replacement UI,
  CLI discovery and intentional snapshots. Advance rubric wire/prompt/cache identities so old queued
  jobs require a fresh preview. See the [scoring contract](plan/score.md) and [mechanism](notes/score.md).
- Add synthetic two-stage Provider, persisted review/confirmation, tenant-isolation and mocked browser
  regressions for multiple sources, invalid second citations, joined text and source removal.

## 2026-10-05: Team workflow co-sign review and consumption gates

- Implemented requirement co-sign policy and the starred-task rule, immutable
  input-bound response/disposition rounds, distinct professional domain signatures,
  replay receipts and permanent retirement after input or actual signer-authority
  changes. Completed the approved [ADR 0005 amendment](adr/0005-human-confirmed-responses.md).
- Added FORCE RLS review history and complete-round database/service gates. Only
  the final authorized human request confirms Evidence and appends a confirmed
  response; tokens, workers and agents cannot sign. Legacy single-domain records
  retain their real human decisions without fabricated signatures.
- Bound draft/export/check/score consumption and freshness to policy, round and
  signatures. Incomplete approvals remain gaps; existing Evidence confirmation,
  negative-deviation and prototype/final-export gates remain enforced.
- Added co-sign board projections, Chinese console controls, API/CLI commands,
  schema discovery and intended Result snapshots. See
  [Team workflow](notes/team-workflow.md) for the mechanism and recovery boundaries.
- Preserve `stale_material` as the sole input reason when screenshot withdrawal
  retires a single-domain response review. Use `needs_reconfirmation` when only
  that approval is lost, and reserve `cosign_required` for multi-domain policies.
- Align database response-item reasons, new and stale draft projections, export
  readiness and board actions without changing approval or Evidence gates. Keep
  the screenshot regression's exact reason assertion and add cross-consumer
  reason checks; see [Team workflow](notes/team-workflow.md).
- Reuse a review round's immutable, already-validated citation inputs during
  approval checks, retaining the check/score shared citation batch and strict
  changed-source gates without repeated per-card location work.
- Preserve specific legacy confirmation input errors and the completed
  single-domain memory-after-review behavior. Pending and multi-domain review,
  material, citation and signer invalidation remain strict.
- Use independent professional reviewers in generated-card, simulation and agent
  acceptance flows; require real renewed review after changing a pinned
  requirement. Align stale-export acceptance with the existing blocked-preflight
  response. See [Team workflow](notes/team-workflow.md).
- Derive completed round summaries from the valid required-domain signature set,
  including the final signing transaction's recheck before the card decision is
  applied. Keep the independent consumption predicate and strict partial-round
  invariant; failed final checks roll back the last signature and confirmation.
- Allow either authorized required-domain human to finalize the exact complete
  round through the legacy task-content trigger, including Evidence and independent
  disposition decisions. Retain primary-domain authorization for reject,
  needs-material and reopen; see [Team workflow](notes/team-workflow.md#how-it-works).
- Granted only the review-round column update privilege needed for PostgreSQL
  row locks; immutable-history triggers still reject updates. Unit deactivation
  uses an authorization epoch without granting platform functions business-table
  access, and task rule changes advance the existing access epoch.
- Preserved the [new single-domain review contract](plan/team-workflow.md#co-sign-policy-rounds-and-consumption-gates):
  successful fixture preparation now submits and confirms through the real service,
  while direct SQL denial cases retain their triggers. Independent professional
  fixture reviewers keep unrelated actor-role changes from retiring their reviews.
- Gave required co-sign reason fields explicit accessible names. Browser scenarios
  wait for all material controls before reviewing them, and their API fixture
  includes the existing read-only extraction reasoning preview.

## 2026-10-05: Model citations within the sent tender-source segment

- Verify rubric citations against the redacted tender-source segment retained for each ref,
  excluding requirement summaries and position labels from the sent-side match. Preserve original
  and pinned-Source checks, and share the segment matcher with check and score semantic acceptance.
  Summary/position-only quotations and repetitions within the source retain their rejection codes.
- The dev run's remaining `invalid_overall_citation` came from the requirement summary repeating
  the full Source quote in the assembled prompt ref, not from page-level repetition. The pinned-span
  fix in #18 remains correct for repeated text elsewhere on a PDF page or Word block. See
  [scoring pitfalls](notes/score.md#pitfalls) and [checking pitfalls](notes/check.md#pitfalls).
- Add synthetic Chinese source-boundary tests and fake-Provider rubric/check/score regressions,
  including two-stage overall citations and preserved Word locations and original spans.

## 2026-10-05: Model citations within pinned Source spans

- Resolve rubric, check and score model citations inside the verified Requirement Source span,
  preserving extraction's boundary preference when locating the full Source. Repeated text elsewhere
  on the same PDF page or Word block no longer rejects an otherwise unique citation; missing or
  ambiguous Sources and citations outside or ambiguous within the Source retain their rejection
  reasons. See [scoring pitfalls](notes/score.md#pitfalls) and [checking pitfalls](notes/check.md#pitfalls).

## 2026-10-05: Reviewable rubric normalization failures

- Separate rubric Provider wire shape from normalized confirmation rules. Preserve model-declared
  caps, weights, bounds, aggregation and rule text on candidates, and record semantic inconsistencies
  with the existing normalization errors instead of discarding stage 1. Fixed section context reaches
  item generation unchanged; malformed fields and invalid citations retain their rejection rules.
- Add forward migration `0044` for candidate numeric declarations while retaining strict subject/set
  confirmation gates, including direct SQL checks independent of stored error metadata. Existing
  console blockers and complete human replacement revisions expose and resolve the errors.
- Add `provider_output_truncated` for output-token exhaustion across shared Providers, preserve partial
  result/accounting behavior, and give rubric jobs actionable reasoning/output-limit guidance.
  Advance the rubric wire schema identity and CLI schema discovery for candidate values. See the
  [score contract](plan/score.md#first-version-aggregation-algorithms) and [pitfalls](notes/score.md#pitfalls).

## 2026-10-05: Task stream snapshot recovery

- Require a successful authorized snapshot and its cursor before opening or
  resuming console task streams. Failed discussion parent validation retries the
  snapshot without opening a cursorless stream; org reset discards late snapshot
  results and clears task content and unsaved discussion drafts. See the
  [snapshot and reconnect contract](plan/team-workflow.md#durable-task-events-and-sse).

## 2026-10-05: Team workflow assignment and discussion

- Added requirement/extraction assignment with explicit revision and reason, active
  owner/contributor targets and membership-change guards; assignment remains
  metadata and never grants review authority or changes response-card content.
- Added encrypted append-only card threads, replies and validated in-task mentions
  for human task readers, including viewer observers. Request UUID replay returns
  the original receipt; changed content conflicts. Archived tasks reject writes,
  and comments preserve card revisions, evidence and existing human gates.
- Added Chinese Element Plus assignment/discussion controls, shared authenticated
  API/local/remote CLI paths, typed discovery and intentional zero-cost Result
  snapshots. Audit and durable events contain metadata only; co-sign remains the
  approved next slice. See [Team workflow](notes/team-workflow.md).

## 2026-10-05: Queued agent authority-loss cleanup

- Persist a rejected controller Job as failed before clearing its transaction
  execution binding and saving the authority pause. A queued Job has no admitted
  run; cleanup uses the existing no-execution fence without weakening the
  [agent recovery guards](plan/agent.md#worker-recovery-and-cancellation).

## 2026-10-05: Agent and task authority integration

- Apply the [agent task boundary](plan/agent.md#identity-permissions-and-human-gates)
  to session admission, continuation, tools, model calls and child publication.
  Preserve authority-loss cleanup and the current step's execution identity while
  refreshing live task grants. Retain team event production and batched citations.
- Combine the team workflow and agent scopes, routes, job access and worker
  fences. Keep the migration chain at team workflow `0041` then builtin agent
  `0042`; distinguish historical ownership from current task admission in guards.

## 2026-10-05: Controller identity after child-result reads

- Recheck and restore the live controller identity after the command broker reads a child result and after the final draft validity read, before writing steps, completion messages or checkpoints. Keep the existing execution fence and immutable creator fields required by the [agent recovery contract](plan/agent.md#worker-recovery-and-cancellation).
- Record a failed collection step and its audit event when an integrity error interrupts collection of a terminal child. Preserve already published results and the contract's partial-session outcome with an explicit stop reason; controller checkpoint success does not imply goal completion.

## 2026-10-05: Atomic agent checkpoints and pause references

- Read controller costs before the terminal transition, then persist the complete session checkpoint and cleared execution binding in one UPDATE. Preserve the live step/message fence and [terminal-state immutability](plan/agent.md#worker-recovery-and-cancellation).
- Bind absent pause budget references as SQL NULL instead of JSON `null`, retaining the existing budget-only reference CHECK. Strengthen checkpoint receipts and pause-persistence coverage without changing hard-limit termination or unknown-request recovery semantics.

## 2026-10-05: Agent integration regression fixes

- Restore private candidate rendering for a verified human export submission while preserving worker identity, current attempt/lease checks and the separate [human release gate](plan/export.md#human-identity-and-release-gate). Automated actors still cannot invoke human-only export actions.
- Restore verified actor context in the separate transactions that record rubric and score denials, so [invocation provenance](plan/agent.md#audit-and-a02-provenance) is validated without replacing the original authorization error. Human-only scope errors identify the required human session.
- Batch draft-history provenance reads without increasing the query bound. Align regression expectations with the approved provenance and legacy-output contracts, and use known synthetic provider prices and zero remaining allowance in agent budget scenarios rather than erasing prior task expenditure.

## 2026-10-05: Team workflow slice 1

- Batched board, response-card and scoring citation validation by bound source,
  sharing exact/normalized searches while preserving verbatim, ambiguity and
  Unicode-unit checks. Added a 300-page, 5,000-requirement board bound and frozen
  predicate equivalence coverage; see [bulk citations](notes/docx-citations.md#how-it-works).
- Corrected offline event production to derive missing org context only for the
  effective privileged database role, preserving strict runtime/direct-append
  checks. Fixed disabled-owner recovery autoflush and released task locks during
  local source rendering, with live authorization rechecked at publication.
- Updated existing SQL/API fixtures to establish explicit task members through
  the real workflow services, retained permission and tenant-isolation assertions,
  and corrected Element Plus reason/checkbox selectors in the browser scenarios.
- Added task owners, explicit task members and bounded review domains, intersected
  with current org roles and token scopes. Human org administrators retain the
  approved read/member-recovery exception; existing task, document, card, resource,
  job, check, score, export and signed-download services apply the task boundary.
- Added revision-checked handover, archive and explicit unarchive. Busy tasks
  cannot archive; active-state checks fence submission, worker admission and
  publication while preserving settlement of dispatched vendor calls.
- Migration `0041` adds FORCE RLS workflow/member/event tables and reviewed
  owner/member import, preflight and cutover administration. Unresolved legacy
  tasks block cutover; no historical activity grants implicit task access.
- Added bounded task-board/progress/activity reads, encrypted continuation and
  event cursors, same-transaction metadata events, SSE reconnection and polling.
  Added Chinese Element Plus board/member pages and slice 1 CLI/schema commands.
- Approved all team-workflow defaults and the ADR 0005 co-sign amendment. Assignment,
  discussion and co-sign execution remain later slices. Added API/PostgreSQL,
  CLI and mocked-API browser acceptance; see [Team workflow](notes/team-workflow.md).

## 2026-10-05: Check and score console assessments

- Integrated task membership, archival and task responsibility domains into assessment discovery, console projections and shared review mutations. Capability hints use the same task policy; authorized denials retain their audited error semantics.

- Implemented the approved rules-check, combined-check, rubric-review and score-report console flow with Element Plus, Chinese labels, explicit preview/consent, durable job recovery and links to the responsible response-card review.
- Added bounded assessment input, job and verified-citation reads plus opt-in check/rubric/score projections. SQL keysets precede narrative hydration; principal/snapshot-bound cursors, complete-row byte limits and two-org API tests preserve existing authorization. No migration or Provider algorithm change was introduced.
- Added full-snapshot rubric replacement, separate classification/coverage/domain/set decisions and compact transactional receipts. Preserved null, partial, stale and unavailable-score semantics without browser-side totals.
- Connected enforced task-budget preflight and Result 4.0 cost fields, added matching CLI discovery/console variants and intentional snapshots, and supplied stateful mocked built-app Playwright acceptance. See [console assessment mechanisms](notes/console-assessments.md) and [acceptance limits](plan/console-assessments.md#implementation-acceptance-note).

## 2026-10-05: Export render exit race

- The Linux render memory probe treats a renderer that exits while `/proc/<pid>/statm` is being read (ESRCH) as exited instead of failing the render job.

## 2026-10-05: English developer documentation

- Unified developer and agent documentation in English using the [glossary](glossary.md), renamed the [design document](design.md), and conservatively corrected stale statuses and superseded decisions against implementation and later decisions. The [roadmap](plan/roadmap.md) remains the home for remaining scope and open decisions.

## 2026-10-05: Rubric review compatibility and budget preflight

- Generate stage-2 item keys from their fixed tender refs rather than numeric local UUIDs. UUID-derived keys matched the sensitive-number redaction rules, discarded valid items, and left downstream coverage and replacement requests empty. The item prompt version advances; queued jobs using the previous prompt must be resubmitted. Human review request contracts and redaction checks are unchanged.
- Adapt the budget-admission test seam to `BudgetCallQuote`. Rubric cost preflight carries one structure quote and a conservative quote for every item batch, including direct-provider task liability. Cached previews use the same actor-bound cache key as submission despite the published structure changing the final input hash.
- Add DB-free acceptance from the shared two-stage fake provider through citation validation to existing human coverage and revision request construction, plus platform/direct-provider budget-preflight and accounting coverage. Mechanism: [Human-reviewed rubrics](notes/score.md).

## 2026-10-05: Two-stage score rubric generation

- Stage 1 sends the full fixed scoring table once and proposes only section structure and the overall rule. It sees every scoring Requirement; each proposed section and the overall rule must have verified citations. The entire serialized HTTP body is checked against `BID_RUBRIC_MAX_REQUEST_BYTES`; exceeding it blocks preview/submission before any calls and never triggers splitting.
- `RubricProvider.extract_structure` and `RubricProvider.extract_items` expose the two calls. Service validation checks section keys/titles/order, aggregation, bounds, caps, weights, inclusion and review domains before stage 2. Item details use bounded Requirement batches using `llm_batch_chars`; Provider owns batching and concurrency. Every batch receives the entire fixed section list. Unknown/cross-batch references remain unresolved. Failed batches can retain valid sections even when no items were accepted; partial results exit 5.
- Preview remains zero-write and zero-call, with an upper bound for both stages and the complete section list repeated in each stage-2 batch. Prompt/schema versions enter preview cache/input identity; incompatible queued work fails explicitly and must be resubmitted. The published rubric `input_hash` derives from the manifest containing stage-1 output and its structure hash. The queued job `cache_key` remains the preview input, while submission stores `preview_input_hash` as provenance; no migration is required.
- The design follows a real `glm-5.3-flash` attempt over 89 Requirements and about 25,000 tokens: low reasoning at about 21,000 tokens returned `invalid_provider_output`, while high reasoning at about 32,000 tokens was truncated. Those whole-table attempts followed earlier blind batching, which produced orphan sections and left 80/89 Requirements unresolved. Existing redaction, refusal, metering, attempt ownership and human-review gates remain in force.
- Rubric and scoring calls use shared per-call admission and settlement. Task-budget enforcement remains an external dependency governed by the [budget contract](plan/budget.md), not a score-specific guarantee. See the [score contract](plan/score.md) and [mechanism note](notes/score.md).

## 2026-10-05: Built-in agent command orchestration

- Chain [agent migration `0042`](../server/migrations/versions/0042_builtin_agent.py) after [team-workflow migration `0041`](../server/migrations/versions/0041_team_workflow.py); add persistent owner-only principals/sessions/messages/steps/pauses/job links, encrypted receipts and history, org-composite constraints and database actor/fence gates.
- Register authenticated API and CLI session start/list/show/messages/steps/message/resume/cancel commands with shared Result, invocation schemas, revision checks and endpoint-scoped idempotency.
- Connect the structured decision adapter and seven protected command services to checkpoint controller jobs, transactional child/queue dispatch and durable recovery wakes. Safe retries reuse uniquely proved owned jobs; unknown paid outcomes pause without redispatch.
- Apply immutable cumulative session limits to decisions and descendants through the existing task-budget, prepaid and per-job admission/settlement boundary. Preserve fixed input/model/redaction checks, original human review/confirmation, no-memory A01 generation and confirmed-only draft assembly.
- Add verified built-in-agent/token-automation invocation provenance to jobs/audit and public card/generation/draft views, retaining origin through human edits and immediate worker identity.
- Adopt the defaults in the English [agent contract](plan/agent.md); mechanism and source entry points are in [Built-in agent orchestration](notes/builtin-agent.md). PostgreSQL/RLS, queue crash recovery, concurrent budget and full human-review workflow acceptance remain required; Vue agent pages, MCP and later tools are outside this slice.

## 2026-10-05: Task budget enforcement and cost preflight

- Add migration `0040`, task revision history, human admin/bidder budget changes, tenant constraints, safe audit and admin-controlled low-balance policies/notices.
- Reserve task liability and prepaid funds in the existing admission transaction; enforce Task → Job → OrgBalance locks, live submitter permissions, durable unknown holds and idempotent late settlement.
- Meter search, local OCR and Browser operations; expose task preflight, Result 4.0 and a legacy projection, and retain actual cost and human intervention through failed or partial job status/wait.
- Preserve actorless local worker provenance without granting Provider admission, retain validated actual-model receipts beside fixed requested-model quotes, and serialize deterministic draft cost to JSON before persistence.
- Approve the English [budget contract](plan/budget.md) and [ADR 0007](adr/0007-task-budget-reservations.md). Mechanism: [Task budgets](notes/task-budgets.md). Plans and monthly quotas remain outside this slice.

## 2026-10-05: Faster database citation verification

- Added migration `0039` with a guarded fast path for unique exact quotes, retaining the original normalization, ambiguity and boundary behavior for other inputs.
- Deduplicated live citation verification at check and score publication, preserving source bindings and rejecting additions after early deferred validation; see [check publication gates](notes/check.md#how-it-works).
- Added database parity coverage against the original SQL and Python reference, gate call-count and mutation regressions, and a timed 1,500-requirement publication scenario including commit.

## 2026-10-05: Platform service credential management

- Accepted ADR 0006 and added global-table/dedicated-role exceptions. Migration `0038` adds platform credentials, fixed management/resolver functions, catalog-reference validation, and migration-owner-only ciphertext rewrapping.
- Platform TOTP sessions can create, replace, enable/disable, remove, and test credentials through API, CLI, and Element Plus console. Responses contain fingerprints/last-four only; changes/audit share a transaction; removal retains tombstones. Import supports read-only prechecks, identical replay, and whole-batch conflict rollback.
- Catalog models, Perplexity, and standalone/eval resolve committed state before every outbound call/retry. Missing, disabled, removed, decryption, or database failure has no fallback. API/worker/standalone rejects legacy vendor-key env/direct configuration.
- `rotate-encryption --scope provider-secrets` covers platform and BYOK history, decrypt-only retired keyring, binding validation, CAS rewrapping, and resumable counts. See [Platform credential authority](notes/platform-credentials.md).

## 2026-10-05: First org memory slice

- Added memory CRUD/history, human approve/reject of exact revisions, disable/delete, keyword retrieval/history, per-call usage records, feedback recovery, and org (organization/tenant, 单位) evaluation-sample API/CLI. Runtime contracts moved into schemas; documentation draft module removed.
- Migration `0038` adds org-isolated tables, composite foreign keys, immutable history, and human/worker database gates. All additions are candidate; only human org admin sessions approve/manage/review samples. Disabled scopes explicitly reject.
- `keyword-v1` supplies complete rules/preferences as separate card-generation context. Scope epoch, expiry, and policy version enter cache/admission/publication checks. Each real call saves fixed memories/requirements; settlement links existing usage, without embedding calls.
- Human rejection/editing of model-derived response cards (响应卡) creates encrypted sanitized feedback, org samples, and recoverable candidate jobs; confirmation creates samples only. `feedback-copy-v1` makes no model calls/automatic approvals; unique source keys prevent duplicate candidates. See [Organization memory](notes/memory.md).

## 2026-10-05: Semantic check scope and complete scoring-table requests

- Combined check sends only response rows with confirmed bid (标书) text to CheckProvider. Gaps (缺口) and comply-only (须遵守) retain deterministic checks with semantic status `not_requested`; missing response text alone does not make the report partial. Precheck item counts, cost bounds, and job call plans use the same selection. No model calls without response text. Migration `0036` adjusts partition-specific semantic publication constraints; see [Confirmed-draft checks](notes/check.md).
- Initial rubric implementation sent all scoring Requirements in one request, with a request-capacity gate and no splitting. This generation strategy was superseded by the approved two-stage design in [Two-stage score rubric generation](#2026-10-05-two-stage-score-rubric-generation); source citation, confirmation, redaction and call-billing gates remain.
- Added API→worker and MockTransport acceptance for mixed partitions, zero semantic calls, full-table generation above old batching threshold, and capacity rejection. Replaced old batching assertions while preserving failure/billing gate coverage.

## 2026-10-05: Score execution for confirmed response drafts

- Added `bid score run/list/show`, preview/submit/list/show HTTP routes, and persistent `score` jobs. Bind current DraftRun, confirmed rubric, evaluation date, model/price/reasoning, and redaction (遮挡) revision. Dry-run writes nothing; submission binds preview hash.
- `ScoreProvider.score` receives only redacted scoring rules, tender citations, and fixed confirmed response (响应) text. Each score needs locally verified tender/draft citations; confirmed responses across requirements may support scores. Missing support, out-of-range values, ambiguity, weak commitments, external comparisons, and redaction dependence remain unassessable.
- sum, weighted_sum, capped_sum use Decimal with final eight-place ROUND_HALF_UP. Unassessable children/unsupported aggregates make totals unavailable; assessed subtotal is shown separately. Partial show/wait exits 5.
- Migration `0035` adds FORCE RLS reports, item estimates, supporting responses, and citations; bind same org/task/document/extraction/rubric/DraftRun. Publication rechecks confirmation/worker attempt; report/aggregate history is immutable.
- Reuse outbound admission/reservation/billing. Late refusal/truncation/cancelled calls retain usage; later-batch failure may retain partial results. Input changes, lease/billing failures prevent publication. Added MockTransport/API/worker/CLI/PostgreSQL gate acceptance; see [Scoring](notes/score.md).

## 2026-10-05: Normalized score rubrics and human confirmation (人工确认)

- Added `bid score rubric generate/list/show/revise/classify/section decide/item decide/coverage decide/decide/history`, HTTP interfaces, and `score_rubric` jobs. Candidates read all scoring Requirements only from the selected successful extraction. Dry-run fixes input hash/redaction/cost bounds; submission reuses queue/lease/retry/cancel/usage/prepaid boundaries.
- Rubric sections/items/requirement coverage/replacement versions use FORCE RLS org tables. Admin classifies only; commercial (商务)/technical reviewers confirm items in saved review domains (职责). Bidder confirms the entire set only after deterministic coverage/citation/bounds/weights/caps/aggregate checks. Tokens/agents/workers cannot make human decisions; classification/decision/revision history appends only.
- Both CLI modes retain seven-key Result and 0/2/3/4/5 exits; complex inputs use JSON files only. `bid schema` publishes stage A rubric commands only. Four score scopes register under the approved contract; stage B `score run/list/show`, reports, and scoring Provider jobs remain approved but unregistered. See [Human-reviewed score rubrics](notes/score.md).
- Stage A has an explicit limitation: if redaction changes fixed `Source.quote`/`Source.location`, preview returns `sensitive_scoring_source` and submission rejects before Job creation. Current schema cannot safely retain verbatim Source binding without confidential plaintext persistence. This is not a new contract default; ordinary invalid model citations remain unresolved under the contract.

## 2026-10-04: Combined semantic checks for confirmed drafts

- `bid check run --mode combined` uses separate CheckProvider/structured HTTP calls, fixing org configuration or catalog, reasoning, price, and input hash. Rules still call no model.
- Outbound uses local refs/unified redacted text only. Disabled redaction blocks preview/submission. Validate no_risk_found/risk/unknown per requirement; verify continuous unique citations twice; reject out-of-range, spliced, redacted citations and sensitive reasons.
- Every call retains reservation/billing; refusals, truncation, and calls completing after cancellation record real usage. Later model/budget stops may publish partial; input changes/lease loss/billing failures do not publish.
- Migration `0033` extends existing RLS reports/citations with support for no-risk conclusions and unassessed reasons; added synthetic MockTransport/API/worker/CLI/database-gate tests. See [Confirmed-draft checks](notes/check.md).

## 2026-10-04: Deterministic checks for confirmed drafts

- Added `bid check run/list/show/decide/history` and HTTP interfaces. `rules` fixes one current draft (初稿), extraction, requirements, confirmed responses/materials, certificates (证照), and configuration revisions. Preview returns input hash; worker publishes immutable PDF/Word-citable reports. Same inputs reuse jobs; cancellation/explicit retry/leases/input changes retain existing job fences.
- Stage one covers starred (★) mandatory clause (★条款)/substantive clause (实质性条款) omissions, confirmed negative deviation (负偏离), gaps without human review, and certificate declaration dates. Boundary dates are inclusive; absent dates are unknown; uncited certificates do not bind unrelated requirements. Unconfirmed candidate text enters neither snapshots nor reports. Rules call no Provider/write no UsageRecord/incur no charge.
- Risks are suggestions only. Commercial/technical reviewers append dismiss/reopen decisions with reasons; admins/tokens/workers cannot decide. Historical reports remain readable after input invalidation but cannot receive decisions. Migration `0032`, `check:read`, `check:run`, `check:decide`; `combined` is reserved for stage two and currently returns `check_mode_unavailable`. See [Confirmed-draft checks](notes/check.md).

## 2026-10-04: PDF subprocess isolation and background-image OCR exclusion

- PDF upload validation/full parsing/online preview/evidence-page rendering use short-lived subprocesses with wall-clock/CPU/Linux memory/output limits. Timeouts terminate/reap; excess/crashes end with nonretryable `pdf_resource_limits`. OCR/usage stay in parent; see [PDF parsing](notes/pdf-parsing.md) for mechanism/macOS limits.
- Native text blocks covering at least 25% of an image's area classify it as background; blank margins no longer trigger OCR. Scanned bodies with page numbers still use OCR; text-only pages do not.

## 2026-10-04: PDF rasterization limits and mixed-page OCR

- Rasterization shares pixel-budget prechecks. OCR lowers DPI for manageable large pages and explicitly rejects oversized ones. Scanned bodies with native page numbers/headers use OCR and merge verifiable text; incomplete parsing warns. See [PDF parsing](notes/pdf-parsing.md).

## 2026-10-04: Simulated features still block final export after rebinding

- Export declaration checks all feature/product origin markers. A simulated feature revision rebound to an ordinary product still blocks final sections (正式件); review copies (审阅件) still require warning confirmation. See [product-simulation.md](notes/product-simulation.md).

## 2026-10-04: Live gates for cached export previews

- Cached preview pages reuse DOCX download gates, rejecting reads when inputs/initiator permissions are invalid. Preview state follows export validity and stops serving invalid pages. See [page-previews.md](notes/page-previews.md).

## 2026-10-04: Certificate images and multi-file originals

- Certificate originals accept multiple PDFs/PNGs/JPEGs, composed in order as one PDF, one image per page, individually rotatable. A single unrotated PDF is retained unchanged. Image headers limit dimensions to 40 million pixels before decoding; orient by EXIF and re-encode without location/other metadata in originals/previews. Uploaded files are encrypted unchanged as original components (migration `0031`); components cannot change after submission.
- Console “单位资料” adds “证照与附件”: add business licenses/certificates, drag/multiselect, thumbnails/order/rotation/page preview/download, expired/within-30-days markers. Existing certificate path supports task selection/evidence-page archival/export appendices; image pages have no text and are not sent to models. CLI `resource certificate file add` accepts repeated `--file`. See [versioned-certificate-files.md](notes/versioned-certificate-files.md#composed-originals).

## 2026-10-04: Confidential fields and export-time substitution

- Added confidential fields (保密字段) for quotes/identity/bank/contact/phone values, encrypted append-only values (migration `0030`). `org` has one org-wide value; `task` one per task. Only logged-in admin/bidder sessions set/reveal full values; reveals audit. Tokens never receive `confidential:write`/`confidential:reveal`.
- Drafting replaces registered values with `{{secret.<key>}}` before transmission, with placeholder manifests, `card-draft-v3`/`bid-redaction-v3`. Old submitted jobs stop with `generation_rules_changed`. Cards referencing unknown/archived fields reject; `[REDACTED_…]` prevents confirmation.
- Export fills fixed value rows: final missing values block with `confidential_value_missing`; review missing values show “【名称】”; changes after submission require resubmission. Resources may reference fields; evidence excerpts also fill on export.
- Added “保密字段”, task “报价与保密信息”, and “单位资料” editors. Card/profile text shows chips inserted by drag/click, without typed placeholders. Added `bid confidential`. See [confidential-values.md](notes/confidential-values.md).

## 2026-10-04: Outbound redaction preserves tender wording

- `bid-redaction-v2` stops redacting labels alone. Identity/account/phone labels require number-shaped values; contact labels require colon/equal; amount labels require separators/digits/currency/uppercase amounts; English labels must be separate words. Original text such as “刷身份证登录”“管理员账号”“联系人管理”“预算管理”“Intel 8358” is sent unchanged.
- Submitted/unexecuted old-rule jobs stop with `generation_rules_changed` and require resubmission.

## 2026-10-04: Parallel CI tests on PRs only

- Checks run on PRs (or manually), no postmerge main rerun. Markdown-only/`web/`-only changes skip Python; unchanged `web/` skips console build; both required checks report success. CI database disables durable flushing.
- `pytest -n` parallelism: under advisory lock, one worker migrates base DB, others clone `<database>_gwN`; fixture passwords use same-format low-round hashes. CI uses runner core count and reports slowest tests.

## 2026-10-04: Perplexity vendor search

- With `BID_PERPLEXITY_API_KEY`, vendor-source search and simulated proposals (模拟拟投) use Perplexity Search API instead of SearXNG, which search-engine CAPTCHAs/rate limits block. Without it, SearXNG remains.
- Simulations search candidate vendor domains first. When official pages cannot be fetched, use search excerpts with verbatim checks against excerpts; results/console label “搜索摘录”. Vendor-source capture still accepts only directly fetched pages.

## 2026-10-04: Simulated proposals (demo)

- Task “模拟拟投（演示）” groups technical requirements by purchase items in tender tables. Model classifies hardware/software/services and proposes hardware vendors; worker searches and trusted-fetches vendor pages (one additional same-site link level from catalog pages). Model reads product models, retaining only verbatim models/parameters, saving 【模拟】 products/feature declarations fixed to task. Software development/services propose no products.
- SearXNG vendor search adds Yahoo engine.
- Append-only `simulated_resources` (migration `0029`) retains simulated origin after human revisions. Console labels material/evidence “模拟”; final export blocks with `export_simulated_material`; review requires confirmation. See [product-simulation.md](notes/product-simulation.md).
- Drafting/simulation share structured calls (`providers/structured.py`).

## 2026-10-04: Concurrent drafting batches

- Drafting batch characters become `BID_DRAFTING_BATCH_SCALE` times the extraction level's batch size (default 4), covering about 40 short requirements per call.
- Batches run concurrently up to `BID_LLM_CONCURRENCY` (default 4), like extraction. Each retains call/charge admission limits. Failure stops unstarted batches; sent batches complete/publish in original batch order.

## 2026-10-04: Desktop console layout and readability

- Content max width 1880px; larger body/table/notice type; location labels stop repeating section paths; programmatically focused headings hide focus outlines.
- Task main column: parsing/extraction/history/exports; right: progress/info/current jobs. Drag/drop upload, primary actions first, retry actions in “更多”; reasoning/extraction precheck on one row.
- Review statistics become one row; full-width filters; list rows keep state/star/negative-deviation tags, with category/domain/eligibility as explanation text. Drafting scope/batch dispositions group visually; decision buttons fixed at detail footer. Draft assembly/history side by side; breadcrumbs show task name.

## 2026-10-04: Online tender, certificate, and export previews

- Org console previews tender/certificate PDFs by page; “在线查看原文位置” opens citation page. Word previews parsed paragraphs/tables and highlights cited paragraph/cell.
- Task adds “导出文件”. Commercial reviewers preview released exports; first open converts DOCX through private worker Gotenberg (LibreOffice), then page views reuse one conversion. Preview/download share gates; open audits `export.preview_opened`. Failure saves nothing, explicit retry required. Added `BID_CONVERTER_URL`, `BID_PREVIEW_MAX_PAGES`, Compose `converter`; migration `0028` adds audit events. See [page-previews.md](notes/page-previews.md).

## 2026-10-04: Element Plus console redesign

- Org/platform consoles use Element Plus with on-demand imports/Chinese locale: dark sidebar, org/Chinese roles in topbar, task stepper, review stats/filters/requirements/fixed-right details, draft section tabs. Mobile uses horizontal top nav/stacked requirement cards. Browser titles vary by page/console instead of universal “平台后台”.
- State/domain/eligibility/deviation/job/document codes display Chinese. Member-visible server errors/extraction precheck notices have Chinese explanations; unknown codes retain original text/code.
- Confirm/reject/supply-material operations use dialogs. Disabled “确认响应” explains missing conditions below. Opening/switching pages warns to recheck only when checked review items were cleared. Material panels stop directing web members to CLI resource maintenance.
- Styled status tags preserve browser performance gates for filtering/paging 1,200 requirements. E2E chooses dropdowns by option text and clicks confirmation dialogs.

## 2026-10-04: Missing material no longer becomes negative deviation in drafting

- Prompt `card-draft-v2` uses negative deviation only when material/commitment fails requirements. Missing material alone retains evidence kind with no deviation (无偏离); text states the required material type without asserting satisfaction or inventing models/parameters/lists. Bidder-performed delivery/construction/warranty/service-response obligations draft as commitments using original tender values. Previously missing-material cards became negative deviations; redrafting after supplementation was skipped with `negative_deviation_weakened` and needed human rewriting.

## 2026-10-03: Export tables follow a winning-bid layout

- Three response tables become four columns “序号｜招标文件要求｜投标文件响应内容｜响应情况”, numbered from 1 per table. Copy requirements verbatim, prefix starred with ★. End responses with navigable “（见附件 E003、声明 D001）” references. Compliance text is “响应”/“响应且无负偏离”; deviations use “正偏离：/负偏离：” plus differences, negative deviations bold. Comply-only/gap lists, evidence index, and appendix descriptions omit internal labels, source coordinates, IDs, confirmers, timestamps, hashes. Index shows material name/original page/excerpt/table/ordinal. Render profile `docx-export-v3`; manifest `human-export-manifest-v2` records material names.
- Added `bid export provenance` / `GET /exports/{id}/provenance`: same document numbering returns confirmer/time/evidence/resource revisions/appendix hashes. Added `bid export template-sample`: starter A4 template, 宋体小四 body, 黑体三号 headings, footer page numbers, six anchors, directly usable binding.
- Binding columns become `ordinal`/`requirement`/`response`/`compliance`; public contract `3.0`. Old seven-column bindings remain listable with `current: false`, blocked by `export_binding_outdated`; new seven-column bindings are rejected.

## 2026-10-03: Two-org sandbox and lifecycle acceptance

- Added [sandbox_two_org_acceptance.py](../scripts/sandbox_two_org_acceptance.py) using two synthetic orgs/real worker/gVisor node in development. B receives unknown-resource-equivalent 404 for A runs/task lists/artifact links/job status/cancel/submission/signed links. Runtime DB role reads no sandbox rows without org context; B cannot read/update A. RLS/composite FKs reject unauthorized inserts with successful controls. In-flight cancellation/SIGKILL worker publish no artifacts; containers reap in about 1.5 seconds, subsequent rendering succeeds. Lease-expiry takeover/disconnection/storage fault injection remains unrun.

## 2026-10-03: Word export layout acceptance

- Opened synthetic long-response final section in macOS Microsoft Word (150 requirements, 130 confirmed certificate appendix pages): three tables total 150 rows, unprotected document, 130 independent embedded images/130 bookmarks one-to-one. Appendix descriptions occupied one page and pushed images to the next; renderer now reserves two inches and keeps descriptions/images together, halving appendix pages. Profile `docx-export-v2`, no old-cache reuse.
- Added optional scale scenario `server/tests/test_export_scale.py` and `scripts/word_inspect.applescript`; see [development.md](guides/development.md#check-an-export-in-word).

## 2026-10-03: Split API entry points and shared versioned-resource services

- Products/features/certificates/qualification profiles/templates share `services/versioned.py`: one revision-lock/conflict/task-selection replacement/audit implementation. `VersionedKind` declares tables/scopes/actions; hooks handle feature-product validation/template storage.
- `api/main.py` retains assembly/middleware/error structure/auth context only. Domain routes move to `api/account.py`, `api/tenders.py`, `api/resources.py`, `api/jobs.py`; uploads/parse-extract submission/requirements/job status/token issuance move to `services/`. Four signed-download paths share `api/common.py`. Interfaces/route names/OpenAPI unchanged.

## 2026-10-03: Sandbox proxy adversarial acceptance driver

- Added [sandbox_proxy_acceptance.py](../scripts/sandbox_proxy_acceptance.py): production `FetchBroker`/`SocketBrowserProvider` use real TLS synthetic loopback origins, recording actual requests per target. Covers malicious query/path/userinfo/encoded IP/private/metadata/mixed A/AAAA/DNS rebinding/cross-origin/HTTPS downgrade/infinite redirects/untrusted certificates/Cookie/POST/compression bombs/oversized chunks and forged supervisor fetch frames. Violating targets receive zero requests; every connection uses verified addresses. Excludes proxy-node kernel egress filters/real DNS.

## 2026-10-03: CI time and main protection

- CI runs on PR/main only, not duplicate branch pushes; new PR pushes cancel old runs. Markdown-only required checks succeed without tests/builds. Cache uv/Cargo downloads/renderer builds.
- Protect main: PR-only merge, required `python`/`web`, no force-push/deletion, administrators included.

## 2026-10-03: Independent token keys and data-key rotation

- Sessions/platform sessions/password links/signed downloads use mandatory `BID_TOKEN_KEY`, distinct from `BID_ENCRYPTION_KEY`/`BID_SECRETS_KEY`/retired data keys, otherwise startup fails. Configure before upgrade; existing sessions/links invalidate once after deployment.
- Retired `BID_ENCRYPTION_KEY_PREVIOUS` decrypts only; `python -m app.admin rotate-encryption` rewrites encrypted fields/storage to current key per org, repeatably. See [development.md](guides/development.md#keys-and-storage).
- Migration `0027` removes `api_tokens.encrypted_secret`: tokens retain digests only. Downgrade restores an empty column; deleted ciphertext is unrecoverable.

## 2026-10-03: Slice 1 public-PDF acceptance and review fixes

- Remote CLI completed login/task upload/parse/extract/list with public hardware tender PDF “德邦基金信创交换机项目招标文件” and platform-default model. Saved citations occur verbatim on referenced pages; every starred source row has a requirement; mismatched citations reject/report individually. Slice 1 completion criteria met.
- Unexpected server exceptions return Result `500 internal_error`, exit 4, no exception text. Local mode matches; CLI unexpected errors also return internal_error/4 instead of traceback/1.
- Correct password without org membership returns `401 invalid_login`, same as wrong password.
- Open PDF once; render/OCR pagewise; at most one scanned page in memory.
- Certificate source archives read/render originals outside task lock, then recheck selection/duplicates under lock. At most two concurrent pages/process; timed-out ongoing render keeps its slot until completion. Exhaustion returns retryable `source_render_busy`.
- Missing `BID_TEST_ADMIN_URL` fails database tests rather than skipping.

## 2026-10-03: Self-hosted vendor-source search

- Added `bid evidence search`, `evidence candidates`, `evidence adopt`: precheck shows vendor/model-only terms and input hash. Worker calls self-hosted SearXNG, normalizes fetched URLs (rejecting HTTP/credentials/ambiguous addresses), deduplicates/merges engines, ranks existing same-vendor org-library domains then PDFs, stores at most 20 candidates. Unavailable search retries without saving results. Adoption writes new product `official_url`/`whitepaper_url` revision and selects it for task; stale product revisions reject.
- Compose adds digest-pinned internal-only SearXNG; config `deploy/searxng/settings.yml`. See [development.md](guides/development.md#run-vendor-search-locally) and [screenshot-evidence.md](notes/screenshot-evidence.md#vendor-search).

## 2026-10-03: Vendor capture tolerates missing page resources

- Successful entry-document load suffices for screenshots. Failed resources return per-request `fetch_failed`, without ending the run. Capture even if `load` exceeds navigation budget; stop new requests/wait in-flight before reporting. Mark `vendor_resources_incomplete`. Entry failure/policy changes/revocation still fail; missing primary resources return `source_fetch_failed`. Open development policy is no longer limited to 60 requests/org/minute.
- Vendor archives record incompleteness/rejected request count. Migration `0026` requires sandbox-receipt agreement and `vendor_capture_incomplete` review before confirmation. VendorArchiveView adds fields; public contract `2.2`.
- Real gVisor twice captured/stored H3C product page (139 requests, 3 failures) and white-paper PDF.

## 2026-10-03: Concurrent vendor forwarding and gVisor rendering fixes

- Vendor requests have sequential IDs, at most 4 in flight. Runner/supervisor/caller match responses; supervisor rejects unknown/duplicate/out-of-order IDs. When both source connection leases are occupied, wait rather than reject.
- Approved runsc vendor capture launches Chromium with `--disable-seccomp-filter-sandbox`, retaining namespace sandbox, fixing gVisor `sched_getaffinity` renderer crashes. Prototype rendering unchanged. See [sandbox.md](plan/sandbox.md#decisions). Rebuilt/digest-pinned image.

## 2026-10-03: Development vendor egress and sandbox runner errors

- Development-only `open_public_https` revision permits public HTTPS when `BID_SANDBOX_DEV_OPEN_EGRESS=1`, still URL normalization/credential rejection/public-address/byte limits; accepts local fake-IP proxy `198.18.0.0/15`. Without flag the whole policy file is invalid. See [sandbox-runtime.md](guides/sandbox-runtime.md#open-vendor-egress-on-a-development-node).
- Browser exceptions no longer exit as `invalid_frame`; navigation timeout/failure/unexpected errors report `source_timeout`/`source_navigation_failed`/`runner_unexpected_failure`. Vendor navigation deadlines use run budget. Image rebuilt/digest-pinned.
- Real gVisor completed H3C official white-paper PDF capture/storage. Web capture still limited by slow sequential forwarding and Chromium sandbox crashes; see [sandbox-execution.md](notes/sandbox-execution.md#pitfalls).

## 2026-10-03: Vendor-page and white-paper screenshot evidence

- `screenshot prepare --sandbox-artifact` downloads vendor_capture web/PDF images for local plan processing. `screenshot add` stores `vendor_web`/`vendor_pdf` with archive-hash review. Server resolves URL hash/final source/title/capture time/content hash from run/proxy receipts, replays plan and rechecks image/archive bytes. Web requires `archive=bundle`; multiple pages share one archive.
- Migration `0025` binds vendor archive to sandbox run/archive artifact/entry receipt and images to that capture. Confirmation requires `vendor_model_scope`. VendorSource retains only image artifact ID; removed unimplemented VendorCaptureInput; contract `2.1`. See [screenshot-evidence.md](notes/screenshot-evidence.md#vendor-captures).

## 2026-10-03: Model-generated HTML prototypes (ui mock)

- Added `bid ui mock`/`POST /tasks/{T}/prototype-generations`: fix one requirement/selected feature revision; send redacted requirement/feature declaration only. Precheck prices one call; paid run requires hash. Leased worker generates single-page HTML, offline sandbox captures; encrypted HTML/PNG and receipts persist prototype (原型). Changed selection/unavailable sandbox blocks before model call.
- Fixed intermittent 4 KiB loss for artifacts above 64 KiB under gVisor (`artifact_hash_mismatch`): runner uses `PIPE_BUF` atomic frames, verified with 10 consecutive renders of a model-generated 238 KiB page.
- Added `GET /prototype-runs/{id}`/source image reads. `screenshot prepare --prototype-run` downloads/processes locally, then human ingest creates origin=prototype assets. See [screenshot-evidence.md](notes/screenshot-evidence.md#prototype-generation).

## 2026-10-03: Dedicated VM with rootful Docker and runsc

- Supervisor requires rootful runsc with cgroups enabled; rootless allowed only for synthetic runc. Reject with `sandbox_runsc_cgroups_unenforced`/`sandbox_rootless_required`.
- After development VM switches to runsc (`--oci-seccomp`), isolation subset passes: file/secret boundaries, zero egress with positive controls, CPU loops/memory bombs terminated by limits, container cleanup on disconnect/supervisor restart. Driver `scripts/sandbox_colima_acceptance.py`; rationale [sandbox contract](plan/sandbox.md#decisions).

## 2026-10-03: Sandbox development node and real-pipeline fixes

- Real pipeline on macOS dedicated Colima VM: rootless Docker, digest-pinned images, mTLS control, render/separate verification containers, synthetic HTML→PNG. Node scripts/systemd/env templates in `deploy/sandbox-node/`; see [sandbox runtime](guides/sandbox-runtime.md).
- Added missing `chroot` to seccomp for Chromium sandbox startup. Browser launch failures now report `browser_launch_failed`, not frame errors only.
- Measurements found gVisor/rootless Docker cannot enforce cgroups; runtime combination remains an open decision at this date.

## 2026-10-02: Paid drafting preview binding and spending caps

- Optional `expected_input_hash`/`max_charge` on CardGenerateRequest verify input/model/price preview at submission. Mismatch rejects `generation_input_changed` without job. Accumulated charges+next reservation above cap stop admission and save partial with `spend_cap_reached`. Higher/missing cap on unfinished same-key job conflicts with `generation_cap_conflict`; retry caps cumulative spend. Preview echoes cap/`spend_cap_below_first_call`; CLI adds `--expect-input-hash`/`--max-charge`.
- Org console enables paid drafting after cap/authorization checkbox, submits preview hash, tracks job in panel. See [drafting-binding.md](plan/drafting-binding.md).

## 2026-10-02: Image export and prototype decision gates

- Human exports consume confirmed `image_region` evidence with fixed derived PNG bytes/hashes embedded. All images labeled evidence images; no source-kind/prototype labels in document.
- Final sections need current valid keep decisions for every prototype; reject `prototype_decision_required`, `prototype_replacement_pending`, `prototype_decision_stale`. Decisions enter hash; changes invalidate published exports. Review copies do not depend on decisions. Migration `0024` stores decisions on export evidence rows/rechecks completion. CI builds Rust renderer so image-path tests execute. See [human-section-exports.md](notes/human-section-exports.md).

## 2026-10-02: Org models, human exports, org console, sandbox, and screenshots

- Org configuration: BYOK/platform selection, independent encryption, immutable revisions, `bid provider set/list/history/test`; extraction/drafting share resolution. BYOK zero platform charge but per-call admission/usage, quota guidance/balance queries. Migration `0020`; see [provider-config.md](notes/provider-config.md).
- Human export: human bidder previews/prepares/releases/downloads Word response sections against fixed org template revisions. Final rejects gaps; review marks every page as not for submission. Certificate attachments retain original bytes, encrypted storage/signed downloads/audit/limits. Migration `0021`; see [human-section-exports.md](notes/human-section-exports.md).
- Org console: `web/` adds tender tasks/parsing/official-level extraction, thousand-card review/domain disposition, drafting cost preview, three-table drafts. Draft reads use batch loading. Paid button awaits preview-binding contract. See [org-console.md](notes/org-console.md).
- Sandbox: offline HTML prototype rendering, exact-allowlist vendor web/PDF capture, one-use containers/separate verification/resource budgets/cleanup reconciliation/encrypted artifacts/source receipts. Prototypes have no visible labels; real isolation runtime disabled by default. Migration `0022`; see [sandbox-execution.md](notes/sandbox-execution.md), [sandbox-fetch.md](notes/sandbox-fetch.md), [runtime guide](guides/sandbox-runtime.md).
- Screenshot Phase A: local Rust pixel redaction/crop/region annotation; human exact-hash ingest approval. `image_region` response evidence enters card/draft dependencies; per-prototype keep/replace decisions; admitted/billed multimodal matching/region suggestions/text reading. CLI contract `2.0`, retain legacy Evidence input. Migration `0023`; see [screenshot-evidence.md](notes/screenshot-evidence.md).

## 2026-10-02: Reservation, citation boundaries, and star-detection fixes

- Extraction/drafting HTTP adapters share output-limit validation; model catalog forbids fields/aliases overriding output limits. Reservation/drafting estimates use greatest actual request output limit, preventing smaller aliases from under-reserving. Adapter cache versions updated.
- Migration `0019` aligns human confirmation/table assembly (组表) with Python source spans/normalization/segment boundaries, fixing internal `3.5mm/5mm` and `内存/扩展内存` matches. Verbatim citations remain; no historical rewriting.
- Settlement-pool timeout/unrecoverable database/accounting errors stop further admission first. Failure after an unknown write retains stop state/original reservation; sent calls continue settlement. Explicitly recoverable DBAPI errors retain bounded retries.
- Star merging uses quote spans in complete source text, no substring starring/masking missing starred segments. Postprocessing cache version updated.
- Added MockTransport API/job regression covering catalog/admission/citation repair/human confirmation/table assembly/star supplementation/SQL-Python agreement. See [LLM provider layer](notes/llm-providers.md), [prepaid billing](notes/prepaid-billing.md), [Word citations](notes/docx-citations.md), [response cards](notes/response-cards.md).
- Implemented by Codex after the second adversarial review; verified against real database and corrected one test field name. Full regression at that time: 819 passed.

## 2026-10-02: Model response drafting and outbound redaction

- Added `bid card generate`, API/background job: model/worker drafts for selected extraction/requirements, suggesting disposition/response/deviation (偏离)/candidate citations. Protected cards skipped; versions rechecked at persistence; models cannot weaken recorded negative deviation.
- Submission fixes/encrypts input text. Allowlist: selected requirement text/location, task-fixed citable resource fields, locally extracted certificate-source text. Public preview only IDs/hashes/redaction state/versions/hit counts. Default amount/contact/phone/identity/bank redaction; human org admin alone disables. Recheck permissions/settings revision before every vendor call.
- Anthropic/OpenAI-compatible structured drafting shares default models/official reasoning/admission/settlement/prepaid charges. Batches, truncation/format splits, and transient retries all bill; first-round plan participates in call limits. Preserve valid partial results with exit 5; report incomplete/rejected items by ID/reason only, no raw input/output logs.
- Local citations validate sent text and fixed original. No valid quote remains evidence draft needing material, not automatic commitment. Discard/warn extra commitment citations; candidates/text always require human review.
- Migration `0018` expands adapter catalog identity on drafting records and database gates for model-input dependencies during confirmation/old-draft invalidation/table assembly. No new tables/history rewriting. Assembly `response-draft-v3`.
- Register command/JSON snapshots; Result seven keys/version `1.2`. Approved contract becomes [ADR 0005](adr/0005-human-confirmed-responses.md). See [response cards](notes/response-cards.md), [model outbound/redaction](notes/model-drafting-redaction.md), [LLM provider layer](notes/llm-providers.md), [CLI guide](guides/cli.md#generate-model-response-proposals).
- Platform models without credentials no longer check balance first; extraction/drafting explain `provider_unavailable`. Codex implemented/verified/corrected against real database. Full regression then: 768 passed.

## 2026-10-02: Exact source citations and controlled repairs

- Locate citations using NFKC, curly/straight quotes, whitespace normalization, then save uniquely matched continuous original spans; preserve model text in `model_quote`. Reject absent/duplicate/unknown matches individually while saving other verified results.
- Parameter gap filling no longer treats a multiparameter quote as covering every parameter. Star rules split semicolon/newline segments, star only explicit segments, and add missing items. `gap_fill.remaining` counts parameters still uncovered after saved/rule-added requirements.
- Prompt remains `req-v3`; postprocessing `exact-spans-v1`, HTTP adapter v4 invalidate all provider caches after citation changes. Result remains `1.2`.
- Human org admin `bid req repair-citations`: read-only preview by default; execute requires preview hash/reason. Scope/source/current-card changes yield `repair_preview_changed`; nonunique historical matches remain unchanged. Audit IDs and hashes of old/new citations/model quotes/reasons, no raw text.
- Migration `0017` adds nullable `requirements.model_quote`/card revision citation hashes without rewriting history. Changed hashes yield `needs_reconfirmation`; comply-only needs another human decision. `response-draft-v2` reports gaps; old reads recompute validity while retaining snapshots. See [LLM extraction](notes/llm-providers.md), [Word citations](notes/docx-citations.md), [response cards](notes/response-cards.md), [repair steps](guides/cli.md#repair-legacy-requirement-citations).
- Multiple normalized matches choose one separated on both sides by text boundaries/whitespace/list punctuation, avoiding confusion between `5mm插孔`/`3.5mm插孔` or `内存`/`扩展内存`. Codex implemented/verified/corrected against real database. Full regression then: 727 passed.

## 2026-10-02: Model budgets, immediate accounting, and job leases

- Added shared job execution context: first extraction round/splits/gap fills/retries check attempt ownership/cancel/cumulative calls/platform charge cap before calls. Org transactions reserve call costs to prevent concurrent balance reuse.
- Migration `0016` adds mandatory-org-isolated call records/idempotent usage links. Valid usage commits record/charge/ledger/cumulative cost atomically. Accounting retries never double-charge; late old-attempt charges remain. Over-limit extraction fails without half a requirement set; existing usage retained.
- Renew by `run_id`, heartbeat during long requests. Cancel/expired lease/takeover blocks old attempts. Ordinary cancel waits for sent-call accounting; unknown reservations await reconciliation.
- API/processor/`httpx.MockTransport` scenarios cover budgets/concurrency/cancel/accounting retries/heartbeat/takeover/isolation. Bounds/recovery in [prepaid billing](notes/prepaid-billing.md), parameters in [development](guides/development.md#configure-job-guards).
- Call caps scale with initial batches (`BID_JOB_VENDOR_CALLS_PER_BATCH`, default 4/batch, never below `BID_JOB_MAX_VENDOR_CALLS`) so large tender documents (招标文件) are not stopped mid-first-round. Codex implemented/real-database verified. Full regression then: 717 passed.

## 2026-10-02: Unified password limits and atomic TOTP consumption

- Org discovery/login/platform login share normalized-account failure counts via existing audit/PostgreSQL locks; source limits added. Unknown/disabled/no-password accounts retain generic failures.
- Password verification uses dedicated thread pool/bounded wait queue/cross-process compute slots. Saturation/timeouts retryable; cancelled requests retain running capacity and finish failure accounting.
- Platform TOTP reads/consumes counter under same account lock/transaction; issue session only after successful audit commit, preventing concurrent code reuse. No migration/dependency.
- See [platform authentication](notes/platform-console.md#password-admission-and-totp-consumption), [development retry steps](guides/development.md#run-the-platform-console).

## 2026-10-02: Human response cards and deviation-table draft stage one

- Added create/edit/classify/submit/confirm/reject/supply-material/withdraw/reopen and atomic same-extraction batch dispositions. Humans decide per technical/commercial domain; tokens/agents/workers cannot confirm/dispose.
- Migration `0015`: response revisions/real Evidence/links/drafting runs/three-table snapshots. FORCE org isolation, DB human/state/immutable-history/relationship/full-coverage checks. Task redaction defaults on, human admin only; reserve stage-two model drafting/fixed inputs.
- `bid draft` deterministically assembles confirmed text into substantive/commercial/technical tables, comply-only/gap lists, preserving negative deviation. Material replacement/card revision invalidates old reads. No model/cost in assembly.
- API/local/remote CLI/`bid schema` add commands, retain seven-key `1.2`. Gapped drafts/job status/wait use partial exits. See [response-cards.md](notes/response-cards.md), [CLI guide](guides/cli.md#review-responses-and-assemble-a-draft).
- Stage one reserves model drafting/actual redaction but has no generate/export commands. Later decisions archived in [ADR 0005](adr/0005-human-confirmed-responses.md).
- Development sample smoke: all 1,056 requirements were no-card gaps; 9 normalized-only `invalid_citation`; assembly exited partial. Full regression then: 680 passed.

## 2026-10-02: Item-by-item parameter extraction

- Prompt emits each hardware/software parameter separately with its quote/values/units/qualifiers, no “等” omission; skip bare headings, including rule-added “★3.合同的终止：”. Prompt cache updated.
- After initial extraction, scan semicolon/newline parameter segments, resend only those not covered by valid citations while retaining chunk/page IDs. One gap-fill round uses existing batching/retries/validation/deduplication.
- Job `gap_fill` reports pending segments/calls/new requirements. Every billed call records usage, including failed/truncated/invalid-format gap fills. Existing CLI/API/Result/database unchanged.
- See [llm-providers.md](notes/llm-providers.md) for mechanism/call bounds/extra cost.
- Sample Word/`low`: 1,056 saved (previously 484), 1 gap-fill call added 37, 335 seconds/220,000 tokens. Dense technical chunk covered 144/161 parameters (previous low 73/max 123).
- Full regression then: 584 passed.

## 2026-10-02: Official reasoning levels and extraction history

- Catalog registers official levels (Zhipu GLM-5.3 low/high/max), per-level request options/batch size/Anthropic effort/default. Console edits/tests each level.
- `bid req extract --reasoning LEVEL`, official default if omitted. Unregistered fails `unsupported_reasoning`; unlevelled model ignores/warns. `--dry-run` lists levels.
- Each extraction saves independently. `req list` defaults latest successful/document, `--job` selects one, `req history` lists all; requirements include `job_id`/`reasoning`. Result `1.2`.
- Migration `0014`; [ADR 0004](adr/0004-extractions-per-reasoning-level.md), [reasoning-levels.md](notes/reasoning-levels.md).
- Sample Word/GLM-5.3-Flash: low 141 seconds/484 requirements/150,000 tokens; max (4,000 characters/batch) 51 minutes/914/950,000 tokens. Invalid citations 1/2 respectively; both starred 23/23.
- Empty quotes/text reject individually (`empty_quote`/`empty_text`); unexpected extraction errors retain usage, logs only exception type/stack.
- Network/timeouts/rate limits retry twice in batch (after 10/30 seconds), no whole-job restart. Long-request dropped TLS (`httpx` raw `ssl.SSLError`) is network interruption.
- Full regression then: 572 passed; Playwright E2E passed.

## 2026-10-02: Provider quota exhaustion guidance

- Quota/balance/plan exhaustion (HTTP 402, `insufficient_quota`, `billing_error`, Zhipu 1113 and quota/plan codes within 1308–1321) fails `provider_quota_exhausted` without three retries. Include reset time when supplied and contact-admin guidance. Zhipu 1302/1305 rate limits remain retryable. See [llm-providers.md](notes/llm-providers.md).

## 2026-10-01: Word structural citations

- Parse paragraphs/table cells directly; recognize sections by heading styles/numbering. Stable merged-cell/nested-table/content-control locations. Warn about skipped headers/footers/text boxes/etc.
- Word citations use section path + paragraph/cell, `page:null`, new `location`; Result `1.1`. Quotes must occur within exactly that chunk. Hard rule 6 updated; [ADR 0003](adr/0003-word-structural-citations.md), [docx-citations.md](notes/docx-citations.md).
- Citation matching ignores full/half-width and curly/straight-quote differences; requirements in source order.
- Truncated/invalid output splits batches in half: sections, chunks, then lines for long pages/cells. Fail only if one line still exceeds limits.
- Concurrent batches (`BID_LLM_CONCURRENCY`), default 8,000 characters/32,000 output tokens, per-call total deadline. `BID_LLM_REQUEST_OPTIONS` adds provider options.
- [evals/extract_tender.py](../evals/extract_tender.py) supports Word/citation-pass/star recall reporting.
- Migration `0013`.
- WPS sample (2,117 chunks): GLM thinking off, 135 seconds/449 extracted/446 valid citations/23/23 starred (with rules).
- Invalid citations reject individually; save other items, report rejected location/text/reason in `rejected` plus warnings. All invalid still fails `invalid_citation`.
- Full regression then: 556 passed.

## 2026-10-01: Prepaid balances (预付余额) and recharge cards

- Org prepaid balances/append-only ledger; platform calls charge selling price, balance >0 required for submission, no overdraft allowance.
- Platform admins batch-generate/void recharge cards (充值卡密), add/subtract/set org balances. Org admins redeem at `/app/org/billing` or `bid billing redeem`.
- `BID_BILLING_CURRENCY`; remove `usd` suffix from selling-price/receivable fields.
- Login lists account orgs (`/auth/orgs`).
- Migration `0012`; [ADR 0002](adr/0002-prepaid-billing.md), [prepaid-billing.md](notes/prepaid-billing.md).
- Full regression then: 540 passed; Playwright E2E passed.

## 2026-10-01: Platform operator console (平台运营后台)

- Deployment-defined administrators require password/TOTP, single-use codes, lock after 5 failures within 15 minutes. Platform sessions last 30 minutes, not interchangeable with org sessions/tokens.
- `/app`/`bid platform`: provision/disable/enable orgs, one-time password links, model catalog/tests, monthly usage/receivables/CSV export, audit.
- Disabled orgs immediately invalidate login/sessions/tokens.
- Default catalog model serves all org extractions; usage records cost/selling prices separately.
- Migrations `0010`/`0011`; cross-org [ADR 0001](adr/0001-platform-console-access.md), [platform-console.md](notes/platform-console.md).
- Full regression then: 514 passed; Playwright E2E passed.

## 2026-10-01: Real LLM extraction

- Added Anthropic/OpenAI-compatible httpx adapters; `BID_LLM_*` platform model configuration. See [llm-providers.md](notes/llm-providers.md).
- Retain completed-batch usage before failed calls; errors `provider_refused`, `invalid_provider_output`.
- Empty environment variables count as unset.
- Added [evals/extract_tender.py](../evals/extract_tender.py) for real-service acceptance.
- Full regression then: 483 passed; real service not yet called.

## 2026-10-01: Code review fixes

- Route transactions commit before response (`Depends(context, scope="function")`); commit failures no longer appear successful.
- PBKDF2 verification runs in thread, no event-loop blocking.
- Exit-3 temporary job failures (such as unavailable storage) requeue rather than fail immediately.
- Full regression then: 467 passed.

## 2026-10-01: Eight independent scopes

Each round obtained contract confirmation before implementation. Real LLM integration was deferred by user decision; production extraction explicitly errored. After round eight: 467 local regression passes, remote CI unrun.

1. **Foundation**: global User/Membership, scoped tokens, tasks/encrypted files, pagewise PDF/local OCR, extraction/citation contracts, Procrastinate, remote/local CLI. Migrations `0001`/`0002`; [tenant-isolation.md](notes/tenant-isolation.md), [background-jobs.md](notes/background-jobs.md).
2. **Product metadata**: immutable revisions/optimistic concurrency/fixed task selections/explicit replacement/audit. Migration `0003`; [versioned-resources.md](notes/versioned-resources.md).
3. **Software feature declarations**: product association/declaration state/revisions/task binding. Migration `0004`; [versioned-features.md](notes/versioned-features.md).
4. **Certificate declarations**: qualification (资格)/personnel kinds, optional unknown dates/explicit date checks/separate scopes. Migration `0005`; [versioned-certificates.md](notes/versioned-certificates.md).
5. **Org profile (单位资料) declarations**: optional unknown text fields/separate scopes. Migration `0006`; [versioned-profiles.md](notes/versioned-profiles.md).
6. **Org-private DOCX templates**: encrypted original revisions/task binding/authorized download. Migration `0007`; [versioned-templates.md](notes/versioned-templates.md).
7. **Certificate PDF originals**: original+declaration form new revision; no backfill/inheritance into older revisions. Migration `0008`; [versioned-certificate-files.md](notes/versioned-certificate-files.md).
8. **Unconfirmed PDF sources**: archive fixed original pages at 150 dpi PNG, permanently unconfirmed/ineligible for draft/export. Migration `0009`; [unconfirmed-evidence-sources.md](notes/unconfirmed-evidence-sources.md).

## 2026-09-30: Design document

- Initialized the repository and committed the [AI Bid Tool design](design.md) v0.2 draft by @yiyi.
