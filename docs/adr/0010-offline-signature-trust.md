---
kind: adr
---

# 0010 Offline PDF signature validation and platform trust anchors

Date: 2026-10-08. Status: **accepted.**

## Context

The [bid-review plan](../plan/bid-review.md#signature-and-seal-completeness)
requires local validation of existing PDF signatures, with independent cryptographic,
revision-coverage, certificate-trust and revocation observations. This extends
inspection of uploaded bids; it creates no signatures and submits nothing to a
procurement platform. Trust must come from explicitly admitted public CA certificates,
rather than a certificate embedded by a bid signer or an outbound network lookup.
The [tenant-isolation rule](../../agent.md#hard-rules-must-never-be-violated)
requires an explicit exception for that shared public trust store.

## Decision

- Approve `platform_trust_anchors` as the fifth additional global-table exception,
  without tenant `org_id` or RLS. Store only public CA DER, SHA-256 fingerprint,
  label, subject/issuer, validity dates, enabled state, revision and operator creation
  or disable metadata. It contains no bid signer certificates, org content, private
  keys, authentication factors or credentials.
- Authenticate management with existing platform password-and-TOTP sessions. Parse
  a bounded single PEM/DER certificate locally and require a supported CA certificate
  before preview or admission. Duplicate fingerprints return the existing record
  without changing its state or adding a second audit. Retain at most 128 admitted
  records, including disabled records. Disabling preserves public
  certificate bytes; no re-enable, replacement or deletion is provided.
- Use the dedicated `bid_trust_anchors_fn` owner with `NOLOGIN NOSUPERUSER
  NOBYPASSRLS NOINHERIT`. Grant only anchor read/insert, management-column update and
  append-only platform audit insertion. `bid_app` has no direct table privileges and
  executes only fixed metadata list, enabled-snapshot, add and disable functions.
  Fix `search_path`, qualify table names, revoke PUBLIC execution and prohibit dynamic
  SQL. Actor text and `SET app.*` are never platform authentication. Successful
  state changes and their audit records commit in the same transaction; audit details
  contain only public fingerprint and revision.
- Pin the enabled store and its SHA-256 digest in an immutable tenant preparation
  row at admission. Serialize admission with store writes and reject a changed
  snapshot. A retry reads the preparation's pinned snapshot, never the current
  enabled store. Public CA DER is retained in that tenant snapshot so later disables
  cannot alter the historical result.
- Record one encrypted digital-validation payload for each prepared original,
  retaining independent results for each signature and the final revision. Bind it
  through composite tenant/task/submission/preparation/document keys and the pinned
  trust digest. Keep certificate subjects, issuers and signature field names inside
  ciphertext. Restricted human source permissions govern decrypted presentation.
- Signing-clause candidates bind through the exact page identity and retain original
  quote and offsets inside ciphertext. Their applicability is always `unknown`;
  extraction never creates confirmed signing requirements or evidence. Worker lease,
  live human actor, RLS, publication completeness and append-only guards apply to
  both kinds of observations. Owner-only ciphertext rewrapping preserves all other
  fields; the encryption rotation registry includes both payload columns.
- Validation is offline. The approved GM dependency supports the selected local
  profile; unsupported algorithms or formats remain unknown/unsupported. Missing
  trusted timestamp or revocation proof remains visible. An earlier valid signature
  cannot validate a modified final revision, and no visual observation may override
  a cryptographic failure. Make no model calls and accrue no model cost.
- Downgrade must preserve public certificates, pinned snapshots, ciphertext and
  audit history. Removal requires a separate explicit recovery or retention plan.

## Tradeoffs and consequences

A shared public trust store avoids an implicit trust decision per org and gives
operators a visible admission boundary. It does not establish signer identity from a
visible seal, guarantee current revocation status or confirm that a tender's signing
requirements have been met. Disabled anchors affect future preparations; historical
preparations remain reproducible using the snapshot admitted with their input hash.

The database function boundary restricts direct SQL privileges. It relies on the
application's platform authentication and certificate parsing and does not isolate a
compromised application process. Encrypted signer details remain tenant business data
and follow ordinary tenant key rotation and restricted source-access rules.
