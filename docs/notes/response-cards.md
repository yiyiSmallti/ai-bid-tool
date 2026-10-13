# Response cards (响应卡) and draft tables

## Problem

A tender requirement, a proposed response, a material declaration, and a human
review are different facts. Combining them into one editable record would let a
new extraction or material selection silently change an earlier decision.
Draft tables must also account for requirements that have no response, rather
than presenting an incomplete table as a complete bid.

## Usage

Follow [Review responses and assemble a draft](../guides/cli.md#review-responses-and-assemble-a-draft).
The shared inputs and views are defined in
[response_card_contracts.py](../../server/app/schemas/response_card_contracts.py).
The review decisions and their rationale are recorded in
[ADR 0005](../adr/0005-human-confirmed-responses.md). The outbound input boundary is
defined in [model-drafting-redaction.md](model-drafting-redaction.md).

## How it works

A card binds one requirement to its task and an explicitly selected successful
extraction job. Re-extraction creates an independent set of requirements. The
card's current pointer advances through immutable revisions; every write checks
`expected_revision`. Task locks serialize review changes with material selection
replacement. Batch disposition locks the task and cards in a stable order, and
commits all items and their audits together.

The `Identity` context in [auth.py](../../server/app/services/auth.py) supplies
transaction-local actor kind, token and agent attribution. Services recheck current
scopes and saved worker grants. Only the installation human may confirm, reject,
reopen, decide dispositions, classify cards or change the task redaction setting.
Review domains determine response-table grouping. API tokens and agents cannot
obtain confirmation or export scopes.

The service and database enforce these transitions:

| From | Action | To |
| --- | --- | --- |
| No card | Create or generate | draft |
| draft / rejected / needs_material | Edit or generate | draft |
| draft | Submit | pending_review |
| pending_review | Confirm / reject / request material | confirmed / rejected / needs_material |
| pending_review | Withdraw with a reason | draft |
| confirmed | Human reopens with a reason | draft |

Each action appends a revision. Pending and confirmed content cannot be replaced
directly. A human `comply_only` decision protects content until the human changes
that decision back to `respond`. Confirmation requires complete
response text, deviation and explanation, an exact tender citation, and explicit
review of every linked evidence ID and warning. An evidence response requires
confirmed material; a commitment has no evidence. Confirmation records `respond`
when no disposition was previously decided. A separate human `comply_only`
decision needs neither response text nor evidence and cannot act as confirmation.
Reasons and warning acknowledgments remain in authorized revision history.

Evidence resolves typed selection and revision references on the server. A
resource quote must occur exactly in an allowed field. Product URLs remain
stored declarations and are never fetched. Certificate page evidence reads the
retained original, verifies its hash and page, and matches locally extractable
page text. Human confirmation (人工确认) changes the Evidence's page review status; the
[unconfirmed source archive](unconfirmed-evidence-sources.md) remains unchanged.
Replacing a task selection invalidates dependent material. Selecting the old
resource revision again creates another selection and does not revive the old
Evidence. A confirmed card must be reopened, updated and reviewed again.

### Model proposals

`submit_generation` in [card_generation.py](../../server/app/services/card_generation.py)
requires `card:generate`, `card:read`, `task:read` and every selected material's
read grants. It fixes the successful extraction job, selected requirements,
current card revision IDs, provider configuration and model identity, reasoning and versioned
input manifest. The encrypted text snapshot lives in internal `jobs.result.submission`
until publication; job status never exposes submission state. Publication appends
an immutable `CardGenerationRun` with the same encrypted snapshot and public
ID/hash manifest, satisfying the database's running-attempt insertion gate.

The cache includes the input hash and expected revisions.
A manual edit, new input,
reasoning level, provider configuration revision, prompt/schema/adapter version or redaction
setting changes the key.
Retries keep the original encrypted input and cumulative
call budget.
Changed configuration fails explicitly before sending new text.

The worker fixes `origin=model`, `actor_kind=worker`, `model_job_id` and `state=draft`.
It never sets a human disposition or confirmer. Under the task lock it checks each
expected revision again. Confirmed, pending and comply-only (须遵守) cards are reported as
protected skips at submission and publication; concurrent edits report
`revision_conflict`; a changed requirement quote/location reports
`requirement_input_changed`. A proposal changing a recorded current negative deviation to
none or positive is rejected. Missing/duplicate proposals and unknown requirement
IDs are reported without storing their output text.

Because that rule makes a model-recorded negative deviation permanent, the drafting
prompt ([drafting.py](../../server/app/providers/drafting.py) `SYSTEM_PROMPT`) reserves
`negative` for material or commitment content that falls short of the requirement. A
requirement that only lacks material keeps the evidence kind with `deviation=none`,
names the material to add, and asserts no unproven parameter; the `needs_material`
hint and the evidence rule for confirmation keep it out of confirmed output.
Obligations the bidder performs itself, such as delivery, warranty and service
terms, are drafted as commitments at the tender's own values.

Valid responses from completed batches may be published after a later provider
or resource-limit failure, with `completion=partial`, `stop_reason`, usage IDs and exit 5.
Without a completed batch the job fails without cards.
Lease loss, cancellation,
heartbeat failure or accounting failure publishes no proposals.
Protected skips
alone do not cause partial completion; invalid references, missing proposals,
revision conflicts and `needs_material` do.
A successful partial job is terminal;
repeat requests return it, while editing the unresolved card or changing inputs
creates a new submission.

Every supplied field/page is a conservative dependency of the model's response,
even if not cited. Submission, classification and review actions retain
`model_job_id`; an explicit content replacement starts new dependencies. A
model-generated commitment that received materials therefore becomes stale when
one of those selections is replaced. A commitment generated without material
inputs, a manually authored commitment and a comply-only decision have no such
dependency. `generation_materials_stale` checks the full manifest and its read
permissions. The database's `response_generation_materials_active` enforces the
same dependency boundary during confirmation and assembly.

The PostgreSQL gates `response_require_human`, `response_revision_gate`,
`response_card_gate`, `response_evidence_confirmation_complete`,
`response_draft_complete` and `response_immutable_gate` in
[00_functions_response.sql](../../server/migrations/baseline/00_functions_response.sql)
combine task references from
[40_foreign_keys.sql](../../server/migrations/baseline/40_foreign_keys.sql),
restricted write grants from
[60_grants.sql](../../server/migrations/baseline/60_grants.sql), immediate
actor/state checks and deferred completeness checks. They reject missing or
nonhuman decision context, invalid current pointers, unconfirmed evidence rows,
late links to historical revisions, incomplete draft coverage, and updates or
deletes of history. Trusted application authentication supplies the actor context;
SQL access as the application role is not an alternative authentication API.

Card citation checks require the stored quote to occur as exact text and to
resolve to one original span at the recorded page or Word block. Application and
database checks use the same normalization and segment-boundary disambiguation
defined in [docx-citations.md](docx-citations.md#how-it-works). The baseline defines the shared citation predicate consumed by confirmation and
draft gates, plus `requirements.model_quote` and
`response_card_revisions.quote_sha256`. Creation, editing, withdrawal, reopening,
and human disposition bind a new revision to the current exact requirement quote;
other actions inherit the previous baseline. Existing revisions remain immutable: when their hash is null,
the service derives the historical hash from `model_quote`, or from the current
quote when no model quote exists. It never backfills or rewrites revision history.

A legacy citation repair may change the requirement's exact quote or populate
`model_quote`, but it does not silently approve existing review. When the current
revision's quote hash differs from the repaired source quote, the card reports
`invalid_citation` and cannot be confirmed. A pending card must be withdrawn,
and a confirmed card reopened, before it is submitted or edited and reviewed
again. A `comply_only` requirement needs a fresh human disposition. The repair
human audit event `requirement.citation_repair.executed` contains only object IDs and old/new
hashes, including separate quote and model-quote changes; it excludes tender
text and provider text, and retains the operator's reason only as
`reason_sha256`.

Draft submission fixes an input manifest and hash containing every requirement,
its card revision or absence, citation hash and material eligibility. The job
processor rechecks the initiator's current permissions, cancellation/attempt
identity and fixed inputs before publication. A changed input fails with
`draft_input_changed`, requiring a fresh submission. An identical input reuses
its job and immutable draft. Dry-run performs the same input checks without
creating a job, audit, usage record or draft.

Assembly copies confirmed content verbatim. Each requirement appears exactly once
in a substantive, commercial or technical row, the comply-only list, or the gap
list. Starred/substantive requirements have table priority; otherwise the review
domain selects the table. Rows preserve the original category and star flag.
Reading order follows chunks, located blocks and quote positions. Negative
deviations remain visible. Gaps contain source locations and reason codes, never
unconfirmed candidate text. Reading an old draft recalculates validity and
affected requirements without rewriting its response snapshots. The assembly rule
(`RULE_VERSION` in [drafts.py](../../server/app/services/drafts.py)) maps repaired cards awaiting review to the
`invalid_citation` gap reason, so an old draft becomes invalid when its
current source inputs no longer match its reviewed revision.

Assembly does not call a model or OCR and records zero model cost without creating
an empty usage record. A job may succeed with `completion=partial`: CLI draft,
status and wait commands then return exit 5 and `ok=false`. Audits contain IDs,
state/disposition changes and correlation identifiers; review reasons, tender
text, response text, material quotes and credentials are not copied into logs.

## Pitfalls

- Coverage is relative to saved requirements of the chosen extraction; it does
  not prove that extraction found every tender obligation. Older extraction jobs
  remain available explicitly.
- Material declarations and a human page review do not prove original authenticity,
  hardware performance, completed functionality, or semantic truth. Proof-material
  warnings require a recorded human handling decision. A commitment is not proof.
- A page without locally extractable text remains available as a source preview,
  but cannot supply an exact page quote to this workflow. There is no implicit OCR,
  vendor call or image upload fallback.
- Citation repair is not card repair. It leaves unlocatable requirements and all
  card revisions unchanged; the human review workflow is what renews a response
  or `comply_only` decision after a source quote changes.
- A changed redaction setting stops new calls from queued or running generation;
  it does not alter stored tender text, prior responses or already issued calls.
- The command registry in [schema.py](../../cli/bid_cli/schema.py) defines
  available commands. Export requires its own authorization and renewed evidence
  checks; a historical draft is not an export authorization.
- Downgrade refuses to delete review history. Retain the schema when reverting
  application code.

## Code

- [response_card_contracts.py](../../server/app/schemas/response_card_contracts.py): shared validation and views.
- [models/response_cards.py](../../server/app/models/response_cards.py): task records and references.
- [services/response_cards.py](../../server/app/services/response_cards.py): actor checks, material resolution, revisions and human actions.
- [services/citation_repair.py](../../server/app/services/citation_repair.py): preview-bound human-only citation repair and hash-only audit metadata.
- [services/drafts.py](../../server/app/services/drafts.py): manifests, complete assembly and historical validity.
- [services/card_generation.py](../../server/app/services/card_generation.py): fixed inputs, cache, worker publication and partial results.
- [api/response_cards.py](../../server/app/api/response_cards.py): authenticated API entry points.
- [jobs/processor.py](../../server/app/jobs/processor.py): attempt-safe background publication.
- [test_response_cards.py](../../server/tests/test_response_cards.py): API-to-worker workflows and repeatable synthetic artifact.
- [test_card_generation.py](../../server/tests/test_card_generation.py): model-to-review chain, outbound privacy, references, costs, attempts and isolation with MockTransport.
- [test_response_card_db.py](../../server/tests/test_response_card_db.py): direct SQL human and integrity gates, including `test_nonhuman_or_missing_actor_cannot_make_decisions`, `test_response_history_cannot_be_deleted` and `test_confirmation_requires_nonempty_confirmed_evidence`.
