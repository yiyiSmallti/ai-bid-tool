---
kind: reference
---

# Glossary

Developer and agent documentation uses English. Use the canonical English terms below,
with the Chinese bidding term in parentheses on its first use on each page. Product
content, console UI strings, export text, code identifiers and literal contract values
retain their original spelling. The root README also retains a short Chinese summary.

## Bidding and responses

| Chinese | English | Meaning or usage |
| --- | --- | --- |
| 招标文件 | tender documents | Documents issued for a procurement. |
| 投标文件 / 标书 | bid (bid document) | The bidder's submitted document; use **bid** in prose and **bid document** when the document itself needs emphasis. |
| 招标人 | purchaser | The party conducting the procurement. |
| 投标人 | bidder | The party submitting a bid. |
| 投标专员 | bid specialist | The human role responsible for bid preparation; preserve the code role identifier `bidder`. |
| 废标 | bid rejection | Rejection of a bid. |
| 扣分 | point deduction | A reduction in the assessed score. |
| ★条款 / ★号条款 | starred (★) mandatory clause | A starred mandatory requirement. |
| 实质性条款 | substantive clause | A substantive procurement requirement. |
| 综合评分法 | comprehensive scoring method | The scoring method used to assess bids across criteria. |
| 评分标准 | scoring criteria | The criteria used to assess a bid. |
| 响应 | response | A response to a tender requirement. |
| 偏离 | deviation | Difference between a response and a requirement. |
| 正偏离 / 负偏离 / 无偏离 | positive / negative / no deviation | The three deviation classifications. |
| 资格 | qualification | Qualification requirements or responses. |
| 商务 | commercial | Commercial requirements or responses. |
| 技术 | technical | Technical requirements or responses. |
| 响应卡 / 卡片 | response card | A card holding a proposed or human-confirmed response. |
| 初稿 | draft | An assembled response draft. |
| 组表 | table assembly | Assembly of confirmed responses into tables. |
| 须遵守 / 仅需遵守 | comply-only | A requirement classified as requiring compliance without a response-table row. |
| 缺口 | gap | An unresolved requirement or missing response or material. |
| 证据 | evidence | Material supporting a response. |
| 证照 / 证书 | certificate | Organization or personnel certificates. |
| 单位资料 | org profile | Organization information and supporting resources. |
| 保密字段 | confidential field | A registered value protected during outbound processing. |
| 遮挡 | redaction | Removal or substitution of sensitive text or pixels. |
| 审阅件 | review copy | An export intended for review. |
| 正式件 | final section | A response-section export released for final use. |
| 模拟拟投 | simulated proposal | Simulated product selections and specifications. |
| 原型 | prototype | Generated software UI content with retained internal provenance. |
| 厂家 | vendor | A product vendor; use **provider** for an external service adapter. |
| 拟投产品 | proposed product | A product selected for a bid. |
| 白皮书 | white paper | A vendor's published technical source. |

## Organization and operations

| Chinese | English | Meaning or usage |
| --- | --- | --- |
| 单位 | org (organization/tenant) | The tenant and primary data-isolation boundary; use **org** after its first definition. |
| 任务 | task | A bid-preparation task. |
| 充值卡密 | recharge card | A prepaid recharge credential. |
| 预付余额 | prepaid balance | The balance used for platform-billed calls. |
| 平台运营后台 | platform operator console | The platform administration interface. |
| 发起人 | initiator | The human who initiates an operation or agent session. |
| 人工确认 | human confirmation | Confirmation performed by an authorized human. |
| 职责 | review domain | The domain assigned to a reviewer for confirmation. |
| 审计 | audit | The recorded action trail. |
| 用量 | usage | Metered resource consumption. |
| 记忆 | memory | Stored rules, preferences or experience used as context. |
| 候选 | candidate | A proposal that has not become effective through approval. |
| 看板 | dashboard | The task view where humans and agents track work. |
| 后台作业 | background job | A persisted asynchronous operation; distinct from a bid task. |
| 接入层 | provider layer | The common interfaces and adapters for external capabilities. |
| 溯源 | provenance | Retained origin, inputs and transformations for a result or artifact. |
| 取证 | evidence capture | Capturing real sources for later review. |

## Plan statuses and decisions

| Chinese | English |
| --- | --- |
| 待批准 | pending approval |
| 未实施 | not implemented |
| 已批准 | approved |
| 已接受 | accepted |
| 已实施 / 已实现 | implemented |
| 部分已实施 / 部分实施 | partially implemented |
| 待决定 / 待定决定 / 待定问题 | open decisions |
| 已定决定 / 已解决决定 | decisions |

These labels describe separate dimensions: approval of a contract does not establish
implementation or completion of its acceptance checks.
