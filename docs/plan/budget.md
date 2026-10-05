---
kind: plan
---

# 契约草案：任务预算执行与费用预检

状态：**待批准，未实施。** 对应[路线图](roadmap.md) F06、F09、C03。

[agent.md](../../agent.md#工作方式)要求：
“新功能先写 Pydantic 模型、Provider 接口和 CLI 的 JSON 结构，等确认后再写实现。”
[Pydantic 与 Provider 草案](budget/budget_contracts.py)仅供审查，未注册到运行 API/CLI。
本文中的新增行为均为提议；待决定事项采用推荐值表达契约，不代表已经批准。

## 目标与边界

[设计文档](../AI%20标书工具设计文档.md#cli-设计规范与外部-agent-接入)要求费用命令支持
不写数据的 `--dry-run`；任务预算覆盖任务内调用，agent 在额度不足时向人询问。
设计另有单位套餐、月度配额和通知管理员的目标。

首条完整链路为：人设定任务预算 → 命令只读预检 → 作业内每次 Provider 调用原子预留
任务额度及适用的预付余额 → 返回用量立即结算 → 额度不足停止新调用、保留可安全发布的
结果 → 返回机器可读的人工处理信息。任务额度跨命令、作业和重试累计，不按月重置。
同时提供单位低余额的持久通知与查询，覆盖没有人等待 CLI 结果的作业。

本切片覆盖已有任务 LLM、Vision、本地 OCR、搜索调用；任务中的本地 Browser 操作记为明确
零费用，并保留沙箱原有访问控制。尚未接入的云 OCR、Embedding、收费 Browser 必须满足同一
契约后才能开放，不在此实现新厂商。`check`、`score`、内置 agent 未实现，后续复用本契约。
单位 `provider test` 无任务，只约束单位余额及作业上限，但也必须有费用预检。

不做订阅、月度额度、在线支付、自动换汇、自动增加预算、邮件/短信/外部 Webhook、自动对账
或通用作业暂停/续跑框架。任务预算不是投标报价，不进入模型提示词。人工确认证据、正式导出
与原型交付决定的关口保持有效；预算批准不等于内容确认。

## 现有依据与设计差异

| 依据 | 可复用行为与缺口 |
| --- | --- |
| [contracts.py](../../server/app/schemas/contracts.py) `TaskCreate`、`Cost`、`Result`；[documents.py](../../server/app/services/documents.py) `create_task` | `budget_usd` 仅写入/展示；`Task.budget_usd` 为 `numeric(12,4)`，未参与准入。Result 契约版本为 `3.0`，七个顶层键；`Cost.usd` 是厂商 USD 成本，不是平台售价 |
| [calls.py](../../server/app/providers/calls.py) `accounted_call`、`current_accounting`；[execution.py](../../server/app/jobs/execution.py) `JobExecution.admit`、`_complete_once`、`job_cost` | 真正的统一模型准入点在每次外发前；上下文绑定作业尝试。现行锁为 Job → OrgBalance，已有持久 `VendorCall` 预留与幂等用量结算 |
| [llm.py](../../server/app/providers/llm.py) `HTTPExtractor.reservation/post`；[screenshot_vision.py](../../server/app/providers/screenshot_vision.py) `_reservation/preview/analyze` | 文本字节/输出上界及图片计价规则已有实现，复用其精确请求与价格版本，不另写一套估算公式 |
| [billing.py](../../server/app/services/billing.py) `require_funds/charge_usage/verify_currency`；[预付机制](../notes/prepaid-billing.md) | 提交检查仅作提示；逐次预留、用量、扣款及余额流水才是实时约束。单位密钥调用预付扣款为零，但仍消耗调用数 |
| [ADR 0002](../adr/0002-prepaid-billing.md) | ADR 的“不做预估冻结”和允许在途透支是较早决定，与 `0016_vendor_call_guards.py` 后的逐次预留实现不同；本提案建立在现有实现上，批准实施时补后继 ADR，不把旧描述当作当前行为 |
| [provider-config.md](../notes/provider-config.md)；[configured.py](../../server/app/providers/configured.py) `resolve_configured/model_identity` | 固定单位配置修订/平台目录身份，任务预算不得改变供应商选择；单位自带密钥的 USD 单价可缺失，不能据零平台扣费宣称零成本 |
| [tender_jobs.py](../../server/app/services/tender_jobs.py) `submit`；[card_generation.py](../../server/app/services/card_generation.py) `estimate/submit_generation` | parse/extract 预检仍为未知费用；起草已有首轮上界、首个调用拦截和外发清单。整轮预估不等于完整作业保证 |
| [drafts.py](../../server/app/services/drafts.py) `submit`；[response-cards.md](../notes/response-cards.md) | `draft` 是零模型费用的确定性组表；付费起草实际为 `card generate`，不可混称 |
| [local_ocr.py](../../server/app/providers/local_ocr.py) `LocalOCR.recognize`；[processor.py](../../server/app/jobs/processor.py) `record_usage` | 本地 OCR 有用量、零厂商费用，但目前在处理后集中记录，未逐页准入 |
| [search.py](../../server/app/providers/search.py) `PerplexitySearch.search/SearXNGSearch.search`；[vendor_search.py](../../server/app/services/vendor_search.py) `process`；[product_simulation.py](../../server/app/services/product_simulation.py) `vendor_pages/process` | 搜索未经过 `accounted_call`，没有逐次 UsageRecord。模拟拟投的模型调用已接入，搜索仍旁路；其 API 已有预检，CLI 尚未注册 |
| [screenshot-evidence.md](../notes/screenshot-evidence.md)、[product-simulation.md](../notes/product-simulation.md) | 图片分析、原型生成已有模型预估/准入；搜索和模拟拟投预览缺完整成本；图片预估只检查首个请求是否可准入，与整轮是否够钱分开报告 |
| [platform.py](../../server/app/services/platform.py) `test_model` | 平台探针无单位/任务，`accounted_call` 在无上下文时直接执行，仅平台审计保留 usage，未写 UsageRecord；这不满足逐次用量硬规则，不能冒充已纳入预算。处理提议见待决定 |
| [background-jobs.md](../notes/background-jobs.md)、[llm-providers.md](../notes/llm-providers.md) | 已有租约、心跳、run_id、累计上限；抽取失败原子回滚，起草允许部分发布。设计的“预算询问”尚无暂停状态 |

## 接口

新增类型以草案 Python 文件为准；复用 `Contract/Cost/Result/ProviderUsage/TaskCreate/ProviderTest`
及 `Sha256`，不复制既有业务请求、来源、证据或内容模型。以下路由写逻辑路径；版本前缀见 JSON 契约。

| 入口 | 请求 / `Result.data` / `Result.items` |
| --- | --- |
| `GET /tasks/{task_id}/budget`；`bid task budget show --task UUID --json` | 无 body；`TaskBudgetData`；`[]` |
| `PUT /tasks/{task_id}/budget`；`bid task budget set --task UUID --input FILE --json` | `TaskBudgetSet`；提交后的 `TaskBudgetData`；`[]`。同事务检查 expected_revision、当前已花/预留额并记历史 |
| `GET /tasks/{task_id}/budget/history?before_revision=N&limit=100`；`bid task budget history --task UUID --json` | limit 1–100；`BudgetHistoryData`；`TaskBudgetRevisionView[]`，按 revision 倒序 |
| 既有 `POST /tasks`；`bid task create --budget AMOUNT --budget-currency CODE --json` | `BudgetTaskCreate` 扩展既有 TaskCreate；原任务字段与 `budget: TaskBudgetView`。显式不设上限用 API `budget.limit=null`；CLI 不传预算参数代表不设任务额度上限 |
| `GET/PUT /billing/low-balance-policy`；`bid billing alert show/set --input FILE --json` | PUT 为 `LowBalancePolicySet`；`LowBalancePolicyData`；`[]`，show 无 input |
| `GET /billing/notices?before=UUID&limit=100`；`bid billing notices --json` | 游标按 `(created_at,id)` 倒序，limit 1–100；`LowBalanceNoticesData`；`LowBalanceNoticeView[]` |
| 既有成本命令 `--dry-run --json` | 在既有预览 data 保留业务字段，增加 `budget_preflight: BudgetPreflightData`；顶层 `cost` 与其 `estimate` 一致，items 保留该命令的既有预览项目 |
| 既有 `GET /jobs/{job_id}`、`bid job status/wait UUID --json` 和成本命令 `--wait` | 保留领域 result，增加 `data.result.budget: BudgetJobResult`；通用完成度/stop_reason 与该对象一致，不泄露 submission 快照 |

所有受影响的成本入口必须覆盖，而非仅给新命令加预检：

| 命令 / HTTP | 请求复用与估算范围 |
| --- | --- |
| `tender parse` / `POST /documents/{id}/parse` | `JobAction`；只在本地检查文件/页，统计候选 OCR 页，不运行 OCR；本地引擎可确定零费用，云引擎尚不启用 |
| `req extract` / `POST /documents/{id}/extract` | `JobAction`；复用分批和请求构造，报告首轮；后续补漏、拆批、重试单列不确定性 |
| `card generate` / `POST /tasks/{id}/cards/generations` | `CardGenerateRequest/CardGeneratePreview`；保留外发清单、expected_input_hash、max_charge，补任务额度快照 |
| `screenshot analyze` / `POST /tasks/{id}/screenshot-analyses` | `ScreenshotAnalyzeInput/VendorJobPreview`；每张图的已核验图片 token/单价版本，报告首轮和下一调用两种检查 |
| `ui mock` / `POST /tasks/{id}/prototype-generations` | `PrototypeGenerateInput/VendorJobPreview`；模型首轮加本地 Browser 的零费用，保留沙箱 blocker |
| `evidence search` / `POST /tasks/{id}/screenshot-searches` | `VendorSearchInput/VendorSearchPreview`；固定查询条数、每次请求价格或明确平台承担的零单位责任，不执行搜索 |
| 新 `bid product simulate --task UUID --input FILE --dry-run --json` / 既有 `POST /tasks/{id}/product-simulations` | 导入既有 `ProductSimulationInput` 注册薄 CLI；保留 human-only 权限，不新增业务实现。只估本地可构造的分类首轮；后续模型生成查询/网页内容未知，maximum_calls 按现有有限分支计算或 null，不能编造端到端 token 上界 |
| `provider test --dry-run` / `POST /providers/test` | `BudgetProviderTest` 扩展既有 ProviderTest；合成页单调用、本地定价、不读远程厂商余额，不创建无任务 provider_test 作业 |
| `platform model test --id MODEL --dry-run` / `POST /platform/models/{id}/test` | `BudgetPlatformModelTest`；按目录推理档位构造合成输入，不实际探测端点；真实探针需 `--test-org UUID` / test_org_id，租户计量约束见待决定 |
| `draft`、`screenshot annotate`、`sandbox render/capture`、`export prepare` | 复用各自请求与零费用预览；没有模型请求就不生成虚假 UsageRecord。沙箱/转换的本地资源配额与未来商业价格分开 |

### 预检与 Result.cost

预检只读：不建 Job、不写审计/通知/用量、不预留、不发起模型、OCR、搜索、浏览器采集、
远程余额查询。缓存命中也须重新验权及检查输入依赖。命中有效缓存时 `basis=cache_hit`、本次
estimate 各费用为零；原作业已付的历史成本放 `cached_result_cost`，任务 spent 不回退。

`cost.llm_tokens/ocr_pages/usd` 沿用原类型；真实执行是累计实际用量，dry-run 是首轮保守估计。
`usd` 永远是厂商 USD 成本，任何一次未知则合计 null，不能填平台销售额或其他币种。
拟新增 `basis`、`charge`、`billing_currency`、`task_amount`、`unpriced_calls`、
`unresolved_calls`：`charge` 只代表单位预付扣款；`task_amount` 使用同一 billing_currency
表示本提案的任务支出。新增金额用 Decimal，JSON 为十进制字符串，数据库为 `numeric(18,8)`。
`unpriced_calls` 指任务责任未能定价的调用，厂商 USD 成本缺失但平台售价已知不计入该数。
taskless provider_test 保留同一按责任计价的调用金额，但不归属/消耗任何任务额度；其 task_id、
task_budget 和 budget_revision 均为 null。查询命令与排队受理响应的本次 cost 为明确零费用；
status/wait 报告被查询作业的累计费用，不把查询请求自身当成付费调用。
`duration_ms` 是本次预检耗时；作业预计耗时单放 `estimated_duration_ms`，无可验证测量则 null。

`next_call` 是下一次真实请求的保守预留；`admission_blocker` 表示此刻不能开始的原因。
`first_pass_fits=false` 只说明整轮估计超过可用额度，不禁止可以安全完成的一部分；它不能被解释为
整个作业必定失败。agent 可在派工前据此询问人。动态分支使总量未知时返回 null 和 uncertainty。
预检成功（包括成功发现 blocker）退出 0；真正提交被拒绝才按错误表处理。

输入/价格/Provider 身份 hash 不包含瞬时余额；预算修订单独报告。已有强制预检 hash 的入口继续
校验，其他入口无需持久化预检或信任客户端给出的金额。预检不是额度承诺，实际调用必须重算并锁定准入。

**兼容性提议：Result 4.0。** `BudgetResult` 继承现有七键结构，只扩展 cost；既有 `Cost`
禁止额外字段，且预算控制/失败语义有变化，因此不能静默宣称仍是 3.0。拟用 `/v4` 前缀服务新
CLI；既有无前缀 API 保留 3.0 投影至少一个主版本周期。两者共用相同预算执行，旧投影不移除
服务器保护。新 CLI 默认 4.0，`--contract-version 3.0` 仅发现/调用既有命令；`bid schema`
分别公布精确版本、参数、输出与退出码。版本路由/投影本身属于批准后必要接入，本草案不注册。

完整的七键预检响应示意（合成值；data 省去命令特有预览，仅展示新增附件）：

```json
{
  "ok": true,
  "command": "req extract",
  "data": {
    "budget_preflight": {
      "dry_run": true,
      "command": "req extract",
      "task_id": "10000000-0000-0000-0000-000000000001",
      "input_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "as_of": "2026-10-04T12:00:00Z",
      "task_budget": {
        "org_id": "20000000-0000-0000-0000-000000000001",
        "task_id": "10000000-0000-0000-0000-000000000001",
        "revision": 2,
        "limit": "20.00000000",
        "currency": "USD",
        "state": "active",
        "spent": "4.00000000",
        "reserved": "1.00000000",
        "available": "15.00000000",
        "unpriced_calls": 0,
        "unresolved_calls": 1,
        "history_complete": true,
        "as_of": "2026-10-04T12:00:00Z"
      },
      "planned_calls": null,
      "maximum_calls": null,
      "estimate": {
        "llm_tokens": 0,
        "ocr_pages": 0,
        "usd": null,
        "basis": "unknown",
        "charge": null,
        "billing_currency": "USD",
        "task_amount": null,
        "unpriced_calls": 1,
        "unresolved_calls": 0
      },
      "next_call": null,
      "admission_blocker": "billing_price_unavailable",
      "first_pass_fits": null,
      "full_run_guaranteed": false,
      "cached_job_id": null,
      "cached_result_cost": null,
      "estimated_duration_ms": null,
      "duration_basis": "unknown",
      "uncertainty": ["unknown_price", "retry_or_split", "concurrent_spending"]
    }
  },
  "items": [],
  "warnings": ["价格未知；本次未调用 Provider，也未预留额度。"],
  "cost": {
    "llm_tokens": 0,
    "ocr_pages": 0,
    "usd": null,
    "basis": "unknown",
    "charge": null,
    "billing_currency": "USD",
    "task_amount": null,
    "unpriced_calls": 1,
    "unresolved_calls": 0
  },
  "duration_ms": 12
}
```

## 预算口径与币种

| 支付责任 | 任务预留/结算 | 单位预付余额 |
| --- | --- | --- |
| `org_platform` 平台模型 | 使用固定目录售价的 charge，币种为 `BID_BILLING_CURRENCY` | 同一次准入预留；有效 usage 到达即扣款 |
| `org_direct` 单位自带密钥 | 以配置修订中的厂商单价计算预算成本；首版仅在部署币种 USD 且单价齐全/有请求上界时准入有限预算 | 零平台扣款；不是零任务支出 |
| `platform_absorbed` 平台承担搜索 | 显式任务责任 0，逐次计数、记录厂商成本（未知为 null）；不能将免费责任推断成厂商零成本 | 不从单位扣款；沿用路线图中的费用归属，改为单位付费需另行批准 |
| `local_free` 本地 OCR/Browser | 显式零金额，计页/次数；纯组表、文件读取不造 Provider 用量 | 不扣款 |

任务上限 `limit=null` 表示不设任务金额上限，`0` 表示拒绝任何正金额调用，允许明确零责任调用。
有限预算遇到无法计价的下一请求或不完整历史，拒绝正金额/未知金额请求，返回
`task_budget_unpriced`；仍可读取、导出符合原关口的现有成果、组表及执行明确免费的本地操作。
不设上限也不能绕过单位余额、job 上限、调用数/权限及计量。不得将 null/unknown 默认成 0。

币种必须与部署配置匹配，不允许按请求更换，不提供汇率。`budget_usd` 仅作为兼容输入/展示，
只有 USD 部署可映射至新 limit，和新 budget 互斥。非 USD 部署拒绝新的 `--budget-usd`（退出 2），
历史非空 USD 上限标为 `currency_review_required`，由人明确设定本币额度后才允许新增正金额
责任，不自动改标签或兑换。预算口径是本系统对已声明价格的控制，不保证 BYOK 厂商的系统外账单。

## 数据模型与迁移提纲

复用 [entities.py](../../server/app/models/entities.py) 的 Task、VendorCall、UsageRecord、
OrgBalance、Job、AuditLog；不建第二套资金账本，不从 balance_entries 反推任务全部成本。

| 表 | 拟新增/扩展字段与约束 |
| --- | --- |
| 新 `task_budget_revisions` | `id, org_id NOT NULL, task_id, revision, limit, currency, state, actor_user_id, origin, reason_sha256, created_at`；`UNIQUE(org_id,id)`、`UNIQUE(org_id,task_id,revision)`；只增不改删；人改必有 actor 与原因 hash，migration 可无 actor，token 创建仅可留下无上限初始记录 |
| `tasks` | `budget_limit numeric(18,8) NULL`、`budget_currency NOT NULL`、`budget_revision NOT NULL`、`budget_state NOT NULL`。Task 行作为任务支出互斥锁；当前值与历史修订通过 deferred 约束/触发器保持一致 |
| `vendor_calls` | 增加 task_id（仅 provider_test 可空）、capability/payer、budget_revision、`reserved_task_amount`（未知可空）、currency、价格版本/请求 hash 与公开 quote 元数据；原 `reserved_charge/charge` 继续专用于预付，不混用。任务/作业/配置绑定不可改 |
| `usage_records` | 增加 capability/payer、`task_amount numeric(18,8)`、billing_currency、price_revision、search_requests；沿用 ProviderUsage 的厂商/model/version/duration/tokens/ocr_pages/usd/image 元数据。新增调用必须引用已准入 call，结算视图为 BudgetUsageView |
| `jobs` | 保存提交人的 user/token/actor_kind 和已授范围，用于重新验权及审计；业务 `result` 增加 budget 附件，原 submission 保持内部使用。不增加 paused 状态 |
| `org_balances` | low_balance_threshold（默认 0，null 禁用）、alert_revision（初始 1）、low_balance_active、alert_cycle。可用余额按原余额减全部未结预付预留计算，不存第二个余额 |
| 新 `org_balance_notices` | `id, org_id NOT NULL, policy_revision, cycle, threshold, currency, available_balance, created_at`；`UNIQUE(org_id,id)`、`UNIQUE(org_id,policy_revision,cycle)`；只增不改删，记录进入低余额区间的快照 |

两张新表都在同一次迁移启用 **ENABLE 与 FORCE ROW LEVEL SECURITY**，使用现有
`app.current_org` 的 `USING/WITH CHECK`；缺单位上下文拒绝读写。运行角色不拥有表、不具
BYPASSRLS；不扩大 `bid_platform_fn` 的跨单位 SELECT 策略。org_id 引用 orgs；所有租户父引用
带 org_id：预算历史到 tasks，actor 到 memberships `(org_id,user_id)`；通知到 org_balances；
调用到 `(org_id,task_id)` 及 `(org_id,task_id,budget_revision)`；usage 继续引用
`(org_id,job_id,run_id,call_id)`。额外唯一键/触发器校验 job、task、call、budget_revision
属于同一任务，不能只验证“同单位”。任务为空的 provider_test 必须没有任务预算修订。

金额非负（available 可负）、币种三位大写且与部署匹配、revision 递增、completed 与 usage
一一对应由数据库约束/门禁保证。运行角色不直接更新预算字段，预算写通过受控事务及人类角色
触发器；预算历史和通知无 UPDATE/DELETE 权限。审计只写 ID、修订、币种、原因 hash、结果码，
不写具体任务预算值、报价、任意原因原文、URL、提示词或密钥；预算金额仅在授权业务表/视图出现。

迁移按以下顺序准备，本文不生成迁移文件：

1. 停止未参与新准入的旧 worker，等待可确定调用结算；pending/unknown 不因部署释放。
   新列先补值、加复合键/RLS/门禁，然后切换运行二进制，禁止混跑旧准入路径。
2. 按 Job 补 VendorCall.task_id，按已存 UsageRecord 分离平台 charge、BYOK USD 与零责任。
   任务已花额包含切换前可归属的记录；未知价格/来源保留 null 与 history_complete=false。
   旧的无 call_id 用量只计一次；不能把已结算 VendorCall 再累加为消费。
3. USD 预算按原值精确映射并产生 migration 修订；非 USD 历史值按上述人工复核规则迁移。
   旧请求缺少持久提交人时不能编造操作者，需经受权的新提交绑定身份再运行。无法还原的搜索历史
   若有固定政策可证实为“平台承担”，单位责任可确定为零；厂商成本观察不完整另列 warning，
   不因此将单位责任的 history_complete 标为 false。无法证明付款责任则不能推断为零。
4. 校验每任务 `spent/reserved/unpriced_calls`、每单位可用余额以及两单位隔离，再加 NOT NULL
   和完整门禁。未知历史的有限预算需对账补齐后才能开展正金额工作；对账写入不在本切片中自动执行。
   降级保留账本/历史，不能重启会绕过任务额度的旧 worker 继续收费。

新记录显式保留计价时币种；存量 usage 的平台 charge 从对应 BalanceEntry.currency 恢复，不能
仅套当前环境变量。无法恢复则记未知并拒绝有限预算下的正金额调用。部署改币种除原余额检查，
还必须核对有效任务额度与 pending/unknown 调用；有未结 hold 时拒绝切换，历史已结明细始终按
原币种读取。本切片不提供币种切换或跨币种历史清零操作。

## 唯一准入、Provider 与并发结算

草案 `BudgetQuoteProvider.quote` 只在接入层根据实际请求字节、固定配置和计价规则计算
`BudgetCallQuote`，不外发。文本与图片复用已有上界算法；搜索按次、本地 OCR 按页。
业务服务不能提供可信价格、payer 或 task_id；task_id 来自受权 Job，价格来自固定目录/配置。
`BudgetCallAccounting` 是既有 `CallAccounting` 的拟演进，`BudgetedCallProvider.call`
对应既有 `accounted_call` 边界，不是第二个准入服务。`LLMProvider/OCRProvider` 的业务协议
继续复用 [base.py](../../server/app/providers/base.py)，搜索业务结果复用 `SearchResult`。

所有生产任务调用必须有 `current_accounting`：无上下文显式失败，不再静默执行；standalone
真实 adapter 只可在 `evals/` 使用。OCR 从 processor 的事后重复入账迁到逐页 complete；搜索
在 Provider 每次 HTTP dispatch 外包同一 accounted_call，域名限制后的开放搜索也算另一次。
模拟拟投的查询与读页之后再调用模型必须共享同一任务预算。实际本地 Browser 操作也记零责任
调用；网页内部子请求仍由 fetch broker 配额控制，不把每个静态资源当成新的模型调用。
Gotenberg/存储/纯组表无供应商费用，本切片不创造其商业计费规则。

任务型锁序统一为 **Task → Job → OrgBalance**；taskless provider_test 为 Job → OrgBalance。
修改预算只锁 Task，不反向锁 Job。结算、准入和发布均遵守该序。
[card_generation.py](../../server/app/services/card_generation.py) `generate`、
[screenshot_jobs.py](../../server/app/services/screenshot_jobs.py) 和
[prototype_generation.py](../../server/app/services/prototype_generation.py) 发布已有 Task → Job；
[product_simulation.py](../../server/app/services/product_simulation.py) `process` 先 owned_job 再
经 `versioned.select_revision` 锁 Task，批准实施时必须纠正该反序。沙箱既有单位互斥若需要，
仍在 Task 之前获取，不可从 OrgBalance 反向获取该互斥。

一次准入须在一个短事务中：

1. 从同单位 Job 解析任务，锁 Task 后锁 Job，复核关联未变、run_id/lease、取消/stop flag、
   当前提交人权限及 `before_admit` 的输入/配置关口。
2. 汇总该任务所有 UsageRecord 的已知 `task_amount` 为 S；所有 pending/unknown 的
   `reserved_task_amount` 为 H；新请求上界为 R。有限预算 L 必须满足 **S + H + R ≤ L**。
   未知正金额历史/请求不得准入；预算修订从实时 Task 读取，不信任排队时的旧额度。
3. 保留既有累计调用数、job_charge_limit、起草 max_charge 检查；平台付费再锁 OrgBalance，
   按全单位未结预留检查可用余额。新增 OCR/search/browser 也消耗调用数，plan_calls 必须计入
   页数/查询/操作，不能仍只按原 LLM 批次数配置上限；单作业金额上限仍是预付 charge 口径。
4. 两重预留在**同一 VendorCall 行、同一事务**落盘，成功提交后才发送请求。拒绝时无外发、
   无用量/资金扣除，不留下只有一侧的预留。事务提交不明也不得外发，已落盘的 hold 留待对账。

`complete` 用原 `(org_id,job_id,run_id,call_id)` 幂等键，在同一事务插 UsageRecord、扣平台
余额/写 BalanceEntry、把两个预留替换为实际金额、刷新 job cost 并检查低余额阈值。
金额计算用 Decimal；预留向上取整到八位小数，结算沿用现有 ROUND_HALF_UP 八位小数，不能用
二进制 float 累加任务额度。适用金额列统一支持 numeric(18,8)。同一 call 的 payer/currency/
price_revision/配置归属必须与准入快照一致，服务不接受 Provider 回包自行改变付款责任。
复用现有对可重试数据库错误的三次有限重试；模糊提交不重复扣款。被拒、格式错误、有 usage 的
错误响应在内容解析前照常记账。Task 上的两个并发作业不能各自看到同一份未预留额度。

超时、无/非法 usage、进程死亡保持 pending/unknown 的两种 hold；lease 过期、取消、提高
预算和 `--retry` 均不能释放。迟到响应即使 run_id 已换也可按原 call 结算，但不能发布业务
结果或覆盖后继状态。已有发出的调用在截止时间内排空；没有发出的批次停止。

若实际费用 C≤R，任务并发超支界为 0；价格/端点违反上界时，真实 usage 必须全额记录，可能
超额为已准入调用的 Σmax(0,C−R)。返回 `call_charge_bound_exceeded`，停止该尝试后续调用；
其他尝试的下一次准入重读已结算和在途占用，不能沿用旧余额。
不截断账单、不伪装零超支。BYOK 的价格声明不完整则本来就不具备可证明上界。禁用/变更目录的
处理沿用 Provider 身份校验，不能用更便宜的预览价格配更贵的实际调用。

## 权限、预算修改与审计

依据是 [auth.py](../../server/app/services/auth.py) 的 `authenticate/membership/Identity`、
`ROLE_SCOPES/SCOPES/set_actor_context`，以及各服务对 saved worker grants 与当前角色的交集。
认证加密在 [core/security.py](../../server/app/core/security.py)，权限定义实际在 services/auth.py，
不是另建 core scopes。数据库租户上下文复用 [core/db.py](../../server/app/core/db.py)。

| 身份/操作 | 提议权限 |
| --- | --- |
| admin/bidder/technical/viewer 与受权 token | `task:read` 可读本单位任务预算/历史；作业/预检还须命令本身与材料读取权限，不因能读预算就能发起付费调用 |
| 人类 admin、bidder | 创建任务时可设置初始预算；新 `task:budget:write` 只授这两种人类角色，可改本单位任务额度。尚无任务成员规则，不凭 created_by 臆造所有权边界 |
| technical/viewer、内外 agent、API token | 不得改变/解除预算，也不得创建带显式预算的任务；已有 `task:create` token 可按既有能力创建不设上限任务，是否强制所有新任务有额度另待决定 |
| 人类 admin | 新 `billing:alert:write` 修改低余额策略；通知与单位精确余额由既有 `billing:read` 读取，满足角色交集的 token 可读但不能改 |
| 平台管理员 | 身份本身不赋予单位任务读取/预算修改权；平台探针选择内部测试单位也须该单位的有效人类 admin Membership |

`task:budget:write/billing:alert:write` 不加入可签发 token 的 SCOPES，并在数据库 token 约束
和写入 actor gate 同时禁止。API token 永远不能拿 `evidence:confirm`、`export`；agent 即使用
发起人的授权也必须保留非人 actor_kind，不能修改预算或确认。无权/跨单位资源与不存在统一 404；
同单位资源存在但操作范围不足按既有 403；失效身份 401/403。预检给无 billing:read 的调用者
只返回 `insufficient_balance/low_balance` 及处理方向，不泄露单位具体余额/通知历史。

改额度要 expected_revision 与非空 reason；去首尾空白后仍空拒绝。变更限额不能小于
S+H，不能覆盖未知的已花正金额；冲突 409/退出 2。null 解除额度也是一次人类审计变更。
增额不抵扣余额、不免除 job 上限、不自动重新启动终态作业。提交人离职/失权后不可仅凭早先
scope 继续派工；既有已发调用仍结算。

事件为 `task.budget.created/changed/admission_denied/bound_exceeded`、
`billing.low_balance_policy.changed`、`billing.low_balance`。人类改额审计与历史同事务；准入
拒绝在原事务回滚后使用短事务按 job/run/reason/budget_revision 去重，记录实际提交者 user/token
与 worker 身份，不能假托任务创建人。费用数字留在账本/业务表，AuditLog 只记对象 ID/修订与
安全原因码。自动通知没有人类操作者，不伪造 AuditLog.actor_user_id：由通知行充当系统事件，
有人改策略时另外写人类 AuditLog。dry-run 不写任一事件。

## 作业终态与 agent 的预算问题

复用 [jobs/processor.py](../../server/app/jobs/processor.py)、[services/jobs.py](../../server/app/services/jobs.py)
及 [execution.py](../../server/app/jobs/execution.py) 的状态/lease/run_id；不用新队列，不增加
`paused`，不持有租约等人回复。预算耗尽为不可自动重试的可解释停止：

| 情况 | Job / CLI / 后续 |
| --- | --- |
| 首个请求不能准入，或未得到可安全发布成果 | `failed`、`ok=false`、退出 4；包含 intervention、已发生 cost 和未结 hold，不能因已经付过钱称部分成功 |
| 起草等已有部分发布语义且有通过校验的成果 | `succeeded` + `completion=partial`、`stop_reason=task_budget_exceeded`、`ok=false`、退出 5；只发布通过原领域关口的结果，items 列未完成 ID/安全原因 |
| 抽取/解析等要求原子发布，或搜索/原型无完整可用产物 | 仍 `failed`/退出 4，不存不完整 requirements/chunks 或半成品文件；usage/诊断保留。原子抽取不改为部分提取，这是与“所有预算停止都 exit 5”的明确区别 |
| 取消、lease/run_id 丢失、心跳/记账失败、费用超过上界 | 遵守原发布 fence；不借部分成功发布候选，已结算费用和 hold 保留 |

`BudgetIntervention` 是并行 `docs/plan/agent.md` 草案可消费的边界：code、task/job、budget_revision、currency、
available、required_next_call、minimum_new_limit、action、authorized_roles，固定
`human_required=true/auto_retry=false`。`minimum_new_limit=S+H+R` 仅覆盖下一次调用，不是全程
保证；价格或历史未知为 null。余额不足要求 admin 充值，改任务额度无效；job 上限须另行审查。
agent 可把这份信息转成预算问题，但批准必须通过上表的人类 API；CLI 本身不交互提问。

失败作业用原命令显式 `--retry`，继承累计消费/调用数/未知 hold；新 run_id 只代表新尝试。
部分成功起草仍是终态缓存：使用 `requirement_ids` 仅提交剩余条目形成新输入，重复原请求返回
已有部分结果；提高预算不改变模型输入 cache key、不重做或覆盖已人工确认的卡片。
`BudgetJobResult.continuation` 指明 retry_job/submit_remaining/reconcile_first，完成后为 none。
不得承诺本切片提供任意命令逐批 checkpoint/resume。

`job status` 目前对部分普通 failed job 仍返回 ok=true，而 `job wait` 抛出失败还可能丢失
已付费用附件（[cli/main.py](../../cli/bid_cli/main.py) `wait_for_job/partial_completion_exit`）。
批准后必须统一：status/wait/命令 --wait 保留同一完整 Result 的实际 cost、intervention 与
领域 items，再映射退出码，不能在等待包装时换成零费用空错误。

## 低余额通知

阈值以本币表示，默认 0，null 禁用；判断 `available_balance = balance − outstanding_charge`。
准入预留、结算、兑换、平台调整以及人改阈值时在 OrgBalance 锁内更新状态；从高于阈值进入
低于或等于阈值时增加 cycle、插入唯一通知。初次启用且已低也产生一条；持续低不重复，恢复到
阈值之上后再降才产生下一条。并发/重试依唯一键去重，不在只读 GET/预检中补写。

默认 API/CLI 查询通知（`billing notices`），成本命令可附不含余额的 `low_balance` warning；
本切片不宣称已推送邮件或建立 SSE 页面。通知只提醒，不代替准入判断，也不要求确认才能充值。
失效通知保留历史，客户端同时读取当前策略判断现在是否仍低。兑换/平台调整复用原受控函数，
通知维护不能授予平台角色任意单位业务读取能力。

## 失败模式与退出码

| 退出码 | HTTP / 原因 / 保留内容 |
| --- | --- |
| 0 | 200：预算查询/变更成功；预检完成（可有 blocker）；作业成功。提交 queued 表示已受理，完整执行结果须 wait/status |
| 2 | 400/422 参数、非有限数字、精度溢出、币种不匹配的预算输入、互斥旧新字段；409 revision 冲突或降低到 S+H 以下。未改额/未外发 |
| 3 | 408/429/503 暂时网络、队列、数据库或等待超时；等待超时不取消作业。盲目重试不能释放已有 hold，记账异常还须保留原 stop flag |
| 4 | 401/403/404 身份/权限/对象；402 task_budget_exceeded/insufficient_balance；409 task_budget_unpriced/task_budget_currency_review_required/作业上限；其他 Provider 不可重试或原子失败。实际费用与 intervention 仍输出 |
| 5 | HTTP 200 的终态部分成功；仅有可发布领域成果，`ok=false`、completion=partial，items 给未完成项而非未经审核文本 |

其他错误沿用 `ProviderFailure/ServiceError` 分类；预算拒绝不能映射成 3 造成无人值守重试循环。
减额与正在结算的竞态以 Task 锁顺序决定，数据库超时不得当作额度够用。未知请求不自动假定未扣费。
实际高于报价上界可使 available 为负，视图如实报告；不得截为零掩盖异常。

## 批准后测试与验收计划

以下为实施验收目标，本次草案不运行数据库/外部服务、不编写功能测试。优先使用真实 API、
受限 PostgreSQL 角色、worker 与 CLI 的端到端链路；Provider 用假实现或 httpx.MockTransport。

1. **每表两单位**：新增 task_budget_revisions/org_balance_notices，扩展 tasks/vendor_calls/
   usage_records/jobs/org_balances，以及相关 audit_logs/balance_entries，逐表测 A/B 读写、无
   app.current_org、FORCE RLS、同单位错 task/job/call/修订父引用、伪造 actor、修改/删除历史。
   既有 User 全局例外不能扩大；平台函数不能读预算/通知内容。
2. **每路由两单位**：接口表全部 GET/PUT/POST（包括每个成本预检/执行、任务创建、job status/
   cancel、通知游标）以 A 身份替换 B 的每个路径/body/查询 ID，统一 404且零外发。无上下文与
   失效成员/token 再验证；平台测试单位选择也必须查 Membership，不能只验 operator。
3. **并发金额关口**：两个 worker/两个 job/两种能力同任务，S+H+R=L 放行、超最小货币单位拒绝；
   不同任务竞争同单位余额；同任务 BYOK 与平台混合；减额/增额/结算/取消/缓存同时发生；事务
   回滚无半边预留。验证 Task→Job→OrgBalance 与模拟拟投发布无反向死锁。
4. **持久性与恢复**：发送前/响应后/结算提交不明时中断，lease 过期、接管、迟到 complete、
   数据库临时失败三次、未知 usage、厂商 C>R；唯一用量与扣费、hold 不释放、旧 run 不能覆盖结果。
   同 job 显式重试及换 job 均不能重置任务累计。历史未知、USD/non-USD 迁移逐项验证。
5. **所有能力入口**：extract 的重试/拆批/补漏、起草、Vision、prototype、模拟拟投中的全部
   LLM/search、逐页 OCR、本地 Browser、taskless provider test 都有每次 call/usage 对应；无
   accounting 的生产调用被拒。parse/extract 保持原子结果，起草保留可用部分，费用始终保留。
6. **权限与内容关口**：token 请求 budget write/evidence:confirm/export 一律拒绝；内置 agent
   不能伪装 session 加预算/确认。未确认证据不能 draft/export、原型正式导出决定、模拟材料
   禁止正式导出、已确认卡片不被续跑覆盖，沿用原端到端门禁场景。
7. **CLI 快照**：每个新增命令、所有成本命令 dry-run/执行、job status/wait 的 0/2/3/4/5 与
   七键结构；Cost/Decimal/null/basis、partial items/intervention、v3 投影/v4 schema、远程/本地
   模式一致。预检前后数据库/文件快照无业务写入，Provider 请求数为 0，缓存新费用为 0。
8. **通知与复核工件**：阈值首次进入/持续低/充值恢复/再次进入、并发结算/调账、禁用/重新启用、
   token 读取与无权余额不泄露。端到端工件保留合成输入、请求/结果快照、call/usage/余额核对及
   JUnit，放 `artifacts/budget/` 或 `data/work/budget-validation/`，不放 docs。
   真实厂商验证仅在 `evals/` 显式执行，验证端点/图片价格上界；CI 不连接真实外部服务。

## 待决定

| 决定 | 选项 | 推荐默认与理由 |
| --- | --- | --- |
| 运行中额度不足 | 挂起原 job 等待人；停止并返回安全部分/原子失败 | 停止。沿用现有租约与终态，起草有成果退出 5、原子抽取退出 4；agent 的询问独立于 worker |
| 任务预算覆盖口径 | 只计平台扣款；合并单位责任（平台售价 + BYOK 声明成本）；全部平台成本也转嫁单位 | 合并单位责任；防 BYOK 绕过任务额度，平台承担搜索仍显式为零。BYOK 控制仅对配置单价成立 |
| 非 USD、BYOK 与历史预算 | 自动汇率；另设多币种上限；不换汇并阻止未定价调用 | 不换汇。平台用部署币种；BYOK 首版只在 USD 可有界计价；历史 USD 额度在非 USD 部署由人重新设额 |
| 谁可增额/减额/解除 | 仅 admin；admin 与 bidder；任务创建者；token 也可 | 人类 admin 与 bidder。与投标专员创建任务设预算一致；technical/viewer/agent/token 不具增额权，理由+修订审计 |
| 无上限任务 | 禁止；保留 null；部署默认额度 | 首版保留 null 与已有创建行为，明确不是硬预算任务；若要求所有 agent 任务有限额，需再确定默认额及 task:create token 的创建政策 |
| 低余额提醒方式/阈值 | 仅即时 warning；持久通知；邮件/SSE；默认百分比或固定额 | 持久通知 + warning，默认 0、admin 可设固定额，避免引入通信服务及任意百分比基数；邮件/SSE后续 |
| 平台搜索费用 | 平台承担；转为单位按次扣款；BYOK | 延续平台承担并计量。售价/云 OCR/收费 Browser 规则未定，不能假设免费或擅自开始收费 |
| 平台模型真实探针归属 | 新全局 usage 例外；指定内部测试 org；继续不计量 | 指定内部测试 org，operator 同时必须是该单位人类 admin，经已有 provider_test 框架计量，按被测目录的固定身份和售价逐调用准入；不能改测试单位的活动配置或退回其默认模型。缺 test_org_id 属参数错误退出 2；无受权测试单位或可用余额则真实测试退出 4，仍可 dry-run。不新增全局业务表例外 |
| Cost/CLI 版本过渡 | 静默扩展 3.0；4.0 + 旧版投影 | 4.0，并保留一个主版本周期的 v3 API/CLI 适配；旧版同样执行硬预算，避免旧 strict schema 解析失败 |
| 单位套餐、月度额度 | 订阅、按量、混合；自然月/滚动月；硬停/审批宽限 | 留出本切片。预付消费决定不等于订阅/月额度决定；先明确账期、时区、结转、退款及超额规则，再独立立契约 |

批准后按“数据库与预算读写 → 唯一准入/全能力补齐 → 预检/CLI/终态 → 通知与完整端到端”依赖
顺序实施，同一完整链路验收后才标记交付；本文及 Python 草案不构成已上线行为。
