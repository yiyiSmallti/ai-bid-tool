---
kind: reference
---

# Local model settings

## Boundary

The model settings page reads the installation's current and historical
`llm_extract` configuration without decrypting credentials or calling a vendor.
The installation and identity boundary is defined by
[ADR 0012](../adr/0012-single-user-installation.md). Provider writes and execution
follow [provider configuration](provider-config.md).

## Usage

`provider show`, `provider revision show --id UUID`, and
`provider history-page` return Result 5.0 metadata. A human or scoped API token
with `provider:read` can read it. Saving and testing require the human identity
and `provider:write`; API tokens and agent identities cannot receive that scope.
An installation without a saved revision returns `configured=false` and no
current configuration. A configured revision has `connection_status=not_checked`
until an explicit test supplies a receipt; presence of a key proves no connection.

Save appends an immutable revision with `expected_revision`. The page rereads
metadata after success. Testing uses a cost preflight and a separate start
against the saved revision. The test receipt identifies the configuration ID,
so exact revision detail remains meaningful after a later save. Preflight
estimates and actual recorded costs have separate meanings.

## Projection and pagination

[Metadata schemas](../../server/app/schemas/management_providers.py) allowlist
configuration, provider/model identity, timestamps, and reasoning choices.
They exclude raw keys, ciphertext, fingerprints, key suffixes, and credential
identifiers. Reasoning request options reject credential and transport fields
recursively and have structural bounds. Unsafe stored data fails projection
without exposing validation input.

History orders by revision and UUID descending. Reads use `limit + 1` keysets,
complete-envelope byte limits, and encrypted cursors bound to the live actor,
authority, purpose, and filters. Exact revision lookup uses the saved UUID.
Read routes perform no decryption, vendor request, or per-row business audit.

A fresh password control supplies a key once, clears before the request waits,
and never enters URLs, persistent storage, or telemetry. The server may reuse
an existing key only when provider and endpoint remain unchanged. Logout or
lost authority must clear forms, cancel reads, and discard late responses.

## Implementation

- [HTTP reads](../../server/app/api/management_providers.py) and
  [bounded read service](../../server/app/services/management_providers.py).
- [Write and test routes](../../server/app/api/providers.py) and
  [configuration service](../../server/app/services/provider_configs.py).
- [CLI reads](../../cli/bid_cli/management_providers.py).
- [DB/API acceptance](../../server/tests/test_model_settings_api.py),
  [scale acceptance](../../server/tests/test_model_settings_scale.py), and
  [CLI acceptance](../../server/tests/test_management_providers_cli.py).
