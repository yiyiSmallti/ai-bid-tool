---
kind: plan
---

# 契约草案：单位自带模型与平台付费模型

状态：**待批准，未实施。** 对应[路线](roadmap.md)中的 P04。平台目录与平台默认模型的计费
已经上线，机制见 [platform-console.md](../notes/platform-console.md)；本草案只剩单位自带模型
与单位自选平台模型。

## 目标

- **平台付费模型**：平台运营方配置一组模型及售价，单位选用后按用量付费。
- **单位自带模型**：单位填写自己的服务商、模型与密钥，费用由单位直接向服务商支付。
- 每条用量记录写明付费方和金额，为后续额度与计费（F09）提供依据。

## 平台模型目录

目录由平台运营后台维护，字段见 `PlatformModelSet`
（[platform_contracts.py](../../server/app/schemas/platform_contracts.py)）。单位选择目录中的哪个模型，
由下面的单位配置决定；未配置时使用平台默认模型。

## 单位配置

新表 `provider_configs`：NOT NULL `org_id`、FORCE RLS、同次附带双单位隔离测试。
每个单位每种能力最多一条生效配置，修改产生新修订，旧修订保留用于追溯历史作业。

```python
class ProviderConfigSet(Contract):
    capability: Literal["llm_extract"]
    source: Literal["platform", "org"]
    platform_model_id: str | None          # source=platform 时必填
    provider: Literal["anthropic", "openai"] | None   # source=org 时必填
    model: str | None
    base_url: str | None
    json_mode: Literal["json_schema", "json_object"] = "json_schema"
    input_usd_per_mtok: float | None = None   # 单位自填，仅用于展示自身成本
    output_usd_per_mtok: float | None = None
    expected_revision: int | None = None

class ProviderConfigView(Contract):
    id: UUID
    org_id: UUID
    revision: int
    capability: str
    source: str
    platform_model_id: str | None
    provider: str
    model: str
    base_url: str | None
    key_last4: str | None                  # 密钥本身永不返回
    updated_by: UUID
    updated_at: datetime
```

- 密钥通过 `BID_PROVIDER_KEY` 环境变量或 0600 文件传给 CLI，不接受命令行参数。
  服务端用独立的 `BID_SECRETS_KEY` 加密保存，不再与文件加密共用一个密钥。
- 解析顺序：单位生效配置 → 平台默认模型 → 未配置（抽取明确失败）。
- 作业开始时解析配置并把配置修订 ID 写入作业；缓存键包含该修订 ID。

## 用量与计费归属

`input_tokens`、`output_tokens`、`platform_model_id`、`charge` 已由迁移 `0010` 加入，
`usd` 即服务商成本。本草案只再新增一列 `provider_config_id`（使用的单位配置修订，未配置时为
null），并把单位自带模型的 `charge` 记为 0，汇总中归为 `org` 计费类别。

## 额度用完与余量

- 服务商返回额度用完、欠费或套餐失效时，作业以 `provider_quota_exhausted` 失败、不重试，
  报错带服务商给出的重置时间（如有）。平台模型提示联系系统管理员（已实现）；单位自带模型
  改为提示单位管理员到服务商充值或续订。
- `bid provider list` 与单位后台的模型页显示自带密钥的余量：
  - 服务商提供余额接口的，测试与查看时实时查询并显示余额和币种。DeepSeek 为
    `GET /user/balance`（`is_available`、`balance_infos`）。
  - 智谱 Coding Plan 的额度查询接口待确认；确认前显示"该服务商不提供余量查询"。
  - OpenAI、Anthropic 的普通 API 密钥无法查询余额，同样显示不支持。
  - 所有服务商都附带本系统记录的本月用量（token 与按单位自填价格估算的成本）。
- 余量查询只读、不计费，失败时显示"暂时无法查询"，不影响抽取。

## 权限

- 新范围 `provider:read`、`provider:write`。只有 admin 角色有 `provider:write`。
- API 令牌永远不能获得 `provider:write`，与 `evidence:confirm`、`export` 一样在服务和
  数据库两层拒绝：agent 不能替单位授权新的模型或密钥。
- 设置、测试和切换配置都写审计记录，审计中不含密钥。

## CLI 与 API

| 命令 | 路由 | 说明 |
| --- | --- | --- |
| `bid provider list` | `GET /providers` | 平台目录（只含 id、模型、售价）与本单位生效配置 |
| `bid provider set --input FILE` | `POST /providers` | 新建或更新配置；`expected_revision` 冲突返回 409 |
| `bid provider test --capability llm_extract` | `POST /providers/test` | 用一页合成文本发起一次真实调用，返回耗时与用量，并按归属写入用量 |
| `bid provider history` | `GET /providers?history=true` | 全部修订 |

输出沿用 Result 1.0 七字段；新增命令进入 `bid schema` 与 CLI 快照。

## 验收

1. 双单位：A 的配置、密钥、用量对 B 不可见；缺上下文拒绝。
2. 解析顺序三种情况各跑一次真实作业链（Mock 服务商），用量的 `billing`、金额正确。
3. 令牌申请 `provider:write` 在服务与数据库两层被拒绝。
4. 密钥不出现在任何响应、日志、审计、错误中；只显示后四位。
5. 修改配置后，历史作业仍指向旧修订；新作业不命中旧缓存。

## 待你决定

- 平台付费的结算方式：预付额度扣减，还是按月出账。这决定是否需要在调用前检查余额。
- 单位自带模型的用量是否计入平台额度（仅统计、还是也受限额约束）。
- 是否需要任务级覆盖（某个任务换用别的模型）。本草案不含，后续可加。
