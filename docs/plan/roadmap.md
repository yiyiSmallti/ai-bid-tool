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

## 覆盖矩阵：地基、权限和数据模型

| ID | 现状 | 缺口 | 依赖 |
| --- | --- | --- | --- |
| F01 架构 | 服务端技术栈、真实 Procrastinate worker 与 Vue 3 + Element Plus 控制台已落地 | 内置 agent 入口 | 新接口确认 |
| F02 数据库隔离 | 现有业务表均 NOT NULL `org_id`、FORCE RLS、组织复合外键 | 每张新表、每个新接口同次带双单位与缺上下文测试 | 硬规则 |
| F03 文件隔离 | 招标文件、模板、证书原件（含合成前的上传文件）、来源 PNG 已加密并受签名下载约束 | 合同、区域截图、网页、导出件 | 新契约 |
| F04 账号与角色 | 全局 User、Membership、四种角色；平台管理员（配置名单、TOTP、运营后台） | 单位成员管理入口、任务成员、评论权限、OIDC、全局记忆维护 | 新契约；SSO 需授权 |
| F05 ApiToken | 签发、范围、期限；DB 禁止确认/导出范围 | 吊销入口、令牌列表、签发与吊销审计 | 新接口确认 |
| F06 Org/Task | 任务名称、编号、截止、预算字段 | 套餐与月度预算、任务成员与归档、预算执行、一次性组合创建 | 计费规则待定 |
| F07 后台作业 | parse/extract/card_generate/draft/provider_test/export_render/export_preview/sandbox/截图/原型生成/厂家搜索/模拟拟投作业持久化、取消、有限重试、租约与 `run_id` 防覆盖 | 其他命令的作业、SSE、遗留作业自动恢复 | 新接口确认 |
| F08 AuditLog | 资源、卡片人工决策、模型起草、遮挡设置与组表审计 | 登录、令牌、导出、其他配置及 agent 调用审计与查询 | 新契约 |
| F09 UsageRecord | OCR、抽取和模型起草的逐次用量记录；平台计费调用按售价从预付余额扣除，充值卡密 | 存储计量、任务预算预检、低余额通知、在线支付 | 新契约 |
| F10 部署与质量 | 本机迁移、Compose（含 SearXNG、Gotenberg）、锁定依赖、GitHub Actions 在每个 PR 上按改动范围运行，测试按 worker 分库并行 | 生产对象存储、TLS、备份与密钥轮换、私有化包装 | 生产需授权 |

## 覆盖矩阵：资源与任务选择

| ID | 现状 | 缺口 | 依赖 |
| --- | --- | --- | --- |
| R01 Template | 单位私有 DOCX、声明类型与章节、不可变修订、任务固定 | 自动章节抽取、章节匹配、公共共享、导出适配 | 新契约 |
| R02 Product | 型号与来源 URL、修订、任务固定；来源 URL 经沙箱采集为厂家证据，可由厂家搜索候选写入；演示用模拟拟投按采购项从厂商官网录入【模拟】产品与逐字参数，导出正式件拒收，机制见 [product-simulation.md](../notes/product-simulation.md) | 规格书文件上传、型号精确匹配；模拟拟投对官网不可达或无名称表格行的召回 | 新契约 |
| R03 FeatureItem | 描述、声明状态、产品关联 | 真实功能截图、状态核对、`ui mock` 对照、需求文档导入 | 文件链独立；生成依赖 API |
| R04 Certificate | 声明、日期检查、原件修订：PDF 或多张图片/PDF 按序合成，图片去元数据；控制台“单位资料”页管理证照，标出过期与 30 天内到期 | OCR、真实性核验、到期通知 | 新契约 |
| R05 OrgProfile | 文本声明、修订、任务固定 | 业绩合同附件、真实性验证、与资格/业绩表关联 | 新契约 |
| R06 TaskResource | 五类资源固定选择、显式替换及响应依赖失效判定 | 组合配置、创建时原子选择、看板变更提示 | 新契约 |
| R07 Task creation | 创建与后续选择各自可用 | 设计中 `--template/--features/--certs/--providers` 的组合创建 | 新契约 |

## 覆盖矩阵：解析、要求、证据、响应与校验

