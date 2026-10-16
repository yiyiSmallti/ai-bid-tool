---
kind: adr
---

# 0014 Scoped authority for package imports

Status: accepted. Date: 2026-10-15.

## Context

A [package](../plan/enterprise-gateway.md#packages) carries a project's history:
extractions, review events, card revisions, drafts, check and score runs and
the jobs that produced them. Database triggers on these tables admit a row only
as the live step of its own workflow: a running job with a valid lease, a human
actor for decisions, append-only history in order. A historical row copied from
another installation satisfies none of these, so a row-by-row import is refused.

Three ways were considered:

- **Replay through the services.** The import calls the same code a member
  would. No trigger changes, but check and score reports and job history are
  produced again rather than kept, and every confirmation needs a human again,
  contrary to the package rule that confirmations keep their timestamps.
  Rejected.
- **A smaller package.** Only content (documents, chunks, requirements with
  review state, latest card revisions, library items) is imported and runs are
  repeated. Fewer guards change, but the project loses its record of decisions.
  Rejected.
- **Scoped import authority.** The guards admit inserts that belong to one
  running import. Chosen.

## Decision

A `package_imports` row binds an import to its job and to the task it
creates, which keeps its source identifier. A guard admits an `INSERT` without its lease and actor checks only when all
hold:

- the transaction names that import (`app.package_import_id`) and the job's
  execution run, as worker execution already does;
- the import is in state `importing`, its job is `running` with that run and an
  unexpired lease;
- the row belongs to the import's task, or is a library row the import
  creates.

The import conditions sit on the checks that make a row the live step of its
workflow. Imported rows skip the lease and actor checks, currency against later
edits (current inputs, active selections and evidence), cross-table ordering,
run inputs the package omits, retired library items and the order of a review
chain written in one statement. The checks on a row's own content and bindings
still run. Updates and deletes are never admitted this way, apart from the
search-vector refresh of library rows the import creates; immutability guards
are unchanged. The import writes everything in one transaction, so no other
session sees the task before the import commits. Decisions keep their
timestamps; the `package.imported` audit entry, attributed to the importing
member, records the source in `imported_from`. Agents and tokens still cannot
confirm anything.

## Consequences

- Each guard on an included table gains import conditions on its live-step
  checks. A guard added later to an included table must decide whether imports
  pass it.
- A failure in any row aborts the whole import with no writes.
- The authority is as strong as worker execution: code running inside the
  import job can write the imported task's history. The import code verifies the
  manifest, rejects a task that already exists and resolves every citation before writing.
