---
kind: plan
---

# 契约草案：已确认响应草案的逐项评分预估

状态：**待批准，未实施。** 本草案对应[路线图](roadmap.md) B10。仓库运行时代码、
数据库、HTTP、CLI、权限和作业处理器均未注册本提案。
[Pydantic 与 Provider 草案](score/score_contracts.py)用于在实现前固定边界；共用的
评估、引用、外发和 Result 约定见 [B09 已批准契约](check.md)，类型来自
[共享契约](../../server/app/schemas/check_contracts.py)，不在本文件复制。

## 目标与结论边界

首版输出“已确认响应草案评分预估”：把一套经人确认的评分 rubric 逐项对照指定的
current `DraftRun`，给出可估项目的分数、失分原因、补强动作和逐字引用。它是辅助判断，
不是招标人的正式得分，也不是全文、版式、附件完整性或最终交付件审查。

评分分两阶段完成：

1. 从一个显式指定且成功的抽取作业中，只读取 `Category.scoring` Requirements，生成
   有版本的 rubric 候选。逐项人工确认后，再由人确认整套规则完整性。
2. 只使用已确认整套 rubric，对一个显式指定且仍为 current 的 `DraftRun` 运行评分。
   模型输出仍要经过本机边界、引用和聚合校验，结果恒为 advisory。

现有 `Requirement.condition` 是自由 `dict`，没有 rubric 契约。首版不读取它来决定分值、
权重、上下限或公式，也不把它发送给评分 Provider。rubric 候选只能依据所选 scoring
Requirement 的固定文字和已核验 `Source`；不能重新遍历 Chunk、扫描全文或偷偷启动新的
抽取。为了重新核验引用，服务只按 `Source.chunk_id` 读取其所指 chunk/block，并定位该连续原文；
不得借机扫描相邻 Chunk 或发现新评分项。这里的“完整”只表示覆盖该抽取作业保存的 scoring
Requirements，不声称原招标文件没有漏抽。

## 固定输入

### Rubric 候选输入

`RubricInput` 固定 `org_id`、`task_id`、`extraction_job_id`、`document_id` 和
`input_hash`。服务必须验证抽取 Job 已成功，且 Job、Document、Requirement 同单位、同任务、
同文档。选择集为该 Job 下全部 `Category.scoring` Requirements；调用方不能挑一部分后仍
请求“完整 rubric”。Provider 收到的只有每条 Requirement 的本地 ref、固定文字和来源位置，
不收到任意 `condition`、整份招标文件、其他类别要求或资源库内容。

### 评分输入

`ScoreRequest` 在共用 `AssessmentRequest` 上只增加 `rubric_id`。快照必须固定：

- 指定 current `DraftRun` 的 `org_id`、`task_id`、`draft_id`、`extraction_job_id`、
  `document_id`、`draft_input_hash`；DraftRun 必须与 confirmed rubric 固定到同一抽取作业和文档。
- DraftRun 中每条 `ResponseItem` 的分区元数据：`response`、`comply_only` 或 `gap`，以及
  requirement、source、gap reason、disposition 和固定 revision 绑定。评分项自身的行称为
  `anchor_response_item`，其分区保存为 `anchor_partition`。
- 同一 DraftRun 的全部 `response` 行作为候选支持材料，但只发送 DraftRun 固定的已确认
  `response_text`、`deviation_note`；不得读取当前 Card 指针、未确认/待审/已拒绝 Card 的文字，
  也不得用之后修改的 Card 偷换输入。这样评分项可以引用其他已确认技术/商务响应中的真实支持，
  而不是错误地假定支撑一定与 scoring Requirement 同 ID。
- `comply_only` 和 `gap` 行只发送必要元数据与招标侧原文，不生成或借用卡片文字。anchor 为 gap
  不自动证明整份草案无材料；只有其他 confirmed response 的逐字内容确实支持时才可能 assessed。
  `comply_only` 自身没有投标侧文字，不能单独得分；没有任何已确认投标侧引用时恒 unassessable。
- 已确认 rubric 的 set、section、item 版本、覆盖决定、上下限、权重和聚合规则。
- `assessment_date`、Provider/平台模型目录修订、reasoning、prompt/schema/评分规则版本、
  遮挡设置修订及规则版本。

