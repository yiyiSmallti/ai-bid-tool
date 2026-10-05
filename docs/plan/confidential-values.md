---
kind: plan
---

# 契约草案：保密字段库与导出时填入

状态：**已批准，实施中。** 对应[路线图](roadmap.md#覆盖矩阵评测与保密) S01 与 B11。

## 目标与边界

报价、身份证号、银行账号、联系人和电话这类值，起草时模型用不到，也不应发给模型厂商。
单位把它们登记为保密字段；起草时模型只看到并只能写出 `{{secret.<key>}}` 占位符；
人工确认的卡片保存占位符而不是值；正式导出时由服务把占位符换成登记的值。

现有的正则遮挡（[redaction.py](../../server/app/services/redaction.py)）保留，作为未登记值的兜底，
仍输出 `[REDACTED_…]`，不能在导出时换回。本草案不改变证据规则：引文必须逐字来自原材料，
占位符不能作为引文或证据。

## 数据模型

三张单位业务表，均为 NOT NULL `org_id`、FORCE RLS、组织复合外键，同次带双单位与缺上下文测试。

| 表 | 内容 | 可变性 |
| --- | --- | --- |
| `confidential_fields` | `key`（`^[a-z][a-z0-9_]{1,47}$`，单位内唯一）、`label`（如“投标总价”）、`kind`（`amount`、`contact`、`identity`、`bank_account`、`other`）、`scope`（`org` 或 `task`）、`archived` | 仅 `label` 与 `archived` 可改，经 `expected_revision` |
| `confidential_values` | `field_id`、`task_id`（`scope=task` 时必填，`org` 时为空）、`encrypted_value`（`Secrets.for_data`）、`value_hmac`、`tail`、创建人 | 只增不改；每次设值追加一行 |
| `confidential_value_heads` | 每个（字段，任务或单位）当前生效的 `confidential_values` 行 | 只改指针 |

- `scope=org` 用于身份证号、银行账号、联系人、电话；`scope=task` 用于投标总价、分项报价，每个任务各自填写。
- `tail` 只为 `identity`、`bank_account`、`contact` 保存末 4 位，与 `platform_cards` 的做法一致；`amount` 与 `other` 不存任何明文片段。
- `value_hmac` 使用数据密钥派生的 HMAC，用于起草时的精确匹配和导出时的变化检测，不能反推值。
- 值长度 1–2000 字符，NFKC 后去首尾空白；数字类按去掉空格与连字符后比较。

## 权限

| 授权 | admin | bidder | technical | viewer | API 令牌 |
| --- | --- | --- | --- | --- | --- |
| `confidential:read`：字段 key、label、kind、是否有值、末 4 位 | 是 | 是 | 是 | 是 | 可授予 |
| `confidential:write`：建字段、设值、归档 | 是 | 是 | 否 | 否 | 永不 |
| `confidential:reveal`：查看完整值 | 是 | 是 | 否 | 否 | 永不 |

`confidential:write`、`confidential:reveal` 与 `evidence:confirm`、`export` 一样在数据库约束中禁止写入令牌范围。
内置和外部 agent 只能读 key 与 label，在卡片中插入占位符，不能读值。

## 起草外发

`card_generation.snapshot` 在正则遮挡之前先做库内替换：

1. 取任务的全部生效值（单位级值加该任务的任务级值），把材料与要求原文中与之精确匹配的片段换成 `{{secret.<key>}}`。
   多个值重叠时取最长匹配。
2. 再跑正则遮挡，未登记的敏感值仍变成 `[REDACTED_…]`。
3. 提示词附上可用占位符清单（key、label、kind），不附值；要求模型在需要写出这些值时只用占位符，
   不得编造数值。

任务遮挡开关同时控制第 1、2 步：关闭后登记值与其他文字一并原文外发，预览照常警告未遮挡外发。
提示词在开关关闭时仍列出占位符，要求模型优先写占位符。
清单（manifest）记录用到的字段 ID、值行 ID 与命中次数，不记录值；规则版本升为 `bid-redaction-v3`、
提示词升为 `card-draft-v3`，旧版本提交的作业以 `generation_rules_changed` 停止。

## 卡片

`response_text` 与 `deviation_note` 可含 `{{secret.<key>}}`。保存与确认时校验：

- key 必须是本单位未归档的字段，否则 422 `unknown_confidential_field`；
- 文本不得含 `[REDACTED_…]`，否则 422 `redacted_placeholder_in_response`，提示改用保密字段或手写；
- 卡片只保存占位符，确认不绑定值，值变更不使已确认卡片失效。

控制台在卡片里把占位符显示为带 label 的标签，编辑器提供“插入保密字段”。

## 导出

预检（`bid export preflight` 与对应 API）新增 `confidential` 清单，列出本次初稿用到的每个字段：

```json
{
  "key": "bid_total",
  "label": "投标总价",
  "scope": "task",
  "status": "filled",
  "value_id": "…",
  "tail": null
}
```

`status` 取 `filled` 或 `missing`。规则：

| 情况 | `final_section` | `review_copy` |
| --- | --- | --- |
| 用到的字段都有值 | 填入值 | 填入值 |
| 有字段缺值 | 拒绝，`confidential_value_missing`，列出 key | 允许，缺值显示为“【label】” |
| 字段已归档 | 拒绝，`confidential_field_archived` | 同样拒绝 |
| 提交后、发布前值被改 | 发布时拒绝，`confidential_value_changed`，需重新提交 | 同样拒绝 |

“一键填入”就是正式件的默认行为：提交时绑定 `confidential` 清单中的 `value_id` 集合的哈希，
worker 渲染时按这些固定值行解密填入，人工发布时复核值行仍是当前生效行。
导出件下载仍只限有 `export` 的人类 bidder，填入值不扩大接触范围。

导出模板中的独立段落 `{{secret.<key>}}` 按同一规则填入，与 `{{bid.task_name}}` 相同，
只允许单独成段；其他位置出现即按未知标记拒绝。

审计记录字段 ID 与值行 ID，不记录值或末 4 位以外的片段。日志、错误、作业结果与清单都不含值。

## CLI

值只从标准输入读取，避免进入 shell 历史；不做交互式提问。

| 命令 | 说明 |
| --- | --- |
| `bid confidential field add KEY --label L --kind K --scope org\|task` | 建字段 |
| `bid confidential field list` | 列字段及单位级是否有值 |
| `bid confidential set KEY [--task TASK] --value-stdin` | 设值，追加值行 |
| `bid confidential list --task TASK` | 列任务可用字段、生效值行 ID、`filled`/`missing`、末 4 位 |
| `bid confidential history KEY [--task TASK]` | 值行历史（时间、设值人、末 4 位） |
| `bid confidential field archive KEY --expected-revision N` | 归档 |

CLI 不提供查看完整值的命令；完整值只在控制台对有 `confidential:reveal` 的会话显示。
所有命令沿用七键 `--json` 结构，`data` 不含值字段；`bid schema` 与快照测试同步。

## 控制台

- 资源管理新增“保密字段”页：建字段、单位级设值、查看历史，完整值点击后显示并记审计。
- 任务页新增“报价与保密信息”：填写任务级字段，显示缺值项。
- 卡片编辑器的占位符标签与插入按钮。
- 导出页列出 `confidential` 清单，缺值项可跳转填写。

## 测试与验收

- 双单位：A 的字段、值行、指针对 B 不可见，B 的卡片引用 A 的 key 返回 422。
- 令牌：申请 `confidential:write` 或 `confidential:reveal` 被拒，数据库直接写入同样被拒。
- 外发：登记值在请求体、清单、作业结果、审计、日志中均不出现；关闭遮挡开关后仍不出现。
- 导出：正式件与审阅件含值，审阅件缺值显示标签；缺值、归档、提交后改值各自按上表处理。
- 端到端：登记字段 → 起草返回占位符 → 人工确认 → 正式导出的 DOCX 解包后值出现在对应单元格，
  工件存于 `data/work/`。

## 已定决定

| 决定 | 结论 |
| --- | --- |
| 占位符写法 | `{{secret.<key>}}`，与模板的 `{{bid.*}}` 一致；控制台显示 label |
| 审阅件是否填值 | 填值；缺值显示“【label】” |
| 遮挡开关关闭时是否仍替换登记值 | 不替换，与正则遮挡一并关闭 |
| 正则命中但未登记的值，是否提示加入保密字段 | 首版不做 |
| 谁可查看完整值 | admin 与 bidder |
