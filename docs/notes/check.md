# Confirmed-draft checks

## Problem

A saved draft can omit a mandatory response, retain a negative deviation or
depend on a certificate whose declared dates do not cover the assessment date.
These risks must be reported without turning unreviewed card text into bid
content, granting an agent a human decision right or claiming that a rules-only
scan checked the complete delivered document.

## Usage

Run `bid check run --task TASK --draft DRAFT --as-of YYYY-MM-DD --dry-run
--json`, inspect the fixed input hash and limitations, then submit the same
request with `--expected-input-hash HASH`. Add `--wait` to return the terminal
report. `bid check list` and `bid check show` read reports; a responsible bidder
or technical reviewer uses `bid check decide` and `bid check history` to append
and inspect a dismiss or reopen decision.

The default and only executable mode is `rules`. `combined` is reserved for the
semantic phase and fails with `check_mode_unavailable`; it never falls back to a
rules-only result. A dry run makes no job, audit, usage, balance or report write
and performs no provider call.

## How it works

The input builder accepts one current `DraftRun` and fixes its extraction job,
document, complete requirement partition, response rows, confirmed material,
active certificate selections, confidential-value revisions, redaction setting
and rule/schema versions. Every saved requirement must have exactly one response,
comply-only or gap row. Candidate card text is absent from the fixed snapshot;
only verbatim confirmed response fields and confirmed material can enter it.
Submission must return the preview hash, and the worker recomputes the input and
authorization before publication.

The local rules publish one coverage row per requirement. They report a starred
or substantive gap, every confirmed negative deviation, and a gap caused by an
unfinished or stale human review. Certificate validity uses the explicit
assessment date and inclusive boundary dates. A certificate is mapped to a
requirement only when confirmed evidence for that response names the selected
certificate; unused selections remain in the report with no requirement IDs.
Missing dates are `unknown`. These observations do not authenticate a
certificate or infer a qualification consequence.

An unknown observation on a mapped requirement is unassessed, so the durable
job succeeds with `completion=partial` and the CLI returns exit 5. The report is
still readable. Identical input reuses that terminal report; changing the
material, assessment date or rules creates the input for a new check.

Findings keep the tender's verified PDF page or Word block source. A negative
deviation also cites the fixed response item's deviation note; confirmed textual
evidence may be cited when relevant. Word sources never receive invented page
numbers. Findings and citations are immutable. Human dismiss and reopen actions
append a revision with a non-empty reason and compare both the finding revision
and report input hash. Commercial findings belong to bidders and technical
findings to technical reviewers. Admin sessions, API tokens and workers cannot
make these decisions.

The check is a durable tenant-scoped job. Identical inputs share its cache key;
cancelled or failed work needs explicit retry, and a live lease cannot be taken
over. Publication is fenced by the current run ID, lease and input hash. Reports
remain readable after their inputs become stale, with a warning, but stale
findings cannot receive new decisions. All check tables use forced PostgreSQL
RLS and organization-bound composite references.

## Pitfalls

- Coverage is relative to requirements saved by one extraction job. It does not
  prove that extraction found every tender obligation.
- `comply_only` records a human disposition; it is not proof that material exists
  or that the requirement will receive a score.
- The rules mode does not inspect semantic contradictions, weak explanations,
  confidential values filled during export, layout, headers, signatures,
  attachments or image contents. Its report is advisory and does not change an
  export, response card, evidence or score gate.
- An unmapped certificate date is still shown, but it is not attached to an
  unrelated requirement. A missing date is unknown rather than valid.
- Changing a card, material selection, certificate, confidential value or other
  fixed input makes the historical report stale. Assemble a current draft and
  run a new check instead of reusing its decisions.

## Code

- [check_contracts.py](../../server/app/schemas/check_contracts.py) defines the
  public request, preview, report, citation and decision views.
- [check_inputs.py](../../server/app/services/check_inputs.py) fixes and
  authorizes the confirmed-draft snapshot; [check_rules.py](../../server/app/services/check_rules.py)
  evaluates deterministic observations; [check.py](../../server/app/services/check.py)
  submits, publishes, reads and decides reports.
- [0032_check.py](../../server/migrations/versions/0032_check.py) creates the RLS,
  immutable-history and worker/human decision gates.
- [test_check.py](../../server/tests/test_check.py),
  [test_check_storage.py](../../server/tests/test_check_storage.py),
  [test_check_api.py](../../server/tests/test_check_api.py) and
  [test_check_cli.py](../../server/tests/test_check_cli.py) cover the database,
  API-to-worker and command boundaries with synthetic inputs.