首版不读取任何 released/review DOCX、导出运行、Gotenberg PDF、模板正文或页面图像。导出
流程会在 human-only 下载件中填入真实保密值，agent 和模型无权下载；评分若读取 released
文件，会同时破坏人工关口和保密边界。以后如确有需求，应另立“人工会话专用 released-export
评审”契约，并单独决定授权、外发、文件引用、费用与审计，不能扩充本命令的隐含输入。

## Rubric 的版本、覆盖和人工确认

Provider 只创建 candidate。候选 section 和 item 固定到原 Requirement/Source，引用必须在
实际发送文本和固定来源中唯一连续命中。未知、歧义、拼接或被遮挡的引用不修补；相应候选
留为未解决，不能进入 confirmed set。

每个 scoring Requirement 必须有一条 `RubricRequirementCoverageView`：

- `mapped`：明确关联一项或多项 rubric item；拆成多项时各项边界和分值均需确认。
- `duplicate`：人指定 canonical Requirement 并写原因；不得由模型静默去重。
- `excluded`：人说明为什么该 Requirement 不是可计分规则；仍保留来源和决定。
- `pending`：尚未决定，阻止整集确认。

rubric item 保存规范化规则文字、section、顺序、评估模式、分数上下限、可选权重、审阅职责、
来源和内容 fingerprint。section 保存自己的上下限、权重、cap、是否进入总体、审阅职责以及
`sum`、`weighted_sum`、`capped_sum`、`formula` 或 `non_additive` 聚合方式。确认整集前，服务必须
确定性检查：

- 所有 scoring Requirements 均有非 pending 覆盖决定；item/section key、顺序和 fingerprint
  没有未处置重复；引用仍绑定同一固定来源。
- 每个 section/item 均已有 `review_domain` 并由对应职责的人确认；被拒项不能留在 set 中。
  商务项由 bidder、技术项由 technical 处理。分类不复用只适用于 Card 的既有接口：本契约新增
  section/item classify 请求，只有 admin 人类会话可执行，写非空理由和 expected revision/hash；
  分类不因此赋予 admin 跨专业确认权。确认请求不接收 `review_domain`，授权只读取已存分类，
  调用方不能用请求字段换职责。
- 每个已声明上下限满足 `0 <= minimum <= maximum`，权重合法，item 到 section、section 到
  overall 的纳入关系没有环、重复计入或悬空。
- 每个 section 的合计/上限/权重规则和不同 section 如何形成 overall 已明确；能机械核对的
  上下限与合计一致。招标原文确有歧义时，允许把 item 明确确认成 `ambiguous`，但不能把
  未知规则编造成数字；该项在评分时恒为 unassessable。

模型候选中的标题、规则、上下限、权重、cap、聚合方式或职责如有错误，人类先以
`RubricReviseRequest` 提交完整替换快照；请求带 expected revision/input hash，只能引用该固定输入
中的 Requirement 和上一版 section/item，不能改写 Source。服务创建新的 candidate 版本和新 IDs，
保存 `prior_rubric_id` 与修订理由，旧版本及其决定不改。新版本重新经过分类、覆盖、逐项和整集
确认。revision 输入不接收 `review_domain`，新版本 section/item 分类全部重置为 null，必须由 admin
重新分类，避免修订人继承或自填职责绕过关口。决定、分类、coverage 和 revision history 均
append-only，并可通过 history GET 分页读取。

整集只有 `completeness.complete=true`、无 normalization error 且所有逐项关口完成后才能
confirmed；score 只接受 confirmed set。`formula`/`non_additive` 可以作为“规则已完整记录且人工
确认”的 rubric 进入 complete，但其 `aggregation_assessable=false`，不会被执行或阻止 rubric
完整性；对应 section/overall 在评分报告中恒为 `unavailable`。

### 首版聚合算法

服务只执行以下三个确定性算法，全部使用 `Decimal`，中间过程不四舍五入，section/overall 最终
结果按 `0.00000001`、`ROUND_HALF_UP` 取值：

- `sum`：纳入项分数直接相加；可能范围分别相加每项下限和上限。
- `weighted_sum`：权重是 `(0,1]` 的小数比例，不是百分数字符串；同一聚合节点下的纳入子项权重
  必须精确合计 `1.00000000`，结果为 `sum(score * weight)`，可能范围同算。
