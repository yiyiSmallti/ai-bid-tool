---
kind: plan
---

# 契约草案：模型起草的预览绑定与消费上限

状态：**已批准并实施。** 对应[单位后台契约](org-console.md)的 G3-B 决定：付费起草须先绑定
输入、模型、价格和用户金额上限，单位后台的付费运行按钮在此之前保持禁用。机制背景见
[model-drafting-redaction.md](../notes/model-drafting-redaction.md) 与
[prepaid-billing.md](../notes/prepaid-billing.md)。

## 绑定对象

预览的 `input_hash` 已覆盖固定输入清单、遮挡结果和 `model_identity`（服务商、模型、平台目录
模型 ID 与修订、adapter 版本）。平台目录改价必然提升模型修订，因此对平台模型，绑定
`input_hash` 即同时绑定输入、模型和单价。本契约只补两项：提交时核对该哈希，以及用户给出的
单次作业平台扣费上限。

## 接口

```python
class CardGenerateRequest(_TrimmedContract):
    extraction_job_id: UUID
    requirement_ids: list[UUID] | None = None
    reasoning: str | None = Field(default=None, pattern=r"^[a-z0-9_-]{1,20}$")
    dry_run: bool = False
    retry: bool = False
    # 新增：二者均可选，保持既有 CLI 与令牌调用兼容；单位后台付费运行两者必填。
    expected_input_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    max_charge: Decimal | None = Field(default=None, gt=0, max_digits=18, decimal_places=8)


class CardGeneratePreview(_TrimmedContract):
    ...  # 既有字段不变
    max_charge: Decimal | None = None  # 回显请求值
    # admission_blocker 新增取值 "spend_cap_below_first_call"
```

CLI：`bid card generate --task T --job J [--requirement ID ...] [--reasoning LEVEL]
[--expect-input-hash HASH] [--max-charge AMOUNT] [--dry-run] [--wait]`。Result 七键不变，
`data` 回显 `max_charge`；版本按新增可选字段处理为次版本。

## 行为

| 情形 | 结果 |
| --- | --- |
| 提交时重算的 `input_hash` 与 `expected_input_hash` 不同 | 拒绝，`generation_input_changed`，409，退出码 3；不建作业、不扣费 |
| 预览时首个调用预留已超过 `max_charge` | `admission_blocker = "spend_cap_below_first_call"` |
| 运行中累计扣费加下一次调用预留超过 `max_charge` | 停止后续准入，`spend_cap_reached`；已完成部分按既有部分成功规则保存，退出码 5 |
| 命中已成功的缓存作业 | 直接返回，不产生新扣费，上限不参与 |
| 命中未结束的同键作业，其上限缺失或高于本次 `max_charge` | 拒绝，`generation_cap_conflict`，409，退出码 3；不静默沿用更高上限 |
| `retry` 重新排队失败作业 | 新上限作用于该作业的累计扣费（含此前尝试），仍不高于服务端 `job_max_charge` |

实际上限为 `min(max_charge, job_max_charge)`，在 `JobExecution.admit` 中与既有作业扣费上限
同一处判断，并写入作业提交记录与审计。`max_charge` 的币种为 `billing_currency`。

单位自带密钥的平台扣费为零，`max_charge` 不能约束厂商侧账单；单位后台对此显示预估 USD
与“厂商计费不受平台上限约束”的说明，不把上限表述为厂商费用上限。

## 单位后台

起草预检后，付费运行按钮可用的条件是：无 `admission_blocker`、用户已填写上限（默认填入向上
取整的 `estimated_charge`，可改小或改大但不超过服务端上限）、并确认外发摘要。提交携带预览的
`input_hash` 与该上限。收到 `generation_input_changed` 时丢弃预览并要求重新预检，不自动重试。

## 验收

端到端测试覆盖：哈希不符不建作业；上限低于首调用的预检阻断；运行中达到上限后部分保存且
扣费不超过上限；同键进行中作业的上限冲突；单位后台按钮在预检前禁用、在输入变化后重新禁用。
