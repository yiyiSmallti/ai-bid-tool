---
kind: readme
---

# AI Bid Tool

帮助投标单位读懂招标文件、查证技术参数、校验标书并预估得分，每条结论可追溯到原文或真实证据。
工具以 SaaS 方式部署，单位之间数据隔离；网页、内置 agent 和外部 agent 共用 API 与 `bid` CLI。

AI Bid Tool helps bidders (投标人) understand tender documents (招标文件), verify
technical parameters, check bids (bid documents; 标书), and estimate scores. Every
conclusion traces back to the tender text or real evidence (证据). The tool is
deployed as SaaS with data isolated between orgs (organizations/tenants; 单位).
The web interface, built-in agent and external agents use the same API and `bid` CLI.

Implemented and remaining scope is maintained in [the roadmap](docs/plan/roadmap.md).
Requirement extraction needs a configured model; see
[Configure the extraction model](docs/guides/development.md#configure-the-extraction-model).

## Quick start

Requires Python 3.12, [uv](https://docs.astral.sh/uv/) and PostgreSQL 16.

```sh
uv sync --frozen --extra dev --python 3.12
LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 uv run python scripts/test_runtime.py start --root /tmp/ai-bid-test
source /tmp/ai-bid-test/environment.sh
uv run pytest -q
```

For development database setup, the API and worker, and Docker Compose, see
[Local development and verification](docs/guides/development.md). For command usage,
see [bid CLI](docs/guides/cli.md).

## Documentation

- [Design](docs/design.md): authoritative scope, architecture, data model and phases.
- [agent.md](agent.md): hard rules, technology stack, directory structure and workflow; required reading before a coding agent starts work.
- [Documentation index](docs/README.md): guides, mechanism notes, roadmap and changelog.
- [Glossary](docs/glossary.md): canonical English terminology and Chinese domain terms.
