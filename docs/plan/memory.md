---
kind: plan
---

# 分层记忆存储、检索与自动候选

状态：**已批准；单位层首片已实现，PostgreSQL 集成验收待主会话执行。** 对应[路线图](roadmap.md) M01、M02、M03，并涉及 P03 与 C02。

[运行契约](../../server/app/schemas/memory_contracts.py)定义 Pydantic 模型、Provider 接口和
CLI 的 JSON 结构；所有推荐默认值已批准，见[已定决定](#已定决定)。实现入口见
[记忆机制](../notes/memory.md#code)，后续作用域仍按本文的启用条件推进。

## 目标与边界

完整目标来自[设计文档的记忆系统](../AI%20标书工具设计文档.md#记忆系统)：
四层记忆、显式 CRUD、候选人工审批、停用与到期、带单位及作用域约束的检索、
结果中的使用记录，以及从人工反馈产生候选和评测样本。记忆只影响工作方法，
永远不能充当参数证据、替代真实材料、满足证据确认或导出关口。

首片完整链路为：**单位层**新增候选 → admin 人工审批 → PostgreSQL 精确、
关键词及标签检索 → 卡片模型起草 → 按实际调用记录使用清单 → 人工驳回或编辑产生新候选。
这一链路不依赖 Embedding；CRUD、检索、反馈转候选本身不调用模型。已存在的卡片起草仍通过
LLM Provider 调用并计费。人工确认卡片同时形成单位内评测样本，不生成自动生效的记忆。

四层语义及后续启用条件如下。首片拒绝未启用的作用域，不能把它悄悄改成 `org`，
也不能用“空结果”冒充已实现的层；M01–M03 的完整范围继续保留。

| 层 / wire 值 | 内容、所有者与读取边界 | 写入与确认 | 首片边界 |
| --- | --- | --- | --- |
| 全局 / `global` | 通用招投标知识；所有单位只读，绝无单位来源 | 仅平台维护；来源与审核人见[已定决定](#已定决定) | 禁用；不建全局表，不使用特殊单位或空 `org_id` 绕过规则 |
| 单位 / `org` | 单位惯例、经验、规则及默认偏好；本单位成员和获授权 agent 可读 | 可写成员/令牌提出，单位人类 admin 审批 | 完整闭环 |
| 用户 / `user` | 个人偏好；所有者为 `(org_id, user_id)`，仅本人及本人发起的 agent 读取 | 本人管理，激活仍须本人会话确认 | 模型先定义；后续启用私人结果和作业读取隔离，不能把私人偏好写进共享卡片快照 |
| 项目 / `project` | 本任务决定、选型、答疑、分工；归属 `(org_id, task_id)` | 任务成员提出、人类任务成员确认；归档后只读保留 | 模型先定义；依赖 F06 的任务成员与归档机制，不把单位成员等同任务成员 |

不在首片范围：全局运营维护、Vue 记忆管理页、语义检索/重排、LLM 自动总结、跨单位训练集、
历史卡片批量回填、风险卡误报（B09 尚待契约）、req extract/check/score/agent 的记忆消费。
后续消费者必须遵守同一检索和使用记录协议，不能直接读取表拼提示词。
不推进任务成员/归档的独立功能，也不声明首片完成四层产品。

## 接口

下文列出首片入口及后续向量入口。`org_id` 在 HTTP 中来自已验证的单位请求上下文；检索 body 还必须显式
包含相同的 `org_id`，不一致返回 404。其他写入 body 不允许传组织、创建人、确认人、状态或
来源种类以覆盖认证结果。列表的 target 编码为 `scope/user_id/task_id` 查询参数。

| 入口 | 契约与返回 `data` / `items` |
| --- | --- |
| 数据 | [memory_contracts.py](../../server/app/schemas/memory_contracts.py)：复用 `Contract`、`Result`、`Cost`、`ProviderUsage`、`JobAction`、`ReviewDomain`，不复制既有定义 |
| `POST /memories` | `MemoryCreate` → `MemoryData` / `[]`；始终创建 candidate |
| `GET /memories` | `MemoryListRequest` → `MemoryPageData` / `MemoryView[]`；带 scope，分页、筛选不改变授权 |
| `GET /memories/{id}` | → `MemoryData` / `[]`；过期和停用可查，默认不显示逻辑删除对象 |
| `PUT /memories/{id}` | `MemoryUpdate` → `MemoryData` / `[]`；完整替换内容和到期时间，新 candidate 修订 |
| `GET /memories/{id}/history` | → `MemoryPageData` / `MemoryRevisionView[]`；显式历史入口保留已删除对象的受权追溯 |
| `POST /memories/{id}/decisions` | `MemoryDecision` → `MemoryData` / `[]`；approve 或 reject，登录人类且满足层级角色 |
| `POST /memories/{id}/disable` | `MemoryDisable` → `MemoryData` / `[]` |
| `DELETE /memories/{id}` | `MemoryDelete` JSON body → `MemoryData` / `[]`；逻辑删除，不物理擦除历史 |
| `POST /memories/retrieve` | `MemoryRetrievalRequest` → `MemoryRetrievalData` / `MemoryHit[]`；`?preview=true` 只读，不持久化检索记录 |
| `GET /memory-retrievals/{id}` | → `MemoryRetrievalData` / `MemoryHit[]`；重新检查所有输入的读取权限，过时记录明确标记，不能作为当前检索结果复用 |
| `GET /jobs/{id}/memory` | → `MemoryCallData` / `[]`；只展示该 job 实际准入调用的记忆引用，不展示加密提示词 |
| `POST /tasks/{task_id}/memory-candidates` | `MemoryCandidateJobRequest` → `MemoryJobSubmissionData` / `[]`；只重放服务器已有反馈事件，不接受任意反馈正文 |
| `GET /tasks/{task_id}/memory-feedback` | → `MemoryPageData` / `MemoryFeedbackView[]`；供失败恢复定位事件 |
| `GET /tasks/{task_id}/memory-evaluations` | → `MemoryPageData` / `MemoryEvalSampleView[]`；只有单位内样本元数据 |
| `GET /memory-evaluations/{id}` | → `MemoryEvalDetailData` / `[]`；受权 admin 查看净化摘要和来源后审阅，不返回原始敏感差异 |
| `POST /memory-evaluations/{id}/review` | `MemoryEvalReview` → `MemoryEvalData` / `[]`；接受/排除样本不等于批准记忆或证据 |
| 作业 | 复用 `GET /jobs/{id}`、`POST /jobs/{id}/cancel` 及 CLI status/wait/cancel，增加该 job kind 的权限检查 |
| 后续向量步骤 | `POST /memories/index` 接收 `MemoryIndexRequest`，返回作业；启用前不注册，不能调用未配置 Provider |

分页默认 50、上限 100，按 `(created_at,id)` 稳定排序，cursor 绑定单位、用户与过滤条件，
跨单位/作用域复用 cursor 拒绝。检索最多 50 条、默认 12 条和 8,000 字符；完整条目放入上下文，
不从中间截断规则。Pydantic 负责结构限制；资源归属、成员、敏感内容、服务器时间和数据库
并发约束必须由服务/数据库校验，不能把模型校验当成授权。

## 当前代码依据与差异

| 当前依据 | 接入点或明确差异 |
| --- | --- |
| [auth.py](../../server/app/services/auth.py) 的 `Identity`、`authenticate`、`membership`、`SCOPES`、`ROLE_SCOPES`，以及 [api/main.py](../../server/app/api/main.py) 的 `context` | 认证/角色与范围实际在 services，不在 `core/` 的某个 scope 模块；复用会话/令牌与 Membership 交集，memory scopes 与角色边界见[权限表](#权限角色与-provider-计费) |
| [core/db.py](../../server/app/core/db.py) 的 `Database.transaction`；[隔离笔记](../notes/tenant-isolation.md) | 使用事务级 `app.current_org`、`set_actor_context`；运行角色不能拥有表或 BYPASSRLS |
| [entities.py](../../server/app/models/entities.py) 的 `Task`、`Job`、`UsageRecord`、`VendorCall` | Task 未提供任务成员/归档；Job 的 task/document 空值只对 `provider_test` 例外，不能虚构文件为无任务 Embedding 填充 |
| [response_cards.py](../../server/app/services/response_cards.py) 的 `update_card`、`card_action`、`append_revision` | 人工编辑/驳回与不可变修订同事务；`CardUpdate` 没有 reason，不能假称编辑时已有学习指令 |
| [ADR 0005](../adr/0005-human-confirmed-responses.md)、[response-cards.md](../notes/response-cards.md) | 已定义模型提议/人工确认与卡片缓存，明确未包含自动记忆；本契约只扩展依赖与候选，不改变专业职责确认 |
| [card_generation.py](../../server/app/services/card_generation.py) 的 `snapshot`、`submit_generation`、`generate`、`publish`、`check_input_access`；[drafting.py](../../server/app/providers/drafting.py) 的 `request_body`、`groups`、`draft` | snapshot 与 wire 输入使用独立 memory 规则/偏好及升级后的 prompt/schema 版本；保留双文本引用校验 |
| [drafts.py](../../server/app/services/drafts.py) 的 `assemble`、`current_draft_inputs`、`show_draft` | `draft` 已是确定性逐字组表，不再调用 LLM；记忆在上游 card generation 消费，组表仅继承使用轨迹及失效提示 |
| [providers/base.py](../../server/app/providers/base.py) 的 `LLMProvider`；[configured.py](../../server/app/providers/configured.py) 的 `resolve_configured`；[provider_contracts.py](../../server/app/schemas/provider_contracts.py) | 现有配置 capability 是 `llm_extract`，没有可直接调用的 Embedding resolver；保留 `server/app/memory/` 为业务包，该目录承载业务读写与选择 |
| [ADR 0001](../adr/0001-platform-console-access.md)、[agent.md](../../agent.md#硬性规则任何情况下都不得违反) | 全局表例外只覆盖获准的平台表，没有 memory；设计所需全局层必须另行明确批准，不直接沿用跨单位策略 |

## 数据模型与迁移轮廓

首片使用下列单位业务表，**每张均 `org_id NOT NULL`、`ENABLE ROW LEVEL SECURITY` 和
`FORCE ROW LEVEL SECURITY`**，USING 与 WITH CHECK 同时限定当前组织；缺组织上下文全拒绝。
每张实体表有 `(org_id,id)` 唯一约束；epoch 用复合主键。外键禁止仅引用裸 UUID。
表定义见 [memory.py](../../server/app/models/memory.py)，约束与授权见
[记忆迁移](../../server/migrations/versions/0037_memory.py)。

| 新表 / 公共 view | 字段与约束 |
| --- | --- |
| `memories` / `MemoryView` | id、scope、user_id/task_id、current_revision_id、revision、deleted_at、source_feedback_event_id/generator_version；来源事件与生成器的非空组合在同 org 唯一；首片 DB scope CHECK 仅 `org`，后续迁移才打开 tenant 三层；当前修订指针的 `(org_id,id,current_revision_id,revision)` 复合外键延迟校验 |
| `memory_revisions` / `MemoryRevisionView` | memory_id、revision、content(kind/conflict_key/text/tags)、status、source、content_sha256、created_by/actor_kind、confirmed_by/at、expires_at、decision/reason hash；`UNIQUE(org_id,memory_id,revision)`；只追加，不可更新/删除 |
| `memory_scope_epochs` / `MemoryEpochView` | `(org_id,scope,owner_id)` 主键与递增 epoch；org 的 owner_id 等于 org_id；未来 user/project 分别绑定 Membership/Task；用于包含零命中结果的缓存失效 |
| `memory_feedback_events` / `MemoryFeedbackView` | task/card、before/after revision、actor、kind、净化策略版本/摘要；唯一 `(org_id,card_id,after_revision_id,kind)`；加密的有界净化反馈，原子 outbox，事件不可修改 |
| `memory_eval_samples` / `MemoryEvalSampleView` | event/task/card/修订引用、label、策略版本、摘要、加密净化样本、review_state/revision/by/at；唯一 `(org_id,feedback_event_id,generator_version)`；样本正文不可变，受权人工更新审阅列须乐观锁和审计 |
| `memory_retrievals` / `MemoryRetrievalView` | actor/user/token/task、查询及 manifest hash、scope/epoch 清单、策略版本、valid_until、created_at；加密查询/发送文本快照，追加写；preview 不建记录 |
| `memory_retrieval_items` / `MemoryRetrievalItemView` | retrieval_id、memory/revision、内容/发送 hash、顺序、选入/排除理由；`UNIQUE(org_id,retrieval_id,memory_revision_id)`；无裸正文，追加写 |
| `memory_call_inputs` / `MemoryCallInputView` | task/job/run/call/retrieval、实际 requirement IDs 与所发记忆修订、prompt/hash/version、usage ID、调用状态；唯一 `(org_id,job_id,run_id,call_id)`；提交前写 admitted，结算仅补 completed/unknown 与 usage ID，输入不可变 |

所有记忆正文先经敏感值拒绝检查后存储。首片可检索的规则文本/标签用 PG text/text[]，
不虚称已加密字段可直接做 SQL 关键词匹配；只允许不含机密的规则/偏好。待审核反馈、
查询和发送文本快照使用 [core/security.py](../../server/app/core/security.py) 的 `Secrets.for_data`
加密组织/记录绑定 envelope，读取核对绑定。日志、审计与公开调用清单只保留 ID/hash/计数。
记忆不得存报价、身份证号、银行账号、密钥或证据原文；不能通过关闭起草遮挡开关解除这个限制。
首片复用 [redaction.py](../../server/app/services/redaction.py) 的规则与
[confidential.py](../../server/app/services/confidential.py) 的保密值识别，命中即拒绝原文写入。
自动反馈先净化，且不复制 Evidence、证书页、材料 quote、截图或整张卡片作为记忆。
规则识别仍有边界，候选必须人审，不能声称检测覆盖所有机密表达。

复合外键及额外 gate：

- user/创建人/审批人都引用 `(org_id,user_id) → memberships`，不借全局 `users` 共享个人记忆。
  task 引用 `(org_id,task_id) → tasks`，token 引用 `(org_id,token_id) → api_tokens`。
- 记忆修订引用 `(org_id,memory_id) → memories`；来源卡片同时绑定
  `(org_id,card_id,task_id)` 与 `(org_id,card_id,revision_id)` 到既有 card/revision 唯一键。
  feedback 的前后修订同卡且相邻，事件/样本/候选间同组织同任务；服务器生成来源，不能接受
  客户端伪造 source.origin/system 或 feedback ID 冒充人工事件。
- retrieval item 绑定 `(org_id,memory_id,revision_id)` 与父检索；call input 绑定
  `(org_id,job_id,run_id,call_id) → vendor_calls` 和 `(org_id,retrieval_id)`，usage 引用含
  org/job/run/call 的匹配唯一键。数组中的 memory/requirement 引用还须触发器逐项核验；
  只能是该检索已选的记忆和该请求确实发送的要求，不能伪造 `used` 清单。
- candidate/event/sample 的来源去重靠唯一约束；worker 写 candidate 还核对当前运行 job/run/lease，
  人工 approve/disable/delete 的触发器核对 session、有效用户/Membership/Org 与层级角色。
  令牌表新增禁止 `memory:approve`、`memory:manage`、`memory:eval:review` 的 CHECK，
  保留原 `evidence:confirm`、`export` 禁止项；单靠应用按钮隐藏不算 gate。
- 内部 worker 身份须同时绑定 job 提交者和受限任务；未来 user/project 表策略在组织 RLS 之外
  增加所有者/任务成员保护，所有 history、job、检索、样本入口都走同一读取检查。不能由
  `app.actor_kind=worker` 获得任意私人记忆读取权。

审批对象是确切内容修订。create 总为 candidate，update 追加 candidate 并撤下旧 active；
不得编辑 active 后继承旧确认。approve 仅 candidate → active，记录人和时间；reject 为
candidate → disabled，disable 为 active/candidate → disabled，delete 为禁用修订加 tombstone。
disabled 重新编辑只能成为 candidate，不能直接 enable；逻辑删除不能复活，可另外新建。
所有变更检查 expected_revision，不一致 409；来源、归属、层级不允许 update/move/promote。
同级同 kind/conflict_key 的有效项原则上一条，审批在 scope epoch 锁下检查，冲突 409，
不自动替人停用已有规则。到期通过服务器 UTC 时间即时排除，effective_status=expired；
历史 status 不由定时器改写。expires_at 创建/审批时须在未来。未来项目归档时禁止一切修改，
包括候选审批、停用和作业写入；已存项目记忆仍限成员只读。

迁移按依赖：建立表和复合键 → 加 RLS/最小 grants/不可变与人工状态 trigger → 扩展
令牌 CHECK、作业 kind 分派及使用记录关联 → 最后给运行角色授权。首片只给 org scope，
不回填历史卡片、不改变证据确认；用空 memory manifest 的显式新版本区分旧缓存。
回滚保留历史表和已有审计，不提供破坏性 downgrade。实施时同次增加每张表双单位及缺上下文测试。
迁移保留所有历史，不提供破坏性 downgrade。

### pgvector 与后续索引

向量单独采用拟定的 `memory_embeddings` 表，避免给追加只读的 memory revision 做就地回填。
它也必须 `org_id NOT NULL`、FORCE RLS，含 id、memory_revision_id、scope/user_id/task_id、
`embedding vector NULL`、provider/model/version/model_revision/price_revision/dimensions/metric、provider_config_id、
text_sha256、job/run/usage ID、created_at；公共 `MemoryEmbeddingView` 不返回向量。
使用 `(org_id,memory_revision_id) → memory_revisions` 及其 scope/owner 一致性 gate，
ProviderConfig/Usage/Job 均组织复合外键；唯一键为组织、记忆修订、模型配置修订、模型版本。

首片迁移可保留这个**空表及 nullable vector 列**，无 Provider 调用、无向量索引和零向量占位。
非空向量要求 metadata 全齐、有限非零值、`vector_dims(embedding)=dimensions`，只允许完成
且已结算的对应作业发布；空值不参与距离计算。实际维度、可索引维度上限、模型及价格经 P03
确认后再建匹配维度的索引/分区；不同模型版本或维度不得混排。失效旧向量保留供追溯，
新模型写新记录，禁止覆盖旧修订向量。扩展是否已安装以实施时迁移预检为准，不猜测可用状态。

后续先开放 task 绑定的 `memory_index` / `memory_query` 作业，再讨论单位无任务的批量索引。
后者需要明确扩展 Job/Usage 的 task/document 约束和 [provider 配置机制](../notes/provider-config.md)
中的 capability；不能套用 `provider_test` 或假造 Document。全部新作业仍沿用租约和计费。

## 检索与优先级

`MemoryRetrievalProvider.retrieve` 必须收到 org_id 和非空 scopes；实现绑定已认证的 Identity 和
单位数据库事务，不能从请求 body 构造 actor。调用服务先核对 Identity，
数据库查询再次显式限定 `org_id`、scope、scope owner、current revision、active、未删除、
未到期，之后才打分/排序/limit。无权限的 ID 与不存在统一 404；禁用层返回
`memory_scope_unavailable`。用户过滤只允许认证本人，agent 从发起身份取得本人，不接受
任意 `user_id`。任务过滤先过成员权限，不能由“同单位 task:read”代替。

首片 `keyword-v1` 不增加全文分词依赖：文本/查询 NFKC、casefold、合并空白，精确全文相等
记 100 分；查询和显式 keywords 的去重词项按子串命中每项 10 分（最多 20 项），
精确标签交集每项 5 分（最多 20 项）；零分不选。中文无空格查询作为完整词项，调用者可
提供具体 keywords/tags；不声称具有语义召回。SQL 用绑定参数、转义 LIKE 通配符，
先在有 `(org_id,scope,status)`/归属索引的集合内检索，标签可用 GIN。

检索层扫描顺序为项目、用户、单位、全局；最终并非把四层混成一个分数：

- 事实/规则类 `rule` 按项目 > 单位 > 全局，同 `conflict_key` 低层结果记录 `shadowed`；
  用户层不存规则，不覆盖单位合规规则。
- 偏好类 `preference` 按用户 > 单位默认；项目和全局不提供个人偏好。
  规则块先于偏好块，提示词明确规则约束偏好；偏好不能改证据门禁或业务事实。
- 先按显式冲突键解决层级，再按类、优先级、相关度降序、memory UUID 升序稳定排序。
  同级遗留冲突全部排除并报告 `same_priority_conflict`，不凭“最新”选择事实。
  不同 key 的自然语言矛盾不能确定性识别，必须提示审核，不能声称算法已消除语义冲突。
- top_k 和字符预算只能保留完整项，排除理由公开，保留规则优先；达到预算不调用模型压缩。
  超过索引/查询资源限制显式报错，不先跨单位取 top_k 再过滤。

向量阶段同样在 SQL 距离排序 **之前**限定 org_id、scope、owner、active/到期和 embedding
identity；禁止仅凭 RLS 或应用返回后过滤代替显式双过滤。查询向量及缓存也按单位/作用域隔离。
`vector`/`hybrid` 未配置返回 `embedding_unconfigured`，不悄悄退成 keyword；明确请求 keyword
一直可独立使用。hybrid 的归一化评分、召回目标经评测后另定，规则优先级不能被相似度推翻。

## 起草消费、使用审计与 C02 缓存失效

在 `card_generation.snapshot` 完成目标要求和材料权限检查后，根据本批要求及受限标签生成
检索请求；首片固定 scopes=[org]。将 `MemoryPromptContext` 作为独立 `memory_rules` /
`memory_preferences` 输入交给 `LLMProvider.draft`、`providers/drafting.request_body`；确切新增
可选关键字参数由 `MemoryAwareLLMProvider.draft(..., memory=...)` 定义，返回类型复用
既有 `DraftingOutput`，空上下文不改变其他消费者，
业务读写和选择放入预留的 `server/app/memory/`，厂商/Embedding adapter 只能在 `providers/`。
memory 不能塞进 materials 的 ref 字典，也不能成为模型可引用的 EvidenceInput；
`usable_as_evidence=false` 是协议字段，服务器未知 ref 拒绝规则仍是最终约束。

`snapshot` 同时扩展无正文的 public manifest 与加密 secret：固定 scope/owner、检索版本、
优先级版本、scope epochs、query hash、按顺序选中的 memory ID/revision/hash、到期时间、
排除摘要、context hash。secret 保存这次实际发出的净化文本。计入 `estimate`、`groups`
和 `HTTPExtractor.reservation` 的完整输入大小，不能因为加了记忆而少预留费用。
dry-run 同样检索和估价，但无持久记录、无 audit、无 usage、无外部调用。

“检索到”不等于“模型实际使用”。每个实际 HTTP 批次在 `JobExecution.admit` 同一事务内，
以生成的 call_id 写 `memory_call_inputs`，关联 vendor_calls、job/run、requirement IDs、
检索/记忆修订、发送与提示词 hash。空记忆也记录空清单。重试/拆批各有新 call_id，不能
只给整个作业记一个使用清单；模型声称用了什么不能修改服务器记录。结算时原子补 usage ID，
取消、拒绝、输出非法和 usage unknown 仍保留已准入记录。该字段表示“送入上下文”，不宣称
能证明模型内部因果使用。未实际派发而崩溃的 admitted 项保留 unknown，不冒称 completed。

`CardGenerationRun` 的原有 manifest 追加 retrieval/调用引用；每张生成卡片可追到其成功批次，
partial result 也准确对应。job/status/card/history/draft 只暴露通过 ACL 的 ID/hash 清单，
详细内容经 memory 读取入口查看；个人层未来不能靠共享 job 或卡片泄露偏好正文/ID。

| 变化 | 对新调用、缓存与既有结果的要求 |
| --- | --- |
| 新 active、停用、删除、编辑撤下 active、approve | 在同一事务锁定并推进 scope epoch，触发所有相关检索重新求值；包括以前零命中或未进入 top_k 的结果，不能只对已用 ID 做失效 |
| 到期 | 检索 manifest 的 valid_until 取可访问 active 候选集合的最早到期时间（包括被遮蔽项）；缓存读及每次调用准入实时检查，不能依赖清理作业准时执行 |
| candidate 的无生效内容变化 | 不改变其他调用的有效集合，candidate 永不送模型；如 update 撤下 active，则按上一行 epoch 处理 |
| 检索、优先级、净化、prompt/schema/adapter 或 embedding identity 变化 | 进入 input_hash/cache_key；旧 queued job 版本不一致明确失败，需新提交，禁止新 parser 消费旧快照 |
| 提交后输入失效 | `generate` 的 `before_admit` 与 `publish` 重查 epochs、到期、读取权限及确切修订；失败 `memory_input_changed`，不重检索后偷偷换用新文本 |
| 调用已发出后失效或租约丢失 | 已发调用正常结算；旧 attempt 不能发布内容，调用轨迹保留 |
| 未人工确认的模型草稿所用记忆变化 | 展示 `memory_input_stale`，不得继续确认旧模型草稿；需显式编辑或重新生成后再审阅 |
| 已人工确认的卡片所用记忆变化 | 保留确切人工决定和使用轨迹，显示 `memory_changed_after_review`，不自动改写或撤销确认；人可重开再审。证据/引用/材料原有失效 gate 仍独立生效 |

相同有效输入、顺序和版本重用原付费 job；命中缓存不新增 vendor usage/call，返回原调用 lineage。
新 memory epoch 不隐式重新起草已 confirmed/pending 的保护卡片。`drafts.assemble` 仍只复制确认内容，
`show_draft/current_draft_inputs` 汇总记忆提醒及 lineage，但不让提醒把有效人工确认变成缺口。
未审模型稿的 gate 必须同时扩展 Python 确认路径和数据库确认 predicate，不能只改缓存。
`generate` 同时检查 prompt、redaction、schema 与记忆策略版本；旧快照不得由新 parser 静默消费。

## 自动候选、样本与作业

依据 [ADR 0005](../adr/0005-human-confirmed-responses.md) 的单人专业确认流程，只有成功提交的
人类 `card_action(reject)` 或对模型派生内容的 `update_card` 触发候选事件；confirm 只产样本。
须检查 previous.model_job_id，不只检查 origin：人工驳回修订会变成人类 origin 但仍保留
model_job_id，编辑会清除此字段。捕获前后修订 ID 应在清除前完成。不监听模型生成或 worker
自己写的 revision，避免候选反馈循环；无实质内容变化的编辑不产生候选。

在 task/card 锁、expected_revision 校验及 `append_revision` 成功之后，同一数据库事务
写 feedback event 和唯一的评测样本；reject/edit 再写 `memory_candidate` Job，confirm 不创建
候选作业。任务与 document 使用来源卡片的真实抽取作业绑定；恢复批次只接受同任务同文件的
事件，跨文件返回输入错误。保存发起人的身份/范围，worker 开始与发布前重新核验当前成员及
memory/card/task 权限；没有权限时保留人类反馈事实但不发布候选，不以 worker 提权。
派发在提交之后执行，复用 [queue.py](../../server/app/jobs/queue.py) 的
`Queue.enqueue` 和 [processor.py](../../server/app/jobs/processor.py) 的 `Processor`。
提交后 enqueue 失败保留已提交的人类决定、outbox/job，明确返回重试提示和恢复 ID，
不把成功的人类决定伪装成已回滚，也不自动重放决定；重放入口只调度尚未处理的 event。
直接调用旧卡片动作返回契约保持不变，恢复 ID 放入 warnings 的有界编码和审计关联。

首片 `feedback-copy-v1` 是确定性 `MemoryCandidateProvider`：

- reject：把净化后的人工理由包装成“适用性待审核的反馈”，绑定来源卡片/任务；
  edit：记录有界字段变更摘要（response_text/deviation/deviation_note），明确“人工修改，
  尚未说明是否可泛化”。排除 evidence 字段、quote 及 secret 实值，不凭相邻词猜通用规则。
- 一次事件最多一个 org candidate，kind=rule，默认 conflict_key 使用事件派生键，tags 指向
  非敏感类别；管理员在 approve 前可编辑适用范围/规则正文和冲突键。自动提议恒
  origin=system、status=candidate、confirmed_by=null；内容过长不静默截断成断章规则，
  标记 skipped/no_reusable_feedback。只剩敏感值时 skipped/sensitive_only，不保存原文。
- feedback、candidate source 和 sample 有唯一约束。同事件同 generator 版本重放返回
  duplicate；后续生成器变更也不得静默复制生效记忆，需显式重新提出，保留来源关系。
  不把“卡片驳回”当成“记忆候选驳回”，两者分别审批。

`memory_candidate` 沿用 queued/running/succeeded/failed/cancelled 状态、租约 heartbeat、
每次 claim 新 run_id 和发布前所有权检查。耗时净化/候选计算在事务外；逐事件准备后在
当前 attempt 下原子发布成功项。duplicate/正常 skip 不算部分失败；有成功项且某事件失败
为 completion=partial / exit 5，全部失败为 failed，不把取消或租约丢失当部分成功。
已提交的样本事件不随取消删除；重试只能在旧 attempt 终结或租约过期后，不能抢活跃租约。
缓存键包含 org/task/document、按序 event IDs/净化 hash、generator_version；同批重复请求
复用 durable job。成功的 partial 作业不重置状态，重放其失败 event 子集生成新 job，已产
候选仍由唯一键去重。新 kind 的 status/cancel 必须扩展
[services/jobs.py](../../server/app/services/jobs.py) 的 `status/cancel`：状态读取要求
job:read + memory:read + card:read + task:read，取消再要求 job:cancel 和该作业执行权限。
自动派发与恢复命令走同一实现，无另一套裸 asyncio 后台队列。

样本标签是人工 confirm/reject/edit 的实际动作，不代表事实正确性。保留双版本、来源与
净化 hash，默认 unreviewed；admin 可 accept/exclude（审阅事件仅 hash/ID）。
样本默认留在本单位，既不自动批准记忆，也不自动进入公共评测或厂商训练。候选里的
“来自人工”说明 provenance，不能冒充人工已批准其泛化规则。

## 权限、角色与 Provider 计费

| 新范围 | 人类角色与资源条件 | API token / agent |
| --- | --- | --- |
| `memory:read` | 四角色可读 org；user 仅本人，project 仅任务成员 | 可授予，与当前角色交集，user 必须本人发起；旧令牌不会自动获得 |
| `memory:write` | admin/bidder/technical 提 candidate；org 非 admin 只编辑自己创建且未生效的候选，admin 可管理 | 可提候选，只改本令牌所建且未生效候选；无批准、停用、删除权 |
| `memory:approve` | org 仅 admin；未来 user 本人、project 获任务权限的非 viewer 人类成员 | 禁止，且 actor_kind 必须 session；内置 agent 也不能借用户 scope 确认 |
| `memory:manage` | org 仅 admin 停用/删除；未来 user 本人、project 人类任务成员；归档项目拒写 | 禁止 |
| `memory:retrieve` | 与 read 同时具备；起草还需原有 card/task/material 权限 | 可授予，不能突破指定 scopes |
| `memory:candidate:run` | admin/bidder/technical，且 card:read、task:read、memory:read/write；仅可访问的事件 | 可授予，不能注入虚假人类反馈 |
| `memory:eval:read` / `memory:eval:review` | org admin 加 card:read/task:read；读取/审阅样本 | 首片均不授予 token；DB 禁止 review 范围 |
| 未来 `memory:index` | org admin 且启用 embedding 配置 | 首次只开放人类管理员，另经配置契约确认 |

不新增 evidence:confirm/export 权限来源；[tokens.create_token](../../server/app/services/tokens.py)
与数据库都继续拒绝令牌取得这两项。平台 session 不能当单位 session，用于 global 的后台
必须沿用 [platform.py](../../server/app/api/platform.py) 的独立认证，不能查看单位记忆或反馈。
无资源级权限统一 404；身份无效 401，缺功能 scope 403，不通过错误区分别人是否有该记忆。

`MemoryRetrievalProvider` 是 PG 读写边界，不是厂商 SDK。`MemoryCandidateProvider` 首片只做
本地确定性转换，返回 usages=[]；不制造零费用 UsageRecord。后续语义 candidate 如获批准，
必须放入 providers 适配层并输出 `ProviderUsage`，不能绕开作业准入。

`EmbeddingProvider.embed(EmbeddingRequest) → EmbeddingOutput`、`reservation → Decimal`
已在运行契约定义：输入显式单位/作用域/job/run、模型配置修订与有限文本批次；输出维度、
模型身份、向量、逐次 ProviderUsage。服务核对结果条数、维度、非零有限值及文本顺序，
结果不能自带 scope 来改变检索边界。未配置的生产实现只能显式失败，不用随机/零向量。

所有外部调用（现有起草、以后 Embedding/语义候选）都使用
[calls.py](../../server/app/providers/calls.py) 的 `accounted_call` 与
[execution.py](../../server/app/jobs/execution.py) 的 `JobExecution.activate/admit/complete/unknown`。
每次请求前以该能力实际价格与输入上界预留；Embedding 没输出 token 上限，不能原样照搬
[llm.py](../../server/app/providers/llm.py) 的 LLM reservation 公式。调用失败、解析失败、拒绝、
取消照样即时计量，未知用量保留 reservation；历史费用不能被重试清零。

`UsageRecord` 保存 provider/model/version、duration_ms、tokens/input_tokens/output_tokens、usd、
provider_config_id、platform_model_id、charge 与 job/run/call。平台调用通过
[billing.py](../../server/app/services/billing.py) 的 `require_funds` 预检及 `charge_usage` 结算，
admit 再锁余额扣除所有未结 reservation；结算与 usage/账本/call 状态原子且幂等。
单位自有密钥 charge=0，但仍登记 usage 和调用上限；Result.cost.usd 为厂商成本，不冒充
平台计费币种的 charge。详细规则以[预付费机制](../notes/prepaid-billing.md)为准。
向量查询必须有已提交的 job 和计量上下文，不能在同步 retrieve route 裸调用厂商；
相应异步 CLI/API 行为在向量启用契约确认，不改变首片同步 keyword 的语义。

## CLI JSON 与错误

所有命令都有 `--json`、无交互，org 取现有登录/令牌上下文；远程 API 与本地 PostgreSQL
共享服务及权限，不采用文件数据库。source、正文和多字段修改用 `--input FILE`，避免在
命令行历史传大段反馈或敏感值。输出严格复用 `Result` 七键，不另加顶层 schema_version。
契约版本沿用 [contracts.py](../../server/app/schemas/contracts.py) 的 `CONTRACT_VERSION` 与
`X-Bid-Contract-Version`；[CLI schema registry](../../cli/bid_cli/schema.py) 仅在批准实施后注册。

| CLI（均可加 `--json`） | 输入 / `data` / `items` |
| --- | --- |
| `bid memory add --input FILE` | MemoryCreate / MemoryData / [] |
| `bid memory list --scope org [--status candidate] [--cursor C] [--limit N]` | MemoryListRequest / MemoryPageData / MemoryView[] |
| `bid memory show --id UUID` | ID / MemoryData / [] |
| `bid memory update --id UUID --input FILE` | MemoryUpdate / MemoryData / [] |
| `bid memory history --id UUID [--cursor C] [--limit N]` | ID / MemoryPageData / MemoryRevisionView[] |
| `bid memory approve` 或 `reject --id UUID --input FILE` | MemoryDecision；action 必须与命令一致 / MemoryData / [] |
| `bid memory disable` 或 `delete --id UUID --input FILE` | MemoryDisable/MemoryDelete / MemoryData / [] |
| `bid memory retrieve --input FILE [--dry-run]` | MemoryRetrievalRequest；dry-run 对应 preview / MemoryRetrievalData / MemoryHit[] |
| `bid memory retrieval show --id UUID` | 受权历史 / MemoryRetrievalData / MemoryHit[] |
| `bid memory used --job UUID` | MemoryCallData / [] |
| `bid memory feedback list --task UUID` | MemoryPageData / MemoryFeedbackView[] |
| `bid memory candidates run --task UUID --input FILE [--dry-run] [--retry] [--wait]` | event_ids + JobAction / MemoryJobSubmissionData，wait 后 MemoryCandidateJobResult / [] |
| `bid memory samples list --task UUID` | MemoryPageData / MemoryEvalSampleView[] |
| `bid memory samples show --id UUID` | MemoryEvalDetailData / [] |
| `bid memory samples review --id UUID --input FILE` | MemoryEvalReview / MemoryEvalData / [] |
| `bid job status/wait/cancel` | 复用现有 job 契约，结果增加 memory kind 的受权视图，不泄露 submission |

未来 list 的 user/project 分别要求 `--user UUID` / `--task UUID`；未启用时明确报错。
已注册的新命令只增加 schema 项，不修改旧命令不相容的 JSON。候选作业 dry-run 返回
job_id=null、计数、无写入/计费；普通提交返回 durable job ID，`--wait` 才反映最终完成状态。

空检索的七键示例（此处 hash 是说明用合成值，不是运行记录）：

```json
{
  "ok": true,
  "command": "memory retrieve",
  "data": {
    "retrieval_id": null,
    "org_id": "00000000-0000-0000-0000-000000000001",
    "mode": "keyword",
    "retrieval_version": "keyword-v1",
    "priority_version": "memory-priority-v1",
    "query_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "manifest_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "scopes": ["org"],
    "epochs": [{"org_id": "00000000-0000-0000-0000-000000000001", "scope": "org", "owner_id": "00000000-0000-0000-0000-000000000001", "epoch": 0}],
    "valid_until": null,
    "omitted": [],
    "context_chars": 0,
    "preview": true,
    "currently_valid": true,
    "stale_reasons": []
  },
  "items": [],
  "warnings": [],
  "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0},
  "duration_ms": 3
}
```

| 退出码 | HTTP / 失败情形 | 行为 |
| --- | --- | --- |
| 0 | 200/201/202；有效 CRUD、无命中、正常 skip/duplicate、作业提交成功 | `ok=true`；仅等待完成才宣称结果已产出 |
| 2 | 400/422 非法输入、敏感值拒绝；409 expected_revision/conflict_key 冲突或已归档 | `ok=false`，不写半个修订/候选；修正输入再提交 |
| 3 | 503 暂时数据库/派发不可用、可重试 Provider 故障 | 保留已提交的 durable job、outbox 与费用，显式 retry，不重做人工决定 |
| 4 | 401/403/404；未启用 scope、embedding_unconfigured、输入变更、内容拒绝、预算拦截、计量失败 | 明确错误码；拒绝新调用或发布，已发生用量保留 |
| 5 | HTTP 200 的 terminal completion=partial，部分事件失败或沿用 card generation 部分结果 | `ok=false`，列成功/失败 ID、stop_reason、warnings 与累计 cost；不可假报完整成功 |

错误 `data=MemoryErrorData` 与现有 `api/main.error_response` 同形：
`{"error":{"code":"…","message":"…","exit_code":4}}`；不回显输入、查询、候选正文、
密钥或厂商原文。超时/未知 usage 不当作零费用；取消和租约接管不发布旧 attempt 结果。

## 审计事件

沿用 [entities.py](../../server/app/models/entities.py) 的 `AuditLog` 和 actor context。
审计事件为 `memory.create/update/approve/reject/disable/delete`、`memory.retrieve`、
`memory.feedback.record`、`memory.candidate.publish`、`memory.eval.review`、
`memory.call.attach`，以及后续 `memory.index.publish`。记录 org、actor/user/token、
object/source/job/run/call、前后修订/状态、策略版本、hash、计数和理由 hash；正文和
原始差异不入审计。memory.call.attach 必须能回查 vendor_calls/UsageRecord。
到期无伪造人工动作，读取以 expired 展示。角色拒绝不泄露被拒目标的内容。
平台全局发布将使用平台审计，但该范围必须先取得全局表例外批准。

## 批准后测试计划

以下为实施验收要求；不把未执行的 PostgreSQL 测试当作通过。按实际 API → PostgreSQL RLS →
worker → Provider fake → CLI 链路验证，不在实现后补复述代码的单元测试。

1. 双单位 A/B：对数据模型表中的每张表及 `memory_embeddings` 验证 SELECT/INSERT/UPDATE/
   DELETE、缺上下文、伪造复合 FK、owner/task/card 跨绑、epoch/向量侧信道；不可变表的
   UPDATE/DELETE 在同单位也拒绝。每一条上表 HTTP 路由，包括列表/cursor、history、
   used、job status/wait/cancel、feedback/eval、preview，都以 A 访问 B 资源验证 404 或
   无 B 行；不只测试 memory 主表。后续全局只读发布需专门测试无单位写入/来源入口。
2. 人工关口：人类 admin 只有 org memory 审批权，不据此获得技术/商务证据确认权；
   token 申请 evidence:confirm/export/memory 审批管理范围均拒绝，token/agent/worker
   不能构造 active/confirmed_by；API 与直接 SQL 均验证。candidate/disabled/expired/
   deleted 从不进入提示词；记忆 ref 不能成为 Evidence。并发 approve/update/disable、
   同冲突键审批和乐观锁无半提交。用户同账号跨组织、他人偏好、项目非成员与归档测试
   随各层启用作为必过 gate，首片先验证未启用层被拒。
3. 无向量真实本地链：创建 → 审批 → 精确/中文 keyword/tag → 起草 fake → 驳回/编辑 →
   一个 system candidate 和样本 → 人工审批后影响下一次起草。验证 reject/edit/confirm
   三种来源、model_job_id 继承、空编辑、敏感-only、超长、重复派发、enqueue 中断、
   partial/cancel/retry/lease takeover；全过程不调用真实厂商。
4. 检索与缓存：固定合成规则验证排序、两类优先级、低层遮蔽、同级冲突、预算完整条目；
   无命中后新增、未选 top_k 规则变动、到期后次优项、修改归属企图、scope/模型变化、
   缓存重用与 concurrent disable。准入前/HTTP 在途/发布前各注入变更，记录确切 call
   manifest，旧 worker 不发布且已发用量只扣一次；人工已确认卡仅提示，证据失效照旧拦截。
5. Provider 与计费：fake Embedding 验证组织+scope 下推在 distance/limit 前、条数/维度/
   NaN/零向量/模型错配拒绝、无配置不隐式降级。起草记忆新增字符进入成本上界；余额不足、
   已计费非法输出、usage unknown、重复结算和多 worker 并发保留原预付费规则。
6. CLI/API：每个新增命令做七键快照，覆盖正常/空/参数错/404/409/503/partial 和 exit
   0/2/3/4/5；本地与远程输出相同、无交互、schema registry 匹配。包含 wait/status/cancel
   的访问控制和累计成本。把人工动作派发失败后的可恢复状态作为独立端到端用例。
7. 工件与评测：端到端输出可重复的合成 JSON 报告到 `data/work/memory-validation/`，
   包含输入样本版本、命令、结果 hash、关联 ID 和断言，不保存凭据，不写 `docs/`。
   `MemoryEvalCase` 固定 relevant/forbidden IDs、预期排序/错误；报告 recall@k、MRR、
   priority/隔离违反数、candidate 人工采纳率、敏感内容拒绝、缓存误命中、费用和耗时。
   隔离/人工门禁违反数必须为 0，召回目标待基线实测。真实 Embedding/LLM 服务只在
   `evals/` 显式启用；公共合成样本可入版本库，单位反馈不自动外传，真实材料另需同意。

## 已定决定

下表采用已批准的推荐默认值。后续能力仍须满足各项启用条件。

| 决定 | 已批准选择与边界 |
| --- | --- |
| 第一条完整链路 | org keyword、确定性 candidate 与起草追溯；user/project 等待私人输出与任务成员机制 |
| 全局来源 | 独立公开来源，逐条记录出处、授权/适用范围与版本；禁止单位反馈汇总、提升或流入 |
| 全局审核人 | 采编与领域审核两个平台人类身份；平台身份不得读取单位业务内容 |
| 全局存储例外 | 后续另立 ADR 并修改全局表例外后启用；首片关闭 global，不建立全局存储 |
| 人工新增 | 所有新增先 candidate，再由 org admin 会话明确 approve 确切修订 |
| 自动候选 | 确定性复制净化反馈并标明待泛化；不增加模型调用，不自动生效 |
| 已确认响应 | 保留确切人工决定与 lineage 并提醒；未审模型稿需编辑或重新生成，证据原有 gate 继续生效 |
| 用户偏好与共享卡片 | 后续默认只用于私人输出，先解决 job/card/manifest ACL |
| 默认到期与删除 | 可显式 expires_at、默认无 TTL；逻辑删除保留历史与审计 |
| Embedding | 首片不配置、不调用、不创建向量索引；接口保留，P03 完成模型/维度/价格/驻留选择后另行启用 |
| 自动评测样本 | 仅单位内、默认 unreviewed；接受只允许单位内评测，不隐含对外分享 |

首片不建立可选的 `memory_embeddings` 空表，因此迁移不安装或假定 pgvector；
未来创建 vector 列前必须按[索引预检要求](#pgvector-与后续索引)确认扩展已经安装。

实现检查沿用[开发指南](../guides/development.md#run-the-checks)。运行契约由
[CLI 契约测试](../../server/tests/test_memory_cli.py)覆盖，数据库与调用边界分别见
[存储测试](../../server/tests/test_memory_storage.py)、[API 测试](../../server/tests/test_memory_api.py)、
[反馈链路测试](../../server/tests/test_memory_feedback.py)、[起草链路测试](../../server/tests/test_memory_drafting.py)
和[检索评测](../../server/tests/test_memory_evaluation.py)。
验证工件只写 `data/work/memory-validation/`，不写入文档目录。
