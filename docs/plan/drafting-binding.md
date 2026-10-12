---
kind: plan
---

# Contract: model drafting preview binding

## Binding target

Preview `input_hash` covers fixed input manifests, redacted content and
`model_identity` (provider, model, catalog model ID/revision, adapter version).
Catalog model changes increment model revision. Submission rechecks the same
inputs, current authority and provider configuration before creating a job.

## Interfaces

`CardGenerateRequest` retains extraction job, optional requirement selection,
reasoning, dry-run, retry and `expected_input_hash`. The org console sends the
preview hash for every model run. The shared Result 5.0 contract reports six
resource counts under [ADR 0011](../adr/0011-remove-billing.md).

## Behavior

| Case | Result |
| --- | --- |
| Submission hash differs from `expected_input_hash` | `generation_input_changed`, exit 3; no job is created |
| Provider configuration unavailable | Explicit configuration blocker; retain input selection and offer administrator recovery |
| Successful cached job matches | Reuse the result; the current read makes no additional provider calls |
| `retry` requeues a failed job | Recheck current input and authority; retain usage from all earlier attempts |
| Later call limit, cancellation or provider failure | Stop new calls and preserve completed work under the existing partial-result contract |

## Org console

The preview shows input scope, model identity, reasoning, redaction counts and
read-only `data.preflight`. A separate unchecked “我已核对外发范围” control records
human consent to the displayed outbound scope. Input/settings changes clear that
consent. On `generation_input_changed`, discard the preview and require another
precheck without automatic retry.

## Acceptance

Verify stale input/model revisions reject before submission, matching cached
results make no calls, and retry/cancellation cannot erase historical usage.
