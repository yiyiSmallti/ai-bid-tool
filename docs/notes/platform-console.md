---
kind: reference
---

# Human-managed installation settings

## Identity and authority

The installation access key or its console session acts as the human under
[ADR 0012](../adr/0012-single-user-installation.md). Settings use the same ordinary
request context and runtime database as business routes. `HUMAN_ONLY_SCOPES` in
[auth.py](../../server/app/services/auth.py) includes `provider:write`,
`credential:manage`, `clef:manage`, and `trust-anchor:manage`. API tokens and
agents cannot receive or exercise these scopes. Database actor checks preserve
the same human boundary for writes.

## Resources

The resource paths retain `/platform` for compatibility; they carry no separate
login or identity authority. They accept the ordinary bearer access key or
console session. CLI commands read the ordinary access-key/token configuration
in [Client](../../cli/bid_cli/client.py).

| Settings | Routes | Required scope | Contract |
| --- | --- | --- | --- |
| Model | `/providers`, `/management/providers` | `provider:read`; human `provider:write` for saves/tests | [Model settings](model-settings.md) |
| Service credential | `/platform/credentials` | Human `credential:manage` | [Credential settings](platform-credentials.md) |
| Clef | `/platform/clef` | Human `clef:manage` | [Clef contracts](../../server/app/schemas/clef.py) |
| Offline CA trust | `/platform/trust-anchors` | Human `trust-anchor:manage` | [Signature trust](bid-review.md) |

Clef stores separate Workers AI and gateway credential references and revisions.
Its check admits a bounded gateway metadata request and records the checked
configuration revision. Offline CA management accepts supported public CA
certificates, exposes metadata, and pins enabled snapshots for preparation.
Disabling an anchor changes future snapshots without rewriting saved evidence.

## Error and secret boundaries

Settings return the shared seven-field Result envelope and `Cache-Control:
no-store`. Secret inputs and unknown credential validation properties cannot be
reflected in errors. CLI projections validate paths, commands, status, and
allowlisted response schemas before output. Management operations do not prove
vendor availability; only explicit checks produce receipts.

## Implementation

[main.py](../../server/app/api/main.py) mounts ordinary-context settings routes:
[credentials](../../server/app/api/platform_credentials.py),
[Clef](../../server/app/api/platform_clef.py), and
[trust anchors](../../server/app/api/platform_trust_anchors.py).
