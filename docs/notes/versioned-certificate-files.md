# Certificate PDF originals bound to immutable revisions

## Problem

A later original or declaration must not rewrite an earlier task's selected
certificate. An old revision without a file must stay explicitly empty. Reading an
original requires independent file permission, tenant isolation and byte integrity;
retaining a PDF does not authenticate the certificate or establish eligibility.

## Usage

Create the existing certificate declaration first. The new file-add command takes
complete CertificateFileCreate metadata, expected_revision and one original PDF.
It creates a new declaration revision and file together. Explicitly select that
revision for a task; repeating the same selection returns the saved snapshot.
Metadata-only updates still create revisions without files. Download an exact
revision UUID to a new path using the commands in [cli.md](../guides/cli.md#attach-certificate-pdf-originals).

## How it works

Migration 0008 adds certificate_files with NOT NULL org_id, FORCE RLS, tenant-bound
revision/creator foreign keys, one original per revision, descriptor constraints,
and an object key bound to org/certificate/revision/SHA. The restricted runtime can
only SELECT/INSERT files and cannot rewrite existing revisions or audit records.
An invoker-security trigger requires a visible parent's xmin to equal
pg_current_xact_id()::xid. Thus the current service creates the revision and file
in the same top-level transaction; a committed metadata-only revision cannot be
backfilled. The trigger respects caller RLS and leaves missing/mismatched parents
to the composite foreign key. [PostgreSQL system columns](https://www.postgresql.org/docs/16/ddl-system-columns.html)
identify xmin as the inserting transaction; [transaction functions](https://www.postgresql.org/docs/16/functions-info.html#FUNCTIONS-PG-SNAPSHOT)
document the current transaction and xid8-to-xid cast. This comparison is a
creation-time guard, not a permanent globally unique transaction identifier.

The certificate row is locked before checking expected_revision or writing the
object. Existing encrypted immutable local/S3 Storage stores unchanged original
bytes. New revision, pointer, file and identifier-only audit commit atomically;
storage failure rolls them back. A later DB commit failure may retain an encrypted
unreferenced object. No automatic object or history deletion is performed.

Task file queries join the task's fixed revision, never the resource's current
pointer. Missing file and file ID are both null with an explicit warning. Current
file lists do not fall back to old originals. Separate file read/write scopes must
intersect current human-role grants and the existing certificate read/write scope.
Old tokens gain no file scopes, and a selection scope does not grant file content.
Download links are signed for org/revision/kind for 300 seconds and still require
identity, membership and file-read permission. Server and CLI verify length/SHA;
CLI permits only the matching own-service route, no redirects, and atomically
creates a 0600 nonsymlink output without overwriting any existing file.

## Pitfalls

PDF validation requires readable, unrepaired, unencrypted 1–200-page PDF bytes,
a safe .pdf name and at most 40 MiB (or the server's lower upload limit). PyMuPDF
checks page rectangles; no PDF actions/JavaScript or external URLs are executed.
This is not a comprehensive malware scan, OCR, authenticity or metadata-matching
check. PNG/JPEG and multiple attachments are not supported.

The transaction guard deliberately fails closed for a revision inserted inside a
savepoint whose subtransaction xmin differs from the top-level transaction. The
current service uses a direct top-level transaction, verified on PostgreSQL 16 in
both native and Compose deployments. Privileged provisioning owners are trusted;
the runtime is not allowed to alter the trigger or immutable history. Retention,
restores and object cleanup require a separate operator plan; destructive downgrade
is refused. Do not treat raw-original download as bid export or evidence approval.

## Code

- Schemas: server/app/schemas/certificate_file_contracts.py
- Service: server/app/services/certificate_files.py
- API/CLI: server/app/api/main.py; cli/bid_cli/main.py; cli/bid_cli/client.py
- Migration: server/migrations/versions/0008_certificate_files.py
- Tests: server/tests/test_certificate_files.py; test_certificate_file_client.py;
  test_resource_rls.py; test_cli_snapshots.py; test_runtime_integration.py
- Real storage smoke: scripts/container_smoke.py
