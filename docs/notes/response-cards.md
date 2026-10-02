# Response cards and draft tables

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
The wider workflow, including model proposals and outbound redaction, is specified
in [review-and-draft.md](../plan/review-and-draft.md).

## How it works

A card binds one requirement to its task and an explicitly selected successful
extraction job. Re-extraction creates an independent set of requirements. The
card's current pointer advances through immutable revisions; every write checks
`expected_revision`. Task locks serialize review changes with material selection
replacement. Batch disposition locks the task and cards in a stable order, and
commits all items and their audits together.

The `Identity` context in [auth.py](../../server/app/services/auth.py) supplies
transaction-local actor kind, user, token, and organization. Services recheck
active membership and intersect current role grants with token or saved worker
grants. Only human sessions may make decisions: technical members review technical
cards, bidders review commercial cards, and admins classify an unclassified draft
or change the task redaction setting. Admin status does not confer a professional
reviewer's authority. API token scopes exclude confirmation and export.

The transition table in the approved contract governs submit, withdraw, confirm,
reject, request-material, and reopen actions. Confirmation requires complete
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
page text. Human confirmation changes the Evidence's page review status; the
[unconfirmed source archive](unconfirmed-evidence-sources.md) remains unchanged.
Replacing a task selection invalidates dependent material. Selecting the old
resource revision again creates another selection and does not revive the old
Evidence. A confirmed card must be reopened, updated and reviewed again.

The PostgreSQL gates in
[0015_response_cards.py](../../server/migrations/versions/0015_response_cards.py)
combine tenant RLS, composite references, restricted write grants, immediate
actor/state checks and deferred completeness checks. They reject missing or
nonhuman decision context, invalid current pointers, unconfirmed evidence rows,
late links to historical revisions, incomplete draft coverage, and updates or
deletes of history. Trusted application authentication supplies the actor context;
SQL access as the application role is not an alternative authentication API.

Card citation checks require the stored quote to occur as exact text and to have
one unique normalized match at the recorded page or Word block. Migration `0017`
adds nullable `requirements.model_quote` and
`response_card_revisions.quote_sha256`. Creation, editing, withdrawal, reopening,
and human disposition bind a new revision to the current exact requirement quote;
other actions inherit the previous baseline. Existing revisions remain immutable: when their hash is null,
the service derives the historical hash from `model_quote`, or from the current
quote when no model quote exists. It never backfills or rewrites revision history.

A legacy citation repair may change the requirement's exact quote or populate
`model_quote`, but it does not silently approve existing review. When the current
revision's quote hash differs from the repaired source quote, the card reports
`needs_reconfirmation` and cannot be confirmed. A pending card must be withdrawn,
and a confirmed card reopened, before it is submitted or edited and reviewed
again. A `comply_only` requirement needs a fresh human disposition. The repair
audit event `requirement.repair_citation` contains only object IDs and old/new
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
`needs_reconfirmation` gap reason, so an old draft becomes invalid when its
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
- The task redaction setting is persisted and protected, but its outbound behavior
  belongs to the model drafting workflow. Changing it does not redact stored tender
  text or rewrite existing responses.
- Model proposal fields and the generation-run table reserve the approved data
  contract; the command registry in [schema.py](../../cli/bid_cli/schema.py) defines
  available commands. Export requires its own authorization and renewed evidence
  checks; a historical draft is not an export authorization.
- Downgrade refuses to delete review history. Retain the schema when reverting
  application code.

## Code

- [response_card_contracts.py](../../server/app/schemas/response_card_contracts.py): shared validation and views.
- [models/response_cards.py](../../server/app/models/response_cards.py): tenant records and references.
- [services/response_cards.py](../../server/app/services/response_cards.py): actor checks, material resolution, revisions and human actions.
- [services/citation_repair.py](../../server/app/services/citation_repair.py): preview-bound tenant-admin citation repair and hash-only audit metadata.
- [services/drafts.py](../../server/app/services/drafts.py): manifests, complete assembly and historical validity.
- [server/migrations/versions/0017_exact_citations.py](../../server/migrations/versions/0017_exact_citations.py): quote provenance and immutable revision hashes.
- [api/response_cards.py](../../server/app/api/response_cards.py): authenticated API entry points.
- [jobs/processor.py](../../server/app/jobs/processor.py): attempt-safe background publication.
- [test_response_cards.py](../../server/tests/test_response_cards.py): API-to-worker workflows and repeatable synthetic artifact.
- [test_response_card_db.py](../../server/tests/test_response_card_db.py), [test_rls.py](../../server/tests/test_rls.py): direct SQL gates and tenant isolation.
