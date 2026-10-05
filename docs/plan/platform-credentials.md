---
kind: plan
---

# 平台后台管理服务凭据

状态：**已批准，推荐默认值全部采纳。** 对应[路线图](roadmap.md) F10、P04。

[agent.md](../../agent.md#工作方式)要求先确认 Pydantic 模型、Provider 接口和 CLI JSON。
[批准的契约类型](platform-credentials/platform_credentials_contracts.py)定义输入、输出和内部接口；
运行类型位于 [platform_credentials.py](../../server/app/schemas/platform_credentials.py)。
全局表例外、信任边界与取舍由已接受的 [ADR 0006](../adr/0006-platform-credentials.md)定义。
机制与实现入口见[平台凭据机制](../notes/platform-credentials.md)。

## 目标与范围

平台运营人员经平台 TOTP 登录后，在 `/app/platform/credentials` 创建并启用凭据，
给目录模型选择匹配的凭据，查看消费对象、替换密钥、停用、移除、测试认证连接。
API、worker 与 standalone/eval 共用数据库权威值，变更不依赖修改厂商密钥 env 文件。
单位作业仍遵守原 Provider 选择、模型版本、费用准入与 RLS；不增加单位读取平台密钥的接口。

精确迁移边界如下。未列入的厂商能力需要后续契约，不自动接收任意秘密名称。

| 配置 / 现有入口 | 归属与接入要求 |
| --- | --- |
| `BID_PLATFORM_CREDENTIAL_<NAME>`；[llm.py](../../server/app/providers/llm.py) `credential_value/platform_llm` | 入库，`purpose=catalog_llm`；沿用 `PlatformModel.credential` 名称，大小写映射不改变 |
| `BID_PERPLEXITY_API_KEY`；[search.py](../../server/app/providers/search.py) `create_search_provider` | 入库，`purpose=vendor_search`；固定 Perplexity endpoint，唯一未移除服务凭据 |
| `BID_LLM_API_KEY`；[evals/extract_tender.py](../../evals/extract_tender.py) `settings/run`、`create_llm` | 入库，`purpose=standalone_llm`；独立工具使用受限服务连接，不再允许从 env 或 Settings 构造真实密钥。provider/model/推理参数仍为非秘密设置；与凭据 provider/endpoint 必须匹配 |
| 单位 `ProviderConfig.encrypted_key`；[provider-config.md](../notes/provider-config.md) | 保持原单位权限和不可变修订；只为共用加密根增加受限重包裹能力，不迁入全局表 |
| `BID_DATABASE_URL`、`BID_MIGRATION_DATABASE_URL`、`BID_DATABASE_PASSWORD`、`BID_OWNER_PASSWORD`、`BID_BOOTSTRAP_PASSWORD` | 继续部署注入；新专用连接 `BID_PLATFORM_DATABASE_URL`、`BID_CREDENTIAL_DATABASE_URL` 同属 bootstrap secrets，不能存入它们访问的数据库 |
| `BID_ENCRYPTION_KEY`、`BID_ENCRYPTION_KEY_PREVIOUS`、`BID_TOKEN_KEY`、`BID_SECRETS_KEY`、拟新增 `BID_SECRETS_KEY_PREVIOUS`、`BID_CLI_KEY` | 继续外部秘密注入；根密钥及退役密钥不进凭据表 |
| `BID_PLATFORM_TOTP_SECRETS`、`BID_PLATFORM_ADMIN_EMAILS` | 身份信任根继续部署控制，应用不能为自己增加运营人员 |
| `BID_S3_ACCESS_KEY`、`BID_S3_SECRET_KEY`，MinIO root identity | 首期保留部署秘密；[storage.py](../../server/app/providers/storage.py) `S3Storage` 在启动时建立客户端，Compose 还复用这些值启动 MinIO，独立迁移另立契约 |
| `BID_SANDBOX_TLS_KEY/CERT/CA`、双方 SHA256 pin；`SEARXNG_SECRET` | 保留沙箱/SearXNG 基础设施信任配置；不收集私钥文件到本表 |
| `BID_SEARCH_URL`、`BID_LLM_BASE_URL`、S3 endpoint/bucket、转换/OCR 参数 | 非秘密配置；拒绝 URL 内认证值。SearXNG、Gotenberg、本地 OCR 目前没有应迁入此表的出站 API key |

原 `platform_llm` 在 OpenAI 自定义 endpoint 缺 key 时允许匿名调用。本提案要求平台目录
必须引用有效凭据，不再把“缺失/停用”解释成匿名许可；匿名本地模型不在批准范围内，需另立显式契约。
所有真实调用入口均移除 env fallback，测试仍可显式注入假 Provider，不能把测试旁路注册到生产。

## 现有机制与必要接入点

| 来源 | 实施时必须处理的差异 |
| --- | --- |
| [ProviderSecrets](../../server/app/core/provider_secrets.py) | 复用 Fernet、SecretStr、身份绑定及显式失败；新增平台域，不能伪造 org UUID。当前只有单一 secrets key，无轮换 |
| [admin.py](../../server/app/admin.py) `rotate_encryption/ENCRYPTED_COLUMNS` | 当前只轮换 `BID_ENCRYPTION_KEY`；不能把凭据列直接加入数据域清单。BYOK UPDATE trigger 也需专用维护通路 |
| [platform.py](../../server/app/services/platform.py) `identify/model_view/test_model/audit_entries` | 平台会话可复用；`credential_configured` 必须改查安全 DB 状态；audit details 原样返回，写端必须严格白名单 |
| [configured.py](../../server/app/providers/configured.py) `resolve_configured/model_identity` | 保留 BYOK 修订、目录 ID/revision 的作业绑定；密钥替换不递增目录 revision |
| [llm.py](../../server/app/providers/llm.py) `HTTPExtractor`、[structured.py](../../server/app/providers/structured.py)、[prototyping.py](../../server/app/providers/prototyping.py) | adapter 当前可能持有整个作业期间的密钥。改为每次 HTTP 外发前解析，覆盖抽取、起草、check/score、Vision、原型生成及重试 |
| [screenshots.py](../../server/app/api/screenshots.py) `search_provider`、[processor.py](../../server/app/jobs/processor.py)、[product_simulation.py](../../server/app/services/product_simulation.py) | 搜索预检、作业与模拟拟投统一服务选择；预检仅查元数据，不能解密 |
| [prepaid-billing.md](../notes/prepaid-billing.md#admission-and-the-spending-bound) | 凭据检查接入逐次外发前的控制路径，不绕过 `JobExecution.admit/accounted_call`、UsageRecord 与结算 |
| [providers.py](../../cli/bid_cli/providers.py)、[client.py](../../cli/bid_cli/client.py) | 复用安全 key-file 读取与 `platform=True` 会话模式，不发送 X-Org-Id；本提案不用厂商 key 环境变量作为普通 CLI 输入 |

## 数据结构与迁移轮廓

### 表和约束

仅新增全局 `platform_credentials`，不创建独立绑定表、密文历史表、平台 Job 或全局用量表。
运行对象关系和合法取值以 [CredentialSpec/View](platform-credentials/platform_credentials_contracts.py)
为准。

| 列 | 约束 / 意义 |
| --- | --- |
| `id uuid`、`name varchar(40)` | 主键；name UNIQUE，`^[a-z0-9_]{1,40}$`，不可改名/复用，即使 tombstone 也占用名称 |
| `purpose`、`provider`、`endpoint` | 创建后不可变。purpose 为 catalog_llm / standalone_llm / vendor_search；provider 为 anthropic / openai / perplexity；endpoint 规范化为 HTTPS base URL，不含 userinfo、query、fragment，仅默认端口 |
| `encrypted_key text`、`envelope_version` | 非 removed 时有密文；removed 时必须 NULL。使用 ADR 的身份绑定 envelope；`api_key` 从不落 plaintext 列 |
| `fingerprint`、`last_four` | SHA-256 原 key 的前 16 个十六进制字符，加 `sha256:` 前缀；末四个 ASCII 字符。只作视觉辨认，不作授权、唯一性或去重依据；key 最少 16、最多 4096 个非空白可打印 ASCII 字符，不接受口令/短 PIN |
| `state`、`revision bigint`、`secret_version bigint` | active / disabled / removed；revision 从 1 开始，每次业务变更 +1；secret_version 仅替换 key 时 +1。根密钥重包裹不改变二者 |
| `created_at`、`updated_at`、`updated_by` | 有时区时间和已验证平台 actor 邮箱；不得接受客户端提交这些列 |

非 catalog 用途在 `state != removed` 上对 purpose 建唯一部分索引；同一平台服务只能有一个
未移除凭据，disabled 仍占位。移除后允许以新 name/id 新建该服务凭据，旧作业不能自动改绑。
catalog 凭据可供多个模型共用；`platform_models.credential` 增加到 name 的外键，拒绝物理删除。
目录保存和每次解析都校验 purpose、provider、规范化 endpoint 一致；Anthropic 的空 base_url
按现有适配器的官方默认值规范化。任何改 endpoint 的需求须新建凭据、显式更新模型目录，
不能用复用旧 key 的“编辑地址”把认证头发往新服务。

状态机：create 默认 disabled，可显式 active；replace 保持原状态；set-active 支持 active
与 disabled；remove 从任一非终态进入 removed 并清空密文。所有变更必须带 expected_revision，
对行加锁后比较；冲突返回 409。removed 不可 replace/enable；相同状态请求返回原状态且不
增加 revision，记一次无变更审计。停用、移除允许仍有目录引用，返回影响清单，后续真实调用
显式失败；不自动切模型、不删除历史用量。启用时验证当前密文可解密，连接测试不是强制前置。

### 数据库角色和函数

| 角色 / 连接 | 允许的访问 |
| --- | --- |
| `bid_app`（单位 API/worker 普通业务事务）及现有 `bid_platform_fn` | 无新表 SELECT/INSERT/UPDATE/DELETE、无新函数 EXECUTE、无新角色成员资格；单位表原 RLS 不变 |
| `bid_platform_credentials_fn`，NOLOGIN/NOSUPERUSER/NOBYPASSRLS/NOINHERIT | 固定函数属主；只对新表有 SELECT/INSERT/UPDATE，对 platform_models 有消费引用查询的有限列权限，对 platform_audit_logs 有 INSERT 和限速/探针状态需要的有限读取；无单位业务表权限，无 DELETE |
| `bid_platform_app`，LOGIN/NOSUPERUSER/NOBYPASSRLS/NOINHERIT | `BID_PLATFORM_DATABASE_URL` 专用池；只可执行 list/show/create/replace/set-active/remove/import、probe-begin/finish 及元数据访问审计函数；无可返回密文的函数，无单位表权限 |
| `bid_credential_reader`，LOGIN/NOSUPERUSER/NOBYPASSRLS/NOINHERIT | `BID_CREDENTIAL_DATABASE_URL` 专用池；只可执行按消费对象查询的 readiness 元数据函数及 resolve-catalog、resolve-service、resolve-probe、resolve-operator-check 单条绑定密文函数；无枚举、写入、普通明文接口 |
| 迁移属主 | 建表/授权/重包裹维护；不用于 API/worker 的任何请求池 |

函数均撤销 PUBLIC 默认 EXECUTE，固定 `search_path=pg_catalog` 并全限定业务表，禁止动态 SQL，
调用角色也无 schema CREATE/角色管理能力。catalog resolver 接收目录 ID 与 expected_model_revision，
从可信目录读取 name，不能接受单位 HTTP 参数指定 name。service resolver 接收固定 service enum
及服务端已经选定的 credential_id；probe resolver 只接受尚未结束、未过期限且绑定 revision 的
probe_id。resolve-operator-check 接收目标 UUID、expected_revision 和 import/activate 用途，
仅由已验平台身份的服务路径调用，允许 disabled，不允许 removed，不注册 HTTP/CLI 接口。
它依赖解析连接及应用验权，不声称数据库能独立验证 TOTP；dry-run 可以只读比较而不写审计。
解析角色持有者是可信服务，不是终端用户；内部解析只解密一个明确目标。
平台普通写池不返回 ciphertext；需要验证/探测时先由平台会话授权，再使用内部解析池。

平台 operator 只由 `platform.identify` 构造，不从 body/email 字段取信；单位 role/token 的请求
在调用专用池前拒绝。数据库函数不能验证 Web TOTP，不把 GUC 中的 actor 当成已认证；函数
只信任有 EXECUTE 的专用连接，actor 仅作为已核验调用的审计归属。

### 迁移和部署顺序

1. `agent.md` 硬规则 1 纳入批准例外；迁移为 `0038_platform_credentials.py`（接在记忆迁移 `0037` 之后）。
   建立表、角色、函数、审计约束，默认拒绝一切未显式授权的访问。
2. 先建目录 name 外键为 NOT VALID；新目录写仍校验引用。将已有同名但 provider/endpoint
   不一致的目录列为迁移冲突，先明确拆分；不自动将一个 key 授权给多个 endpoint。
3. 在维护窗口停止接收相关付费提交并排空在途工作，准备 API/worker/standalone 的一致版本、
   bootstrap 根密钥与专用连接。不能让仍读 env 的旧 worker 与新后台同时服务，以免停用失效。
   本计划不执行任何服务操作。
4. 用已有 bootstrap 登录能力启动只供运营人员使用的新版本，进程不携带旧厂商 key env。
   从受保护的旧环境导出文件显式导入；这个暂时文件不是新的长期凭据源。核对引用完整性和
   选定状态，验证外键，再开放生产任务。旧匿名目录须显式处理，缺失 key 不制造占位密文。
5. 更新部署模板/指南和真实秘密注入源，删除三类旧厂商 key，设置明确的搜索 provider，
   确认每个副本都通过“禁止旧 env”检查；验证无密钥的业务配置检查和 fake-provider E2E 后切流。
6. 禁止破坏性 downgrade。失败时保留表/审计/加密根，以维护模式修复向前；不能直接回退到
   旧 env 版本并继续业务，否则会违背已停用/移除状态。必要的灾难恢复按保留的 DB/根密钥备份
   重建，再重新核验厂商吊销状态；不得在文档或验证工件中保存秘密备份。

## API 与 CLI 契约

路由沿用既有无版本前缀 `/platform/*`，全部复用平台 `operator` 依赖，不接收 org_id。
非平台会话（包括单位管理员、过期会话、API token）统一 `invalid_session` / HTTP 401；
已认证运营人员访问不存在 ID 为 `not_found` / 404。无查看原文、导出原文或下载密文的路由。

| HTTP / Result.command | 请求 | 成功 data / items |
| --- | --- | --- |
| `GET /platform/credentials` / `platform credential list` | `CredentialListQuery`；按 name 升序游标分页 | `CredentialListData` / `CredentialView[]` |
| `GET /platform/credentials/{id}` / `platform credential show` | UUID | `CredentialData` / `[]`，包含 catalog/service consumers |
| `POST /platform/credentials` / `platform credential create` | `CredentialCreate` | `CredentialData` / `[]` |
| `POST /platform/credentials/{id}/replace` / `platform credential replace` | `CredentialReplace` | `CredentialData` / `[]` |
| `POST /platform/credentials/{id}/active` / `platform credential set-active` | `CredentialSetActive` | `CredentialData` / `[]` |
| `POST /platform/credentials/{id}/remove` / `platform credential remove` | `CredentialRemove` | tombstone `CredentialData` / `[]` |
| `POST /platform/credentials/{id}/test` / `platform credential test` | `CredentialTest` | `CredentialProbeData` / `[]`；失败仍给安全 probe 结果 |
| `POST /platform/credentials/import-env` / `platform credential import-env` | `CredentialImportRequest`，最多 100 条、整体 body 最多 512 KiB | `CredentialImportData` / `CredentialImportItem[]` |

固定路径 import-env 在 UUID 路径之前注册。创建和变更成功 HTTP 200，列表及详情 200；不返回
Location 中的任何敏感信息。新接口加 `Cache-Control: no-store`。已有模型接口保留
`credential`、`credential_configured` 输出形状；后者表示引用 active 且密文存在，不证明
解密、网络或厂商授权成功。模型保存增加上述 DB 引用校验，凭据列表不解密。

CLI 命令如下，均支持 `--json`，无交互提问，不在 argv、JSON 配置文件或 env 接受新厂商 key：

```text
bid platform credential list [--state STATE] [--purpose PURPOSE] [--after-name NAME] [--limit 100] --json
bid platform credential show --id UUID --json
bid platform credential create --input METADATA.json --key-file FILE --json
bid platform credential replace --id UUID --expected-revision N --reason REASON --key-file FILE --json
bid platform credential set-active --id UUID --expected-revision N --active|--inactive --reason REASON --json
bid platform credential remove --id UUID --expected-revision N --reason REASON --json
bid platform credential test --id UUID --expected-revision N --json
bid platform credential import-env --manifest MANIFEST.json --env-file FILE [--dry-run] --json
```

create 的 metadata 使用 CredentialCreateInput，不含 api_key；命令边界从 key-file
读取后构造 CredentialCreate。文件须调用人拥有、0600、普通文件、无符号链接及符号链接父路径，
长度受限，复用 BYOK 的安全打开方式；允许去掉单个末尾换行，不静默 strip key 中的空白。
普通命令不新增 `--key VALUE` 或 `BID_PROVIDER_KEY` 输入。API 的 api_key 为唯一一次性秘密字段；
SecretStr/`exclude=True` 防止通用 model_dump 泄漏，真正发请求时只在专用传输构造处显式取值。
错误不输出 Pydantic input/context、CLI 文件内容或任何 argv/session。HTTP 日志及代理不得捕获请求 body。

复用 `bid platform login` 和加密 CLI session，调用 `platform=True`，不发送单位头。
本地模式也必须有显式平台登录和 PostgreSQL 权限边界，不允许 owner URL 或本地标志跳过 TOTP。
新命令/输入/输出/错误码必须注册 [schema.py](../../cli/bid_cli/schema.py) 的发现清单。

### Result、错误和退出码

复用 [Result/Cost](../../server/app/schemas/contracts.py) 的七键和已发布版本，不改已有
命令字段类型；新增命令为加法。api_key、ciphertext、envelope、认证头都不能进入 data/items/warnings。
error 位置沿用 `data.error = {code,message,exit_code}`，message 为服务端固定文案。
现有 Client.request 的非 2xx 分支只抛 ServiceError，会丢弃额外 data.probe；新 credential
传输路径必须校验并保留 CredentialErrorData 的安全 probe 投影，CLI 输出仍为同一七键 Result。
不能因此透传任意厂商/HTTP body，也不改变其它命令的错误投影。
所有管理操作及本提案的认证元数据探针均零 LLM/OCR 用量、`cost.usd=0.0`；有收费能力的端点
不准冒充零费用探针。duration_ms 是本次操作实测耗时。

```json
{
  "ok": false,
  "command": "platform credential test",
  "data": {
    "error": {"code": "credential_removed", "message": "Credential has been removed", "exit_code": 4}
  },
  "items": [],
  "warnings": [],
  "cost": {"llm_tokens": 0, "ocr_pages": 0, "usd": 0.0},
  "duration_ms": 1
}
```

成功详情为 `data.credential: CredentialView`，列表为 items；完整无秘密示例由契约文件
`RESULT_EXAMPLES` 构造。probe 失败可在 data.probe 附本次已脱敏的结果，不泄漏响应内容。

| 条件 / code | HTTP / exit | 语义 |
| --- | --- | --- |
| 成功、导入 dry-run、相同内容重放导入 | 200 / 0 | dry-run 不写记录、不写审计、不探测网络 |
| 缺参数、非法 key/file/endpoint、未知字段 `invalid_input` | 422（CLI 本地无 HTTP）/ 2 | 修正输入；不回显原值 |
| `revision_conflict`、name/purpose/import conflict、`credential_reference_mismatch` | 409 / 2 | 刷新或修正绑定，不自动覆盖 |
| `invalid_session` / `not_found` | 401 / 4；404 / 4 | 不因请求的 credential ID 暴露存在性给无权者 |
| `credential_missing/disabled/removed` | 409 / 4 | 没有后备 key；从单位作业返回既有 provider_unavailable，不暴露全局 name/指纹 |
| `credential_unreadable` | 503 / 4 | 绑定校验/密文/根密钥错误，人工修复；不按网络错误重试 |
| `credential_backend_unavailable`、`credential_audit_unavailable` | 503 / 3 | 不外发、不假装写成功；提交结果不明先重读 revision |
| `credential_probe_auth_failed` | 422 / 4 | 厂商拒绝认证，不自动换 key |
| `credential_probe_unsupported`、`credential_probe_endpoint_rejected` | 422 / 4 | 不支持安全探针或目标违反出站策略，不发送请求 |
| `credential_probe_timeout/unavailable/interrupted` | 503 / 3 | 未证明认证成功，操作人可显式重试 |
| `credential_probe_rate_limited` | 429 / 3 | 仅安全、范围受限的 retry_after_seconds，不返回厂商 header |
| `credential_env_forbidden` | 启动/standalone exit 4 | 只列配置变量名，不打印值；服务不就绪 |
| 部分成功 | 新 credential API/CLI 不使用 exit 5 | 单次变更与每批导入原子；admin 根轮换可部分重包裹，另行报告 exit 5 |

## 控制台页面

沿用 [App.vue](../../web/src/App.vue)、[router.js](../../web/src/router.js) 的平台区域和会话，
增加“凭据”导航；单位导航不增加入口，前端路由隐藏不能替代 API 验权。

| 页面 / 动作 | 可见内容与行为 |
| --- | --- |
| `/app/platform/credentials` | name、purpose、provider、状态、指纹/末四位、更新时间、消费对象数量；按 purpose/state 过滤、游标翻页；“已配置”不等于“连接已验证” |
| 新建页 `/app/platform/credentials/new` | 名称、用途、provider/endpoint、一次性 password 输入、默认停用；解释启用及不可改名的影响 |
| 详情 `/app/platform/credentials/:id` | 安全元数据、目录模型 ID/revision/enabled/default、固定服务选择；链接现有模型页面；不显示单位、任务或 BYOK 数据 |
| 替换弹窗 | 只提交新值、原因代码和 expected_revision；旧值不预填，展示关联消费对象；提交成功清空输入，冲突后必须刷新 |
| 启停/移除 | 显示同一 revision 的影响清单；停用阻断下一次解析，移除清除在线密文且终态；用户在该操作中确认具体影响，不串联额外审批流程 |
| 测试连接 | 仅认证元数据；展示本次 tested_revision/secret_version、结果、耗时；若期间已更新则标“旧版本结果”。不展示供应商原始输出，不把 unsupported 当通过 |
| 目录模型编辑页 | credential 输入改为匹配 provider/endpoint 的 active 凭据选择；已有断引用显示明确不可用及管理链接，不自动换默认模型 |

浏览器不把输入 key 存进 session/localStorage、URL、错误报告、analytics、草稿或下载文件。
只有输入框和发往本站的单次 TLS 请求暂时持有原文；保存/取消/路由离开后清空表单。
fingerprint/last_four 使用文本渲染，不能进入 v-html。生产操作不采集含输入框值的截图或 HAR。

## 解析顺序、缓存与作业边界

1. 单位任务仍优先使用已经固定的单位 ProviderConfig 修订。若 source=org，仅按原 org/config
   envelope 解密；失败即失败，不使用平台凭据。若选择平台，按固定目录 ID/revision；无单位
   配置时按现有启用的平台默认；没有可用默认则 DisabledLLM。不存在 env 模型补位。
2. 提交/预检/展示只查询凭据引用与状态，保存非秘密身份。catalog 使用现有目录 revision
   固定 credential name；服务搜索/standalone 固定所选 credential_id/provider/endpoint，
   旧搜索作业若没有这个新字段，需重新提交，不能猜测绑定。
3. 准备真实调用时，provider 通过 `PlatformCredentialResolver.resolve_for_call` 从专用连接
   读取一条最新提交记录，校验消费对象、状态与 envelope；仅返回短期 ResolvedCredential。
   保留原计费准入：无有效凭据不发 HTTP、不计厂商调用，已创建预留但未发送的路径必须按
   可证明未外发的规则关闭/释放，不能留下未知收费。准入等待后重新检查凭据状态，再构造
   此次请求头；不得在持有数据库事务/行锁时等待远端 HTTP。
4. 每次 HTTP 重试、分批、结构化调用、搜索请求均重新解析；shared adapter 只保存引用，
   不把 secret 放进 Settings 副本、default headers、长期 httpx/boto 客户端或作业参数。
   明文不跨调用缓存；也不缓存 missing/disabled，首期不引入 Redis、TTL 或通知失效机制。
5. 解析的 read-committed 行读取是本次调用的生效边界：更新提交后才开始的解析看到新状态；
   已经取得旧值的本次在途请求可能完成。无“数据库提交后所有远端调用立即取消”的保证。
   每个已计量调用的内部诊断可记录 credential_id/secret_version，单位输出沿用原许可投影，
   不向 org users/token 暴露平台凭据元数据。
6. secret 替换不改变目录 revision、provider_identity 或结果缓存键；仍使用旧目录的排队作业
   下一次请求可用新 key。目录换绑定/服务换 id 属于身份变化，旧作业失败并要求重新提交。
   停用/移除阻止新请求，但允许读取已经完成、权限仍有效的结果缓存；缓存读取不会外发。

搜索增加非秘密 `BID_SEARCH_PROVIDER=perplexity|searxng|disabled`，默认 disabled，迁移清单
要求显式选定。perplexity 只找唯一未移除 vendor_search 凭据；searxng 只用 BID_SEARCH_URL，
不解密平台 key；缺失/停用 Perplexity 不回落 SearXNG。standalone provider=disabled 不调用；
启用时只解析 standalone_llm 用途，使用 DB 身份并核对非秘密模型设置。无数据库的 standalone
在线调用不再支持，fake/offline 测试保持可用。搜索费用归属仍按[路线图待定事项](roadmap.md#待定决定)，
此提案不把费用转嫁给单位，也不修改现有计费规则。

## 测试连接与审计

平台认证探针只能使用已保存凭据，可对 disabled 凭据测试，但不能对 removed 测试。
expected_revision 固定该次测试，probe-begin 校验 revision 并先写审计；内部 resolve-probe
只放行这次授权的 revision。替换发生在解析前则返回冲突；解析后发生替换允许旧 probe 完成，
结果明确 tested_revision，不写“当前已通过”标记。probe 授权有效 30 秒，完成后不再可解析。

Provider 接口只允许认证元数据操作：已确认支持的适配器可使用认证模型列表/身份端点，
实现前按厂商主文档确认准确路径、认证要求和不收费性质；未确认及 Perplexity 默认 unsupported，
绝不拿生成/搜索请求充当零费用探针。单次最多 5 秒、64 KiB 响应、不自动重试、不跟随跳转，
只访问凭据绑定 endpoint，出站规则拒绝 loopback/private/link-local/metadata 地址与 DNS rebinding；
私有 endpoint 须部署方显式允许。HTTP 200 必须符合允许名单内认证响应结构才 passed，公开
健康页或 HTML 不算认证成功。不返回响应 body/headers/模型名/URL/远端 request id。

每运营人员每分钟最多 10 次、每凭据每分钟最多 5 次，借用平台审计与事务锁跨副本计数；
超限不发请求。probe-start / probe-finish 使用相同 probe_id；进程中断只留 start，审计展示为
interrupted（结果未知），不伪造 finish。finish 审计持久化失败返回 audit_unavailable，不报告 passed。
真实内容生成仍用原模型/单位测试契约；现有 platform model test 的计量缺口见
[预算计划待决定](budget.md#待决定)，本提案不借它扩展无单位收费探针。

审计 action 使用 `credential.create/replace/set_active/remove/import/read/probe_start/probe_finish`
及 `credential.rewrap`；details 使用 CredentialAuditDetails 允许的字段，actor/object_id/outcome/time
使用原审计列。读列表的 read 事件不记录用户提交的任意 query；失败只记允许列表内代码与已知
目标 UUID。变更与成功审计同事务；拒绝/失败在失败事务外另行提交，以免回滚丢失。导入 dry-run
遵守纯预检不写审计的约定；失败审计也不得导致把 key 记录进异常文本。表权限/触发器保证审计
只能追加，新维护通路也不能 UPDATE/DELETE 它。

未认证请求在专用凭据连接前拒绝，普通审计连接只追加 `platform.credential_denied`，
使用固定内部 actor 和 `invalid_session` 分类；它不属于 `credential.*` 探针授权事件。
已认证请求的无效输入仍使用凭据审计函数记录固定错误码。

## 一次性导入与禁止 env 后备

import-env 是显式恢复/迁移工具，不是服务启动钩子。CLI 从受保护 env-file 只读取 manifest
指名的三类源变量；不执行 shell、不 source 文件、不展开 `$()`/变量替换，不上传整个 env 文件、
文件路径或无关 bootstrap secret。遇到不能无歧义解析的赋值直接报输入错误。API 接收的 source_env
只是校验和追溯标签，不让远端 API 读取操作系统环境；secret 仍只在 write-only 字段中。

manifest 为 CredentialImportManifest：逐条明确 name/purpose/provider/endpoint/active/source_env。
同一 env 的 key 若关联不同 endpoint 不自动复制授权。先 `--dry-run` 验证源值格式、目录映射与
冲突；data/items 只列目标名和计划动作，不含 fingerprint/key。正式每批在一个事务内创建和审计。
已有同名、身份/状态/实际解密 key 完全一致时 skipped，不用截断 fingerprint 判断相等；removed、
不同值或不同元数据为 import_conflict，整批零写入。跨多批不宣称整体原子；中断后同 manifest
可重复，绝不默认覆盖现有 key。将源值不可用/目录未覆盖逐项列为安全错误，不吞掉跳过。

导入通路只向管理连接发送加密后的待写行。已有值比较由受信服务使用 resolve-operator-check
完成，不向平台客户端返回明文；仅返回同值判定给导入事务，正式写事务再锁定并核验该次
比较的 revision，防止比较后并发修改被跳过。

切换后，API/worker/standalone 启动检查这些旧名称及非空直接 Settings 字段，出现就以
credential_env_forbidden 拒绝启动；`BID_PLATFORM_CREDENTIAL_*` 全前缀检测，不能只检查
模板的 MAIN。即使 DB 已有同名有效值也拒绝；不只是“DB 优先”，也不静默忽略。
bootstrap-only 的迁移导入客户端可以读取指定 env-file，但该文件不注入任何服务进程。
清理服务 env/config、旧 secret 挂载与导入临时文件后再开放任务；遗留实例未退出不能宣称
平台停用已经覆盖全部调用方。

## 加密根轮换与故障恢复

厂商 key 替换用 replace；静态加密根用既有管理入口扩展：

```text
uv run python -m app.admin rotate-encryption --scope provider-secrets
```

新增 `--scope data|provider-secrets`，默认 data 保持现有输出及数据域行为。provider-secrets
读取 BID_SECRETS_KEY 与 BID_SECRETS_KEY_PREVIOUS，遍历平台 active/disabled 行和每个单位
BYOK 的全部历史修订，不访问 removed 空密文，不用 data key 解密。所有当前/退役 data、token、
secrets 密钥须合法且跨域不重用。旧 BYOK envelope 仍按原 org/config 校验并保持语义，不因
引入平台 domain 就使历史 key 无法读取。

新 key 先分发为各副本“可读的 previous”，仍写旧 current；所有副本确认可读新旧后，再逐个
切换新 current 并保留旧 previous。全体完成后运行重包裹，避免滚动期间新密文被旧副本读坏。
每行锁定或 compare-and-swap 原 ciphertext，避免扫描覆盖并发 replace。平台成功重包裹与
credential.rewrap 审计同事务；BYOK 维护路径仅迁移属主可执行，保留 org 上下文及复合外键，
只能更换等价密文，不能改 key、业务列、id/revision、删除修订或关闭整表触发器。

`RotationReport` 分别统计 platform_checked/rewritten、org_revisions_checked/rewritten、failed，
不输出密文、key 或坏载荷。失败项按稳定 ID 记录受限运维报告，可重入、已用新 key 的行只校验
不重写。failed=0 时 exit 0；有成功校验或重包裹且有失败时 exit 5；零成功且所有失败可重试
时 exit 3；零成功且至少一个不可重试失败时 exit 4，启动配置错误也是 4。已在 current key 下
通过绑定校验的行同样计作成功，不能用 rewritten=0 推断全失败。只有
失败为零且第二遍 rewritten=0，才可从在线 keyring 移除旧 key；备份生命周期内仍保管旧 key。
这不是厂商密钥回滚：在线旧密钥已经被替换/移除时只能重新提交，不存在历史明文读取接口。

## 实施顺序与测试计划

按“迁移与权限 → 加密/轮换及 resolver → API/CLI → 控制台与导入切换”的依赖实施，
每条真实入口都必须接上 resolver，不能留下 env 分支。以下场景定义验收门禁；
数据库套件由主集成会话在隔离 PostgreSQL 运行，不以静态检查替代运行时验收。

| 验收场景 | 必须证明的结果 |
| --- | --- |
| 两单位 A/B + org admin/普通人/API token/无上下文直接 SQL | 任何 org 会话访问平台凭据路由失败，错误不枚举 ID；bid_app/现有 bid_platform_fn 直接表查询、DML、函数调用、SET ROLE 均拒绝，GUC 伪装 actor 无效；原 org RLS 无扩大 |
| 专用角色最小授权 | 管理角色不能查密文，reader 无枚举/写权限，NOLOGIN 属主不可登录；PUBLIC EXECUTE/schema CREATE/角色成员资格不存在；固定函数和允许列均由 catalog 查询核验 |
| 平台 TOTP 流程 | 真正平台登录后可执行操作；过期/移出部署名单拒绝；token 不能申请 credential scope；本地 CLI 无 bypass |
| 创建→列表→详情→模型选择→worker 调用 | 页面与 API/CLI Result 只显示 fingerprint/last_four；真实消费对象正确；两个假厂商验证新值只在请求 header 出现，业务文件、队列参数、缓存与审计均无 key |
| 错误/异常/测试泄漏 | 假厂商把 key 放在 body、header、model name、错误内容、超长响应和跳转 URL；最终 data/items/warnings、日志、审计、错误验证均无 key/ciphertext。key-file 无权限/符号链接/超长/控制字符均失败 |
| 加密绑定和根密钥 | 跨行、改 name、改 provider/endpoint、跨 BYOK/org/revision 复制密文均失败；错误根/坏密文明确 unreadable；所有当前/退役根跨域复用被拒绝 |
| 并发替换与禁用 | 两运营人员 expected_revision 冲突只一人成功；多 worker 下一次调用看到新 key；已解析在途请求的边界可复现；disable/remove 后再调不外发，无 env/旧 key/SearXNG/匿名后备 |
| 模型/搜索身份与缓存 | 替换 key 不改目录模型/cache identity，排队调用用新值；换目录引用/服务 id 使旧作业失败；已完成缓存仍可授权读取；LLM 分批、重试、structured、原型、Vision、搜索及 eval 不漏路径 |
| 审计原子性与故障 | 注入审计失败时变更不提交、探针不发出或不宣称成功；失败审计不被业务回滚；探针中断留下 start；平台审计 UPDATE/DELETE 被拒绝 |
| probe 限制 | 支持者仅认证元数据请求；unsupported 零网络调用；每副本共享限速、超时、大小、DNS/redirect/私网限制；不创建单位业务内容、不触发厂商计费 |
| env 导入与切换 | dry-run 零写入；同值重放 skipped、冲突整批回滚；manifest 无关字段拒绝，不导入 bootstrap secrets；各启动入口检测旧 env/直接配置并拒绝，删除后 DB 值生效；显式 searxng 与 Perplexity 配置失败不互换 |
| 主密钥轮换 E2E | 建 A/B BYOK 历史及平台凭据，读新旧 key、重包裹、并发 replace、故障中断恢复、第二遍零改写、移除旧在线 key 后仍可调用；BYOK 普通 UPDATE 仍拒绝，旧备份按留存 key 可恢复 |
| Result/CLI/页面 | 七键 JSON 和 nested exit_code 快照、schema 发现完整；UI list/create/replace/disable/test/consumers 全链路与冲突反馈；不录制带真实 secret 的截图或 HAR |

使用假 Provider 和临时合成凭据跑可重复 E2E，主会话管理 PostgreSQL 生命周期。保留
`data/work/platform-credentials-validation/` 下的复现命令、脱敏 Result 快照、JUnit/检查摘要；
不在 docs 下保存验证日志、截图或证据，不保存请求 body、secret、解密值或原始 HAR。
可分享的工件只含安全视图、角色授权断言及通过/失败摘要。

契约的静态验证命令：

```sh
uv run ruff check docs/plan/platform-credentials/
uv run ruff format --check docs/plan/platform-credentials/
uv run pyright docs/plan/platform-credentials/platform_credentials_contracts.py
uv run python -c "import runpy; runpy.run_path('docs/plan/platform-credentials/platform_credentials_contracts.py')"
```

## 已定决定

| 决定 | 已批准选择 | 理由 / 影响 |
| --- | --- | --- |
| 全局表与数据库连接隔离 | 采纳 ADR 0006；独立管理/解析角色与连接池 | 能用真实 org 数据库角色证明无密文访问；增加两条 bootstrap 连接的运维工作，不能仅靠共享 bid_app + actor GUC |
| 首期迁入范围 | 三类厂商 key 全迁，包括 standalone/eval；S3/沙箱/信任根留部署渠道 | standalone 在线调用将依赖 DB；不把基础设施恢复身份纳入运行期供应商表 |
| 启用默认与匿名模型 | 新建默认 disabled，显式启用；缺 key 一律失败，匿名 endpoint 另立显式契约 | 停用不能变成匿名调用；既有匿名目录需在切换前处理 |
| 同名 key 的权威源 | 一次性显式导入后拒绝遗留 env/config 值 | 暴露漏迁移，而非用静默忽略/回落留下双重权威 |
| 搜索服务选择 | 非秘密 BID_SEARCH_PROVIDER 显式选择，默认 disabled | Perplexity 停用/缺失不能悄悄变 SearXNG；部署迁移时必须设好选择 |
| 密钥与作业身份 | 每次外发读 DB，零跨调用 key cache；换值不改模型 revision | 下一次解析可见停用/新值；代价为每次读取，接受已解析在途请求窗口 |
| 移除与恢复 | tombstone + 擦除在线密文，不复用 name；不保留历史 key | 可追溯且避免旧值复活；恢复必须重录，备份与厂商撤销仍单独管理 |
| 根密钥轮换 | 共用 BID_SECRETS_KEY 域，扩展 rotate-encryption 同时覆盖 BYOK 历史与平台凭据 | 不能留下共用根的一半无法轮换；需要 BYOK 受限重包裹通路 |
| 连接测试 | 首期认证元数据探针，Perplexity/未验证适配器返回 unsupported；不做收费合成搜索 | 保持“每次内容调用计量”和 org 隔离；如需实际收费测试，先批准专用测试单位及预算计划，不能新增无 org 用量例外 |
| 管理 CLI | 与后台同一 TOTP 会话、只读 key-file，提供显式 import-env | 支持可重复运维，不把 key 放 argv 或一般配置 JSON；不提供 token 管理能力 |

全部选择按推荐默认值批准；数据库和端到端验收通过后才可进行部署切换。