- `capped_sum`：先按 `sum` 计算，再取 `min(sum, cap)`；cap 必填且非负，可能范围同样应用 cap。

section 作为 overall 子项时，其 `weight` 只供 overall 的 `weighted_sum` 使用；item 的 `weight`
只供所属 section 的 `weighted_sum` 使用。其他算法出现不需要的 weight、缺 cap、重复纳入、权重
和不为 1 或声明上下限与算法结果冲突，均形成 normalization error。`formula` 和
`non_additive` 只保存逐字规则与限制原因，不解析或执行任意公式字符串。

## 评分语义与聚合

第一版仅对 `assessment_mode=model_assessable` 且规则、上下限、输入证据均足够的项目给出
`estimated_score`。以下项目必须明确为 `unassessable`，保留原因和可补强动作，不猜分：

- 评分文字或分段规则有歧义；公式在 rubric 中仍不完整或首版不支持。
- 任何价格比较项，或依赖其他投标人、评委排序、基准价、现场演示、主观印象、外部名次和
  当前输入没有的第三方数据的项目。
- 没有任何 confirmed response 能提供投标侧逐字支持、需要附件/证明但只有无证据薄承诺，或
  无法用实际发送内容和固定原文支持结论的项目。

纯承诺只有在 confirmed rubric 明确规定“承诺文字本身足以得分”时才可评分。需要证书、报告、
截图、参数、业绩或附件的规则，不得仅凭“满足、完全响应、可提供”之类薄承诺给满分。模型
给出的分数必须落在 confirmed item bounds 内，并同时有至少一条通过本机核验的招标侧引用和
一条当前 DraftRun 的已确认投标侧引用；只有招标原文引用不能得分。报告的
`response_item_ids` 只列实际通过核验并支持该得分的响应行，unassessable 时为空。无效引用不
自动改写为其他来源。最终分数低于 confirmed item maximum 时，`deduction_reasons` 至少一项；
补强动作可以为空，但不得建议伪造证件、报告、截图、参数或设计非目标中的报价策略。

每个 section 和 report 始终输出 `assessed_subtotal`，字段名称明确它只是已评估项小计。
只有全部纳入项均 assessed、所有聚合规则可确定执行、Provider 各批次完整且本机校验通过时，
才输出 `estimated_score`/`estimated_total`。否则 total 状态只能是 `range_only` 或 `unavailable`，
并把 `estimated_total` 置空；如 confirmed bounds 足够，可输出 `possible_range`。不得把部分小计
改名成总分，也不得以未评估项为零来制造总分。

报告读取时重算 `validity`。DraftRun 不再 current、rubric 被 supersede、固定 Card revision 的
确认状态失效、遮挡/输入依赖变化时标为 stale，并列出 invalidation code；历史报告不可重写。

## 引用、外发与保密

Provider 请求使用共用 `OutboundContext`：`texts` 的每个本地 ref 唯一，
`confidential_fields` 只含占位符、名称和类别。外发前沿用
[模型起草与遮挡机制](../notes/model-drafting-redaction.md)：登记值先换成
`{{secret.<key>}}`，再执行版本化规则遮挡；原材料和 DraftRun 不改。固定 manifest 包含实际使用的
confidential field/value-row IDs、每个实际 sent text 的 SHA-256 和总 input hash；即使
redaction revision 未变化，保密值换版导致的遮挡结果变化也会形成新输入并拒绝旧 preview。

模型只返回 `ModelEvidenceRef`。服务按该批实际发送 ref 白名单解析，并逐字核对 quote 同时存在于
实际发送文本和固定原文。持久化使用共用 `VerifiedCitation`。首版 score profile 只接受
`TenderCitation` 和绑定当前 DraftRun confirmed ResponseItem 的 `DraftCitation`；
`EvidenceCitation` 虽属于共用类型，本命令首版拒绝，因为证据正文不在评分外发输入中。
任意模型生成的 UUID、URL、路径、选择/修订号或未知 ref 都不能创建绑定；错误 ref 只记录脱敏
摘要，不回显任意模型字符串。

