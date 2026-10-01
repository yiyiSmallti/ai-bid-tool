---
kind: howto
---

# Use the bid CLI

Log in, run the tender workflow, maintain versioned organization resources,
and handle results. Humans and agents use the same commands. `bid schema --json`
lists every implemented command with its flags, input schemas, and the shared
output schema; it is generated from [cli/bid_cli/schema.py](../../cli/bid_cli/schema.py)
and is the authority on what exists.

## Set up a session

Start the API and worker as described in [development.md](development.md).
Every command takes the same global options:

| Option | Meaning |
| --- | --- |
| `--mode remote` | Call the server at `--server`. HTTPS is required except on loopback. |
| `--mode local` | Run the API in-process against the local PostgreSQL, with the same RLS, membership, and scope checks. The worker is still a separate process. |
| `--state PATH` | Encrypted session file, written with mode 0600. |

Credentials come from the environment, never from arguments or prompts:

- `BID_PASSWORD`: the password for `login`.
- `BID_CLI_KEY`: the key that encrypts the session file and token output files.
- `BID_SESSION` and `BID_ORG`: an existing session, used instead of a state file.

```sh
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE login --email YOUR_EMAIL --org ORG_ID --json
```

Pass the same `--server` on every remote call; the CLI does not remember it.
Logging in to one organization grants nothing in another.

## Run the tender workflow

Uppercase values are placeholders. Use the IDs each command returns, not names
guessed from local files.

```sh
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE task create --name TASK_NAME --tender TENDER.pdf --deadline 2027-01-31T17:00:00+08:00 --json
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE tender parse --document DOCUMENT_ID --wait --timeout 120 --json
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE req extract --document DOCUMENT_ID --dry-run --json
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE req extract --document DOCUMENT_ID --wait --json
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE req list --task TASK_ID --json
```

1. `task create --tender` creates the task, uploads the file, and starts
   parsing. `tender upload --task TASK_ID --file FILE` adds another file later.
2. `tender parse --wait` blocks until the job finishes or the timeout expires.
   A timeout exits 3 and does not cancel the server job; continue with
   `job wait JOB_ID` or `job status JOB_ID`.
3. `req extract --dry-run` reports whether the document is parsed without
   calling a model or creating a job. Cost estimates are `null` when unknown.
