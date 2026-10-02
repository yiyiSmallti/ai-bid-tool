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
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE req extract --document DOCUMENT_ID --reasoning high --wait --json
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE req list --task TASK_ID --json
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE req history --task TASK_ID --json
```

1. `task create --tender` creates the task, uploads the file, and starts
   parsing. `tender upload --task TASK_ID --file FILE` adds another file later.
2. `tender parse --wait` blocks until the job finishes or the timeout expires.
   A timeout exits 3 and does not cancel the server job; continue with
   `job wait JOB_ID` or `job status JOB_ID`.
3. `req extract --dry-run` reports whether the document is parsed and lists
   the model's reasoning levels (`reasoning_levels`, with the default marked)
   without calling a model or creating a job. Cost estimates are `null` when
   unknown.
4. `req extract` requires a parsed PDF or Word document with verified
   citations and a configured model ([development.md](development.md#configure-the-extraction-model)).
   Without one it fails with `provider_unavailable` and exit 4. Items whose
   quote is not found at the cited position are not saved; the job still
   succeeds, lists them in `result.rejected`, and adds a warning. If no item
   passes, the job fails with `invalid_citation` and saves nothing.
5. `--reasoning LEVEL` picks one of the model's official reasoning levels;
   without it the vendor's default level is used. Higher levels find more
   requirements and take much longer. Each level is its own job: repeating a
   level returns the same job, another level runs a new one. A level the model
   does not offer fails with `unsupported_reasoning` and exit 2; a model
   without levels runs anyway and warns.
6. `job cancel JOB_ID` stops a queued or running job; a cancelled attempt can
   no longer save results. To run a failed, cancelled, or expired job again,
   repeat the parse or extract command with `--retry`. Repeating it without
   `--retry` returns the existing job.

`req list` shows, for each document, the requirements of its latest
succeeded extraction; pass `--job JOB_ID` to see another one. `req history`
lists every extraction of the task (optionally `--document DOCUMENT_ID`) with
its level, model, status, saved and rejected counts, and tokens; `latest`
marks the ones `req list` shows. Each requirement carries its `job_id` and
`reasoning`. Requirements are listed in reading order. A PDF source cites `page`; a Word
source has `page: null` and a `location` with the block ID, the heading path,
and a `label` such as `第五章 采购需求 > 表 5 第 3 行第 2 列`. Text boxes, headers,
and footers in Word files are not parsed; the parse result lists them under
`warnings`. Details are in [docx-citations.md](../notes/docx-citations.md).

## Review responses and assemble a draft

1. Choose a succeeded extraction with `req history --task TASK_ID --json`, then
   list its complete requirement set:

   ```sh
   bid card list --task TASK_ID --job EXTRACTION_JOB_ID --json
   ```

   Keep using that extraction ID for card creation, disposition and assembly.
   A slot marked `missing_card` still needs a decision. Read the inputs with
   `bid schema --json`; write a private JSON input file
   containing `extraction_job_id`, `requirement_id`, and `content` using the
   [card contracts](../../server/app/schemas/response_card_contracts.py).

2. Write the proposed response, choose `evidence` or `commitment`, and record the
   deviation and its explanation. For evidence, use a real task selection ID,
   an allowed field path and an exact quote, or a retained certificate page
   `evidence_source_id` and exact page quote. Use the selection commands below
   and [archive certificate pages](#archive-an-unconfirmed-pdf-page) first.
   A commitment must have an empty evidence list.

   ```sh
   bid card create --task TASK_ID --input CARD_CREATE.json --json
   bid card show --id CARD_ID --json
   bid card update --id CARD_ID --input CARD_UPDATE.json --json
   ```

   Updates contain `expected_revision` and a complete replacement `content`.
   For an unclassified draft, an admin supplies `expected_revision`,
   `review_domain` and `reason` in `CLASSIFICATION.json`:

   ```sh
   bid card classify --id CARD_ID --input CLASSIFICATION.json --json
   ```

3. Submit a card, read its returned revision and evidence IDs, and have the
   assigned professional reviewer inspect the actual materials and response.
   Use a human login session for all decisions:

   ```sh
   bid card submit --id CARD_ID --expected-revision REVISION --json
   bid card confirm --id CARD_ID --expected-revision REVIEW_REVISION --evidence EVIDENCE_ID --json
   ```

   Repeat `--evidence` for every linked item; omit it for a commitment. If the
   card lists warnings, pass each with `--reviewed-warning CODE` and record the
   handling decision with `--reason TEXT`. Technical members confirm technical
   cards; bidders confirm commercial cards. Admins do not cross these domains.

   To reject or request material, use `card reject` or `card needs-material`
   with `--expected-revision` and `--reason`. Use `card withdraw` before editing
   pending content, and the responsible reviewer's `card reopen` before editing
   a confirmed card. Read retained reasons with `card show --id CARD_ID --history`.

4. For procedural clauses that only require compliance, prepare
   `DISPOSITIONS.json` with the extraction ID and an `items` array. Each item
   contains `requirement_id`, `expected_revision` (`null` for no card),
   `disposition` (`comply_only` or `respond`) and a human reason:

   ```sh
   bid card disposition --task TASK_ID --input DISPOSITIONS.json --json
   ```

   One conflict rejects the entire batch. Read the latest revisions and retry
   the whole intended batch. Withdraw pending cards or reopen confirmed cards
   first. Change a comply-only decision back to `respond` before editing it.

5. Preview and then assemble all requirements from the chosen extraction:

   ```sh
   bid draft --task TASK_ID --job EXTRACTION_JOB_ID --dry-run --json
   bid draft --task TASK_ID --job EXTRACTION_JOB_ID --wait --json
   bid draft list --task TASK_ID --job EXTRACTION_JOB_ID --json
   bid draft show --id DRAFT_ID --json
   ```

   Without `--wait`, the receipt identifies the background generation job.
   Use `job status JOB_ID`, `job wait JOB_ID`, or `job cancel JOB_ID` with that
   ID. A failed/cancelled identical input needs an explicit `--retry`; changed
   fixed inputs need a fresh preview and submission. The result includes all
   three tables, comply-only entries and gaps. Exit 5 preserves partial results;
   review each gap and resubmit after completing the missing work. Negative
   deviations remain in their rows. Check `validity` before using an old draft.

6. To change the persisted outbound-redaction setting, a human admin prepares
   `REDACTION.json` with `expected_revision` and `model_redaction_enabled`, then
   runs:

   ```sh
   bid task list --json
   bid task redaction set --task TASK_ID --input REDACTION.json --json
   ```

   Read the setting revision from the task list before another change. The
   setting's purpose and the boundary between assembly and model drafting are
   described in [response-cards.md](../notes/response-cards.md).

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

## Operate the platform

Platform commands need an operator account; see
[development.md](development.md#run-the-platform-console). They use a
platform session, which org commands do not accept, and the reverse.

```sh
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE platform login --email OPERATOR_EMAIL --totp 123456 --json
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE platform org create --name ORG_NAME --admin-email ADMIN_EMAIL --json
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE platform org set-active --id ORG_ID --inactive --json
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE platform model set --input MODEL.json --json
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE platform usage --from 2026-10 --to 2026-12 --json
```

1. `platform login` reads the password from `BID_PASSWORD` and takes the
   current authenticator code. A code works once; five failures in 15 minutes
   lock the email. The session lasts 30 minutes.
2. `platform org create` returns `setup_url` when the admin has never set a
   password. Give the full link to the admin; it works once within 24 hours.
3. `platform org set-active --inactive` blocks every login, session and token
   of the org immediately; `--active` restores access. Data is kept.
4. `platform model set` takes the fields of `PlatformModelSet` in
   [platform_contracts.py](../../server/app/schemas/platform_contracts.py).
   Send `expected_revision` when updating. Setting `default: true` makes the
   model the one used and billed for extraction.
5. `platform model test --id MODEL_ID` makes one real vendor call per
   registered reasoning level and reports each under `levels`. Register levels
   in `reasoning` with the vendor's official names and mark the official
   default in `default_reasoning`; see
   [reasoning-levels.md](../notes/reasoning-levels.md).
6. `platform org list`, `platform model list`, and `platform audit` read the
   current state.

A new org admin can set the password from the CLI instead of the browser. Put
the token part of the link, after `#token=`, in `BID_SETUP_TOKEN` and the new
password in `BID_PASSWORD`:

