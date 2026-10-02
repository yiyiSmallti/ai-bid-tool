# agent.md

本文件给 coding agent（Codex、Claude Code 等）阅读。动手前先读完本文件和 `docs/AI 标书工具设计文档.md`（AI 标书工具设计文档）。

## 项目概述

AI 标书工具：帮投标单位解析招标文件、抽取要求、查证技术参数并截图取证、校验标书、预估得分。
以 SaaS 方式部署，多个单位（租户）共用一套服务、数据互相隔离；CLI `bid` 是对外接口，网页看板、内置 agent 和外部 agent 都调用同一套命令。

## 硬性规则（任何情况下都不得违反）

1. **单位隔离**
   - 所有业务表必须有 `org_id`（NOT NULL）并启用 PostgreSQL 行级安全策略（RLS）。新建表时，同一次改动里必须带上 RLS 策略和隔离测试。
   - `User` 是全局身份表，是单位 `org_id` 与 RLS 要求的唯一例外，仅保存登录身份与认证信息；一个全局账号可以加入多个单位。单位归属、角色和权限保存在 `Membership` 中，`Membership` 及其余单位业务表仍必须有 `org_id`（NOT NULL）并启用 RLS。
   - 平台运营后台另有经批准的例外：全局表 `platform_models`（平台模型目录）、`platform_audit_logs`（只能新增、不能改删）和 `platform_cards`（充值卡密，只存哈希与末 4 位），以及 `NOLOGIN`、无 `BYPASSRLS` 的角色 `bid_platform_fn`。该角色只在 `orgs`、`memberships`、`usage_records`、`org_balances` 上有只读跨单位策略，只作为固定函数的属主；函数不得返回任何单位业务内容，入账只能经 `redeem_card` 和 `platform_adjust_balance`。决定与理由见 `docs/adr/0001-platform-console-access.md` 和 `docs/adr/0002-prepaid-billing.md`。
   - 登录成功不代表可以访问任意单位；切换或访问单位前必须校验有效的 `Membership`，再设置该请求的单位上下文。
   - 数据库会话通过 `SET app.current_org = ...` 设置单位上下文；应用代码不得使用绕过 RLS 的数据库角色。
   - 对象存储路径一律以 `org/{org_id}/` 开头；下载只发放带签名的短期链接。
   - 向量检索必须同时按 `org_id` 和记忆作用域过滤。
   - 后台作业必须携带单位上下文执行，不允许在作业里"顺便"查询其他单位的数据。
2. **人工确认关口**
   - `Evidence.confirmed_by` 为空的证据，不得进入 `draft` 生成的响应表，也不得出现在 `export` 导出的文件里。
   - API 令牌永远不能获得 `evidence:confirm` 和 `export` 权限；内置 agent 和外部 agent 都不能确认证据。
3. **不伪造材料**
   - 不实现任何"生成厂家页面、检测报告、证书"的功能。硬件证据只能来自真实网页或文件的截图。
   - `ui mock` 为尚未实现的功能生成的截图，必须带"设计原型"水印。
4. **模型调用只经接入层**
   - 只有 `server/app/providers/` 可以导入厂商 SDK；业务代码只依赖 Provider 接口。
   - 每次调用都要记录服务商、模型名、耗时、费用，写入 `UsageRecord`。
5. **CLI 契约**
   - 所有命令支持 `--json`，输出统一结构：`ok`、`command`、`data`、`items`、`warnings`、`cost`、`duration_ms`。
   - 退出码：0 成功；2 参数或输入错误；3 可重试失败；4 不可重试失败；5 部分成功。
   - 不做交互式提问，缺参数直接报错。
   - JSON 输出结构的不兼容变更必须升版本号；`bid schema` 输出与实现保持一致。
   - 本地模式使用本机 PostgreSQL，并保留与远程模式一致的 RLS 和权限校验；文件保存在本地目录，仍以 `org/{org_id}/` 隔离。不得用纯文件数据库替代 PostgreSQL。
6. **引用**：每条 `Requirement` 必须带来源文件和可核验的位置：PDF 为页码，Word 为章节路径与段落或表格单元格位置。引用原文必须逐字出现在所指位置；缺少有效引用的抽取结果要拒绝或标记，不能静默保存。
7. **密钥与敏感数据**：服务商密钥和令牌加密存储；日志中不得出现密钥、报价、身份证号、银行账号。
8. **记忆**：单位记忆永远不写入全局层；系统自动提议的记忆一律以 `candidate` 状态保存，经人确认后才生效。

## 技术栈

