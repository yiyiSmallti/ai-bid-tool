---
kind: plan
---

# 契约草案：逐条人工确认与偏离表初稿

状态：**待批准，未实施**。对应[路线图](roadmap.md) B07、B08；本页定义待实施契约，
剩余审批项见[待你决定](#待你决定)。

## 目标与边界

选择一个抽取作业 → 模型起草响应卡片 → 人工审阅文字、材料与条款处置 → 按职责确认 →
生成三张响应表、须遵守条款清单与缺口清单。入口为 API 和本地、远程 CLI，不依赖看板。
响应文字由模型起草、人可修改，不使用固定句式或模板填充；组表原样复制人工确认内容。

硬规则依据 [agent.md](../../agent.md#硬性规则任何情况下都不得违反)，实体和流程依据
[设计文档](../AI%20标书工具设计文档.md#数据模型)。本切片消费 B04 来源及 R02—R06
固定资源；不含网页/白皮书取证、B05 标注、B06 截图/原型、B09 全文语义校验、B10 评分、
B11 导出与模板适配，也不含要求补录、修改抽取结果、多作业/多文档合并、看板或自动记忆。
人工确认不等于系统认证原件真伪，也不宣称自动完成型号匹配或参数单位换算。

### 响应种类与条款处置

两个维度独立保存，不能用响应种类代替人工处置决定：

| 字段 | 含义与消费条件 |
| --- | --- |
| `response_kind=evidence` | 依据材料作出响应；确认至少需要一项真实且有效的 Evidence |
| `response_kind=commitment` | “我方承诺……”类响应，如交货期、付款条件、投标有效期、服务义务；负责人可无材料确认，Evidence 列表必须为空，表行显示“承诺” |
| `disposition=respond` | 需要响应；确认后进入一个主表，否则列缺口 |
| `disposition=comply_only` | 人工认定仅需遵守的程序条款，如投标人须知中的保证金退还、投诉、评标委员会规则；进入须遵守条款清单，不进三表，也不算缺口 |
| `disposition=null` | 尚未人工标记；模型建议不改变此值，无已确认卡片时仍是缺口 |

两种响应都必须选择偏离并说明差异；不能作出的承诺应如实标为 `negative`。
承诺不得声称已有证书、截图、报告或参数证明。条款出现“提供……证书/检测报告/截图/
说明书/证明/复印件”等材料要求时，CardView 显示 `proof_material_required` 警示，
由人核对上下文并决定补材料、改为证据响应或如实记录负偏离；系统不把承诺升级为证明。
词语提示不代替人工判断，材料义务不能仅靠承诺被视为已履行。

## 抽取作业、卡片与材料的绑定

读取作业沿用 [req history / req list --job](../notes/reasoning-levels.md#usage)。
创建、批量处置、模型起草和组表必须显式指定同单位、同任务、已成功的抽取 job；
其真实 `document_id` 固定本次范围，不隐式选择“最新”。按卡片 ID 的写入沿用既有绑定。
每个 `(org_id, task_id, extraction_job_id, requirement_id)` 最多一张卡片，不换绑、
不修改招标原文。列表覆盖该 job 的全部要求，无卡片也返回 `missing_card`；不能按类别、
★ 标记或响应难度排除要求。完整性只针对抽取结果，不代表招标文件没有漏抽。

`EvidenceSource` 仍是[未确认来源档案](../notes/unconfirmed-evidence-sources.md)，
沿用 [`EvidenceSourceArchive`](../../server/app/schemas/evidence_source_contracts.py)。
新 Evidence 表达“该要求的响应使用这份材料作出判断”；确认只写 Evidence/Card，
不改变来源档案的 `unconfirmed_source`、`confirmed_by` 或 `eligible_for_draft_export`。

| 材料输入 | 固定关系与边界 |
| --- | --- |
| 产品 | `task_resources` → `product_revisions` 的具体字段，保留型号、版本、包号；URL 不等于已访问网页或硬件截图 |
| 功能 | `task_features` → `feature_revisions`，保留产品关联与声明状态；`implemented` 仍是声明，`developing/planned` 不得写成已实现 |
| 证书声明 | `task_certificates` → `certificate_revisions`；声明编号、日期不等于原件证明或真实性核验 |
| 证书 PDF 页 | 同任务 `evidence_source_id` → 固定证书选择、修订、原件、页码及 PNG 哈希；保留用户提供材料的来源性质 |
| 单位资料 | `task_org_profiles` → `org_profile_revisions` 的具体字段；常用表述不等于已有业绩合同或附件 |

快照机制沿用[产品](../notes/versioned-resources.md)、[功能](../notes/versioned-features.md)、
[证书](../notes/versioned-certificates.md)、[单位资料](../notes/versioned-profiles.md)笔记。
元数据只能支持其记载的声明；模板和记忆不是证据。证据响应缺少所需材料时保留缺口，
不能由人工点击将声明升级成不存在的证明文件。

Evidence 保存材料种类、固定选择/修订、字段或页码、摘录、性质；修订与哈希由服务端
从父记录解析，不信任客户端副本。字段摘录须逐字出现在引用字段；PDF 摘录即使匹配页文本，
仍须人对照原页后才从 `unreviewed_page` 变为 `human_page_review`，文本匹配不证明原件真伪。
空摘录、任意 URL/磁盘路径或脱离任务的资源均不可成为 Evidence。

## 状态、职责与人工关口

| 当前状态 | 动作与目标状态 | 条件与执行者 |
| --- | --- | --- |
| 无卡片 | 创建 → `draft` | `card:write` 成员/受限令牌，或获授权的起草 worker |
| `draft` | 编辑/重新起草 → 新 `draft`；提交 → `pending_review` | 提交报告缺项，材料不足也可送人处理 |
| `pending_review` | 确认 → `confirmed` | 对应职责的人类登录身份，全部确认前检查通过 |
| `pending_review` | 驳回 → `rejected`；需补材料 → `needs_material` | 同一确认职责，必须有原因 |
| `rejected` / `needs_material` | 编辑/重新起草 → `draft` | 保留历史意见，不继承旧确认 |
| `pending_review` | 撤回 → `draft` | 编辑者记录原因；待审内容不能直接覆盖或重新起草 |
| `confirmed` | 重开 → `draft` | 仅对应职责的人，必须有原因；历史确认保留 |

每次内容或状态变化增加修订。模型只生成 `draft`，材料不足以 `review_hint=needs_material`
提请人处理，不代替人执行同名审核动作。不提供批量确认、默认确认或自动确认路径；
API token、内外部 agent、worker 均不能确认、驳回、作出补材料决定、重开或修改已确认卡片。

新增范围建议为 `card:read`、`card:write`、`card:generate`、`draft:run`、`draft:read`；
确认、职责分类和处置标记复用受保护的 `evidence:confirm`。权限沿用
[`Identity / ROLE_SCOPES`](../../server/app/services/auth.py)的交集规则：

| 身份 | 非决策操作 | 人类决策 |
| --- | --- | --- |
| `admin` | 查看、编辑、提交、起草、组表 | 对待定类别指定职责；默认无跨专业确认或处置权 |
| `bidder` | 查看、编辑、提交、起草、组表 | 商务/资格职责内逐卡确认及处置 |
| `technical` | 查看、编辑、提交、起草、组表 | 技术职责内逐卡确认及处置 |
| `viewer` | 仅查看 | 无 |
| API token / agent / worker | 仅获授的非决策能力，与有效成员权限取交集 | 永远无，即使发起人有确认权限也拒绝 |

所有操作还需 `task:read` 及涉及材料的读取权限：产品/功能 `resource:read`，证书
`certificate:read`，单位资料 `profile:read`，PDF 页同时需 `evidence:source:read`、
`certificate:read`、`certificate:file:read`。旧令牌不自动获得新范围；无权和跨单位对象
统一 404，卡片、模型输入和初稿不得绕过原材料权限。

`review_domain` 决定审核人而非表名：technical 类固定技术职责，qualification 类固定
商务职责；substantive/scoring 无法确定职责时留空，阻止确认及处置，由人类 admin
分类并记录理由，agent 不可分类。本切片按专业单人逐卡确认，不新增会签或角色管理。

### 人工处置与批量标记

`disposition` 只能由对应职责的人设置；模型仅填写 `suggested_disposition`，不能自动
采用建议。confirm 将尚未标记的处置显式记为 `respond` 并审计；已标记 `comply_only`
的卡片须先由人改回 `respond` 才能提交确认。须遵守条款不要求响应文字、偏离或 Evidence，
其处置决定与响应确认分开保存，不能伪填 `confirmed_by`。

支持单次请求标记同一任务、同一抽取 job 的多条要求，每项带 requirement ID、预期修订、
处置及理由。无卡片时预期修订为 null，并创建空的 `draft` 卡片保存人工决定；已有卡片
仅允许在 `draft/rejected/needs_material` 标记，已确认须先重开，待审须先撤回。
建议整批原子提交：任一越权、跨 job 或版本冲突则全部失败；通过时每条要求各追加一条修订
与审计，共用请求关联 ID。批量操作不确认任何响应/Evidence，也不删除历史候选内容。
人标为 `comply_only` 后，普通编辑和起草不能改写其内容或处置；改回 `respond` 需人决定。

### 确认前检查

确认请求携带 `expected_revision`、本次逐项审阅的 `reviewed_evidence_ids`；服务端重读：

1. 当前为 `pending_review`，身份、职责、成员及材料权限有效，处置不是 `comply_only`。
2. 要求属于固定 job，`Source.quote` 逐字存在于所指位置，不得用摘要替代；PDF 用页码，
   Word 用[结构位置](../notes/docx-citations.md)。旧抽取引用若仅规范化匹配而非逐字匹配，
   报 `invalid_citation`，不能静默修改原文。
3. 两类响应均有非空文字、偏离与说明；无偏离说明对应关系，正/负偏离说明具体差异，
   不得只写“满足”。无法兑现的承诺及已知未达的数值、状态、条件如实保留负偏离。
4. `evidence` 至少一项真实材料，审阅 ID 无重复且精确覆盖全部链接，选择仍有效、
   摘录及材料性质与响应相符；`commitment` 的链接和审阅 ID 都为空，不制造占位 Evidence。
5. 对 `proof_material_required` 等警示，人须提交逐项审阅标记及处理理由；承诺不能声称
   证明已存在，缺附件不能写成已附，planned 不能写成已实现。无法自动可靠判断的语义
   由人复核；确认仍不得规避材料义务或弱化负偏离。

任一失败，卡片、Evidence 与成功审计全部不写；确认在同一事务提交，确认人/时间由
认证上下文产生。未变的历史 Evidence 可保留原确认记录，本次响应修订仍需人工确认。
证据响应无有效材料只能待补材料；承诺无材料可确认，两者均可如实确认负偏离。

### 并发、历史与失效

已有卡片写入须带预期修订，模型作业使用提交时捕获的版本；锁顺序为任务 → 卡片 →
关联记录，批量卡片按 ID 排序，与资源替换共用任务锁。冲突返回 HTTP 409、
`revision_conflict`、退出码 2，不写修订或成功审计，不自动合并覆盖。

重抽产生独立要求集合，旧卡片不迁移，即使原文相同也不跨 job 拼接。旧 job 可显式查看
或组表并显示历史警示；新 job 未处理的要求仍为缺口。资源库新增修订不改变任务固定选择；
显式替换选择则使依赖该材料的卡片成为 `stale_material`，包括模型起草实际使用的输入依赖。
已确认卡片须人重开、更新材料并重审，选回旧修订不自动恢复；无材料依赖的承诺及须遵守
处置不因无关替换失效。

`state` 保存历史动作，`eligibility` 在读取/消费时计算；引用失效同样阻止消费。
已有初稿是不可变快照，卡片重开、处置改变或所需材料/引用失效时，读取返回
`validity=stale` 及受影响要求，不能以历史 `completion=complete` 掩盖失效。

## 模型起草

`card generate` 按所选要求创建或更新 `origin=model, actor_kind=worker` 的 draft 修订，
提出响应种类、处置建议、响应文字、偏离、说明和证据引用；不套用响应模板。默认选择该 job
全部要求，可用重复 `--requirement ID` 缩小范围。已确认、待审和人工标为 `comply_only`
的卡片跳过并逐项报告；已确认卡片只有人先重开才能重新起草。worker 落盘再次核对状态与
预期修订，运行期间被确认的卡片也必须跳过，不得触碰其当前指针、旧修订或确认记录。

新增结构化起草能力接入现有 LLMProvider/adapter，业务层不直接调用厂商。复用
[平台默认模型与官方推理档位](../notes/reasoning-levels.md#how-it-works)、
[批次、有限重试与逐次用量](../notes/llm-providers.md#how-it-works)。`--reasoning` 取目录
官方值或默认值；未知值报 `unsupported_reasoning`，无档位模型按既有规则警示，不自造
档位。单位自带模型、自选平台模型仍属于 [provider-config.md](provider-config.md)，
本切片不以其实施为前置条件，也不重复提出已采用的预付费选择。

### 外发内容、遮挡与引用校验

单位材料是机密。输入清单仅从任务当前固定选择的可引用字段及已选 EvidenceSource 页构建，
随提交固定。每批发给厂商的业务内容严格限定为所选要求的原文及位置、清单内资源字段值、
所选证书页的页文本；附局部引用 ID、字段/页定位，
以及起草指令和输出 schema。不发送未选资源、整份证书 PDF、页图片、其他页面、存储路径、
密钥、其他任务或其他单位的数据；旧卡片正文不默认加入提示词。

遵循[设计文档的可选自动遮挡](../AI%20标书工具设计文档.md#安全与合规)：新增任务开关
`model_redaction_enabled=true`，外发前遮挡**报价、联系人、身份证号、银行账号**，
作用于上述全部文本。建议仅本单位人类 admin 修改开关并审计；不是每次命令临时覆盖。
输入清单及 dry-run 列出资源/字段/页标识、文本哈希、遮挡开关/规则版本和命中数量，
不返回敏感值。关闭时明确显示将按清单发送未遮挡文本；usage、审计、错误和常规日志均
不含发送正文或模型原始返回，不得靠日志脱敏补救已发生的外传。

建议仅使用本地可提取且绑定原件哈希/页码的证书页文本；无文本页报告
`page_text_unavailable`，不静默启用云 OCR 或发送图片。页文本和发送清单是受本单位权限
保护的输入快照，不写入日志；自动遮挡只改变外发副本，不修改原材料或招标引文。

模型引用使用请求中的局部 `ref`；服务端映射到真实 selection/revision/field 或 PDF
source/revision/page，不接受模型自行声明的新来源。每条 quote 必须逐字出现在**该批实际
发送的对应字段/页文本和固定原始文本两者中**，沿用要求引用的服务端定位核验原则。
遮挡占位符、未发送片段、拼接引文、错页、未知 ref、过期或越界选择均拒绝；不反向补回
遮挡值、不模糊匹配修复。无效引用丢弃并按要求/ref/原因代码报告，原始无效引文不保存为
Evidence，也不写入审计或用量。

证据响应过滤后无有效引用时，仍保存未确认 draft，派生 `review_hint=needs_material`，
不得伪造材料或自动改成承诺。承诺必须没有 Evidence；模型多余引用丢弃并警示。
合法引用也只是未确认候选，PDF 仍需人工对照原页。模型不得虚构参数、证书、业绩或实现
状态，也不得弱化负偏离；已记录 negative 而输出改为 none/positive 的候选拒绝，其他
事实和措辞由人逐项审阅，引用校验不能充当语义真实性保证。

## 偏离表初稿

`draft` 只组表，不另行改写确认文字。每个要求恰好归入**一条响应行、一条须遵守记录、
一条缺口**中的一个；模型建议不能使要求脱离缺口。有效的人工 `comply_only` 决定及原文
引用进入单独的“须遵守条款清单”，保留全文、位置、卡片修订和决定人/时间，不需响应确认。
其他要求仅在当前卡片已确认且有效时进表，否则为缺口。

| 表标识 | 标题 | 归入规则 |
| --- | --- | --- |
| `substantive` | 实质性响应一览表 | `starred=true` 或原始类别 substantive 优先 |
| `commercial` | 商务响应偏离表 | 非实质性且商务职责，包括资格条款 |
| `technical` | 技术响应偏离表 | 非实质性且技术职责 |

每要求只进一个主表，保留原始类别与 ★ 标记；scoring 按人工确定职责归表，不计算得分。
三表、须遵守清单及缺口均按原文顺序稳定排列（chunk、页/块位置、要求 ID），不得筛掉负偏离。
行内包含原样 `Source.quote`、文件 ID/名称、位置、确认后的 response_kind/文字/偏离/说明，
以及可回溯的 Evidence；commitment 标“承诺”且 `evidence=[]`，evidence 标“材料响应”。
PDF 位置为“文件名 · 第 N 页”，Word 使用真实 `Location.label` 且 `page=null`，不得猜页码、
省略引文或写“同上”。负偏离始终显示“负偏离”和具体差异，不设隐藏选项。

Evidence 引用保留 ID、选择/修订、性质、字段摘录或 PDF 页码/哈希、确认人/时间；不含
存储路径或长期下载链接，原件继续走受权短期预览。缺口只含原文、位置、卡片修订（若有）
及原因，不含候选响应或默认偏离。未标记且未确认、缺材料、驳回、未分类、失效均可产生缺口；
comply_only 若引用不可核验也列缺口，不能用标记隐藏损坏引用。抽取被拒条目及解析遗漏
另作作业警示，不能冒充已保存 Requirement。

初稿始终 `status=draft`；未来 export 仍须重新检查全部关口，不凭旧快照放行。

## 拟新增数据模型与数据库关口

业务新表统一 `org_id NOT NULL`、`UNIQUE(org_id,id)`、ENABLE/FORCE RLS；运行角色不拥有
业务表、不使用超级用户或 BYPASSRLS，缺单位上下文拒绝。以下表及约束随未来迁移同次交付。

| 表/扩展 | 字段与约束 |
| --- | --- |
| Task 扩展 | `model_redaction_enabled` 默认 true、设置修订与操作者；变更审计，作业固定当时值 |
| `response_cards` | task/extraction_job/requirement 绑定唯一；`current_revision_id, revision`，只更新当前指针和递增版本，不换绑/删除 |
| `response_card_revisions` | card/revision 唯一；`state, review_domain, disposition, disposition_by/at, response_kind, suggested_disposition, response_text, deviation, deviation_note, review_hint, reason, reviewed_warning_codes, confirmed_by/at, origin, model_job_id, actor_user_id/token_id/kind, created_at`；内容及状态只追加 |
| `evidence` | card、带类型的任务选择/修订外键、可选 source、field/quote、material_kind/quote_check、服务端哈希、确认人/时间；内容不可变，新材料新 ID，首次确认受关口保护 |
| `card_evidence_links` | revision/evidence 组合唯一，同卡片同任务；只追加，状态修订可复用未变 Evidence |
| `card_generation_runs` | task/extraction_job/generation_job、发起身份、固定输入清单/哈希、目标卡片版本、模型目录修订/档位、遮挡设置/规则版本、结果引用；正文快照加密且受 RLS，不进入日志 |
| `draft_runs` | task/extraction_job/generation_job、input_hash、发起身份、固定输入清单、完成摘要；同输入复用，不覆盖旧初稿 |
| `response_items` | draft/requirement 唯一；card_revision（gap 可空）、`kind=row/comply_only/gap`、table、原文位置快照、对应响应/处置/缺口字段；三种字段互斥，只追加 |

响应行的 Evidence 集合经固定修订的不可变链接读取，不另存会分叉的 JSON ID 列表。
起草输入清单记录实际使用的固定材料依赖；组表清单记录每项当时修订或无卡片状态，不能
只存哈希。业务引用均用含 org_id 的复合外键，并约束同 task/job/card 与父资源：卡片到
Requirement/Document，Evidence 到具体选择/资源修订，PDF 再到证书原件/source；禁止
无外键的通用 resource_id。人引用 `(memberships.org_id,user_id)`，令牌引用单位复合键。

服务与数据库两层共同保证：

1. 保留令牌禁止 `evidence:confirm/export` 的 SQL 与签发约束；认证层设置可信
   actor_kind/user/token/org，请求体不能伪造。agent/worker 不能继承人类身份。
2. 确认、处置变更、分类、重开等人类决策须 `actor_kind=session`、无 token、有效成员、
   对应职责和合法状态/版本；token/agent/worker 或缺上下文 SQL 伪填确认人也失败。
   新建 draft 的 disposition 必须为空，除非经人工处置入口；worker 只可原样继承已有
   处置及决定人/时间，不能伪填这些字段。
3. confirmed 必有确认人/时间、respond、响应种类/文字/偏离/说明；evidence 类至少一条
   链接且全部已确认，commitment 类恰为零条。非 confirmed 新修订不能伪填确认人。
   comply_only 必有人工决定人/时间，不能由 suggested_disposition 推导或改成 confirmed。
4. 延迟约束检查跨表完整性，禁止改删历史和非法改指针。组表服务与落盘 DB 按三类输出
   分别检查消费条件；全部 Requirement 集合不重不漏。数据库结构校验不代替人的内容核对。

审计复用 audit_logs，记录卡片动作、逐项 disposition/classify、起草提交/结果、遮挡设置、
组表提交/完成；仅含对象/修订/作业 ID、旧新状态/处置、原因代码、身份、时间及关联 ID。
响应、条款、材料、模型收发正文和处理理由不复制进日志，理由留在受权修订内。
业务与成功审计同事务；失败回滚，权限拒绝和冲突不写成功审计。

## Pydantic 契约草案

以下只在本文供审批，实施后进入 schemas/API/`bid schema`。共用
[`Contract、Source、Cost、Result`](../../server/app/schemas/contracts.py)，沿用 extra=forbid、
Word page=null、位置互斥与时区规则；不冻结现有契约版本号。

```python
CardState = Literal["draft", "pending_review", "confirmed", "rejected", "needs_material"]
ReviewDomain = Literal["commercial", "technical"]
ResponseKind = Literal["evidence", "commitment"]
Disposition = Literal["respond", "comply_only"]
Deviation = Literal["none", "positive", "negative"]
TableKind = Literal["substantive", "commercial", "technical"]
GapReason = Literal["missing_card", "unconfirmed", "rejected", "needs_material",
                    "unclassified", "stale_material", "invalid_citation"]

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
    response_kind: ResponseKind | None = None
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
    reviewed_warning_codes: list[str] = Field(default_factory=list)
    reason: str | None = Field(default=None, min_length=1, max_length=10000)

class CardClassify(Contract):
    expected_revision: int = Field(ge=1)
    review_domain: ReviewDomain
    reason: str = Field(min_length=1, max_length=10000)

class DispositionItem(Contract):
    requirement_id: UUID
    expected_revision: int | None = Field(default=None, ge=1)
    disposition: Disposition
    reason: str = Field(min_length=1, max_length=10000)

class DispositionBatch(Contract):
    extraction_job_id: UUID
    items: list[DispositionItem] = Field(min_length=1, max_length=1000)

class TaskRedactionSet(Contract):
    expected_revision: int = Field(ge=1)
    model_redaction_enabled: bool

class ModelEvidenceRef(Contract):
    ref: str = Field(min_length=1)
    quote: str = Field(min_length=1, max_length=20000)

class ModelCardProposal(Contract):
    requirement_id: UUID
    response_kind: ResponseKind
    suggested_disposition: Disposition
    response_text: str = Field(min_length=1, max_length=20000)
    deviation: Deviation
    deviation_note: str = Field(min_length=1, max_length=10000)
    evidence: list[ModelEvidenceRef] = Field(default_factory=list, max_length=100)

class CardGenerateRequest(Contract):
    extraction_job_id: UUID
    requirement_ids: list[UUID] | None = None
    reasoning: str | None = None
    dry_run: bool = False

class CardGeneratePreview(Contract):
    dry_run: Literal[True] = True
    task_id: UUID
    extraction_job_id: UUID
    selected_requirements: list[UUID]
    skipped: dict[UUID, str]
    input_hash: str
    platform_model_id: str
    model_revision: int
    reasoning: str | None
    model_redaction_enabled: bool
    input_refs: list[str]
    redacted_counts: dict[str, int]
    estimated_cost: Cost
    estimated_charge: Decimal | None
    billing_currency: str
    cost_basis: Literal["known", "unknown"]
    estimated_duration_ms: int | None = Field(default=None, ge=0)

class CardGenerateResult(Contract):
    generation_job_id: UUID
    completion: Literal["complete", "partial"]
    created_revision_ids: list[UUID]
    skipped: dict[UUID, str]
    rejected_references: dict[UUID, list[str]]
    needs_material: list[UUID]
    usage_record_ids: list[UUID]
    charge: Decimal | None
    billing_currency: str

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
    disposition: Disposition | None
    disposition_by: UUID | None
    disposition_at: datetime | None
    suggested_disposition: Disposition | None
    origin: Literal["human", "agent", "model"]
    actor_kind: Literal["session", "token", "agent", "worker"]
    model_job_id: UUID | None
    review_hint: Literal["needs_material"] | None
    source: Source
    content: CardContent
    evidence: list[EvidenceView]
    confirmed_by: UUID | None
    confirmed_at: datetime | None
    warning_codes: list[str]
    eligibility: Literal["eligible", "comply_only", "unconfirmed", "unclassified",
                         "stale_material", "invalid_citation"]

class DraftRequest(Contract):
    extraction_job_id: UUID
    dry_run: bool = False
    retry: bool = False

class DraftPreview(Contract):
    dry_run: Literal[True] = True
    task_id: UUID
    extraction_job_id: UUID
    input_hash: str
    response_requirements: int = Field(ge=0)
    comply_only_requirements: int = Field(ge=0)
    gap_requirements: int = Field(ge=0)
    table_rows: dict[TableKind, int]
    gap_reasons: dict[GapReason, int]
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
    response_kind: ResponseKind
    response_text: str
    deviation: Deviation
    deviation_note: str
    evidence: list[EvidenceView] = Field(default_factory=list)

class ComplyOnlyEntry(Contract):
    requirement_id: UUID
    card_id: UUID
    card_revision_id: UUID
    tender_clause: Source
    location_label: str
    disposition_by: UUID
    disposition_at: datetime

class DraftGap(Contract):
    requirement_id: UUID
    card_id: UUID | None
    card_revision_id: UUID | None
    tender_clause: Source
    location_label: str
    reasons: list[GapReason] = Field(min_length=1)

class DraftView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    generation_job_id: UUID
    status: Literal["draft"] = "draft"
    completion: Literal["complete", "partial"]
    validity: Literal["current", "stale"]
    input_hash: str
    tables: dict[TableKind, list[ResponseRow]]
    comply_only: list[ComplyOnlyEntry]
    gaps: list[DraftGap]
    invalidated_requirements: list[UUID]
```

验证器与 DB 约束须覆盖字段间关系，不能只按类型实施：

- 字符串去空白后非空；字段路径只接受资源 schema 白名单，kind 决定选择表；哈希为
  SHA-256 十六进制。PDF source_archive 仍恒未确认，不与 Evidence 确认混淆。
- confirm 要求完整响应/偏离/说明；evidence 类审阅 ID 非空、无重复且覆盖全部链接，
  commitment 类为空，ResponseRow 同样按种类校验数量。其他动作不得携带审阅集合。
  withdraw/reject/needs_material/reopen/classify 必须有 reason；警示审阅亦须理由。
- disposition 不在 CardContent 或 ModelCardProposal 可写字段中；批量无重复 requirement，
  全属同 job、逐项查版本及职责。建议值不能映射成人工决定，模型 origin/actor 由服务端写。
- requirement_ids 省略表示全部，显式列表须非空、无重复且全部属于该 job。
  ModelCardProposal 只允许请求选中的要求和 ref；拒绝项返回 ref 与原因代码，不返回原文。
  review_hint 由有效材料派生，不允许模型直接设置审核 state 或 confirmed_by。
  skipped 使用固定原因代码区分受保护跳过、版本冲突和候选拒绝；后两者计入 partial。
- 三张 table 键始终齐全，行 table 与键一致；row/comply_only/gap 集合互斥且并集等于
  全部要求。partial 当且仅当 gaps 非空；预检三类计数和等于要求总数，缺口原因可多计。
- 输入不得写 org、确认人/时间、原始类别、来源修订、身份或 state 等服务端字段；
  损坏引用的 gap 保留历史 Source 并标错，不伪造可用位置。

## CLI、API 与 Result

所有命令支持 `--json`，缺参立即报错、无交互提问。`--input FILE` 使用上述模型；
`--job` 专指抽取 job，起草与组表作业使用回执的 generation_job_id 查询。

| CLI | API | 输入 / 输出 |
| --- | --- | --- |
| `bid card list --task T --job J` | `GET /tasks/{T}/cards?job={J}` | 全要求槽位，含 CardView 或 missing_card |
| `bid card show --id C [--history]` | `GET /cards/{C}?history=...` | 当前或历史修订，显示建议、处置、警示和失效原因 |
| `bid card create --task T --input FILE` | `POST /tasks/{T}/cards` | CardCreate → CardView，重复绑定 409 |
| `bid card update --id C --input FILE` | `PUT /cards/{C}` | CardUpdate → CardView |
| `bid card classify --id C --input FILE` | `POST /cards/{C}/classification` | CardClassify → CardView，仅人类 admin 给待分类 draft 指定职责 |
| `bid card disposition --task T --input FILE` | `POST /tasks/{T}/cards/dispositions` | DispositionBatch → 逐项 CardView；支持一次标记数百条，建议整批事务 |
| `bid card generate --task T --job J [--requirement ID ...] [--reasoning LEVEL] [--dry-run] [--wait]` | `POST /tasks/{T}/cards/generations` | CardGenerateRequest → 作业回执 / CardGeneratePreview；完成为 CardGenerateResult |
| `bid card submit/withdraw/confirm/reject/needs-material/reopen --id C --expected-revision N ...` | `POST /cards/{C}/actions` | CardAction；evidence 确认传重复 `--evidence ID`，commitment 省略；警示审阅用 `--reviewed-warning CODE`，理由用 `--reason` |
| `bid task redaction set --task T --input FILE` | `PUT /tasks/{T}/model-redaction` | TaskRedactionSet → 设置及修订；建议仅人类 admin |
| `bid draft --task T --job J [--dry-run] [--retry] [--wait]` | `POST /tasks/{T}/drafts` | DraftRequest → 作业回执 / DraftPreview |
| `bid draft show --id D` | `GET /drafts/{D}` | DraftView，重算 validity，不改历史内容 |
| `bid draft list --task T --job J` | `GET /tasks/{T}/drafts?job={J}` | 完成初稿摘要，按抽取 job 过滤 |

人类决策在路由、服务和数据库均检查身份与权限，不仅隐藏 CLI 命令。无新增 export；
未来注册 bid schema 并保持现有命令兼容，不兼容变化按 [CLI 契约](../../agent.md#硬性规则任何情况下都不得违反)升版本。

Result 顶层严格保留 `ok、command、data、items、warnings、cost、duration_ms` 七键。
详情/写入 data 为回执，items=[]；列表 items 为各项，data 为范围；错误放 data.error，
不回显输入。起草拒绝引用、跳过及补材料项放 data 内的结果字段，警示仅含标识和原因代码。

| 退出码 | 本切片语义 |
| --- | --- |
| 0 | 查询/动作/dry-run 成功、作业受理或完整结果；受理不等于作业完成 |
| 2 | 缺参、非法 job/空要求集、版本冲突、非法迁移、确认缺项、未知 reasoning |
| 3 | 可重试网络/队列/存储故障，或固定输入变化；重新读取输入后重试 |
| 4 | 身份/角色/范围失败、令牌执行人工决策、无权对象 404、材料损坏、余额不足、不可重试厂商失败 |
| 5 | 组表有缺口，或起草有拒绝引用/需补材料/未完成项；返回全部有效结果和逐项原因 |

错误和部分成功 ok=false；受理 ok=true，--wait 返回最终码。job status/wait 采用同样
业务完成度；基础 Job 可以 succeeded 而 completion=partial。起草跳过 confirmed、
comply_only 或 pending_review 是受保护的跳过并警示，本身不算失败；并发版本冲突则为
未完成项。有效负偏离须显式警示但不使组表 partial；全缺口为 5，全须遵守且有效可为 0。

## 作业、dry-run 与费用

起草 `jobs.kind=card_generate` 与组表 `jobs.kind=draft` 均复用
[后台作业机制](../notes/background-jobs.md)的取消、有限重试和 attempt 隔离；document_id
均来自固定抽取 job 的招标文件，不用证书冒充或放宽为空。提交固定一致性输入；worker
恢复单位上下文、复核发起人成员和读取/运行权限，外发前重验任务遮挡设置修订，变化则停止
并要求重新提交；落盘再验版本、处置与材料有效性。
旧 attempt 或取消作业不能落盘；已经发生的厂商调用仍记录用量和费用。

起草固定要求/材料输入及哈希、卡片版本、平台模型及目录修订、官方 reasoning、提示词/
schema/adapter 版本、遮挡开关/规则版本；全部进入单位内缓存键，禁止跨单位复用。
提交后不能改用新默认模型配旧缓存键；固定配置不可用则明确失败。同一提交重试复用原作业，
相同输入命中缓存无新调用、不重复收费；对新的可编辑修订重新起草产生新修订，不覆盖旧版。
落盘冲突按要求跳过并报告，合法的其他候选可保存；调用失败前没有可验证结果则不造卡片。

组表固定全部要求、卡片修订/处置、材料状态和规则版本；相同输入复用初稿。完成事务
再次检查全部消费条件，变化则 `draft_input_changed`、不发布半份初稿。起草和组表均不
把早先 dry-run 或提交检查当作最终授权。

两个 dry-run 都执行相同权限和输入检查，零写入、零厂商调用、不扣费；起草返回目标/
跳过、模型/档位、外发清单摘要、遮挡及成本，组表返回三类计数、各表行数、缺口与负偏离。
起草有可靠价格与 token 估计才给金额并标 estimated；未知成本用 null、cost_basis=unknown
及原因，不伪报零。estimated_charge/billing_currency 区分平台扣款与 Cost.usd 的服务商
成本；本次 dry-run 实际 cost 为零，预估在 data 内，耗时不可估时为 null。

起草复用 [UsageRecord 与预付扣费](../notes/prepaid-billing.md#how-it-works)：平台计费
提交及调用前执行 require_funds，余额不足以 insufficient_balance 阻止生成；dry-run
报告相同拦截但不调用。沿用现有余额检查，不声称已实现预算预占或实际费用硬上限。
每次实际调用，包括重试、截断、拒绝及后续作业失败，按已有逐次记账机制记录服务商、
实际模型、档位、耗时、tokens、成本与平台售价 charge；UsageRecord 与余额扣减同事务，
幂等落账，未知服务商成本不伪造为零。usage 和审计不保存发送正文。
组表不调用模型/OCR，因此实际模型费用为零，不制造空 UsageRecord；起草费用不能被
后续零费用组表掩盖。服务商错误及重试边界沿用 LLM 接入层，不以静默降级掩盖失败。

## 批准后的实施顺序与端到端验收

契约/权限 → 数据与关口 → 卡片及批量处置 CLI/API → 起草 Provider/遮挡/计费 → 组表
worker → 端到端验收。以下均为未来标准，本文不表示已实现或已运行功能测试。

1. **承诺与材料**：交货期、付款条件、有效期、服务义务的 commitment 可由对应人无
   Evidence 确认，行标“承诺”且 evidence=[]；同输入改 evidence 类必须失败。不能兑现
   的承诺保留 negative 和差异。提供证书/报告/截图等条款显示警示并留下人的处理记录，
   不能确认成已有不存在的材料；来源 Archive 始终未确认。
2. **处置与全集覆盖**：人批量标记数百条同 job 程序条款，逐项修订/审计；有效 comply_only
   不进三表、不算缺口，清单逐字保留条款和位置。未标记未确认及仅有模型建议者仍为缺口。
   验证三类集合恰好覆盖全部要求，无跨 job 拼接；负偏离始终可见，全缺口/全须遵守码正确。
3. **模型起草**：经实际 API 与 worker 输入固定字段/页文本，生成 model/worker 的新 draft；
   伪 ref、错修订/页、未发送引文、编造或拼接 quote 均丢弃报告、不保存 Evidence。全部
   引用失效时提示 needs_material，不自动改承诺；无虚构参数、证书、业绩或实现状态，
   已知负偏离不得被改为满足。重跑追加修订，已确认卡片在提交和落盘两个时点均保持不变。
4. **隐私与费用**：在 Provider 边界核验实际发送载荷仅含清单内数据，默认遮挡四类敏感值，
   切换设置改变缓存输入，其他单位/未选数据绝不外发；usage、审计和错误无正文。验证官方
   reasoning、批次/重试、逐次 UsageRecord、平台扣款、余额不足零调用、缓存不重复收费，
   成本未知为 null，dry-run 零写入/调用。CI 使用明确标识的合成材料与 Provider fake；
   获准的真实材料单独跑真实服务链，不把假调用当模型效果证明。
5. **人类及 DB 关口**：bidder/technical 各自单人确认，admin 默认不能跨专业；token、agent、
   worker 不能确认生成文字、标记 disposition 或重开。API、本地/远程 CLI 和运行 DB 角色
   伪写 confirmed、人工处置、确认人/指针均失败；事务失败无半张卡片/半组证据/成功审计。
6. **隔离、并发与历史**：单位 A/B 检查各表和路由、模型输入、预览、用量，越界统一 404；
   另验同单位不同任务/job/source 组合。并发编辑/确认仅一方成功，批量冲突按所选原子策略
   回滚；材料替换使依赖卡片及旧初稿失效，必须重审，旧修订不能被 agent 或 worker 覆盖。
7. **原文、作业与工件**：PDF/DOCX 引用可逐字复核，无猜测页码或摘要替代；取消、旧 attempt、
   重试和落盘前变化无重复修订/扣费或半份初稿。两种 CLI 的 schema、Result 七键、退出码
   一致；未来验收保存脱敏输入 ID/哈希、命令、JSON 三类结果、断言和重跑步骤至独立产物
   目录，不在 docs/ 写证据，也不包含令牌或真实敏感正文。

## 待你决定

仅以下细节仍待选择；上文相应条款按建议描述，不影响已确定的三项产品决定。

| 剩余问题 | 建议与备选 |
| --- | --- |
| 批量处置遇到单项冲突如何提交 | **整批原子提交**，便于核对一次人工决定；备选逐项成功并返回部分失败，但需额外重试语义 |
| 谁能关闭任务的外发遮挡 | **仅本单位人类 admin**，默认开启且变更审计；备选允许任务所属职责的人修改，仍不允许 token/agent/worker 修改 |
| 无可提取文本的证书页如何参与模型起草 | **首版只用本地可提取页文本**，扫描页提示不可用于模型引用、仍可由人对照原件；备选将本地 OCR 纳入本切片，需补页文本溯源与误识别处理契约 |
