---
kind: adr
---

# 0006 平台服务凭据的数据库存储与访问边界

日期：2026-10-05。状态：**已接受。**

## 背景

平台运营方需要在 `/app` 平台后台维护厂商凭据。平台模型的 `credential` 字段目前由
[llm.py](../../server/app/providers/llm.py) 的 `credential_value` 映射到环境变量；搜索和
独立模型适配器从 [Settings](../../server/app/core/config.py) 读取密钥。修改这些值需要
维护部署配置，不同 API、worker 进程也可能持有不同值。

单位 BYOK 已由 [ProviderSecrets](../../server/app/core/provider_secrets.py) 加密，并绑定
单位及配置修订，机制见 [provider-config.md](../notes/provider-config.md#revisions-and-authority)。
平台服务凭据不归属于某个单位，不能放入虚构单位来绕开
[agent.md 硬规则 1](../../agent.md#硬性规则任何情况下都不得违反)。
[ADR 0001](0001-platform-console-access.md) 和 [ADR 0002](0002-prepaid-billing.md)
已经批准的全局表例外不自动覆盖本提案。

## 决定

数据结构、路由、迁移步骤与验收定义在
[平台凭据契约计划](../plan/platform-credentials.md)。

- 批准一个范围受限的新全局表 `platform_credentials`，无 `org_id`，不采用租户 RLS。
  它只保存平台持有的出站服务凭据、加密和管理元数据；不得保存单位 BYOK、业务内容、
  登录身份、平台管理员名单或用于解锁自身的密钥。不新增全局凭据历史明文/密文表。
  这一例外及专用角色边界纳入 `agent.md` 硬规则 1。
- 迁入数据库的是 `BID_PLATFORM_CREDENTIAL_<NAME>`、`BID_PERPLEXITY_API_KEY`、
  `BID_LLM_API_KEY`。后者覆盖 standalone/eval，不能成为单位作业的隐式后备模型。
  单位 BYOK 保留原租户表和权限。新增云 OCR、Embedding 或其他厂商前须确认能力契约，
  不把此表扩成任意键值配置中心。
- 启动、信任根和基础设施身份留在部署秘密注入渠道：`BID_DATABASE_URL`、迁移及本提案
  专用数据库连接、`BID_ENCRYPTION_KEY` 及退役密钥、`BID_TOKEN_KEY`、`BID_SECRETS_KEY`
  及退役密钥、平台 TOTP seeds、bootstrap 密码、S3/MinIO 凭据、沙箱 mTLS 私钥。
  S3 和沙箱身份虽不是数据库解锁密钥，也属于恢复、服务启动和双端身份配置，首期不迁移。
  管理员名单、端点、桶名、模型名、非秘密运行参数继续使用各自既有配置渠道；不得夹带
  URL userinfo、查询串 token 或额外认证头来重新引入厂商密钥。
- 复用 BYOK 的独立 Fernet 加密域 `BID_SECRETS_KEY`，与数据、签名密钥及所有退役根密钥
  保持分离。认证加密的 JSON envelope 包含版本、`domain=platform_credential`、行 UUID、
  不可变 name、purpose、provider、规范化 endpoint、secret_version 和 api_key。解密后逐项比对
  数据库行；复制密文到另一行、改名、改端点或跨 BYOK 域使用都失败。这里的 envelope 是
  Fernet 加密的结构化载荷，不另引入每行 DEK/KMS 包裹层。
- 区分两种轮换：后台“替换密钥”覆盖本行密文并增加 secret_version/revision；根密钥轮换
  扩展现有 `app.admin rotate-encryption`，增加 provider-secrets 范围及
  `BID_SECRETS_KEY_PREVIOUS` 只解密 keyring，同时重加密平台凭据和 BYOK 历史修订。
  现有命令只覆盖数据域，不能直接用于凭据域。重加密不改变凭据值、业务修订或作业身份；
  BYOK 的只追加限制只允许迁移属主走专用的密文重包裹通路，不能由运行角色更新。
  轮换可重入、可计数核验，坏密文必须报告失败；旧密钥保留至在线数据和备份恢复要求均满足。
- 只有经现有平台登录、密码及 TOTP 签发的有效平台会话可以列出、创建、替换、启停、移除或
  测试凭据。沿用 [ADR 0001](0001-platform-console-access.md) 的会话寿命与部署管理员名单。
  单位用户、单位会话和 API tokens 一律不可管理或读取凭据；不存在可授予 token 的平台
  credential scope。API/CLI/页面从不回读完整密钥，包括创建响应；只提供指纹和末四位。
  “只写”针对所有人类/客户端接口；必要解密只发生在可信服务调用路径的短暂内存中。
- 沿用 `bid_platform_fn` 的受限函数属主模式，但不把新密文访问权扩大给该已有角色。
  新 `bid_platform_credentials_fn` 为 `NOLOGIN NOSUPERUSER NOBYPASSRLS`，只拥有固定
  凭据函数所需的表权限，不获任何单位业务表权限。单位运行角色 `bid_app` 无该表的直接
  权限，也无这些函数的 EXECUTE/角色成员资格。平台管理专用连接只可执行元数据和写函数；
  API/worker 的凭据解析专用连接只可执行按已验证消费对象定位的单条密文解析函数。
  二者不获表的 SELECT/UPDATE，不继承属主，不拥有任何 `BYPASSRLS` 能力。
  函数固定 `search_path`、全限定对象名，撤销 PUBLIC EXECUTE，禁止动态 SQL。
  TOTP 在平台应用边界核验，数据库不把可伪造的 `SET app.*` 当作认证；不同连接角色是
  单位 SQL 路径的隔离边界，不宣称能隔离已被攻陷且持有根密钥的整个服务进程。
- 所有成功变更与 `platform_audit_logs` 的追加在同一事务；测试先持久记录开始，再记录
  允许名单内的结果。读取管理元数据也记访问事件；拒绝、冲突、轮换和导入记有限的分类信息。
  不记录请求 body、密文、原文、Authorization、远端响应、任意厂商错误文字或操作人输入的
  自由文本。审计不能 UPDATE/DELETE，失败不能伪装成已保存或已测试成功。
- 单位作业仍先按 [现有 Provider 配置契约](../notes/provider-config.md#resolution-and-cache)
  固定 BYOK 修订或平台目录身份。平台身份只固定凭据的引用，API/worker 在每一次真实出站
  调用及每次重试前读取已提交的凭据状态并解密最新 secret_version。首期不缓存密文、明文
  或“可用”判定跨调用；连接池只复用无认证头的连接。替换后下一次解析采用新值；停用或
  移除后的下一次解析失败，数据库不可达也失败，不回落到环境变量、旧密钥、其他模型或搜索服务。
  已经解析并开始的一次调用属于在途请求，不能承诺撤回；紧急吊销还须由厂商撤销旧 key。
- 移除采用 tombstone：擦除在线密文、保留行身份及审计，不复用 name；仍引用该凭据的
  目录和排队作业明确不可用。替换密钥不改变目录版本、价格或模型结果缓存键；改变目录
  的凭据引用仍走原目录修订校验。返回已完成的缓存结果不产生新外发，也不重新使用密钥。
- 提供显式、一次性的受保护导入。切换完成后，API、worker、standalone/eval 检测到迁移范围
  内的旧厂商密钥环境变量或直接配置值便拒绝启动；不采用静默忽略。这样可以暴露遗留
  部署和漏迁移的调用方，避免运营后台显示已停用而旧进程继续使用环境密钥。

## 权衡与后果

一张全局表能让共享厂商账号有单一维护入口，同时需要明确的新权限例外。新增两个受限
连接池和函数属主增加部署工作，但仅靠共享 `bid_app` 连接上的应用判断，不能证明单位
数据库角色读不到平台密文。数据库及根密钥仍须分开备份和保管；数据库泄露本身不能解密，
数据库与根密钥同时泄露则不能靠本设计防护。

每次调用多一次数据库读取，换来可界定的换钥/停用生效点，避免 TTL 或失效通知丢失导致
旧密钥复活。数据库故障期间停止新的付费请求。在线旧值不保留，替换错误时须重新提交可用
密钥，不能“一键找回”；备份可能保留旧密文，移除不等于厂商撤销或备份中的密码学擦除。

连接测试默认仅做允许名单内的认证元数据探针，不生成模型内容、不传单位材料，也不触发
收费搜索；无法提供这种探针的适配器返回 `unsupported`。它不能证明推理能力、余额或模型
授权。收费合成调用的计量方案另见[已定决定](../plan/platform-credentials.md#已定决定)，
不能借测试凭据新增匿名全局 UsageRecord 或绕过单位隔离。
