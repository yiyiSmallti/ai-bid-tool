---
kind: plan
status: superseded by ADR 0012
---

# Production deployment plan

This hosted-deployment proposal is superseded by
[ADR 0012](../adr/0012-single-user-installation.md). Its hosted topology and
multi-person acceptance requirements below are retained as historical planning,
not instructions for a single-user installation. Restoring hosted operation
requires a new decision. Local operation follows the
[development guide](../guides/development.md); desktop packaging, bundled runtimes
and signing remain outside [the single-user cutover](single-user.md#out-of-scope).
The baseline and runtime pointers below identify current source locations without
authorizing this former deployment model.

## Repository basis and release prerequisites

The [design](../design.md), [agent hard rules](../../agent.md#hard-rules-must-never-be-violated)
and [development guide](../guides/development.md) define the application boundaries
and command procedures. This plan assigns production preparation and acceptance; it
does not replace those contracts or mark [roadmap gaps](roadmap.md) complete.

| Asset | Production use and preparation required |
| --- | --- |
| [Compose stack](../../deploy/docker-compose.yml) and [environment template](../../deploy/.env.example) | Use as the starting inventory: PostgreSQL, one-shot migration, API, worker, object storage, search and converter. Prepare a reviewed production manifest with private networking, restart policies, health supervision, resource limits and explicit secret injection. A plain `compose up` is not the release procedure. |
| [Application Dockerfile](../../deploy/Dockerfile) | Preserve frozen Python dependencies, OCR language data and the non-root runtime. Build an immutable API/worker image and package required native binaries; pin its base and final digests in the release record. The Dockerfile does not package the desktop app or Rust executable. |
| [Native desktop build](../guides/desktop-development.md) | Build the MyGo application separately with its Python/PostgreSQL/renderer resources. The API serves authenticated business routes and SSE; it does not serve frontend assets. |
| [`app.admin`](../../server/app/admin.py) and [migration environment](../../server/migrations/env.py) | Run provisioning, bootstrap and rotations only through the migration/admin identity. API and worker must never receive its connection credentials. |
| [Worker](../../server/app/jobs/worker.py), [queue](../../server/app/jobs/queue.py) and [sandbox queue](../../server/app/jobs/sandbox_queue.py) | Run the worker as a separately supervised process consuming the PostgreSQL-backed `bid` queue. Procrastinate needs no Redis. Plan durable queue recovery and graceful drain before replacement. |
| [Rust renderer build](../../scripts/build_screenshot_renderer.sh) and [adapter](../../server/app/providers/screenshot_renderer.py) | Build the locked `stamp` crate for the target architecture; package `bid-screenshot-renderer` and its license material. Configure an absolute executable path with `BID_SCREENSHOT_RENDERER`; symlink or missing binaries fail. |
| [Sandbox image](../../deploy/sandbox.Dockerfile), [seccomp profile](../../deploy/sandbox-seccomp.json) and [node guide](../guides/sandbox-runtime.md) | Provision and accept the independent runsc execution boundary. The business Compose file does not provision this node. |
| [Converter adapter](../../server/app/providers/converter.py) and [page-preview mechanism](../notes/page-previews.md) | Keep the Compose-pinned Gotenberg image private, retain disabled Chromium/webhook routes, and verify LibreOffice/CJK conversion. An unset converter URL disables previews. |
| [Search selection](../../server/app/providers/search.py) and [SearXNG settings](../../deploy/searxng/settings.yml) | Select search explicitly. SearXNG remains private; Perplexity needs an active platform `vendor_search` credential. Neither is an automatic fallback for the other. |
| [Storage adapter](../../server/app/providers/storage.py) and [MinIO Dockerfile](../../deploy/minio.Dockerfile) | Provision the private bucket separately. The source-built MinIO image is explicitly a development dependency; production requires a maintained service and named operator. |
| [CI workflow](../../.github/workflows/check.yml) | Use release-source checks as a prerequisite. CI runs on PRs or manual dispatch, skips unaffected suites, and does not deploy. A documentation-only green check does not qualify a runtime release. |

The production manifest must explicitly forward every approved runtime setting.
Compose interpolation does not pass arbitrary entries from an env file to containers.
In particular, account for `BID_SCREENSHOT_RENDERER`, PDF limits,
LLM concurrency/deadlines/request options, rubric limits, drafting limits, job
call limits/leases, and sandbox policy/transport/TLS settings. Reconcile the differing
Compose and [Settings](../../server/app/core/config.py) defaults rather than relying
on an assumed env-file override. Verify effective setting names and non-secret values
without dumping credentials or a fully expanded Compose configuration into logs.

Packaging and operational changes identified here require a subsequent authorized
implementation. Until their gates pass, affected capabilities must remain unavailable
and must not be advertised as production-ready.

## Target topology options

| Option | Placement | Tradeoff and suitability |
| --- | --- | --- |
| Single business host | TLS proxy, API, worker, PostgreSQL, converter and optional SearXNG; local encrypted files or a separately maintained object store. A sandbox still requires an isolated Linux VM/node. | Lowest initial infrastructure count and straightforward Compose operation. Host loss couples application, database and local storage outages; OCR/rendering competes with database I/O. Suitable only for a limited pilot with accepted recovery downtime, off-host backups and a named operator. |
| Split database and storage — recommended | Linux application host for proxy/API/worker/converter and optional SearXNG; private PostgreSQL service; private S3-compatible service; separate sandbox node. | Separates durable data from application replacement and enables independent backup/PITR operations. Costs more and requires private connectivity, but avoids making a lost application disk the recovery boundary for all tenant data. Managed services are preferred if they support the required database privileges and storage semantics. |
| Additional worker separation | Retain split data services and place business workers on additional Linux hosts; sandbox remains separate. | Introduce after queue latency and resource measurements justify it. Requires common encrypted object storage, consistent keyrings/policy, connection budgets and acceptance of distributed fetch quotas and job recovery. It is not the initial availability promise. |

For the default, expose only the TLS ingress to clients. Keep the API listener private
behind it; PostgreSQL, S3 administration, converter and SearXNG have no public service
ports. Restrict administrative access and sandbox control to approved management or
caller networks. Require verified TLS to external database/object services. A managed
PostgreSQL candidate must support pgvector, role creation, fixed function ownership,
RLS and Procrastinate; inability to provision those controls blocks that provider.

Terminate TLS at an owner-selected proxy or load balancer and automate certificate
renewal. Preserve `/app`, API paths and streamed task events; configure upload limits,
SSE buffering/timeouts and trusted forwarding addresses explicitly. Test client-address
handling against [authentication admission](../notes/platform-console.md#password-admission-and-totp-consumption).
Do not trust arbitrary forwarded headers or expose the ASGI listener as another public
route. Database/storage separation does not by itself make the application highly available.

## Environment decisions

The owner approved the defaults below. Each row still needs the listed owner input and
an accountable operator before its step; values are not provisioned resources or commitments.

| Decision | Approved default | Owner input required before |
| --- | --- | --- |
| Host/provider, region and data residency | Linux application host, split PostgreSQL/S3 and isolated sandbox node; keep approved data flows within the selected residency policy | Procurement: provider, region, CPU architecture, support contact and acceptable outage |
| Domain, ingress and TLS | One HTTPS origin for console/API, automated certificate renewal, private upstreams | Ingress configuration: domain ownership, DNS operator, proxy and renewal owner |
| PostgreSQL operator | PostgreSQL 16 with pgvector and the role privileges required by the migrations; managed service if compatible | Provisioning: service choice, connection limits, extension policy and maintenance windows |
| Storage operator and retention | Maintained private S3-compatible service, versioning, encrypted off-host backup; no public bucket | Bucket creation: location, access policy, retention/deletion requirements and conditional-write support |
| Backup destination and recovery objectives | Independent failure domain and separate backup authority; proposed RPO at most 15 minutes and RTO at most 4 hours, subject to a measured drill | Backup design: encrypted destination, retention, capacity and approved RPO/RTO |
| LLM/vision vendors, models and keys | Owner-approved supported Anthropic/OpenAI-compatible endpoints and explicit model capabilities; enable vision only after real-image acceptance. | Provider setup: vendors, models, data-retention/training terms, quota and keys supplied privately |
| Search configuration | Start with `disabled`; enable Perplexity or private SearXNG after coverage/rate-limit acceptance | Search release: provider, key if required, approved sources and request limits |
| OCR and embeddings | Local Tesseract with packaged language data; defer cloud OCR and embeddings until their separate scope is approved | Capability selection: required languages and whether additional integration is needed |
| Email and identity operations | No application email dependency for initial release; manually controlled onboarding/password setup and platform TOTP | Onboarding: confirm whether email delivery is mandatory; if so, specify sender/domain/vendor and implement and accept delivery separately |
| Secret custody and recovery | Secret manager; owner-only 0600 service env files as a small-installation alternative | Provisioning: custodians, rotation process, offline recovery access and audit retention |
| Resource usage | Record provider/model/duration and resource counts under [ADR 0011](../adr/0011-remove-billing.md) | First org/model: credentials, model capabilities and operational call limits |
| Capacity and concurrency | One application replica and one worker process initially; bound model concurrency, worker/render resources and database pools by acceptance measurements | Sizing: simultaneous tasks, maximum documents, daily volume and acceptable queue delay |
| Sandbox and vendor egress | Dedicated Linux runsc node, private mTLS control, exact approved vendor policy and kernel-filtered broker egress | Capture enablement: node operator, allowed sites, per-org request budget and accepted runtime profile |
| Monitoring and incident response | Restricted centralized logs, external probes and infrastructure/queue monitoring with an accountable on-call operator | Rehearsal: monitoring destination, alert channels, retention and escalation coverage |
| Initial feature scope and launch approval | Synthetic acceptance orgs only until all enabled-path gates pass; keep unresolved roadmap capabilities out of the launch promise | Go-live: named approver, accepted defects, support process and maintenance window |

No SMTP transport is specified by the repository deployment configuration. Email-shaped
login identities and password-setup links do not prove email delivery. Operational
alerting can use the chosen monitoring service independently of application email.

## Secrets and platform credential preparation

Use [Keys and storage](../guides/development.md#keys-and-storage) and
[platform credential authority](../notes/platform-credentials.md) as the key-domain and
connection contracts. Keep runtime material outside the checkout and image build
context. Secret-manager delivery should expose only the values each process needs;
file delivery requires a private directory, service-owned 0600 files and no shell
tracing. Docker administrators remain trusted because injected process environments
are accessible to them. Do not invent unsupported `*_FILE` application settings;
arrange injection through the selected service manager or approved entrypoint.

Generate independent roots for stored data (`BID_ENCRYPTION_KEY`), sessions/signed
links (`BID_TOKEN_KEY`) and provider/operator-factor secrets (`BID_SECRETS_KEY`). Use a
separate `BID_CLI_KEY` on operator CLI machines for their encrypted local sessions;
do not distribute it as an application root. Keep database passwords, S3 credentials,
TOTP seeds and sandbox TLS private keys separate from all of these. Neither root keys
nor operator identities/TOTP seeds belong in `platform_credentials`.

Never put secret values in repository files, command arguments, release evidence,
terminal transcripts, logs, screenshots or support bundles. Exclude request bodies,
Authorization/Cookie headers, signed-link query strings, database URLs and expanded
environment dumps. Configure the local operator through
[Run the platform console](../guides/development.md#run-the-platform-console):
`BID_PLATFORM_ADMIN_EMAILS` and `BID_PLATFORM_TOTP_SECRETS` are the authentication
source. Keep the password and factor private; authentication factors do not belong
in `platform_credentials`.

For a fresh environment, follow
[Manage platform credentials](../guides/development.md#manage-platform-credentials):
authenticate an allowlisted platform operator with password and TOTP, create each
credential from metadata plus an owner-only key file, then bind catalog models to
matching credential/provider/endpoint identities. Use the separate `vendor_search`
purpose for Perplexity and `standalone_llm` only for authorized standalone evaluations.
Metadata JSON contains no key. API needs dedicated management and reader connections;
worker needs the reader connection and must not receive management credentials.

If explicitly importing legacy platform credentials, the proposed cutover sequence is:

1. Stop new admissions and drain old workers. Prepare only approved vendor assignments
   in a temporary 0600 env file and a reviewed metadata manifest; never source that
   legacy file into a new runtime.
2. Run `bid platform credential import-env --manifest MANIFEST_FILE --env-file
   LEGACY_ENV_FILE --dry-run --json`, inspect metadata-only results, then run the same
   command without `--dry-run` under the approved operator session. Same-value replay
   is safe; a mismatch requires a decision and must not be blindly retried.
3. Bind all catalog references, then have the migration owner run `ALTER TABLE
   public.platform_models VALIDATE CONSTRAINT platform_model_credential_fk`.
4. Remove `BID_PLATFORM_CREDENTIAL_*`, `BID_LLM_API_KEY` and
   `BID_PERPLEXITY_API_KEY` from every service environment and mount, including empty
   assignments. Startup rejects legacy names. Remove temporary key/import files and
   verify readiness plus an authorized real call before reopening admissions.

For rotation, stage readable previous keys across all replicas before changing the
current root. Use `python -m app.admin rotate-encryption --scope data` for data and
`python -m app.admin rotate-encryption --scope provider-secrets` for platform/BYOK
secrets, under the migration owner with the correct storage and keyring. Require a
second pass with zero rewrites and no failures. Retain retired keys securely for all
backups that still require them. Replacing `BID_TOKEN_KEY` invalidates sessions and
signed links immediately; rehearse that deliberate reauthentication event. Credential
revocation must not be undone by serving an older env-reading release.

## Database provisioning and recovery

For the current installation, database provisioning follows
[the development guide](../guides/development.md#provision-a-development-database).
[The single-user baseline revision](../../server/migrations/versions/0001_single_user_baseline.py)
replaces the former tenant foundation. Migration and queue connections need session
semantics; qualify any pooler against actor context, advisory locks and queue behavior.
Keep production durability enabled. CI's disposable-database settings that disable
`fsync`, `synchronous_commit` and `full_page_writes` must never be copied to production.

The current role boundary is defined by [ADR 0012](../adr/0012-single-user-installation.md):
migration owner and runtime `bid_app`. The former dedicated platform/credential
connection checks are removed. API and worker use
[`Database.verify_role`](../../server/app/core/db.py), which rejects superuser,
BYPASSRLS and application-table ownership. The stage table below records the
superseded hosted proposal and must not be used to provision single-user roles.

| Execution stage | Connection delivery | Deployment verification |
| --- | --- | --- |
| Provisioning and maintenance | Migration owner only for DDL, bootstrap and controlled rotation | Confirm sufficient role/ownership privileges, then remove this connection from the runtime launch context. |
| API startup | Tenant login plus separate `bid_platform_app` and `bid_credential_reader` URLs | Require successful tenant, management and reader validation; forbid direct credential-table grants and inherited owner authority. |
| Worker startup | Tenant and credential-reader URLs; management URL absent | Confirm the effective worker environment cannot use management functions and includes no migration credentials. |
| Backup and restore | Separately authorized backup/restore identity | Prove full org coverage despite FORCE RLS; restore the approved grants and provision authentication from secret custody. |

Use `python -m app.admin init-db` once per release under the owner. It checks/creates
`bid_app`, runs Alembic to the release head, provisions explicitly supplied dedicated
role passwords, and installs/grants the queue schema. It does not create the database
service or rotate an existing `bid_app` password. Provision that password through the
database administrator when needed. The dedicated LOGIN roles are initially created
without passwords; supply their authentication privately before starting API/worker.
Do not grant application roles role-management or owner membership to bypass a failure.

The migration gate must compare Alembic's database revision with the candidate release
head and inventory `pg_roles`, memberships, ownership, grants, `pg_policies`,
`relrowsecurity` and `relforcerowsecurity`. Every org business table requires NOT NULL
`org_id`, ENABLE/FORCE RLS and the expected USING/WITH CHECK policies. Approved global
exceptions are only those in [hard rule 1](../../agent.md#hard-rules-must-never-be-violated);
Procrastinate metadata has its own queue grants and is not tenant business storage.
Test missing org context and two-org isolation using the runtime identity. Verify fixed
credential functions have restricted `search_path`, qualified names, no PUBLIC EXECUTE
and no dynamic SQL. A privileged successful query is not tenant-isolation evidence.

Plan encrypted daily database backups and continuous WAL archival or managed PITR,
with retention and recovery objectives selected in [Environment decisions](#environment-decisions).
Logical dumps are useful additional recovery artifacts but do not provide PITR alone.
For self-managed PostgreSQL, prepare tested base-backup/WAL procedures; the repository
Compose stack supplies neither WAL archival nor a production backup scheduler. Capture
roles/grants/extension requirements as protected recovery material; exclude password
verifiers from ordinary evidence and re-provision authentication through secret custody.

Back up object data/version history, deployment configuration, keyring recovery material
and supervisor configuration as well. Retain database and storage recovery points that
can be reconciled: an object key is insufficient if its overwritten ciphertext needs a
different historical key. A live volume copy is not a verified PostgreSQL backup.

Test both a full restore and a
selected PITR timestamp, recover encrypted fields and objects with retained keys, verify
RLS/role grants and signed access, and reconcile application/queue/vendor-call records
before allowing any worker to resume.
Record recovery point, elapsed recovery time,
missing objects, key availability and owner verdict.
A successful backup upload alone
does not pass the restore gate.

## Storage and document processing

Use S3 mode for the recommended topology. Create a private bucket before activating
`BID_STORAGE=s3`; inject bucket-scoped service credentials rather than object-store root
credentials. The [S3 adapter](../../server/app/providers/storage.py) requires explicit
access/secret keys; do not assume workload-identity-only authentication works without
an implementation change. Resolve the worker-reachable endpoint separately from a
host development endpoint; Compose maps `BID_S3_CONTAINER_ENDPOINT` to the runtime
`BID_S3_ENDPOINT`.

Accept the selected store's conditional-create behavior, bounded reads and conditional
deletion used by [sandbox orphan cleanup](../../scripts/sandbox_orphans.py). Preserve
application encryption and `org/{org_id}/` paths, deny public bucket/list access, enable
service-side encryption/versioning as appropriate, and restrict backup/delete authority
separately. Clients download through the application's authorized short-lived signed
flow; do not publish raw object URLs as a substitute. Cross-org, expired, tampered and
revoked-authority download tests must run against the chosen S3 service.

If the pilot selects local storage, API and worker must share the same encrypted files
volume and keyring; use disk encryption, private permissions, free-space alerts and
off-host backups. Do not move a worker to another machine while retaining host-local
file assumptions. Storage lifecycle deletion must follow approved retention and database
references; do not remove inaccessible orphans after an ambiguous database commit.

Gotenberg must accept only private service traffic, with bounded CPU, memory, temporary
space and request deadlines. Retain the pinned image and disabled Chromium/webhook
routes from Compose. Verify Chinese fonts, page counts and attachments using a real
DOCX-to-PDF conversion. Local PDF/export child limits and OCR language availability
must also be exercised on Linux; matching an env template on macOS is insufficient.

## Worker, renderer and sandbox host requirements

Keep API and business workers non-root on Linux and supervise both independently.
Set memory/CPU/pids/temporary-storage limits for the full service, plus the PDF/export
child limits in [Settings](../../server/app/core/config.py).
Size connection pools and per-process concurrency before adding workers.
Preserve
queue/business IDs through restarts and never truncate queue tables to clear a backlog.

Install the Rust executable and fonts read-only for the target architecture and run a
real screenshot job. The B05 renderer requires non-root Linux execution with no renderer
network capability and tested process/CPU/RSS limits. The
[approved B05 execution contract](annotation.md#cost-and-execution-limits) proposes
one live renderer per job with a 1 CPU/512 MiB starting envelope. Its additional OS
restrictions and cancellation polling require implementation and acceptance; the
existing screenshot adapter is not proof that the planned B05 cloud annotation path
is delivered. Keep the distinction in the selected launch scope.

Follow [Prepare the node](../guides/sandbox-runtime.md#prepare-the-node) for browser
execution. Its privileged daemon belongs only on a dedicated credential-free Linux
node or isolated VM with cgroup v2, rootful Docker and accepted runsc. Browser containers
remain non-root, networkless, capability-restricted and read-only except for bounded
scratch space. API and workers must never mount a Docker socket. Do not mount business
files, home directories, cloud credentials or SSH agents into the sandbox node or image.

Pin and preload the architecture-specific sandbox image; the supervisor must not pull
at execution time. Install the administrator-owned seccomp allowlist read-only, verify
its digest, and register runsc with OCI seccomp enforcement. Require enforced cgroup
CPU/memory/pids limits; reject rootless runsc, ignored cgroups, disabled Chromium
sandboxing and synthetic runc as substitutes. Apply independent wall/output limits and
verify both render and validation stages share the admitted budget.

Use private mTLS control for a separate node, with TLS 1.3, CA validation, hostname
validation and both approved leaf pins. Restrict listener ingress and rotate endpoint
keys/pins together. Unix control is only an option within an appropriately isolated
node arrangement with distinct peer identities. Use the
[supervisor service unit](../../deploy/sandbox-node/bid-sandbox-supervisor.service) as a
reviewed starting point for restart supervision and persistent orphan reconciliation.

The trusted fetch broker requires exact approved vendor-source policies, quotas and
kernel-enforced denial of private/business/metadata destinations. The broker's worker
process also needs business services, so qualify its egress isolation explicitly; a
blanket worker firewall rule or browser-only network isolation is not sufficient proof.
Keep `BID_SANDBOX_DEV_OPEN_EGRESS` unset and reject open-public-HTTPS policies. Test
redirects, DNS rebinding, cancellation, worker death, supervisor restart and cleanup
failure quarantine on the selected host. Set `BID_SANDBOX_RUNTIME_ACCEPTED=1` only after
those gates; the flag is an operator assertion, not a certification or connectivity probe.

## Observability and operational response

Provision monitoring as deployment work; no Prometheus `/metrics` endpoint or complete
dependency readiness should be inferred from the application. The existing
[`/health` handler](../../server/app/api/account.py) reports API/model configuration and
can exercise resolution, but does not prove queue consumption, storage, converter,
sandbox connectivity or real-model authorization. Startup role/readiness checks must
pass, followed by separate synthetic end-to-end probes.

| Signal | Collection and proposed alert |
| --- | --- |
| API and TLS | External HTTPS and restricted authenticated canaries; error rate, latency, certificate expiry, authentication 429/503 and process restart counts. Page on sustained unavailability; tune thresholds after rehearsal. |
| Worker and queue | Process supervision plus restricted queue/business-state queries: oldest queued job, failed/stuck jobs, worker heartbeat age, lease expiry, retries and unsettled vendor calls. Alert when heartbeat exceeds the configured lease or queue age exceeds the agreed service target. |
| Database | Connections/pool saturation, locks, query latency, disk/WAL growth, replication/archive lag and backup completion. Alert immediately on backup/WAL failure or lag that threatens the approved RPO. |
| Storage and conversion | Failed/slow object writes/reads, missing/decryption failures, free space, converter failures/timeouts and preview output bounds. Repeated deterministic failures require investigation, not unbounded retry. |
| Sandbox | Admission failures, profile/pin drift, CPU/memory/output peaks, orphan inventory, supervisor restarts and cleanup quarantine. Stop affected admissions on unconfirmed cleanup or isolation drift. |
| Providers and cost |Alert before the owner-approved monthly cap and at sustained model/search degradation. |

Collect API, worker, converter and supervisor logs centrally with access control,
rotation and owner-approved retention. Keep Uvicorn access logging disabled as in
Compose; configure proxy logs to omit signed query strings and credentials. Use
existing request/job/run/org identifiers and sanitized error codes for correlation,
not documents, quotes, confidential fields, prompts or response bodies. Verify that
database statement/error logging cannot expose role passwords or credential payloads
during provisioning and maintenance.

Use infrastructure exporters or restricted operational queries where application
metrics are absent; separately scope any required instrumentation. Audit rows are
security records, not ordinary disposable logs. Retention must preserve authorization
history and authentication admission/TOTP state. The owner must assign alert handling,
maintenance windows and a vendor/restore escalation path before go-live.

## Proposed first-deployment runbook and gates

These are execution stages to approve and rehearse, not actions performed by this
document. Use the linked procedures for command details. All capitalized command
arguments below are placeholders; obtain actual values only through private deployment
configuration. Run disposable database suites on a separate PostgreSQL cluster, not
merely another database on the production cluster: test fixtures can alter cluster-wide
roles as well as truncate test tables.

1. **Approve the environment and release scope.** Resolve the decisions table, name
   deployment/database/security/acceptance owners, and select the source release and
   retained previous image. Produce the production manifest, network policy, secret
   references and recovery procedure. Gate: owner approval and no unresolved requirement
   that affects an enabled feature, data residency or recovery authority.
2. **Build and qualify immutable artifacts.** Follow
   [Run the checks](../guides/development.md#run-the-checks) in a disposable environment,
   build the console and Rust renderer, and package both in the Linux release image.
   Record image/base/renderer/font/seccomp hashes and lockfiles. CI must actually run
   the affected Python/database/Rust and frontend checks; browser tests, live providers,
   sandbox security, storage acceptance and restore drills are additional gates.
   Gate: no relevant skipped check and a reproducible artifact inventory, without secrets.
3. **Provision infrastructure and backups.** Prepare private networks, Linux service
   identities, TLS ingress, storage bucket, database and off-host backups/PITR. Provision
   the sandbox node only if included. Keep public admissions closed. Gate: allowed and
   denied network paths verified, durable storage writable by the correct service
   identities, secret recovery available and backup monitoring active.
4. **Initialize the database under the owner.** Inject migration-only credentials and
   run `python -m app.admin init-db` from the candidate release. For a Compose execution,
   use the reviewed manifest's one-shot migration service; do not let an unmodified
   local `depends_on: postgres` silently create a second database in a managed-DB setup.
   Gate: release Alembic head, queue schema/grants, role matrix, FORCE RLS and missing/
   foreign-org checks pass. Remove owner credentials from the runtime launch context.
5. **Bootstrap controlled identities.** Run `python -m app.admin bootstrap --org-name
   ACCEPTANCE_ORG_NAME --email ADMIN_EMAIL`, obtaining its password only through
   `BID_BOOTSTRAP_PASSWORD`. Provision the platform operator allowlist/TOTP privately
   using [Run the platform console](../guides/development.md#run-the-platform-console).
   Gate: password/TOTP sign-in, org membership and task permissions behave correctly;
   repeated bootstrap must not overwrite an existing identity. Use a second synthetic
   org for isolation acceptance, not imported development accounts.
6. **Configure credentials and catalog.** Apply the fresh credential setup or the
   explicitly authorized legacy import sequence above; set model identity, endpoints,
   capabilities and request limits, and the explicit search selection.
Gate: credential
   function grants/readiness, catalog FK validation, disabled/replaced key behavior and
   removal of forbidden legacy env names.
7. **Start private services in dependency order.** Start converter/selected search,
   the accepted sandbox supervisor if enabled, API and worker from the same release.
   Native commands are `uvicorn app.api.main:create_app --factory --host 127.0.0.1
   --port 8000 --no-access-log` and `python -m app.jobs.worker`; a container API can
   listen internally while the host binding remains private. Configure service-manager
   restart and resource limits. Gate: startup role checks, HTTPS `/app/` and `/health`,
   real queue completion, encrypted storage, converter and sandbox round trips succeed.
8. **Run acceptance and recovery rehearsals.** Complete the matrix below against the
   candidate images and chosen service types. Destructive fault injection belongs on
   an isolated production-equivalent environment. Perform non-destructive smoke paths
   in the restricted production acceptance orgs. Gate: security denials, real calls,
   WPS review and full/PITR recovery have recorded owner verdicts; no unexplained skips.
9. **Approve go-live and open ingress/admissions.** Capture the approval and exact
   release/configuration references in the release record, take a final consistent
   recovery checkpoint and confirm operators are available.
Gate: smoke checks stay within the accepted limits and rollback/
   recovery material is immediately accessible.
A failed gate stops promotion.

## Development data and exceptional migration

Recreate authorized catalog configuration and
fresh identities through the normal administration paths.
Production acceptance data
must be synthetic or approved public inputs in dedicated acceptance orgs only.
Decide
their retention/disablement before inviting business users; retain acceptance records
without copying confidential content into evidence bundles.

Existing multi-org data is not converted by the
[single-user installation decision](../adr/0012-single-user-installation.md).
Any future data import requires a separate approved inventory, encrypted-object
and keyring mapping, recovery point and validation plan.

## Rollback and incident recovery

Record a pre-release checkpoint, previous immutable images, compatible configuration,
database revision and key references before every rollout.

Application rollback is allowed only when the previous binary is verified compatible
with the migrated schema, stored data/key formats, queue payloads and all security
controls already activated. An incompatible schema or changed authority requires a
corrective release. In particular, never restore an env-reading credential path or a
binary that ignores task membership/archival. Revocations and audit history must survive.

Use roll-forward migrations. Do not run `alembic downgrade` as an outage response:
[the development guide](../guides/development.md#keys-and-storage) documents migrations
that reject downgrade and early ones that destructively drop data. On partial migration
failure, keep traffic closed, inspect the committed revision/schema and apply a reviewed
forward repair. Do not stamp a revision or bypass constraints to make startup pass.

If forward repair cannot recover within the accepted outage window, the owner must
authorize database restoration and its measured loss window.
Restore into a new private
database, recover the selected base/WAL point with matching objects and keyrings, and
reapply compatible migrations and role verification before switching endpoints.
Keep
the damaged environment isolated for reconciliation.
Reapply post-checkpoint revocations and deliberate
session invalidation before reopening.
Re-run isolation, object decryption and workflow
smokes, then record the actual RPO/RTO and affected data with the owner.

## Security review gates

Each check requires observable acceptance in the intended role, not only configuration
inspection. Existing controls remain defined in the linked contracts.

- [ ] **Identity and token scope:** test active Membership before org switching,
  task membership/archival/review-domain enforcement, platform password plus TOTP,
  and trusted-proxy authentication limits. API tokens must fail to obtain human-only
  confirmation/export or `token:create`; inspect
  [`api_tokens.token_forbidden_creation_scope`](../../server/migrations/baseline/10_tables.sql)
  and [Task access](../../server/app/services/task_access.py). Track any
  remaining token-management interface limitations through roadmap F05.
- [ ] **RLS and credential functions:** complete the database gate as `bid_app`, with
  two orgs and missing context, plus dedicated-role positive/negative tests. Verify
  cross-org summary functions expose no business content and credential metadata
  never reveals ciphertext or plaintext. Test disabled credentials fail closed.
- [ ] **Human gates and authentic materials:** unconfirmed or stale evidence cannot
  enter confirmed response/export paths; prototype final-use decisions and single-domain
  human/material/B02 gates remain enforced. The legacy response-round trigger
  compatibility blocker is tracked in [Known defects](roadmap.md#known-defects). Review
  [human-section exports](../notes/human-section-exports.md) and
  [screenshot evidence](../notes/screenshot-evidence.md); search/model output never
  becomes authentic hardware evidence without the required source and human checks.
- [ ] **CSP and browser delivery:** verify the production proxy preserves
  `CONSOLE_HEADERS` from [API assembly](../../server/app/api/main.py), including CSP,
  frame denial, `nosniff` and no-referrer; test `/app` routing and no-store HTML.
  Do not loosen CSP to compensate for a bad asset build. Serve only the intended build.
- [ ] **Signed downloads and storage:** test expiry, tampering, wrong org and revoked
  authority on the selected store; preserve encrypted org-prefixed objects and private
  bucket access. No proxy log or cache may retain bearer tokens or signed query strings.
- [ ] **Outbound and secret boundaries:** inspect image layers/manifests/log samples
  for secret leakage, confirm data/token/provider-secret separation, default outbound
  redaction and consented provider policy. Accept exact sandbox policies and broker
  egress without weakening TLS verification for unreachable vendor sites.
- [ ] **Runtime and recovery:** verify non-root business/renderer execution, runsc
  enforcement, seccomp/resource bounds, supervisor cleanup and secret/backup recovery.
  CI-only fake providers, synthetic runc and skipped database cases do not pass these gates.

## Acceptance before go-live

Keep reproducible scripts/commands, sanitized Result JSON, JUnit/browser reports,
document hashes, approved public-input identifiers and manual verdicts outside `docs/`.
Local rehearsal artifacts may use ignored `data/work/production-acceptance/`; production
records belong in restricted operational storage. Do not preserve login tokens, TOTP
screens, signed URLs or private document content in general-purpose reports.

| Gate | Proposed execution and pass condition |
| --- | --- |
| Release checks and migration rehearsal | Run the actual CI checks and [development verification](../guides/development.md#run-the-checks) on disposable PostgreSQL, including runtime-role/RLS, credential, token and workflow suites. Save machine-readable results and the image inventory. Relevant skips or mock-only coverage remain explicit failures of the corresponding live gate. |
| Container smoke | Use [container_smoke.py](../../scripts/container_smoke.py) only with a dedicated test Docker daemon/project and a new output directory. Verify API, real worker, encrypted S3 writes and isolated downloads. Its `result.json` covers its synthetic flow; also test the final production manifest, console and chosen storage service. |
| Human and token end-to-end journey | On the built console and CLI, use acceptance org A/B and intended admin, bidder, technical reviewer, observer and token roles. Upload/parse a public tender, extract/review requirements, select pinned resources, collect/confirm evidence, draft, check/score and export. Verify denied foreign-org/task access, stale approvals, token human-gate attempts, archive writes and SSE reconnect. Save sanitized job/output IDs and hashes. The current [native window procedure](../guides/desktop-development.md#verify-without-using-default-account-data) and [synthetic API fixture](../../scripts/org_console_e2e.py) cover local acceptance; they do not establish this superseded hosted journey. |
| Real providers and accounting |[extract_tender.py](../../evals/extract_tender.py) provides a standalone extraction check; also test tenant catalog/BYOK resolution, drafting, check/score and enabled vision/agent paths through real jobs.
Metadata probes are insufficient.
Search acceptance must measure vendor-source coverage and incomplete/rate-limited results; Perplexity expense remains a platform cost until its separate attribution decision is resolved. |
| Export and WPS | Follow [export scale preparation](../guides/development.md#check-an-export-in-word), then open the same final DOCX in the owner's target WPS version. Inspect Chinese fonts, long/repeated table headers, row counts, page breaks, captions, evidence/bookmark references and attachment order; compare with Gotenberg PDF preview and record discrepancies. Word/LibreOffice success is not WPS acceptance. Include review-copy/final-section gates and a denied cross-org S3 download. |
| Sandbox isolation and failure drills | Follow [real pipeline](../guides/sandbox-runtime.md#run-the-real-pipeline-check) and [contract acceptance](sandbox.md#end-to-end-acceptance-after-approval) on the selected Linux/runtime/architecture: zero container egress, seccomp/cgroup enforcement, maximum inputs, CPU/memory/pids/output exhaustion, DNS/redirect/metadata denials, cross-instance canaries, expiry takeover, caller/worker disconnect, supervisor crash, failed cleanup and storage failure. Require no accessible partial artifact, no leaked container and conservative recovered accounting. Preserve packet counters, resource peaks and inventories. |
| Queue and outage recovery | Kill/restart only the isolated acceptance worker, interrupt database/object/converter access and revoke a lease during work.
Use the [known-defects register](roadmap.md#known-defects) to resolve or restrict affected launch paths; agent wake recovery is not proof of generic sandbox recovery. |
| Backup/PITR and alert delivery | Restore the candidate environment from independent backups, recover encrypted data/objects and perform the chosen PITR point. Meet approved measured RPO/RTO, preserve isolation/revocations and reconcile queue/accounting before resume. Trigger each critical alert and confirm delivery to the named operator. |

[sandbox_proxy_acceptance.py](../../scripts/sandbox_proxy_acceptance.py) exercises
synthetic broker attacks but does not establish kernel egress policy.
The former two-org driver was removed by ADR 0012. Current sandbox lifecycle and
fault requirements are in [sandbox acceptance](sandbox.md#end-to-end-acceptance-after-approval).
[sandbox_colima_acceptance.py](../../scripts/sandbox_colima_acceptance.py) targets a
prepared Colima environment; reproduce its relevant gates on the selected Linux node.
None should be run unreviewed against an active production instance.

Go-live requires the owner's recorded approval of the enabled scope, all corresponding
gates and residual defects. A blocked sandbox, WPS, credential, isolation or recovery
gate blocks the affected release scope; a successful HTTP health response cannot waive it.

## Capacity planning

Compare single-host and split application/worker/sandbox deployments at pilot,
expected and peak demand using measured resource assumptions. Record tasks/day,
pages/task, image count/size, model calls/tokens, OCR/search/browser counts,
export volume, retention and peak concurrency. Size CPU/RAM, database connections,
object storage, backup retention, network throughput and queue limits from those
measurements. Product resource usage follows [ADR 0011](../adr/0011-remove-billing.md).