rubric Provider 只收到已遮挡的招标原文 ref。score Provider 的每个 item 必须显式收到
`tender_ref`、confirmed `rule_ref` 与同一 DraftRun 全部已确认响应的候选 `draft_refs`；`rule_ref`
是供推理的人工规范化规则，不能生成 `TenderCitation`，真实招标引文只能来自 `tender_ref`。服务
发布 assessed 结果前分别校验 tender/rule/draft ref，并要求模型的 tender 与 draft 引用均非空。
读取 rubric 或历史报告时仍要在当前 org/task scope 下重新解析所有 Source、DraftRun、ResponseItem
和 Card revision 父对象；越权或不存在统一 404，依赖失效则报告 stale 并禁止据此发起新评分，
不能因为报告保存了 Source JSON 就绕过当前读取权限。Source 原文核验只读取所指 chunk/block，
不扫描全文或产生新 Requirement。

真实保密值不进入 score 快照、提示词、Provider 错误、Job result、UsageRecord、审计或报告。
虽然关闭遮挡仍是既有的人类 org admin、revision-checked、audited 任务设置，但 B10 比模型起草
收紧：rubric/score dry-run 在关闭时返回 `admission_blocker=redaction_required`，正式提交拒绝，
请求参数不能覆盖。设置修订变化会停止后续调用，并要求重新 preview/submit。后续是否允许单位
自带或本地模型在关闭遮挡时运行须另行批准。

## Provider、作业和预付费

[协议草案](score/score_contracts.py)定义两个结构化 Provider 方法：

- `RubricProvider.extract_rubric`：输入固定 scoring Requirements，输出 section/item 候选。
- `ScoreProvider.score`：输入 confirmed rubric item、对应 DraftRun 分区和外发上下文，输出逐项
  assessed/unassessable、分数、原因、补强动作与 refs。

二者都只由 `server/app/providers/` 的实现调用厂商 SDK/HTTP，使用严格 JSON Schema；业务服务
只依赖 Protocol。适配层保留已完成 batches 和每次 `ProviderUsage`，第一次不可恢复失败后停止
尚未开始的批次，已在途调用可完成并计费。结构错误可按既有边界拆半，但单项仍失败就明确报告，
不能用默认分数、空引用或宽泛重试掩盖。

rubric 和 score 的 Job kind 分别固定为 `score_rubric`、`score`。两者的 `task_id`、`document_id`
必须非空；score Job 的 document 来自 DraftRun 固定 extraction Job 的真实 Document，不能造占位
Document。执行复用既有
`JobExecution.activate/owned_job/admit/_complete_once`：每次尝试持有 lease 和 `run_id`，发布前
再次核验所有固定输入；接管、过期、取消或设置变化后旧 run 不得发布。

