# Private DOCX originals and retained task revisions

## Problem

A template's file and declarations can change after a task selects it. Each task
must retain the exact original bytes and declared metadata, while human-only file access
and download-path restrictions remain enforced. Upload success must not imply chapter matching
or export compatibility.

## Usage

The human supplies complete TemplateCreate metadata and a readable DOCX. Each update
supplies complete TemplateUpdate metadata, expected_revision and another DOCX.
Select an integer revision, or resolve current under lock. Explicit replacement
retains old history; duplicates return one saved selection. Resource lists provide
revision UUIDs for downloading to a new nonsymlink path. See
[cli.md](../guides/cli.md#manage-docx-templates) for the commands. Unknown project types and chapters stay null; declarations are warned.

## How it works

`templates`, `template_revisions` and `task_templates` use parent foreign keys
and a deferred current pointer in the authoritative baseline schema. The
runtime cannot delete rows or rewrite revisions/audit. SQL binds the storage key to
template/revision and file SHA-256, and constrains descriptor size/media type.
Selection share-locks the current template and locks the task, fixing the revision
and descriptor. Active slots allow multiple templates/lots; replacements retain
history. Audit and DB writes commit before a successful response.

Validation bounds original bytes to 40 MiB (or the server's lower limit), declared
ZIP expansion to 100 MiB, and checks safe members, macros and readable DOCX. It
executes nothing and fetches no external relations. Existing Storage encrypts each
immutable object with a resource/revision-bound key. A stale update fails before object writing.
DB/object storage are not a distributed transaction: commit failure rolls back DB
and audit but retains any newly stored encrypted unreferenced object.

The signed link lasts 300 seconds and still requires identity, `template:read` and human-only `template:file:read`. The server verifies decrypted hash/length. The CLI
accepts only the fixed relative same-service path, follows no redirects, bounds
stream length, verifies SHA-256 and writes a private temporary file. fsync plus
atomic no-overwrite linking creates the new output only after full verification.
Errors remove temporary files; existing paths and symlinked parents remain intact.

Separate `template:read`, `template:write` and `task:template` scopes govern
metadata and selection. Task lists also need `task:read`. Human-only file reads,
binding decisions and export confirmation cannot be granted to tokens.

## Pitfalls

This is no comprehensive malware scan, chapter inference, public sharing, generated
export or format-compliance certification. Original files can contain confidential
content; download only to an authorized local destination. Never log contents,
signature links or credentials. No automatic object cleanup or destructive
downgrade is permitted. Tests use synthetic documents.

## Code

- server/app/schemas/template_contracts.py and services/template_files.py.
- services/templates.py, services/versioned.py, api/resources.py, models/entities.py and services/auth.py.
- server/migrations/baseline/ and cli/bid_cli/client.py/main.py/schema.py.
- test_templates.py, test_template_client.py, test_resource_rls.py, runtime and
  container integration.
