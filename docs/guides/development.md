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
`bid_test` prefix, and seed only synthetic tasks and documents.

Without `BID_TEST_ADMIN_URL`, every database-backed test fails; tests that
need no database still run.
[.github/workflows/check.yml](../../.github/workflows/check.yml) runs the same
checks in CI on pull requests that are not drafts; it skips the suite whose files a
pull request leaves unchanged and can be started by hand from the Actions tab. Open a
pull request as a draft while still pushing fixes, and mark it ready for review to run
the checks once; the required `python` and `web` checks fail on drafts so a draft cannot
be merged on skipped checks. With `-n`, each pytest-xdist worker creates and migrates its own
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

The provisioning owner runs migrations and never serves requests. The API and worker
use `bid_app`, which cannot own application tables or bypass their triggers.

1. Set `BID_MIGRATION_DATABASE_URL` for the owner and `BID_DATABASE_PASSWORD`
   for the new `bid_app` role.
2. Create the role, run migrations, and install the queue schema:

   ```sh
   uv run python -m app.admin init-db
   ```

3. Set `BID_ACCESS_KEY` to a random secret of at least 32 characters in the local
   environment. Supply it as a bearer credential or exchange it through
   `POST /auth/session` with `{"access_key": "..."}`. `GET /auth/session` describes
   the current actor and scopes.
4. For CLI authentication, set `BID_ACCESS_KEY`, or set `BID_ACCESS_KEY_FILE` to an
   owner-only regular file containing the key. `BID_TOKEN` accepts a scoped `bid_`
   token when neither key source is set. No login command or session file is needed.

Variable definitions are in [deploy/.env.example](../../deploy/.env.example) and
`Settings` in [server/app/core/config.py](../../server/app/core/config.py).

## Start the API and worker

