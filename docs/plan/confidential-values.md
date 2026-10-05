---
kind: plan
---

# 契约：保密字段库与导出时填入

状态：**已实施。** 对应[路线图](roadmap.md#覆盖矩阵评测与保密) S01 与 B11。
存储、外发替换、卡片校验和导出填入的机制见
[confidential-values.md](../notes/confidential-values.md)。

## 目标与边界

报价、身份证号、银行账号、联系人和电话这类值，起草时模型用不到，也不应发给模型厂商。
单位把它们登记为保密字段；起草时模型只看到并只能写出 `{{secret.<key>}}` 占位符；
人工确认的卡片保存占位符而不是值；导出时由服务把占位符换成登记的值。

未登记的值仍由[外发遮挡规则](../notes/model-drafting-redaction.md#outbound-rules)兜底，
输出 `[REDACTED_…]`，导出时不能换回。证据规则不变：引文必须逐字来自原材料，
占位符不能作为引文或证据。

## 接口

| 入口 | 内容 |
| --- | --- |
| 数据契约 | [confidential_contracts.py](../../server/app/schemas/confidential_contracts.py)；导出预检新增 `ExportPreview.confidential`（[export_contracts.py](../../server/app/schemas/export_contracts.py)） |
| HTTP | [api/confidential.py](../../server/app/api/confidential.py)：字段增改、设值、当前值列表、历史、查看完整值 |
| CLI | `bid confidential field add/list/update`、`bid confidential set/list/history`；值只从标准输入读取，CLI 不提供查看完整值的命令 |
| 控制台 | “保密字段”页；任务页“报价与保密信息”；卡片编辑器“插入保密字段” |
| 外发请求 | 请求体新增 `confidential_fields`（占位符、名称、类别），提示词 `card-draft-v3`，遮挡规则 `bid-redaction-v3` |

## 已定决定

| 决定 | 结论 |
| --- | --- |
| 占位符写法 | `{{secret.<key>}}`，与模板的 `{{bid.*}}` 一致；控制台显示名称 |
| 字段范围 | `org` 全单位一个值；`task` 每个任务一个值，用于报价 |
| 审阅件是否填值 | 填值；缺值显示“【名称】”。导出件下载仍只限人类 bidder，填值不扩大接触范围 |
| 正式件缺值 | 阻止，`confidential_value_missing` |
| 遮挡开关关闭时是否仍替换登记值 | 不替换，与正则遮挡一并关闭 |
| 谁可设值和查看完整值 | admin 与 bidder 的登录会话；令牌和 agent 只能读字段名 |
| 正则命中但未登记的值，是否提示加入保密字段 | 首版不做 |
| 导出模板中的 `{{secret.*}}` 标记 | 首版不做：模板正文目前只允许章节标题和锚点 |
