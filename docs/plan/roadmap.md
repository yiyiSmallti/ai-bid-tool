---
kind: plan
---

# 剩余范围与路线

本页列出[设计文档](../AI%20标书工具设计文档.md)中尚未完成的部分、待定决定和建议顺序。
已交付的行为以[机制笔记](../README.md#机制笔记)和源码为准，交付历史见
[changelog.md](../changelog.md)。设计“非目标”中的材料伪造、电子投标平台对接、
报价策略和自研模型不列入。

新功能按 [agent.md](../../agent.md#工作方式) 的要求，先提交 Pydantic 模型、
Provider 接口和 CLI JSON 结构供确认，再实现。

## 首要缺口：切片 1 的完成标准

[agent.md](../../agent.md#当前任务切片-1) 定义的切片 1 完成标准仍未满足：

- 抽取 adapter 已实现，但还没有用真实服务和公开招标 PDF 跑通端到端抽取。配置密钥后，
  先运行 [evals/extract_tender.py](../../evals/extract_tender.py)，再走一遍
  [CLI 招标流程](../guides/cli.md#run-the-tender-workflow)。
- GitHub Actions 工作流从未在远程运行。

后续切片的部分基础（资源库、证书原件、未确认来源）已提前实现，但不改变上述缺口。

## 已知代码缺陷

来自 2026-10-01 的代码审查，尚未修复：

| 位置 | 问题 |
| --- | --- |
| [parsing.py](../../server/app/services/parsing.py) `read_pdf` | 先把全部扫描页渲染成 PNG 再 OCR，页数上限内可能占用 GB 级内存；`validate_document` 对同一文件执行两次 |
| [evidence_sources.py](../../server/app/services/evidence_sources.py) `create_source` | 持有任务行锁期间读存储、渲染（最长 20 秒）、写存储；超时线程不会终止 |
| [cli/bid_cli/main.py](../../cli/bid_cli/main.py) `main` | 本地模式下的未知服务端异常输出 traceback、退出码 1；服务端没有通用异常处理，500 响应不符合 Result 结构 |
| [security.py](../../server/app/core/security.py) `Secrets` | 一个 Fernet 密钥同时用于会话、下载签名、令牌密文和文件加密，无轮换；ApiToken 另存可解密的 `encrypted_secret` |
| [conftest.py](../../server/tests/conftest.py) `admin_engine` | 缺 `BID_TEST_ADMIN_URL` 时数据库测试被跳过而非失败 |
| [auth.py](../../server/app/services/auth.py) `login` | 密码正确但非成员返回 404、密码错误返回 401，可区分密码是否正确；已有[共享认证限速](../notes/platform-console.md#password-admission-and-totp-consumption) |
| [services/platform.py](../../server/app/services/platform.py) `test_model` | 模型测试在数据库事务内发起真实调用，等待期间占用连接 |
| [processor.py](../../server/app/jobs/processor.py) 的 extract 分支 | 抽取提交后切换默认模型可能以旧缓存键记录新模型结果；模型卡片起草已固定并核对目录身份 |
| [api/main.py](../../server/app/api/main.py) | 所有路由在 `create_app` 内，上传、作业、令牌逻辑没有进入 `services/`；五个版本化资源服务高度重复 |

## 覆盖矩阵：地基、权限和数据模型

| ID | 现状 | 缺口 | 依赖 |
| --- | --- | --- | --- |
| F01 架构 | 服务端技术栈与真实 Procrastinate worker 已落地 | Vue 入口、内置 agent 入口 | 新接口确认 |
| F02 数据库隔离 | 现有业务表均 NOT NULL `org_id`、FORCE RLS、组织复合外键 | 每张新表、每个新接口同次带双单位与缺上下文测试 | 硬规则 |
| F03 文件隔离 | 招标文件、模板、证书原件、来源 PNG 已加密并受签名下载约束 | 其他格式、多附件、合同、区域截图、网页、导出件 | 新契约 |
| F04 账号与角色 | 全局 User、Membership、四种角色；平台管理员（配置名单、TOTP、运营后台） | 单位成员管理入口、任务成员、评论权限、OIDC、全局记忆维护 | 新契约；SSO 需授权 |
| F05 ApiToken | 签发、范围、期限；DB 禁止确认/导出范围 | 吊销入口、令牌列表、签发与吊销审计 | 新接口确认 |
| F06 Org/Task | 任务名称、编号、截止、预算字段 | 套餐与月度预算、任务成员与归档、预算执行、一次性组合创建 | 计费规则待定 |
| F07 后台作业 | parse/extract/card_generate/draft/provider_test/export_render/sandbox/截图作业持久化、取消、有限重试、租约与 `run_id` 防覆盖 | 其他命令的作业、SSE、遗留作业自动恢复 | 新接口确认 |
| F08 AuditLog | 资源、卡片人工决策、模型起草、遮挡设置与组表审计 | 登录、令牌、导出、其他配置及 agent 调用审计与查询 | 新契约 |
| F09 UsageRecord | OCR、抽取和模型起草的逐次用量记录；平台计费调用按售价从预付余额扣除，充值卡密 | 存储计量、任务预算预检、低余额通知、在线支付 | 新契约 |
| F10 部署与质量 | 本机迁移、Compose、锁定依赖、工作流文件 | 远程 CI、生产对象存储、TLS、备份与密钥轮换、私有化包装 | 推送与生产需授权 |

## 覆盖矩阵：资源与任务选择

| ID | 现状 | 缺口 | 依赖 |
| --- | --- | --- | --- |
| R01 Template | 单位私有 DOCX、声明类型与章节、不可变修订、任务固定 | 自动章节抽取、章节匹配、公共共享、导出适配 | 新契约 |
| R02 Product | 型号与来源 URL 元数据、修订、任务固定 | 规格书/白皮书文件、URL 取证、型号精确匹配、截图存档 | 搜索/视觉依赖服务 |
| R03 FeatureItem | 描述、声明状态、产品关联 | 真实功能截图、状态核对、`ui mock` 对照、需求文档导入 | 文件链独立；生成依赖 API |
| R04 Certificate | 声明、日期检查、PDF 原件修订 | 图片与多附件、OCR、真实性核验、到期提醒 | 新契约 |
| R05 OrgProfile | 文本声明、修订、任务固定 | 业绩合同附件、真实性验证、与资格/业绩表关联 | 新契约 |
| R06 TaskResource | 五类资源固定选择、显式替换及响应依赖失效判定 | 组合配置、创建时原子选择、看板变更提示 | 新契约 |
| R07 Task creation | 创建与后续选择各自可用 | 设计中 `--template/--features/--certs/--providers` 的组合创建 | 新契约 |

## 覆盖矩阵：解析、要求、证据、响应与校验

| ID | 现状 | 缺口 | 依赖 |
| --- | --- | --- | --- |
| B01 tender parse | PDF 按页文本、扫描页本地 OCR；Word 按段落与表格单元格解析，按文档位置引用 | PDF 段落/表格/坐标结构、OCR 坐标入库；Word 文本框与页眉页脚 | 新大型依赖先说明 |
| B02 req extract | Anthropic 与 OpenAI 兼容 adapter、并发批次、引用逐条核验、★ 规则并集、缓存、引用不通过逐条拒绝并报告、按官方档位选择推理强度与抽取历史；样例 Word 招标文件实测 446/449 条引用通过 | 要求确认入口；被拒条目的人工补录；单位后台的抽取页面 | 新契约 |
| B03 参数判定 | `condition` 为自由 dict | 类型化 param/op/value/unit、单位换算、模糊表达转人工 | 新契约 |
| B04 evidence fetch | 固定证书 PDF 页来源及响应 Evidence 绑定，来源档案恒未确认；人工截图入库与 `image_region` 证据；沙箱按允许名单采集厂家网页/PDF，页图经人工入库成为绑定归档的厂家证据，机制见 [screenshot-evidence.md](../notes/screenshot-evidence.md#vendor-captures) | 厂家搜索候选（[screenshots.md](screenshots.md) Phase B）、自动满足判定 | 搜索依赖 API |
| B05 evidence stamp | 截图链的 Rust 遮挡、裁剪、区域框与哈希已实施，不加水印 | 证书页等其他材料的标注；草案见 [annotation.md](annotation.md) | 待批准 |
| B06 ui mock | LLM 生成单页 HTML 原型并经沙箱离线截图、入库与逐项保留/替换决定；机制见 [screenshot-evidence.md](../notes/screenshot-evidence.md) | 软件响应表自动编排 | 新契约 |
| B07 人工确认 | 卡片与 Evidence 按职责人工确认、不可变修订、原子处置、模型提议与消费关口；决定见 [ADR 0005](../adr/0005-human-confirmed-responses.md) | 会签、任务成员与看板交互 | 新契约 |
| B08 draft | 三张人工确认响应表、须遵守与缺口全集分区、负偏离和旧稿失效；机制见 [response-cards.md](../notes/response-cards.md) | 多文档/多抽取作业合并 | 新契约 |
| B09 check | 未实施 | 标书与要求对照、废标/扣分风险、误报处理 | 语义校验依赖 LLM |
| B10 score | 未实施 | 逐项预估分、失分原因、引用 | 语义评估依赖 LLM |
| B11 export | 人工 Word 响应章节导出、正式件/审阅件、证书页附件、审计；机制见 [human-section-exports.md](../notes/human-section-exports.md) | Word/WPS 视觉分页验收；契约见 [export.md](export.md) | 已批准 |

## 覆盖矩阵：Provider、记忆、看板、agent 与 CLI

| ID | 现状 | 缺口 | 依赖 |
| --- | --- | --- | --- |
| P01 LLMProvider | `extract`/`draft` 协议、两个 HTTP adapter、DisabledLLM、测试替身 | check/score/agent 所需的通用结构化调用 | 新契约 |
| P02 OCRProvider | 本地 Tesseract | 坐标持久化、单位级语言与开关、云 OCR | 云服务需授权 |
| P03 Vision/Search/Embedding/Browser | 截图多模态匹配、区域建议与读字；沙箱 Browser 离线渲染与允许名单采集 | Search、Embedding、本机浏览器采集；沙箱真实隔离验收 | 搜索依赖 API |
| P04 ProviderConfig | 平台模型目录与计费；单位自带模型与平台模型选择、`provider set/list/history/test`；机制见 [provider-config.md](../notes/provider-config.md) | 视觉、搜索等其他能力的单位配置 | 新契约 |
| P05 通用控制 | 调用准入、即时记账、期限、有限重试、提取原子失败与起草部分成功 | 跨能力限流与统一进度 | 新契约 |
| M01 记忆存储 | 未实施 | 四层记忆 CRUD、候选审批、失效 | 全局来源待定 |
| M02 记忆检索 | 未实施 | 强制 `org_id` 与作用域过滤、优先级 | 向量依赖 Embedding |
| M03 自动候选 | 未实施 | 驳回/修改生成 candidate、评测样本 | 依赖 B07 |
| U01 看板 | 单位后台招标任务、解析抽取、响应卡审阅、起草预览与初稿页面；机制见 [org-console.md](../notes/org-console.md) | 资源/配置/记忆管理页 | 新契约 |
| U02 卡片/SSE | API/CLI 卡片修订与状态迁移 | 看板交互、SSE | 界面契约 |
| A01 内置 agent | 未实施 | CLI 工具映射、无确认/导出权限、状态恢复、预算询问；执行环境边界见 [sandbox.md](sandbox.md) | 编排依赖 API |
| A02 外部 agent | CLI、`bid schema`、范围令牌 | 调用审计与看板标记、可选 `mcp serve` | 新接口确认 |
| C01 CLI 契约 | Result 七键、schema 注册、统一退出码、两种模式 | 后续命令、主版本兼容周期 | 新命令确认 |
| C02 缓存 | 模型起草固定输入/版本缓存，保留人工确认，组表重算依赖 | 其他能力的跨依赖失效 | 新契约 |
| C03 dry-run/预算 | 起草外发清单与首轮费用上界、预付余额/调用上限拦截；组表零费用预检 | 更精确 token/耗时估算、任务预算执行 | 价格与测量依赖服务 |

## 覆盖矩阵：评测与保密

| ID | 现状 | 缺口 | 依赖 |
| --- | --- | --- | --- |
| E01 公开测试集 | 未实施 | 公开硬件招标 PDF、人工标注、匹配与指标定义 | 真实指标依赖 API |
| E02 质量指标 | 未实施 | ★ 召回率等指标的实测 | 依赖 B02、B04、B09 |
| S01 机密数据 | 对象/起草快照加密、组织隔离、默认外发遮挡与受控开关；机制见 [model-drafting-redaction.md](../notes/model-drafting-redaction.md) | 扩充敏感模式、单位自带模型、磁盘加密、保留与轮换 | 新契约 |
| S02 真实材料 | 无伪造接口、模型双文本引用校验、人工确认门禁 | 真实截图链、原型水印、导出清单的正向验收 | 依赖对应业务链 |

## 真实服务接入范围

接入层支持：要求抽取与响应起草的平台 LLM（Anthropic、OpenAI 兼容）。尚未接入：语义 check/score/agent
的模型调用、视觉判断、语义向量、付费搜索与云 OCR，以及这些能力的评测。

不依赖真实服务、可各自立契约推进的是：本地浏览器取证、Rust 标注、
水印、确定性规则、Vue 界面、记忆 CRUD 与审批、预算机制、公开评测集准备。

## 待定决定

| 决定 | 何时需要 |
| --- | --- |
| 平台模型的服务商、模型与单价 | 在运营后台配置默认模型时 |
| 平台默认 Vision/Embedding/Search/OCR，及费用与数据政策 | Provider 配置之前 |
| 模板公共共享 | 扩大模板读取边界之前 |
| 计费方式、全局记忆来源与审核人、数据驻留 | 对应范围契约时 |
| 看板卡片交互与进度展示 | U02 之前；服务状态机见 [response-cards.md](../notes/response-cards.md) |
| 远程 CI 运行与生产对象存储维护方 | 推送代码、生产部署时 |

## 建议顺序

1. 提交现有代码并跑通远程 CI。
2. 配置平台模型，用公开招标 PDF 完成切片 1 的端到端验收。
3. 修复上方已知代码缺陷。
4. 扩大真实证据取证与标注链（B04、B05）。
5. 模板导出，或 check 首版（B09、B11）。
6. 看板、卡片状态与 SSE（U01、U02）。
7. Provider 配置、score、agent、记忆、用量与部署。
