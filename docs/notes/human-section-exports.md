# Human release of editable response sections

## Problem

A successful draft job is neither a human release decision nor permission to
reuse stale material. Export must preserve exact reviewed responses, account for
every saved requirement, and bind a private Word file to the selected template,
evidence pages and human warning acknowledgments. The approved scope and remaining
integration boundary are in [the export contract](../plan/export.md).

## Usage

The API and CLI share the models in
[export_contracts.py](../../server/app/schemas/export_contracts.py). An active human
admin previews and creates an immutable template binding. An active human bidder
previews a particular draft and task-template selection, then submits its exact
input hash and warning IDs with `bid export prepare`. `--wait` ends when a private
candidate awaits release; it never releases a file. `bid export release` separately
requires the input hash and candidate hash. List and show return history without
implying that the associated bytes remain downloadable.

`bid export download` reads current history, requests a short-lived link, verifies
the returned file descriptor and saves to a new local `.docx` path. The local mode
uses the same authenticated API and PostgreSQL gates. Final sections (正式件) require no
gaps. Explicit review copies (审阅件) retain the visible no-submission marker and return
partial completion on release and download, including when they have no gaps.

## How it works

[exports.py](../../server/app/services/exports.py) rebuilds a canonical input
manifest from the selected `DraftRun`, immutable `ResponseItem` rows, current card
reviews, typed Evidence relations and template binding. Every requirement occurs
once as a response, a comply-only (须遵守) decision or a gap. Gap entries carry source and
reason metadata, never unconfirmed proposed responses or candidate material.
Canonical UTF-8 JSON uses sorted keys, compact separators and ASCII escaping.
The input hash excludes acknowledgments; the manifest hash also binds the exact
acknowledgment set. Cache identity adds the initiator to the input hash.

Human authorization intersects current membership and role with all source-read
permissions. The token scope allowlist excludes export. The worker retains its
worker identity and must own the exact job attempt and lease. Normalized run items
and evidence links supply composite foreign keys; the manifest does not substitute
for those relations. [0021_exports.py](../../server/migrations/versions/0021_exports.py)
adds FORCE RLS, append-only grants, immediate actor gates, deferred coverage and
confirmation checks, and export-specific audit identity checks. The worker policy
for a succeeded job lasts only through its completion transaction. Older material
tables retain their tenant RLS; the trusted worker loader restricts actual reads to
the fixed run's authorized inputs.

The template adapter accepts the ordered body anchors and style/column bindings
specified by the contract. Response tables have the four columns of a winning bid:
number within the table, the verbatim tender requirement, the confirmed response with
links to its attachments and declarations, and the compliance state with any
deviation. Identifiers, confirmers, timestamps and hashes are not printed; the
provenance route rebuilds them from the fixed manifest with the same numbering. Static headings must match the selected template
revision's declared chapter titles. Missing metadata is refused when its marker is
present. Unsupported fields, hidden content, revisions, media and external
relations fail adaptation. Template modification requires another retained revision
or binding; adaptation never repairs a supplied package by deleting business text.

[export_render.py](../../server/app/jobs/export_render.py) stages verified inputs in
a private temporary directory and starts
[export_child.py](../../server/app/jobs/export_child.py). The child has no database,
storage credentials or object-writing capability. Cancellation, lease loss,
deadline and memory failures terminate and reap it. Linux additionally enforces an
address-space limit; macOS resident memory is monitored by the parent. Deployment
ceilings in [Settings](../../server/app/core/config.py) may tighten the approved
limits. The process reads and checks each archived PNG separately, preserves its
bytes, and embeds only individually confirmed pages.

[export_renderer.py](../../server/app/services/export_renderer.py) builds real Word
tables and paragraphs, stable evidence bookmarks and page attachments. Each
attachment starts a new page; its caption, in the binding's heading style, keeps with
the image, which is scaled to the usable page area less two inches so both fit on one
page. It fixes
ZIP order, timestamps, compression, core properties and XML attribute ordering.
The renderer profile records the actual runtime and serialization dependencies.
Reading a retained export compares its inputs against the recorded profile rather
than silently rebinding it to the currently installed renderer. Re-rendering still
requires that profile to be available. Conflicting output hashes stop publication.

