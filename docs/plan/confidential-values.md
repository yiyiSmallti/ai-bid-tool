---
kind: plan
---

# Contract: confidential fields and export-time substitution

Status: **implemented.** Covers S01 and B11 in the [roadmap](roadmap.md#coverage-matrix-evaluation-and-confidentiality). See [confidential-values.md](../notes/confidential-values.md) for storage, outbound substitution, card validation, and export filling.

## Goal and boundaries

Quotes, identity numbers, bank accounts, contacts, and phone numbers are unnecessary for model drafting and should not reach model vendors. Orgs (organizations/tenants, 单位) register them as confidential fields (保密字段). During drafting, the model sees and may write only `{{secret.<key>}}` placeholders. Human-confirmed response cards (响应卡) store placeholders rather than values; the service substitutes registered values during export.

Unregistered values still use [outbound redaction rules](../notes/model-drafting-redaction.md#outbound-rules), producing `[REDACTED_…]`, which export cannot restore. Evidence (证据) rules remain: quotes must come verbatim from original material; placeholders cannot be quotes or evidence.

## Interfaces

| Entry point | Content |
| --- | --- |
| Data contracts | [confidential_contracts.py](../../server/app/schemas/confidential_contracts.py); export precheck adds `ExportPreview.confidential` ([export_contracts.py](../../server/app/schemas/export_contracts.py)) |
| HTTP | [api/confidential.py](../../server/app/api/confidential.py): add/update fields, set values, list current values, history, reveal full values |
| CLI | `bid confidential field add/list/update`, `bid confidential set/list/history`; values come only from stdin; CLI has no full-value reveal command |
| Console | “保密字段” page; task “报价与保密信息”; placeholder editors in card editor and “单位资料”: fields display as chips, inserted by dragging from the field bar or clicking |
| Resource declarations | Product, feature, certificate (证照), org profile (单位资料), and template text may reference fields; reject unknown/archived fields. Fill fields in evidence excerpts at export too |
| Outbound requests | Add `confidential_fields` (placeholder, name, category); prompt `card-draft-v3`, redaction rules `bid-redaction-v3` |

## Decisions

| Decision | Outcome |
| --- | --- |
| Placeholder syntax | `{{secret.<key>}}`, consistent with template `{{bid.*}}`; console shows the name |
| Field scope | `org`: one value across the org; `task`: one per task, for quotes |
| Fill review copies (审阅件)? | Yes; missing values show “【名称】”. Downloads remain restricted to human bidder users; filling does not broaden access |
| Missing values in final sections (正式件) | Block with `confidential_value_missing` |
| Substitute registered values when redaction is off? | No; disabled together with regex redaction |
| Who can set/reveal full values? | Logged-in admin/bidder sessions; tokens and agents read field names only |
| Suggest registering regex-matched values? | Not in the first release |
| Must users type placeholders? | Console uses drag/click insertion; CLI/API submit text `{{secret.<key>}}` |
| `{{secret.*}}` markers in export templates | Not in the first release: template body currently permits only section headings and anchors |
