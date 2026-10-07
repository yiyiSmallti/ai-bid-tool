---
kind: reference
---

# Cloud certificate-page annotation

## Problem

Cropping a certificate page or boxing a value creates a new image; it must not
inherit a human's approval of other pixels. Annotating a source, attaching an
observation to a response card (响应卡), and approving that response are separate
operations. A confirmed image footer must identify the exact current decision,
without replacing the immutable candidate image or changing the canonical Evidence
hash. The source and permission boundaries are defined in the
[annotation contract](../plan/annotation.md).

## Usage

A human task owner or contributor with `evidence:annotate` opens the annotation
editor from an editable response card, explicitly selecting its extraction scope.
The source picker reads archived certificate PDF pages. The human inspects the
original, enters a crop and up to 20 boxes in source pixels, and selects “预览标注”.
This is a read-only geometry and binding preview; it does not create a candidate.
After acknowledging the exact original page and ranges, “生成标注材料” submits a
server job. The CLI uses the same Result 4.0 routes (`/v4` HTTP prefix):

```sh
bid evidence stamp --task UUID --input PLAN.json --dry-run --json
bid evidence stamp --task UUID --input PLAN.json \
  --expected-input-hash HASH --reviewed-source-png-sha256 HASH \
  --request-id UUID --wait --json
bid evidence annotation list --task UUID --card UUID --limit 50 --json
bid evidence annotation show --id UUID --json
bid evidence annotation preview --id UUID --json
```

`PLAN.json` follows `AnnotationInput`; the CLI sends no image bytes for rendering.
Successful admission is a job receipt. Successful `--wait` returns the published
candidate and the terminal job's actual zero cost. A timeout retains the job ID;
`bid job status --id UUID`, `bid job wait --id UUID`, and `bid job cancel --id UUID` are the
existing recovery commands.

After inspecting the actual candidate, “用于此响应卡” opens the existing response
editor. A human writes a visual observation, stages an `ImageEvidenceInput`, and
explicitly saves the response. Neither render success nor attachment confirms
Evidence. Current requirement confirmation (人工确认), professional-domain review,
and any complete co-sign (会签) round remain independent gates. Complete response
approval queues the confirmed rendition. Release history and preview use
`evidence annotation releases` and `evidence annotation release preview`; the
explicit `release retry` command takes `AnnotationReleaseRetry` with a failed job
and the exact Evidence/card/approval pin, and grants no new approval.

## How it works

`AnnotationInputManifest` fixes the task, extraction, editable card revision and
content hash, requirement review pin, archived source PNG and original PDF hashes,
certificate selection/revision, plan hash, and renderer/font identity. Submission
checks the reviewed PNG hash and the preflight input hash again. A request ID
identifies one exact payload; retries cannot bind it to newer source or card data.

`AnnotationRenderer.predict` invokes the same trusted screenshot-renderer binary
with `--annotation-describe`. Rust measures the fixed provenance footer and returns
only the bounded canvas and content mapping. It performs no image render, storage
write, job creation or model call. The worker uses the same binary and the
`annotation-render-v1` pipe protocol for the actual candidate. The Linux-only [process launcher](../../server/app/providers/annotation_process.py)
requires a non-root runtime and a root-owned executable and ancestors. It applies
non-increasable CPU/address-space/process/core limits, rejects inherited sockets,
and installs `no_new_privs` plus architecture-checked seccomp rules that deny network,
io_uring, fork and filesystem writes. The adapter owns wall-clock timeout, pipe
bounds, cancellation and reaping; unsupported sandbox configurations fail explicitly.
Both sides validate
PNG limits, plan/provenance hashes, renderer identity and the reversible source
mapping. The complete canvas includes padding and footer limits; it cannot scale
or clip the source to fit.

Immutable annotation requests, materials and releases carry tenant/task/source
bindings. Jobs retain the initiating human and fixed input; publication rechecks
live task authority, source selection, card revision and worker execution fencing. Each attempt records its
private output key and a delayed cleanup delivery before upload. Reconciliation waits
for the grace period, checks the immutable run fence and every committed rendition
reference, and deletes only a saved unreferenced key. A late storage upload schedules
a fresh cleanup after it actually finishes. B05 S3 requests use bounded timeouts and
no SDK retries; the job processor owns retries.
Candidate pixels retain the fixed `UNCONFIRMED` footer. The worker does not attach
materials, edit card text or create a human decision.

