---
kind: readme
---

# AI 标书工具

帮助投标单位读懂招标文件、查证技术参数、校验标书并预估得分。每条结论都能追溯到
招标原文或真实证据。工具以 SaaS 方式部署，单位之间数据隔离；网页、内置 agent 和
外部 agent 都通过同一套 API 与 `bid` CLI 工作。

已实现和尚未实现的范围见[路线](docs/plan/roadmap.md)。抽取要求需要先配置模型，
见[配置抽取模型](docs/guides/development.md#configure-the-extraction-model)。

## 快速开始

需要 Python 3.12、[uv](https://docs.astral.sh/uv/) 和 PostgreSQL 16。

```sh
uv sync --frozen --extra dev --python 3.12
LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 uv run python scripts/test_runtime.py start --root /tmp/ai-bid-test
source /tmp/ai-bid-test/environment.sh
uv run pytest -q
```

初始化开发数据库、启动 API 与 worker、使用 Docker Compose，见
[本地开发与验证](docs/guides/development.md)；CLI 用法见 [bid CLI](docs/guides/cli.md)。

## 文档

- [AI 标书工具设计文档](docs/AI%20标书工具设计文档.md)：完整范围、架构、数据模型与分期，以此为准。
- [agent.md](agent.md)：硬性规则、技术栈、目录结构和工作方式，coding agent 动手前必读。
- [文档索引](docs/README.md)：指南、机制笔记、路线和变更记录。
