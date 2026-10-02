---
kind: plan
status: "待批准，未实施"
---

# 契约草案：单位端招标任务与响应审阅控制台

状态：**待批准，未实施**。范围对应[路线图](roadmap.md)的 B02、U01、U02，
在 `web/` 的 Vue 3 应用中接入任务、解析、抽取、响应卡审阅和偏离表初稿。
本页定义页面与既有业务契约的连接方式，不授权改写响应卡规则或扩大材料能力。

## 目标、依据与边界

目标链路为：单位登录 → 创建任务并上传招标文件 → 跟踪解析 → 选择官方推理档位抽取 →
选择抽取历史 → 对照原文逐条审阅响应卡和真实材料 → 人工处置与确认 → 查看三表及缺口。
模型起草入口接入[模型起草](../notes/model-drafting-redaction.md)，费用预览在调用之前展示。
“处理完成”仅指所选抽取集合的处理情况，不表示完整标书已交付或招标文件没有漏抽。

规则归属如下；本页不另立确认、计费或模型外发规则：

| 依据 | 本页如何使用 |
| --- | --- |
| [agent.md 硬性规则](../../agent.md#硬性规则任何情况下都不得违反)、[产品设计](../AI%20标书工具设计文档.md) | 单位隔离、真实材料、人工关口、令牌禁确认及导出、CLI Result |
| [ADR 0005](../adr/0005-human-confirmed-responses.md)、[response-cards.md](../notes/response-cards.md)、[model-drafting-redaction.md](../notes/model-drafting-redaction.md) | 已采纳的人工确认决定与卡片机制；模型起草与外发遮挡已交付 |
| [reasoning-levels.md](../notes/reasoning-levels.md)、[docx-citations.md](../notes/docx-citations.md) | 官方档位、独立抽取集合、逐字引文和 Word 结构位置 |
| [platform-console.md](../notes/platform-console.md)、[api.js](../../web/src/api.js)、[router.js](../../web/src/router.js)、[App.vue](../../web/src/App.vue) | 平台/单位会话分离、同源请求、导航和现有单位计费入口 |
| [provider-config.md](provider-config.md)、[annotation.md](annotation.md) | 独立草案的边界；不把单位自带模型、标注或派生图当作可用证据能力 |

接口依据限定为本 checkout 的 [main.py](../../server/app/api/main.py)、
[response_cards.py](../../server/app/api/response_cards.py)、
[CLI 命令注册](../../cli/bid_cli/schema.py)及上表已批准契约。
路线图与响应卡草案中的旧状态文字不能代替入口核验；模型起草即使有 schema/预留表，
也不能据此判定路由可用。另一 checkout 的二期交付须在集成时核对，不作为本文现成文件。

### 真实标书规模对页面的约束

产品输入给定的中标样本为 663 页 PDF：投标函与授权书、实质性响应一览表、商务与技术
响应偏离表，约 305 页逐项技术响应，约 217 张截图/图示、约 125 页整页扫描材料，
以及合同/中标通知书等业绩、社保/证书等团队材料、164 页技术方案；几乎每页还有电子章。
这些数字用于说明工作量，不作可相加的章节预算，也不宣称本页已核验样本内容。

- 以“要求 → 响应 → 材料 → 人工决定”为操作单位，材料预览按需加载；不能把整份长 PDF
  或全部图片一次塞进页面或模型输入。
- 合同、报告、截图、团队附件与长篇技术方案存在不同材料链。首版只选择接口支持的固定
  资源字段和证书 PDF 页；其他材料如实显示尚缺支持，不能冒充证书、声明或占位图片入库。
- 三张初稿表是标书的一部分。投标函、授权书、完整方案、整本排版与附件编排不因此完成；
  电子章由政府电子投标客户端处理，本控制台不连接该平台，也不提供签章或加密上传。

## 页面结构与角色

复用 [style.css](../../web/src/style.css) 的基础样式与原生表单语义，单位工作区增加任务导航，
平台区保持独立。下列为拟 Vue 浏览器路由，**不是新增 API 路由**：

| 浏览器入口 | 页面职责 |
| --- | --- |
| `/app/org/login` | 沿用单位登录与单位选择 |
| `/app/org/tasks` | 单位任务列表及创建入口；登录后各业务角色均可进入 |
| `/app/org/tasks/:taskId` | 上传/解析状态、抽取入口和历史；只展示服务端可取回的信息 |
| `/app/org/tasks/:taskId/review?job=J` | 固定抽取 job 的要求列表、单卡编辑与审阅；可附 requirement ID 定位 |
| `/app/org/tasks/:taskId/drafts?job=J` | 组表预检、历史初稿、三张响应表、须遵守清单和缺口 |
| `/app/org/billing` | 沿用余额与充值，仅 admin 可用 |

入口守卫通过 `GET /org/current` 取得本单位角色，不从邮箱、缓存按钮或平台身份推断权限。
角色与范围以 [ROLE_SCOPES / Identity](../../server/app/services/auth.py) 和响应卡服务的
`human` 检查为准；按钮可用性只是交互提示，每次请求仍由服务端重新授权。

| 身份 | 页面可见内容与非决策操作 | 人工决策 |
| --- | --- | --- |
| `admin` | 本单位任务、要求、材料、卡片、初稿；创建、上传、解析、抽取、编辑、提交、起草、组表；余额页与遮挡设置 | 为未分类 draft 指定职责并写理由；不能跨专业确认、驳回、补材料、重开或批量处置 |
| `bidder` | 同单位业务内容；创建任务及上述业务操作；不展示余额页或模型密钥 | 商务/资格职责内确认、驳回、需补材料、重开及处置 |
| `technical` | 同单位业务内容；在已有任务上传、解析、抽取、编辑、提交、起草、组表；无创建任务入口 | 技术职责内的上述人工决定 |
| `viewer` | 要求、原文、受权材料、卡片/修订、作业与初稿只读 | 无编辑、运行、确认、批量处置或评论入口 |
| 平台运营身份 | 原平台运营页面；不展示单位材料或任务 | 不凭平台会话进入单位业务；兼有 Membership 的人另行单位登录 |
| API token / agent / worker | 无 Web 登录入口；CLI/API 仅能执行既有范围与有效成员权限的交集 | 永远不能确认、驳回、补材料决定、分类、处置、重开或导出；不能模拟人工勾选 |

“待我审阅”按当前角色和 `review_domain` 计算，不虚构任务负责人、任务成员 ACL 或会签。
未知职责显示“待单位管理员分类”，不能按表名、★ 标记或模型建议自动授予确认权。

## 任务、解析与抽取

### 创建与恢复

创建表单复用 `TaskCreate`：名称必填，编号、带时区的截止时间与预算可选；预算是记录值，
不宣传为已经执行的任务费用上限。资源组合创建和成员管理不在该表单中。
顺序调用创建、上传、解析三个既有 API，分别保留返回的 task/document/job ID；上传不等于
解析完成，创建成功后上传失败不再次创建任务。重选同一真实文件使用上传的 `duplicate`
回执；解析失败显式重试原文档，不生成“成功”占位状态。

上传使用 `FormData` 的 `file` 字段，不复用 JSON 编码，不手写 multipart boundary。
显示文件类型/大小错误与服务端限制；没有上传进度事件时显示进行中，不伪造百分比。
解析按 queued/running/succeeded/failed/cancelled 展示；未知总量不显示推算进度条。

同页刷新可恢复当前标签页保存的对象 ID，随后重新授权读取。任务中的完整文档发现、
未完成解析作业跨设备恢复存在 G1 缺口；不能用抽取历史伪造未抽取文档列表，不能宣称
仅靠浏览器缓存已经提供完整任务恢复。

### 推理档位与抽取历史

进入抽取表单先调用 `POST /documents/{D}/extract`，请求 `JobAction(dry_run=True)`。
从 `data.reasoning_levels` 的 `name/label/default` 构建选择器，提交官方 `name`；不能硬编码
“快速/均衡/深度”映射，也不调用平台运营模型目录来为单位补数据。空档位列表显示
“此模型未提供可选推理档位”，不发送自造值；省略 reasoning 代表服务端默认。
未知档位提示 `unsupported_reasoning`，刷新 dry-run 后重新选择，不静默降档。

dry-run 返回的 `parsed` 不是完成承诺，执行仍以文档 parsed 状态和后端校验为准。
未知估价显示“费用暂不可估”，保留 warnings；未返回的模型名、单价或时长显示未知。
按钮清楚区分“预检”和“开始抽取（可能产生费用）”，打开页面或改变筛选不得触发付费调用。

历史展示 job、文档、官方档位、模型、状态、开始/结束时间、保存/拒绝数量、tokens、
错误及 latest 标记；缺值显示未知。历史选择必须显式固定成功的抽取 job，写进页面上下文。
失败/运行中作业可查看状态与错误，但不能作为响应卡的范围。重新抽取后旧卡片保留在旧 job，
不自动迁移、合并或将页面跳到最新结果；默认 `req list` 的多文档结果不能用作审阅集合。

要求行显示类别、★、要求描述、完整 `source.quote` 与位置入口。描述、模型原样
`model_quote`、可核验引文分开呈现；只有 `source.quote` 是招标原文，不对其做清洗或摘要。
PDF 标签用文件名与第 N 页；Word 用文件名与 `Location.label`、章节路径，`page=null`，
不猜测排版页码。详情对照受权 chunks/原件，模型原样引文默认折叠并注明仅供追溯。
逐条被拒条目、解析警示及 `gap_fill.remaining` 单独展示，不能算成已保存要求。

## 单卡审阅、材料与批量处置

### 单卡操作

桌面布局为要求列表与详情区，详情依次呈现招标原文、材料、响应正文及人工操作；窄屏
改为顺序区块。`CardSlot` 的 `missing_card` 仍占一个要求位置，不能从进度中消失。
状态、eligibility、处置和模型建议分开显示，沿用[状态与职责](../notes/response-cards.md#how-it-works)。

- 编辑提交 `CardCreate/CardUpdate`，保存和送审是两个明确动作。已待审卡须先撤回，已确认卡
  须由对应职责的人重开；原因留在历史中。普通编辑、起草不覆盖待审/已确认/comply_only。
- `evidence` 模式从任务固定选择中添加逐字字段摘录或已有证书页来源；`commitment` 模式
  Evidence 必须为空，显示“承诺”。切换种类导致移除候选材料时先展示影响，不悄悄丢弃编辑。
  两种响应都要写正文、偏离和说明；负偏离保持可见，不能自动改为无偏离。
- 确认前逐项核对实际链接的 Evidence、页面及警示，不默认勾选、不提供全选确认。发送
  `reviewed_evidence_ids` 精确覆盖本修订证据，警示逐项确认并记录处理理由；commitment
  的审阅证据集合为空。`proof_material_required` 不能由“我方承诺”自动消除。
- 驳回、需补材料、撤回、重开填写非空原因。`review_hint=needs_material` 只表示模型/服务
  提示，不能显示成已有人作出补材料决定。确认人和时间只读，由认证上下文产生。
- 每次写入携带 `expected_revision`。409 时展示服务器新修订与本地未保存编辑的差异，
  由人重新处理；不自动合并、重发确认或沿用旧勾选。其他成员更改导致 `stale_material`、
  `invalid_citation`、`needs_reconfirmation` 时清除本次审阅勾选并要求重新核对。

### 材料选择与预览

材料选择器只消费同任务当前固定的 products/features/certificates/profiles、
certificate-files 和 evidence-sources。资源库最新版本不能替代任务固定版本；
列表须显示选择 ID、修订、材料性质、是否仍为有效选择及证书相关日期警示。
Evidence 允许的 `kind/field_path` 直接遵循
[RESOURCE_FIELD_PATHS](../../server/app/schemas/response_card_contracts.py)，不允许粘贴任意路径或 URL 造证据。

材料库已存在的资源可通过既有选择接口固定到任务，显式替换须展示依赖失效影响。
本页不建设整套资源 CRUD；没有真实材料时保留缺口并指向 CLI 资源维护流程。
产品 URL 是声明，功能 implemented 是声明，单位资料的业绩文字不是合同附件。
证书 PDF 整页来源始终是 `unconfirmed_source`；确认 Evidence 不能改写来源档案状态。
无可提取页文本的扫描件可以供人查看，但现有精确摘录链不能据此生成可确认的页 Evidence，
也不静默启用 OCR、传图片给模型或把扫描件伪造为文本证据。

预览先取受权短期链接，再携带单位 Bearer 和 `X-Org-Id` 获取字节；单有签名不够。
不得把签名 URL 直接塞入无法携带身份的 `<img>`/iframe，更不能把会话令牌放 URL。
首版 PNG 用已鉴别类型的字节生成仅在内存中的 `data:image/png` 展示，兼容现有 CSP；
按当前打开的材料加载，关闭、登出或切换单位即释放。原始 PDF 采用受权下载，由人对照
页码；招标内容在控制台内先使用 chunks/结构位置，不承诺缺少接口的 PDF 页渲染或 Word 排版预览。
若后续改为 blob 内嵌查看器，须单独评估 CSP 的最小调整，不放宽脚本、跨域或 frame 限制。

### 批量“仅需遵守”

批量入口仅作 disposition 决定，主要用途是 `comply_only`，不能批量确认响应或证据。
页面先显示选中要求、职责、原文、预期修订及逐项理由；没有卡片时 revision 为 null。
只能提交同任务、同抽取 job 且职责可处理的集合；单次不超过 `DispositionBatch` 上限。
待审/已确认项不得直接进入批次，不能跳过撤回/重开关口。

推荐沿用已实现的整批原子事务：任一项越权、过期或版本冲突则整批失败，保留选择并展示
需要重新读取的范围。浏览器不把一个批次拆成后台自动循环写入。将 comply_only 改回
respond 也是人工决定；模型建议不会自动选中任何项，有效处置与确认数分开统计。

## 模型起草、组表与费用

模型起草必须在二期端点交付后接入；尚无端点的部署隐藏运行按钮并说明能力未开放，
不以生产 mock 返回候选卡。入口选择固定 job、全部或明确的 requirement ID 集合及官方档位。
档位复用抽取 dry-run 的目录输出，生成预检再确认实际 reasoning；失配时重新读取，不替换选择。

按已批准的 `CardGeneratePreview` 展示目标与跳过数量、模型/目录修订、档位、输入引用标识、
遮挡开关及命中数量、预估 tokens/服务商 USD 成本、平台扣款及币种、估算依据和时长。
外发正文及遮挡前敏感值不出现在预览摘要。未知值为 null，不写 0 或“免费”；dry-run
顶层 cost 为本次预检实际零费用，不能拿它冒充运行估价。遮挡默认开启，读取任务列表中的
设置修订，仅人类 admin 可改；关闭时明确提示将按清单发送未遮挡文本。

开始前由人确认本次付费操作与外发摘要。该动作只授权运行，**不确认任何响应或材料**。
任一选项/材料/设置改变后重做预检；预检不保留预算、不锁价格，也不是最终授权凭据。
`CardGenerateRequest` 没有输入哈希/价格修订/金额上限的回传条件，因此存在 G3 的预览与
提交绑定缺口；未补契约前不得声称“费用不会超过预览”或把 `input_hash` 当作已提交的条件。
外发范围、遮挡四类信息、引用双重逐字核验及 worker 关口复用
[模型起草](../notes/model-drafting-redaction.md)，不在前端另写遮挡或直接连接厂商。

生成后刷新受影响卡片，突出新增修订、拒绝引用、受保护跳过及待补材料项；模型结果只到
draft。不得自动提交、自动采用 disposition 建议或替人勾选审阅项。

组表独立调用 `DraftRequest(dry_run=True)` 后提交，展示表行数、comply_only、缺口及负偏离。
实际组表不调用模型/OCR，零模型费用不能抵消或隐藏此前起草费用。显示实质性、商务、技术
三表，另列须遵守清单与缺口；每条要求恰好落入一个集合。有效负偏离不是缺口，仍始终醒目。
缺口只展示原文、位置、原因与回审入口，不把未确认的候选正文伪装成响应行。

初稿显示 `status=draft`、completion、validity、输入 job 与受影响要求。旧稿 stale 时保留
原快照并提示重新审阅/组表，不能用旧 complete 徽标表示可交付；本范围没有导出按钮。

### 作业、取消与成本记录

解析、抽取、起草、组表使用既有 jobs；前端轮询 `GET /jobs/{id}`，建议前台 2 秒起，
连续失败退避至 30 秒，遵守 `Retry-After`。后台标签页暂停高频轮询，恢复时重新查询，
终态停止。无 SSE 路由就不假设推送。显示实际 attempts、reasoning、错误和返回结果，
不推算未提供的执行百分比。

取消只调用既有 cancel，HTTP 受理不表示已发厂商调用无费用；已发生用量继续结算。
前端断网/等待超时不自动取消作业，不自动重试付费写入。解析/抽取/组表显式 retry 复用
既有参数；起草请求没有 retry 字段，不自行添加。作业/attempt 的隔离与缓存遵循
[background-jobs.md](../notes/background-jobs.md)及二期起草契约。

费用使用作业结果与 `UsageRecord` 的既有记账口径，区分 `Cost.usd` 和平台 charge；不能
把查询请求顶层的零 cost 当作作业总费用。只展示响应实际提供的金额，未提供为未知。
预付余额、逐次准入、预占、跨 attempt 上限与幂等扣款见
[prepaid-billing.md](../notes/prepaid-billing.md)。余额不足停止新调用并提示联系 admin，
非 admin 不为显示余额而调用运营后台。任务预算强制执行、月度预算、退款及单位用量明细页
不在本范围，缺少的数据接口列入 G5。

## 数据模型与 Pydantic 边界

### 持久化与隔离

**建议本控制台不新增业务表或迁移**：页面是既有实体的投影，不复制一套 Web 卡片或审批状态。

| 数据 | 权威模型与用途 |
| --- | --- |
| User / Membership、Task / Document / Chunk / Requirement / Job | [entities.py](../../server/app/models/entities.py)：身份、任务、原文及抽取范围 |
| 固定任务资源、证书原件、EvidenceSource | 各资源 schema 与[来源档案](../notes/unconfirmed-evidence-sources.md)：候选材料及真实预览 |
| response_cards、response_card_revisions、evidence、card_evidence_links | [response_cards.py](../../server/app/models/response_cards.py)：卡片当前指针、不可变修订、人工确认材料 |
| draft_runs、response_items | 同一模型文件：初稿快照、响应行/须遵守/缺口 |
| card_generation_runs | 响应卡模型预留、二期起草契约归属；前端不负责建表或填入模型结果 |
| audit_logs、usage_records、vendor_calls、余额账本 | 复用业务审计和计费机制，不另建前端操作/费用真相表 |

列表筛选与选择只存在页面内存；可恢复的单位/任务/job/requirement ID、页码和非文本筛选
存当前标签页，按单位命名并在切换时清除。搜索正文、响应草稿、材料字节、签名链接不进入
URL、localStorage、IndexedDB 或持久缓存；未保存内容导航离开时提示处理，不暗中落浏览器盘。

若后续选择服务器保存审阅位置或列表偏好，须另行批准表与接口，不能以 UI 实现为由隐式
加入。**每张新业务表**均须 `org_id NOT NULL`、`UNIQUE(org_id,id)`、ENABLE/FORCE RLS，
同次交付双单位隔离验收；业务父引用使用含 org_id 的复合外键，必要时同时约束 task/job/card，
人员引用 Membership 的单位复合键。禁止通用无约束 resource_id、仅靠前端过滤或新增
BYPASSRLS 例外。对象键保持 `org/{org_id}/`，worker 恢复单位上下文，缺上下文拒绝。

### 共用输入与视图

所有输入复用 Pydantic `Contract(extra=forbid)`；不增加可写的 org_id、actor、确认人、
时间、权限或服务器 state。模型定义以以下文件为单一来源：

| 操作 | Pydantic 契约 |
| --- | --- |
| 登录、创建、解析/抽取、引文、Result | [contracts.py](../../server/app/schemas/contracts.py)：Login、TaskCreate、JobAction、Source/Location、Cost/Result |
| 卡片编辑/动作/分类/批量 | [response_card_contracts.py](../../server/app/schemas/response_card_contracts.py)：CardCreate、CardUpdate、CardAction、CardClassify、DispositionBatch |
| 卡片列表/详情、材料、初稿 | 同文件：CardSlot、CardView、EvidenceInput/EvidenceView、DraftRequest/Preview/View/Summary；沿用 needs_reconfirmation |
| 起草与遮挡 | 同文件及二期契约：CardGenerateRequest/Preview/Result、TaskRedactionSet/View；schema 存在不代表起草路由已注册 |
| 任务选材 | [resource_contracts.py](../../server/app/schemas/resource_contracts.py) 的 TaskProductSelection、[feature_contracts.py](../../server/app/schemas/feature_contracts.py) 的 TaskFeatureSelection、[certificate_contracts.py](../../server/app/schemas/certificate_contracts.py) 的 TaskCertificateSelection、[profile_contracts.py](../../server/app/schemas/profile_contracts.py) 的 TaskOrgProfileSelection |
| 证书页来源 | [evidence_source_contracts.py](../../server/app/schemas/evidence_source_contracts.py)：EvidenceSourceCreate、EvidenceSourceArchive |

以下是拟用于前端适配边界的 Pydantic 形状；前两项准确描述既有抽取 dry-run 的 data，
后两项只定义页面状态，不注册为 API 参数，不要求后端接受筛选/分页字段：

```python
from typing import Literal
from uuid import UUID
from pydantic import Field
from app.schemas.contracts import Category, Contract
from app.schemas.response_card_contracts import CardState, Disposition, ReviewDomain

class ReasoningChoice(Contract):
    name: str
    label: str | None
    default: bool

class ExtractionPreviewData(Contract):
    dry_run: Literal[True]
    document_id: UUID
    parsed: bool
    estimated_cost_usd: float | None
    reasoning: str | None
    reasoning_levels: list[ReasoningChoice]

class ReviewContext(Contract):
    task_id: UUID
    extraction_job_id: UUID
    requirement_id: UUID | None = None

class ReviewListState(Contract):
    context: ReviewContext
    category: Category | None = None
    starred: bool | None = None
    state: CardState | Literal["missing_card"] | None = None
    review_domain: ReviewDomain | None = None
    disposition: Disposition | None = None
    only_mine: bool = False
    only_gaps: bool = False
    query: str = Field(default="", max_length=200)
    page: int = Field(default=1, ge=1)
    page_size: Literal[25, 50, 100] = 50
    selected_requirement_ids: list[UUID] = Field(default_factory=list, max_length=1000)
```

适配验证还须检查：所选 job/task 与所有 CardSlot/Requirement 一致；选中 ID 不重复且来自
已加载集合；按页切换不生成业务写入；缺卡片不是空白 CardView；确认/批量写入使用保存时
的 revision，不拿列表下标或页码代替。页面状态不是权限声明，服务端重新校验全部关系。
Result 不另建 Web 专用顶层包装；字段变更进入原 schema 并遵循兼容策略。

## CLI、API 与接口缺口

下表的 T/D/J/C/S/R 分别为 task/document/extraction-job/card/source/certificate-revision ID；
job status/cancel 使用提交回执的作业 ID，不能拿抽取 J 查询起草或组表。
“已有”以相应 API 与 CLI 注册为依据；“已约定”须等待二期端点交付。所有 CLI 均支持
`--json`、缺参立即失败，不增设浏览器专用的隐式业务命令。

### 任务与抽取接口

| CLI 或现有辅助读取 | API | 状态及用途 |
| --- | --- | --- |
| `bid auth orgs`、`bid login` | `POST /auth/orgs`、`POST /auth/login` | 已有；单位查找和登录 |
| `bid org use ORG_ID` | `GET /org/current` | 已有；请求带选定单位上下文，返回 role |
| `bid task create`、`bid task list` | `POST /tasks`、`GET /tasks` | 已有；列表不是完整任务详情 |
| `bid tender upload --task T --file FILE` | `POST /tasks/{T}/documents` | 已有；multipart，返回文档及重复标记 |
| 无独立注册 CLI 命令，API 辅助读取 | `GET /documents/{D}`、`GET /documents/{D}/chunks` | 已有；状态与原文块 |
| 无独立注册 CLI 命令，API 原件读取 | `GET /documents/{D}/download-link`、`GET /documents/{D}/download?signature=...` | 已有；后一项成功返回二进制，仍需身份 |
| `bid tender parse --document D [--dry-run] [--retry] [--wait]` | `POST /documents/{D}/parse` | 已有；JobAction，无 reasoning |
| `bid req extract --document D [--reasoning LEVEL] [--dry-run] [--retry] [--wait]` | `POST /documents/{D}/extract` | 已有；dry-run 提供官方档位 |
| `bid req history --task T [--document D]` | `GET /tasks/{T}/extractions?document={D}` | 已有；document 过滤可省略 |
| `bid req list --task T --job J` | `GET /tasks/{T}/requirements?job={J}` | 已有；审阅页必须显式传 job |
| `bid job status ID`、`bid job wait ID` | `GET /jobs/{id}` | 已有；wait 是 CLI 轮询，没有 wait 路由 |
| `bid job cancel ID` | `POST /jobs/{id}/cancel` | 已有；权限和终态限制 |

### 材料、响应与初稿接口

| CLI | API | 状态及用途 |
| --- | --- | --- |
| `bid task resource/feature/certificate/profile list --task T` | `GET /tasks/{T}/products`、`/features`、`/certificates`、`/profiles` | 已有；可用 history 读取历史选择 |
| 对应 `bid resource product/feature/certificate/profile list` | `GET /resources/products`、`/features`、`/certificates`、`/profiles` | 已有；选材时只读库，具体查询参数沿用各服务 |
| `bid task resource/feature/certificate/profile add --task T --input FILE` | `POST /tasks/{T}/products`、`/features`、`/certificates`、`/profiles` | 已有；选择或显式替换，按各角色已有范围 |
| `bid task certificate file list --task T` | `GET /tasks/{T}/certificate-files` | 已有；固定证书原件 |
| `bid resource certificate file download` | `GET /resources/certificates/revisions/{R}/file/download-link`、`GET /resources/certificates/revisions/{R}/file/download?signature=...` | 已有；受权读取原件，非标书 export |
| `bid evidence source add/list --task T` | `POST /tasks/{T}/evidence-sources`、`GET /tasks/{T}/evidence-sources` | 已有；指定固定证书选择与真实页码 |
| `bid evidence source download --id S` | `GET /evidence-sources/{S}/preview/download-link`、`GET /evidence-sources/{S}/preview/download?signature=...` | 已有；归档 PNG 预览 |
| `bid card list --task T --job J`、`bid card show --id C [--history]` | `GET /tasks/{T}/cards?job={J}`、`GET /cards/{C}?history=...` | 已有；全要求槽位/不可变历史 |
| `bid card create/update ... --input FILE` | `POST /tasks/{T}/cards`、`PUT /cards/{C}` | 已有；CardCreate/CardUpdate |
| `bid card classify --id C --input FILE` | `POST /cards/{C}/classification` | 已有；人类 admin 分类 |
| `bid card disposition --task T --input FILE` | `POST /tasks/{T}/cards/dispositions` | 已有；DispositionBatch 原子事务 |
| `bid card submit/withdraw/confirm/reject/needs-material/reopen --id C --expected-revision N ...` | `POST /cards/{C}/actions` | 已有；CardAction，动作与审阅字段依原契约 |
| `bid task redaction set --task T --input FILE` | `PUT /tasks/{T}/model-redaction` | 已有设置入口；外发执行依赖二期 |
| `bid card generate --task T --job J [--requirement ID ...] [--reasoning LEVEL] [--dry-run] [--wait]` | `POST /tasks/{T}/cards/generations` | **已约定，待二期接入**；本 checkout 未注册 |
| `bid draft --task T --job J [--dry-run] [--retry] [--wait]` | `POST /tasks/{T}/drafts` | 已有；只组表 |
| `bid draft list --task T --job J`、`bid draft show --id ID` | `GET /tasks/{T}/drafts?job={J}`、`GET /drafts/{id}` | 已有；摘要及重算有效性的快照 |
| `bid billing balance/redeem` | `GET /billing`、`POST /billing/redeem` | 已有；沿用 admin 页面 |
| `bid schema --json` | 无对应业务 API | 已有；核对命令及输入模型，不杜撰 schema 路由 |

表内同族路径缩写均以上方同一前缀展开。没有独立 Evidence 确认接口，材料随卡片动作
在事务中确认；不新增独立自动确认按钮。引用修复沿用已存在的管理员 CLI 流程，
本页只呈现 invalid_citation/needs_reconfirmation，不自动修复招标原文。

### 必须显式保留的缺口

| ID | 缺口与影响 | 本契约的处理 |
| --- | --- | --- |
| G1 | 没有任务完整详情/任务文档列表/解析作业列表；GET tasks 只返回简要任务及遮挡设置，抽取历史找不到尚未抽取文件 | 不编造路径或返回字段；首段交付可限定创建链与已知 ID 恢复，完整任务控制台交付前须另批文档/作业发现契约 |
| G2 | requirements/cards 及多数列表无服务端筛选、分页、总数/游标契约；卡片列表返回完整 CardView | 首版使用真实全集配前端分页，必须通过大列表门槛；未通过则先批准在既有读取接口上的分页/摘要扩展，不擅发 page/q 参数或静默截断 |
| G3 | 起草预览有 input_hash 与价格信息，但请求没有预览绑定或扣款上限条件；抽取预检也不固定模型/价格快照 | 预览只作估算；若要求强一致确认或金额封顶，先补二期/抽取契约，不能以页面二次确认假装解决竞态 |
| G4 | 无招标 PDF 页图、Word 排版预览或任意附件读取契约；扫描证书缺少可核验页文本链 | 原文块与受权原件下载、既有证书 PNG 预览；新材料/渲染/OCR 能力独立立约 |
| G5 | 无单位业务审计查询、完整单位用量明细、SSE、任务预算强制执行或会话注销/续期接口 | 显示现有修订/作业结果，轮询；本地退出不称为服务端吊销；不借用 platform API 或发明端点 |

另有前端适配工作：`api.js` 把 `!payload.ok` 全部抛错且丢失 partial payload，只支持 JSON
body；这些须在未来实施时支持业务部分成功、multipart、受权二进制与 Retry-After，
属于已知客户端差距，不伪称缺少响应卡 API。未知 500/non-JSON 问题仍按
[路线图已知缺陷](roadmap.md#已知代码缺陷)处理，不把未知响应降级成空列表。

### Result 与失败呈现

所有 JSON 结果严格保留七个顶层键：`ok`、`command`、`data`、`items`、`warnings`、`cost`、
`duration_ms`；错误在 `data.error`，列表在 items，详情/回执在 data。二进制下载成功响应
按已列接口的媒体类型消费，不强行解析 Result；错误仍按错误响应处理。

| CLI 退出码 | Web 中的对应行为 |
| --- | --- |
| 0 | 查询/动作/预检成功或作业已受理；只有终态结果才能显示执行完成 |
| 2 | 输入、档位、状态迁移、确认缺项或 revision 冲突；定位错误，重新读取，不自动写入重试 |
| 3 | 可重试网络/队列/限流/等待超时、固定输入变化等；保留对象 ID，遵守 Retry-After 或提示重新预检 |
| 4 | 身份/权限/无权资源、不可重试厂商失败、材料完整性或余额不足；明确停止，不降级成功 |
| 5 | 组表缺口/起草部分完成，或 CLI 组合创建后续失败；保留有效产物与逐项原因，不抛弃整个结果 |

HTTP 状态与退出码分别处理，不向 Result 顶层添加 exit_code。HTTP 2xx 且 ok=false 时，
只对契约明确的 `completion=partial`（详情或 job 的 result）渲染部分成果；其他未知形状
报契约错误。基础 job 可 succeeded 而业务 partial；解析/抽取 job 的 `data.error` 与终态
也须检查，不能仅看顶层 ok。抽取有拒绝条目不自动套用起草 partial 规则。
401 清除对应会话并回登录；已识别的单位停用停止单位请求；403 不假报会话过期。
资源不存在与跨单位对象统一展示“不可访问”，后端 404 不泄露其存在。

## 会话、CSRF 与审计

沿用 `bid.org.session` 和 `bid.platform.session` 的 sessionStorage 分离；所有单位业务请求
显式携带当前人类会话 Bearer 与 `X-Org-Id`，只向同源白名单路径发送，不接收用户提供的
服务器 URL。认证不依赖浏览器自动附带的 cookie，因此不能虚构现有 CSRF cookie、CSRF
端点或 `X-CSRF-Token`。保持服务端要求 Authorization/单位上下文，拒绝外站表单和无
身份请求，不开放带凭据跨域；请求可显式 `credentials: "omit"` 防止未来误依赖 cookie。
若另行采用 cookie 会话，须先单独批准 CSRF/SameSite/Origin 和续期契约。

登录重用现有查找/登录和限速语义；只在登录表单内持有密码，成功后清除。不存 token 到
URL、日志、截图或错误。退出清理单位会话、业务内存、预览及请求，保持平台会话独立；
这不代表服务端已吊销旧 session。切换单位须重新验证 Membership/role，废弃旧请求响应、
选材/勾选/编辑与旧单位缓存，不能只换顶部名称。新角色权限在服务端即时生效。

保持 [CONSOLE_HEADERS](../../server/app/api/main.py) 的同源 CSP、no-referrer、nosniff。
招标原文、模型文字及文件名当纯文本渲染，不使用 `v-html` 执行来源内容，不引入第三方
分析脚本读取材料。XSS 防护不能由“已有 CSRF”代替。

审计沿用后端 audit_logs：卡片编辑/动作、分类、逐项处置、材料选择替换、遮挡设置、
起草/组表提交与结果由对应服务记录；前端不能补写成功审计，也不能把点击当作执行完成。
原子批次共用关联 ID，每项有修订和审计；失败/冲突无成功审计。记录只含身份、对象/
修订/job ID、状态/处置变化、原因代码、时间和哈希；处理理由留在受权历史，条款、响应、
模型收发、凭据及四类敏感值不进入审计或普通日志。全局单位审计页依赖 G5，本页先提供
卡片历史和作业结果入口；缺失的操作审计不能由前端遥测充数。

## 大列表与无障碍

以 1,000 条以上要求、存在卡片与多份证据的集合验收，不仅用空卡片测流畅度。
首版取得指定 job 的 requirements/cards 全集后按 requirement ID 联合，沿服务端原文
顺序分页，默认 50、可选 25/100；摘要总数由完整集合计算并标明所选 job。加载未结束
不能显示“0 项缺口”或允许全量批量操作；不得用虚拟滚动掩盖数据截断。

筛选包括类别、★、状态/缺卡片、职责、处置、失效/缺口、待我审阅，以及要求/引文文本
搜索。展示“匹配数 / 全集数”，筛选不会改变组表范围；完整初稿的负偏离计数与警示保持
可见，不提供隐藏负偏离的展示开关。页码越界回到有效页，切换 job 清空筛选关联的选择。

选择明确区分“本页”与“全部匹配”，后一项先显示固定 ID 数量和职责限制再由人选择；
超过 1,000 项要求缩小范围。改变筛选/切换范围清除批次选择并提示，不能悄悄把不可见项
带进人工决定。单卡审阅完成后提供“下一条待我审阅”，按原文顺序定位，不自动确认下一张。
打开详情重新读取当前修订，成功写入更新局部列表；不每次按键重拉所有卡片或预取所有图片。

优先使用语义 table、caption、列头、原生按钮/复选框和明确 label；状态同时有文字，不能
只靠红绿或 ★ 图标。提供跳到主内容、明显焦点、区域标题与详情返回焦点。分页/保存/作业
状态用节制的 `aria-live` 通知，不在轮询时重置焦点。错误关联字段，冲突/决定对话框锁定
焦点并在关闭后归还。原文与证据图提供可读位置和替代说明，不把机器 OCR 当完整图文替代。

键盘可完成筛选、分页、打开卡片、编辑、逐项核对、选择动作和下一条导航；输入框中不截获
文字快捷键。可选快捷键须可发现/关闭，确认/驳回/批量提交没有单键即执行路径。200% 缩放
及窄屏不遮住原文或动作，横向表格滚动区域可用键盘进入，图片支持放大与返回。

建议验收基线为可重复的 1,200 条集合：一次只挂载当页列表和一个详情，不预加载图像；
已加载数据上的筛选、翻页、下一条操作到可交互的 p95 不超过 300ms。真实 API 首次加载
到可操作目标不超过 5 秒，测试工件注明机器/浏览器/材料大小和请求耗时。若 G2 的全量
API 无法达到门槛，暂停大规模交付并补摘要/分页契约，不放宽数据完整性。

## 分期交付建议

以下是批准后的纵向切片，每期完成对应 Playwright 链路；不是本草案已经实施的记录。

| 期次 | 可交付结果 | 依赖与退出条件 |
| --- | --- | --- |
| U1 任务与抽取 | 单位导航、角色入口、创建/上传/解析、官方档位预检、抽取历史、要求与原文 | 只用已有路由；完成创建链和已知 ID 恢复。G1 未补前须明确是受限范围，不能宣称任意存量任务可完整恢复 |
| U2 人工审阅与初稿 | 固定任务选材、证据/承诺编辑、按职责决定、原子 comply_only、三表/须遵守/缺口、并发与失效 | 依赖一期响应卡 API；通过 1,200 条和双单位/角色端到端检查。完整单位控制台验收前关闭 G1，G2 不达标则先补契约 |
| U3 模型起草集成 | 起草预览、付费运行、遮挡提示、生成结果与部分成功 | 二期 Provider/作业/外发遮挡/计费已交付并核对 CLI/API；决定 G3 的估算语义或先批准绑定扩展，不重复实现二期 |

建议先 U1 → U2，再接 U3；人工链不被另一 checkout 的二期进度阻塞。G4/G5 所列的
附加能力继续在各自契约处理，不因控制台页面出现就默认授权后端扩项。

## 端到端验收

以下为未来 Playwright 验收条件，本草案不新增代码、测试或运行真实模型。
测试使用隔离 PostgreSQL、API、真实 worker、存储与构建后的 Vue 应用；两单位各准备
admin/bidder/technical/viewer，另有平台身份和受限 API token。CI 只在 Provider 边界用
明确标识的合成材料/fake，不能 mock 掉待验证 API、数据库确认关口或作业落盘。
获授权真实样本单独验收，不上传到测试服务或复制到公开证据。

1. **入口与身份**：浏览器单位登录后进入任务页，admin 可另进计费，technical 无创建入口，
   viewer 只读。平台会话访问单位 API 被拒；篡改浏览器角色、跨单位深链接/对象 ID、失效
   Membership 均不能读取或操作数据。分别验证 401、403、404、org_inactive 与限速倒计时。
2. **创建链和恢复**：上传真实可解析 PDF/DOCX，浏览器提交后由 worker 解析，刷新继续读
   同一对象；上传/入队失败只重试未完成阶段，不重复创建任务。同内容重复上传显示 duplicate。
   G1 补齐后另用全新浏览器会话恢复仅上传、待解析和解析失败的存量任务，不能依赖本地缓存。
3. **档位与历史**：从 dry-run 响应列出两个不同的已配置官方档位和默认项；确认无作业/
   用量写入。运行两次不同档位保留两个独立集合，重复相同输入命中缓存；空目录、未知档位、
   模型不可用、拒绝引用均真实显示，重抽不改旧卡片。PDF 页码/Word label 与原文逐字一致。
4. **真实材料边界**：从任务固定选择创建字段 Evidence 和有文本证书页 Evidence，受权加载
   原页核对；过期签名、换单位签名/ID、错页、错字段、非活动选择被拒。全扫描页只可预览、
   无文本时不能假造摘录；功能规划不能显示已实现，URL/业绩声明不能显示成取证网页/合同。
5. **人工关口**：bidder 处理商务、technical 处理技术，admin 只能分类/设置；evidence 缺材料
   无法确认，commitment 无 Evidence 可按职责确认，负偏离和证明材料警示保留。逐项证据/
   警示未核对不能送出确认。通过浏览器关联的 API 请求验证 token/worker 身份不能绕过人工
   决策，token 申请 evidence:confirm/export 被拒；未确认材料不出现在组表行中。
6. **批量与并发**：两浏览器在同一 revision 编辑/确认仅一方成功；失败方保留编辑但清空
   旧审阅勾选。数百条 comply_only 一次提交，混入一条越权或冲突则全部不写；修订、审计
   由持久化结果核验。成功批次不生成 confirmed 响应，改变筛选后没有隐形选中条款被提交。
7. **组表与失效**：通过 UI 预检并组表，校验 row/comply_only/gap 不重不漏覆盖所选 job，
   三表位置/原文/确认文字一致。HTTP 2xx、ok=false 的 partial 正常显示全部有效结果和缺口；
   全缺口、全须遵守、有效负偏离分别符合退出码语义。重开、材料替换或引用修复后旧稿 stale，
   不能继续显示为当前有效；历史修订和旧稿不被修改。
8. **二期接入**：端点未交付时无付费生成请求。交付后由 UI 预检/确认运行，核验 Provider
   实际载荷只含所选单位的固定文本，遮挡默认开启，不发整份 PDF/图片或未选材料；用量、
   平台扣款、重试/缓存/取消结算一致。生成只写 draft；材料/版本变化产生跳过或失败，不能
   覆盖人工确认。未知估价显示未知，价格/输入预览竞态按 G3 最终选择验收，不测试虚假的封顶。
9. **大列表与无障碍**：1,200 条混合状态、长引文、多材料集合上完成筛选、跨页选择、键盘
   连续审阅、角色切换与回到原位置；统计完整、符合性能门槛、无全量图像加载。用可访问名称
   和角色定位元素，检查焦点、字段错误、状态播报、200% 缩放和窄屏；无浏览器控制台错误。
10. **隔离、CSRF 与审计证据**：对本范围每个读写/预览/作业端点用 A 身份尝试 B ID，并检查
    缺上下文拒绝；重复同单位跨 task/job/source 混接检查。外站表单/无 Bearer 请求不能改变
    业务，切换单位时延迟到达的旧响应不渲染。以受限运行 DB 角色核验现有消费链，以及将来
    每张新表的 FORCE RLS、复合外键与 A/B 隔离；原子失败不留成功审计，日志无正文或凭据。
11. **CLI 对照与可重复工件**：在相同隔离数据上对照真实 CLI 的 --json 与 API 的 Result
    七键及 0/2/3/4/5，作业受理/完成/部分成功不混淆。运行产物写入新建的
    `artifacts/org-console/<run-id>/` 或受控临时目录，包含脱敏 result.json、断言、所用
    合成数据标识/哈希、性能记录、关键页面截图及重跑参数。只记录命令形状与环境变量名，
    不保存密码、Authorization、Cookie、签名 URL 或原始网络 trace；不在 docs/ 写证据。

## 明确不在范围内

- 整份 663 页标书的编排、投标函/授权书填制、164 页技术方案生成、Word/PDF 导出与模板适配。
- 政府电子投标平台接入、逐页盖章、电子签名、加密、上传投标文件或报价策略。
- 编造报告/证书/业绩/社保材料、制造厂家页面或替代真实截图；自动确认、批量确认或令牌导出。
- 任意合同/报告/团队附件的新材料库、网页取证、Rust 标注、ui mock、云 OCR 与多模态传图。
- 修改要求、人工补录、自动引用修复、多文档或多抽取结果合并、评分、全文 check、风险卡与评论。
- 全套资源管理、单位成员/会签/任务 ACL、OIDC、服务端会话吊销、自带模型/Provider 配置、
  记忆与 agent 编排、SSE、单位审计/用量新查询、任务预算执行及支付扩展。
- 在本草案中决定缺失 API 的 URL/新字段、迁移或持久化偏好；这些须补独立契约再接入。

## 待你决定

| 决定 | 具体选项 | 推荐 |
| --- | --- | --- |
| 首个可交付范围 | A：先 U1/U2 人工链，再接二期；B：等模型起草一起交付 | **A**。一期已有业务接口可先验证审阅效率，U3 以二期真实入口为接入条件 |
| G1 的文档/作业发现 | A：先允许创建链和已知 ID 的受限交付，完整控制台前补读接口；B：先补契约与接口，再开始 U1 页面 | **A**。在任务页明确恢复边界；跨设备任意存量任务恢复仍是完整交付门槛 |
| 1,000+ 要求的分页 | A：现有全集接口加前端分页，按 1,200 条门槛验收；B：先批准服务端分页、摘要及总数契约 | **A**。成本小且保持既有接口；性能不达标立即转 B，不能截断要求或调低完整性标准 |
| 起草费用与预览绑定 | A：估价仅供参考，明确付费确认，执行服从现有后端额度；B：先补输入/模型/价格绑定及用户金额上限 | **B** 作为开放 U3 付费入口的推荐条件。大材料量下应能识别预览过期并重新确认；A 只可在明确接受估价非封顶语义后采用 |
| 预览深度 | A：原文块、既有证书 PNG 与受权原件下载；B：先增加 PDF/Word 内嵌查看与定位契约 | **A**。复用已授权字节读取和 CSP，不把缺失的任意材料预览纳入首版 |
| 审阅位置保存 | A：当前标签页仅保存 ID/非文本筛选，无新表；B：服务器保存跨设备审阅位置 | **A**。减少业务状态分叉；B 须补 Pydantic/接口及 NOT NULL org_id、FORCE RLS、复合外键和双单位验收 |

