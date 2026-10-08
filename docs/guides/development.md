---
kind: howto
---

# Develop and verify locally

Run the test suite against a disposable PostgreSQL instance, provision a
development database, and start the API, worker, and containers. The CLI
workflows are in [cli.md](cli.md).

## Prerequisites

- Python and dependency versions: [pyproject.toml](../../pyproject.toml) and
  [uv.lock](../../uv.lock), installed with `uv`.
- PostgreSQL 16 binaries. [scripts/test_runtime.py](../../scripts/test_runtime.py)
  defaults to `/opt/homebrew/opt/postgresql@16/bin`; pass `--bin` for another
  location.
- Tesseract language data for `chi_sim` and `eng` when parsing scanned PDFs.

## Run the checks

The test runtime script creates a PostgreSQL instance that listens only on a
private Unix socket. The `--root` directory must not exist yet.

```sh
uv sync --frozen --extra dev --python 3.12
LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 uv run python scripts/test_runtime.py start --root /tmp/ai-bid-test
source /tmp/ai-bid-test/environment.sh
uv run pytest -q -n 4
uv run ruff check server cli scripts evals
uv run ruff format --check server cli scripts evals
uv run pyright
uv build
uv run python scripts/test_runtime.py stop --root /tmp/ai-bid-test
```

`environment.sh` holds generated test secrets with mode 0600. Source it only in
a local shell without tracing. Tests refuse any database whose name lacks the
`bid_test` prefix, and seed only synthetic users and documents.

Without `BID_TEST_ADMIN_URL`, every database-backed test fails; tests that
need no database still run.
[.github/workflows/check.yml](../../.github/workflows/check.yml) runs the same
checks in CI on pull requests that are not drafts; it skips the suite whose files a
pull request leaves unchanged and can be started by hand from the Actions tab. Open a
pull request as a draft while still pushing fixes, and mark it ready for review to run
the checks once. With `-n`, each pytest-xdist worker creates and migrates its own
`bid_test_gwN` database in the same cluster, because tests truncate shared tables.
CI splits the suite across runners with `BID_TEST_SHARD=index/count`; `shard_of`,
`ISOLATED_SHARD_TESTS` and `PINNED_SHARD_TESTS` in
[conftest.py](../../server/tests/conftest.py) run the longest scale test and the board
latency bounds alone on shard 1 and pin the other long scale tests, and the required `python` check passes only when every
shard does. The same variable reproduces one shard locally.
Tests marked `latency` assert contractual wall-clock bounds; CI runs them in separate
serial jobs (`uv run pytest -q -m latency`) so other workers do not compete for the
runner's cores.

## Provision a development database

The provisioning owner runs migrations and never serves requests. Runtime receives
`bid_app` for tenant work, `bid_platform_app` for credential management, and
`bid_credential_reader` for per-call resolution. The dedicated roles have no table or
tenant access; see [Platform credential authority](../notes/platform-credentials.md).

1. Set `BID_MIGRATION_DATABASE_URL` for the owner and `BID_DATABASE_PASSWORD`
   for the new `bid_app` role.
2. Create the role, run migrations, and install the queue schema:

   ```sh
   uv run python -m app.admin init-db
   ```

3. Create the first organization and admin. The password comes from
   `BID_BOOTSTRAP_PASSWORD` (at least 12 characters); existing identities are
   refused rather than changed.

   ```sh
   uv run python -m app.admin bootstrap --org-name ORG_NAME --email ADMIN_EMAIL
   ```

The command prints the new organization ID. Variable names and their meaning
are listed in [deploy/.env.example](../../deploy/.env.example) and
`Settings` in [server/app/core/config.py](../../server/app/core/config.py).

## Start the API and worker