4. `req extract` requires a parsed PDF whose pages have verified citations
   and a configured model ([development.md](development.md#configure-the-extraction-model)).
   Without one it fails with `provider_unavailable` and exit 4. If any quote
   is not verbatim source text, the job fails with `invalid_citation` and
   saves nothing.
5. `job cancel JOB_ID` stops a queued or running job; a cancelled attempt can
   no longer save results. To run a failed, cancelled, or expired job again,
   repeat the parse or extract command with `--retry`. Repeating it without
   `--retry` returns the existing job.

Word files keep their text and tables but have no verified page numbers.
Convert them to PDF before extracting cited requirements.

## Handle results

With `--json`, every command prints one object with exactly the keys `ok`,
`command`, `data`, `items`, `warnings`, `cost`, and `duration_ms`. Errors are
in `data.error` with a stable `code` and the exit code. The models are
`Result` in [server/app/schemas/contracts.py](../../server/app/schemas/contracts.py),
and exit code meanings are fixed by the CLI contract in
[agent.md](../../agent.md#硬性规则任何情况下都不得违反).

| Exit | What to do next |
| --- | --- |
| 0 | Continue with the returned IDs and items. |
| 2 | Fix the input or output path, then retry. |
| 3 | Back off, then retry the transient failure or wait timeout. |
| 4 | Stop retrying; read the denied, missing, conflict, or integrity error. |
| 5 | Keep the completed IDs and retry only the unfinished part. |

`task create --tender` exits 5 when the task is saved but the upload or
dispatch fails. Reuse the returned task instead of creating another one.

## Maintain versioned resources

Products, software features, certificates, and organization profiles share
one workflow. Each command group is `resource <name>` for the library and
`task <name>` for a task's selections.

```sh
bid --mode local --state SESSION_FILE resource product add --input PRODUCT.json --json
bid --mode local --state SESSION_FILE resource product list --json
bid --mode local --state SESSION_FILE resource product update --id PRODUCT_ID --input PRODUCT_UPDATE.json --json
bid --mode local --state SESSION_FILE resource product list --id PRODUCT_ID --history --json
bid --mode local --state SESSION_FILE task resource add --task TASK_ID --input SELECTION.json --json
bid --mode local --state SESSION_FILE task resource list --task TASK_ID --history --json
```

Replace `product`/`resource` with `feature`/`feature`, `certificate`/`certificate`,
or `profile`/`profile` for the other resources.

1. **Create.** The input file is UTF-8 JSON of at most 128 KiB holding a
   `data` object. Invalid input fails before anything is sent, and error
   messages do not echo the input.
2. **Update.** Send the complete `data` plus `expected_revision`, the
   revision you last read. If someone updated it since, the command fails with
   `revision_conflict` and nothing is overwritten; read the current revision
   and try again.
3. **Select for a task.** The selection input names the resource ID and an
   optional `lot`. Omit `revision` to pin the current one, or give an existing
   integer revision. Later updates never change a saved selection.
4. **Replace a selection.** Run `task ... add` again with the new revision.
   The old selection stays readable with `--history`. Repeating an identical
   selection returns the same ID with `duplicate: true`. Nothing can be deleted.

Field definitions are in the Pydantic models
[resource_contracts.py](../../server/app/schemas/resource_contracts.py),
[feature_contracts.py](../../server/app/schemas/feature_contracts.py),
[certificate_contracts.py](../../server/app/schemas/certificate_contracts.py), and
[profile_contracts.py](../../server/app/schemas/profile_contracts.py).
Every value is a declaration by the user, and each result says that it has
not been verified. Points specific to each resource:

- **Products**: URLs are stored as metadata and never fetched.
- **Features**: `status` is `planned`, `developing`, or `implemented`.
  Screenshot fields are rejected.
- **Certificates**: dates may be `null`. Add `--as-of DATE` to `list` to get
  `validity_by_revision`; boundary days count as valid, and an unknown bound
  stays unknown unless the other bound already proves the state. Without
  `--as-of` the state is unknown.
- **Profiles**: optional text fields stay `null` when unknown; supplied text
  must not be blank.

## Manage DOCX templates

Templates work like the resources above, but create and update each upload a
DOCX file with the metadata.

```sh
bid --mode local --state SESSION_FILE resource template add --input TEMPLATE.json --file TEMPLATE.docx --json
bid --mode local --state SESSION_FILE resource template update --id TEMPLATE_ID --input TEMPLATE_UPDATE.json --file NEW_TEMPLATE.docx --json
bid --mode local --state SESSION_FILE resource template list --id TEMPLATE_ID --history --json
bid --mode local --state SESSION_FILE task template add --task TASK_ID --input TEMPLATE_SELECTION.json --json
bid --mode local --state SESSION_FILE resource template download --revision REVISION_UUID --output NEW_FILE.docx --json
```

Metadata, chapter declarations, and file limits are defined in
[template_contracts.py](../../server/app/schemas/template_contracts.py) and
[template_files.py](../../server/app/services/template_files.py). Every update
creates a new revision, even with identical bytes. `download` takes the
revision UUID from `list`, not the integer revision number.

## Attach certificate PDF originals

A file revision carries the complete certificate data, `expected_revision`,
and one readable, unencrypted PDF. Limits are in
[certificate_files.py](../../server/app/services/certificate_files.py).

```sh
bid --mode local --state SESSION_FILE resource certificate file add --id CERTIFICATE_ID --input FILE_REVISION.json --file ORIGINAL.pdf --json
bid --mode local --state SESSION_FILE resource certificate file list --id CERTIFICATE_ID --history --json
bid --mode local --state SESSION_FILE task certificate file list --task TASK_ID --history --json
bid --mode local --state SESSION_FILE resource certificate file download --revision CERTIFICATE_REVISION_UUID --output NEW_FILE.pdf --json
```

A stale `expected_revision` fails with exit 4 before anything is stored. A
later metadata-only update does not carry the file forward, so its revision
reports no original. `--revision` takes an exact revision UUID and cannot be
combined with `--id` or `--history`. Task file lists report the file of the
pinned revision.

## Archive an unconfirmed PDF page

A source is a rendered page of the PDF original pinned by an active task
certificate selection.

```sh
bid --mode local --state SESSION_FILE evidence source add --task TASK_ID --input SOURCE.json --json
bid --mode local --state SESSION_FILE evidence source list --task TASK_ID --history --json
bid --mode local --state SESSION_FILE evidence source download --id SOURCE_ID --output NEW_PAGE.png --json
```

`SOURCE.json` is `{"task_certificate_id": "SELECTION_ID", "page": 1}`. The page
is rendered as a whole-page 150 dpi RGB PNG; size and time limits are in
[evidence_sources.py](../../server/app/services/evidence_sources.py). Adding
the same page again returns the existing source. Every source has status
`unconfirmed_source`, `confirmed_by: null`, and
`eligible_for_draft_export: false`. It is not confirmed evidence and cannot
reach a draft or export.

## Download files safely

All `download` commands follow the same rules. The server issues a 300-second
signed link that still requires your session. The CLI accepts only a relative
link on the same server, follows no redirects, checks the length and SHA-256,
and writes a new mode-0600 file atomically. It refuses an existing output
path and any symlink in the path; pick a new file in a real directory. File
bytes never appear in the JSON output.

## Create an API token for an agent

```sh
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE token create --name AGENT_NAME --scope task:read --scope job:read --expires-at 2027-01-31T00:00:00+0800 --output NEW_TOKEN_FILE --json
```

Only a logged-in admin can create tokens, and a token cannot create another.
The token is written encrypted with `BID_CLI_KEY` to the new output file and
never printed. Requested scopes must lie within the creator's role grants, and
each request is further limited to what the owner's current role allows;
`ROLE_SCOPES` and `SCOPES` in
[server/app/services/auth.py](../../server/app/services/auth.py) define both.
Tokens can never hold `evidence:confirm` or `export`. Agents cannot confirm
evidence, fabricate sources, or send confidential documents to an external
service without the user's explicit decision.
