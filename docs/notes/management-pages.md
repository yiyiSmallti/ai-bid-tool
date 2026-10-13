---
kind: reference
---

# Resource library management

## Read contracts

The installation library exposes bounded query, exact detail, content history and
lifecycle history for products, features, certificates, company profiles and templates.
The route and schema definitions are in
[management_products.py](../../server/app/api/management_products.py),
[management_features.py](../../server/app/api/management_features.py),
[management_certificates.py](../../server/app/api/management_certificates.py),
[management_profiles.py](../../server/app/api/management_profiles.py) and
[management_templates.py](../../server/app/api/management_templates.py).
[management_pages.py](../../server/app/schemas/management_pages.py) defines the
shared page and lifecycle contracts. Payloads carry resource IDs and revisions;
they do not attribute a revision to a person. Company profile table names describe
company declarations, not a tenant boundary.

Queries apply normalized prefix filters before `LIMIT limit+1`. Products search
name/vendor/model; certificates search name/number; other resource kinds search
name. Features additionally filter product and implementation state. Resource
bodies, confidential values and file contents are not search fields. Root creation
time and UUID order lists; revision and UUID order histories. Pages contain an
observation time, continuation and bounded rows rather than a total count.

Encrypted cursors bind actor kind, token or agent session, current scopes, parent,
filters and anchor. A changed identity or filter invalidates the cursor. Queries
limit rows before projection, enforce encoded Result size limits and use a
statement timeout. The resource scale tests in `server/tests/test_management_*_scale.py`
inspect query plans as well as timings and response bytes.

## Identity and human decisions

[authenticate](../../server/app/services/auth.py) resolves the access key, console
session or scoped token. The human has `HUMAN_SCOPES`; a token or agent cannot gain
`HUMAN_ONLY_SCOPES`. Read services enforce each resource's read scope. Lifecycle
changes additionally require a human and the resource's write/lifecycle scopes.
The actor and scope boundary is independent of metadata provenance.

Original template downloads require `template:read` and human-only
`template:file:read`. Binding reads and writes use
[exports.human_access](../../server/app/services/exports.py). Preview and creation
retain exact template/static-content hash checks and an explicit human decision.
[management_bindings.py](../../server/app/services/management_bindings.py) validates
the exact template revision parent before bounded binding reads. Bindings remain
immutable; file validation or binding review never confirms evidence.

## Lifecycle and task selections

Content and lifecycle revisions are independent. A transition checks both expected
versions while locking the resource root. The lifecycle event, root update and audit
commit together. [The baseline schema](../../server/migrations/baseline/10_tables.sql)
binds each event to exactly one resource root and immutable content revision.
Its [trigger functions](../../server/migrations/baseline/00_functions_core.sql)
protect event sequences and retained history.

[versioned.select_revision](../../server/app/services/versioned.py) resolves task
access before resource locking. An exact existing active pin is an idempotent
replay, including after resource deactivation. A different selection of an inactive
resource fails without closing the previous pin. Revision or restoration never
replaces a task selection automatically. Retired pins cannot be reactivated.

Feature selection locks the task first and relevant resource roots in UUID
order. It rechecks the feature's product parent after locking. New or replacement
pins require the feature and applicable current/historical product parents to be
active. Moving a feature to another product creates a content revision and retains
old task pins.

## Files and declarations

Certificate detail joins only the requested revision's original and bounded parts.
A metadata-only revision stays without an original even if older versions have one.
Date advice requires an explicit inspection date and does not establish authenticity.
File links remain short-lived authenticated reads; lifecycle changes neither rewrite
originals nor confirm evidence. See [certificate files](versioned-certificate-files.md).

Template revisions require DOCX bytes and retain independent file hashes. Selection
and binding inspection refer to exact revisions. See [templates](versioned-templates.md).
Profile text retains confidential placeholders without reading their values; see
[confidential fields](confidential-values.md).

## Client behavior

`bid resource <product|feature|certificate|profile|template>` provides `browse`,
`show`, `history`, `lifecycle set` and `lifecycle history`. `bid export binding`
provides bounded `browse` and `show` with an exact template revision. Commands use
access-key or token authentication and validate response kind, parent, revision and
Result envelope before presenting data. They retain file hash, safe-path and
no-overwrite checks described in the file notes.

The console adaptation requirements and repeatable acceptance criteria belong in
[the management plan](../plan/management-pages.md). Library scopes do not grant
human-only task decisions. CAS conflicts require fresh state and deliberate
resubmission; secret values and search text stay outside persistent browser storage.
