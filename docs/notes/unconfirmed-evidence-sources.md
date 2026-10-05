# Unconfirmed fixed-PDF sources

## Problem

A retained original PDF does not establish authenticity, eligibility or a confirmed
Evidence decision. A genuine source page must stay bound to the task's fixed
revision while current declarations and task selections evolve.

## Usage

Use the three `evidence source` commands in the CLI guide with an existing active
same-task certificate snapshot that has a PDF original. The only input is that
snapshot ID and a1-based page. An archive is always unconfirmed_source with null
confirmed_by and false eligible_for_draft_export. No confirmation/export endpoint
or permission is added. A replaced source is available through --history and its
same authenticated preview ID; it is never inherited by a new selection.

## How it works

Validate scope intersections and active membership before reading any parent. Join
immutable snapshot/revision/file identities and check original length/SHA/
descriptor. A disposable child validates the original PDF and renders the actual
whole page at 150 DPI RGB without alpha, preserving rotation, using PyMuPDF.
Check projected and real dimensions against the shared
[PDF raster budget](pdf-parsing.md#how-it-works), PNG byte limit (40MiB
or lower configured limit), and actual output. The shared
[PDF process limits](pdf-parsing.md#how-it-works) terminate and reap a child that
exceeds its deadline; only a successful render reaches storage/DB work.
Reading and rendering run without the task lock, at most two renders per process.
The slot is released after the child is reaped, and a full pool fails with retryable
`source_render_busy`. The task is then locked in the same order as
certificate selection and the snapshot and duplicate checks are repeated, so a
selection replaced during rendering fails with `inactive_snapshot` and writes nothing.

Store encrypted immutable bytes under org/{org_id}/evidence-source/{source_id}/SHA.png
using existing Storage. Save archive/audit atomically; commit precedes response.
Task locking plus unique(org,snapshot,page,profile) serializes duplicate requests.
A repeated source has the same ID and no object write or new creation audit; one
already archived before the request is not read or rendered either. New page/new
snapshot is independent. UTC output normalizes database timezone presentation.
Active status is read from the original snapshot, not copied.

Migration0009 adds NOTNULL org_id/FORCERLS and exact composite task/snapshot/certificate/
revision/file/creator FKs. An invoker page-bounds trigger follows caller RLS; SQL
checks enforce PNG metadata limits and permanent unconfirmed state. Runtime access
is SELECT/INSERT only. Old-table payloads are unchanged by the additive migration.
Source read/write intersects all task:read/certificate:read/certificate:file:read
and live role grants. Only admin/bidder/technical create; viewers read. Old tokens
obtain neither added scope, and no token can confirm/export.

A300second org/source/kind signed URL still requires valid authenticated identity,
membership and scopes. Link Result.items contains exactly one source archive so
the CLI can validate its PNG descriptor. Final CLI receipt has empty items. Reject
foreign/redirect paths, bound streamed length, verify hash/PNG/dimensions before
atomic0600 save, and never overwrite existing output or follow symlinks.

## Pitfalls

No OCR, model analysis, image fabrication, crop, watermark, annotation, quote,
matching or eligibility conclusion occurs. SQL parent binding and decoded PNG
validity cannot prove that an uploaded certificate itself is authentic. This is a
source archive, not a full Evidence/Card/confirmation chain or bid export.

The rendering deadline covers the PDF child, not the original storage read and
final write. Child timeout, resource exhaustion or crash returns non-retryable
`pdf_resource_limits`; no partial image is archived. Storage succeeds before
DB commit; a failed commit may leave an encrypted unreferenced object, retained
for an explicitly designed later cleanup policy. Never delete historical source
or original data to roll back; revert application behavior while retaining0009.

When verifying a browser download, check the saved file at its exact path; a
download link alone is insufficient.

## Code

- server/app/schemas/evidence_source_contracts.py
- server/app/services/evidence_sources.py
- server/app/models/entities.py:EvidenceSource
- server/migrations/versions/0009_evidence_sources.py
- server/app/api/resources.py:evidence_source_* routes
- cli/bid_cli/client.py:download_evidence_source and CLI/source schema entries
- server/tests/test_evidence_sources.py, test_evidence_source_client.py,
  test_resource_rls.py, test_cli_snapshots.py, test_runtime_integration.py
- scripts/container_smoke.py
