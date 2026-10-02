# Tenant isolation

## Problem

A missing application filter must never expose another organization's records.
Identity can be global; organization membership and business records cannot be.

## Usage

Use `Database.transaction(org_id)` for every tenant operation. HTTP handlers only
receive their transaction after a valid identity and active membership are checked.
The candidate tenant transaction used to check membership cannot reach a handler
when the check fails. Background jobs carry the same organization UUID.

## How it works

The initial migration enables and **forces** RLS on all nine organization tables.
`set_config('app.current_org', ..., true)` is transaction-local and resets when
connections return to the pool. Missing context denies all organization rows.
The runtime role is neither superuser nor BYPASSRLS and owns no application table.
Startup rejects a privileged or table-owning runtime role. Global `users` is the
documented login-identity exception; the runtime has SELECT permission only.
Composite foreign keys bind children and parents to the same organization.
Unknown and inaccessible resources return the same 404. Storage keys use
`org/<uuid>/`; local resolution rejects traversal and foreign tenant prefixes.
Download links expire after 300 seconds and additionally require membership.
Original files are encrypted before local or S3 persistence with authenticated
Fernet encryption, binding ciphertext to the exact tenant object key. Reads reject
tampering, another encryption key or a copied object from another tenant. Duplicate
uploads compare decrypted content; S3 uses conditional creation rather than overwrite.

## Pitfalls

Migration/seed credentials are separate and must never reach the HTTP server or
worker. RLS does not protect TRUNCATE, so it is not granted to the runtime role.
Never use SQLite or a file database to imply equivalent isolation. Procrastinate
queue tables are internal dispatch metadata containing organization/job IDs; the
business job and results live in the RLS-protected `jobs` table. They are never
queried through a user-facing route. Worker context must be restored before any
business read/write. Synthetic test providers live in tests, never production.
Keep the deployment encryption key available and backed up through the operator's
existing secret workflow. Automatic key rotation or plaintext file conversion is
not implemented. Unsupported plaintext files fail explicitly; do not overwrite
existing documents to conceal a migration problem.

## Code

`server/app/core/db.py`, `services/auth.py`, `models/entities.py`,
`migrations/versions/0001_tenant_foundation.py`, `tests/test_rls.py`,
`tests/test_api.py` and `tests/test_job_boundaries.py`.

Reference: https://www.postgresql.org/docs/16/ddl-rowsecurity.html
