---
kind: reference
---

# Product library management

## Problem

An org (organization/tenant; 单位) library must expose manageable pages without
loading every product revision. Library maintenance must preserve the exact
version selected by each task (任务). Withdrawing a product from future selection
must retain its content, historical pins and auditable human action.

## Usage

The product list at `/app/org/products` supports prefix search and lifecycle
filters. `/app/org/products/:id` presents the current or explicitly selected
revision, content history, lifecycle history and complete revision editing.
Technical staff and org administrators maintain declarations; all org roles may
read them. Declaration or simulated-proposal (模拟拟投) provenance is not evidence
(证据) confirmation. Task context supplies an explicit exact-revision selection
action under the existing task authority ceiling.

The corresponding commands are `bid resource product browse --input QUERY.json`,
`show --id R [--revision N]`, `history --id R`, `lifecycle set --id R --input STATE.json`
and `lifecycle history --id R`. Their payloads, bounds and seven-field Result envelope
are defined in the [approved contract](../plan/management-pages.md#http-and-pydantic-interfaces).
Existing `add`, `update`, `list` and task resource commands retain their contracts.

## How it works

The [product read service](../../server/app/services/management_products.py) applies
org visibility and filters in SQL before `LIMIT limit+1`. Immutable root creation
time and ID order the live list; revision and ID order each root's histories.
Encrypted cursors bind the current actor, org, authority, parent, filters and
anchor. Pages have their own observation time and no total count. Indexed current
name/vendor/model tokens support prefix search; source URLs and bodies are not
search fields. Exact revision authors come from a unique matching audit (审计)
association, or remain unknown. A root-level simulation marker applies to older
revisions too.

Product read routes use the joined identity, active Membership and org lookup in
[`authenticate`](../../server/app/services/auth.py). The service consumes that
request's authenticated identity once; direct service calls revalidate authority.
This removes duplicate reads without weakening the session/token checks. Scale
acceptance counts SQL statements for authentication and transaction-local settings
as well as the projection. Cursor filter digests preserve the same binding while
keeping maximum-length Unicode searches within the opaque-token size limit.

Content revision and lifecycle revision are independent. A transition requires
both expected versions, a real human maintainer, a fixed reason code and the
locked product root. The event insertion changes the root and requires the audit
in the same transaction. Tenant composite foreign keys, FORCE RLS and database
guards protect event bindings and immutable history. The product-only event arm
requires a migration before other library kinds can adopt lifecycle behavior.

[Selection](../../server/app/services/versioned.py) checks task membership and
archival before locking the product. An exact existing active pin returns its
original receipt without a selection write, including after product deactivation.
Every other inactive selection is rejected before retiring the prior pin. New
content revisions never post task selections. Restoring a product enables future
explicit selections without changing old pins.

For direct SQL, tenant `WITH CHECK` rejects foreign or missing org context before
product-state validation. The selection guard runs as a nondeferred AFTER trigger,
after the existing immediate composite foreign keys. Invalid parent pointers
therefore retain their FK rejection; every row that passes those keys still runs
task authority and lifecycle checks before the statement completes. The existing
BEFORE archive guard retains the task-first lock order. Missing-parent lookup
shortcuts must not skip these checks, because FK visibility can differ from an
earlier ordinary lookup during concurrent or same-statement writes.

## Pitfalls

- Library read/write scopes grant no task role. Observers, reviewers and admin
  recovery readers cannot pin; archived tasks reject even duplicate selection requests.
- Historical inactive snapshots are not active duplicates. A different revision,
  root or normalized lot creates a distinct selection operation. Selecting another
  root does not remove the previous root.
- Editing an inactive product does not restore it. CAS conflicts require a fresh
  read and deliberate save; the browser retains only nonsecret unsaved edits.
- Search text and form bodies stay out of URLs and persistent browser storage.
  Org changes cancel reads and discard late results. Legacy all-row lists are not
  used to populate these pages.
- Database and browser acceptance must run in an environment with PostgreSQL and
  Chromium. The fixed-scale scenario records real API timings, SQL plans and bytes
  beneath `data/work/management-pages-validation/scale`; mocked browser artifacts
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
