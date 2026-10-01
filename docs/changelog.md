---
kind: changelog
---

# 变更记录

按日期记录已交付的范围。每条范围的机制说明见[机制笔记](README.md#机制笔记)。

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
