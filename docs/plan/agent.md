---
kind: plan
---

# 契约草案：内置 agent 的命令编排与人工暂停

状态：**待批准，未实施。** 对应[路线图](roadmap.md) A01；调用来源标记同时供 A02 使用。

依照[项目规则](../../agent.md#工作方式)，先确认 Pydantic、Provider 和 CLI JSON 契约，
再实施。[契约模型](agent/agent_contracts.py)仅供审阅，未注册到运行 API、CLI 或 worker，
也不创建表。本页的选择均为提案，待决定项集中在[待决定](#待决定)。

## 目标与边界

[设计文档的 agent 部分](../AI%20标书工具设计文档.md#看板与-agent-设计)要求理解意图、
派发命令、有限重试、汇总、服务器状态恢复和超预算先问人。内置 agent 与控制台、外部
agent 共用 CLI 命令的请求和 Result，不具备证据确认、修改已确认卡片或导出权限。
执行边界沿用 [sandbox.md 的 agent 工具边界](sandbox.md#后续内置-agent-工具边界)。

首条链路从**一个已有任务、真实招标 Document 和成功的抽取 job**开始：
人提交目标及限额 → agent 读取该抽取的要求和卡片 → 预检并提交 `card generate` →
等待已有生成作业 → 展示提案并暂停 → 人通过原有卡片入口审阅、提交及确认 →
agent 重读状态，调用 `draft` 组装已确认响应 → 返回初稿引用与未解决缺口。
生成提案保持 `draft`，未确认证据不得进入响应表；初稿有缺口时如实返回部分完成。

这条链路覆盖工具发现、付费决策、工具子作业、人工关口、恢复与来源审计；不要求人先把
整个任务确认为完成。已有卡片人工动作及职责不变，也不增加“agent 批准即确认证据”。
任务预算和单位额度的定义、预检、批准、预留及失效机制只由并行 **contract-budget**
草案 `docs/plan/budget.md` 定义。该文档未随本草案交付；实现前必须完成依赖契约并确认
接口，不能以单 job 限额或正余额替代。A01 只保存其不透明引用并呈现待办。

首片不做任务创建、上传/解析/重新抽取、自动采集、原型生成、check/score、记忆、MCP、
跨任务代理、并行工具、SDK 框架、长驻 shell、代码解释器或浏览器。后续工具必须逐项
补充输入读取集合、副作用、费用、取消与恢复契约。首片不启动沙箱的 `agent_tool`
profile；已有沙箱支持不表示任意命令可以执行。网页/PDF/工具输出中的指令始终是数据。

## 依据与设计差异

| 已有依据 | 拟复用点与必须补齐的部分 |
| --- | --- |
| [schema.py](../../cli/bid_cli/schema.py) 的 `COMMANDS`、`command_schema()`；[main.py](../../cli/bid_cli/main.py) 的 `emit()` | 注册表只公布已实现命令；请求体 `input` 和 `cli_parameters` 分开，读命令的 `input` 可为 null，专门的 `output` 只覆盖部分命令。尚无可直接执行的完整 agent 工具 schema；需按下文补足，不能把 null 当成任意参数 |
| [contracts.py](../../server/app/schemas/contracts.py) 的 `Contract/Result/Cost/ProviderUsage` | 直接导入，不复制七键 envelope。版本取 `CONTRACT_VERSION`，不在此固定现有版本号 |
| [auth.py](../../server/app/services/auth.py) 的 `Identity/authenticate/membership/set_actor_context`；[core/db.py](../../server/app/core/db.py)、[core/security.py](../../server/app/core/security.py) | `core` 提供事务隔离和签名/加密原语；角色和 scope 实际在 `services/auth.py`。现有 token 是范围与当前角色求交集；新增内置 agent principal 和持久授权不能假装已经存在 |
| [jobs.py](../../server/app/services/jobs.py) 的 `status/cancel`；[processor.py](../../server/app/jobs/processor.py) 的 `Processor`；[execution.py](../../server/app/jobs/execution.py) 的 `JobExecution` | 保留业务 job、取消、租约、心跳与 `run_id`。尚无 agent 状态机及业务恢复巡检；`job status/cancel` 必须增加 agent 会话访问校验，不能成为旁路 |
| [entities.py](../../server/app/models/entities.py) 的 `Job.job_document_binding` | 除 provider_test 外必须绑定 task/document；A01 从同任务成功 extraction 取得真实 document，不伪造 Document、不放宽该约束 |
| [base.py](../../server/app/providers/base.py) 的 `LLMProvider`；[llm.py](../../server/app/providers/llm.py) 的 `HTTPExtractor.post/resolve_llm/with_reasoning`；[structured.py](../../server/app/providers/structured.py) 的 `json_request/json_call/strict_schema` | 已有协议只有 extract/draft；起草等能力已用结构化 HTTP 调用。新增 `decide`，不把设计中的通用“消息 + Schema”接口说成已有实现 |
| [execution.py](../../server/app/jobs/execution.py) 的 `JobExecution.admit/complete`、`job_cost`；[calls.py](../../server/app/providers/calls.py) 的 `accounted_call`；[billing.py](../../server/app/services/billing.py) 的 `require_funds` | 复用逐次 UsageRecord 和预付结算。余额、单 job charge/call ceiling 已有，任务预算执行属于 contract-budget；不能靠反复创建 agent job 重置累计上限 |
| [response_cards.py](../../server/app/services/response_cards.py)、[card_generation.py](../../server/app/services/card_generation.py) 的 `submit_generation/check_input_access`、[drafts.py](../../server/app/services/drafts.py) 的 `submit_draft/show_draft` | 复用受保护卡片跳过、版本校验、固定输入、费用预检和仅消费确认内容；不让 agent 改写这些规则 |
| [versioned.py](../../server/app/services/versioned.py) 的 `audit()`；[entities.py](../../server/app/models/entities.py) 的 `AuditLog` | 已有 user/token 和业务审计，尚无统一调用审计与可约束的 agent 来源列；A01/A02 共享下文来源模型 |
| [保密机制](../notes/confidential-values.md)、[模型输入规则](../notes/model-drafting-redaction.md) | 现有关闭任务遮挡会同时关闭登记值替换。A01 不允许把此状态用于 agent 外发；首片要求遮挡开启且每次准入重验，不自动修改开关 |

设计中的 `evidence fetch/stamp` 没有对应完整同名命令；取证与标注已有分阶段命令，
`check/score` 未注册。它们不进入首片工具表。设计先用外部 CLI agent、再考虑 SDK，
本提案选择现有 worker 加窄 Provider，不引入 SDK。设计的卡片恢复也不足以恢复已付费
模型决策和提交中的子作业，须新增持久步骤记录。本地模式仍为 PostgreSQL/RLS，
不采用设计早期描述可能暗示的纯文件存储模式。

## 接口

所有路由经既有认证、`X-Org-Id` 和单位事务。`S/T` 为 UUID；body 不接收 org、owner、
principal、actor、有效 scopes、job/run ID 等服务端归属字段。新入口与模型均为提案。

| HTTP | CLI（均支持 `--json`） | 输入与 Result 内容 |
| --- | --- | --- |
| `POST /tasks/{T}/agent-sessions` | `bid agent start --task T --input REQUEST.json [--dry-run]` | `AgentStartRequest`；`data=AgentMutationData`，只受理并返回 job；dry-run 为 `AgentPreviewData` |
| `GET /tasks/{T}/agent-sessions` | `bid agent list --task T [--cursor ID] [--limit N]` | `AgentListRequest` query；`data=AgentPageData`，`items=AgentSessionView[]` |
| `GET /agent-sessions/{S}` | `bid agent show --id S` | `data=AgentShowData` |
| `GET /agent-sessions/{S}/messages` | `bid agent messages --id S [--cursor ID] [--limit N]` | 分页；`items=AgentMessageView[]` |
| `GET /agent-sessions/{S}/steps` | `bid agent steps --id S [--cursor ID] [--limit N]` | 分页；`items=AgentStepView[]` |
| `POST /agent-sessions/{S}/messages` | `bid agent message --id S --input REQUEST.json` | `AgentMessageRequest`；仅 paused 接受，追加人类消息但不自动续跑；`data=AgentMutationData` |
| `POST /agent-sessions/{S}/resume` | `bid agent resume --id S --input REQUEST.json` | `AgentResumeRequest`；校验暂停和当前事实后排队；`data=AgentMutationData` |
| `POST /agent-sessions/{S}/cancel` | `bid agent cancel --id S --input REQUEST.json` | `AgentCancelRequest`；持久取消会话及其自有在途 job；`data=AgentMutationData` |
| 既有 `GET /jobs/{J}` | `bid job status/wait J` | 控制作业的 `data.result=AgentJobResult`，扩展该 kind 的访问守卫；子 job 保持原契约 |
| 既有 `POST /jobs/{J}/cancel` | `bid job cancel J` | 控制作业取消等同会话取消；独立取消自有子 job 会让会话停止派发并报告取消原因 |
| 既有 schema 入口 | `bid schema --json` | 新增 agent 命令及下节工具调用结构；schema 发现不授予权限 |

消息内容从 JSON 文件读取，不做终端交互式提问。命令缺参退出 2。首片新增命令不另加
长轮询 `--wait`；show 展示会话状态，既有 job wait 只等待指定作业片段，不能把片段完成
当作会话完成。列表游标按 `(created_at,id)` 稳定排序，由同一查询范围内的 ID 定位；
跨单位、跨任务、别人的会话游标与不存在的游标均 404。

修改须 `expected_revision` 和调用方生成的 UUID 幂等键。幂等记录随主记录持久化：
相同主体、同端点、同键、同规范化请求重放原回执；同键异参为 409/exit 2。先查重再查
expected_revision，避免成功后的网络重放被误判冲突。首次 start 的 `task + owner + key`
在本单位唯一。dry-run 不消费幂等键，不产生会话、审计、用量、对象或队列记录。

## CLI 工具映射

工具名严格等于注册的 CLI 命令名，一个工具只表示一个命令；不提供万能 `run(command)`。
`AgentToolProvider` 是可信 broker 协议，不是模型执行 shell 的接口。参数必须通过
`ToolCall` 的命令分支、当前注册表 Schema 和业务服务三层校验。CLI 参数的文件路径只
存在于 CLI 读入环节，broker 接收类型化 body，不能读取模型指定的本机文件。

| 工具名与参数模型 | 既有 CLI / HTTP 映射 | 最小 scopes 与行为 |
| --- | --- | --- |
| `req list` / `ExtractionArguments` | `--task --job` → `GET /tasks/{T}/requirements?job=J` | `task:read`；固定本会话 extraction；不允许省略 job 而悄悄读取新的抽取 |
| `card list` / `ExtractionArguments` | `--task --job` → `GET /tasks/{T}/cards?job=J` | `task:read, card:read`；读取该抽取的卡片槽位 |
| `card show` / `CardShowArguments` | `--id [--history]` → `GET /cards/{C}?history=...` | `task:read, card:read` 加服务的材料访问校验；必须属于固定任务/抽取 |
| `card generate` / `CardGenerateArguments` | `task` 对应 `--task`；`input: CardGenerateRequest` 的 extraction_job_id 对应 `--job`，其余按 `card_generate()` flags 映射 → `POST /tasks/{T}/cards/generations` | `task:read, card:read, card:generate` 及固定材料的 read grants；先 dry-run，再绑定 expected_input_hash/max_charge 提交原有 job；外发仅原 Provider 路径，提案恒未确认 |
| `draft` / `DraftArguments` | `task` 对应 `--task`，`input: DraftRequest` 对应 `--job/--dry-run/--retry` → `POST /tasks/{T}/drafts` | `task:read, card:read, draft:run` 及材料读取；已有组表 job；零模型费用，缺口仍可形成 partial 初稿 |
| `draft show` / `DraftShowArguments` | `--id` → `GET /drafts/{D}` | `task:read, draft:read` 及原服务依赖校验；只读本会话关联初稿 |
| `job status` / `JobStatusArguments` | 位置参数 J → `GET /jobs/{J}` | `job:read` 及该 kind 的动态权限；只读本会话控制/子作业及固定 extraction，排除任意 job、export/provider_test |

材料 read grants 从真实 `check_input_access`/材料解析获取，不能只靠上表三项固定 scope；
首片 `AgentScope` 仅允许这些路径所需的读取与生成/组表范围。被保护、pending、confirmed
或人工 comply-only 卡片继续由原服务跳过；负偏离不得被模型改成满足。工具参数中的
task、job、card、draft、requirement ID 都重新验证父链，不因来自之前一次响应就受信任。
模型不能控制 `--wait/--timeout`、认证、URL、方法、重试次数或全局 CLI 配置。
`retry=true` 只有 broker 验证原 job 可安全重试时接受，未知调用不重试。生成请求中的
expected_input_hash 必须来自本步骤预检；max_charge 由 broker 按预检、已批准预算和
会话剩余金额校验/收紧，模型填写这些字段不构成授权，也不能增加原来批准的金额。

实施时在 `command_schema()` 增加向后兼容的 `invocation_input`：路径/查询选择器加原有
Pydantic body 的完整 JSON Schema；保留原 `input/cli_parameters`。工具表中的参数模型
就是对应 schema 的草案，`req list` 仅在 agent profile 收紧为显式 job。控制台和外部
调用可发现同一结构；CLI 保持已有 flags，以确定性映射形成该对象。完整 schema 随
Result schema、命令名生成哈希，保存到会话和步骤。升级后哈希不符须暂停重新预检，
不得用旧计划配新 schema 执行。批准后做注册表、CLI flags、broker、API 的一致性快照，
不维护第二份手抄工具定义；不兼容变更依项目规则升主版本。

broker 在进程内调用**与 API route 相同的认证后命令服务**，返回同一 Result 和退出码，
不 fork `bid`、不构造任意 HTTP、不复制业务算法。HTTP 与 broker 共用提交事务边界，
以便子 job 与步骤关联原子提交。它仍执行 `Identity`、单位事务、材料/卡片守卫、预算和
审计；不能把“进程内”解释成跳过 API 安全层。首片工具输出保持既有结构，发给模型的
只是一份受限、遮挡后的投影；投影不冒充原始 Result，也不作为证据保存。

## 身份、权限与人工关口

新增持久 `agent_principals`，由已认证人类 session 在 start 时创建，绑定有效 Membership、
user、org、授权到期和 scope 快照。`actor_kind=agent`，没有可复用的用户 Bearer/Cookie。
每步有效权限为 **创建时人类 grants ∩ 当前 Membership/角色 grants ∩ 请求缩减范围 ∩
服务端 A01 白名单**，任何一方缺失即拒绝。以后增加角色权限不自动扩张既有会话。
即使发起者是管理员/bidder，也永久删除确认、导出、保密值写入/揭示、provider/token/
资源管理、红线开关与人工处置权限；只减 scope 不足以保护人的职责，还须保留 actor_kind。

新增 `agent:read/run/cancel` 只授予人类：admin/bidder/technical 可 run/cancel，viewer
只有 read；首片会话只由发起人读取和控制，其他同单位成员也返回 404，不默认给管理员
会话旁观权。run 还须具有上述首片业务 scopes；已有角色本身不会保证所选材料可读。
人因降权不能续跑，但有效所有者保留撤销自己会话的能力。退出单位或单位停用立即封闭
worker 准入，且不能借仍有效 principal 绕过 Membership。API token 不可申请这些
agent 管理 scopes，不能创建内置代理或嵌套代理；A02 继续使用原命令范围令牌。

API token 永远不能有 `evidence:confirm`、`export`、`confidential:write/reveal`，沿用
[tokens.create_token](../../server/app/services/tokens.py) 和数据库约束。内置 agent 不使用
人为签发的 API token 伪装发起人；子 worker 的即时 actor_kind 保持 worker，另携带不可
变的 agent 来源。所有 human-only 服务与数据库门禁都明确拒绝 agent/token/worker。

授权期限取发起 session 的剩余有效期与 8 小时中较短者；只保存签发/到期元数据，
不存原 session 凭据。已有签名会话并无持久撤销 ID，因此不宣称浏览器退出会立即撤销
委托；显式 cancel、授权到期、成员/单位失效会阻止新调用。到期进入 authority pause，
只有同一用户的新有效 session 可 resume，重新缩减 grants，仍不增加累计限额。

人工暂停使用单独 `agent_pauses`，不是向终端提问。模型可提出 `HumanActionNeeded`，
系统也可因门禁创建暂停；question 和结果须脱敏、限长。`export/confidential_reveal`
只能产生给人的固定动作指引，不执行对应命令；完整保密值绝不回流对话或模型。用户
通过原有业务界面或 CLI 自行操作，resume 只重读事实并解除相应暂停，不代行确认。
`review_cards` 的响应列表记录 card/revision ID；发现新版本/新输入先刷新待办，不把一句
“已批准”视为 confirm。用户可明确结束审阅并让已有 draft 服务如实列出缺口。

## 数据模型与迁移轮廓

新增下表六张业务表，**每张均有 NOT NULL `org_id`、UNIQUE(org_id,id)、ENABLE/FORCE
RLS、带 USING/WITH CHECK 的策略**。按[单位隔离机制](../notes/tenant-isolation.md)，
使用 `Database.transaction(org_id)` 与 transaction-local `app.current_org`；缺上下文
不可读写。运行角色非属主、无 BYPASSRLS/SUPERUSER，不使用跨单位特权扫描恢复。

| 表 / 公共 view | 存储内容与约束 |
| --- | --- |
| `agent_principals` / `AgentPrincipalView` | owner user、membership、授权初始 grants/缩减 scopes、签发与到期、revoked_at；membership 与 user 必须匹配。初始 grants 不可改，续权只更新缩减后的有效范围与期限并审计 |
| `agent_sessions` / `AgentSessionView` | task/document/extraction/principal、owner、state/revision、limits、步骤/调用数/活动时间、active_since、固定 schema/model/input 哈希、当前控制 job/run/pause；start 与唯一终态 cancel 分别保存请求哈希/幂等键/加密回执；终态不可重开 |
| `agent_messages` / `AgentMessageView` | session、递增 ordinal、role/author/step、加密内容、脱敏 content 与 hash、幂等键/请求哈希/回执；UNIQUE(org_id,session_id,ordinal)，只追加，不保存推理链 |
| `agent_steps` / `AgentStepView` | decision/tool、序号、稳定 invocation_id、不可变 created_by_job_id/run_id、step revision 和 last_transition_job_id/run_id、固定调用结构/参数哈希/input refs/schema 哈希、child job、状态/结果哈希/退出码/usage IDs；调用结构和完整回执加密，公共 view 不含参数原文；UNIQUE(org_id,session_id,ordinal)、UNIQUE(org_id,invocation_id)；终态不可覆盖 |
| `agent_pauses` / `AgentPauseView` | 类型、待办对象、输入快照、脱敏问题、contract-budget 不透明引用、状态/处理人/时间；resume 幂等键及原回执；每会话至多一个 pending 暂停，只有受权人可解决 |
| `agent_job_links` / `AgentJobLinkView` | session/step/job、controller/tool、owned、创建时间；同会话同 job 唯一。部分唯一索引 UNIQUE(org_id,job_id) WHERE owned=true，归属与新 Job 同事务创建；owned=true 聚合该 job 全部用量，历史缓存只读引用为 owned=false，永不计入本会话消费 |

所有父链采用含 org 的复合外键：principal → `(org_id,membership_id,user_id)` 的
Membership 候选键；session → task、`(org_id,task_id,document_id)` 的 Document 候选键、
`(org_id,task_id,document_id,extraction_job_id)` 的 Job 候选键及同 owner 的 principal；
message/step/pause/link → 同单位 session；step/pause 相互引用时包含 session_id；
link/step child_job → 同 org/task/document 的 Job，不能混绑另一任务。
必要的父表复合 UNIQUE 约束随迁移添加。User 仍为全局身份表，业务归属通过 Membership
校验，不能把一个全局 user FK 当作单位成员证明。extraction 必须 kind=extract 且成功，
类型/状态与当前 revision 由服务和数据库 trigger 共同验证，RLS 本身不保证同单位同任务。

没有 pause 的 queued/running/waiting_job 取消也将 cancel 回执写在 session；同键重放
原回执、异参冲突。已经终态后另一 cancel key 返回 terminal_session/exit 2，不新增
伪暂停。start/cancel/message/resume 的键各按主体和端点隔离，不使用无界 JSON 回执列表。

控制 job 增 kind `agent`，保留 job_document_binding。复合引用 current_job/pause 等
用可延迟外键完成同一事务初始化；历史 rows 不 cascade delete。step/消息/暂停内容只能
由符合真实 actor 上下文的写入路径生成，禁止客户端注入 assistant/tool 角色、篡改已完成
step 或自填 resolved_by。view 采用显式字段白名单，不用 ORM 任意序列化。

迁移顺序：增加父候选键及六表 → 建立 RLS/复合约束/只追加与状态 trigger/最小列授权 →
增加 jobs 与审计归属字段及人工门禁 → 回填可证实的历史审计来源 → 同次交付逐表双单位
和缺上下文测试 → 再启用入口。新增审计关联也使用 org 复合外键，见下节。未知历史来源
标为 legacy_unknown，不猜成 agent。业务文本复用 `core.security.Secrets` 加密并绑定
org/session/step；加密列加入 [admin.py](../../server/app/admin.py) 的轮换清单。没有新的
对象存储文件格式；如后续存附件，仍须 `org/{org_id}/` 和既有签名下载契约。回退关闭入口
与 worker 分支、保留历史和账本；不靠 downgrade 删除会话、审计或费用。

## Worker、状态恢复与取消

循环运行在现有 Procrastinate worker 的 `agent` 控制作业中，API 只做验证、持久化和派发。
每个控制 job 执行至一个有持久 checkpoint 的边界后退出；等待子 job 或人时不占 worker
槽位，也不持数据库事务。成功的控制 job 只代表 checkpoint 已保存，完整状态看 session。
checkpoint 事务在 live run fence 下先发布 session/step，然后将该控制 Job 置 succeeded、
写 finished_at/AgentJobResult、清 lease_until 和 session.current_job_id/current_run_id。
非终态会话的 result.completion=null，以 disposition 表示 continue/waiting_job/paused；
仅会话 completed/partial 时分别为 complete/partial。下一次 wake 创建新的控制 Job，
不能把已正常结束的片段当作失联 running 重跑。

| 会话状态 | 进入/离开条件 |
| --- | --- |
| queued → running | 新建或恢复控制 job；行锁读取 session/current_job，`Processor` 取得新 run_id 与 lease |
| running → waiting_job | 同一提交事务持久 step、子 job、关联、session revision；控制 job 正常结束 |
| waiting_job → queued | 子 job 终态后回收器唤醒；重读真实 Result，完成 step，再排下一控制片段 |
| running → paused | 预算、人工动作、授权失效或不确定结果；checkpoint 与暂停原子提交，释放 worker |
| paused → queued | 同一人类所有者 resume；输入版本/权限/预算/剩余限额重验通过 |
| running → completed/partial/failed | 完成目标；有已发布的独立有效成果但仍有失败为 partial；无成果失败为 failed |
| 任一非终态 → cancelled | 持久取消先封闭新准入，再取消本会话拥有的在途 job；不删除已发布提案和费用 |

每次决定和每次工具执行各占一个 step，失败、dry-run 和重试也占额度；幂等重放不新建
step。计划输出先验证并加密保存，再执行其中唯一工具；参数包含显式默认值，以排序键的
UTF-8 JSON（无多余空白）计算 SHA-256，连同原始字节哈希、工具 schema/hash 固定。
不把模型生成的 UUID 当作可信 invocation ID：ID 由 broker 生成，固定到 step。

现有 `Queue.enqueue` 使用独立连接，不能覆盖提交后入队前的崩溃窗口。A01 须增加
事务内派发适配：**业务 Job、agent step/link、幂等回执、提交审计和 Procrastinate
持久 task 记录使用同一 PostgreSQL 事务**，通知随提交可见，失败一起回滚。已安装的
Procrastinate `Task.configure(connection=...).defer_async` / `JobManager.defer_job_async`
接受外部连接；适配从 SQLAlchemy 当前事务取得底层 psycopg 连接，只借用、不另开事务、
不自行 commit/close。接口归于 [queue.py](../../server/app/jobs/queue.py)，锁定依赖见
[uv.lock](../../uv.lock)；批准后须用真实 PostgreSQL 验证连接与回滚，不以接口存在宣称
已完成。原有非 agent 命令的队列路径不因此被宣称已自动恢复。
外部工作不在事务中；命中已有 job 时记录 reuse。
模型没有执行任意数据库语句的能力。没有原子关联或可查询幂等回执的写命令不得纳入工具。

所有 step/session/checkpoint 发布与失败处理都检查 session 未取消、expected revision、
current_job_id/current_run_id、该 Job 的 live lease，并对 step 的 revision/state 做 CAS，
再检查当前权限与输入。created_by_job_id/run_id 永远保留最初来源；新控制片段可以在
自己有效 claim 下推进 waiting_job → completed，更新 step revision/last_transition，
不能用旧创建 attempt 的 lease 拒绝合法恢复。child job 的 run_id 单独从其 Job 读取。
终态 step 不可修改，过程转换及来源另写审计。仅检查
最后一个 session update 不够，子 job 提交前也须 fence。旧 attempt 可按真实用量结算，
不能继续派发、记录成功 step 或覆盖新状态。沿用
[后台作业机制](../notes/background-jobs.md)的 heartbeat 与取消，不另造一套租约。

新增启动及最长 30 秒周期的业务恢复器，以内部 Procrastinate 的 `bid.agent_wake`
任务作为持久唤醒链：start 与每个非终态 checkpoint 同事务保存下一条 wake（仅含
org/session IDs，带有界 scheduled_at）；前一个 wake 只有在后继记录提交后才完成。
恢复器可扫描这些内部队列元数据的 todo/doing 记录，对失联 wake 重新调度；活动会话
对应的最后一个 wake 在业务终态前不得被队列清理。崩溃在提交前全部回滚，提交后则
必有 wake；无需先成功 enqueue 才发现 org，也不引入跨单位业务查询例外。
获取 IDs 后逐一 `Database.transaction(org_id)` 检查会话、锁定 queued、过期 running、
waiting_job 与待推进 checkpoint；模型不能写队列。paused wake 仅查到期，不调用模型。
恢复器本身由 worker 正常生命周期持续调度；DB 不可用时保留记录，恢复后接续，不承诺
停机时仍在 30 秒内推进。延期投递、派发回滚和重复唤醒均保持幂等。

恢复先读 step 和关联 job：已完成步骤直接复用；已提交子 job 重连其状态；仍持有 live
lease 的 job 不接管；过期 job 仅在其既有 retry 规则内恢复，累计调用、花费和输入不清零。
等待 job 期间的控制片段不持锁等候，单 worker 槽也不会因父 job 等子 job 死锁。
已结算且已保存的模型决定不再生成。若请求已准入但决定未持久保存，或 VendorCall 为
pending/unknown，标 step=uncertain 并暂停 recovery，保留预约；不能声称跨厂商 HTTP
exactly-once 或静默再付一次钱。恢复须核实账本和已存结果；无法确认的步骤终止，由人
另开会话。A01 不新增自动对账或释放未知预约的权限。

`services/jobs.status/cancel` 必须对 kind=agent 及关联子 job 追加所属会话/当前身份检查。
所有取消途径都关闭父会话准入；仅本会话 owned=true 的 job 才可自动取消，复用历史 job
不得连带取消其他工作。已准入调用可在原供应商 deadline 内结算，随后禁止业务发布；
取消不是退款，也不能中断记账后假报零费用。

## Provider、上下文和计量

`AgentReasoningProvider` 扩展既有 `LLMProvider`，新增
`decide(AgentDecisionRequest) -> AgentDecisionOutput`；一次返回 `tool/human_action/complete`
之一，不提供推理链字段、不并行执行多个工具。`AgentToolProvider.definitions/invoke/recover`
负责工具发现、受权提交和幂等恢复；`AgentRecoveryProvider.wake` 仅是内部调度协议，
不暴露为工具。协议声明不是实现，也不是允许在业务模块中导入厂商 SDK。

拟实现放在 providers 下，经 `resolve_llm/with_reasoning` 选择并固定模型/配置/价目修订，
用 `structured.json_request/json_call`、`HTTPExtractor.post`、`accounted_call`。现有
json_call 只返回 wire 结果、不返回其已结算 usage；拟在 Provider 内增加返回
`(validated_decision, trusted_provider_usage)` 的共用 helper，并让原 json_call 保持原返回
契约。**模型输出 schema 仅为 AgentDecision**；adapter 用 HTTP 返回的可信 usage
组装 AgentDecisionOutput，绝不让模型填写 ProviderUsage，也不把该 wrapper 作为 wire。
供应商
strict schema 必须由选中工具的具体分支展开，不能直接对任意 dict 参数调用
`strict_schema`：该函数会封闭对象并破坏开放字典含义。工具定义中的 JSON Schema 是
输入给模型的受信数据，返回值使用 `AgentDecision` 的闭合、带 discriminator 的结构。
未知命令/额外参数/伪造结果均拒绝；有限网络重试最多沿用三次实际请求，仍逐次计量。

模型仅收到本会话用户消息、服务端筛选的当前 task/extraction 数据、已验证工具结果
投影、工具 schema 和剩余限额。每次外发先替换登记值，再执行
[redaction.py](../../server/app/services/redaction.py) 的遮挡；要求遮挡开启并固定其修订，
关闭或变化会暂停。原文件、签名 URL、值尾号、凭据、无关材料、整段 job.submission 和
原始厂商输出不进入上下文。对话保存加密原输入，公共消息只返回脱敏内容；拒绝日志中的
正文回显。当前消息最长 8,000 字符，单结果投影上限 64 KiB，单次模型请求序列化上限
128 KiB；超限暂停要求缩小选择，不静默截断引用或假装读完全文。模型最多接收 40 条
消息；达到边界首片暂停，不调用额外模型做不可审查的自动摘要。不实现记忆检索，
因此不伪填“已使用记忆”。人类可读概要和工具回执可以保存，隐藏推理过程不保存。

每次模型 HTTP（包括决策、子作业生成、重试、被拒、截断和取消后的响应）走
`HTTPExtractor.post/accounted_call → JobExecution.admit → VendorCall → HTTP 请求 → complete → UsageRecord`。
响应先记账再解释；唯一键沿用 `(org_id,job_id,run_id,call_id)`。ProviderUsage 直接导入；
在 job 执行上下文中已即时结算，AgentDecisionOutput.usage 只描述该次调用，消费者不再
插入一条 UsageRecord。没有真实供应商调用的读工具/组表不创建空用量记录。

父会话费用是**直属 decision 用量 + 自有工具子 job 用量的 ID 去重汇总**；不复制
UsageRecord、不额外扣余额。每个控制 job 的 `AgentJobResult.cost` 只含它自身费用，
会话聚合放 `AgentSessionView.cost`，不能相加重复统计。历史缓存命中 `owned=false` 的
新增费用为零，原 job 可保留历史费用供查看但不进入本会话消费。仍在运行的别人 job
首片不接管；它完成后只能引用结果，不能取消或让其账单属于两个会话。

供应商 USD 与平台结算币种分开：Result.cost.usd 是供应商成本；平台 charge、未结
预约、币种与 unpriced/unresolved 计数在 `AgentCostView`。单位自带模型仍记录
provider_config_id、token 和成本，平台 charge 为零；不能因零平台 charge 无视外部费用。
价格未知保留 null，首片不准入无法强制成本上界的付费调用；未知费用不能变成 0。
准入、结算失败及实际费用突破预约的处理沿用
[预付账本](../notes/prepaid-billing.md#admission-and-the-spending-bound)，不声称可以强制
不遵守 token 上限的供应商。超预约仍记真实费并停止后续调用。

## 预算暂停与硬限额

任务预算只依赖 **contract-budget / `docs/plan/budget.md`**。A01 需要它为固定计划、
输入/模型/价格修订及已结/未结调用提供预检与逐调用准入，返回需要人处理的引用；
这些接口名称、额度字段、审批主体、预留规则及锁顺序由该稿定义，不在此另建预算模型。
`BudgetDependencyRef` 仅持有 question/revision 引用，不是票据、凭据或审批决定。

在首个付费决策前也执行预算预检；之后每个工具先调用其原 `--dry-run`，计划变化、
重试、拆批和子 job 内部请求均通过预算依赖重新准入。收到需人工处理结果时，先持久
pause 和已知计划摘要，再让 API/CLI 展示问题，不发送超预算请求。若依赖未可用，返回
`budget_dependency_unavailable`，不能临时用 task.budget_usd 自行计算。现有 dry-run
只有首轮费用上界，不能当作整个 agent 计划的最终费用承诺。

人通过 budget 契约的独立入口调整或批准，resume 仅带其引用并在服务端验证当前状态、
主体、计划和修订。模型文本、过期批准、价格或输入改变不能续用旧决定；单位余额不足
仍由已有预付账本拒绝，人的预算回答不能绕过欠费。一次拒绝不会触发自动换模型或拆小
调用来规避询问。被预算服务永久拒绝时停止，已有独立成果可以作为 partial 返回。

`AgentLimits` 是本会话额外的不可扩张安全上限，不替代任务预算。推荐默认 24 steps、
32 个实际 vendor calls、900 秒活动时间、24 小时总生存期；服务器最多允许 64 steps、
64 calls、3,600 秒活动时间。成本由人明确填写 max_vendor_usd、max_platform_charge
及系统币种，建议交互初值 USD 2 的供应商成本；平台金额按本单位结算币种显式输入，
不做隐含汇率换算。平台付费不能以 charge=0 准入；单位自带模型可为 0。

这些计数跨控制 job、工具子 job、attempt、恢复与 resume 累计。所有付费后代调用准入
必须同时检查会话剩余步数/时间/费用/未结预约与 task budget，再执行原 job/余额门禁，
不能只在工具提交前检查。共用调用准入扩展要与 budget 契约锁顺序对齐后才能启用 A01。
成本上限同时约束供应商 USD 和平台 charge，未知价格阻止执行。总调用数不沿用单 job
按批次自动扩大的上界来扩大父会话上界。

活动时间含 queued/running/waiting_job、重试退避和故障停机，paused 人工等待不计，
但仍受总生存期约束。active_since 在 DB 持久化，checkpoint 按数据库时间累加，重启
不能把遗失时间当 0。每次真实 HTTP deadline 收紧为 Provider 原期限、剩余活动时间、
总到期时间中最短者；取消/超时后的结算可以完成但不能再次派发。恢复器终止到期会话。
达到硬 step/time/cost/call 上限时终止，不通过“批准”在同会话加大上限；另开会话仍受
同一任务预算与既有未结预约约束。

## 审计与 A02 来源标记

在已有 `audit_logs` 中拟增可索引的 `actor_kind`、`initiated_by`、`agent_principal_id`、
`agent_session_id`、`agent_step_id`、`invocation_id`、`job_id`、`run_id`，保留已有
actor_user_id/actor_token_id。新字段可空以兼容历史；新 agent 事件按 `AgentProvenance`
验证组合关系，所有 tenant 对象外键带 org。历史回填只能使用可靠记录，不能把空 token
一律当成人。审计仍仅追加，业务变更及其成功审计同一事务；失败事件另写，不改写历史。

即时执行者与发起来源分开：builtin_agent 带 principal/session/step，代表当前人类
Membership 的缩减授权；后台发布的 actor_kind 是 worker，initiated_by 仍为
builtin_agent。A02 外部调用必须从已验证 ApiToken 获得 token ID 与 owner，标记
external_agent/token，不能从请求 JSON 或自报 User-Agent 接收身份。首片把 API token
发起的命令统一显示为“令牌自动化”，不声称能识别背后一定是某种模型；具体产品名称
留给 A02 独立契约。A02 不创建 A01 会话、不使用其恢复或预算回答入口。

事件建议 `agent.session.started/resumed/cancelled/completed/failed`、
`agent.decision.started/completed/uncertain`、`agent.tool.submitted/completed/failed`、
`agent.pause.created/resolved`、`agent.recovery.claimed`、`agent.limit_reached`。统一命令层
为 token 调用增加 `command.invoked/completed/failed`，读取调用也留痕；明确 dry-run
保持零写入，不为调用审计破坏它。变更事件保留命令、对象/修订 IDs、哈希、拒绝原因和
费用关联，不保存对话、正文、完整参数、模型输出、凭据、签名链接或保密值。

卡片/生成 run/draft 的公开 view 拟增加只读 `AgentProvenance | null`；下游人工编辑保留
历史 agent 来源并记录新的人工作者，不能洗掉来源，也不把人实际确认标为 agent 确认。
来源从可信 job/step 传递，不靠扫描审计 details 临时猜测。具体数据库记录可通过现有
model_job_id/生成 run 加 org 关联解析，直接工具写入时必须同事务保存来源引用。UI 据此
显示内置 agent 或令牌自动化标记；展示边界作为契约交付，首片不实现 Vue 页面。

## Result、退出码与失败模式

所有新命令外层直接使用 `Result`：**ok、command、data、items、warnings、cost、duration_ms**
七键，不加 status/version/agent 顶层字段。详情和变更的 items=[]；列表 data 只含分页，
items 使用上文公共 views。错误 `data=AgentFailureData`，即 `data.error.code/message/retryable`
加本单位可见的关联 IDs；不泄漏 traceback/输入。模型和工具的内部 Protocol wrapper
不是 CLI envelope。示例中的 UUID/耗时仅表示格式：

```json
{
  "ok": false,
  "command": "agent resume",
  "data": {
    "error": {
      "code": "budget_confirmation_required",
      "message": "预算待办尚未由人处理",
      "retryable": false
    },
    "session_id": "00000000-0000-0000-0000-000000000001",
    "job_id": null
  },
  "items": [],
  "warnings": [],
  "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0},
  "duration_ms": 12
}
```

| 退出码 | HTTP/会话语义 |
| --- | --- |
| 0 | 成功预检/受理/读取/合法取消；queued/waiting_job/paused 的正常查询是 0，明确显示未完成。控制 job 成功仅表示 checkpoint。会话完整完成查询为 0 |
| 2 | 400/422 非法参数，409 revision/幂等键冲突、已终态不能续跑，预算/人工待办尚未处理的 resume；修正输入或先处理待办 |
| 3 | 429/503 队列或 DB 暂不可用，Provider 有界暂时故障；保留会话及幂等键，可重试但不得越过次数/时限。显式查询可重试 failed 会话也映射 3 |
| 4 | 401 无认证、403 无动作权限、404 不存在/无权资源，永久拒绝、取消后的运行结果、硬限额、未知用量/记账失败、协议不符或无法安全恢复；不自动绕过 |
| 5 | 会话或子 job 有已发布独立有效成果，同时存在明确失败/缺口，ok=false；不得把“还在等人”或只有费用无成果当成部分成功 |

`agent show` 对 terminal 会话按其结果给出 0/3/4/5，并仍返回 AgentShowData；历史 list、
messages、steps 的成功读取为 0，内部失败事实不改写。会话聚合 cost 由 session.cost
提供；show 外层 cost 同该聚合，列表/纯控制请求外层为零。本地与远程保持相同语义，
隐私读取失败统一 404，不通过错误差异暴露别人的会话存在性。

| 失败模式 | 处理要求 |
| --- | --- |
| 提示注入、假命令/IDs、敏感读/确认/导出企图 | broker 白名单与父链/actor 双重拒绝；安全动作需求改为人类待办，恶意参数不回显 |
| 选中卡片/材料/模型/schema/遮挡修订改变 | 暂停并重新预检；不能自动扩大输入或覆盖人确认内容 |
| 排队投递丢失、worker 重启、重复派发、旧 lease 返回 | 按持久 step/link 恢复、live run_id fence；重放不重做完成步骤、不再扣费 |
| 未知供应商结果或计量落库失败 | 保留预约、停止并提示 recovery；不自动重发或归零 |
| budget 依赖缺失、超任务预算、平台余额不足 | 分别阻止准入/暂停问人/沿用账本拒绝；互相不能替代 |
| 硬限额、取消、Membership/委托到期 | 封闭后续调用；已有调用结算但不发布；授权到期可在剩余额度内由人续权 |
| Provider 拒绝/非法 JSON/上下文超限 | 固定安全错误，不保存任意输出；有限暂时错误重试不扩大输入/价格/权限 |
| 生成 partial、受保护卡片或 draft gaps | 保存可核验成果及真实缺口，不能自动确认或吞掉失败后宣称完整 |

## 批准后的测试计划

验收从 CLI/API → 真 PostgreSQL/RLS → 真实队列 worker → fake Provider → 人类原入口
→ draft 的端到端链路进行；以下是拟实施测试，本草案的静态检查不证明运行时行为。

1. **逐表双单位**：对六个新表逐一测试 A 读/插入/修改/删除 B 与无 org 上下文、RLS
   FORCE、非属主角色、复合 FK 的跨 org/同 org 跨 task/session 混绑。测试新审计关联和
   job 链接、只追加/终态触发器、真实 actor、scope 缩减与 owner-only，不只测 SELECT。
2. **逐路由双单位**：对接口表每个 GET/POST（含 messages/steps/list/cursor/start
   dry-run/resume/cancel、既有 job status/wait/cancel 的 agent 分支）验证 A 访问 B 与
   不存在均 404；所有者之外的同单位用户也 404，平台运营身份无单位旁路；队列和模型
   输入不得含 B 的合成 canary。成功/错误/列表/权限撤回均覆盖。
3. **人工门禁**：API token 请求 evidence:confirm/export/confidential:write/reveal
   必拒绝；agent principal 直接调用原 human-only API、数据库门禁、工具、子 worker
   均不能确认、重开/覆盖 confirmed/pending/comply-only、原型处置、导出或揭示值。
   原 bidder/technical 人类职责仍能正向确认；未确认证据始终不能 draft/export。
4. **完整链路与可信数据**：合成成功 extraction → req/card 读取 → generation dry-run
   → fake 结构化决定 → 子 job → 人类确认 → draft → 有引用的最终概要。核对命令 registry、
   schema/参数/default/hash 与真实 API 一致；模型指令、工具结果、URL/ID 注入不能
   扩大白名单；遮挡开关变化、原文/值 canary、输出超限、负偏离和卡片冲突均不能被隐藏。
5. **恢复与并发注入**：每个 checkpoint 前后 SIGKILL、业务/队列同事务提交与回滚、旧
   run_id 回写、heartbeat 失效、两 worker 同时 resume/cancel、重复幂等键、恢复器重启、
   单 worker 槽、未知 VendorCall。验证控制 Job 正常终态化、每个非终态 session 都有
   持久 wake、doing wake 的失联接管、无 pause 的 cancel 幂等回执；已完成步骤/usage
   不重复；数据服务可用时到期记录在下次巡检处理，旧 attempt 不发布任何 step/业务状态。
6. **预算与计费**：使用 contract-budget 的测试替身及其集成测试接口，验证首次决定
   前的预算问题、子 job 内重试/拆批、并发余额与父限额、resume 不清账、过期/变更批准、
   已结算响应崩溃、缓存历史费用不再计入、新调用精准去重、取消仍结算、单位自带模型
   未知成本阻止付费、硬时间期限覆盖子调用。预算回答从来不提升业务权限。
7. **CLI/审计工件**：每个新增命令、每个工具映射的 --json/schema、本地/远程模式和
   0/2/3/4/5 快照；暂停返回机器可读数据且终端无提问。验证 agent 与 token 来源贯穿
   步骤、卡片、job、审计，成功写入与审计同事务，dry-run 零写入。保存合成输入、命令、
   脱敏 Result、usage/余额变化 ID、checkpoint 和 JUnit 到 `artifacts/` 或临时目录，
   不在 docs 放验证日志或截图，保证同命令可重复核验。

CI 不调用真实厂商，所有外部 Provider 使用假实现/MockTransport；不以假账本证明 RLS
或并发扣款。获授权的真实模型/网站评测只能在 `evals/`，记录公开输入、固定模型/Schema
和实际费用，单次运行需满足预算与人工关口。草案核验仅执行 ruff、pyright 和离线导入，
无需启动 PostgreSQL，不新增运行实现或 changelog 条目。

## 待决定

| 决定 | 选项 | 推荐默认与理由 |
| --- | --- | --- |
| 首片工具范围 | ① 已抽取任务的七个工具与人工审阅/组表闭环；② 同时加入 parse/extract/搜索/截图/原型 | **①**，已有生成、确认、组表契约可直接复用，先验证付费编排和恢复；其余逐工具立契约接入，不把不存在的 check/score 当工具 |
| 循环执行位置 | ① worker 的有 checkpoint 控制作业；② API 请求内循环；③ 新 agent SDK 服务 | **①**，复用租约/取消/计量，等待人或子 job 时释放槽位；无需新增大依赖 |
| 会话可见性 | ① 发起人专有；② 同任务成员共享、管理员可读 | **①**，任务成员权限尚无完整入口，避免把对话隐含开放给全单位；后续共享另立角色契约 |
| 授权持续时间 | ① 原 session 有效期与 8 小时取较短，过期由同一人续权；② 独立长期委托 | **①**，不存 session 秘密且缩小无人值守窗口；续权不清限额，不能承诺现有 logout 即撤销 |
| 限额初值 | ① 24 steps / 32 calls / 900 秒活动 / 24 小时生存期，金额显式输入；② 首片更大并允许运行中抬高硬限额 | **①**，使恢复和父子费用上界可测；金额建议 vendor USD 2，平台币种独立填写，任务预算批准不提升硬限额 |
| 不确定请求恢复 | ① 暂停核实，无法核实则终止另开；② 自动重发并接受可能重复费用 | **①**，现有账本没有厂商自动对账，不能凭 lease 过期释放预约或重复执行 |
| 外发遮挡与未知价格 | ① 遮挡开启且可计算成本上界才准入；② 允许人给 agent 关闭遮挡或无限额自带模型 | **①**，保持 agent 无保密值能力与硬费用上界；价格/遮挡变化产生暂停，不替人改配置 |
| A02 标记粒度 | ① API token 统一标为令牌自动化并保留 token ID；② 新增外部 agent 注册身份/产品名称 | **①**，身份来源可由现有认证证明，先共享审计模型；②交 A02 独立契约，不信任客户端自报身份 |
| 会话内容保留 | ① 首片加密保留且只追加，无自动删除；② 同期增加单位可配保留期与删除 | **①**，先保证恢复与历史账务可追溯；②需明确审计/账本引用和删除恢复边界后另立契约 |

上述选项须人工确认后才能实施；任务预算具体规则只在 contract-budget 决定，不在此
增加第二张预算决定表。批准范围为本页接口与首条链路，其余设计目标仍由路线图维护。
