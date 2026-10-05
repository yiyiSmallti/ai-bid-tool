---
kind: plan
---

# Contract: model drafting preview binding and spending limits

Status: **approved and implemented.** Covers decision G3-B in the [org console contract](org-console.md): paid drafting binds inputs, model, price, and a user spending limit first; the org (organization/tenant, 单位) console paid-run button stays disabled until then. See [model-drafting-redaction.md](../notes/model-drafting-redaction.md) and [prepaid-billing.md](../notes/prepaid-billing.md) for background.

## Binding target

Preview `input_hash` already covers fixed input manifests, redacted content, and `model_identity` (provider, model, catalog model ID/revision, adapter version). Catalog price changes always increment the model revision, so for platform models, binding `input_hash` also binds inputs, model, and unit prices. This contract adds only submission-time hash verification and a user-supplied per-job platform charge cap.

## Interfaces

```python
class CardGenerateRequest(_TrimmedContract):
    extraction_job_id: UUID
    requirement_ids: list[UUID] | None = None
    reasoning: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,20}$")
    dry_run: bool = False
    retry: bool = False
    # Both new fields are optional for existing CLI/token compatibility;
    # the org console requires both for paid runs.
    expected_input_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    max_charge: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=8)


class CardGeneratePreview(_TrimmedContract):
    ...  # Existing fields remain unchanged.
    max_charge: Decimal | None = None  # Echo the request value.
    # admission_blocker adds "spend_cap_below_first_call".
```

CLI: `bid card generate --task T --job J [--requirement ID ...] [--reasoning LEVEL]
[--expect-input-hash HASH] [--max-charge AMOUNT] [--dry-run] [--wait]`. Keep Result's seven keys;
`data` echoes `max_charge`. Treat the new optional fields as a minor version change.

## Behavior

| Case | Result |
| --- | --- |
| Recomputed submission `input_hash` differs from `expected_input_hash` | Reject with `generation_input_changed`, 409, exit 3; no job or charge |
| First-call reservation in preview exceeds `max_charge` | `admission_blocker = "spend_cap_below_first_call"` |
| Cumulative charges plus next-call reservation exceed `max_charge` during execution | Stop further admission with `spend_cap_reached`; save completed portions under existing partial-success rules, exit 5 |
| Successful cached job matches | Return directly without new charges; cap does not apply |
| Unfinished same-key job has no cap or a cap higher than this `max_charge` | Reject with `generation_cap_conflict`, 409, exit 3; never silently reuse a higher cap |
| `retry` requeues a failed job | New cap applies to cumulative charges including earlier attempts, still no higher than server `job_max_charge` |

The effective cap is `min(max_charge, job_max_charge)`, checked alongside existing job charge limits in `JobExecution.admit` and saved in submission records/audit. `max_charge` uses `billing_currency`.

BYOK has zero platform charges, so `max_charge` cannot limit vendor bills. The org console shows estimated USD and “厂商计费不受平台上限约束”; it must not describe the cap as a vendor cost cap.

## Org console

After drafting precheck, enable paid run only with no `admission_blocker`, a filled cap (default: rounded-up `estimated_charge`; adjustable down/up but no higher than server cap), and confirmation of the outbound summary. Submit preview `input_hash` and that cap. On `generation_input_changed`, discard preview and require another precheck without automatic retry.

## Acceptance

End-to-end tests cover: mismatched hash creates no job; cap below the first-call reservation blocks precheck; reaching the cap preserves partial results without charges above it; cap conflicts for unfinished same-key jobs; console button disabled before precheck and again after input changes.
