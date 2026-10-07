---
kind: howto
---

# Prepare and verify a sandbox execution node

Use this procedure to provision the optional BrowserProvider execution boundary defined in
[the approved sandbox contract](../plan/sandbox.md#runtime-enforced-restrictions).
The API and business workers use [SocketBrowserProvider](../../server/app/providers/browser.py).
The execution node runs [sandbox_supervisor.py](../../scripts/sandbox_supervisor.py).

## Prepare the node

1. Provision a dedicated Linux node or a dedicated Linux VM without home, project, database,
   storage, SSH-agent or cloud-credential mounts. Do not join the business Compose network.
   Keep the node free of business credentials. A macOS host cannot run this supervisor directly.
2. Provision a rootful Docker daemon with cgroup v2 limits on that dedicated node and register
   the `runsc` runtime for the selected CPU architecture. gVisor cannot enforce cgroup limits
   under rootless Docker, so the supervisor rejects a rootless daemon, or `--ignore-cgroups` /
   `--rootless` runtime arguments, when `runsc` is configured; rootless Docker is accepted
   only for runc synthetic mode. Do not disable Chromium sandboxing to make a failing
   architecture work.
3. Create separate service identities for the supervisor and its caller. Only the supervisor
   identity may open the Docker socket; the caller must not be able to open it. Put both
   identities in a dedicated group used only for the control socket. Give the supervisor
   a private persistent state directory and a dedicated control directory.
4. Place the fetch broker in a trusted process with an egress policy that has no routes to
   business private networks or metadata endpoints. The browser container has no network;
   its bounded fetch frames go through the supervisor control connection to the caller's
   [FetchBroker](../../server/app/providers/sandbox_fetch.py). No HTTP CONNECT or arbitrary
   headers are forwarded. The caller must close the broker when the execution is cancelled.
5. Configure service-manager automatic restart for the supervisor, including restart after
   abnormal exit. Persistent instance inventory is reconciled before the control socket
   accepts work. Without supervisor restart, a stopped supervisor cannot reap its own
   containers; deployment acceptance must exercise this failure explicitly.

## Prepare a macOS Colima development VM

1. Create a dedicated profile with host mounts disabled. Keep `COLIMA_HOME` on
   a writable directory whose path stays short: Lima's socket path must be under
   104 characters, and an unwritable or missing `COLIMA_HOME` makes Colima fall
   back to `~/.colima`.

   ```sh
   export COLIMA_HOME=/PATH/TO/data/work/bsx
   colima start bsx --activate=false --ssh-config=false --vm-type vz --arch aarch64 \
     --mount none --cpus 4 --memory 4 --disk 40 --root-disk 12 --binfmt=false --runtime docker
   ```

2. Run [provision-guest.sh](../../deploy/sandbox-node/provision-guest.sh) as root
   inside the VM (`colima ssh -p bsx -- sudo sh provision-guest.sh`). It creates
   the `bidsbx` supervisor identity, delegates cgroup controllers to user
   sessions, installs gVisor `runsc` and the administrator-owned directories.
3. Register `runsc` with the VM's rootful daemon through the Colima profile, so
   Colima's regenerated `/etc/docker/daemon.json` keeps it, and add `bidsbx` to
   the `docker` group. In `$COLIMA_HOME/bsx/colima.yaml`:

   ```yaml
   docker:
     runtimes:
       runsc:
         path: /usr/bin/runsc
         runtimeArgs:
           - --oci-seccomp
   ```

   `--oci-seccomp` makes gVisor apply the image's seccomp profile inside the
   sandbox as well. Never add `--ignore-cgroups` or `--rootless`: the supervisor
   preflight rejects them, and a rootless daemon, because gVisor then enforces no
   memory, CPU or pids limit. Restart the profile with `colima stop bsx` and
   `colima start bsx`. A rootless daemon for `bidsbx`
   (`dockerd-rootless-setuptool.sh install`) is needed only for runc synthetic mode.
4. Install the supervisor code under `/opt/bid-supervisor` with a virtual
   environment containing `httpx`, the server certificate and key, and
   [supervisor.env.example](../../deploy/sandbox-node/supervisor.env.example)
   filled in as `/etc/bid-sandbox/supervisor.env`. Enable
   [bid-sandbox-supervisor.service](../../deploy/sandbox-node/bid-sandbox-supervisor.service),
   which restarts the supervisor automatically. Lima forwards the guest's
   `127.0.0.1:8443` to the host, so the macOS API/worker uses mTLS to
   `127.0.0.1` without exposing Docker's socket.
5. Load the pinned image into the rootful daemon, set `BID_SANDBOX_RUNTIME=runsc`
   and `BID_SANDBOX_DOCKER_SOCKET=/var/run/docker.sock`, and run the
   [acceptance driver](../../scripts/sandbox_colima_acceptance.py) before setting
   `BID_SANDBOX_RUNTIME_ACCEPTED=1` for business inputs.

The runtime combination and its rationale are recorded in
[the sandbox contract](../plan/sandbox.md#decisions).

## Build and pin the image

On a development VM without a registry, push the built image to a temporary
`registry:2` container bound to `127.0.0.1:5000` to obtain an immutable
`repository@sha256:` reference, then stop the registry; the supervisor never
pulls.

1. Resolve and verify the immutable, architecture-specific digest of the Playwright Python
   base image matching [sandbox.Dockerfile](../../deploy/sandbox.Dockerfile). Supply the whole
   `repository@sha256:digest` as `BASE_IMAGE`; there is no default image.
2. Build on the isolated build node with the repository as build context:

   ```sh
   docker build --build-arg BASE_IMAGE="$BASE_IMAGE" -f deploy/sandbox.Dockerfile -t bid-sandbox:acceptance .
   ```

3. Publish to the approved registry and record the resulting image digest. Preload that exact
   image on the execution node. The supervisor never pulls an image at execution time.
4. Copy [sandbox-seccomp.json](../../deploy/sandbox-seccomp.json) to the execution node's
   administrator-owned configuration directory. Record its SHA-256 and make the file
   read-only to the service identity. The explicit allow list permits Chromium's nested
   namespaces; compatibility and security acceptance remain required for the chosen runtime.
5. Inspect the final image for unexpected files and environment entries. The image must
   contain no business credentials or copied project data. Its copied application files are
   restricted by the Dockerfile. Fonts and Chromium are inherited from the pinned base;
   Python browser/PDF packages are pinned in that Dockerfile.

## Configure local Unix control

Set the following operator-controlled environment values for the supervisor and caller.
Use the same image and seccomp digest in both processes so their profile hashes agree.
Do not put these variables into browser input or caller-controlled API fields.

```sh
export BID_SANDBOX_ENABLED=1
export BID_SANDBOX_TRANSPORT=unix
export BID_SANDBOX_RUNTIME=runsc
export BID_SANDBOX_RUNTIME_ACCEPTED=1
export BID_SANDBOX_IMAGE="$APPROVED_IMAGE_DIGEST"
export BID_SANDBOX_SECCOMP=/etc/bid-sandbox/seccomp.json
export BID_SANDBOX_SECCOMP_SHA256="$APPROVED_SECCOMP_SHA256"
export BID_SANDBOX_SOCKET=/run/bid-sandbox/control.sock
export BID_SANDBOX_STATE=/var/lib/bid-sandbox/supervisor.sqlite3
export BID_SANDBOX_DOCKER_SOCKET=/run/user/1000/docker.sock
export BID_SANDBOX_CLIENT_UID=1001
export BID_SANDBOX_CONTROL_GID=1002
```

The example UIDs are service identities to substitute during provisioning. The supervisor
must belong to the control group so it can set socket ownership. It sets the control directory
to `0750` and the socket to `0660`, then checks Linux peer credentials for every connection.
The state directory and inventory stay private to the supervisor identity. A process lock
prevents a second supervisor from reaping containers belonging to a live instance.

Set `BID_SANDBOX_RUNTIME_ACCEPTED=1` only after completing the required isolation acceptance.
It is an operator assertion, not an automatic security certification. Leaving it unset or
leaving the image/seccomp digest unset causes admission to fail closed. Client availability
also requires the configured control socket.

Start the supervisor through the configured service manager, with the installed application
available on `PYTHONPATH`:

```sh
PYTHONPATH=server .venv/bin/python scripts/sandbox_supervisor.py
```

## Configure remote mutual TLS control

Use `mtls` when business workers and the credential-free execution node run on different
hosts. Keep the immutable image, seccomp and acceptance configuration above. Configure
these common values on both endpoints:

```sh
export BID_SANDBOX_TRANSPORT=mtls
export BID_SANDBOX_HOST=sandbox-control.example.internal
export BID_SANDBOX_PORT=8443
export BID_SANDBOX_TLS_SERVER_NAME=sandbox-control.example.internal
export BID_SANDBOX_TLS_CA=/etc/bid-sandbox/tls/ca.pem
export BID_SANDBOX_TLS_SERVER_SHA256="$APPROVED_SERVER_LEAF_SHA256"
export BID_SANDBOX_TLS_CLIENT_SHA256="$APPROVED_CLIENT_LEAF_SHA256"
```

Use lowercase SHA-256 fingerprints of the DER-encoded leaf certificates, not file hashes
of PEM text. Provision a server certificate with a matching DNS/IP subject alternative name
and server-auth usage, and a separate caller certificate with client-auth usage. Both must
chain to the configured CA. Install only the endpoint's own private key on that endpoint.

On the execution node set its numeric listening address and its own certificate/key paths:

```sh
export BID_SANDBOX_BIND_HOST="$SANDBOX_NODE_ADDRESS"
export BID_SANDBOX_TLS_CERT=/etc/bid-sandbox/tls/server.pem
export BID_SANDBOX_TLS_KEY=/etc/bid-sandbox/tls/server.key
```

On the business worker set its own client identity:

```sh
export BID_SANDBOX_TLS_CERT=/etc/bid-sandbox/tls/client.pem
export BID_SANDBOX_TLS_KEY=/etc/bid-sandbox/tls/client.key
```

Private key files must be readable only by their service owner, such as mode `0600`.
Keys must be provisioned without an interactive password prompt; the adapter does not accept
key passwords or copy key bytes into container environment variables. Keep certificate
material outside repository and business artifact storage. A client/server certificate
rotation requires updating both approved leaf pins and restarting the corresponding service
configuration; the endpoint and pins are part of the runtime profile digest.

Restrict node ingress to approved worker addresses and the configured control port. This is
a dedicated framed control protocol, with mandatory protocol ALPN; it serves no HTTP, CDP,
WebSocket or arbitrary forwarding interface. Do not expose the Unix socket through a TCP
forwarder. The container still has no network and receives no TLS credential or path.

The adapter requires TLS 1.3, mutual CA validation, an exact approved client leaf pin at the
server, and hostname plus exact approved server leaf pin at the client. A different client
certificate issued by the same CA is rejected before any application frame is read. Handshake
time is limited to five seconds and TLS shutdown to two seconds. Peer UID/group settings
apply only to Unix mode; the remote caller never receives a daemon socket or node credentials.

Readiness and dry-run checks inspect operator configuration and certificate-file readability
only. They make no connection and do not perform DNS lookup or TLS negotiation. A readable
configuration is not evidence that the remote supervisor is reachable or accepted for use.
Run the real pipeline and transport checks before enabling the production acceptance assertion.

## Exercise synthetic development mode

For a dedicated development VM with only synthetic inputs, explicitly set
`BID_SANDBOX_RUNTIME=runc` and `BID_SANDBOX_SYNTHETIC_ONLY=1`, and complete the same image,
seccomp, rootless and controller preparation. The internal run descriptor must also set
`synthetic_input=True`. The production provider availability check excludes this mode;
business API requests cannot select the internal synthetic flag.

This is a weaker development boundary. It is never selected automatically after `runsc`
fails. A synthetic-mode result is not acceptance for production or real tenant content.

## Run the real pipeline check

With the prepared supervisor running, execute:

```sh
BID_SANDBOX_RUNTIME_TEST=1 \
BID_SANDBOX_ARTIFACT_DIR=data/work/sandbox-verification/runtime \
PYTHONPATH=server:cli .venv/bin/python -m pytest server/tests/test_sandbox_runtime.py -q
```

The opt-in case sends a synthetic HTML input through the real socket, render container and
separate validation container, and writes returned PNG/DOM bytes to the artifact directory.
It does not start a VM, install Docker, build images or silently substitute a fake renderer.
Without the opt-in flag the real case is skipped; that skip provides no isolation evidence.

Run the broader attack and lifecycle acceptance in
[the contract acceptance section](../plan/sandbox.md#end-to-end-acceptance-after-approval) before setting a
production acceptance assertion. Preserve packet counters, runtime identity, resource peaks,
container inventories, hashes and machine-readable results outside `docs/`.

## Open vendor egress on a development node

A local development node may let vendor captures fetch any public HTTPS URL instead of
maintaining an exact allow list. Never configure this on a production node.

1. Write a policy file containing only an open revision, readable by the worker alone:

   ```json
   {"policies": [{"revision": "dev-open-v1", "open_public_https": true}], "revoked_revisions": []}
   ```

2. Create a private (mode 0700) directory for the quota ledger.
3. In the worker and API environment, set `BID_SANDBOX_POLICY_FILE` to that file,
   `BID_SANDBOX_FETCH_QUOTA` to a ledger path inside the directory, and
   `BID_SANDBOX_DEV_OPEN_EGRESS=1`, then restart both processes. Without the flag, an
   open revision makes the policy file invalid and every capture fails closed. The open
   revision uses its own per-organization window, `BID_SANDBOX_OPEN_FETCH_PER_MINUTE`
   requests a minute (default 600, a positive integer), instead of the 60 that named
   vendor policies use, because a whole page loads many resources.

Run the opt-in check against a public page and a public PDF of your choice:

```sh
BID_SANDBOX_RUNTIME_TEST=1 BID_VENDOR_LIVE_PDF_URL=https://... \
  uv run pytest server/tests/test_vendor_screenshots.py -k real_sandbox
```

It captures, downloads, prepares and ingests each page through the API and writes the
page PNGs and archive summary to `data/work/vendor-live`. Set `BID_VENDOR_LIVE_WEB_URL`
to include a web page; see the pitfalls in
[sandbox-execution.md](../notes/sandbox-execution.md#pitfalls) for its current limits.

## Verify lifecycle and accounting

1. Submit a run and inspect the supervisor's dedicated daemon inventory for separate
   `render` and `validate` container names. The same attempt cannot start another group.
2. Kill the calling worker during rendering. The supervisor monitors caller EOF and its
   independent deadline, removes both stage instances and verifies their absence. Cancel
   the client task when authorization or the business job lease is revoked.
3. Restart the supervisor during execution. Verify that the persistent inventory and
   Docker-labelled orphan containers are removed before admission resumes. Verify the
   periodic inventory sweep by creating only approved synthetic orphan cases.
4. Inject a Docker cleanup failure. Verify new admission is denied while the node is
   quarantined. Restore Docker connectivity and restart the supervisor to reconcile and
   reopen the node. A cleanup-pending result must never become a downloadable success.
5. Verify the deliberately reduced fixed CPU profile: one vCPU, a 10 ms quota period,
   and a shared wall deadline one second below the admitted budget. Both stages share
   that deadline, bounding execution CPU independently of sampling while termination
   remains subject to the tested daemon kill latency. The approved profile allows lower
   limits; the adapter does not expose a caller-controlled increase. Confirm `cpu_ms`
   comes from sampled Docker daemon cumulative counters and that memory uses daemon
   peak counters when available, otherwise observed samples. The runner waits for a fixed acknowledgement while the supervisor obtains terminal
   counters, and a missing final sample fails the run. The acknowledgement/exit interval
   remains outside that sample; it is not an exact final cgroup ledger.
6. After caller death, call `BrowserProvider.recover(descriptor)` with the original full
   descriptor. Only the matching descriptor digest can receive the cleanup status. A
   missing journal entry becomes `not_started` only after checking the daemon inventory.
   Treat `pending`, `failed` and a failed control connection as unconfirmed cleanup.
   Recovery receipts mark accounting as `recovered_samples_require_conservative_budget`: an
   interrupted supervisor cannot prove terminal CPU/output samples, so retry admission
   must charge the remaining attempt bound instead of releasing it from a partial sample.
7. Check total transfer output across render and validator stages, including the trusted
   request manifest or bundle, against the profile limit. The stages share one deadline
   and output/CPU counters. Neither stage receives a fresh budget.

## Check the remaining acceptance boundaries

The adapter validates fixed framing, declared dimensions, header magic and byte hashes on
the trusted side. PNG/PDF decoding occurs only in the isolated validator. The validator checks complete
static PNG framing and CRCs and rejects a uniform vendor screenshot; blank prototype
and explicitly selected PDF pages remain valid. Blocked offline prototype resources produce
the fixed `offline_resources_blocked` warning when the full output set is produced. A vendor
capture with denied resources completes with `vendor_resources_incomplete` as long as its
entry document loads. A digest-pinned
image and a validator success do not prove the image/kernel cannot be compromised.

Verify login/challenge and error-page handling against approved synthetic sites. The fixed
browser runner rejects HTTP failures, password forms and recognized challenge frames, but
those heuristics cannot classify every site-specific soft error page. Successful capture
remains an unconfirmed input artifact, not authenticity or semantic validation.

Keep runsc/Chromium compatibility, packet-level zero-egress checks, memory
exhaustion, supervisor restart and cross-instance canary tests as explicit deployment
acceptance gates. The focused pipeline check above does not cover all of those attacks.

Check the trusted proxy against attack origins. The driver needs no VM, network
access or privileges: it starts synthetic HTTPS servers on loopback with a throwaway
CA, runs the production broker and host relay against them, and writes
`results.json` with every case and the requests each target received:

```sh
PYTHONPATH=server .venv/bin/python scripts/sandbox_proxy_acceptance.py
```

It exits 1 when any case fails. Kernel-level egress filtering on the proxy node and
real DNS resolution stay deployment checks.

Check two-org isolation and lifecycle faults on a running development instance with
two synthetic orgs. Org A needs a task with a succeeded extraction and a selected
feature; pass their IDs and both orgs' credentials through the environment variables
listed in [sandbox_two_org_acceptance.py](../../scripts/sandbox_two_org_acceptance.py):

```sh
PYTHONPATH=server .venv/bin/python scripts/sandbox_two_org_acceptance.py --worker-command data/dev-runtime/run-worker.sh
```

It renders prototypes as A, probes A's runs, jobs and signed links as B, reads and
writes across org context with the runtime database role, cancels one run and SIGKILLs
the worker during another, then restarts the worker. Run it only against a development
instance.

## Submit and inspect a sandbox run

1. Create a task, upload and parse its tender, complete extraction, and select a
   fixed feature or product revision with the ordinary CLI resource commands.
2. Save a `PrototypeSpec` or `VendorSpec` JSON file using the field definitions in
   [sandbox_contracts.py](../../server/app/schemas/sandbox_contracts.py). For a
   prototype, calculate `html_sha256` and `html_size_bytes` from the UTF-8 bytes.
   For capture, hash the exact source URL retained by the fixed product revision;
   include an explicit ordered page set when `format` is `pdf`. A new
   `capture_key` means a new capture intent; preserve it on transport retries.
3. Preflight and submit through the API-backed CLI:

   ```sh
   bid sandbox render --task TASK_ID --html prototype.html --input prototype-plan.json --dry-run --json
   bid sandbox render --task TASK_ID --html prototype.html --input prototype-plan.json --wait --json
   bid sandbox capture --task TASK_ID --input capture-plan.json --wait --json
   bid sandbox list --task TASK_ID --limit 50 --json
   bid sandbox show --id RUN_ID --json
   bid sandbox download --artifact ARTIFACT_ID --output NEW_FILE --json
   ```

   The CLI automatically repeats preflight before real submission. A blocked
   preflight does not execute a container or create a storage object. Download
   destinations must be new files. Session/token scopes are defined in the
   [sandbox contract](../plan/sandbox.md#cliapi-and-identity).
4. Cancel through `bid job cancel JOB_ID --json`. Reconcile an interrupted run by
   retrying the same request with `--retry` after the supervisor has recovered.
   Cleanup confirmation cannot reset consumed budgets. A changed selection,
   policy or runtime profile requires a new submission.

## Reap unreferenced encrypted candidates

1. Configure the API's restricted database role and encrypted storage settings
   for the organization being maintained. Keep the worker/runtime credentials
   separate. Stop using an object store that does not support conditional deletes
   for this cleanup path.
2. Inspect eligible objects; the command defaults to a dry-run:

   ```sh
   .venv/bin/python scripts/sandbox_orphans.py --org ORG_ID
   ```

3. Remove the inspected eligible candidates:

   ```sh
   .venv/bin/python scripts/sandbox_orphans.py --org ORG_ID --delete
   ```

   The command holds the same organization admission lock as submission, only
   considers exact `sandbox-inputs` and `sandbox-artifacts` key layouts older
   than 24 hours, and excludes database references and queued/running jobs.
   Repeat bounded batches when `limit_reached` is true. Retained source history
   and other business prefixes are excluded.
