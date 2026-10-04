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
uv run pytest -q
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
checks in CI.

## Provision a development database

Two credentials are involved. The provisioning owner runs migrations; the
restricted `bid_app` role is the only one the server and worker receive.

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

## Configure the extraction model

`bid req extract` uses the platform model configured on the API and the worker.
With `BID_LLM_PROVIDER=disabled`, the default, extraction fails with
`provider_unavailable`. All variables are listed in
[deploy/.env.example](../../deploy/.env.example); keep the key in an ignored
env file or your secret manager.

1. Choose a provider:
   - Anthropic: `BID_LLM_PROVIDER=anthropic` and `BID_LLM_API_KEY`. The model
     defaults to `claude-opus-5-5`; override it with `BID_LLM_MODEL`.
   - Any OpenAI-compatible service: `BID_LLM_PROVIDER=openai`, `BID_LLM_MODEL`,
     `BID_LLM_BASE_URL` (for example `https://api.openai.com/v1`), and
     `BID_LLM_API_KEY` unless the service needs none. If the service rejects
     JSON Schema output, set `BID_LLM_JSON_MODE=json_object`.
2. Set `BID_LLM_INPUT_USD_PER_MTOK` and `BID_LLM_OUTPUT_USD_PER_MTOK` to the
   vendor's prices per million tokens. Without them, usage records store
   tokens but `usd` is `null`.
3. Optional tuning: `BID_LLM_BATCH_CHARS` (characters per request, default
   8000), `BID_LLM_CONCURRENCY` (parallel requests, default 4),
   `BID_LLM_TIMEOUT_SECONDS` (total deadline per request, default 600), and
   `BID_LLM_REQUEST_OPTIONS`, a JSON object added to every request body, for
   example `{"thinking": {"type": "disabled"}}` for models that otherwise
   think until the output limit. Catalog models with reasoning levels use each
   level's own options and batch size instead.
4. Restart the API and the worker. `GET /health` reports
   `real_llm_configured: true`. A selected provider with a missing key or
   model stops startup instead of falling back to disabled.
5. Check the model on a public tender before real use. The script accepts a
   PDF or Word file, calls the vendor, and costs money; it reports verified
   citations and ★ recall and writes every item with its position and a
   `citation_verified` flag:

   ```sh
   uv run python evals/extract_tender.py --file PUBLIC_TENDER.docx --output /tmp/extract-result.json
   ```

Changing the provider, model, or prompt changes the job cache key, so the
next `req extract` runs again instead of returning the cached job. How
batching, errors, and costs work is described in
[llm-providers.md](../notes/llm-providers.md).

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

1. Create the operator's account. An existing user works; otherwise create one
   with `bootstrap` from [Provision a development database](#provision-a-development-database).
2. Generate a TOTP secret and scan the printed URI in an authenticator app:

   ```sh
   uv run python -m app.admin platform-totp --email OPERATOR_EMAIL
   ```

3. Set `BID_PLATFORM_ADMIN_EMAILS` to the operator emails and
   `BID_PLATFORM_TOTP_SECRETS` to the printed `email:SECRET` pairs, comma
   separated. For each catalog model credential, set
   `BID_PLATFORM_CREDENTIAL_<NAME>` to the vendor key.
4. Build the console and point the API at it:

   ```sh
   cd web && npm ci && npm run build
   ```

   Set `BID_WEB_DIR` to the absolute path of `web/dist`, restart the API, and
   open `http://127.0.0.1:8000/app/`. For live editing, run `npm run dev` in
   `web/` with `BID_API_URL` pointing at the API.

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
current month and creates an org, a model named `e2e-model` and recharge cards,
so run it against a disposable database.

## Run with Docker Compose

1. Copy [deploy/.env.example](../../deploy/.env.example) to an ignored env file
   and fill every value. Migration credentials go only to the `migrate` service.
2. Start the stack from [deploy/docker-compose.yml](../../deploy/docker-compose.yml):
   PostgreSQL with pgvector, MinIO, SearXNG for vendor-source search, and
   separate migrate, server, and worker containers. SearXNG publishes no host
   port; the worker reaches it at `http://searxng:8080`.
3. Storage defaults to a shared local volume. To use S3, create a private
   bucket first, then set `BID_STORAGE=s3` and `BID_S3_CONTAINER_ENDPOINT`. The
   host endpoint and the container endpoint are different addresses.

The MinIO image is built from pinned community source by
[deploy/minio.Dockerfile](../../deploy/minio.Dockerfile) for development only;
choosing a production object store is an open decision in
[the roadmap](../plan/roadmap.md#待定决定).

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

## Run vendor search locally

Vendor-source search (`bid evidence search`) queries a private SearXNG
instance configured by
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
roll back, deploy the previous application version and keep the tables. Each migration's tables
and constraints are described in the matching note listed in the
[documentation index](../README.md#机制笔记).

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `initdb` reports an invalid locale | The shell locale is not installed for PostgreSQL. Prefix the start command with `LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8`. |
| Tests fail with `cannot use a string pattern on a bytes-like object` | The cluster was created with `SQL_ASCII` encoding, for example under `LC_ALL=C`. Stop it, delete its directory, and start a new one with a UTF-8 locale. |
| Server exits with `Runtime database role must not be superuser or BYPASSRLS` | `BID_DATABASE_URL` uses the owner account. Use the `bid_app` URL. |
| Compose build stops with no space left | Increase the test VM disk, for example `--disk 16` on Colima. |