| ID | 现状 | 缺口 | 依赖 |
| --- | --- | --- | --- |
| B01 tender parse | PDF 按页文本、扫描页本地 OCR；Word 按段落与表格单元格解析，按文档位置引用 | PDF 段落/表格/坐标结构、OCR 坐标入库；Word 文本框与页眉页脚 | 新大型依赖先说明 |
| B02 req extract | Anthropic 与 OpenAI 兼容 adapter、并发批次、引用逐条核验、★ 规则并集、缓存、引用不通过逐条拒绝并报告、按官方档位选择推理强度与抽取历史；Word 与公开 PDF 招标文件均已用真实模型端到端抽取 | 要求确认入口；被拒条目的人工补录 | 新契约 |
| B03 参数判定 | `condition` 为自由 dict | 类型化 param/op/value/unit、单位换算、模糊表达转人工 | 新契约 |
| B04 evidence fetch | 固定证书 PDF 页来源及响应 Evidence 绑定，来源档案恒未确认；人工截图入库与 `image_region` 证据；沙箱按允许名单采集厂家网页/PDF，页图经人工入库成为绑定归档的厂家证据，机制见 [screenshot-evidence.md](../notes/screenshot-evidence.md#vendor-captures)；Perplexity Search API（未配置时用自托管 SearXNG）搜索厂家来源候选，经人选定后写入产品库 | 自动满足判定 | 新契约 |
| B05 evidence stamp | 截图链的 Rust 遮挡、裁剪、区域框与哈希已实施，不加水印 | 证书页等其他材料的标注；草案见 [annotation.md](annotation.md) | 待批准 |
| B06 ui mock | LLM 生成单页 HTML 原型并经沙箱离线截图、入库与逐项保留/替换决定；机制见 [screenshot-evidence.md](../notes/screenshot-evidence.md) | 软件响应表自动编排 | 新契约 |
| B07 人工确认 | 卡片与 Evidence 按职责人工确认、不可变修订、原子处置、模型提议与消费关口；决定见 [ADR 0005](../adr/0005-human-confirmed-responses.md) | 会签、任务成员与看板交互 | 新契约 |
| B08 draft | 三张人工确认响应表、须遵守与缺口全集分区、负偏离和旧稿失效；机制见 [response-cards.md](../notes/response-cards.md) | 多文档/多抽取作业合并 | 新契约 |
| B09 check | 已实施已确认初稿的确定性规则校验、证书日期清单、风险报告与人工 dismiss/reopen；机制见 [check.md](../notes/check.md) | `combined` 语义矛盾、薄弱响应和材料覆盖检查，见[校验契约](check.md) | 阶段二 Provider 与评测 |
| B10 score | 未实施 | [评分契约草案](score.md) | 待批准 |
| B11 export | 人工 Word 响应章节导出、正式件/审阅件、证书页附件、审计；导出件经 Gotenberg 转 PDF 在线按页预览；机制见 [human-section-exports.md](../notes/human-section-exports.md)、[page-previews.md](../notes/page-previews.md) | WPS 视觉分页与隔离 S3 下载验收；契约见 [export.md](export.md) | 已批准 |

## 覆盖矩阵：Provider、记忆、看板、agent 与 CLI

| ID | 现状 | 缺口 | 依赖 |
| --- | --- | --- | --- |
| P01 LLMProvider | `extract`/`draft` 协议、两个 HTTP adapter、DisabledLLM、测试替身；起草与模拟拟投共用的结构化 JSON 调用；check 的 rules 阶段不调用模型 | check 的 combined 阶段、score、agent 接入结构化调用 | 已批准 check 契约；其余需新契约 |
| P02 OCRProvider | 本地 Tesseract | 坐标持久化、单位级语言与开关、云 OCR | 云服务需授权 |
| P03 Vision/Search/Embedding/Browser | 截图多模态匹配、区域建议与读字；沙箱 Browser 离线渲染与厂家采集；Perplexity Search API 或自托管 SearXNG 搜索 | Embedding 草案见 [memory.md](memory.md#pgvector-与后续索引)；本机浏览器采集、仅用 SearXNG 时搜索引擎限流下的召回；代理节点内核级出网过滤、沙箱租约接管与断连/存储失败注入验收 | 新契约 |
| P04 ProviderConfig | 平台模型目录与计费；单位自带模型与平台模型选择、`provider set/list/history/test`；机制见 [provider-config.md](../notes/provider-config.md) | 视觉、搜索等其他能力的单位配置 | 新契约 |
| P05 通用控制 | 调用准入、即时记账、期限、有限重试、提取原子失败与起草部分成功；抽取、起草与模拟拟投按 `BID_LLM_CONCURRENCY` 并发 | 跨能力限流与统一进度 | 新契约 |
| M01 记忆存储 | 未实施 | [记忆契约草案](memory.md#数据模型与迁移轮廓) | [待批准与待决定](memory.md#待决定) |
| M02 记忆检索 | 未实施 | [记忆检索契约草案](memory.md#检索与优先级) | [分步范围](memory.md#目标与边界) |
| M03 自动候选 | 未实施 | [自动候选与样本契约草案](memory.md#自动候选样本与作业) | 依赖 B07；待批准 |
| U01 看板 | 单位后台招标任务、解析抽取、响应卡审阅、起草预览、初稿、导出文件、模拟拟投、单位资料与保密字段页面，招标原件、证书原件和导出件在线按页预览；机制见 [org-console.md](../notes/org-console.md) | 产品、功能、证书、模板管理页；配置与记忆管理页 | 新契约 |
| U02 卡片/SSE | API/CLI 卡片修订与状态迁移 | 看板交互、SSE | 界面契约 |
| A01 内置 agent | 未实施 | 契约草案见 [agent.md](agent.md) | 待批准 |
| A02 外部 agent | CLI、`bid schema`、范围令牌 | 调用审计与看板标记见 [agent.md](agent.md#审计与-a02-来源标记)；可选 `mcp serve` | 新接口确认 |
| C01 CLI 契约 | Result 七键、schema 注册、统一退出码、两种模式；`bid check run/list/show/decide/history` | 后续命令、主版本兼容周期 | 新命令确认 |
| C02 缓存 | 模型起草及确定性 check 固定输入/版本缓存，保留人工确认，组表重算依赖 | 记忆依赖见 [memory.md](memory.md#起草消费使用审计与-c02-缓存失效)；其他能力的跨依赖失效 | 新契约 |
| C03 dry-run/预算 | 起草外发清单与首轮费用上界、预付余额/调用上限拦截；组表与 rules check 零费用预检 | combined check 与 score 估价、更精确 token/耗时估算、任务预算执行 | 价格与测量依赖服务 |

## 覆盖矩阵：评测与保密

| ID | 现状 | 缺口 | 依赖 |
| --- | --- | --- | --- |
| E01 公开测试集 | 未实施 | 公开硬件招标 PDF、人工标注、匹配与指标定义 | 真实指标依赖 API |
| E02 质量指标 | 未实施 | ★ 召回率等指标的实测 | 依赖 B02、B04、B09 |
| S01 机密数据 | 对象/起草快照加密、组织隔离、默认外发遮挡与受控开关；保密字段在外发前换成占位符、导出时填回，机制见 [confidential-values.md](../notes/confidential-values.md)；数据密钥可轮换，会话与签名链接另用独立密钥；机制见 [model-drafting-redaction.md](../notes/model-drafting-redaction.md) | 扩充敏感模式、单位自带模型、磁盘加密、保留期限 | 新契约 |
| S02 真实材料 | 无伪造接口、模型双文本引用校验、人工确认门禁；真实厂家网页与白皮书经沙箱采集入库 | 导出清单在 Word/WPS 中的正向验收 | 依赖对应业务链 |

## 真实服务接入范围

接入层支持：要求抽取、响应起草、原型生成与模拟拟投的平台 LLM（Anthropic、OpenAI 兼容）、截图多模态分析、
Perplexity Search API 或自托管 SearXNG 厂家来源搜索，以及 Gotenberg 文档转换。尚未接入：语义 check/score/agent 的模型调用、语义向量与云 OCR，
以及这些能力的评测。

不依赖真实服务、可各自立契约推进的是：本地浏览器取证、Rust 标注、
确定性规则、Vue 界面、记忆 CRUD 与审批、预算机制、公开评测集准备。

## 待定决定

| 决定 | 何时需要 |
| --- | --- |
| 平台模型的服务商、模型与单价 | 在运营后台配置默认模型时 |
| 平台默认 Vision/Embedding/OCR，及费用与数据政策 | Provider 配置之前 |
| 生产厂家采集的单位每分钟请求上限（现为 60，大页面会被截为不完整） | 生产启用厂家网页采集之前 |
| 模板公共共享 | 扩大模板读取边界之前 |
| 计费方式、数据驻留 | 对应范围契约时 |
| 全局记忆 | [记忆契约待决定](memory.md#待决定) |
| 看板卡片交互与进度展示 | U02 之前；服务状态机见 [response-cards.md](../notes/response-cards.md) |
| 平台搜索服务的费用归属：Perplexity 按次计费，现由平台承担、不计入单位余额 | 生产启用 Perplexity 之前 |
| 仓库可见性与 Actions 额度：私有仓库按分钟计费，公开仓库免费且运行器更快 | 额度不足时 |
| 生产对象存储维护方 | 生产部署时 |

## 已知缺陷

| 缺陷 | 影响 | 处理方向 |
| --- | --- | --- |
| 起草个别批次漏答条目，结果记为 `missing_proposal` | 作业部分完成，漏答要求没有卡片 | 重跑起草可补齐；可在作业内对漏答条目自动补发一次 |
| 没有名称列的招标表格行以“表 X 第 Y 行”命名 | 模拟拟投难以判断品类，召回下降 | 从表格标题或上文段落取名称 |
| 部分厂商官网从 worker 网络不可达或证书链不完整 | 只能使用搜索摘录，或该厂商不被采纳 | 生产节点网络核实；证书链问题不放宽校验 |

## 建议顺序

1. 沙箱租约过期接管与断连、存储失败注入验收；导出件的 WPS 视觉分页验收（B11），需要装有 WPS 的环境。
2. 证书页等其他材料的标注（B05），check 语义模式（B09 阶段二）。
3. 看板、卡片状态与 SSE（U01、U02）。
4. score、agent、记忆、用量与部署。
