---
kind: reference
---

# Console assessment projections

## Problem

Checking and scoring use immutable saved drafts, versioned rubrics and human decisions.
Loading a whole report on every console visit makes large citation graphs expensive and
can conceal incomplete work behind a successful HTTP response. A browser also needs to
recover submitted jobs without assuming that a lost response means submission failed.
The requirements are defined in the [approved console contract](../plan/console-assessments.md).

## Usage

Task, response-review and draft pages link to the task's check and score workspaces.
A new run selects a successful extraction explicitly. Checking and scoring select a
current draft; scoring also selects a confirmed rubric from the same extraction.
Rubric generation needs the extraction but does not require a draft.

Preview and submit are separate actions. Model-backed runs require a positive platform
charge ceiling and explicit consent after preview. The budget panel displays the
server's task limit, spent liability, reservations, available amount and admission
blocker. The [task-budget mechanism](task-budgets.md) defines their meaning.

A report link opens its pinned inputs. A responsible human can dismiss or reopen a
finding with a reason. Rubric review separates administrator classification, bidder
coverage decisions, domain review and whole-set confirmation. Correcting a response
returns to its requirement in the existing response-card review flow.

## How it works

Assessment APIs expose opt-in `view=console` projections with summary and row-specific
parts. Input discovery, task-job discovery and saved-citation traversal supply only the
additional reads needed by the console. Writes reuse the existing check and score
requests and services. No console persistence or independent assessment engine is added.

Read services authorize the parent and its complete dependency graph before exposing
counts. Task visibility, lifecycle and responsibility domains follow the
[task-workflow mechanism](team-workflow.md#how-it-works). Projections, capability
hints and shared human writes reuse its authorization service; hints may reuse a
permission result within one projection, while every mutation reauthorizes.
SQL selects keyset positions before hydrating selected row narratives.
Authenticated cursors bind the principal, org, parent, filters and review snapshot;
a changed snapshot requires reloading the view. A separate stable snapshot identity
lets a replacement editor verify sections, items and coverage belong to one revision.

Pages contain at most 100 rows and 2 MiB of encoded Result data, including metadata.
The response ends before an oversized next row and carries a continuation cursor.
A single row exceeding that ceiling fails visibly without truncating its verified text.
Citation reads traverse a saved authorized entry and verify the entire saved quote,
then expose a window of at most 8,000 Unicode characters with absolute quote offsets.
Rubric sections expose every saved source binding and its selected quote. Their
`origin=sources` traversal selects a zero-based `citation_index`, re-resolves the
pinned Requirement and canonical Source, and verifies the selected quote at that
location. Items and coverage retain single-source traversal. Every section source
contributes to the encoded row limit; sources and quotes are never silently removed.
No arbitrary document, URL or supplied quote is accepted as a citation source.

The console sends assessment and associated job requests through `/v4/` to retain real
budget preflight and Result cost fields. The API adapter recognizes only specified
partial assessment results and terminal job states. Ordinary error envelopes still fail.
Org reset aborts requests, invalidates the response epoch and clears unsaved assessment
state. Reasons and replacement content are never persisted in browser navigation storage.

The full replacement editor loads every page before allowing edits to be saved. It retains
source identities and other-domain content. Section source selectors can be kept,
removed or added from the pinned requirements, with at least one retained; the editor
sends only requirement IDs and selected quotes, never rewritten Source objects. It
compares changes with that baseline and
bounds the serialized replacement to 512 KiB. Revision proposals are read separately
from the current baseline; applying a pending coverage proposal still requires a human
decision. Whole-set and full-replacement mutations can return compact console receipts
inside the same transaction, before commit.

Jobs use polling, explicit cancellation and explicit retry. Optional generation progress
is observational: old workers remain indeterminate; two-stage workers may identify
section analysis, item batches, validation or publication. The UI never invents an
overall percentage from elapsed time or an absent batch count.

## Pitfalls

- A report read's zero cost is the cost of reading it. Historical expense comes from
  the authorized job's actual cost, never from report usage identifiers.
- A blocked preview can have `ok=true`. Submission remains disabled until its blocker
  is resolved and a fresh preview has been reviewed.
- Job completion, report completeness and report freshness are independent. A stale
  report remains useful for tracing earlier decisions but cannot authorize a new one.
- Only `total_status=estimated` permits a displayed total estimate. An assessed subtotal,
  possible range, unavailable section or unassessable item must not become a total or
  a numeric zero. Aggregation belongs to the score service.
- A classification is not confirmation. A whole-set confirmation cannot replace child
  review or requirement coverage decisions. An advisory finding decision does not
  confirm evidence or satisfy export gates.
- Mocked browser flows verify UI requests and state transitions. They do not prove
  PostgreSQL RLS, publication constraints or production Provider effectiveness.

## Code

- [Read contracts](../../server/app/schemas/console_assessments.py): projections, progress and citation windows.
- [Projection boundaries](../../server/app/services/assessment_bounds.py): cursor/snapshot bindings and encoded page limits.
- [Inputs, jobs and checking reads](../../server/app/services/assessment_reads.py): dependency authorization and citation traversal.
- [Rubric projections](../../server/app/services/assessment_rubrics.py) and [score projections](../../server/app/services/assessment_scores.py): bounded review/report reads.
- [Check API](../../server/app/api/check.py), [score API](../../server/app/api/score.py) and [org-console API](../../server/app/api/org_console.py): existing writes and additive reads.
- [CLI read adapter](../../cli/bid_cli/assessments.py) and [schema registry](../../cli/bid_cli/schema.py): remote/local parity and discovery.
- [Console helpers](../../web/src/assessments.js), [check workspace](../../web/src/views/OrgChecks.vue), [rubric review](../../web/src/views/OrgRubricReview.vue) and [replacement editor](../../web/src/components/RubricReplacement.vue): explicit user transitions.
- [Browser acceptance](../../web/e2e/console-assessments.spec.js), [two-org API acceptance](../../server/tests/test_console_assessments_db.py) and [task-authority acceptance](../../server/tests/test_console_task_authority.py): separate UI, org isolation and task-role checks. Repeatable generated artifacts belong under `data/work/console-assessments-validation/`.
