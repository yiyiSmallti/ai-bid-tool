---
kind: changelog
---

# 变更记录

按日期记录已交付的范围。每条范围的机制说明见[机制笔记](README.md#机制笔记)。

## 2026-10-02：按官方档位选择推理强度与抽取历史

- 平台模型目录登记服务商公布的推理强度档位（如智谱 GLM-5.3 的 low、high、max），每档带请求参数、
  批次大小和 Anthropic effort，并标出官方默认档；运营后台可编辑档位并逐档测试。
- `bid req extract --reasoning LEVEL` 选择档位，不选时用官方默认档；未登记的档位以
  `unsupported_reasoning` 失败，未分档的模型忽略并警告。`--dry-run` 列出可用档位。
- 每次抽取的要求独立保存：`req list` 默认显示每个文档最近一次成功的抽取，`--job` 查看指定一次，
  `req history` 列出全部抽取；要求带 `job_id` 与 `reasoning`。Result 契约升为 1.2。
- 迁移 `0014`。决定见 [ADR 0004](adr/0004-extractions-per-reasoning-level.md)，机制见
  [reasoning-levels.md](notes/reasoning-levels.md)。
- 模型返回空引用或空要求文字的条目按单条拒绝（`empty_quote`、`empty_text`），不再导致整次抽取失败；
  抽取中的意外错误也会保留已发生调用的用量，日志只记录异常类型与调用栈。
- 当时的完整回归：570 项通过；Playwright 端到端检查通过。

## 2026-10-02：服务商额度用完的提示

- 服务商返回额度用完、欠费或套餐失效（HTTP 402、`insufficient_quota`、`billing_error`、
  智谱 1113、1308–1321 中的额度与套餐类错误码）时，作业以 `provider_quota_exhausted` 失败，
  不再重试三次；报错写明重置时间（服务商给出时）并提示联系系统管理员。智谱 1302、1305
  限流仍按可重试处理。机制见 [llm-providers.md](notes/llm-providers.md)。

## 2026-10-01：Word 招标文件按文档位置引用

- Word 直接解析为段落块和表格单元格块，按标题样式或编号识别章节；合并单元格、嵌套表格、
  内容控件都有稳定位置，页眉页脚、文本框等跳过的内容列在解析警告里。
- Word 来源的要求引用章节路径加段落或单元格，`page` 为 `null`，新增 `location`；
  Result 契约升为 1.1。引用原文必须落在所指的那一个块里。硬性规则 6 相应修改，
  决定见 [ADR 0003](adr/0003-word-structural-citations.md)，机制见
  [docx-citations.md](notes/docx-citations.md)。
- 引用比对忽略全角半角与弯直引号差异；要求按原文顺序列出。
- 模型输出被截断或不符合格式时自动把批次对半拆开重发：先按章节，再按块，长页面或长单元格再按行；
  只有单行仍超限才失败。
- 抽取批次并发发送（`BID_LLM_CONCURRENCY`），默认批次 8,000 字、输出上限 32,000 token，
  每次调用有总时限；`BID_LLM_REQUEST_OPTIONS` 可向请求附加服务商参数。
- [evals/extract_tender.py](../evals/extract_tender.py) 支持 Word，报告引用通过数与 ★ 召回。
- 迁移 `0013`。
- 样例招标文件（WPS，2,117 块）实测：GLM 关闭思考 135 秒抽出 449 条，446 条引用通过，
  ★ 条款 23/23 覆盖（含规则补抽）。
- 引用不通过改为逐条拒绝：其余条目照常保存，被拒条目的位置、原文与原因写入作业结果
  `rejected` 并给出警告；全部不通过时仍以 `invalid_citation` 失败。
- 当时的完整回归：556 项通过。

## 2026-10-01：预付余额与充值卡密

- 单位预付余额与只能新增的流水；平台计费调用按售价扣除，余额必须大于 0 才能提交平台计费作业，
  不设透支额度。
- 平台管理员批量生成、作废卡密，直接增减或设定单位余额；单位管理员在 `/app/org/billing`
  或 `bid billing redeem` 兑换卡密。
- 计费币种由 `BID_BILLING_CURRENCY` 配置；售价与应收字段去掉 `usd` 后缀。
- 单位登录页按账号列出所属单位（`/auth/orgs`）。
- 迁移 `0012`。决定见 [ADR 0002](adr/0002-prepaid-billing.md)，机制见
  [prepaid-billing.md](notes/prepaid-billing.md)。
- 当时的完整回归：540 项通过；Playwright 端到端检查通过。

## 2026-10-01：平台运营后台

- 平台管理员由部署配置指定，登录需密码与 TOTP，验证码只能用一次，15 分钟内失败 5 次锁定；
  平台会话 30 分钟，与单位会话和 API 令牌互不通用。
- 运营后台（`/app`）与 `bid platform` 命令：开通、停用、启用单位，一次性设置密码链接，
  平台模型目录与测试，按月用量与应收（可导出 CSV），平台审计。
- 停用单位后，其登录、会话和令牌立即失效。
- 设为默认的目录模型用于所有单位的要求抽取，用量按成本价与售价分别记录。
- 迁移 `0010`、`0011`。跨单位访问的决定见 [ADR 0001](adr/0001-platform-console-access.md)，
  机制见 [platform-console.md](notes/platform-console.md)。
- 当时的完整回归：514 项通过；Playwright 端到端检查通过。

## 2026-10-01：真实 LLM 抽取

- 新增 Anthropic 与 OpenAI 兼容两个 httpx adapter，按 `BID_LLM_*` 配置平台模型；
  机制见 [llm-providers.md](notes/llm-providers.md)。
- 失败调用之前已完成批次的用量照常记录；新增错误码 `provider_refused`、`invalid_provider_output`。
- 空环境变量按未设置处理。
- 新增 [evals/extract_tender.py](../evals/extract_tender.py) 用于真实服务验收。
- 当时的完整回归：483 项通过；尚未调用真实服务。

## 2026-10-01：代码审查修复

- 所有路由的数据库事务改为在返回响应之前提交（`Depends(context, scope="function")`），
  提交失败不再表现为成功响应。
- 登录的 PBKDF2 校验移入线程，不再阻塞事件循环。
- 作业遇到退出码 3 的暂时性错误（如对象存储不可用）时重新排队，不再直接失败。
- 当时的完整回归：467 项通过。

## 2026-10-01：八轮独立范围

以下各轮均在实施前获得契约确认。真实 LLM 接入由用户决定暂缓，生产抽取明确报错。
第八轮结束时的本机回归为 467 项通过，远程 CI 未运行。

1. **基础链路**：全局 User 与 Membership、范围令牌、任务、加密文件存储、PDF 分页解析与本地 OCR、
   抽取契约与引用校验、Procrastinate 后台作业、远程与本地两种 CLI 模式。
   迁移 `0001`、`0002`。笔记：[tenant-isolation.md](notes/tenant-isolation.md)、
   [background-jobs.md](notes/background-jobs.md)。
2. **产品元数据**：不可变修订、乐观并发、任务固定选择与显式替换、审计。
   迁移 `0003`。笔记：[versioned-resources.md](notes/versioned-resources.md)。
3. **软件功能声明**：产品关联、声明状态、修订与任务固定。
   迁移 `0004`。笔记：[versioned-features.md](notes/versioned-features.md)。
4. **证书声明**：资格/人员类型、可未知日期、显式日期检查、独立权限范围。
   迁移 `0005`。笔记：[versioned-certificates.md](notes/versioned-certificates.md)。
5. **单位资料声明**：可未知文本字段、独立权限范围。
   迁移 `0006`。笔记：[versioned-profiles.md](notes/versioned-profiles.md)。
6. **单位私有 DOCX 模板**：原文件加密修订、任务固定、受权下载。
   迁移 `0007`。笔记：[versioned-templates.md](notes/versioned-templates.md)。
7. **证书 PDF 原件**：原件与声明形成新修订，旧修订不可回填、不继承。
   迁移 `0008`。笔记：[versioned-certificate-files.md](notes/versioned-certificate-files.md)。
8. **未确认 PDF 页来源**：固定原件指定页的 150 dpi PNG 归档，恒未确认、不能进入 draft/export。
   迁移 `0009`。笔记：[unconfirmed-evidence-sources.md](notes/unconfirmed-evidence-sources.md)。

## 2026-09-30：设计文档

- 初始化仓库，提交 [AI 标书工具设计文档](AI%20标书工具设计文档.md) v0.2 草稿。
