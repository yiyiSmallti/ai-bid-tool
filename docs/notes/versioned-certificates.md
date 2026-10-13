# Declared certificate dates and retained task versions

## Problem

Certificate metadata may change while a task must retain the exact selected
declaration. Date classification must not imply authenticated validity or invent a
business date.

## Usage

Supply qualification/personnel kind, nonblank name/number and optional ISO dates.
Updates require expected_revision. Select an immutable revision for a task, then
replace explicitly if needed. Use --history for retained versions/selections and
--as-of YYYY-MM-DD for declared-date inspection. Missing inspection dates remain
unknown. Each result warns that authenticity, legality and compliance are unverified.

## How it works

The tables preserve the shared versioning algorithm with separate
`certificate:read`, `certificate:write` and `task:certificate` scopes.
Tokens receive only explicitly granted scopes; resource grants alone confer no
certificate access.

Deferred current pointers require real revisions at commit. Composite
snapshot keys enforce the certificate/revision/task relationship. Revisions
and audit are append-only for the runtime; snapshots only permit active updates.
Certificate updates lock the resource; selections serialize on the task and share
lock the resource. Duplicate selections return one ID and one audit event.
Function-scoped API transaction dependencies commit before emitting success.

Date classification is deterministic: end before as_of means expired; start after
as_of means not_yet_valid; both known bounds containing as_of mean valid; otherwise
unknown. Boundary days are inclusive. Known expired/future bounds still classify
when the other bound is unknown. Every returned revision has its own inspection
map entry, including null-as_of/unknown when no date was supplied.

## Pitfalls

SQL CHECK accepts UNKNOWN, so kind/name/number require explicit nonnull guards.
Date checks reject malformed/inverted declarations. Direct database tests cover
these checks in addition to API Pydantic validation. A date label is only an
inspection of user-supplied dates; it is never evidence, authentication or a legal
or qualification decision. No scan, generated document, reminder, AI/provider call,
task deadline inference or evidence confirmation is implemented here.

## Code

- server/app/schemas/certificate_contracts.py and services/certificates.py.
- server/migrations/baseline/, models/entities.py, services/versioned.py and api/resources.py.
- test_certificates.py, test_resource_rls.py, CLI snapshots and genuine runtime test.
