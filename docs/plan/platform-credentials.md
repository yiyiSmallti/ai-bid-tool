---
kind: plan
---

# Local service credential contract

## Delivery boundary

[ADR 0012](../adr/0012-single-user-installation.md) makes service credentials,
Clef, and offline trust ordinary installation settings managed by the human.
[Credential settings](../notes/platform-credentials.md) owns lifecycle,
resolution, and root-key recovery mechanisms. Runtime Pydantic models in
[platform_credentials.py](../../server/app/schemas/platform_credentials.py) are
authoritative; [contract examples](platform-credentials/platform_credentials_contracts.py)
import those models rather than defining a second schema.

## Requirements

- Use ordinary access-key/session request context and the runtime `Database`.
  Require human `credential:manage`; no API token or agent may receive it.
- Retain `/platform/credentials` resource paths and CLI command names with the
  shared Result envelope; names do not define a separate authentication system.
- Support `standalone_llm`, `vendor_search`, `clef_workers_ai`, and `clef_gateway`.
  Keep routing identity immutable and one nonremoved credential per purpose.
- Bound and separately supply secret inputs. CLI manifests and read responses
  contain no plaintext or ciphertext; errors do not reflect submitted properties.
- Enforce expected revisions, terminal removal, secret-version changes on
  replacement, purpose-bound endpoints, and atomic imports. A dry run writes
  nothing; replay skips only an identical state and bound secret.
- Resolve service credentials fresh for each dispatch. Readiness checks do not
  decrypt. Missing, disabled, removed, mismatched, or unreadable credentials fail
  explicitly without substituting another key or model.
- Activation/import verification uses `platform_credential_resolve_human` with
  the expected revision and explicit operation purpose. Preserve the database
  human gate while allowing a disabled credential to be checked.
- Authentication probes use bounded metadata calls and consumed revision-bound
  receipts. Probes do not submit tender content or assert model capabilities.
- Root-key rotation rewraps service credentials and all saved provider revisions,
  preserves envelope bindings and visible versions, and is resumable. Only the
  owner invokes fixed ciphertext compare-and-swap updates.

## Acceptance

[API lifecycle tests](../../server/tests/test_platform_credentials_api.py),
[database lifecycle tests](../../server/tests/test_platform_credentials_db.py),
[CLI boundary tests](../../server/tests/test_platform_credentials_cli.py),
[runtime resolution tests](../../server/tests/test_platform_credential_runtime.py),
and [rotation tests](../../server/tests/test_platform_credentials_rotation.py)
cover revision races, purpose/name conflicts, terminal removal, import atomicity
and replay, disabled-key checks, consumed probes, redaction, encryption binding,
and preservation of historical provider keys. Fixtures create one installation
and use the ordinary human bearer key; agent/token denials remain required.

The [Clef API](../../server/app/api/platform_clef.py) and
[trust-anchor API](../../server/app/api/platform_trust_anchors.py) must use
`clef:manage` and `trust-anchor:manage` respectively, with the same identity and
human checks. Offline signature preparation must retain its pinned CA snapshot.
Validation artifacts belong under `data/work`, outside documentation.
