---
kind: reference
---

# Org resource library management

## Problem

An org (organization/tenant; 单位) library must expose manageable pages without
loading every product, feature, certificate, org-profile or template revision.
Library maintenance must preserve the exact version selected by each task (任务).
Withdrawing a resource from future selection
must retain its content, historical pins and auditable human action.

## Usage

The product list at `/app/org/products` supports prefix search and lifecycle
filters. `/app/org/products/:id` presents the current or explicitly selected
revision, content history, lifecycle history and complete revision editing.
Technical staff and org administrators maintain declarations; all org roles may
read them. Declaration or simulated-proposal (模拟拟投) provenance is not evidence
(证据) confirmation. Task context supplies an explicit exact-revision selection
action under the existing task authority ceiling.

The feature list at `/app/org/features` adds same-org product and implementation
state filters; `/app/org/features/:id` exposes complete feature declarations and
exact revision histories. The implemented state is a declaration that still needs
material. Revising it does not replace screenshots or confirm a prototype (原型).
Product choices use bounded product queries rather than all-row library dumps.

The profile list and certificate section at `/app/org/profiles` use bounded reads.
`/app/org/profiles/:id` and `/app/org/certificates/:id` expose exact declarations,
separate content/lifecycle histories and explicit task selection. Bid specialists
and administrators maintain them; other org roles may read. Certificate flow is
metadata, optional original upload, inspection of the returned revision, then task
selection. Ordered PDF/PNG/JPEG parts and rotation retain the existing file bounds.
A metadata-only revision displays "no original on this revision" even when an older
revision has a file. A date advisory requires an explicit `as_of`; it makes no
claim about authenticity or qualification eligibility.

The template list at `/app/org/templates` supports name-prefix search and lifecycle
filters. Detail at `/app/org/templates/:id` separates declared chapters/project
types, file validation, original inspection, binding inspection and task selection.
Each create or revision requires its own real DOCX. Its stored hash and validation
receipt establish file readability, not evidence authenticity or export approval.
Human org administrators maintain templates; all org roles can inspect metadata
and, through authenticated short-lived links, open originals.

The exact revision's `/app/org/templates/:id/revisions/:revisionId/bindings` page
lets human administrators configure the fixed sections and response columns from
[ExportSectionBinding](../../server/app/schemas/export_contracts.py), preview the
mapping, inspect static content in the original and explicitly create a binding.
Changing the form invalidates the preview and review acknowledgement. Creation
uses the template and static hashes returned by that exact preview. A hash conflict
requires a fresh preview and deliberate review. Human bidders inspect bindings;
technical/viewer users and tokens cannot invoke binding reads or writes. Existing
bindings are immutable, and a legacy `current=false` binding remains unusable for
export. There is no visual Word editor or automatic binding inheritance.