Confirmed `image_region` Evidence becomes an image attachment: the manifest fixes
the rendition ID, PNG hash, size and dimensions, deduplicates by rendition, and
the worker reads the rendition through the screenshot access checks. Every image is
titled `证据图片` and indexed as `图片` with its confirmed visual observation, whatever
its source, so a document never shows which images are prototypes.

A final section also runs `prototype_gate`: each prototype image needs a current
`keep` decision from
[prototype_decisions.py](../../server/app/services/prototype_decisions.py), otherwise
`prototype_decision_required`, `prototype_replacement_pending` or
`prototype_decision_stale` blocks it. The current decisions and their set hash are
part of the input manifest, so a later decision makes the run and its export stale.
`export_run_evidence.prototype_decision_id` stores the kept decision, and
[0024_export_images.py](../../server/migrations/versions/0024_export_images.py)
adds `export_prototype_kept` to the completion gate, which requires that decision
to be the latest `keep` for the same card revision, rendition and feature revision.
Review copies never depend on decisions.

Preparation, candidate publication and human release use the task, sorted cards,
member/dependency and run/job lock order. File I/O occurs outside the short final
mutation checks. The service re-reads fixed inputs under locks before committing
business rows and identifier-only audits. Object storage and SQL are not one
transaction; an unsuccessful commit can retain an encrypted unreferenced object,
but cannot expose a half-published file.

Candidates and released files use different org-prefixed immutable keys in the
existing encrypted Storage. Download signatures bind org, export ID, file hash and
a dedicated kind. Issuing a link and delivering bytes both recheck current human
permission and all relevant input gates. Decrypted length and SHA-256 must match
before a response body is sent. A served audit records a delivery attempt, not proof
of local persistence.

[export_client.py](../../cli/bid_cli/export_client.py) accepts only the exact
same-service signed route, follows no redirects, bounds streams and validates DOCX
structure, length and hash. Private temporary files, directory descriptors,
`fsync` and atomic no-overwrite linking protect existing files and detect parent-path
replacement. Only the verified local receipt reports a completed download.

## Pitfalls

- Prototype decision reasons and material kinds stay in the internal manifest and
  issues. The renderer must not print `material_kind` for images; adding a
  per-source caption would reveal prototypes.
- Source archives remain unconfirmed. Export eligibility comes from the human
  Evidence confirmation; an archive preview or a selected certificate alone is
  insufficient. A declaration does not invent a page attachment.
- Historical metadata and files are immutable. Stale material prevents fresh links
  and invalidates already issued links, but cannot revoke previously downloaded
  local copies. Original files and retained ciphertext are not deleted to roll back.
- Linux address-space limits and macOS sampled resident-memory limits have different
  operating-system semantics. A memory or timeout failure is explicit and produces
  no released file; output is never silently downsampled or truncated.
- Byte reproducibility is scoped to the recorded profile. Word/WPS re-saving or
  editing changes file bytes, and different font environments can change pagination.
  Reopening with python-docx verifies package structure, not visual Word pagination.
- PostgreSQL tests are required to validate RLS, triggers and concurrency. A passing
  renderer, CLI transport test or offline migration generation cannot establish that
  those database gates work. Tests and repeatable synthetic artifacts belong under
  `data/work/`, not documentation.

## Code

- [api/exports.py](../../server/app/api/exports.py): binding, prepare, release, history and signed downloads.
- [models/exports.py](../../server/app/models/exports.py): immutable export entities and tenant references.
- [cli/bid_cli/export.py](../../cli/bid_cli/export.py): non-interactive commands and candidate-only waiting.
- [test_exports.py](../../server/tests/test_exports.py): real API/processor release, isolation and invalidation scenarios.
- [test_export_db.py](../../server/tests/test_export_db.py): runtime-role SQL gates and two-organization table isolation.
- [test_export_renderer.py](../../server/tests/test_export_renderer.py): strict templates, preserved media and cross-process synthetic page artifacts.
- [test_export_client.py](../../server/tests/test_export_client.py): CLI schema, partial completion and hostile download transports.
- [test_export_images.py](../../server/tests/test_export_images.py): prototype gate, keep link, embedded rendition bytes and staleness after replace.
