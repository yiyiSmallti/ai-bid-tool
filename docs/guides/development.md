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

Without `BID_TEST_ADMIN_URL`, the database-backed tests are skipped rather
than failed, so a green run without the environment file proves little.
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

Both processes need `BID_DATABASE_URL` for `bid_app` and `BID_ENCRYPTION_KEY`.
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
3. Restart the API and the worker. `GET /health` reports
   `real_llm_configured: true`. A selected provider with a missing key or
   model stops startup instead of falling back to disabled.
4. Check the model on a public tender before real use. The script calls the
   vendor and costs money; it writes every extracted item with a
   `quote_is_verbatim` flag:

   ```sh
   uv run python evals/extract_tender.py --pdf PUBLIC_TENDER.pdf --output /tmp/extract-result.json
   ```

Changing the provider, model, or prompt changes the job cache key, so the
next `req extract` runs again instead of returning the cached job. How
batching, errors, and costs work is described in
[llm-providers.md](../notes/llm-providers.md).

## Run with Docker Compose

1. Copy [deploy/.env.example](../../deploy/.env.example) to an ignored env file
   and fill every value. Migration credentials go only to the `migrate` service.
2. Start the stack from [deploy/docker-compose.yml](../../deploy/docker-compose.yml):
   PostgreSQL with pgvector, MinIO, and separate migrate, server, and worker
   containers.
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

Original files are encrypted with `BID_ENCRYPTION_KEY` in both storage modes,
and the ciphertext is bound to its object key. Keep the key in your secret and
backup workflow. Key rotation and plaintext conversion are not automated; a
file encrypted with another key fails with `unreadable_file`.

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