Provider 解析、固定 identity 和费用规则复用 [B09 作业与计费](check.md#作业provider-与计费)：
`resolve_llm` 可以选择单位自带配置或平台默认，快照固定 `provider_config_id`、`provider_source`
和 provider identity，不得只记录平台目录。每个真实调用仍走 VendorCall/call ceiling，并按现有
唯一键 `(org_id, job_id, run_id, call_id)` 写 `UsageRecord`。只有平台收费调用按目录售价做正额
reservation，并在同一事务调用 `billing.charge_usage` 扣预付余额；已发送但结果不明保留
reservation。单位自带 key 的 reservation/平台 charge 为 0，跳过预付扣款，供应商 `usd` 不可知
时仍为 null；`max_charge` 只限制平台售价扣款，不能声称限制单位直接承担的厂商账单。取消、引用
拒绝、模型内容无效或报告 partial 都不抹去已发生用量。服务不得另写一套计费。

`--dry-run` 做同范围快照与授权检查，不写业务/审计/Job 数据、不调用 Provider，报告首轮费用
上界、模型目录和遮挡摘要。正式提交必须带 preview 的 `expected_input_hash`；`max_charge` 只是
本 Job 的平台售价扣款上限，不限制单位自带 key 的厂商账单，也不等于尚未实现的
`Task.budget_usd` 全任务累计预算。全任务预算执行保留为待决定，不能在本切片里暗示已经生效。

缓存键至少包含 org/task/document/extraction/draft/rubric ID 与固定版本、input hash、assessment
date、Provider/模型目录修订、reasoning、prompt/schema/规则/遮挡版本。相同键可返回 cached Job；
`retry=true` 仅重试同一固定输入，不选择“最新”DraftRun 或 rubric。

## HTTP、CLI 与 Result

拟新增入口如下；HTTP 和 CLI 使用同一 service 与 Pydantic 模型。复杂 revision/decision/classify
输入使用 UTF-8 JSON 文件，结构分别为 `RubricReviseRequest`、`Rubric*DecisionRequest` 或
`RubricClassifyRequest`，CLI 不做交互提问。

| HTTP | CLI（均支持 `--json`） | data / items |
| --- | --- | --- |
| `POST /tasks/{task_id}/score-rubrics/preview` | `bid score rubric generate --task UUID --extraction-job UUID [--reasoning LEVEL] [--max-charge DECIMAL] --dry-run --json` | `RubricPreview` / `[]` |
| `POST /tasks/{task_id}/score-rubrics` | `bid score rubric generate --task UUID --extraction-job UUID --expected-input-hash SHA256 [--reasoning LEVEL] [--max-charge DECIMAL] [--retry] [--wait] --json` | `RubricJobAccepted`；wait 后为 `RubricGenerateResult` / `[]` |
| `GET /tasks/{task_id}/score-rubrics?cursor=…&limit=…` | `bid score rubric list --task UUID [--cursor CURSOR] [--limit N] --json` | `AssessmentListData` / `RubricSetView[]` |
| `GET /tasks/{task_id}/score-rubrics/{rubric_id}` | `bid score rubric show --task UUID --rubric UUID --json` | `RubricReportData` / `[]` |
| `POST /tasks/{task_id}/score-rubrics/{rubric_id}/revisions` | `bid score rubric revise --task UUID --rubric UUID --input PLAN.json --json` | `RubricReportData` / `[]` |
| `POST /tasks/{task_id}/score-rubrics/{rubric_id}/sections/{section_id}/classification` | `bid score rubric classify --task UUID --rubric UUID --section UUID --input DECISION.json --json` | `RubricClassificationView` / `[]` |
| `POST /tasks/{task_id}/score-rubrics/{rubric_id}/items/{item_id}/classification` | `bid score rubric classify --task UUID --rubric UUID --item UUID --input DECISION.json --json` | `RubricClassificationView` / `[]` |
| `POST /tasks/{task_id}/score-rubrics/{rubric_id}/sections/{section_id}/decisions` | `bid score rubric section decide --task UUID --rubric UUID --section UUID --input DECISION.json --json` | `RubricDecisionView` / `[]` |
| `POST /tasks/{task_id}/score-rubrics/{rubric_id}/items/{item_id}/decisions` | `bid score rubric item decide --task UUID --rubric UUID --item UUID --input DECISION.json --json` | `RubricDecisionView` / `[]` |
| `POST /tasks/{task_id}/score-rubrics/{rubric_id}/coverage/{requirement_id}/decisions` | `bid score rubric coverage decide --task UUID --rubric UUID --requirement UUID --input DECISION.json --json` | `RubricCoverageDecisionView` / `[]` |
| `POST /tasks/{task_id}/score-rubrics/{rubric_id}/decisions` | `bid score rubric decide --task UUID --rubric UUID --input DECISION.json --json` | `RubricSetView` / `[]` |
| `GET /tasks/{task_id}/score-rubrics/{rubric_id}/history?cursor=…&limit=…` | `bid score rubric history --task UUID --rubric UUID [--cursor CURSOR] [--limit N] --json` | `AssessmentListData` / `RubricHistoryItem[]` |
| `POST /tasks/{task_id}/scores/preview` | `bid score run --task UUID --draft UUID --rubric UUID --as-of YYYY-MM-DD [--reasoning LEVEL] [--max-charge DECIMAL] --dry-run --json` | `ScorePreview` / `[]` |
| `POST /tasks/{task_id}/scores` | `bid score run --task UUID --draft UUID --rubric UUID --as-of YYYY-MM-DD --expected-input-hash SHA256 [--reasoning LEVEL] [--max-charge DECIMAL] [--retry] [--wait] --json` | `ScoreJobAccepted`；wait 后为 `ScoreJobResult` / `[]` |
| `GET /tasks/{task_id}/scores?cursor=…&limit=…` | `bid score list --task UUID [--cursor CURSOR] [--limit N] --json` | `AssessmentListData` / `ScoreRunView[]` |
| `GET /tasks/{task_id}/scores/{report_id}` | `bid score show --task UUID --report UUID --json` | `ScoreReportData` / `[]` |

实际 path 前缀沿用现有 API router；这里固定相对资源结构。分页 limit 默认 50、范围 1–200，
cursor 绑定 org、task、资源种类和排序。路径 `task_id` 必须等于 extraction/draft/rubric/report 的
task，否则统一 404。耗时提交默认立即返回 Job；`--wait` 复用既有等待和状态查询。

所有成功、失败和 `--json` 输出都沿用 `contracts.CONTRACT_VERSION` 所定义的 Result 七键：
`ok`、`command`、`data`、`items`、
`warnings`、`cost`、`duration_ms`。`cost` 是该响应可证明的实际用量；preview 的估算在 data 的
`estimated_cost/estimated_charge`，不能冒充已花费用。schema 仅新增上述命令，不改变既有命令；
批准后同步注册 `bid schema`。

| 退出码 | score 明确语义 |
| --- | --- |
| 0 | preview、提交、列表、完整 rubric/report 读取成功；等待结果时只有全部项目可评估且可形成完整总分才为 0 |
| 2 | 参数、日期、UUID、expected hash/revision、非 current DraftRun、未确认 rubric、跨抽取绑定或输入完整性错误 |
| 3 | 尚未形成持久报告的可重试 Provider、队列、网络或临时存储失败 |
| 4 | 身份/权限、资源不存在、内容拒绝、固定输入/计费/引用完整性或不可重试 Provider 失败 |
| 5 | 已保留有效结果但有失败批次、unassessable item、不可聚合 section 或因此没有完整总分 |

异步“已接受”本身返回 0；`job wait` 和 `score show` 读取 partial、含 unassessable item 或没有完整
总分的报告时统一返回 5，不新增额外开关。
跨单位和无权资源仍统一 404；错误不回显原文、外发文字、模型原始输出或厂商错误体。

## 权限与人类关口

拟增加 `score:read`、`score:run`、`score:rubric:generate`、`score:rubric:review`：

- `score:read` 可按现有四种单位角色授予，也可进入 token allowlist；仍与 Membership 和任务读取
  权限取交集。
- `score:run` 与 `score:rubric:generate` 可授予 bidder/technical，并可显式进入 token allowlist，
  使外部 agent 能预览和发起 advisory 作业；它们不能做人工决定。
- `score:rubric:review` 只属于登录的人类 session，不进入 token `SCOPES`。数据库 CHECK/触发器
  与 service 双重拒绝 token/agent/worker actor。technical 只能确认或修订 technical 内容，bidder
  只能确认或修订 commercial 内容；提交完整替换快照时，
  其他职责内容必须与上一版逐字相同。整集确认由 bidder 完成。admin 只执行 section/item
  classify，不默认获得修订或跨专业确认权。history 随 `score:read` 可读。
- 评分报告不能自动确认卡片、修改 DraftRun、写入导出、填保密值或发布最终得分。内置/外部
  agent 与 token 不能把 advisory 结果变成人工决定。

路由必须先验证 session/token scope 和有效 Membership，再设置 `app.current_org`；后台 worker
携带提交时 org，只在该 RLS 上下文中读取。不能使用 BYPASSRLS 角色。

## 数据表与迁移轮廓

批准后新增以下业务表；名称可在实现迁移评审时微调，约束不能弱化：

| 表 | 作用与关键固定字段 |
| --- | --- |
| `score_rubric_sets` | task/extraction job/document、版本、输入哈希、规则/prompt/schema 版本、overall 规则、状态、确认人/时间 |
| `score_rubric_sections` | rubric、source、key/order、聚合、上下限、权重、纳入 overall、状态与 revision |
| `score_rubric_items` | rubric/section/requirement/source、fingerprint、规则、模式、上下限、权重、职责、状态与 revision |
| `score_rubric_coverage` | 每个 scoring Requirement 的 mapped/duplicate/excluded/pending、canonical 绑定与 revision |
| `score_rubric_coverage_items` | mapped coverage 与一个或多个 rubric item 的规范化复合外键关系；不把 item IDs 塞 JSON 假约束 |
| `score_rubric_decisions` | section/item/set 的 append-only 人类决定、原因 hash、revision、session actor 与时间 |
| `score_rubric_coverage_decisions` | coverage mapped/duplicate/excluded/reopen 的 append-only 人类决定 |
| `score_rubric_classifications` | section/item 的 append-only admin 人类职责分类 |
| `score_rubric_revision_events` | 新 candidate 版本与 prior rubric、理由、人类 actor 的 append-only 关联 |
| `score_reports` | job/run、AssessmentInput、rubric 固定版本、规则版本、完成/有效性、聚合状态与用量 IDs |
| `score_report_items` | report/rubric item/requirement/anchor ResponseItem、anchor 分区、结果、分数、原因与补强动作 |
| `score_report_item_responses` | assessed item 与一个或多个实际支持得分的 confirmed ResponseItem 的规范化复合外键关系 |
| `score_item_citations` | report item 与 verified tender/draft citation 的结构化绑定 |

每张表 `org_id UUID NOT NULL`、`task_id UUID NOT NULL`，同一迁移中 `ENABLE ROW LEVEL SECURITY`
和 `FORCE ROW LEVEL SECURITY`，策略只接受 `current_setting('app.current_org', true)` 的精确单位。
每张表都有 `(org_id,id)` 与需要的 `(org_id,task_id,id)` 唯一键；coverage 公开 view 含
id/org/task/rubric/revision，item IDs 由 `score_rubric_coverage_items` 授权聚合。所有 Task、Document、Job、
Requirement、DraftRun、ResponseItem、rubric/report/decision/citation 关系使用含 `org_id` 的复合外键，
任务内链再同时包含 `task_id`，数据库直接拒绝跨单位或跨任务拼接。必填业务字段均 NOT NULL；
可空只用于尚无值的 score、上下限、确认 actor/time 和可选原因，并配成对 CHECK。

迁移还应包含：状态/数值/actor-kind CHECK；每 rubric 版本、section key、item key/fingerprint、coverage
Requirement、report+rubric item 的唯一约束；确认 set 只能引用全部已确认 item/section 的数据库
关口；decision/classification/revision/citation append-only 权限；组织删除策略沿用现有业务表。
如实现 SQL view 或聚合 view，必须使用 security-invoker 与当前 org scope，不能以 view owner 绕过
RLS；API 也可在授权查询后组装 Pydantic 聚合 view。Job 复用现表，只增加明确
kind/cache/result schema 与处理器；`UsageRecord`、`VendorCall`、`AuditLog` 和余额表不复制。

迁移与同一实现改动必须包含两单位 A/B、无 org context、跨 task 复合外键、普通应用角色无法绕过
FORCE RLS 的端到端验收；每张新表、每个公开/聚合 view 和上述每条 route 都验证 A 不能读写 B。
先迁移/模型，再 service/provider/job，再 API/CLI/schema，最后控制台；任一阶段不能暂时用无 RLS
表承载候选或报告。

## 审计

固定成功事件码为 `score_rubric.submitted/completed/cancelled/revised/classified/decided` 和
`score.submitted/completed/cancelled`；固定失败事件码为 `score_rubric.failed`、
`score_rubric.decision_denied`、`score.failed`。持久审计默认只记录已认证、已解析 org 且进入业务
边界的动作；pre-auth、跨单位统一 404 和无法安全绑定对象的拒绝只进既有安全日志。审计 metadata
只含 org/task/object、actor kind/id、revision、输入/原因 hash、稳定 error code 和 usage IDs，不记
外发文字、保密值、模型原始输出或厂商错误体。

dry-run 严格零写入，因此不写 AuditLog；cached 命中返回既有 Job/report，不新增 submitted、
completed 或用量审计。是否把更多 pre-auth/404 失败持久化及相应强制测试，保留在“待决定”表，
但以上默认事件与 metadata 边界不再悬空。

## 评测依据

Provider 匹配实验的来源限制、可得结论与不可声称事项统一见
[B09 阶段二评测依据](check.md#阶段二评测依据)。B10 只继承其保守约束：所有模型引用都由本机逐字核验，
missing/unknown 不默认满分，匹配实验不冒充真实招标得分或重复性证据。本页不复述样本数值。

## 批准后验收

按仓库规则只写端到端验收，不为模型类重复实现编写单元测试。测试 Provider 使用结构化 fake，
真实服务评测放在 `evals/` 且不进默认 CI。至少覆盖：

1. 两单位、无 org context、跨 task/document/draft/rubric 外键与 FORCE RLS；token scope、失效
   Membership、职责错位和 agent 试图确认全部被拒，不能由 404 枚举他单位对象。
2. rubric 只读取指定 extraction Job 的全部 scoring Requirements；不读 condition、不遍历 Chunk/
   全文，只按 Source 读取所指 chunk/block 验引；覆盖、显式去重、修订新版本、admin 分类、逐项/
   整集确认、history、权重/上下限/section 与 overall 合计关口可重复。
3. score 只读取指定 current DraftRun 的全部 confirmed response 候选及 comply-only/gap metadata；
   跨 Requirement 支撑行能被逐字验证并写入 `response_item_ids`，只有招标侧引用不能 assessed。未确认
   Card、后来 Card、released DOCX、模板和真实保密值均不进入可见 fake Provider 请求；遮挡关闭
   时 preview 阻止且正式提交不产生 Job/Provider 调用。
4. sum/weighted_sum/capped_sum 的小数权重、cap、范围与 ROUND_HALF_UP；formula/non_additive 永不
   执行但不阻止规则完整确认。覆盖歧义、价格比较、外部比较、薄承诺、负偏离、低于满分却无
   deduction reason、非法越界分、未知/歧义/遮挡引用、重复/漏答 Provider item；只保留有效批次，
   任何 partial 小计不成为 total，补强动作不能生成假证件或报价策略。
5. preview 无写入/无调用；expected hash、redaction revision、rubric/Draft current fence；lease 过期、
   run_id 接管、取消、并发批次、已发送结果不明 reservation、UsageRecord 唯一和预付余额只扣一次。
6. HTTP 与远程/本地 CLI Result 七键、schema、0/2/3/4/5、所有 route 的 task 绑定、分页、缓存与
   retry；历史 partial report 的 show 恒返回 5。端到端生成一个脱敏 JSON
   rubric/report 工件，能核对 source、confirmed revisions、usage IDs、unassessable 和总分缺失原因；
   工件放测试临时目录，不写入 `docs/`。

实施时运行受影响的 ruff、pyright、迁移、PostgreSQL/RLS、API/CLI 和 worker 端到端关卡；按固定
事件码验证成功、业务失败、dry-run 零写入和 cached 不重复审计。未接真实 Provider、未读 released 文件和未做版式审查必须
留在报告 limitations，不能用“score 已完成”概括为最终投标评审。

## 待决定

| 事项 | 选项 | 推荐默认 | 理由 |
| --- | --- | --- | --- |
| 平台默认评分 Provider（Clef、DeepSeek、GLM 等候选） | 选一个平台默认；按单位覆盖；暂不提供平台默认 | 沿用 [B09 已定决定](check.md#已定决定)，B10 不单独选型 | check/score 应共享中文长文、结构化输出、引用、价格和数据政策评测，避免同一能力出现矛盾默认值 |
| 失败持久审计扩展 | 把 pre-auth/统一 404 全部写 AuditLog；只记已认证且已绑定 org/object 的业务失败；仅安全日志 | 只持久化本文固定的已认证业务失败事件，pre-auth 与跨单位 404 留安全日志 | 避免审计表本身形成对象枚举或高噪声；批准扩展前仍需确定不会泄露目标 ID 的测试方法 |
| `Task.budget_usd` 执行 | 全任务累计硬门槛；只提醒；继续不执行 | 首版继续不执行，只使用平台预付准入与单 Job `max_charge` | 当前没有跨 Job 累计的一致事务；`max_charge` 也不能限制单位自带 key 的厂商账单 |
| 价格及主观评分 | 接入其他投标人/基准价与受控算法；人工录入结论；保持 unassessable | 首版全部 unassessable | 缺少可验证外部输入，自动猜测会制造虚假精度，且报价策略属于设计非目标 |
| released-export 评分 | 扩展现有命令读取；另建人类会话专用流程；不支持 | 不扩展现有命令；需要时另立 human-only 契约 | released 文件含真实保密值并受 export 下载权限保护，不能把 agent 的 `score:run` 变成下载通道 |
| 人工改分、采纳与 UI | 直接改报告；追加人工决定；只展示 advisory 报告 | 后续另立追加式决定与界面契约，首版不改报告 | 保留模型输出、人工判断和最终评标结果的不同来源，避免覆盖历史 |

以上决定批准前，不创建迁移、运行时代码、权限、命令、测试或外部调用。