Existing response-card actions and co-sign services supply the approval binding.
The server normalizes the approval's `confirmed_at` to UTC before hashing; reloading
the same PostgreSQL timestamp through another connection time zone must retain the
same complete approval JSON and hash. The exact-approval comparison remains required.
Release rendering removes the candidate footer and retains already marked content;
it must not crop or draw boxes a second time. Only the fixed `CONFIRMED` footer may
change. Content-pixel hashes, the candidate mapping, canonical Evidence identity,
and the complete current requirement/domain/co-sign decision bind consumption.
Drafts use confirmed canonical Evidence. B05 image exports additionally require
its current confirmed release; stale, absent or corrupt release images cannot fall
back to candidate bytes.

The console reads source/candidate/release pages with bounded cursors, and loads
one source or candidate image at a time. Its typed source adapter uses each
material's authorized download handler, never a source UUID as a tender document
ID. It verifies the PNG hash and clears obsolete requests and images on context
changes. Unsent coordinates remain only in the active tab. Source/card conflicts
retain those coordinates, clear the previous acknowledgement and require explicit
re-preview. Read-only members can inspect authorized history but cannot submit,
attach or retry.

## Pitfalls

- Vendor sources are a separately enabled slice; profile/contract attachment pages
  require their own immutable archive contract. Neither is a certificate-page
  fallback, and declaration text is not an image source.
- A requirement awaiting B02 confirmation can have prepared material. Preparation
  does not authorize response acceptance, a confirmed release, drafting or export.
- A partial or stale co-sign round is not complete approval. The current card,
  requirement and source must still match when retrying or downloading a release.
- Geometry previews and queued receipts contain no published image. Only a
  succeeded, verified publication provides an actual candidate or release.
- The process launcher supports non-root Linux x86_64/aarch64. macOS is an explicit
  configuration failure; raw Rust protocol tests do not prove OS containment. Linux
  `RLIMIT_AS` bounds the child address space and allocation; it is not a claim of
  separately measured cgroup RSS acceptance. Deployment bundles the same root-owned
  executable and caps worker CPU through [Dockerfile](../../deploy/Dockerfile) and
  [Compose worker](../../deploy/docker-compose.yml). CI installs the protected binary
  before the Linux process/API suites in [check.yml](../../.github/workflows/check.yml).
- Mocked transport/browser scenarios prove client behavior. They do not establish
  PostgreSQL RLS, migration behavior, worker recovery or real-renderer publication.
  Repeatable acceptance artifacts belong under the ignored
  `data/work/annotation-acceptance/`, never under `docs/`.

## Code

- [annotation_contracts.py](../../server/app/schemas/annotation_contracts.py):
  `AnnotationInput`, input manifests, preflight, candidate and release contracts.
- [annotations.py](../../server/app/models/annotations.py): immutable request,
  material and release storage; [0053_cloud_annotations.py](../../server/migrations/versions/0053_cloud_annotations.py):
  RLS, composite bindings and mutation guards.
- [annotations.py](../../server/app/services/annotations.py): `preflight`, `submit`,
  `approval_binding`, `enqueue_releases`, `retry_release`, `release_for_evidence`.
- [annotation_jobs.py](../../server/app/services/annotation_jobs.py): cancellable
  attempts, resource/lease rechecks and immutable publication;
  [annotation_objects.py](../../server/app/services/annotation_objects.py): durable
  staging and age-gated orphan reconciliation.
- [annotation_process.py](../../server/app/providers/annotation_process.py): fixed
  Linux-only process launcher; [test_annotation_process.py](../../server/tests/test_annotation_process.py):
  fail-closed host checks and non-root Linux process acceptance.
- [annotation_renderer.py](../../server/app/providers/annotation_renderer.py):
  `AnnotationRenderer.predict`, rendering/receipt verification and approved content.
- [annotation.rs](../../stamp/src/annotation.rs): fixed profiles and description/render
  protocol; [main.rs](../../stamp/src/main.rs): same-binary protocol dispatch.
- [annotation.py](../../cli/bid_cli/annotation.py) and
  [schema.py](../../cli/bid_cli/schema.py): command registration, wait and discovery.
- [OrgAnnotation.vue](../../web/src/views/OrgAnnotation.vue),
  [annotation.js](../../web/src/annotation.js), and
  [CardEditor.vue](../../web/src/components/CardEditor.vue): bounded editor, typed
  image source and explicit human evidence attachment.
- [test_annotation_cli.py](../../server/tests/test_annotation_cli.py),
  [annotation.spec.js](../../web/e2e/annotation.spec.js), and
  [annotation-fixture.js](../../web/e2e/annotation-fixture.js): transport snapshots and
  mocked console acceptance with failure scenarios.
