# Simulated product proposals

## Problem

Drafting can only cite materials an organization has recorded and selected on the
task. When nobody has chosen products yet, every hardware requirement drafts as
"material missing", which shows nothing about the drafting and export path. A
demonstration needs plausible products with real, sourced parameters, and those
materials must never pass for a bid decision or reach a deliverable.

## Usage

A human admin or technical reviewer opens 模拟拟投（演示） on the task page, picks a
succeeded extraction, previews, and starts the run. The CLI-equivalent API is
`POST /tasks/{task_id}/product-simulations` with `extraction_job_id` and
`dry_run: true`, then the returned `expected_input_hash`; `retry: true` requeues a
failed or cancelled run with the same inputs. The `product_simulation` job result
lists every procurement item with its kind, proposed vendor and model, outcome,
source URL and kept parameter quotes. `GET /tasks/{task_id}/simulated-resources`
returns the task's selection IDs that came from a simulation; the console marks
them 模拟 in the material panel and on card evidence.

A run needs `BID_SEARCH_URL`, `BID_SANDBOX_POLICY_FILE` and
`BID_SANDBOX_FETCH_QUOTA`; the preview reports `search_unavailable` or
`fetch_unavailable` otherwise.

## How it works

[product_simulation.py](../../server/app/services/product_simulation.py) groups the
extraction's table-cell technical requirements by tender table row. Item names
come from the row's cells under name-like header columns, repeating merged cells
from the row above. The fixed manifest of items and requirement IDs is the input
hash.

The worker asks the model in
[simulation.py](../../server/app/providers/simulation.py) to classify each item as
hardware, software or service and to name a real, publicly documented product for
hardware. Software development and services never get a product. For each
hardware item it searches the operator's SearXNG, fetches up to three ranked
candidates through the trusted fetch broker, and accepts only a page whose text
names the proposed model. The model then quotes parameters from that page text;
a quote that is not verbatim in the normalized page text is dropped.

Each item with kept quotes becomes a product named 【模拟】 plus the item name, with
the page as its official URL, and one feature declaration per quote. All are
selected on the task and registered in `simulated_resources` by product or feature
root, so a later human revision stays marked. The table only accepts inserts.

[exports.py](../../server/app/services/exports.py) reports
`export_simulated_material` for every declaration whose product or feature is
marked: it blocks a final section and needs acknowledgment in a review copy.
Model calls use the job's accounting like drafting; search and fetch use the same
providers and limits as vendor-source capture.
