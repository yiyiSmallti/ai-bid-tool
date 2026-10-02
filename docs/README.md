---
kind: readme
---

# 文档索引

文档遵循 [Seiso Convention 0.2.0](https://seiso.fog.moe/0.2.0/convention)：每篇声明一个
`kind`，每个事实只在一处维护。种类映射见仓库根目录的 [seiso.toml](../seiso.toml)。

## 设计与规则

- [AI 标书工具设计文档](AI%20标书工具设计文档.md)（plan）：完整设计，以此为准。
- [agent.md](../agent.md)（agents）：硬性规则与工作方式。

## 指南

- [本地开发与验证](guides/development.md)：测试、开发数据库、API 与 worker、容器、OCR。
- [bid CLI](guides/cli.md)：登录、招标流程、资源维护、结果处理、令牌。
- [沙箱运行时准备与验收](guides/sandbox-runtime.md)：Colima/Docker、runsc、Unix/mTLS 控制通道及复验步骤。

## 机制笔记

英文，结构为 Problem / Usage / How it works / Pitfalls / Code。

- [tenant-isolation.md](notes/tenant-isolation.md)：RLS、单位上下文、文件隔离。
- [response-cards.md](notes/response-cards.md)：人工响应卡片、证据确认、原子处置与三表初稿。
- [model-drafting-redaction.md](notes/model-drafting-redaction.md)：模型起草输入快照、外发遮挡和双文本引用核验。
- [background-jobs.md](notes/background-jobs.md)：作业状态、取消、重试。
- [llm-providers.md](notes/llm-providers.md)：抽取与响应起草的模型调用、批次、错误与计费。
- [docx-citations.md](notes/docx-citations.md)：Word 按章节、段落、表格单元格引用。
- [reasoning-levels.md](notes/reasoning-levels.md)：按官方档位选择推理强度、抽取历史。
- [platform-console.md](notes/platform-console.md)：平台运营后台、TOTP、单位启停、模型目录计费。
- [prepaid-billing.md](notes/prepaid-billing.md)：预付余额、充值卡密、扣费与拦截。
- [versioned-resources.md](notes/versioned-resources.md)：产品修订与任务快照。
- [versioned-features.md](notes/versioned-features.md)：软件功能声明。
- [versioned-certificates.md](notes/versioned-certificates.md)：证书声明与日期检查。
- [versioned-profiles.md](notes/versioned-profiles.md)：单位资料声明。
- [versioned-templates.md](notes/versioned-templates.md)：私有 DOCX 模板。
- [versioned-certificate-files.md](notes/versioned-certificate-files.md)：证书 PDF 原件。
- [unconfirmed-evidence-sources.md](notes/unconfirmed-evidence-sources.md)：未确认 PDF 页来源。
- [provider-config.md](notes/provider-config.md)：单位模型修订、独立密钥加密、模型解析、调用计费与厂商余量。
- [human-section-exports.md](notes/human-section-exports.md)：人工 Word 响应章节导出、固定清单、候选与发布关口、证据页附件及签名下载。
- [org-console.md](notes/org-console.md)：单位端任务恢复、解析抽取、响应卡人工审阅、起草费用预览与初稿缺口控制台。
- [sandbox-execution.md](notes/sandbox-execution.md)：隔离执行、作业授权、产物溯源、清理与下载。
- [sandbox-fetch.md](notes/sandbox-fetch.md)：允许名单、DNS/IP 固定、抓取配额与可信请求回执。
- [screenshot-evidence.md](notes/screenshot-evidence.md)：截图像素脱敏与隐私放行、响应卡图片证据、原型交付决定、分析准入计费及失效重算。

## 决策记录

- [0001 平台运营后台的跨单位访问](adr/0001-platform-console-access.md)（adr）
- [0002 预付余额与充值卡密](adr/0002-prepaid-billing.md)（adr）
- [0003 Word 招标文件按文档位置引用](adr/0003-word-structural-citations.md)（adr）
- [0004 按官方档位选择推理强度，每次抽取独立保存](adr/0004-extractions-per-reasoning-level.md)（adr）
- [0005 模型提议与人工确认分离的响应卡片](adr/0005-human-confirmed-responses.md)（adr）

## 计划与记录

- [剩余范围与路线](plan/roadmap.md)（plan）：覆盖矩阵、已知缺陷、待定决定。
- [Rust 标注契约草案](plan/annotation.md)（plan）：待批准的下一范围。
- [单位模型配置契约](plan/provider-config.md)（plan）：单位自带模型与平台模型选择，已实施。
- [导出契约](plan/export.md)（plan）：按单位 Word 模板人工导出偏离表与证据附件，部分已实施，待 Word/WPS 视觉分页验收。
- [截图与证据配图契约](plan/screenshots.md)（plan）：截图与厂家资料取证、脱敏、模型原型与响应证据，Phase A 已实施。
- [沙盒契约](plan/sandbox.md)（plan）：不可信生成内容与内置 agent 的隔离执行环境，已实施，待真实隔离验收。
- [单位后台契约](plan/org-console.md)（plan）：网页端招标、抽取与响应卡审阅流程，已实施，付费起草待绑定契约。
- [起草预览绑定契约草案](plan/drafting-binding.md)（plan）：付费起草绑定预览哈希与用户消费上限，待批准。
- [变更记录](changelog.md)（changelog）：已交付范围。
