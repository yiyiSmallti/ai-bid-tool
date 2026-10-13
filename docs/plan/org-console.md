---
kind: plan
---

# Local task console

Implement the browser workspace under
[ADR 0012](../adr/0012-single-user-installation.md), using the existing API and CLI
contracts. Backend recovery reads are defined in
[the console mechanism](../notes/org-console.md); do not duplicate their fields here.

## Scope and dependencies

1. Exchange the installation access key for a console session. Remove password,
   organization selection, role navigation and all collaboration screens.
2. Connect task creation, tender upload, parse/extract jobs and explicit extraction
   selection. Recover uploaded, failed and completed work through persisted reads.
3. Connect requirement confirmation and response review under
   [requirement confirmation](requirement-confirmation.md) and
   [response cards](../notes/response-cards.md). Human checkboxes, expected revisions,
   exact source and warning acknowledgement remain required.
4. Connect model previews and generation under
   [drafting binding](drafting-binding.md), and delivery under [exports](export.md).
   Never infer consent from preview display or job submission.
5. Use [task progress](../notes/task-progress.md) for bounded board snapshots,
   polling and event replay; [console assessments](console-assessments.md) for checks
   and score projections.

## Request and display requirements

Preserve the seven-field Result envelope, explicit partial outcomes and binary
response types. Show input errors, retryable failures, permanent failures and
partial results distinctly. A 401 clears the matching session; a 403 reports a
missing scope without pretending the session expired. A conflict retains edits
but clears stale review acknowledgements. Automatic retries must not repeat writes.

Send bearer credentials only to same-origin allowlisted paths. Render original
text, filenames and model content as text. Retain the security headers from
[main.py](../../server/app/api/main.py). Logout and task/extraction changes discard
pending requests and late responses from the prior view.

Show counts for the selected extraction only. Do not truncate a complete set and
present it as complete. Mount the current page and selected detail without bulk
image preloading. Distinguish current-page selection from all matching rows and
require explicit review before submitting batches.

## Acceptance

Use the real API, PostgreSQL gates, worker and storage with fake external
providers. The [fixture provisioner](../../scripts/org_console_e2e.py) creates
synthetic inputs; artifacts belong under `data/work/`, never `docs/`.

- Recover task/document/job IDs after refresh; repeated uploads and cached runs
  preserve idempotence. Different reasoning levels retain independent histories.
- Verify PDF/Word citations against exact original locations, wrong-parent IDs,
  expired signatures, changed inputs and stale revisions.
- Verify human confirmation succeeds only after every required evidence/warning
  acknowledgement; agents and tokens cannot confirm or export.
- Verify atomic batch conflicts roll back the batch, generation leaves draft
  output, and stale evidence cannot enter assembled or final exported content.
- Verify event reconnect, revoked-token termination, bounded queues, pagination,
  empty extraction, unknown progress totals and explicit provider failure.
- Exercise 1,200 mixed rows with keyboard navigation, focus recovery, 200% zoom
  and narrow screens. Target first-load interaction within five seconds and loaded
  filter/page interaction p95 within 300 ms; retain material sizes and timings.
- Compare API and CLI envelopes and error codes on the same isolated data.

## Exclusions

Fabricated supporting materials, automatic human confirmation, procurement
submission, arbitrary new libraries and silent API extensions are excluded.
Browser integration must consume the single-user contracts rather than introduce
compatibility identities for removed people or organizations.
