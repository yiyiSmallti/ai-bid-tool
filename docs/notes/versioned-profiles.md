# Organization declarations and retained task versions

## Problem

Company information and common wording change over time, while each task must
retain the exact selected text. Declarations must not be mistaken for authenticated
registration or performance evidence, or automatically change organization identity.

## Usage

Supply a nonblank name and optional registration_details, performance_summary,
standard_wording, and the short facts address, phone, legal_representative and
credit_code that whole-bid forms print verbatim. Unknown text stays null. Updates require expected_revision and
complete data. Select a profile for a task; replace explicitly to change it. History
retains every version and selection. Every result warns that authenticity,
performance and qualification have not been verified.

## How it works

`org_profiles`, `org_profile_revisions` and `task_org_profiles` store company
profile declarations; their names describe the resource, not an authorization boundary. A deferred composite
current pointer requires a real revision at commit. A snapshot binds one immutable
revision to a task. The runtime can only update the profile current_revision
or snapshot active column; it cannot rewrite payload or audit, or delete
retained records. SQL checks guard the JSON object, nonnull string name and bounded
nonblank optional text, accepting unknown/null values without inventing them.

The service locks a profile for expected-version updates. Selection locks the task
and share-locks the profile when resolving current, so parallel duplicates share
one saved ID and stale writes return explicit 409 conflicts. Active slots are scoped
by task/profile/lot. Replacement closes the old row and appends a new row;
audit records actor/token and old/new IDs in the same transaction, without textual
metadata. Function-scoped API dependencies commit before sending success.

Separate `profile:read`, `profile:write` and `task:profile` scopes govern access.
Task lists also require `task:read`. Tokens receive only explicitly granted scopes;
human-only confirmation and export decisions are never delegated.

## Pitfalls

These are user-supplied declarations, never authenticated source materials or an
eligibility decision. There is no contract upload, inferred registration/performance,
document generation, evidence confirmation, draft/export, provider or external call.
Extra attachments and undeclared input keys are rejected. Inputs over 128 KiB fail before
transport and errors redact input content. The company name is resource metadata;
it does not change the installation identity. A provisioning owner still needs separate access
and backups. Downgrade refuses destructive history drops.

## Code

- server/app/schemas/profile_contracts.py, services/profiles.py, services/versioned.py and api/resources.py.
- server/migrations/baseline/, models/entities.py and services/auth.py.
- test_profiles.py, test_resource_rls.py, CLI snapshots and both genuine CLI modes.