Both processes need `BID_DATABASE_URL` for `bid_app`, `BID_ENCRYPTION_KEY` for
stored data, and a different Fernet key in `BID_TOKEN_KEY` for sessions and
signed links; [Keys and storage](#keys-and-storage) describes both.
Startup fails if the runtime role is a superuser, can bypass RLS, or owns
application tables.

```sh
uv run uvicorn app.api.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
uv run python -m app.jobs.worker
```

The API documentation at `http://127.0.0.1:8000/docs` uses the HTTP Bearer
scheme: choose **Authorize**, paste the session or token value without the
`Bearer` prefix, and supply `X-Org-Id` on each operation.

## Enable organization applications

1. Apply migrations using the provisioning owner before running the API or worker.
2. Set `BID_ORG_SIGNUP_ENABLED=true` in the runtime environment (Compose passes it
   through). The default is `false`; operator reviews remain available when disabled.
3. Trust only the deployment's own reverse proxy for forwarded client addresses.
   Application source limits use the effective request peer, never a header read by
   the signup handler.
4. Open `/app/apply` to submit a synthetic application, then approve it in the 申请
   tab on `/app/platform/orgs` using a platform operator session. Sign in with the
   applicant email and password to verify the new organization admin membership.
5. Run the worker for daily expiry. The signup protocol and CLI commands are in
   [Organization self-service application](../notes/org-signup.md).

## Configure the extraction model

1. Configure a platform operator and the dedicated credential connections using
   [Manage platform credentials](#manage-platform-credentials).
2. Create an active `catalog_llm` credential, then select it in the platform Models
   page. The provider and canonical endpoint must match. Set vendor and sale prices,
   model name, reasoning options and the default model in that catalog entry.
3. Tenant jobs use their pinned BYOK revision or the platform catalog. They do not
   use standalone deployment settings as a fallback. A missing or disabled credential
   prevents the next request; replacing its key does not change model/cache identity.
4. For standalone evaluation, create a separate active `standalone_llm` credential.
   Set non-secret `BID_LLM_PROVIDER`, `BID_LLM_MODEL`, `BID_LLM_BASE_URL` and pricing
   settings to match that credential. The evaluator requires the reader database URL
   and secrets root. No anonymous or env-key mode is supported.
5. An explicit public-tender evaluation makes paid vendor requests:

   ```sh
   uv run python evals/extract_tender.py --file PUBLIC_TENDER.docx --output data/work/extract-result.json
   ```

Batching and model options are defined by
[llm-providers.md](../notes/llm-providers.md); variable names are in
[deploy/.env.example](../../deploy/.env.example).

## Manage platform credentials

1. Apply migrations with the migration owner. Provision authentication for
   `bid_platform_app` and `bid_credential_reader` through the deployment secret
   manager/database administration channel; the migration creates the restricted
   LOGIN roles without passwords. For password-authenticated development, supply
   `BID_PLATFORM_DATABASE_PASSWORD` and `BID_CREDENTIAL_DATABASE_PASSWORD` only to
   `app.admin init-db` (the Compose migrate service); it assigns those explicit bootstrap
   passwords after migration. Use matching URL-encoded values in the dedicated runtime
   URLs. Peer/certificate deployments may omit these password variables. Do not grant
   either role membership in another role.
2. Inject `BID_PLATFORM_DATABASE_URL` for management, `BID_CREDENTIAL_DATABASE_URL`
   for resolution, and a distinct `BID_SECRETS_KEY` into the appropriate API/worker
   processes. The standalone evaluator requires only the reader URL. Keep root keys,
   database credentials and platform authentication factors outside the service
   credential table. Operator enrollment uses its own
   [authentication-factor authority](../notes/operator-enrollment.md).
3. Sign in through the existing platform password-and-TOTP flow. In `/app/platform/credentials`,
   create the credential, inspect consumers, replace, enable, disable, remove or test it.
   CLI automation uses `bid platform login` followed by these commands:

   ```sh
   bid platform credential list --json
   bid platform credential create --input data/work/credential-metadata.json --key-file data/work/vendor-key --json
   bid platform credential show --id CREDENTIAL_UUID --json
   bid platform credential replace --id CREDENTIAL_UUID --expected-revision REVISION --reason scheduled_rotation --key-file data/work/vendor-key --json
   bid platform credential set-active --id CREDENTIAL_UUID --expected-revision REVISION --inactive --reason incident --json
   bid platform credential test --id CREDENTIAL_UUID --expected-revision REVISION --json
   bid platform credential remove --id CREDENTIAL_UUID --expected-revision REVISION --reason retired --json
   ```

   Metadata JSON contains `name`, `purpose`, `provider`, `endpoint`, optional `active`
   (default false) and `reason`; it never contains `api_key`. Key files must be owned by
   the caller, mode 0600, and have no symbolic-link path components. Use `--active` to
   enable. Conflicts require refreshing the revision, not blindly retrying.
4. For a legacy deployment, stop admissions and drain old workers before cutover.
   Export only the intended legacy vendor assignments to an owner-only env file; do
   not source it into the new API, worker or evaluator. Prepare a manifest containing
   `entries`, each with `name/purpose/provider/endpoint/active/source_env`.

   ```sh
   bid platform credential import-env --manifest data/work/credential-import.json --env-file data/work/legacy-vendor.env --dry-run --json
   bid platform credential import-env --manifest data/work/credential-import.json --env-file data/work/legacy-vendor.env --json
   ```

   The parser does not execute shell expressions. Dry-run makes no writes; same-value
   replays skip existing rows; any mismatch rejects the batch. Import all catalog
   references, then have the migration owner validate
   `platform_model_credential_fk` using `ALTER TABLE public.platform_models VALIDATE
   CONSTRAINT platform_model_credential_fk`. Resolve any mismatch explicitly.
5. Remove `BID_PLATFORM_CREDENTIAL_*`, `BID_PERPLEXITY_API_KEY` and `BID_LLM_API_KEY`
   from every runtime environment/config, including empty legacy assignments and old
   mounts. Startup refuses them by variable name without echoing values. Remove the
   temporary import/key files after the deployment secret source is cleared.
6. Set `BID_SEARCH_PROVIDER` explicitly: `disabled`, `searxng` or `perplexity`.
   Perplexity requires the active `vendor_search` credential. It never falls back to
   SearXNG. Re-submit old search jobs that lack the pinned service credential identity.

Metadata testing does not prove model authorization or available balance. Official
OpenAI/Anthropic model-list probes are bounded; Perplexity and custom endpoints return
`unsupported`. No probe generates content or performs a paid search.

To rotate the encryption root, first distribute the new key as a readable previous key
on all replicas, then switch it to current while retaining the old key in
`BID_SECRETS_KEY_PREVIOUS`. Run with the migration owner and the same root keyring:

```sh
uv run python -m app.admin rotate-encryption --scope provider-secrets
uv run python -m app.admin rotate-encryption --scope provider-secrets
```

The report separates checked/rewritten platform, BYOK-history and operator-factor
counts; `app.admin rotate-provider-secrets` invokes the same scope. Require zero
failures and zero rewrites on the second run before removing retired keys from the online
keyring. Preserve retired keys for the backup retention period. Default `--scope data`
keeps the existing data rotation behavior. Never downgrade to an env-reading release
while serving requests; retain the table/audits and repair forward.

## Configure score requests

1. Set `BID_SCORE_BATCH_CHARS` consistently on the API and workers. Its default is
   `64000` and its minimum is `1000`. This bounds the serialized score request's
   characters, independently of `BID_LLM_BATCH_CHARS`, which continues to control
   extraction and rubric item batching. The Compose runtime forwards this setting.
2. Size it for the selected model alongside `BID_LLM_MAX_OUTPUT_TOKENS`. Reserve
   room for the system prompt, JSON schema, vendor framing and output. Characters
   are not tokens; the default is a bounded long-context starting point, not a
   guarantee that every model accepts it. The precise scope and indivisible-item
   rule are in the [score Provider contract](../plan/score.md#providers-jobs-and-prepaid-billing).
3. Preview `bid score run --task UUID --draft UUID --rubric UUID --as-of YYYY-MM-DD
   --dry-run` after changing the setting. `score_context_limit` with
   `cost_basis_reason=context_limit` means one complete item, including all confirmed
   bid-side candidates, still exceeds the configured limit. Inspect the fixed input
   and adjust the setting within the model's capacity before previewing and submitting
   again; retries do not remove evidence or truncate text.

## Configure job guards

1. Set these values consistently on the API and all workers before starting
   model jobs:

   | Setting | Default | Meaning |
   | --- | --- | --- |
   | `BID_JOB_MAX_CHARGE` | `10` | Positive platform charge ceiling for one job, in `BID_BILLING_CURRENCY`, across all attempts |
   | `BID_JOB_MAX_VENDOR_CALLS` | `64` | Minimum vendor-request ceiling per job, including retries and unresolved requests |
   | `BID_JOB_VENDOR_CALLS_PER_BATCH` | `4` | Requests allowed per first-pass batch; the per-job ceiling is the larger of this times the batches and `BID_JOB_MAX_VENDOR_CALLS`, so large documents can finish while runaway halving still stops |
   | `BID_JOB_LEASE_SECONDS` | `900` | Lease duration in seconds; at least 3 |
   | `BID_JOB_HEARTBEAT_SECONDS` | `30` | Positive renewal interval; at most one third of the lease duration |

2. Apply [0016_vendor_call_guards.py](../../server/migrations/versions/0016_vendor_call_guards.py)
   with the migration owner before running the new workers. Quiesce old
   workers during rollout: an older binary does not participate in admission.
   Do not mix guarded and unguarded workers when relying on the spending bound.
3. Ensure configured endpoint prices and token limits satisfy the
   [reservation contract](../notes/prepaid-billing.md#admission-and-the-spending-bound).
   A positive balance can still be insufficient for a whole request's reserved
   bound. Set ceilings to the intended workload; retrying the same job keeps
   its previous call count, charges and unresolved reservations.
4. Run the API/processor guard scenarios against the disposable PostgreSQL
   runtime from [Run the checks](#run-the-checks):

   ```sh
   uv run pytest -q server/tests/test_vendor_call_guards.py server/tests/test_llm_providers.py server/tests/test_billing.py server/tests/test_job_boundaries.py --basetemp=/private/tmp/bid-call-guards --junitxml=artifacts/vendor-call-guards.xml
   ```

   Vendors use `httpx.MockTransport`. The guard scenarios write synthetic
   accounting snapshots beneath `--basetemp`; JUnit records passes, failures
   and skips. A missing or sandbox-inaccessible PostgreSQL instance does not
   validate reservations, row locks, migrations or tenant isolation. Keep
   evidence outside `docs/`.

## Run the platform console

The console at `/app` is for platform operators; it shows org accounts and
usage totals, never org business data.

1. Apply migrations with the provisioning owner. Set `BID_PLATFORM_ADMIN_EMAILS`
   to the operator emails and inject the independent `BID_SECRETS_KEY` on the API.
   Leave `BID_PLATFORM_TOTP_SECRETS` empty for browser-managed factors.
2. Build the console and point the API at it:

   ```sh
   cd web && npm ci && npm run build
   ```

   Set `BID_WEB_DIR` to the absolute path of `web/dist`, restart the API, and
   open `http://127.0.0.1:8000/app/`. For live editing, run `npm run dev` in
   `web/` with `BID_API_URL` pointing at the API.
3. Issue the first operator's enrollment link on the trusted host:

   ```sh
   uv run python -m app.admin platform-enroll OPERATOR_EMAIL
   ```

   Deliver the printed relative link privately and open it on the console origin.
   Links expire after 30 minutes. Set a password for a new/setup-only account or
   confirm its existing password, scan the QR code or enter the shown secret, and
   submit a current authenticator code. Enrollment creates no org membership.
4. Wait for the next authenticator code and sign in at `/app/platform/login`.
   Signed-in operators can issue another allowlisted email's link from 平台管理员;
   the host command also supports device recovery. Create any required org
   separately in the console and attach the operator's existing email. Manage
   catalog keys through [Manage platform credentials](#manage-platform-credentials).

For the deployment-managed break-glass alternative, use an account with a usable
password, generate a factor privately, and set its printed `email:SECRET` pair in
`BID_PLATFORM_TOTP_SECRETS` before restarting:

```sh
uv run python -m app.admin platform-totp --email OPERATOR_EMAIL
```

An environment factor always wins over the stored factor and blocks browser
enrollment for that email. Remove the override and restart before returning to
browser enrollment. Enrollment-token binding, key rotation and failure semantics
are defined by [Operator enrollment](../notes/operator-enrollment.md).

Set `BID_BILLING_CURRENCY` (an ISO 4217 code, default `USD`) before any org has
a balance; catalog sale prices, charges, balances and card values all use it,
and startup refuses balances stored in another currency. Org admins use
`http://127.0.0.1:8000/app/org/login` to see their balance and redeem cards.

If sign-in or org lookup returns `too_many_attempts` (HTTP 429), stop retrying
and wait for `Retry-After` before retrying; switching pages or orgs does not
reset the account's limit. For `auth_busy` (HTTP 503), wait for `Retry-After`
and retry with the current authenticator code. Both errors use CLI exit code 3.
After a successful platform sign-in, wait for a new authenticator code before
signing in again. The shared account/source limits, proxy requirements and
concurrency bounds are defined in
[Password admission and TOTP consumption](../notes/platform-console.md#password-admission-and-totp-consumption).

The end-to-end check drives a real browser through sign-in, provisioning,
models, usage, CSV export, disabling and password setup against a running API
that serves the build. It needs an operator account with a known password and
TOTP secret, and writes screenshots, the CSV and `result.json` to a new
directory:

```sh
cd web && E2E_BASE_URL=http://127.0.0.1:8000 E2E_EMAIL=OPERATOR_EMAIL E2E_PASSWORD=OPERATOR_PASSWORD E2E_TOTP_SECRET=SECRET E2E_OUTPUT=/tmp/console-e2e npx playwright test
```

The check expects an org named 计费演示单位 with platform-billed usage in the
current month and creates an org, a model named `e2e-model` and recharge cards (充值卡密),
so run it against a disposable database.

## Check an export in Word

The opt-in scale scenario builds a final section (正式件) with long synthetic tables and one
confirmed certificate page per attachment, and writes it to
`data/work/export-acceptance/scale/`:

```sh
BID_EXPORT_SCALE_PAGES=130 uv run pytest -q server/tests/test_export_scale.py
```

Word on macOS asks for file access for every new folder it opens or saves to. Copy the
DOCX into `~/Library/Containers/com.microsoft.Word/Data/` first, then report its
pages, tables and rows, inline pictures, bookmarks and protection, and save a PDF next
to it:

```sh
osascript scripts/word_inspect.applescript DOCX_PATH PDF_PATH
```

Check that the table rows add up to the requirements, that pictures and bookmarks
match the attachment pages, and that every attachment page shows its caption above
its image in the PDF.

## Run with Docker Compose

1. Copy [deploy/.env.example](../../deploy/.env.example) to an ignored env file
   and fill every value. Migration credentials go only to the `migrate` service.
2. Start the stack from [deploy/docker-compose.yml](../../deploy/docker-compose.yml):
   PostgreSQL with pgvector, MinIO, SearXNG for vendor-source search, Gotenberg
   for export page previews, and separate migrate, server, and worker
   containers. SearXNG and Gotenberg publish no host port; the worker reaches
   them at `http://searxng:8080` and `http://converter:3000`.
3. Storage defaults to a shared local volume. To use S3, create a private
   bucket first, then set `BID_STORAGE=s3` and `BID_S3_CONTAINER_ENDPOINT`. The
   host endpoint and the container endpoint are different addresses.

The MinIO image is built from pinned community source by
[deploy/minio.Dockerfile](../../deploy/minio.Dockerfile) for development only;
choosing a production object store is an open decision in
[the roadmap](../plan/roadmap.md#open-decisions).

[scripts/container_smoke.py](../../scripts/container_smoke.py) builds and
exercises the whole stack in a fresh Compose project with generated
credentials, then stops only its own services:

```sh
uv run python scripts/container_smoke.py --host unix:///PATH/TO/docker.sock --root /tmp/ai-bid-container-test
```

Point `--host` at a dedicated test Docker daemon, never a production one. With
Colima, a separate profile keeps existing contexts untouched; a short
`LIMA_HOME` avoids the Unix socket path length limit:

```sh
COLIMA_HOME=/tmp/colima-cfg COLIMA_CACHE_HOME=/tmp/colima-cache LIMA_HOME=/tmp/colima-vm DOCKER_CONFIG=/tmp/docker-cfg colima start ai-bid-test --activate=false --ssh-config=false --template=false --mount none --cpus 2 --memory 2 --disk 16 --root-disk 4 --binfmt=false
```

## Run export previews locally

Export page previews convert the released DOCX with the Gotenberg image that
[deploy/docker-compose.yml](../../deploy/docker-compose.yml) pins. On a local
Docker daemon, publish it on the loopback address only:

```sh
docker run -d --name bid-gotenberg -p 127.0.0.1:3300:3000 gotenberg/gotenberg:8.37.0 gotenberg --chromium-disable-routes=true --webhook-disable=true --api-timeout=180s
```

Set `BID_CONVERTER_URL=http://127.0.0.1:3300` for the API and worker and restart
them. Check a real conversion with:

```sh
BID_CONVERTER_LIVE_URL=http://127.0.0.1:3300 uv run pytest -q server/tests/test_page_previews.py
```

## Run vendor search locally

Vendor-source search (`bid evidence search`) and product simulation follow the explicit
`BID_SEARCH_PROVIDER` selection. Choose `perplexity` with an active `vendor_search`
credential, or `searxng` with `BID_SEARCH_URL`; the default is `disabled`.
SearXNG scrapes public search engines, which answer a busy
address with CAPTCHAs or rate limits until they lift the block. It is configured by
[deploy/searxng/settings.yml](../../deploy/searxng/settings.yml). Without a
container daemon, run it from the source revision that the Compose image tag
names, in its own environment outside the project's:

```sh
git clone https://github.com/searxng/searxng.git data/work/searxng/src
git -C data/work/searxng/src checkout 19ffbcd30
uv venv --python 3.12 data/work/searxng/venv
VIRTUAL_ENV=data/work/searxng/venv uv pip install setuptools wheel -r data/work/searxng/src/requirements.txt -r data/work/searxng/src/requirements-server.txt
VIRTUAL_ENV=data/work/searxng/venv uv pip install --no-build-isolation -e data/work/searxng/src
SEARXNG_SETTINGS_PATH=$PWD/deploy/searxng/settings.yml SEARXNG_SECRET=$(openssl rand -hex 32) data/work/searxng/venv/bin/python -m searx.webapp
```

It listens on `127.0.0.1:8888`. Set `BID_SEARCH_URL=http://127.0.0.1:8888` for
the API and worker and restart them. Check it against a real product with:

```sh
BID_SEARCH_LIVE_URL=http://127.0.0.1:8888 BID_SEARCH_LIVE_PRODUCT='新华三|S5130S-EI' uv run pytest server/tests/test_vendor_search.py -k live
```

## Check local OCR

The container image ships English and Simplified Chinese data and defaults to
`chi_sim+eng`. Native runs need `BID_OCR_DATA_DIR` pointing at a directory with
both files. To check recognition without any network call:

```sh
uv run python scripts/ocr_smoke.py --data-dir TESSDATA_DIR --output /tmp/ocr-result.json
```

A passing language check is not an accuracy measurement. OCR can misread
characters, so compare the text with the page image before accepting a
citation.

## Keys and storage

Stored files and encrypted database fields use `BID_ENCRYPTION_KEY`; file
ciphertext is bound to its object key. Sessions and signed download links use
`BID_TOKEN_KEY`, which must differ from every other key. Org provider
credentials use `BID_SECRETS_KEY` ([provider-config.md](../notes/provider-config.md)).
Startup fails when a key is missing, malformed or reused. Keep all of them in
your secret and backup workflow. Plaintext conversion is not automated; a file
encrypted with an unknown key fails with `unreadable_file`.

To replace the token key, set a new `BID_TOKEN_KEY` on the API and workers and
restart them. Every outstanding session and link stops working at once, so
users sign in again.

To replace the data key:

1. Generate a new Fernet key. On the API and all workers, set it as
   `BID_ENCRYPTION_KEY`, put the old key in `BID_ENCRYPTION_KEY_PREVIOUS`
   (several retired keys are comma separated), and restart. New data uses the
   new key; data under the old key stays readable.
2. Rewrite existing data with the migration owner, the same key variables, and
   the same storage settings as the API:

   ```sh
   uv run python -m app.admin rotate-encryption
   ```

   It goes through every org's encrypted fields and stored objects and prints
   the checked and rewritten counts as JSON. Values already under the new key
   are skipped, so an interrupted run can be repeated; a second run reports zero
   rewrites.
3. Remove `BID_ENCRYPTION_KEY_PREVIOUS` and restart. Keep the old key as long as
   backups taken before the rotation are retained, because they still need it.

Downgrades from `0003` onward raise an error instead of dropping history
tables; the `0001` and `0002` downgrades do drop their tables and columns. To
recover credential cutover failures, retain the table and audit history and repair forward;
an env-reading application version cannot safely serve after revocation. Each migration's tables
and constraints are described in the matching note listed in the
[documentation index](../README.md#mechanism-notes).

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `initdb` reports an invalid locale | The shell locale is not installed for PostgreSQL. Prefix the start command with `LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8`. |
| Tests fail with `cannot use a string pattern on a bytes-like object` | The cluster was created with `SQL_ASCII` encoding, for example under `LC_ALL=C`. Stop it, delete its directory, and start a new one with a UTF-8 locale. |
| Server exits with `Runtime database role must not be superuser or BYPASSRLS` | `BID_DATABASE_URL` uses the owner account. Use the `bid_app` URL. |
| Compose build stops with no space left | Increase the test VM disk, for example `--disk 16` on Colima. |