Both processes need `BID_ACCESS_KEY`, `BID_DATABASE_URL` for `bid_app`, `BID_ENCRYPTION_KEY` for
stored data, and a different Fernet key in `BID_TOKEN_KEY` for sessions and
signed links; [Keys and storage](#keys-and-storage) describes both.
Startup fails if the runtime role is a superuser, can bypass RLS, or owns
application tables.

```sh
uv run uvicorn app.api.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
uv run python -m app.jobs.worker
```

The API documentation at `http://127.0.0.1:8000/docs` uses the HTTP Bearer
scheme: choose **Authorize**, paste the access key, session or API token value without the
`Bearer` prefix.

## Configure the extraction model

Use encrypted local provider revisions for extraction jobs. The resolver in
[configured.py](../../server/app/providers/configured.py) pins the selected
revision to each job. Follow the provider-management work in
[the single-user plan](../plan/single-user.md) when adapting configuration entrypoints.

Standalone evaluation uses a `standalone_llm` service credential, the runtime
database URL and provider-secret root. Its entrypoint adaptation is tracked in
[the single-user plan](../plan/single-user.md); the caller is
[evals/extract_tender.py](../../evals/extract_tender.py).

Batching and model options are defined by
[llm-providers.md](../notes/llm-providers.md); variable names are in
[deploy/.env.example](../../deploy/.env.example).

## Manage service credential roots

Use the runtime `BID_DATABASE_URL` for local service credentials and a distinct
`BID_SECRETS_KEY` for provider secrets. Credential settings HTTP and CLI changes
are tracked in [the single-user plan](../plan/single-user.md).

To rotate the provider-secret encryption root, retain the old root in
`BID_SECRETS_KEY_PREVIOUS` and set the new root in `BID_SECRETS_KEY`. Run with the
migration owner and the same keyring:

```sh
uv run python -m app.admin rotate-provider-secrets
uv run python -m app.admin rotate-provider-secrets
```

Require zero failures and zero rewrites on the second run before removing the
retired root from the runtime keyring. Retain roots needed to decrypt backups.
Use `rotate-encryption` for stored files and encrypted application fields.

## Configure score requests

1. Set `BID_SCORE_BATCH_CHARS` consistently on the API and workers. Its default is
   `64000` and its minimum is `1000`. This bounds the serialized score request's
   characters, independently of `BID_LLM_BATCH_CHARS`, which continues to control
   extraction and rubric item batching. The Compose runtime forwards this setting.
2. Size it for the selected model alongside `BID_LLM_MAX_OUTPUT_TOKENS`. Reserve
   room for the system prompt, JSON schema, vendor framing and output. Characters
   are not tokens; the default is a bounded long-context starting point, not a
   guarantee that every model accepts it. The precise scope and indivisible-item
   rule are in the [score Provider contract](../plan/score.md#providers-jobs-and-usage).
3. Preview `bid score run --task UUID --draft UUID --rubric UUID --as-of YYYY-MM-DD
   --dry-run` after changing the setting.
Inspect the fixed input
   and adjust the setting within the model's capacity before previewing and submitting
   again; retries do not remove evidence or truncate text.

## Configure job guards

1. Set `BID_JOB_MAX_VENDOR_CALLS`, `BID_JOB_VENDOR_CALLS_PER_BATCH`,
   `BID_JOB_LEASE_SECONDS` and `BID_JOB_HEARTBEAT_SECONDS` consistently on the API
   and all workers.
2. Run `uv run python -m app.admin init-db` with the migration owner before running
   the workers.
Quiesce old
   workers during rollout: an older binary does not participate in admission.
3. Run the API/processor guard scenarios against the disposable PostgreSQL
   runtime from [Run the checks](#run-the-checks):

   ```sh
   uv run pytest -q server/tests/test_vendor_call_guards.py server/tests/test_llm_providers.py server/tests/test_job_boundaries.py --basetemp=/private/tmp/bid-call-guards --junitxml=artifacts/vendor-call-guards.xml
   ```

   Vendors use `httpx.MockTransport`.
The guard scenarios write synthetic
   accounting snapshots beneath `--basetemp`; JUnit records passes, failures
   and skips.
Keep
   evidence outside `docs/`.

## Build the console

```sh
cd web && npm ci && npm run build
```

Set `BID_WEB_DIR` to the absolute path of `web/dist` and restart the API. For live
editing, run `npm run dev` in `web/` with `BID_API_URL` pointing at the API. Console
access-key integration is tracked in [the single-user plan](../plan/single-user.md).

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
`BID_TOKEN_KEY`, which must differ from every other key. Provider
credentials use `BID_SECRETS_KEY` ([provider-config.md](../notes/provider-config.md)).
Startup fails when a key is missing, malformed or reused. Keep all of them in
your secret and backup workflow. Plaintext conversion is not automated; a file
encrypted with an unknown key fails with `unreadable_file`.

To replace the token key, set a new `BID_TOKEN_KEY` on the API and workers and
restart them. Every outstanding session and link stops working at once, so
mint a new session using the installation access key.

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

   It goes through all encrypted fields and stored objects and prints
   the checked and rewritten counts as JSON. Values already under the new key
   are skipped, so an interrupted run can be repeated; a second run reports zero
   rewrites.
3. Remove `BID_ENCRYPTION_KEY_PREVIOUS` and restart. Keep the old key as long as
   backups taken before the rotation are retained, because they still need it.

Use the upgrade path in
[0001_single_user_baseline.py](../../server/migrations/versions/0001_single_user_baseline.py).
Keep database and encrypted-file backups together with the keys needed to read them.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `initdb` reports an invalid locale | The shell locale is not installed for PostgreSQL. Prefix the start command with `LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8`. |
| Tests fail with `cannot use a string pattern on a bytes-like object` | The cluster was created with `SQL_ASCII` encoding, for example under `LC_ALL=C`. Stop it, delete its directory, and start a new one with a UTF-8 locale. |
| Server exits with `Runtime database role must not be superuser or BYPASSRLS` | `BID_DATABASE_URL` uses the owner account. Use the `bid_app` URL. |
| Compose build stops with no space left | Increase the test VM disk, for example `--disk 16` on Colima. |