```sh
bid --mode remote --server https://YOUR_SERVER auth setup-password --json
```

## Recharge and billing

Balances, charges and card values are in the deployment's billing currency.
Platform-billed extraction needs a positive balance; otherwise `req extract`
fails with `insufficient_balance` and exit 4.

Operators issue and void cards and correct balances:

```sh
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE platform card create --count 20 --face-value 100 --output NEW_CARDS.csv --json
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE platform card list --status active --json
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE platform card void --id CARD_ID --json
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE platform org balance --id ORG_ID --add 50 --reason REASON --json
```

1. `card create` writes the codes only to the new CSV file (mode 0600); the
   JSON output and later listings show the last four characters. Codes cannot
   be recovered, so keep the file safe.
2. `card void` works only on unused cards.
3. `org balance` takes exactly one of `--add` (negative to deduct) or `--set`,
   plus a reason, and records a ledger entry.

Org admins check the balance and redeem a card. The code comes from
`BID_CARD_CODE` so it never appears in argv; spaces, lowercase and missing
dashes are accepted.

```sh
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE billing balance --json
bid --mode remote --server https://YOUR_SERVER --state SESSION_FILE billing redeem --json
```

A failed redemption returns `invalid_card` whatever the reason; ten failures
in an hour lock redemption for the org. API tokens can read the balance with
`billing:read` but can never redeem.

To find the org ID for `bid login`, list the orgs of an account; the password
comes from `BID_PASSWORD`:

```sh
bid --mode remote --server https://YOUR_SERVER auth orgs --email YOUR_EMAIL --json
```