The corresponding commands are `bid resource <product|feature|certificate|profile|template> browse --input QUERY.json`,
`show --id R [--revision N]`, `history --id R`, `lifecycle set --id R --input STATE.json`
and `lifecycle history --id R`. Their payloads, bounds and seven-field Result envelope
are defined in the [approved contract](../plan/management-pages.md#http-and-pydantic-interfaces).
Certificate `show` also accepts `--as-of YYYY-MM-DD`. Existing `add`, `update`,
`list`, certificate file and task resource commands retain their contracts.
Template `show` also accepts `--revision-id UUID`, mutually exclusive with
`--revision N`. `bid export binding browse --template-revision UUID` and
`show --id UUID --template-revision UUID` use bounded human-authorized reads;
existing binding `create/list` commands retain their shapes and gates.

## How it works

The [product read service](../../server/app/services/management_products.py),
[feature read service](../../server/app/services/management_features.py),
[certificate read service](../../server/app/services/management_certificates.py),
[profile read service](../../server/app/services/management_profiles.py) and
[template read service](../../server/app/services/management_templates.py) apply
org visibility and filters in SQL before `LIMIT limit+1`. Immutable root creation
time and ID order the live list; revision and ID order each root's histories.
Encrypted cursors bind the current actor, org, authority, parent, filters and
anchor. Pages have their own observation time and no total count. Indexed current
name/vendor/model tokens support prefix search; source URLs and bodies are not
search fields. Features search only name prefixes and filter the current same-org
product and implementation state. Certificates search declared name and number;
Profiles and templates search name only, never profile bodies, confidential
values, declared template chapters or project types.
Exact revision authors come from a unique matching audit (审计)
association, or remain unknown. A root-level simulation marker applies to older
revisions too.

Resource authors use `AuditLog.resource_revision_id_text`, a stored text projection
of the audit revision ID in [0050](../../server/migrations/versions/0050_feature_library.py).
One audit query explicitly filters the authenticated org and only the loaded page's
revision IDs, matching each kind's action partial index's leading columns. Fixed
action literals preserve partial-index eligibility for prepared plans. Root and
numeric revision checks still apply, and multiple exact audit matches leave the
author unknown. The generated column inherits the audit table's FORCE RLS and does
not introduce a writable author or revision identifier. Certificate author lookup
also includes `resource.certificate.file.create`, matching its root, revision ID
and numeric revision. Its index and the profile author index are added by
[0052](../../server/migrations/versions/0052_certificate_profile_library.py).

The fixed-scale suites bound visited immutable revision and audit rows, including
rows discarded by filters and index rechecks. Recorded latency alone does not
establish bounded history access.

Resource management read routes use the joined identity, active Membership and org lookup in
[`authenticate`](../../server/app/services/auth.py). The service consumes that
request's authenticated identity once; direct service calls revalidate authority.
This removes duplicate reads without weakening the session/token checks. Scale
acceptance counts SQL statements for authentication and transaction-local settings
as well as the projection. Cursor filter digests preserve the same binding while
keeping maximum-length Unicode searches within the opaque-token size limit.

Content revision and lifecycle revision are independent. A transition requires
both expected versions, a real human maintainer, a fixed reason code and the
locked resource root. The event insertion changes the root and requires the audit
in the same transaction. Tenant composite foreign keys, FORCE RLS and database
guards protect event bindings and immutable history. Product, feature, certificate,
profile and template events share a table with exactly one non-null root arm. Each
arm binds its real root and immutable content revision through org composite keys;
partial unique indexes
protect each lifecycle sequence. Extending another kind requires its own migration.

[Selection](../../server/app/services/versioned.py) checks task membership and
archival before locking the resource root. An exact existing active pin returns its
original receipt without a selection write, including after deactivation.
Every other inactive selection is rejected before retiring the prior pin. New
content revisions never post task selections. Restoring a resource enables future
explicit selections without changing old pins.

Feature selection locks task/workflow first, then the feature and relevant product
roots in UUID order. It rechecks parent identity after locking; new or replacement
pins require the feature, current parent and selected revision's parent to be active.
An exact existing active pin remains a no-op even after feature or product withdrawal.
Feature create/revise requires an active same-org proposed parent. Moving to another
active product is an explicit new content revision, never a rewrite of existing pins.

For direct SQL, tenant `WITH CHECK` rejects foreign or missing org context before
resource-state validation. The selection guard runs as a nondeferred AFTER trigger,
after the existing immediate composite foreign keys. Invalid parent pointers
therefore retain their FK rejection; every row that passes those keys still runs
task authority and lifecycle checks before the statement completes. The product's
BEFORE archive guard and the feature's pure BEFORE task lock retain task-first
locking; the feature, certificate and profile archive checks run AFTER composite
keys. Certificate/profile guards use their own task selection scopes and human
bidder/admin lifecycle authority. Missing-parent lookup
shortcuts must not skip these checks, because FK visibility can differ from an
earlier ordinary lookup during concurrent or same-statement writes.

An inactive historical pin cannot be reactivated, even by an authorized actor.
The retained-history trigger in [0023](../../server/migrations/versions/0023_screenshots.py)
protects product, feature and certificate pins. The profile trigger in
[0052](../../server/migrations/versions/0052_certificate_profile_library.py) runs
after row, unique and composite-key constraints, then rejects historical activation
before new-pin authority and lifecycle checks. A conflicting active profile slot
therefore retains its unique-constraint rejection. Template pins enforce the same
retained-history rule in the AFTER selection guard in
[0054](../../server/migrations/versions/0054_template_library.py), after RLS,
row, unique and composite-key constraints. Restoring a library root does
not restore retired pins. New pin inserts pass the tenant, composite-key and
authority checks before lifecycle rejection.

Token issuance remains human-only in both the API and database. The additive
[token creation scope guard](../../server/migrations/versions/0051_token_creation_scope.py)
completes the existing domain-specific token scope checks; it validates existing
rows without rewriting tokens. Lifecycle actions retain their separate human
session gate and do not introduce an issuable lifecycle scope. Certificate/profile
actions additionally require `certificate:lifecycle` or `profile:lifecycle`, assigned
only to human bidder/admin roles. Both are in `HUMAN_ONLY_SCOPES`, excluded from
token-issuable scopes, and rejected by the additive database token-scope CHECK.
Their existing content write scope is also required by the database guard.

Certificate detail joins the selected metadata revision to its original and reads
at most the allowed parts for that file. File links are never cached in a page or
history row. Original downloads still use authenticated, short-lived same-org
links through the existing file service. Page previews keep the authenticated PNG
route and `PageViewer` image handling; `DocumentPreview` remains unchanged.
Lifecycle transitions do not update certificate files, file parts or evidence-source
archives, and never confirm evidence. Profile forms retain confidential field
placeholders as declared text without resolving or reading confidential values.

The [template migration](../../server/migrations/versions/0054_template_library.py)
extends lifecycle events with a template root/revision arm and independent unique
sequence. Template lifecycle uses a human admin session, existing template write
scope and content/lifecycle CAS. Selection locks task/workflow before the root;
RLS and immediate composite keys reject invalid tenants and parents before the
AFTER selection guard evaluates authority and active state. Only an exact existing
active pin is an idempotent replay. Restoring or revising a template never replaces
task selections. Recovery preserves lifecycle history and repairs forward.

Template authors use the same stored audit revision projection, with a template
action partial index and one explicitly org-filtered query restricted to the loaded
page's revision IDs. Missing or ambiguous exact matches remain unknown. Template
name-prefix and binding `(org_id, template_revision_id, reviewed_at, id)` indexes
support bounded queries; binding reads validate the exact revision parent and use
[exports.human_access](../../server/app/services/exports.py). Read projections do
not retrieve file bytes or call providers.

Original-file endpoints require both `template:read` and human-only
`template:file:read`. The new scope is denied in `HUMAN_ONLY_SCOPES` and the
API-token database check. The internal template reader remains available to the
existing authorized export worker; the human gate belongs to original-download
entry points. Upload keeps the existing `template:write` eligibility. Binding
preview and creation reuse the existing DOCX inspector and immutable hash checks,
with `export.binding_created` auditing explicit creation. File validation,
lifecycle changes and binding review do not confirm evidence or bypass task export
preflight/release and prototype decisions.

## Pitfalls

- Library read/write scopes grant no task role. Observers, reviewers and admin
  recovery readers cannot pin; archived tasks reject even duplicate selection requests.
- Historical inactive snapshots are not active duplicates. A different revision,
  root or normalized lot creates a distinct selection operation. Selecting another
  root does not remove the previous root.
- Editing an inactive library resource does not restore it. CAS conflicts require a fresh
  read and deliberate save; the browser retains only nonsecret unsaved edits.
- Search text and form bodies stay out of URLs and persistent browser storage.
  Org changes cancel reads and discard late results. Legacy all-row lists are not
  used to populate these pages.
- Database and browser acceptance must run in an environment with PostgreSQL and
  Chromium. The fixed-scale scenario records real API timings, SQL plans and bytes
  beneath `data/work/management-pages-validation`; mocked browser artifacts
  explicitly identify their mode. Neither is a substitute for the other.
- Preserve the lifecycle guards and event history during recovery. Disable the
  affected entry point and repair forward; a pre-lifecycle writer may not serve
  selection writes after the migration.

## Code

- [Product schemas](../../server/app/schemas/management_pages.py) and
  [HTTP routes](../../server/app/api/management_products.py).
- [Read/lifecycle service](../../server/app/services/management_products.py),
  [existing product writes](../../server/app/services/resources.py) and
  [selection service](../../server/app/services/versioned.py).
- [Lifecycle migration](../../server/migrations/versions/0048_product_library.py)
  and [guard ordering](../../server/migrations/versions/0049_product_guard_order.py),
  with the [event model](../../server/app/models/management.py).
- [Product list](../../web/src/views/OrgProducts.vue),
  [detail](../../web/src/views/OrgProduct.vue) and
  [task authority](../../web/src/task-authority.js).
- [API acceptance](../../server/tests/test_management_products.py),
  [pin acceptance](../../server/tests/test_management_product_pins.py),
  [fixed-scale acceptance](../../server/tests/test_management_products_scale.py),
  [CLI snapshots](../../server/tests/test_management_products_cli.py) and
  [browser scenarios](../../web/e2e/management-pages.spec.js).

- [Feature HTTP routes](../../server/app/api/management_features.py),
  [existing feature writes](../../server/app/services/features.py) and
  [feature lifecycle migration](../../server/migrations/versions/0050_feature_library.py).
- [Feature list](../../web/src/views/OrgFeatures.vue),
  [detail](../../web/src/views/OrgFeature.vue) and
  [bounded product picker](../../web/src/components/FeatureProductPicker.vue).
- [Feature API acceptance](../../server/tests/test_management_features.py),
  [storage acceptance](../../server/tests/test_management_features_storage.py),
  [feature scale acceptance](../../server/tests/test_management_features_scale.py),
  [feature CLI snapshots](../../server/tests/test_management_features_cli.py) and
  [feature browser scenarios](../../web/e2e/feature-management.spec.js).

- [Certificate HTTP routes](../../server/app/api/management_certificates.py),
  [profile HTTP routes](../../server/app/api/management_profiles.py),
  [certificate/profile migration](../../server/migrations/versions/0052_certificate_profile_library.py)
  and [shared lifecycle model](../../server/app/models/management.py).
- [Profile entry](../../web/src/views/OrgProfiles.vue),
  [certificate section](../../web/src/components/CertificateSection.vue) and
  [qualification management helpers](../../web/src/qualifications.js).
- [Certificate API acceptance](../../server/tests/test_management_certificates.py),
  [profile API acceptance](../../server/tests/test_management_profiles.py),
  [storage acceptance](../../server/tests/test_management_certificate_profile_storage.py),
  [certificate scale acceptance](../../server/tests/test_management_certificates_scale.py),
  [profile scale acceptance](../../server/tests/test_management_profiles_scale.py),
  [certificate CLI snapshots](../../server/tests/test_management_certificates_cli.py),
  [profile CLI snapshots](../../server/tests/test_management_profiles_cli.py) and
  [qualification browser scenarios](../../web/e2e/qualification-management.spec.js).

- [Template routes](../../server/app/api/management_templates.py),
  [binding routes](../../server/app/api/management_bindings.py) and
  [binding projections](../../server/app/services/management_bindings.py).
- [Template list](../../web/src/views/OrgTemplates.vue),
  [detail](../../web/src/views/OrgTemplate.vue) and
  [binding review](../../web/src/views/OrgTemplateBindings.vue).
- [Template API acceptance](../../server/tests/test_management_templates.py),
  [storage acceptance](../../server/tests/test_management_templates_storage.py),
  [scale acceptance](../../server/tests/test_management_templates_scale.py),
  [template CLI snapshots](../../server/tests/test_management_templates_cli.py),
  [binding CLI snapshots](../../server/tests/test_management_bindings_cli.py) and
  [browser scenarios](../../web/e2e/template-management.spec.js).
