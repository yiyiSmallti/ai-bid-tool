# Trusted sandbox fetching

## Problem

A page inside the [capture sandbox](../plan/sandbox.md#按用途固定网络策略) may
forge resource requests, redirects, response metadata, or DNS answers. The trusted
proxy must independently authorize every request, keep transport and decoding
bounded, and produce its own provenance. Browser interception is not an egress
security boundary.

## Usage

The operator supplies a read-only policy file and a local quota-ledger path. Both
paths are trusted node configuration, never page or API request arguments. The
policy file is a JSON object with `policies` and `revoked_revisions` arrays. Each
policy contains an immutable `revision`, explicit `main_urls`, and `rules`. Each
rule contains one complete HTTPS `url` and a `methods` array limited to `GET` and
`HEAD`. Every main URL must also appear in the rules. The optional
`active_revisions` object maps a main URL to its revision for new submissions:

```json
{"active_revisions": {"https://vendor.example/model": "vendor-v2"}}
```

Keys are canonicalized by the same URL parser; duplicate canonical keys are
invalid.

A development node may instead define one revision with `open_public_https: true`
and no URLs. It admits every canonical public HTTPS URL as a main URL or resource.
The file is valid only while the node environment sets
`BID_SANDBOX_DEV_OPEN_EGRESS=1`; otherwise every load fails with `policy_invalid`.
An exact policy match still takes precedence over the open revision. Each mapped revision must exist, list that URL as a main URL, and not be
revoked. Without an explicit selection, exactly one non-revoked policy must match.
This selector is operator configuration and has no client parameter or edit API.

`PolicySource(path).select(source_url)` admits an explicitly listed main URL and
returns the operator-selected `FetchPolicy`. The trusted caller binds the selected product
revision and policy revision before constructing
`FetchBroker(policy_source, policy_revision, org_id, quota=SQLiteFetchQuota(path))`.
`bundle=True` additionally retains allowed response bodies. Main URL selection
belongs to this trusted caller; subresource fetching uses the same broker and
cannot expand its rules.

`await broker.fetch(url, method="GET")` returns `FetchPayload` with `url`, `status`,
`headers`, and decoded `body`. It accepts no supplied headers, cookies, request
body, credentials, proxy settings, or TLS options. `broker.close()` revokes further
requests. `FetchDenied.code` is the only diagnostic value that callers should log;
failures must terminate the capture rather than silently omit resources.

`broker.receipts` and `broker.manifest_bytes()` provide trusted request records.
`broker.denials` separately records every rejected fetch attempt, including failures
before a socket opens: URL SHA-256, fixed reason code, timestamp, request ordinal,
and optional redirect-parent ordinal. Denials contain no raw URL or exception text.
`broker.entry_receipt(source_url)` establishes one complete, unambiguous GET
redirect chain from the canonical selected main URL to a successful HTTP 200
response. It can be called after closing the broker and never reopens the run.
Publication must use that receipt to bind original PDF length, hash, and type; a
receipt for an unrelated allowed asset cannot establish the selected source.
Requests and denials share monotonically assigned admission ordinals; concurrent
completion does not change manifest ordering. Response records name their matching
archive entry, including empty HEAD responses and redirect bodies.
`broker.archive_bytes()` produces a bounded ZIP with `manifest.json` and fixed
`response-NNN.body` entries when bundle capture was selected. Manifest URLs and
allowed response headers are sensitive artifact content: persist them only in the
authorized encrypted artifact pipeline. They are not audit or ordinary log fields.

## How it works

URL canonicalization rejects credentials, fragments, controls, ambiguous numeric
hosts, path traversal, encoded separators, double encoding, and credential-like
query names. Exact canonical URL matching constrains origin, port, path, and the
complete fixed query. The implementation supports explicit URL lists, a more
restrictive subset of the contract's path ranges; wildcard origins and dynamic
query values have no representation.

The policy file is reread before every request, after DNS and quota acquisition,
and before accepting a response. An existing broker retains the original revision
hash. Revocation, deletion, unreadable configuration, or mutation of that revision
fails closed. Operators publish edits by atomic file replacement and issue a new
revision for changed rules. Retaining an old revision while changing
`active_revisions` leaves existing runs and authorized downloads bound to their
unchanged policy hash. Adding a revision does not implicitly revoke any previous
revision; `revoked_revisions` explicitly stops its use. There is no public policy
editor.

All resolved addresses must be globally routable unicast addresses. Mixed public
and private answers are denied. An open development policy additionally accepts
answers in `198.18.0.0/15`, the range a local fake-IP proxy uses before routing
the connection by hostname; exact policies never do. The transport connects to a validated numeric IP,
sets the original `Host` and TLS SNI, verifies the certificate, disables environment
proxies, and creates no reusable connection pool. HTTP transport performs no retry
or redirect on its own. Every redirected URL passes the same policy and DNS path.
Network enforcement on the dedicated proxy node must independently deny business,
control-plane, host, and metadata destinations.

Raw response streams are counted before decompression; decoded output is separately
counted against response, HTML, and shared run budgets. Only identity, gzip, and
zlib-wrapped deflate are supported. Unknown or stacked encodings, concatenated
compressed streams, incomplete compressed data, and unsupported MIME types fail
closed. Only approved response headers survive; there is no cookie jar. Explicit
password forms and common CAPTCHA markers cause failure. Those markers are
conservative detection, not a general proof that a page is authentic or useful.

`SQLiteFetchQuota` holds only hashed organization/origin identifiers, timestamps,
and temporary lease IDs. It is node control state, not a replacement for PostgreSQL
business records or tenant RLS. SQLite transactions enforce a sliding organization
request window and origin connection leases across processes and restarts. The
ledger parent directory must already exist and be private; every proxy process on
the dedicated singleton proxy node must use the same local ledger. A crashed
request lease expires after the bounded request deadline. Per-run request, byte,
and concurrency counts remain in the single trusted broker instance; new attempts
remain subject to the persistent organization window.

## Pitfalls

- A node-local ledger does not coordinate multiple independently configured proxy
  nodes. Horizontal proxy deployment requires a shared atomic quota backend before
  enabling additional nodes.
- The file and its parent directory must remain operator-controlled. The policy
  reader cannot establish filesystem ownership or deployment trust by itself.
- Unsupported assets or authentication challenges make capture fail. They do not
  authorize automatic policy expansion, login, or a relaxed TLS/network path.
- Request manifests describe proxy-observed bytes. Rendered DOM and screenshots
  still require the separate sandbox validation and provenance pipeline.
- Test transport injection is an internal constructor seam. Public configuration
  must never let a caller replace the resolver, quota, limits, or transport.
- Mock transport checks establish policy and pinned-request construction behavior;
  they do not establish actual TLS handshake, network namespace, firewall, or
  gVisor isolation. Those require the contract's real runtime attack acceptance.

## Code

- [Broker, policy reader, and quota ledger](../../server/app/providers/sandbox_fetch.py)
- [Synthetic fetch failure gates](../../server/tests/test_sandbox_fetch.py)
- [Runtime and resource acceptance](../plan/sandbox.md#批准后的端到端验收)
