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

## 机制笔记

英文，结构为 Problem / Usage / How it works / Pitfalls / Code。

- [tenant-isolation.md](notes/tenant-isolation.md)：RLS、单位上下文、文件隔离。
- [background-jobs.md](notes/background-jobs.md)：作业状态、取消、重试。
- [llm-providers.md](notes/llm-providers.md)：抽取模型调用、批次、错误与计费。
- [platform-console.md](notes/platform-console.md)：平台运营后台、TOTP、单位启停、模型目录计费。
- [versioned-resources.md](notes/versioned-resources.md)：产品修订与任务快照。
- [versioned-features.md](notes/versioned-features.md)：软件功能声明。
- [versioned-certificates.md](notes/versioned-certificates.md)：证书声明与日期检查。
- [versioned-profiles.md](notes/versioned-profiles.md)：单位资料声明。
- [versioned-templates.md](notes/versioned-templates.md)：私有 DOCX 模板。
- [versioned-certificate-files.md](notes/versioned-certificate-files.md)：证书 PDF 原件。
- [unconfirmed-evidence-sources.md](notes/unconfirmed-evidence-sources.md)：未确认 PDF 页来源。

## 决策记录

- [0001 平台运营后台的跨单位访问](adr/0001-platform-console-access.md)（adr）

## 计划与记录

- [剩余范围与路线](plan/roadmap.md)（plan）：覆盖矩阵、已知缺陷、待定决定。
- [Rust 标注契约草案](plan/annotation.md)（plan）：待批准的下一范围。
- [模型配置契约草案](plan/provider-config.md)（plan）：单位自带模型与单位自选平台模型，待批准。
- [变更记录](changelog.md)（changelog）：已交付范围。
