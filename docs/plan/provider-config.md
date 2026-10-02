---
kind: plan
---

# 单位自带模型与平台模型选择契约

状态：**后端与 CLI 已实施。** 单位后台界面属于
[单位后台契约](org-console.md)。实现机制见
[provider-config.md](../notes/provider-config.md)，本页记录范围、已解决决定和验收要求。

## 交付范围

- 单位管理员选择平台目录模型，或配置自带密钥的 Anthropic / OpenAI 兼容模型。
  自带模型声明服务商、模型、HTTPS base URL、JSON 模式、官方推理档位及可选成本单价。
- 抽取与卡片起草共享 `llm_extract` 配置、调用准入和即时用量记账。
- 配置采用不可变修订；使用 `expected_revision` 防止覆盖并发更新，保留历史作业引用。
- 服务端以独立 `BID_SECRETS_KEY` 加密密钥，响应只显示末四位。CLI 从
  `BID_PROVIDER_KEY` 或当前用户所有、权限为 0600 的 `--key-file` 读取密钥。
- `provider set/list/history/test` API 与 CLI；测试使用合成连接检查页，不写入招标资料。
- DeepSeek 余额查询、自带密钥的额度错误提示、UTC 本月实际记录的 token 和估算成本。

不包含任务级模型覆盖、OCR/视觉/搜索配置、网页实现、密钥轮换工具或月度厂商预算限制。

## 输入与输出

权威模型是 [provider_contracts.py](../../server/app/schemas/provider_contracts.py) 中的
`ProviderConfigInput`、`ProviderConfigSet`、`ProviderConfigView` 和 `ProviderTest`。
CLI JSON 文件使用 `ProviderConfigInput`，不允许把密钥写进 JSON。API 使用
`ProviderConfigSet.api_key` 写入密钥；该字段没有输出表示。

| 命令 | 路由 | 权限与行为 |
| --- | --- | --- |
| `bid provider list` | `GET /providers` | `provider:read`；可选目录、生效修订、实时余额及本月用量 |
| `bid provider history` | `GET /providers?history=true` | `provider:read`；全部修订及各修订本月用量，不查询历史密钥余额 |
| `bid provider set --input FILE [--key-file FILE]` | `POST /providers` | 人工 admin 的 `provider:write`；新增修订，冲突返回 409 |
| `bid provider test --capability llm_extract [--reasoning LEVEL]` | `POST /providers/test` | 人工 admin 的 `provider:write`；运行一次有准入和记账的探测调用，等待结果 |

所有命令沿用 Result 的七字段结构，注册到 `bid schema`。测试失败返回
`ok=false`、安全错误、作业 ID 和已记账费用，CLI 使用错误指定的退出码。
权限不足返回 403；跨单位身份或对象仍统一不可见。

## 已解决决定

| 问题 | 决定 |
| --- | --- |
| 付费方式 | 沿用[预付计费](../notes/prepaid-billing.md)：平台调用提交时预检，每次调用前预留，收到用量后立即扣费 |
| 自带密钥是否受限 | 平台 `charge=0`，不检查预付余额；仍计入作业累计调用上限，保存准入记录与实际用量；可选厂商成本不视作平台费用 |
| 模型解析顺序 | 单位最新修订 → 平台已启用默认模型 → 未配置；API/worker 不再使用 `BID_LLM_*` 作为隐式模型回退，独立 adapter/eval 仍可使用这些设置 |
| 能力名称 | 保留 `llm_extract`，同时用于抽取和模型卡片起草，不新增另一份起草配置 |
| 推理档位 | 复用平台 `ReasoningLevel` 与请求选项校验，默认档位必须存在，不允许覆盖 adapter 的输出上限 |
| 作业固定时点 | 提交时固定配置修订和模型身份，worker 按固定修订解析；换单位配置不改变已排队作业，换目录修订会使旧作业明确失败并要求重新提交 |
| 旧缓存 | 模型版本包含配置修订身份；新配置产生新抽取与起草缓存，旧作业保留旧引用 |
| 省略密钥 | 仅相同服务商、相同 base URL 的自带配置更新可以复用并重新加密旧密钥；首次配置、跨服务商或跨端点必须提供密钥 |
| 生效配置 | 每能力最新修订生效；选择平台模型也产生单位修订；不提供删除历史或重置为自动跟随平台默认的操作 |
| 测试作业 | 使用无 task/document 的 `provider_test`，仍通过 Processor 和 JobExecution；不重试、不制造业务资料，每次显式测试是新作业 |
| 余额查询 | 仅 DeepSeek 官方 HTTPS 主机的已知 base URL 查询 `/user/balance`，不跟随重定向；其他服务商显示 unsupported；查询失败不阻塞抽取 |
| 本月用量 | UTC 月份，按所有配置及各修订聚合本系统记录；缺成本单价时成本为 null 并列出未定价调用数，不伪造为零 |
| 历史凭据 | 密文绑定单位和修订，保留历史调用所需密钥；丢失独立加密密钥后明确失败，不回退其他密钥或模型 |

## 数据与权限边界

唯一迁移为 `0020`，分支基线 `down_revision="0019"`，合并时由维护者重新串联。
迁移细节及代码位置见[机制笔记](../notes/provider-config.md#how-it-works)。

`provider:read` 对所有现有角色开放，也可授予 API 令牌；`provider:write` 只授予
人工 admin，在令牌签发服务和数据库 CHECK 两层禁止。数据库触发器还核验配置写入、
探测作业创建的人工 admin 上下文；配置修订不可更新或删除。配置和测试均写不含密钥的审计。

## 验收

[API/processor 关口测试](../../server/tests/test_provider_config.py) 和
[CLI 与外部 HTTP 边界测试](../../server/tests/test_provider_client.py) 覆盖：

1. 双单位配置、历史、作业及用量隔离；无上下文读不到，跨单位引用失败。
2. 自带配置、平台指定、平台默认、未配置四条解析路径；抽取与起草共享配置。
3. 人工 admin、其他角色与令牌权限；数据库拒绝非法配置写入和令牌范围。
4. 修订冲突、历史不可变、排队任务固定旧修订、更新后新缓存。
5. 密钥加密、上下文绑定、CLI 私有文件、输出与错误无明文密钥。
6. 自带密钥零平台费用、无预付检查，仍通过调用上限和即时记账；平台调用仍受余额约束。
7. 额度错误不重试，按付费方提示处理；DeepSeek 余额返回类型严格校验，重定向不被跟随。

数据库迁移、FORCE RLS、API 到 processor 的数据库流程必须在维护者的隔离 PostgreSQL
测试实例中运行；当前沙盒无法连接该实例，非数据库检查不能代替这些验收。