| 部分 | 选型 |
| --- | --- |
| 服务端 | Python 3.12、FastAPI（async）、SQLAlchemy 2.0、Alembic |
| 数据库 | PostgreSQL 16 + pgvector |
| 文件存储 | S3 兼容对象存储（本地开发用 MinIO） |
| 后台作业 | 暂定 Procrastinate（基于 PostgreSQL，少一个 Redis 组件）；如需更换，只改 `server/app/jobs/` |
| 数据结构 | Pydantic v2（同时用于 API、CLI 输出和 LLM 结构化输出） |
| CLI | Typer，命令名 `bid` |
| 文档处理 | PyMuPDF（PDF）、python-docx（Word） |
| 网页截图 | Playwright（BrowserProvider 的默认实现） |
| 证据标注 | Rust（clap、serde、image、sha2），编译为独立可执行文件 |
| 前端 | Vue 3 + Vite |
| 部署 | Docker Compose |
| 质量 | ruff、pyright、pytest、cargo test、GitHub Actions |

引入上表以外的大型依赖前，先说明理由并征得同意。

## 目录结构

```
.
├── agent.md
├── docs/
│   ├── AI 标书工具设计文档.md  # 设计文档（以此为准）
│   └── notes/             # 核心机制笔记，英文
├── server/
│   ├── app/
│   │   ├── api/           # 路由
│   │   ├── core/          # 配置、认证、租户上下文、加密
│   │   ├── models/        # SQLAlchemy 模型
│   │   ├── schemas/       # Pydantic 模型，CLI 输出共用
│   │   ├── services/      # 业务逻辑，每个命令一个模块
│   │   ├── jobs/          # 后台作业定义
│   │   ├── providers/     # LLM、Vision、OCR、Search、Embedding、Browser 接口与实现
│   │   └── memory/        # 记忆读写与检索
│   ├── migrations/        # Alembic
│   └── tests/
├── cli/                   # bid 命令行：远程模式调用 API，本地模式使用本机 PostgreSQL 和本地文件
├── stamp/                 # Rust 证据标注
├── web/                   # Vue 前端
├── evals/                 # 评测数据集与脚本（不在 CI 默认运行）
└── deploy/                # docker-compose 与环境变量模板
```

## 工作方式

- **先接口后实现**：新功能先写 Pydantic 模型、Provider 接口和 CLI 的 JSON 结构，等确认后再写实现。
- **纵向切片**：每次只推进一条完整链路，跑通并有测试后再开始下一条。不要一次铺开多个模块的半成品。
- **小步提交**：每次改动聚焦一件事，提交信息用英文。
- **不确定就问**：设计文档没有覆盖、或与本文件冲突的地方，先提问，不要自行假设。
- **核心机制写笔记**：完成单位隔离、后台作业、证据链、记忆检索等核心机制后，在 `docs/notes/` 下写一页英文笔记，结构为 Problem / Usage / How it works / Pitfalls / Code，供人复习。

## 测试要求

- **隔离测试必写**：测试夹具固定准备两个单位 A 和 B。每个接口、每张表都要有"用 A 的身份访问 B 的数据必须失败"的测试；资源不存在和无权访问统一返回 404。
- **关口测试必写**：未确认证据不能导出；令牌申请确认权限必须被拒绝。
- **CLI 契约测试**：对每个命令的 `--json` 输出做快照测试，结构变化必须是有意为之。
- **不调用真实外部服务**：CI 中所有 Provider 使用假实现；需要真实调用的放进 `evals/`。
- 不得为了让测试通过而放宽隔离规则、确认规则或删除测试。

## 当前任务：切片 1

目标：登录 → 创建任务 → 上传招标文件 → 解析 → 抽取要求 → CLI 输出结果，整条链路跑通。

- [ ] 仓库骨架、`deploy/docker-compose.yml`（PostgreSQL + pgvector、MinIO）、GitHub Actions
- [ ] 数据模型与迁移：Org、User、Membership、ApiToken、Task、Document、Chunk、Requirement、UsageRecord；User 为全局身份表，其余单位业务模型全部带 org_id 和 RLS 策略
- [ ] 认证：账号密码登录、API 令牌（带权限范围和过期时间）、请求级单位上下文中间件
- [ ] Provider 接口：LLMProvider、OCRProvider 各一个真实实现 + 一个测试用假实现
- [ ] 后台作业：`tender parse`、`req extract` 以作业方式执行，可查询状态
- [ ] CLI：`bid login`、`bid org use`、`bid task create`、`bid tender parse`、`bid req extract`、`bid job status`、`bid schema`，支持远程和本地两种模式
- [ ] 测试：隔离测试、CLI 契约快照测试、抽取结果的引用校验测试

完成标准：用一份公开的招标文件 PDF 端到端跑通；全部测试在 CI 通过；`docs/notes/` 下有单位隔离的笔记。

## 不要做

- 不要在 `providers/` 以外直接调用厂商 SDK。
- 不要提交任何密钥；只维护 `deploy/.env.example`。
- 不要实现设计文档"非目标"中列出的功能（编造材料、对接电子投标平台、报价策略）。
- 不要在本切片里做看板、记忆系统和证据采集，它们属于后续切片。
