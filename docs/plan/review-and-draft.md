---
kind: plan
---

# 契约草案：逐条人工确认与偏离表初稿

状态：**待批准，未实施**。对应[路线图](roadmap.md) B07、B08；本页提出接口与验收
目标，不代表功能已交付。批准范围由末尾的[待你决定](#待你决定)确定。

## 目标与边界

建议完成一条链：选择抽取作业 → 为每条要求建立响应卡片 → 关联固定材料并填写响应 →
按职责人工确认 → 生成分表初稿与缺口清单。入口为 API 和本地、远程 CLI，不依赖看板。

硬规则以 [agent.md 的硬性规则](../../agent.md#硬性规则任何情况下都不得违反)为准；
实体与流程依据[设计文档的数据模型](../AI%20标书工具设计文档.md#数据模型)、
[处理流程](../AI%20标书工具设计文档.md#处理流程与-cli-清单)和
[卡片设计](../AI%20标书工具设计文档.md#看板与-agent-设计)。本页只定义本切片新增契约。

本切片消费 B04 已有来源及 R02—R06 固定资源，不宣称完成取证全链。明确排除：

- B06 真实功能截图采集、`ui mock` 与原型生成；B05 Rust 标注和服务端派生图归档。
- B11 Word/PDF 导出、R01 模板适配、附件编排；输出是结构化初稿，不是可交付标书。
- B10 评分；B09 全文语义校验和风险卡。本页的确认前校验只检查本切片的输入与关口。
- 网页/白皮书取证、新材料上传类型、自动真实性判断、自动型号匹配、参数单位换算。
- 要求补录、修改抽取结果、跨抽取作业合并、多文档合并初稿、看板/SSE、自动记忆。

## 抽取作业、卡片与材料的绑定

读取作业历史和指定作业要求沿用
[reasoning-levels.md](../notes/reasoning-levels.md#usage)中的 `req history`、
`req list --job`。新增写入及 `draft` 必填抽取作业 ID，不接受隐含的“最新作业”。
所选作业必须是同单位、同任务、已成功的 `extract`；其真实 `document_id` 固定本次范围。

每个 `(org_id, task_id, extraction_job_id, requirement_id)` 最多一张响应卡片。卡片固定
绑定原要求及其来源，不允许换绑、修改招标原文或覆盖旧作业结果。列表以所选作业的全部
要求为基准：尚无卡片的要求也返回，标为 `missing_card`。不因类别、★ 标记或难以响应而
静默排除要求；初稿完整性只针对这份抽取结果，不能声称招标文件已无漏抽。

`EvidenceSource` 的边界仍由
[unconfirmed-evidence-sources.md](../notes/unconfirmed-evidence-sources.md)及
[`EvidenceSourceArchive`](../../server/app/schemas/evidence_source_contracts.py)定义。
新建的 `Evidence` 表达“该要求的响应使用这份材料、据此作出这个判断”，与来源档案分开。
确认只写新 Evidence/Card；原档仍是 `unconfirmed_source`，其 `confirmed_by` 和
`eligible_for_draft_export` 不变。人工确认不等于系统认证原件真伪。

| 材料输入 | 固定关系与允许用途 | 不能推导的结论 |
| --- | --- | --- |
| 产品 | `task_resources` → `product_revisions`，引用具体字段，保留型号、版本及包号 | URL 元数据不等于已访问网页或硬件参数截图；没有真实截图材料时不能确认硬件满足 |
| 功能 | `task_features` → `feature_revisions`，保留产品关联和声明状态 | `implemented` 仍是声明；`developing/planned` 不得响应成已经实现，不补造功能截图 |
| 证书声明 | `task_certificates` → `certificate_revisions`，保留编号、声明日期 | 日期检查不等于真实性或资格有效性判断；要求原件证明时仅有声明不足 |
| 证书 PDF 页 | 同任务 `evidence_source_id` → 固定证书选择、修订、原件、页码及 PNG 哈希 | 保留用户提供材料的来源性质，不包装成厂家或第三方认证 |
| 单位资料 | `task_org_profiles` → `org_profile_revisions`，引用具体字段 | 公司常用表述不等于已核验业绩合同或其他不存在的附件 |

版本选择机制沿用[产品快照](../notes/versioned-resources.md)、
[功能声明](../notes/versioned-features.md)、[证书声明](../notes/versioned-certificates.md)及
[单位资料](../notes/versioned-profiles.md)。不把模板当证据，不把记忆当材料。
元数据只能支持其实际记载的声明；招标条款要求附件、截图或参数实证而现有类型无法提供时，
必须保留缺口。声明文字不能经人工点击被升级成不存在的证明文件。

每项 Evidence 保存材料种类、固定选择及修订、引用字段/页码、摘录和材料性质。
修订、哈希、页码等由服务端从父记录解析，不信任客户端副本。字段摘录须逐字出现在所引
修订字段；PDF 页摘录初始为 `quote_check=unreviewed_page`，人对照原页确认后才记录
`human_page_review`，不冒称完成 OCR 核验。空摘录、任意 URL、任意磁盘路径或脱离任务
的资源不能成为 Evidence。

## 状态、职责与人工关口

卡片采用以下状态；它们描述响应处理进度，不表示材料本身变成认证文件。

| 当前状态 | 动作与目标状态 | 条件与执行者 |
| --- | --- | --- |
| 无卡片 | 创建 → `draft` | 具备 `card:write` 的成员或受限令牌；绑定不能再改 |
| `draft` | 编辑 → `draft`；提交 → `pending_review` | 可修改响应和材料候选；提交由服务端报告缺项，材料不足也可送人处理 |
| `pending_review` | 确认 → `confirmed` | 对应职责的人类登录身份；所有确认前检查通过 |
| `pending_review` | 驳回 → `rejected`；需补材料 → `needs_material` | 同一确认职责；必须填写原因 |
| `rejected` / `needs_material` | 编辑 → `draft` | 新修订保留处理意见，补充后重新提交 |
| `pending_review` | 撤回 → `draft` | 编辑者撤回并记录原因；不允许在待审版本上直接覆盖内容 |
| `confirmed` | 重开 → `draft` | 仅对应确认职责的人，填写原因；旧确认保留为历史 |

所有动作均增加 `revision`。确认、驳回、补材料、重开不能由令牌或 agent 执行；不提供
批量“一键确认”、默认确认或自动从 `draft` 到 `confirmed` 的路径。agent 不得修改、
重开或覆盖已确认卡片；只有人先重开后，新 `draft` 才重新开放编辑。

建议新增范围 `card:read`、`card:write`、`draft:run`、`draft:read`，确认及人类决策复用
受保护的 `evidence:confirm`。授权与既有
[`Identity` / `ROLE_SCOPES`](../../server/app/services/auth.py)交集规则一致：

| 身份 | 查看 / 编辑 / 组表 | 人类决策 |
| --- | --- | --- |
| `admin` | 查看、编辑、提交、组表；对待定类别指定确认职责 | 建议不默认获得跨专业确认权限 |
| `bidder` | 查看、编辑、提交、组表 | 商务/资格职责的确认、驳回、补材料、重开 |
| `technical` | 查看、编辑、提交、组表 | 技术职责的确认、驳回、补材料、重开 |
| `viewer` | 仅查看 | 无 |
| API token / 内置或外部 agent | 仅显式授予的非决策范围，与有效成员权限取交集 | 永远无；即使签发人有确认权限也拒绝 |

每次读取、编辑、确认、组表还须有 `task:read` 及所涉及材料的读取权限：产品/功能为
`resource:read`，证书为 `certificate:read`，单位资料为 `profile:read`，PDF 页同时要求
`evidence:source:read`、`certificate:read`、`certificate:file:read`。不能通过卡片或初稿
绕过原材料的权限。旧令牌不自动获得新增范围；无权或跨单位对象统一 404。

`review_domain` 与输出分表是两个概念：`technical` 要求固定技术职责，`qualification`
固定商务职责；`substantive` / `scoring` 若无法从既有类别确定职责，先留空并阻止确认，
由人类 `admin` 显式分类并记录依据。agent 不可改变分类；原始类别、★ 标记不能删改。
实质性条款即使进入同一张表，也须按其职责由对应角色确认。本切片采用单人逐卡确认，
不新增会签、成员角色管理或审批流。

### 确认前检查

确认人提交 `expected_revision` 和本次逐项审阅的全部 `evidence_ids`。服务端必须重新
读取并核验，不能把客户端的 `confirmed_by`、`is_human` 或空 token ID 当认证依据：

1. 当前是 `pending_review`；身份、成员有效性、职责和材料读取权限仍有效。
2. 要求属于固定作业，来源位置及逐字原文可核验；不得拿 `Requirement.text` 的摘要替代
   `Source.quote`。PDF 使用页码，Word 使用结构位置，规则见
   [docx-citations.md](../notes/docx-citations.md)。既有抽取使用规范化匹配的引用也须在
   组表前复查逐字一致；不一致则列 `invalid_citation`，不能静默修补已抽取的原文。
3. 响应文字非空；偏离类型已选择；无偏离须说明对应关系，正/负偏离须说明具体差异。
   `deviation_note` 不得只写“满足”；负偏离必须保留未达到的数值、状态或条件。
4. 至少关联一项实际材料，审阅 ID 集合与本修订链接完全相等；材料属于当前任务且选择
   仍有效。摘录、声明性质、型号/版本、包号、证书日期与所作响应不得矛盾；无法可靠
   自动判断的关系由人核对并承担确认，不声称本切片具有语义或参数自动判定能力。
5. 未实现状态不得确认成已实现；附件缺失不得确认成已附；只有产品 URL 不得确认成
   有硬件截图。已知不满足可确认成 `negative`，不能通过改措辞掩盖。

任一检查失败，不写确认、不产生半数已确认的 Evidence。卡片、所关联 Evidence 的
`confirmed_by/confirmed_at` 及审计在同一事务提交，确认人由认证上下文写入。
证据已经被同一卡片历史修订确认且内容未变时保留原确认记录，本次卡片仍须人工确认。

无材料与负偏离分开：有实际材料证明不满足，可以确认负偏离并进入初稿；完全无材料
只能 `needs_material` 并进入缺口，不允许捏造一条空 Evidence 来满足确认条件。

### 并发、历史和失效

写入必带 `expected_revision`；锁定顺序为任务 → 卡片 → 关联记录，并与任务资源替换
采用相同任务锁。版本不符返回 HTTP 409、`revision_conflict`、退出码 2，无新修订或
成功审计；调用者必须重新读取，不自动合并、重试覆盖。状态相同也不跳过版本检查。

重新抽取产生独立要求集合：即使原文或 fingerprint 相同，旧卡片也不迁移到新 job。
旧作业卡片继续可查，必须显式指定旧 job 才能生成其初稿，并显示历史作业警示。
新 job 没有卡片的要求全部列为缺口，不能从旧结果拼成“最新完整响应”。

资源库新增修订不影响已固定的任务选择；显式替换任务选择则使相关卡片的派生
`eligibility` 成为 `stale_material`，不得再供新初稿使用。保留旧确认与材料记录，
返回受影响卡片 ID；恢复使用须由人重开、选择材料并重新确认，不能因又选回旧修订而
自动恢复。旧卡片里的选择 ID 仍是已关闭的历史选择。

`state` 是历史操作结果，`eligibility` 是读取/消费时计算的有效性，不能仅判断
`state == confirmed`。重新解析造成引用不可核验时同样停止消费并报告
`invalid_citation`，不改写历史原文。新确认或资源替换与 draft 提交/落盘之间必须重验，
避免用早先检查结果越过关口。

## 偏离表初稿

建议首版只采用确定性组表：人填写卡片响应、确认整段文字及偏离判断后，`draft` 原样
复制已确认内容。不自动改成“完全满足”，不补齐没有确认的条目，不运行 LLM。

表结构采用逐条重复招标条款并逐项作答的形式，与人工中标文件的响应方式一致。
不依赖读取或导入样例中标文件，不复制其未知业务内容。三张表固定为：

| 表标识 | 标题 | 归入规则 |
| --- | --- | --- |
| `substantive` | 实质性响应一览表 | `starred=true` 或原始类别为 `substantive`，优先归入 |
| `commercial` | 商务响应偏离表 | 非实质性且商务职责，包括资格与商务条款 |
| `technical` | 技术响应偏离表 | 非实质性且技术职责 |

同一要求只进入一个主表，保留原始类别和 ★ 标记；不确定分类的要求列缺口。
`scoring` 类也必须逐条覆盖，按人工确定的职责分表，不计算得分。表内按原文顺序排列
（文档 chunk 顺序、页/块位置、要求 ID 作稳定尾序），不能按“好响应”优先筛选。

每行包含：

- **招标文件条款**：`Source.quote` 原样全文及来源文件 ID/名称、位置标签。PDF 标签为
  “文件名 · 第 N 页”，Word 标签使用真实 `Location.label`（章节、段落或单元格），
  `page=null`；不能截断、省略成“同上”、改写引文或猜测 Word 页码。
- **投标响应**：人工确认的 `response_text`。正/负偏离另外显示已确认的
  `deviation_note`；负偏离固定可见“负偏离”及差异，不设隐藏负偏离的过滤选项。
- **偏离情况**：`none / positive / negative` 分别显示“无偏离 / 正偏离 / 负偏离”。
  “未找到材料”不是“无偏离”，确认也不等于“满足”。
- **证据引用**：所用 Evidence ID、固定任务选择/修订、材料性质、字段摘录或原件 PDF
  页码/哈希、确认人/时间与卡片修订。只含可回溯标识，不把存储路径或长期下载 URL
  写进初稿；查看原件仍走既有受权短期预览。

初稿对所选作业的要求实行集合守恒：每条要求恰好是一条响应行或一条缺口，不能两者
皆无或重复。缺口保留原文、位置、卡片/修订（若有）及原因，不含生成的投标响应或默认
偏离值。原因包括 `missing_card`、`unconfirmed`、`rejected`、`needs_material`、
`unclassified`、`stale_material`、`invalid_citation`。多个原因可同时列出。
被抽取环节拒绝的条目和解析遗漏警示以作业级警示关联展示，不能冒充已保存 Requirement。

草稿保留 `status=draft`，即使所有输入都已经确认也不变成定稿或获得 export 权限。
已有初稿是不可变历史快照；后续卡片重开或材料失效时，读取返回
`validity=stale`、具体受影响要求及原因。原快照仍可审阅，但不得冒充当前可交付结果。
未来 B11 必须重新检查关口，不能只凭旧初稿的生成时间放行。

### LLM 起草的取舍

本切片建议不允许服务端 LLM 改写或生成响应，因此不新增 Provider 接口或模型配置能力。
若后续批准 LLM 辅助，另立契约：仅输入同单位、同作业、已人工确认的 Evidence 及原条款，
每句事实关联已有 Evidence ID；不得扩写参数、证书、业绩、实现状态，不得改变负偏离。
结果必须新建 `draft` 修订并由人确认文字后才能被组表消费，不能复用旧文字的确认。
结构化引用校验不能保证语义真实，仍须人工复核。Provider 与 UsageRecord 规则沿用
[agent.md](../../agent.md#硬性规则任何情况下都不得违反)，模型配置问题归
[provider-config.md](provider-config.md)，本页不重复定义。

## 拟新增数据模型与数据库关口

以下均为待批准的表设计。业务新表统一 `org_id NOT NULL`、`UNIQUE(org_id, id)`、
ENABLE/FORCE RLS；策略同时限制读取与写入，缺单位上下文拒绝。运行角色不得拥有业务表、
使用超级用户或 BYPASSRLS；迁移、授权及双单位隔离验收同次交付。

| 表 | 字段与约束 | 可变范围 |
| --- | --- | --- |
| `response_cards` | `task_id, extraction_job_id, requirement_id, current_revision_id, revision`；上述要求绑定唯一；当前修订必须属于本卡片 | 只更新当前指针与递增版本，不换绑或删除 |
| `response_card_revisions` | `card_id, revision, state, review_domain, response_text, deviation, deviation_note, reason, confirmed_by, confirmed_at, actor_user_id, actor_token_id, actor_kind, created_at`；`UNIQUE(org_id,card_id,revision)` | 只追加；每次编辑或状态变更一条修订，旧修订保留 |
| `evidence` | `card_id`、带类型的任务选择/修订外键、可选 `evidence_source_id`、`field_path/quote`、`material_kind/quote_check`、服务端派生哈希、`confirmed_by/confirmed_at` | 内容不可变；编辑材料产生新 ID；只允许受关口保护的首次确认及页摘录核对标记，不覆盖确认人 |
| `card_evidence_links` | `card_revision_id, evidence_id`，组合唯一，并约束两者属于同一卡片、同任务 | 只追加；状态修订可复用未变的 Evidence，新材料初始未确认 |
| `draft_runs` | `task_id, extraction_job_id, generation_job_id, input_hash, requested_by, actor_token_id, created_at`；同任务/抽取/输入指纹唯一，存固定输入清单及完成摘要 | 输入不可变；完成只由对应作业 attempt 原子写入 |
| `response_items` | `draft_id, requirement_id, card_revision_id`（缺口可空）、`kind=row/gap`、`table_kind`、原文来源快照、响应/偏离快照或 `gap_reasons`、排序键 | 只追加；`UNIQUE(org_id,draft_id,requirement_id)`；row 与 gap 字段互斥 |

响应行的 Evidence 集合通过固定 `card_revision_id` 的不可变链接读取，不另存一个可能
与关系表分叉的 JSON ID 列表。初稿快照保存审核当时的响应，不是资源元数据的新维护入口。
`draft_runs` 的固定输入清单须能核对每条要求当时的卡片修订或无卡片状态，不能只存哈希。

所有业务引用使用含 `org_id` 的复合外键，并在关键关系上连同 `task_id`、`job_id`、
`card_id` 或父资源 ID 一起约束，不能仅校验“两个 UUID 都属于本单位”：

- 卡片 → 同任务、同抽取作业的 Requirement；抽取作业 → 同任务、同招标 Document。
- Evidence → 同卡片、同任务的具体选择 → 同资源不可变修订；PDF 页再精确绑定
  source 的证书选择/修订/原件。按材料类型使用独立可空外键列及互斥 CHECK，禁止无
  外键保障的通用 `resource_id`；必要的父表复合唯一键随迁移补齐。
- `response_items` → 同 draft 所选 job 的 Requirement 及同要求卡片修订；
  `confirmed_by/actor_user_id` → `(memberships.org_id, memberships.user_id)`，
  token → `(api_tokens.org_id, api_tokens.id)`，不直接把全局 User 当单位成员。

数据库不得只保护令牌签发，还须保护确认写入：

1. 保留 `ApiToken` 禁止 `evidence:confirm` / `export` 的 SQL 约束；签发服务同样拒绝。
2. 认证层在事务内设置可信的 `actor_kind`、user、token 和单位上下文。API 请求体
   不得设置或覆盖这些值；缺身份上下文的业务写入失败。内置 agent 必须携带 agent
   身份，不能因继承发起人的登录权限就伪装成人；worker 也不是人类身份。
3. 确认相关触发器/受控写入校验 `actor_kind=session`、token 为空、有效 Membership、
   对应职责、合法状态迁移及版本。API token、agent、worker 或缺上下文的直接 SQL
   INSERT/UPDATE，即使填入合法人的 `confirmed_by`，也须失败。
4. DB 检查 `confirmed` 卡片必有确认人/时间、非空响应与 Evidence 链接，全部 Evidence
   已确认；非 confirmed 新修订不得伪填确认人。以延迟约束触发器检查跨表完整性，避免
   多表写入顺序造成半完成状态。禁止改删历史，禁止绕过状态迁移直接改当前指针。

已有 Evidence 上的确认记录不因重开删除，但只有“当前 confirmed 卡片修订 + 全部关联
Evidence 已确认 + 仍有效的选择/引用”共同成立才可消费。draft 的服务与落盘数据库
校验均执行该谓词；数据库的结构关口不替代人工的材料内容核对。

审计复用 `audit_logs`，不新增并行日志体系。记录 create/edit/classify/submit/withdraw/
confirm/reject/needs-material/reopen、draft 提交和完成；包括单位、任务、作业、要求、
卡片/修订、旧新状态、身份种类、user/token、时间及请求关联 ID。处理原因保存在受权
卡片修订中，审计只存原因代码及记录 ID，不复制原文、响应、报价或敏感资料。
业务与成功审计同事务，提交失败全部回滚；权限拒绝和冲突返回脱敏错误，不写成功审计。

## Pydantic 契约草案

以下名称及字段仅在本文供审批，实施时再进入 schemas、API 和 `bid schema`。
共用 [`Contract`、`Source`、`Cost`、`Result`](../../server/app/schemas/contracts.py)，
保留 `extra=forbid`、Word `page=null` 与位置互斥规则。不复制或冻结现有契约版本号。

```python
CardState = Literal["draft", "pending_review", "confirmed", "rejected", "needs_material"]
ReviewDomain = Literal["commercial", "technical"]
Deviation = Literal["none", "positive", "negative"]
TableKind = Literal["substantive", "commercial", "technical"]

class ResourceEvidenceInput(Contract):
    kind: Literal["product", "feature", "certificate", "org_profile"]
    selection_id: UUID
    field_path: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=20000)

class PageEvidenceInput(Contract):
    kind: Literal["certificate_pdf_page"]
    evidence_source_id: UUID
    quote: str = Field(min_length=1, max_length=20000)

EvidenceInput = Annotated[
    ResourceEvidenceInput | PageEvidenceInput, Field(discriminator="kind")
]

class CardContent(Contract):
    response_text: str | None = Field(default=None, min_length=1, max_length=20000)
    deviation: Deviation | None = None
    deviation_note: str | None = Field(default=None, min_length=1, max_length=10000)
    evidence: list[EvidenceInput] = Field(default_factory=list, max_length=100)

class CardCreate(Contract):
    extraction_job_id: UUID
    requirement_id: UUID
    content: CardContent

class CardUpdate(Contract):
    expected_revision: int = Field(ge=1)
    content: CardContent

class CardAction(Contract):
    expected_revision: int = Field(ge=1)
    action: Literal["submit", "withdraw", "confirm", "reject", "needs_material", "reopen"]
    reviewed_evidence_ids: list[UUID] = Field(default_factory=list, max_length=100)
    reason: str | None = Field(default=None, min_length=1, max_length=10000)

class CardClassify(Contract):
    expected_revision: int = Field(ge=1)
    review_domain: ReviewDomain
    reason: str = Field(min_length=1, max_length=10000)

class EvidenceView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    card_id: UUID
    input: EvidenceInput
    selection_id: UUID
    resource_revision_id: UUID
    material_kind: Literal["declaration", "user_supplied_pdf_page"]
    quote_check: Literal["exact_field_match", "unreviewed_page", "human_page_review"]
    source_archive: EvidenceSourceArchive | None = None
    confirmed_by: UUID | None
    confirmed_at: datetime | None
    active_selection: bool

class CardView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    requirement_id: UUID
    revision: int = Field(ge=1)
    revision_id: UUID
    state: CardState
    review_domain: ReviewDomain | None
    source: Source
    content: CardContent
    evidence: list[EvidenceView]
    confirmed_by: UUID | None
    confirmed_at: datetime | None
    eligibility: Literal["eligible", "unconfirmed", "unclassified", "stale_material", "invalid_citation"]

class DraftRequest(Contract):
    extraction_job_id: UUID
    dry_run: bool = False
    retry: bool = False

class DraftPreview(Contract):
    dry_run: Literal[True] = True
    task_id: UUID
    extraction_job_id: UUID
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    eligible_requirements: int = Field(ge=0)
    gap_requirements: int = Field(ge=0)
    table_rows: dict[TableKind, int]
    gap_reasons: dict[str, int]
    negative_deviations: int = Field(ge=0)
    estimated_cost: Cost
    estimated_duration_ms: int | None = Field(default=None, ge=0)

class ResponseRow(Contract):
    requirement_id: UUID
    card_id: UUID
    card_revision_id: UUID
    table: TableKind
    tender_clause: Source
    location_label: str
    response_text: str
    deviation: Deviation
    deviation_note: str
    evidence: list[EvidenceView] = Field(min_length=1)

class DraftGap(Contract):
    requirement_id: UUID
    card_id: UUID | None
    card_revision_id: UUID | None
    tender_clause: Source
    location_label: str
    reasons: list[Literal[
        "missing_card", "unconfirmed", "rejected", "needs_material",
        "unclassified", "stale_material", "invalid_citation"
    ]] = Field(min_length=1)

class DraftView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    generation_job_id: UUID
    status: Literal["draft"] = "draft"
    completion: Literal["complete", "partial"]
    validity: Literal["current", "stale"]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tables: dict[TableKind, list[ResponseRow]]
    gaps: list[DraftGap]
    invalidated_requirements: list[UUID]
```

草案中的模型须补充以下验证器及对应 DB 约束，不能只按字段类型实施：

- 输入字符串去空白后不得为空；字段路径采用对应资源 schema 的可引用文本字段白名单，
  不接受任意 JSONPath。`kind` 决定选择表，不能混用其他类型 ID。
- confirm 的 `reviewed_evidence_ids` 非空、无重复且精确覆盖当前链接；其他动作必须为
  空。withdraw/reject/needs_material/reopen 和 classify 必须有 reason。
- content 可在 draft 阶段不完整；confirm 须同时具备 response、deviation、说明和材料。
  `source_archive` 仅 PDF 页有值，复用既有 Archive 契约且仍显示恒未确认原档字段；
  `EvidenceView.confirmed_by` 表示另一实体的人工判断，两者不得混淆。
- tables 必须同时含上述表键，即使某表为空；每行 table 与所在键一致。row/gap 集合
  必须与所选 job 的全部 Requirement 集合相等，partial 与非空 gaps 一致。
  DraftPreview 的 table_rows 也须含全部表键、值非负；gap_reasons 仅允许 DraftGap
  的原因代码且计数非负，按原因计数允许同一缺口出现于多个原因。
- 输入不允许 `org_id`、确认人/时间、原始类别、来源修订或 state 等服务端字段。
  时间为带时区值；引用损坏的 gap 仍保留原始 Source 并标错，不伪造可用位置。

## CLI、API 与 Result

所有命令支持 `--json`；缺参立即报错，无交互式提问。`--input FILE` 使用上述模型，
任务来自路径/`--task`，`--job` 专指抽取 job，生成 job 则使用返回的 `generation_job_id`。

| CLI | API | 输入 / 输出 |
| --- | --- | --- |
| `bid card list --task T --job J` | `GET /tasks/{T}/cards?job={J}` | 全部要求槽位；每项含 requirement 与 CardView 或 `missing_card` |
| `bid card show --id C [--history]` | `GET /cards/{C}?history=...` | 当前 CardView，或按修订序返回历史；派生失效原因可见 |
| `bid card create --task T --input FILE` | `POST /tasks/{T}/cards` | CardCreate → CardView；重复要求绑定返回 409，不复制卡片 |
| `bid card update --id C --input FILE` | `PUT /cards/{C}` | CardUpdate → CardView，完整替换可编辑内容 |
| `bid card classify --id C --input FILE` | `POST /cards/{C}/classification` | CardClassify → CardView；只允许待分类的 draft，由人类 admin 执行 |
| `bid card submit/withdraw/confirm/reject/needs-material/reopen --id C --expected-revision N ...` | `POST /cards/{C}/actions` | CLI 动作映射 CardAction；confirm 必填可重复 `--evidence ID`，原因用 `--reason` |
| `bid draft --task T --job J [--dry-run] [--retry] [--wait]` | `POST /tasks/{T}/drafts` | DraftRequest → 作业回执或 dry-run 预检；`--wait` 使用既有 job 等待机制 |
| `bid draft show --id D` | `GET /drafts/{D}` | DraftView；读取前重算 validity，不改历史响应 |
| `bid draft list --task T --job J` | `GET /tasks/{T}/drafts?job={J}` | 已完成初稿摘要，显式按抽取 job 过滤 |

confirm 及其他人工决策的路由/服务均检查身份类型与范围，不只依赖 CLI 隐藏命令。
不新增 export 路由或权限；`draft show` 也不能变成附件下载或 Word 导出通道。
审批后新增命令注册到 `bid schema`，参数、输出模型及 CLI 快照必须一致；现有命令
字段保持兼容。若实施需要不兼容调整，遵循 [agent.md 的 CLI 契约](../../agent.md#硬性规则任何情况下都不得违反)。

Result 顶层严格保留七个键：`ok`、`command`、`data`、`items`、`warnings`、`cost`、
`duration_ms`。详情/写入 `data` 为模型回执、`items=[]`；列表 `items` 为各项，`data`
包含查询范围。draft 回执在 `data` 内放草稿/作业字段，不新增顶层键；错误沿用
[`ServiceError` 的 Result 映射](../../server/app/api/main.py)，放 `data.error`，不回显原输入。

示意的 dry-run JSON（只展示契约形状，不是运行证据）：

```json
{
  "ok": true,
  "command": "draft",
  "data": {
    "dry_run": true,
    "task_id": "11111111-1111-4111-8111-111111111111",
    "extraction_job_id": "22222222-2222-4222-8222-222222222222",
    "input_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "eligible_requirements": 2,
    "gap_requirements": 1,
    "table_rows": {"substantive": 1, "commercial": 0, "technical": 1},
    "gap_reasons": {"unconfirmed": 1},
    "negative_deviations": 0,
    "estimated_cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0},
    "estimated_duration_ms": null
  },
  "items": [],
  "warnings": ["预检发现未确认要求；正式生成时将列为缺口。"],
  "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0},
  "duration_ms": 12
}
```

| 退出码 | 本切片语义 |
| --- | --- |
| 0 | 卡片动作成功、查询成功、作业已受理、完整初稿或 dry-run 完成；作业受理不等于组表完成 |
| 2 | 缺参/非法输入、非成功抽取 job、空要求集、版本冲突、非法迁移、确认前缺材料/未分类 |
| 3 | 临时网络/队列/存储故障，或作业提交与落盘间输入变化导致 `draft_input_changed`；重新预检后重试 |
| 4 | 身份/角色/范围失败、API token 试图确认、跨单位或无权对象 404、材料完整性不可恢复错误 |
| 5 | 组表完成但存在缺口；`completion=partial`，完整返回已确认行和缺口，不返回伪造响应 |

错误退出时 `ok=false`；部分成功同样 `ok=false` 并带完整结果。提交时 `ok=true` 只
表示受理；`--wait` 返回最终完成码。`job status/wait` 及 `draft show` 对组表结果采用同一
partial 映射；基础设施 Job 可为 `succeeded`，业务完成度仍由 `completion` 明确表示。
负偏离是有效的已确认响应，本身不触发退出码 5，但必须产生显式警示；全部条目均为缺口
仍返回 partial，不能把空表当完整成功。历史失效初稿读取返回历史内容和警示，不能被
`completion=complete` 掩盖 `validity=stale`。

## 作业、dry-run 与费用

draft 使用后台作业并复用[后台作业机制](../notes/background-jobs.md)的取消、有限重试和
attempt 隔离。`jobs.kind=draft` 的 `document_id` 取自固定抽取 job 的真实招标文件，
不用证书文件冒充 Document，也不为此把所有 Job 的 document 字段放宽为空。

提交时在一致性事务中固定要求集合、卡片修订、材料选择状态和确定性组表规则标识，生成
`input_hash`。同输入重复请求复用 job/draft；输入改变产生新初稿，不覆盖旧行。worker
恢复单位上下文并复验发起人的成员/读取/组表权限，不能继承人类确认权；完成事务再次
核验所有输入及消费关口。不一致则失败、不发布半份初稿；取消和旧 attempt 不得落盘。

`--dry-run` 执行相同授权与输入检查，返回各表预计行数、缺口原因、负偏离数及输入指纹；
不写卡片、作业、审计或文件，不发起模型/OCR/存储派生，输出遵循 DraftPreview。
dry-run 不保证之后输入未变，正式提交必须重验。

本切片不调用 Provider，实际 `cost.llm_tokens=0`、`ocr_pages=0`、`usd=0`，不制造空
UsageRecord 或声称零基础设施成本。执行耗时实测，未建立预估方法时
`estimated_duration_ms=null` 并解释未知。LLM 模式不接受静默 fallback 或伪造零成本，
其付费调用和估算属于后续获批契约。

## 批准后的实施顺序与端到端验收

按契约模型与权限 → 数据迁移/RLS/状态关口 → 卡片 CLI/API → draft worker/结果 →
端到端验收推进。以下是未来验收标准，本文没有运行或宣称通过功能测试。

1. **真实链路与职责**：以获准使用的真实招标 PDF、DOCX 和真实来源材料，从登录、选择
   抽取 job、建卡、提交、相应角色确认走到 draft；核对三表逐条条款/位置/响应/偏离/
   Evidence，引用可在原件复查。CI 用明确标识的合成隔离材料与 Provider fake，不作为
   生产材料，也不把假调用当真实 AI 验证。
2. **全部要求覆盖**：同一 job 同时准备实质性、资格、技术、评分要求；确认无/正/负偏离，
   保留无卡片、待审、驳回、补材料和未分类条目。验证每项恰好进入行或缺口；无默认满足、
   无跨 job 拼接，负偏离原样可见，全缺口和负偏离但无缺口时退出码分别符合约定。
3. **真实材料边界**：来源 Archive 在建卡/确认后仍恒未确认；新的 Evidence 有人类确认。
   尝试以产品 URL 代替截图、planned 功能当已实现、证书声明当原件、单位常用表述当合同，
   均不能确认成具有不存在的材料；无证据确认失败，实际不满足可如实确认负偏离。
4. **人类与数据库双关口**：通过真实 API/两种 CLI，以 bidder、technical、admin、viewer、
   令牌及 agent 身份执行全套迁移；跨职责、人类决策令牌调用和给令牌签发确认/export 范围
   均失败。用运行数据库角色在 token/agent/worker/缺上下文事务内伪写 confirmed、确认人
   和当前指针同样失败。确认中途失败时没有半张卡片、半组 Evidence 或成功审计。
5. **双单位隔离**：固定单位 A/B，对每张新表的读写、每个新路由及材料预览执行 A 访问 B；
   对象接口统一 404，DB 拒绝越界和缺上下文写入。另测同单位不同任务/job、伪造选择/修订/
   source 组合和跨单位确认人外键，确保不只靠单列 UUID 或服务端 WHERE 隔离。
6. **并发与历史**：两客户端持同版本编辑/确认只能一个成功；确认与资源替换并发不会生成
   有效的过期行。重抽后新 job 全新覆盖、旧 job 可显式查看；资源库修改不漂移旧卡，任务
   选择替换使相关卡片/已有初稿显示失效。重开后 agent 不得继续写旧 confirmed 修订。
7. **原文与作业**：PDF 页码与 DOCX 段落/单元格引用逐字核对；摘要、省略引文、假 Word
   页码和无效引用不进响应行。draft 重复提交、取消、重试、队列失败、旧 attempt 返回、
   落盘前材料变化都无重复或部分发布；历史初稿不被重跑覆盖。
8. **CLI 与可复核工件**：本地 PostgreSQL/RLS 和远程 API 传输跑相同命令，验证 Result 七键、
   schema、退出码、dry-run 零写入/零调用和审计脱敏。保存脱敏的输入标识/材料哈希、
   命令清单、JSON 初稿与缺口、预期断言及重跑步骤到独立验收产物目录，便于复现；
   不写入 `docs/`，不包含令牌、真实敏感正文或报价。

## 待你决定

以下为审批选项，推荐项尚不代表已批准；不开放违反硬规则的选项。

| 决策 | 可选范围 | 建议与理由 |
| --- | --- | --- |
| 确认职责 | bidder/technical 各管本专业；或给 admin 跨类确认权 | **按专业分工，admin 不默认确认**；延续设计中的职责，substantive/scoring 先由人分类 |
| 卡片审核粒度 | 逐卡由对应人确认；或额外引入复核人/会签 | **逐卡单人确认**；整段响应、偏离判断和全部材料一次确认，会签另立契约 |
| LLM 响应起草 | 首版人工文字＋确定性组表；或先扩展本草案纳入受限 LLM 新修订和二次文字确认 | **首版不调用 LLM**；先闭合真实材料和人工关口，后续遵循本页的候选文字约束 |
| 分表方式 | 实质性优先、每要求只进一个主表；或实质性条款还在商务/技术表交叉重复 | **单一主表**；保持逐项完整性，减少重复行不同步；未来模板可再定义交叉展示 |
| 资源替换 | 阻止旧选择卡片进入新初稿并要求重审；或增加专门的人类历史材料复用流程 | **阻止并重审**；不自动继承已关闭选择的确认，历史初稿保留可查 |
| 本切片范围 | 选择一个抽取 job、CLI/API 与结构化初稿；或同时加入多文档、UI、模型及导出 | **一个 job 的完整闭环**；先验收 B07/B08 的关口、分表和缺口，再推进独立切片 |
