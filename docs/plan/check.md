---
kind: plan
---

# 标书初稿校验契约

状态：**已批准。阶段一 rules 已实施；阶段二 combined 未实施。** 对应[路线图](roadmap.md) B09。

[Pydantic 契约](../../server/app/schemas/check_contracts.py)是 API、CLI 和 schema 的活动定义。
阶段一实现确定性规则、持久报告与人工误报决定；本文明确标为阶段二的 Provider、语义检查、
计费和评测内容尚未实施。与 B10 共用的输入、引用、预检和作业回执类型仍由该模块定义，
[score 契约草案](score.md)导入使用。

## 目标与边界

[设计文档](../AI%20标书工具设计文档.md#处理流程与-cli-清单)要求对照招标要求发现废标、
扣分风险，并让人处理误报。首条链路为：选择一个任务的明确 `DraftRun` → 固定输入与
零费用预检 → 确定性规则 → 有引用的风险报告 → 责任人带理由忽略或重新打开。
报告是建议，不能作出实际废标决定，也不自动修改卡片、Evidence、初稿或导出许可。
阶段二在相同边界内增加可选语义检查，不改变这条人工关口。

输入选择 **current 初稿快照**：已确认 `ResponseItem` 正文和偏离说明、已确认且仍有效的
材料摘录，以及 `comply_only`/`gap` 的状态元数据。一个初稿固定一个成功抽取作业；不自动选
最新作业、不合并多轮抽取。允许仍为 current 的 partial 初稿，完整列出其缺口；不发送未确认
候选正文或未确认材料。`comply_only` 表示人工决定遵守，不证明材料已提供或可得评分。

选择依据是 [drafts.py](../../server/app/services/drafts.py) 的 `show_draft`、
`load_draft_reads`、`draft_view` 与 [response_cards.py](../../server/app/services/response_cards.py)
的 `extraction_scope`、`access`、`resolve_material`、`generation_materials_stale`。
它们已有固定要求、逐字确认响应、材料失效和完整分区的基础；新校验必须复核这些门槛。

放行 DOCX 更接近交付文件，但 [exports.py](../../server/app/services/exports.py) 的
`build_manifest`、`confidential_gate`、`release`、`download_gate` 将其绑定到人类导出权限、
模板、原型决定和真实保密值；`export_renderer._fill_confidential` 在渲染时填值。
直接让有 `check:run` 的 agent 读取它会扩大现有导出及保密边界。因此首版不接受 `export_id`、
文件路径、任意上传标书或 URL，也不调用导出服务代取文件。

这一范围不等于整份交付标书检查：不检查 DOCX 的填值、模板排版、页眉页脚、签章、附件图像
是否清晰、正文之外的完整方案，也不做 OCR、视觉调用、取证、自动修复、记忆写入或风险看板。
报告固定 `scope=confirmed_draft`，限制说明随每次结果返回。覆盖只相对于保存的要求，不声称
没有漏抽。[docx-citations.md](../notes/docx-citations.md)所列解析遗漏继续显式呈现。

## 现有基础与设计差异

| 依据 | 已复用基础及阶段二缺口 |
| --- | --- |
| [contracts.py](../../server/app/schemas/contracts.py) 的 `Category`、`Source`、`Result`、`ProviderUsage` | 已有 `scoring` 类别与 PDF/Word 引用；`condition` 仍为自由字典，不能当已批准的数值规则或评分量表 |
| [response_card_contracts.py](../../server/app/schemas/response_card_contracts.py) 的 `DraftView`、`ResponseRow`、`ReviewDomain` | 已有确认响应/仅遵守/缺口分区；不是任意标书全文输入 |
| [ADR 0005](../adr/0005-human-confirmed-responses.md) | 模型提议与人工决定分离；新报告不继承或产生证据确认权 |
| [providers/base.py](../../server/app/providers/base.py) 的 `LLMProvider` | 阶段一不调用模型；阶段二才新增独立 check Protocol，不把 `extract`/`draft` 冒充 check |
| [providers/llm.py](../../server/app/providers/llm.py) 的 `resolve_llm`、`with_reasoning` | 阶段二沿用单位配置优先、平台默认其次的固定配置修订，不另建服务商选择入口 |
| [providers/structured.py](../../server/app/providers/structured.py) 的 `strict_schema`、`json_request`、`json_call` | 阶段二复用结构化请求和已计费 HTTP 边界 |
| [services/jobs.py](../../server/app/services/jobs.py) 的 `status`、`cancel` | 已加入 check 结果过滤及权限校验，继续隐藏 submission/encrypted_input |
| [models/entities.py](../../server/app/models/entities.py) 的 `Job.job_document_binding` | 普通任务作业必须有真实 task/document，provider_test 才是空绑定例外；旧 annotation 草案的“总是 NOT NULL”表述不适用于这一例外 |
| [prepaid-billing.md](../notes/prepaid-billing.md) | 已有逐调用预付准入；设计中的任务累计预算/月度配额尚不是通用执行门槛 |

## 接口

下列路径均已用于阶段一。请求不得提供 `org_id`、确认人、actor、任意模型端点或 redaction 开关；
单位及身份由现有 [api/main.py](../../server/app/api/main.py) `context` 解析。HTTP 与 CLI
使用现有 `Result` 七键包装；下表列 `data` 类型，列表/报告中的风险放 `items`。

| HTTP | CLI（均支持 `--json`） | 请求 → data / items |
| --- | --- | --- |
| `POST /tasks/{task_id}/checks` | `bid check run --task UUID --draft UUID --as-of YYYY-MM-DD [--mode rules\|combined] [--reasoning LEVEL] [--dry-run] [--expected-input-hash HASH] [--max-charge AMOUNT] [--retry] [--wait] --json` | `CheckRequest`；dry-run → `CheckPreview` / []；提交 → `AssessmentJobAccepted` / []；wait 成功 → `CheckJobResult` / [] |
| `GET /tasks/{task_id}/checks?cursor=…&limit=…` | `bid check list --task UUID [--cursor CURSOR] [--limit N] --json` | `AssessmentListData` / `CheckRunView[]` |
| `GET /checks/{report_id}` | `bid check show --id UUID --json` | `CheckReportData`（含 coverage、证书日期检查）/ `FindingView[]` |
| `POST /checks/{report_id}/findings/{finding_id}/decisions` | `bid check decide --report UUID --finding UUID --action dismiss\|reopen --expected-revision N --expected-input-hash HASH --reason TEXT --json` | `FindingDecisionRequest` → `FindingDecisionData` / [] |
| `GET /checks/{report_id}/findings/{finding_id}/decisions` | `bid check history --report UUID --finding UUID [--cursor CURSOR] [--limit N] --json` | `AssessmentListData` / `FindingDecisionView[]` |

列表 limit 默认 50、范围 1–200；cursor 为服务端绑定 org、task、排序的游标。报告绑定首版
最多 2,000 要求，每要求最多 20 条风险、每风险最多 20 引用；超限整批明确失败，不静默截断。
`--wait` 使用既有 `bid job status/wait`；取消用 `bid job cancel`。本地模式仍经过本机
PostgreSQL/RLS、相同身份和服务，不能用直接文件读取绕过授权。参数缺失直接失败，无交互提示。
`total` 为当前授权查询条件下分页前的条目数；history 的游标还绑定 finding。所有路径 task
必须等于 draft/report 的 task，否则 404。公开视图中的 org_id 只读，不能作为 POST 参数。

`Result` 只含 `ok command data items warnings cost duration_ms`；模型只定义 data/items
载荷，不重定义包装。command 分别为 `check run/list/show/decide/history`。rules 的实际
cost 为 `{llm_tokens:0,ocr_pages:0,usd:0}`；dry-run 的包装 cost 也为零，估算位于
`data.estimated_cost`。执行耗时为真实值，预计耗时未知用 null。金额按既有 Decimal 的 JSON
字符串表示，平台扣款与供应商 USD 成本分开。已存在的契约版本见 `contracts.CONTRACT_VERSION`；
`cli/bid_cli/schema.py` 已增加对应条目，既有 JSON 不变，不沿用 annotation 中旧的版本数字。
rules 预检也保留同一结构：provider/config/model/reasoning 相关字段均为 null，预计
charge=0、cost_basis=known、reason 为 `no_model_calls`，币种仍取平台配置；redaction 字段
仅描述当前设置。预检 exit 0 只表示只读分析成功，`admission_blocker != null` 明确表示不能
按当前条件提交，实际提交必须重新检查并拒绝，不把它当成准入授权。默认模式为 `rules`；
阶段一接收到 `combined` 明确返回 `check_mode_unavailable`/exit 2，不静默降级。

| 退出码 | 结果语义 |
| --- | --- |
| 0 | 预检完成（仍须查看 admission_blocker）、作业已接收、读取/人工决定成功，或全部所选规则完成；有风险不等于命令失败 |
| 2 | 缺参、非法模式/日期/游标、超限、stale draft、预检 hash/人工版本冲突、规则模式携带模型选项 |
| 3 | 无可发布报告的暂时网络/队列失败、超时、限流；携带可重试代码，不显示成功 |
| 4 | 身份/权限失败、跨单位或不存在资源、引用/存储完整性硬失败、拒答、余额/价格/计费关口失败且无可发布报告 |
| 5 | 已保存报告但有无法评估项、引用拒绝或后续批次/预算中止；`ok=false`、`completion=partial`，逐项给原因 |

2/3/4 的 `data` 沿用现有错误 `code`/`message` 格式；已创建作业时附 `job_id`，不回显原始
供应商文本。`show` 读取 partial 报告也为 5；stale 历史可读并告警，但不能继续人工处置。
404 统一覆盖跨单位、无权资源和不存在对象，既有 401/403 身份/功能授权语义保留。

## 输入清单、规则和引用

服务端固定 draft/extraction/document、所有 Requirement 的 quote/location hash、ResponseItem
及卡片修订、Evidence/资源选择/修订、证书选择与日期、显式 `assessment_date`、角色职责、
解析/抽取警示、redaction 设置修订/规则、保密字段和值行 ID，以及规则/提示词/schema/adapter
版本。阶段二再固定模型目录修订、推理档位和价格。公开预检只有这些 ID/hash、计数与限额；原文快照按
`card_generation.snapshot` 的加密方式保存，不在审计、UsageRecord 或 job status 展示。
任务截止时间不暗中转换为证书检查日，调用方必须明确 `--as-of`。
读取时须验证该 draft 有且仅有一个 ResponseItem 对应抽取作业中的每条要求，并验证
ResponseItem.draft_id/requirement_id、DraftRun.task_id/extraction_job_id 全部匹配；gap
也必须有该行，不接受仅凭 Requirement 拼出的输入。已有要求引用本身无法定位时返回
`invalid_input_citation`，不将错误引文带入新报告。

| 检查 | 只靠本地确定性信息能判定什么 | 不足时怎么处理 |
| --- | --- | --- |
| ★/实质性条款缺项 | `starred` 或 substantive 要求处于 gap，输出 `mandatory_response_missing` 废标风险候选；依据是要求引用及完整分区，不捏造“缺失文本”的引文 | comply_only 不是缺行，也不自动证明实质满足；有材料义务但没有证据只能语义复核/补材料 |
| 负偏离 | 已确认响应的 `deviation=negative` 必须报告；★/实质性项列废标风险，其余列扣分风险候选，并引用原偏离说明 | 不改变负偏离、不给扣分数；具体后果须原条款和人判断 |
| 未确认证据 | gap 的 unconfirmed/needs_reconfirmation 等状态提示未完成确认；若响应行实际携带未确认证据则输入完整性失败、停止模型调用 | 不读取或发送其候选正文；不能靠风险“忽略”让其进入 draft/export |
| 证书日期 | 对固定任务证书调用 `certificates.inspect_dates`，沿用边界日包含规则；过期/尚未生效输出日期风险，缺日期为 unknown | 是声明日期检查，不认证证书真伪；无可靠要求绑定时仅列证书检查信息，不臆断资格废标 |
| 内容矛盾、薄弱响应、材料覆盖不足（阶段二） | `combined` 才调用 LLM，逐要求给 no_risk_found/risk/unknown，清楚区分承诺、声明与证明 | 阶段一返回 `check_mode_unavailable`；任意 condition、单位换算、隐藏附件、视觉内容与漏抽均不能确定性声称通过 |

`mode=rules` 只完成上表的本地项目，semantic_status=not_requested，并始终声明语义未检查；
无需模型配置、余额或 UsageRecord。`combined` 不许静默退回 rules；即使 LLM 失败，可保存
已经完成的确定性覆盖及未评估原因，返回 partial。结果计数按每条要求唯一覆盖，不按风险数
推断覆盖率。未映射证书的日期未知不使无关要求失败，但报告 limitation 不得省略。

共享 `VerifiedCitation` 区分招标 `Source`、初稿某个响应行的 `response_text/deviation_note`、
以及已确认 `Evidence.quote`。招标 PDF 引 page，Word 引 `Location`，不伪造 DOCX 页码。
草稿不是上传的 `Document`，其引用以 draft/response_item/card_revision/field 定位，不能塞进
一个假的 `Source.document_id`。Evidence 引用须解析回原材料位置并复核确认与有效选择；
image_region 的视觉观察只是人工观察文字，首版不将它当作图像上的逐字原文，也不发送图像。

以下模型引用规则属于**阶段二 combined**。模型只收到服务端生成的局部 ref 和有界文本。服务端按本次已发送 ref 映射引用，使用
[extraction.py](../../server/app/services/extraction.py) 的 `locate_quote`/`locate_span`，
在**实际发送文本和固定原文中**各定位唯一连续区间，保存原文的精确字面片段。
不接受模型提供的真实源 ID/页码、跨块拼接、错误位置、歧义或遮挡值反向还原；
`{{secret.*}}`、`[REDACTED_…]` 不能作为新校验引用。
“内容矛盾/未发现风险”须同时有招标与投标支持引用；缺项只引用招标并附 coverage 元数据，
不强求不存在的投标引文。验证失败丢弃结论文字，仅保存固定原因码，原要求记 unassessed；
不将失败项默认为通过。证据逐字有效不等于语义结论正确。
`VerifiedCitation` 是服务端核验后才可持久化的输出类型，Pydantic 只检查结构，不能靠直接
构造该类型跳过原文/父对象校验；三种 citation 都受上述门槛。FindingView.source 固定为
关联 Requirement 的招标出处，其余论据在 citations 中，不允许模型替换 source。

## 阶段二外发边界

阶段一 `rules` 不外发任何文本，不要求模型配置、余额或遮挡开关，也不创建 UsageRecord 或
VendorCall。以下边界已批准，待 `combined` 实施时适用。

遵循 [model-drafting-redaction.md](../notes/model-drafting-redaction.md) 和
[confidential-values.md](../notes/confidential-values.md) 的替换顺序：登记值先变成占位符，
再由 `redaction.redact_tree` 遮挡其余业务文本。所有原文、响应、偏离说明、Word 位置标签及
字段名称/标签都走同一外发遍历；只发送 `OutboundContext` 的局部 ref、文字和字段提示，
无值、尾号、路径、凭据、文件字节、未选资源或人工误报理由。标题/资料是数据，不执行其中指令。

阶段二 check/score **要求遮挡开启**：现有设置若关闭，预检返回
`redaction_required`，实际语义调用阻止；不由 job 修改开关，也不静默替管理员开启。这比
现有起草允许管理员关闭的行为严格。rules 不外发，因此不受此限制。
值行或设置修订变化，后续调用停止并要求重新预检。包含占位符的响应可用于理解主题，
涉及被遮挡值的满足判断记 `redacted_input_unassessable`，不猜报价或证件值。
模型理由、建议也做本地敏感信息检查，未知占位符/敏感字面值导致拒收；原始输出不落日志。

## 阶段一数据模型与迁移

迁移 [0032_check.py](../../server/migrations/versions/0032_check.py) 新增下列表。每表均有 UUID 主键、**NOT NULL org_id/task_id**，
`UNIQUE(org_id,id)`、启用且 **FORCE RLS**，USING/WITH CHECK 绑定事务 `app.current_org`；
缺单位上下文不能读写。所有父引用用 org 复合外键，任务/报告/抽取关系还要绑定同一任务。
actor 用户引用 `(org_id,user_id)` Membership，不能只引用全局 User 绕过成员关系。
上述约束只针对本次新表，不改变现有 Job 的 provider_test 例外或其它历史表的字段。

| 表 / 公开视图 | 保存内容及关键约束 |
| --- | --- |
| `check_runs` / `CheckRunView` | 不可变报告、job/run_id、draft/extraction/document、input manifest/hash、加密输入和规则/schema 版本；(org_id,job_id) 唯一，一个 job 最多发布一个报告，run_id 记录成功发布的 attempt，父键连 jobs/draft_runs/tasks/documents；阶段一 usage 引用为空 |
| `check_items` / `CheckItemView` | 每报告每 Requirement 恰一行，关联该 draft 的 ResponseItem（包括 comply_only/gap 行）、卡片修订及规则/语义覆盖；(org_id,report_id,requirement_id) 唯一，关联 requirement/extraction、response_item/draft 的组合，不允许同单位跨任务混绑 |
| `check_certificates` / `CheckCertificateView` | 固定任务证书选择、修订、检查日和日期状态；复合外键连 task_certificates/certificate_revisions；要求绑定只允许本报告输入，未绑定保留空列表及 limitation |
| `check_findings` / `FindingView` | 不可变机器候选、方法、风险等级、职责、原因；连 check_item/requirement；阶段一方法固定 deterministic，公开 status/revision/latest_decision 由历史推导，不改写候选 |
| `check_finding_citations` / `FindingCitationView` | 每条有效引用，typed nullable 外键列连 document/chunk 或 response_item/card_revision 或 evidence，CHECK 恰一种来源；quote 保留精确原文，Source 视图从固定父位置组装 |
| `check_decisions` / `FindingDecisionView` | append-only 的 dismiss/reopen、非空理由、reason hash、human actor/time、递增 revision；(org_id,finding_id,revision) 唯一，外键绑定原 finding/report |

输入材料即使未成为引用也是依赖。manifest 中引用的每个对象仍须有受约束的父链，不能仅凭
JSON 内 ID 保证隔离：ResponseItem→卡片修订→Evidence/资源选择沿现有关系核对；证书要求
关联使用 `(org_id,report_id,certificate_id,check_item_id)` 关系表 `check_certificate_items`，
公开视图合并为 `CheckCertificateView.requirement_ids`，该表同样 NOT NULL org/task、FORCE
RLS，复合外键同时绑定 `check_certificates` 与 `check_items` 的报告。Usage 引用不接受客户端
数组，以受单位/作业约束的 `UsageRecord` 查询组装公开列表。

迁移先补足必要父表组合唯一键，再建表、外键/状态约束、RLS、最小运行角色 grants 与触发器。
worker 只在拥有相应 running job/run_id/有效 lease 的完成事务中插入机器报告，原文输入加密
列纳入现有 key rotation 登记；人类决策只经 actor=session、有效 Membership 和职责 gate。
加密沿 `core/security.Secrets.for_data` 的当前数据密钥/历史密钥轮换，不自创 key_id；
内部密文列纳入 `admin.ENCRYPTED_COLUMNS`，不把密钥或加密 envelope 放入公开 AssessmentInput。
递延校验要求覆盖分区完整、引用确切归属、已确认 Evidence、run/draft 输入一致。运行角色
不得更新/删除候选、引用或历史；决策用锁定 finding 的 expected_revision CAS，禁止假造 worker
为 human。历史保留，回退应用时保留表，不做破坏性 downgrade。

## 权限与人工误报处理

权限来源是 [services/auth.py](../../server/app/services/auth.py) 的 `Identity`、`ROLE_SCOPES`、
`SCOPES` 和 [services/tokens.py](../../server/app/services/tokens.py) 的 `create_token`；
[core/security.py](../../server/app/core/security.py)负责密钥/签名，不是 scope 的定义位置。

| 新范围 | 登录角色 | API token / agent |
| --- | --- | --- |
| `check:read` | admin、bidder、technical、viewer | 可按显式签发授予，与有效 Membership 的角色权限取交集 |
| `check:run` | admin、bidder、technical | 可显式授予；不隐式扩充旧 token |
| `check:decide` | bidder 的 commercial、technical 的 technical | 不入 token `SCOPES`，请求及 DB 双重拒绝 token/worker/内外部 agent |

每路由另需 `task:read`、`draft:read`、`card:read` 及实际依赖材料的读取范围；读取报告也要
重验其全部依赖，不通过只隐藏风险正文是不够的。worker 复核发起者有效成员、角色和原 token
状态/范围。新 scope 并不授予 `evidence:confirm` 或 `export`，它们永远不进令牌 allowlist。

职责沿已有卡片 `ReviewDomain`；没有职责的发现不可忽略，先通过现有人工分类流程明确职责，
重新生成 current 初稿/校验。admin 不因为管理身份取得跨专业确认权。dismiss 仅对 open，
reopen 仅对 dismissed；两者均要求非空理由、当前输入 hash 和 expected_revision。
并发失败为 409/exit 2，失败不写半条决定。原因保存于受权历史；审计只存 hash。
该职责映射直接沿 `response_cards.human` 的 commercial→bidder、technical→technical，
不引入尚不存在的任务成员职责系统。每次决定硬性要求 actor_kind=session、token_id=null、
有效 Membership、相应角色及 check:decide；API 和 DB 都检查，worker 即使继承 scope 也拒绝。
新 finding 版本为 1，每次决定将其派生版本加 1，状态由最新 append-only decision 推导。

忽略保留机器原结论和全部证据，可被重新打开，只影响本次 finding 的展示状态，不改变
关口、偏离、得分或之后重跑的结果；新报告从 open 开始，不自动沿用历史忽略。
首版没有“已修复”按钮或自动清除，需修改材料、重新人工确认并重跑验证。模型不得代写
人类理由，不自动生成或启用候选记忆，后续记忆能力另立契约。

## 作业、Provider 与计费

### 阶段一 rules

job kind=`check` 的 `document_id` 使用 draft 所属 extraction 的真实招标 document，
而非虚构标书文档。沿 [background-jobs.md](../notes/background-jobs.md) 的持久化后派发、
租约/heartbeat/attempt `run_id`、取消和显式 retry；
[jobs/processor.py](../../server/app/jobs/processor.py)已接入分支，`services/jobs.status/cancel`
有同等权限门槛。worker 在发布事务内复核输入、身份和所有依赖；变化返回
`check_input_changed`，旧 worker、丢 lease或取消不得发布。

缓存身份绑定 org/task/发起者、draft 全输入 hash、检查日、mode、规则/提示词/schema/adapter、
遮挡及保密值修订。相同输入返回同一作业和报告；新输入产生新报告，不覆盖人工决定。
failed/cancelled/过期 lease 的 `--retry` 延用原输入，不能抢占活 lease。rules 不调用 Provider、
不创建 UsageRecord/VendorCall、不预约或扣除余额；费用和 wrapper cost 都为零。

已映射证书的日期 unknown 或无法形成有效规则引用时，对应要求记为未完成评估，报告为
`partial`，读取与等待返回 exit 5。未映射证书的 unknown 只进入证书清单与 limitation。
partial 是已发布作业的终态，相同输入仍命中原报告；修改输入或规则版本后重新评估，
新报告不继承既有人工决定。

`--dry-run` 零外部调用、零数据写入（包括 Job/Audit/Finding/Usage/VendorCall/余额），先算一致的
输入 hash；actual submission 必须带回 `expected_input_hash`。预检的 provider/config/model/
reasoning 均为 null，`cost_basis_reason=no_model_calls`，`admission_blocker=null`。

### 阶段二 combined

以下 Provider、计费、部分发布和模型估价规则已批准但未实施。阶段一接收 `combined` 时只返回
`check_mode_unavailable`。

`CheckProvider.check(CheckProviderRequest) -> CheckProviderResult` 是阶段二新增的 LLM 能力，
业务仅依赖 Protocol。适配器在 `providers/` 中沿 `HTTPExtractor` 的结构化调用模式实施；
`CheckWireOutput` 不允许额外字段，每批必须恰好覆盖 requested requirement IDs。批次不拆
原文单字段、不隐式截短；超上下文限制显式失败。缺答/重复/未知 ID、无效引用记录拒绝码，
不从另一要求借结果。`batches` 固定每次实际发送 refs；`usages` 复用 `ProviderUsage`，
包括模型/version/duration/input/output tokens/vendor cost/platform charge，绝不从文字长度
补造真实 usage。接入层返回失败码及可用批次，不暴露供应商原始错误。
wire 类型允许先解析出候选以保留可用批次；服务端再以请求 ID/ref 集合逐项拒绝漏答、
重复、串答及越界引用，不能把结构校验等同于接受。unknown 不带 findings；risk 引文须属于
该要求及所绑定响应，no_risk_found 必须同时核验 tender/bid 引文。B10 使用独立 score job、
Protocol 和缓存空间，只导入通用 Assessment 类型，不复用 CheckJobResult。

执行必须处于 [JobExecution.activate](../../server/app/jobs/execution.py) accounting context，
由 [providers/calls.py](../../server/app/providers/calls.py) 的 `accounted_call` 与
`HTTPExtractor.post` 完成逐调用准入和 UsageRecord 落库，复用 `billing.charge_usage`，不另写
扣费器。引用被拒、拒答、截断或取消不退掉已发生用量；同一 `(org_id,job_id,run_id,call_id)`
只结算一次，未决请求保留 reservation。`CheckProviderResult.usages` 仅用于关联核对，不能
再次入账。rules 不制造零用量记录。供应商费用 USD 与平台计费币种/售价分开，计费细节唯一
维护在[预付机制](../notes/prepaid-billing.md#admission-and-the-spending-bound)。
调用关联以 accounting context 的 call_id 及其落库 UsageRecord 为准；ProviderUsage 本身
没有 call_id，不修改既有类型或按列表顺序猜账务归属。统计与账本不一致视为计费硬失败，
停止发布，保留既有扣款/预留等待核对。
模型选择沿[配置机制](../notes/provider-config.md#resolution-and-cache)：提交固定
provider_config_id、provider_source 和非秘密 identity，worker 按该不可变配置修订解析，
目录禁用/改价或不支持新能力时显式失败，不自动切换厂商。单位自带密钥同样记录 usage、
VendorCall 和累计 call ceiling，但按现有机制平台 charge/reservation=0、跳过预付余额检查；
未知供应商 USD 仍为 null，不能把零平台扣费称为免费。平台模型才执行售价、max_charge 与
余额预约；单位直付模型的 max_charge 不承诺控制厂商账单，预检显式警示。

combined 预检参照 `card_generation.snapshot/estimate/submit_generation`，不是零模型组表的费用。
`--dry-run` 仍零外部调用、零数据写入（包括 Job/Audit/Finding/Usage/VendorCall/余额）；
先算一致的输入 hash 和完整请求的 first_pass_upper_bound，actual submission 必须带回
`expected_input_hash`。首次调用余额不足/单 job cap 不足作为 admission_blocker；估价未知为
null 并说明，不报零元。无可用平台售价不得发送收费调用。每次重试/拆批仍受 live reservation、
call ceiling、`max_charge` 和余额约束；first-pass 估计不承诺重试后的总费用。
`Task.budget_usd` 不等于平台币种的 job cap，首版不宣称实现累计任务预算。

有已完成覆盖的后续 provider/预算中止或引用拒绝可发布 partial；取消、lease/heartbeat、
输入失效、计费失败则不发布整个报告。全量失败是否已有本地规则覆盖由结果明确决定，
不能因为 findings=[] 就输出“无风险”。

## 审计与失败模式

审计事件为 `check.submit`、`check.publish`、`check.dismiss`、`check.reopen`，异常及取消沿已有
job 记录；缓存读取、dry-run 不造假执行事件。AuditLog 写 org、actor/user/token、task、job、
run、report/finding/decision IDs、版本、input/reason hash、计数/状态和固定错误码。人工决定
与审计同事务，禁止原文、响应、报价、账号、证件号、raw ref、原始模型输出或理由全文入日志。

| 失败 | 必须保持的行为 |
| --- | --- |
| 缺失/跨单位/跨任务 parent、失效成员/token | 404 或既有身份错误；不发模型、不泄露存在性 |
| draft/卡片/材料/证书/保密值/职责变化 | 预检与调用/发布重新比较；输入变更停止，历史只读标 stale |
| 完整性错误、未确认证据混入响应行 | 硬失败；不得以普通 warning 继续发模型 |
| 日期或证据文本不足、原型图像未做视觉检查 | unknown/明确范围缺失，不能变成满足或默认满分 |
| redaction 关闭、敏感值所需断言被遮挡（阶段二） | 语义调用前阻止，或逐项 unassessed；无自动关闭遮挡重试 |
| 引用拼接/错误/歧义、模型漏项/重复项（阶段二） | 不存无效结论，覆盖项注明原因，partial 可审阅 |
| 原始输出/异常包含秘密或指令（阶段二） | 脱敏固定码，拒绝不可信文本，不执行原文中的指令 |
| 队列派发失败、worker 接管、账务响应未知 | 复用持久作业和 run_id 栅栏；阶段二保留资金预留且不重复结算 |

## 阶段二评测依据

只采用本机 `ai-bid-tool/data/work/clef-eval/` 的 `summary.txt`、`summary_clef.txt`、
`summary_deepseek.txt`、`summary_glm.txt` 聚合统计，不复制真实标书、公司名称或逐条结果。
这些是单份中标样本的受控相关性/变异实验，不能视作正式得分、普遍准确率或投标结论。

数字改小/截断变异中，Clef 分别识别 16/17、15/15，仍有误判；DeepSeek/GLM 为
17/17、15/15，但原响应判对分别只有 29/32、28/32。引用逐字率 DeepSeek 两组为
112/118、63/64，GLM 为 37/66、45/64；Clef 汇总没有对应引用比率。因此语义结果与本地
验引必须分开，保留误报的人类处置及 unknown，而不能按模型“高把握”直接放行。

完整章节的对应项相关性均值为 Clef 1.94/2、DeepSeek 2/2、GLM 2/2；只留前 400 字仍为
1.66/2、1.82/2、1.86/2，说明主题相关性可能高估薄内容。score 必须单独核对计分条件和
所需材料，不能直接映射这些相关性分数。单次中位延迟为 5.79s、4.4s、3.0s；后两者 p90
为 17.6s、5.2s，Clef 缺 p90。不同并发与样本量的批次耗时不能直接用作 SLA。

token 统计仅为聚合量，不能证明重复相同输入的 token 确定性；四份 summary 没有重复调用
逐次对照。因此需在后续 eval 中测同输入的结果/token 波动。Clef 汇总中的 token 数不是
供应商账单或逐请求可结算凭据，不能作为预付扣费来源；目录 LLM 同样只按已接入的真实
逐调用 usage 计费，不能拿此评测估价扣款。

## 验收与阶段二测试

以合成招标、两单位 A/B、真实 PostgreSQL runtime role、API→worker→CLI 链路验证；
所有外部 Provider 为 fake/MockTransport。设置 `BID_CHECK_ACCEPTANCE_DIR` 时，端到端输出
合成 input manifest、报告 JSON 与可重跑命令；工件保留合成记录 ID 以重算 input hash，但不含
会话、Authorization 或候选正文。未设置时只写 pytest `tmp_path`，验证工件不放 `docs/`。

1. 对每张新增表（含 `check_certificate_items`）验证 A 不能读写 B、无上下文拒绝、FORCE
   RLS、跨单位/同单位跨任务复合 FK、历史不可改删、假 actor 和旧 run_id 拒绝；对接口表
   每一条 POST/GET、列表/history、job status/wait/cancel 做 A 访问 B 统一 404 的端到端检查。
2. 确认 gate：响应行无未确认材料、gap 不外泄候选，角色职责交叉拒绝；token 申请
   evidence:confirm/export/check:decide 全拒绝；内外部 agent/worker 无法 dismiss/reopen。
   dismiss 不改变 draft/export/score 门槛，理由空白、版本冲突、并发决定保持原子性。
3. 已确认输入从预检到发出/发布间变更、重开卡片、替换资源、修引文、更改保密值/设置、
   撤销成员与 token、证书边界日/未知日、★ 缺项/负偏离完整覆盖均可重现。
4. 阶段一覆盖 PDF/Word 精确来源、候选正文不泄漏和遮挡引用拒绝；阶段二再覆盖同词多处、
   跨块拼接、未发送 ref、提示词注入、unknown 与 no_risk_found 和薄内容不得默认通过。
5. 阶段一覆盖 rules 零 provider/账务、dry-run 零写入、preview hash 不符、取消/重试与 lease
   接管；阶段二覆盖调用预约、并发余额/上限、拒答/截断仍计费、重复结算和 unknown reservation。
6. 每个新增 CLI 命令、remote/local 模式、七键与 0/2/3/4/5、schema 新增项均做快照；原有
   命令不变。真实模型效果/重复 token 波动/延迟仅在显式启用的 `evals/` 运行，CI 不调用。

阶段一自动化位于 `server/tests/test_check.py`、`test_check_storage.py`、`test_check_api.py` 和
`test_check_cli.py`；阶段二项目继续作为后续验收目标。本文不记录某次测试运行状态。

## 已定决定

| 决定 | 已批准默认与理由 | 阶段 |
| --- | --- | --- |
| 首版输入 | current 初稿，复用确认/引用/agent 读取边界；DOCX 交付件后续另立仅人类可启动的文件校验契约 | 阶段一 |
| 语义外发与关闭遮挡 | check/score 强制开启，否则 `redaction_required`；rules 无外发，不受开关限制 | 阶段二 |
| 误报职责与继承 | 沿 ADR 0005 由专业责任人逐项决定，理由必填、重跑不继承 | 阶段一 |
| 风险与出口 gate | 仅建议，已有 export gate 不变；修改材料并重新确认后重跑 | 阶段一 |
| 证书检查基准日 | 必填 `assessment_date`，不从当前日期或任务截止日推导 | 阶段一 |
| 预算范围 | combined 先复用 prepaid、逐次准入、per-job cap 与 max_charge；不把 Task.budget_usd 当作已执行上限 | 阶段二 |
| Clef 作为并列 Provider | 首版 semantic check 沿用既有可配置 LLM；Clef 只作为后续 triage/score 评测方向，在账单和预约上界明确前不扣单位余额 | 阶段二 |
| partial 的再次评估 | 固定缓存；改输入、规则或模型形成新 run，不自动重复收费或覆盖历史决定 | 阶段一及阶段二 |
