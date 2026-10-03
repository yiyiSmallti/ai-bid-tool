# Isolated sandbox execution

## Problem

Generated HTML and public vendor content must not inherit the API process's
filesystem, credentials, browser session or business permissions. A successful
capture must also remain bound to the authorized input and execution attempt.
The product boundary and limits are defined in the
[sandbox contract](../plan/sandbox.md). Evidence confirmation belongs to the
[screenshot contract](../plan/screenshots.md), not to a sandbox completion.

## Usage

Use `bid sandbox render` with an existing HTML file and `PrototypeSpec`, or
`bid sandbox capture` with `VendorSpec`. The CLI performs a read-only preflight
and submits its request hash. Both require a successful extraction and an active
fixed task selection. List and show return metadata; download obtains a short
signed link, authenticates again, verifies length and SHA-256, and atomically
creates a new file. Runtime and policy preparation steps are in the
[sandbox runtime guide](../guides/sandbox-runtime.md).

Python integrations use the typed BrowserProvider in
[browser.py](../../server/app/providers/browser.py). Business callers should
submit through [sandbox.submit](../../server/app/services/sandbox.py) to retain
authorization, idempotency, jobs, storage and audit. The BrowserProvider's
`render_prototype` and `capture` methods accept only trusted RunDescriptor values;
there is no public arbitrary URL, script, command, image or mount parameter.

## How it works

The request hash binds explicit spec defaults, org, task, initiating identity,
effective scopes, fixed selection and input hashes, runtime profile and network
policy digest. Original HTML is stored as tenant-key-bound ciphertext. Vendor
URLs come from the fixed product revision and are separately encrypted. Neither
HTML nor exact URLs are exposed in job results or audit details.

Migration [0022](../../server/migrations/versions/0022_sandbox.py) adds the input,
run, attempt, artifact and fetch-receipt relations. Composite foreign keys bind
org, task, document, extraction, selection, run and attempt. Every relation has
FORCE RLS and an explicit WITH CHECK policy. Inputs, runs, artifacts and receipts
are append-only. Attempts allow a single terminal transition. A transaction stamp
seals artifact and receipt insertion with that transition, and deferred checks
require a complete artifact set and the current successful job attempt.

The processor claims the same `Job.run_id` lease used by existing jobs. A live
watcher rechecks the initiating member, token intersection, selection, policy and
lease while execution runs. Publication locks the task and rechecks those gates.
An old or cancelled attempt can retain resource accounting and failure audit but
cannot publish artifacts. Capacity waits use a separate bounded dispatch retry
policy, allowing the processor to record queue expiry without consuming a
business execution attempt. Dry-runs create neither jobs nor storage objects.

The execution node owns the Docker control socket. A separate service identity
receives only bounded messages through the supervisor socket. Each attempt uses
separate render and validation containers with no network. The vendor renderer
can request only brokered fetches; the
[fetch mechanism](sandbox-fetch.md) defines the independent network policy.
Neither container has business storage credentials. Original PDF bytes must match
the trusted entry response receipt, not just a sandbox-declared file header.

The supervisor persists instance identities before execution, enforces a shared
deadline and resource budget, kills complete instances, and confirms removal
before returning candidates. Retries receive reduced remaining budgets. Missing
terminal resource accounting consumes the conservative remaining bound. Cleanup
recovery adds `sandbox.reaped` audit without rewriting historical terminal
metrics; it does not restore an old attempt's right to publish.

The trusted service recalculates all byte hashes, builds the internal provenance
manifest, writes immutable encrypted candidates, and publishes their descriptors
with completion audit in one database transaction. The provenance manifest
hashes other artifacts; its own hash lives in the database descriptor. Supplied
HTML has `origin=prototype` and unknown generating model/provider values. This
metadata never becomes a watermark, footer or visible prototype label.

Downloads recheck current authorization, selection, policy and the completed
attempt. Ciphertext and plaintext reads are bounded before integrity checks.
Untrusted HTML, PDF and bundles use attachment, octet-stream, nosniff and a
restrictive CSP; PNGs remain validated image attachments. A signed URL alone is
not an authentication credential.

## Pitfalls

- Under gVisor, writes larger than `PIPE_BUF` to the attached stdout can stall or
  silently lose a 4 KiB block, which the hash check then reports as
  `artifact_hash_mismatch`. The runner's `emit` therefore writes frames in
  4 KiB `os.write` calls; do not replace it with one buffered write.

- The runner reports browser failures with fixed codes: `source_timeout`,
  `source_navigation_failed`, or `runner_unexpected_failure`. An uncaught library
  exception previously ended the stream without an error frame, which the supervisor
  could only report as `invalid_frame`.
- Vendor resources are relayed one request at a time. At roughly one second per
  resource, a product page with about a hundred resources cannot reach `load` within
  the 120-second vendor budget. A navigation may use the budget minus 40 seconds.
- Under gVisor, Chromium's own sandbox crashes the renderer on script-heavy vendor
  pages (`source_navigation_failed`); the same page does not crash with that inner
  sandbox disabled or under runc. PDF captures do not use Chromium and are
  unaffected.

The fake provider and backend validate integration decisions, not containment.
Opt-in container runs do not replace packet, escape, resource exhaustion and
crash acceptance. Ordinary containers are restricted to explicitly synthetic
inputs; production admission requires the accepted runsc profile. Local control uses separate Unix identities; remote control requires TLS 1.3
mutual authentication, hostname verification and both pinned leaf certificates.
A certificate-only MemoryBIO check does not prove an actual remote connection.

Proxy quotas require one dedicated node and a shared private control ledger;
separate nodes need a coordinated quota implementation. The ledger stores
execution metadata and counters, never business records. PostgreSQL remains the
business source of truth. Supervisor recovery requires a service manager to
restart the supervisor; configure that before enabling production admission.

Source HTML can depend on external assets. Offline requests are blocked; complete
prototype outputs can carry an internal warning. Vendor network denials fail the
entire capture. A valid image and HTTP receipt do not certify manufacturer claims
or detect every login, CAPTCHA or soft error page.

Storage writes precede database publication. Failed transactions may leave
unreferenced ciphertext; the age-gated cleanup command in the runtime guide only
considers the two sandbox prefixes and excludes retained references and active
runs. Historical artifacts never become garbage merely because a selection was
replaced.

## Code

- [sandbox_contracts.py](../../server/app/schemas/sandbox_contracts.py): public typed inputs and views.
- [sandbox.py](../../server/app/api/sandbox.py): bounded submission and authenticated downloads.
- [sandbox.py](../../server/app/services/sandbox.py): authorization, provenance and artifact transfer.
- [sandbox.py](../../server/app/jobs/sandbox.py): claim, live guards and final publication.
- [supervisor.py](../../server/app/sandbox/supervisor.py): execution-node lifecycle and Docker backend.
- [runner.py](../../server/app/sandbox/runner.py): fixed render and validation programs.
- [sandbox_gc.py](../../server/app/services/sandbox_gc.py): age-gated orphan cleanup.
- [test_sandbox.py](../../server/tests/test_sandbox.py): API/processor integration and two-org SQL gates.
