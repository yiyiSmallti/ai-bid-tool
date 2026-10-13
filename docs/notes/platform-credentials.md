---
kind: reference
---

# Local service credential settings

## Boundary

Service credentials are encrypted installation settings under
[ADR 0012](../adr/0012-single-user-installation.md). Model configuration keeps its
own revision-bound keys as described in [provider configuration](provider-config.md).
The human manages service credentials using ordinary access-key authentication
and `credential:manage`; tokens and agents cannot use that scope.

## Usage

`bid platform credential` exposes list, show, create, replace, set-active, remove,
test, and import-env. Routes remain `/platform/credentials` and return Result.
The authoritative inputs and projections are
[platform_credentials.py](../../server/app/schemas/platform_credentials.py).
Credential purposes are `standalone_llm`, `vendor_search`, `clef_workers_ai`, and
`clef_gateway`; provider and endpoint must match the purpose. Each service has
at most one nonremoved credential. A name is immutable and reserved after removal.

Writes require an expected revision after creation. Replacement changes secret
version and revision; activation changes revision after validating the bound
secret. Removal is terminal and removes the stored ciphertext. Safe projections
contain routing identity, state, revision, secret version, fingerprint, suffix,
timestamps, and service consumers; they contain no raw key or ciphertext.

CLI input files contain no secrets. The CLI reads secrets from the supported
environment name or caller-owned mode-0600 key file and submits them separately.
Import manifests explicitly map supported environment names to service purposes.
Imports are batch atomic; a dry run validates without writes. Replays skip only
identical routing identity, state, and secret. Conflicts fail the batch.

## Resolution

[PlatformCredentialResolver](../../server/app/services/platform_credentials.py)
uses the runtime `Database`. Readiness exposes safe presence and state without
decryption. Each real call resolves committed state anew; no cached plaintext,
environment fallback, or alternate credential may bypass disabled or removed
state. Cipher envelopes bind UUID, secret version, name, purpose, provider, and
endpoint. The independent `BID_SECRETS_KEY` decrypts only a matching envelope.

The fixed baseline functions in
[00_functions_core.sql](../../server/migrations/baseline/00_functions_core.sql)
implement management, service resolution, and probe resolution. Activation and
import checks use `platform_credential_resolve_human`, which requires the human
actor, expected revision, and explicit `activate` or `import` purpose. It permits
a disabled credential for that check and rejects removed credentials.

Authentication probes admit one revision-bound receipt and a bounded,
allowlisted metadata request. They do not submit business content or prove
model capabilities. A disabled credential may be tested; a removed credential
may not. Completion consumes the receipt, and rate limits prevent repeated
unbounded checks. Errors use fixed codes and text without vendor response bodies,
request IDs, headers, or secrets.

## Root-key rotation

`bid-admin rotate-encryption --scope provider-secrets` validates and rewraps
service ciphertext and every historical provider revision. It preserves envelope
bindings, vendor values, public fingerprints, secret versions, and revisions.
The owner performs fixed compare-and-swap rewrap functions; the runtime cannot
update revision ciphertext. Reports expose counts and failure classification.
A repeated successful pass rewrites nothing. Retain previous root keys until all
rows validate under the current key and backup recovery requirements are met.

## Implementation

- [Service and resolver](../../server/app/services/platform_credentials.py),
  [cipher](../../server/app/core/provider_secrets.py), and
  [probe adapter](../../server/app/providers/credential_probe.py).
- [HTTP routes](../../server/app/api/platform_credentials.py),
  [CLI commands](../../cli/bid_cli/platform_credentials.py), and
  [rotation command](../../server/app/admin.py).
- [API acceptance](../../server/tests/test_platform_credentials_api.py),
  [DB lifecycle](../../server/tests/test_platform_credentials_db.py), and
  [rotation acceptance](../../server/tests/test_platform_credentials_rotation.py).
