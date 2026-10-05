---
kind: reference
---

# Platform credential authority

## Problem

Operator-managed vendor keys must have one revocable authority across API, workers and
standalone adapters. Tenant SQL must not read ciphertext or impersonate credential
administration. The global-table and role exception is defined by
[ADR 0006](../adr/0006-platform-credentials.md); request shapes, state transitions and
error codes are defined by the [credential contract](../plan/platform-credentials.md).

## Usage

Use the platform console Credentials page or `bid platform credential` after platform
password-and-TOTP login. Create and replace accept a key once; all responses expose only
its fingerprint and last four characters. The CLI reads secrets from owner-only regular
files, never command-line key arguments. Setup, legacy import and rotation steps are in
[the development guide](../guides/development.md#manage-platform-credentials).

## How it works

`CredentialConnections` keeps independent management and reader pools. Every checkout
checks the exact restricted login role, its privileges and role memberships. The tenant
pool has neither table access nor credential-function execution. Fixed security-definer
functions use a separate non-login owner, a fixed search path and explicit grants.
Credential audit writes and mutations commit together; the existing tenant audit writer
cannot forge `credential.*` events that authorize probes.

`ProviderSecrets` encrypts a typed envelope in the BYOK secrets domain. Its immutable
identity includes the row UUID, name, purpose, provider, canonical endpoint and secret
version. Decryption compares each field; moving a ciphertext across rows or domains
fails. Replacing a vendor key changes only the credential's revisions. Removing it erases
online ciphertext and permanently retains the name as a tombstone.

Catalog jobs pin model identity and revision, while search pins a service credential UUID.
Adapters store references and resolve again immediately before every outbound attempt,
including retries. Admission waits do not extend the lifetime of a prior key decision.
A rejected credential before transport starts closes the unsent reservation. Completed
result caches do not issue requests. No key, ciphertext or availability decision is cached
between calls, and failures never select an environment key or another provider.

Import preflight is read-only. The trusted service compares the actual decrypted key;
the final transaction locks rows and checks revisions, identities, states and conflicts.
An entire batch commits or rolls back. Legacy vendor variables are rejected by startup
even when the database already contains the same value.

Metadata probes first commit an authorization event. The reader then checks its bound
revision and short lifetime. Only the official OpenAI and Anthropic model-list operations
are allowlisted; compatible endpoints and Perplexity return `unsupported`. Endpoint
semantics follow the [OpenAI models API](https://developers.openai.com/api/reference/resources/models/methods/list)
and [Anthropic models API](https://platform.claude.com/docs/en/api/models/list).
The transport pins a public DNS address while preserving Host/TLS identity, rejects
redirects, bounds the deadline and response size, and projects a fixed outcome only.
Database locks and recent audit events enforce limits across replicas. A saved start
without a finish represents an interrupted probe, not success.

Root rotation reads current and previous secrets keys, validates every binding and uses
compare-and-swap maintenance functions. Platform rewrites append an audit in the same
transaction. BYOK history remains immutable to the runtime role; its migration-owner
maintenance function can only replace ciphertext while preserving the historical row.
The trusted maintenance caller verifies plaintext equivalence before invoking it.

## Pitfalls

- A metadata probe proves authentication only, not generation, balance or model access.
  Unsupported means that no safe probe is implemented; it is never reported as passed.
- A call already resolved and sent may complete after disable/remove. Emergency
  revocation also requires the vendor to revoke the old key.
- A removed service may be replaced under a new name and UUID. Old queued jobs must be
  resubmitted; they never silently bind to the replacement.
- Database or audit failures stop the operation. Re-read metadata after an uncertain
  commit before retrying a mutation with an expected revision.
- Root rotation is not vendor-key recovery. Old backups may retain ciphertext and need
  retired keys for recovery; online tombstones do not erase those backups.
- Do not deploy an old env-reading worker alongside this authority or destructively
  downgrade the migration. Recovery keeps the table and audit history and repairs forward.

## Code

- [0038_platform_credentials.py](../../server/migrations/versions/0038_platform_credentials.py):
  table, roles, fixed management/resolver functions and maintenance privileges.
- [platform_credentials.py](../../server/app/schemas/platform_credentials.py): wire models
  and private resolver interfaces.
- [credential_db.py](../../server/app/core/credential_db.py): independent connection boundary.
- [platform_credentials.py service](../../server/app/services/platform_credentials.py):
  write-only management, import comparison, audit and per-call resolution.
- [provider_secrets.py](../../server/app/core/provider_secrets.py): bound encryption/keyring.
- [credential_probe.py](../../server/app/providers/credential_probe.py): metadata transport.
- [admin.py](../../server/app/admin.py): provider-secrets rotation.
- [platform_credentials.py CLI](../../cli/bid_cli/platform_credentials.py): protected-file
  inputs and safe error projection.
