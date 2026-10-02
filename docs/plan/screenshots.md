---
kind: plan
status: "部分实施：Phase A 与导出接线；沙盒接入待实施"
---

# 契约草案：功能截图、原型与厂家证据配图

状态：**部分实施：Phase A 与导出接线；沙盒接入待实施**。已批准的范围和推荐决定保持有效；实施边界见[Phase A 实施记录](#phase-a-实施记录)。对应[路线图](roadmap.md) B06/B04，涉及 B05 的裁剪、框选与
水印边界。下文按已批准的推荐选项描述契约，各项选择见[已定决定](#已定决定)。

## 目标与边界

运行界面采集、厂家资料取证或 HTML 原型生成 → 本机脱敏与人工检查 → 单位内归档 →
多模态匹配、读字与区域建议 → 关联固定要求的响应卡片 → 对应职责的人核对图片与响应 →
已确认材料进入初稿 → 正式导出前逐项决定原型保留或替换。
目标是逐条解释“这张图支持哪项功能、支持到什么程度”，不以图片数量判断响应完整。

产品输入是一份据产品负责人描述的 663 页中标文件：包括投标函和授权表、实质性响应
一览表、商务与技术偏离表，约 305 页逐项技术响应中有约 217 张截图或图示、约 125 页
整页扫描材料，另有业绩合同与中标通知、团队社保与证书、164 页技术方案，以及在政府
电子投标客户端施加的逐页印章。这些是需求背景，未作为本草案核验过的材料，也不作为
固定页数配额。图片、整页证明材料和长篇方案必须保留不同来源与用途，不能互相冒充。

本切片接入软件截图、已有图示、固定证书 PDF 页、搜索取得的厂家网页/白皮书，以及
由 LLM 按单项功能要求生成的单页 HTML 原型。HTML 在并行编写的独立草案
`docs/plan/sandbox.md` 定义的沙盒中渲染并截图；此处只定义输入、产物关联与
证据消费，不定义或替代沙盒的执行、安全、网络和资源契约。该草案的批准及接口接入是
原型渲染的实施依赖，不以普通浏览器直接执行生成 HTML 代替。

原型可以与真实截图一样作为响应卡和初稿的 Evidence。投标和项目开发可能并行，生成的
原型可能成为实际交付前端，因此图片、初稿及导出文档均不添加水印或可见原型标签；
来源、生成模型和 HTML 哈希内部留存。多模态使用平台目录中已核验图片输入能力的 GLM、
DeepSeek，通过既有接入层为匹配要求、建议区域、读取图中文字提供辅助，不替代人工核验。
不在本文复制开发目录的具体型号、价格或凭据，也不重新把这两类模型能否读图列为待定。

入口为 API、本地与远程 CLI。图片归档、Evidence 和 draft 可先完成；Word 排版仍归
B11，但本草案批准后的交付须包括与[已批准导出契约](export.md)的图片消费、原型决定
门禁及实际文件验收，不能以尚未集成 export 宣称完整验收通过。

硬边界以 [agent.md](../../agent.md#硬性规则任何情况下都不得违反)及
[设计文档](../AI%20标书工具设计文档.md#处理流程与-cli-清单)为准。响应职责、卡片状态、
逐项确认、偏离和初稿覆盖规则沿用已批准的
[ADR 0005](../adr/0005-human-confirmed-responses.md)，其落地机制见
[response-cards.md](../notes/response-cards.md)。下述原型例外是明确的规则修订提案；逐卡
确认、专业职责、令牌禁确认/导出和单位隔离继续适用。

### 已生效的规则修订

以下修订已随本契约批准生效，并已同步到 agent.md、设计文档和 export.md；本节保留修订依据。

拟将 [agent.md 第 3 条](../../agent.md#硬性规则任何情况下都不得违反)中的
“`ui mock` 为尚未实现的功能生成的截图，必须带‘设计原型’水印”替换为：

> `ui mock` 可按功能要求生成单页 HTML，并在 docs/plan/sandbox.md 规定的沙盒中渲染
> 截图。原型图片不加水印；图片、初稿及导出文档不添加可见原型标签。原型可经既有逐卡
> 人工确认作为 Evidence 使用，系统内部必须保留 origin=prototype、生成模型和 HTML
> 哈希。正式导出前，每项引用原型的 Evidence 必须由有相应职责的人明确选择“保留：它
> 已是或将成为交付界面”或“替换为真实截图”；允许按模块批量决定并逐项留痕。未决定
> 或尚未完成替换的项阻止正式导出，但不单独阻止审阅件。此决定不能代替证据确认或
> 人工导出授权。厂家网页、报告和证书仍禁止生成，硬件证据仍只来自真实网页或文件。

该例外同时提议替代[设计文档](../AI%20标书工具设计文档.md#安全与合规)中原型必须可见
标注的对应约束，以及 ADR 0005 材料真实性约束在功能原型这一分支的适用边界；不扩大
到硬件参数、检测报告、证书或其他证明材料。功能状态仍是原有声明，不因原型确认自动
变成 implemented，保留决定也不等于系统认证已交付。

对 [export.md 的导出门禁](export.md#导出门禁与文档内容)提议增加以下拒绝清单，批准
后纳入预检、prepare、worker 取材/保存、release 及下载复核：

| 原型 Evidence 状态 | `final_section` | `review_copy` |
| --- | --- | --- |
| 证据已经按职责确认，原型保留/替换尚未决定 | 拒绝，`prototype_decision_required`，不可用 acknowledge 放行 | 可消费已确认图片；决定未完成本身不构成拒绝 |
| 有效 `keep`，明确它已是或将成为交付 UI | 满足其他导出门禁时允许 | 允许 |
| 选择 `replace`，尚未换成经确认的真实截图 | 拒绝，`prototype_replacement_pending` | 允许仍有效且已确认的旧原型；卡片已重开时按既有 stale/gap 门禁处理 |
| 决定绑定的 Evidence/卡片修订/图片/HTML 哈希或选择已变化 | 拒绝，`prototype_decision_stale`，重新决定 | 原型决定失效本身不阻止；材料或卡片失效仍按原门禁拒绝 |

未确认 Evidence、stale 初稿、无效引用、损坏文件和无权访问仍按 export.md 对两种模式
拒绝。审阅件保留既有“审阅件·不得提交”等整份文档标记和完成码 5，不加任何表示原型
性质的字样。原型门禁原因仅在内部预检/审阅工作区呈现，不写进初稿正文、附件标题、
图片、图注、替代文本或导出文档。原型决定作为独立内部清单固定，不成为 draft 缺口。

### 来源与可以支持的结论

| 来源 | 输入及固定关系 | 材料性质与限制 |
| --- | --- | --- |
| 用户上传真实软件截图 | PNG/JPEG；显式选择同任务 `task_feature_id`，固定功能修订及其产品关联 | 标记“用户提供的界面截图”；软件名称、版本、环境及拍摄时间是提供者声明，不由文件名或 EXIF 推断真实性 |
| 本机浏览器采集 | 本机 BrowserProvider 在已授权运行系统的指定页面截取可见视口或元素；同样绑定任务功能选择 | 标记“本机浏览器采集”；记录采集时间、环境、视口与工具版本，不证明系统全部功能或生产交付 |
| 已有证书页 | 同任务 `evidence_source_id`，由服务端解析证书选择、修订、原件、页码及整页 PNG | 沿用[未确认来源档案](../notes/unconfirmed-evidence-sources.md)；软著、检测报告等原有扫描页只能支持该页可见内容，不冒充运行截图 |
| 已有架构图、流程图等 | 用户上传 PNG/JPEG，绑定任务功能选择，声明图示主题与来源 | `diagram`，只能说明设计或结构；不能单凭图示证明功能已运行、性能达标或证书真实 |
| 厂家网页与白皮书 | 搜索候选或已选产品的官方 URL；固定同任务 `task_resource_id`、产品修订及精确型号；网页截图或 PDF 页截图 | `vendor_web/vendor_pdf`，保留 URL、采集时间、页标题、内容哈希、归档副本及 PDF 页码；可以支持所见参数，搜索摘要和型号元数据本身不算证据 |
| 单页 HTML 原型 | LLM 按固定功能要求生成 HTML，经 sandbox.md 渲染截图；固定功能选择、生成记录和 HTML 哈希 | 内部 `origin=prototype`；无水印、无可见原型标签，可建立 Evidence 并进入 draft/review_copy；正式导出按上节逐项决定 |

功能选择及声明状态来自[版本化功能](../notes/versioned-features.md)，不可由截图上传更新。
`implemented` 不是证明；`planned/developing` 的真实开发界面仍显示开发环境与声明状态，
不得仅据图宣称已经部署。原型允许表达可交付功能界面，审核人须核对实际响应文字与交付
责任；`keep` 不修改功能库状态。未知来源不能默认为真实截图，本机采到原型仍保留
origin=prototype。外部已有原型若没有生成模型及 HTML/哈希，只能先归档待补来源，不能
伪造溯源字段进入本草案的原型 Evidence 链。

## 取证、溯源与哈希

每个归档对象保留独立的“来源信息”和“可供审阅的图片”，不以派生图覆盖来源：

- 共同元数据：单位、任务、显式抽取 job、材料种类、提供人及身份类型、接收时间、
  固定选择与修订、来源字节 SHA-256、来源宽高、处理计划哈希、工具/profile 版本。
  服务端记录 `received_at`；来源发生时间另存，全部带时区并以 UTC 输出。
- 用户截图与图示：保留清理后的来源标签、软件/图示版本、环境、声明时间及可选的
  `captured_at`。时间未知用 null；没有原始文件时不能补造“原图哈希”。客户端给出的
  来源哈希标记 `client_declared`，服务端只保证实际收到字节的哈希。
- 浏览器：本机生成 `capture_id`、实际截取时间、BrowserProvider/浏览器版本、viewport、
  device scale factor、截取方式及原始像素尺寸；保存不含凭据的系统 origin 和经过
  人工检查的路由标签，不保存完整查询串、fragment、用户信息、DOM、HAR、页面正文。
  该回执是本机采集记录，不是第三方可信时间戳或对抗伪造的远程证明。
- 证书页：服务端从受权 Archive 解析原 PDF 哈希、固定原件 ID、页码、150 dpi、渲染
  profile、渲染时间和整页 PNG 哈希；客户端不得替换这些值。渲染时间不叫证书签发时间。
- 厂家来源：记录请求 URL、最终 URL、`captured_at`、页面/PDF 标题、媒体类型、精确
  产品选择/修订、`content_sha256`、`archive_sha256`、归档副本描述符；PDF 另有页码、
  页数、渲染 profile 和页图哈希。网页归档包含同次采集的页面内容及其静态依赖清单，
  PDF 归档固定文件字节，不能先截图再重新下载另一版本冒充同源。URL 仅保留公开来源
  定位所需部分；拒绝带凭据、临时签名或个人数据的链接，不在日志复制 URL/页面正文。
- 原型：内部保存 `origin=prototype`、生成 job、服务商、实际响应模型及目录修订、提示词/
  schema/adapter 版本、脱敏输入哈希、`html_sha256`、HTML 归档描述符、沙盒渲染回执
  标识/哈希、截图来源哈希。只认可固定 HTML 产物与该回执的关联，不把 HTML 哈希充作
  PNG 哈希。生成重试、HTML 改动或重新渲染须保留新产物关系，不能覆盖旧证据来源。
- 至少区分 `source_sha256`（处理前来源）、`upload_sha256`（实际接收的脱敏图片）、
  `image_sha256`（服务端最终规范化并标注后的 PNG）、`plan_sha256`。这些哈希不得混用；
  `source_hash_assurance=server_verified` 只用于服务端实际能读取并重验的来源字节。
  本机采集的网页归档即使上传后可验哈希，也不升级为服务端见证过线上页面的证明。

文件哈希针对实际字节；计划哈希针对验证后、含显式默认值的 UTF-8 JSON：键排序、无
多余空白、ensure_ascii=true、无尾换行，矩形列表顺序保留。元数据摘要另算，不把
文件哈希、水印中的来源哈希与自身最终哈希作循环依赖。Hash 证明字节一致性，不证明
拍摄内容真实或满足条款。

运行系统取证的浏览器在用户本机运行，即使 CLI 使用远程 API 模式也不把内网地址交给 SaaS worker。
仅连接显式指定、由本机用户授权的会话/页面；不开放公网调试端口，不自动枚举已有标签页，
不上传浏览器 profile、Cookie、密码、Authorization 或 storage state。只截图，不代为
登录、提交业务表单或修改业务数据；超时、登录页或目标不匹配明确失败，不拿错误页冒充
目标功能。真实地址经私有配置输入，日志不回显；默认禁用 trace/video/HAR 和截图落盘。

厂家搜索经 SearchProvider，只发送本次固定厂家、型号和经文本遮挡的参数关键词，不发送
整份招标或图片。结果是待核对 URL 候选；人选择后由本机公开网页/PDF 采集入口下载、
归档并截图，不借客户登录会话。仅支持公开 HTTP(S) 来源，逐跳校验地址，拒绝非网页协议、
本机/私网/元数据地址、凭据登录和未获准重定向；错误页、搜索摘要、同系列不同型号不能
替代指定产品材料。厂家归属、型号/版本和页内适用范围须在逐卡审阅时核对。

归档内容和截图共同经过本机隐私检查。公开原文无需遮挡时两个内容哈希可相同；若副本
必须清理敏感内容，则分别保留采集内容哈希与实际脱敏归档哈希、处理版本/计划，不把
脱敏副本称为字节原件，不另存未脱敏副本。被遮挡的参数不能从原网页或模型推断补回。
网页归档仅作内部核验材料，不在受权预览中执行脚本；导出只用已确认图片，不附活动网页。

## 隐私：先处理像素，再允许持久保存

建议采用本机预处理、人工放行入库；这和逐卡证据确认是两件事。报价、联系人、身份证号、
银行账号、社保明细、学生或员工个人信息、账号与会话凭据等，须在新增持久文件或上传前
处理。原图由调用者原有文件或浏览器内存提供，本切片不额外保存未脱敏副本。

1. `prepare/capture` 在受限内存及本机 Rust 子进程管道中解码、正向应用不透明实色遮挡、
   规范化方向与格式、剥离 EXIF/PNG 文本等元数据，然后才写新的脱敏 PNG 和回执。
   不用模糊、半透明、可拆图层或仅遮挡 DOM 的办法代替像素销毁；不持久记录被遮挡值、
   识别文本、DOM 或遮挡前缩略图。Rust 不支持所需隐私 profile 时直接失败。
2. 本机用户查看实际脱敏图，再以人类登录会话提交精确 `reviewed_upload_sha256` 和计划。
   API 重算收到字节的哈希；不匹配拒绝。隐私检查可对多张图逐张核对后由调用者连续提交，
   不用“确认全部证据”替代。无敏感区域也须明确检查，不把空遮挡列表当自动通过。
3. 服务端限制请求体/解码大小，上传流不经框架临时文件、代理请求体缓存或调试转储落盘。
   服务端按来源 profile 规范化编码后加密保存；原型不加水印或 footer，其他来源按下节
   规则标注。人工检查绑定上传哈希及派生的最终哈希。
   原始敏感数据不能先发给云模型查漏。自动检测只可提供建议，不能证明没有遗漏；
   客户端声明也不能让服务端证明一个上传文件已经充分脱敏。
4. 既有证书原件与 EvidenceSource 是已存档的独立材料，保持不变。授权读取时在内存中
   生成脱敏派生图；不复制一份未脱敏原件进入本切片。其旧原件读取权限仍独立有效，
   不能宣称此步骤已脱敏或删除历史 PDF。图片证据预览只提供新脱敏派生图。
5. 发现遗漏后，先撤下资产，立即阻止新预览、确认、draft 消费和未来 export；保留审计
   和历史引用。补遮挡创建新图、新 Evidence，必须重审。旧下载不能远程收回；历史敏感
   对象的保留期、彻底删除和备份清理属于另行批准的保留策略，不能靠覆盖旧图假装已清除。

新增人类专用范围 `screenshot:ingest` 表示已检查上传图片的隐私，仅授予有效
admin/bidder/technical 会话；token/agent/worker 不得获得或代行。准备本机候选、对
已脱敏资产作区域标注及编辑未确认卡片可由受限 agent 执行。隐私放行不填写
`Evidence.confirmed_by`，也不代表允许确认响应或导出。服务和数据库都检查此边界。

[模型外发遮挡](../notes/model-drafting-redaction.md#outbound-rules)处理模型
文本副本；本草案处理存储前像素，二者不能互替。关闭任务 `model_redaction_enabled`
不关闭图片遮挡要求。新增的显式多模态操作只发送本机已脱敏且经人放行的派生图片，
不把原图、原 PDF、网页归档、HTML、Cookie 或签名下载地址发送给模型；服务端再次
核验发出的精确图片哈希。既有纯文本 card generate 不因此隐式外传图片或扩大材料范围。

原型 HTML 生成只读取固定要求及功能选择的白名单脱敏文本，不带真实人员/报价数据；
生成内容在归档和截图入库前同样检查。沙盒产物交接及临时生命周期由 sandbox.md 规定，
截图交接后必须通过本机像素脱敏与人类入库关口。生成 worker 不可代填隐私放行。

### 多模态辅助契约

显式 `screenshot analyze` 固定 requirement ID/引用哈希、图片 rendition/hash、像素映射、
任务材料选择、模型目录身份/价格、推理档位、遮挡版本及输入清单。三个可选目的为
`match_requirements`、`propose_regions`、`read_text`，均纳入本切片，不要求另购云 OCR。
平台配置选择具备图片能力的 GLM 或 DeepSeek；若选中端点不支持所需格式，明确拒绝，
不能悄悄改用其他模型、上传原图或退回自动肯定结果。

模型只看显式选中要求的脱敏文本与派生图片；每张图片使用请求局部 ref，服务端映射回
内部 ID，禁止让模型返回 URL/存储路径建立新来源。结果分别是匹配候选、区域矩形与读字
建议，可附置信度和不确定原因；无匹配可以返回空集合，不能为凑覆盖自动生成证据。
坐标必须落在实际发送图片的内容区，经固定映射还原到引用图；超限/越界/未知 ref 拒绝。
模型读字不是经验证的逐字引用，不能直接填旧 quote 或升级 `human_image_review`。

建议只进入不可变的待核对记录。人查看对应像素，修正观察说明后才能接到卡片并按职责
确认；程序可把候选应用到可编辑卡片，但不得覆盖 pending_review、confirmed、comply_only
或已记录负偏离。模型生成、建议采纳、隐私放行、证据确认和原型导出决定各自留痕，
任一动作都不能代替下一关。所有被发送材料保守计入依赖，即使最终没有被模型引用。

## 裁剪、区域标注与无标签原型

坐标使用方向规范化后的来源像素，左上为原点；整数矩形 `x/y >= 0`、`width/height >= 1`。
先脱敏，再裁剪，后画框，非原型最后加固定来源 footer。裁剪和框选不能重绘图中文字、参数、时间、
证书编号或印章，不能补全遮挡区域。图示不允许转标为 screenshot，原型内部 provenance
只可继承，不能因裁剪、读字、确认或 keep 变成真实来源。

初次处理保存来源坐标的遮挡区域、crop、框；以后派生只读取已脱敏父图的内容区，所有
坐标相对该内容区，不能引用 footer/padding。每个框须完整包含于 crop；越界直接报错。
保存父图 ID、处理计划及 `content_offset_x/y`、内容宽高、footer 高度；逐级映射可回到
来源位置，并明确哪些位置已遮挡、哪些属于工具生成区域。同图可派生不同区域，同一
派生图可支持不同卡片，但每张卡片产生各自的 Evidence 和人工判断。

采用 [annotation.md 的精确像素规则](annotation.md#精确输入与像素决定批准时一并确认)
作为 Rust 渲染基础：不自动缩放，框为 2 px 红色内边界，RGB PNG、无 alpha、固定编码。
源及输出每边最多 8192 px、总像素最多 20000000、PNG 最多 40 MiB，适用配置只能降低。
本机图片入口 PNG/JPEG 同受字节/像素限制；拒绝 SVG、HTML、动画、超限解码及任意路径引用。
生成 HTML 仅走单独的原型沙盒契约，不能作为图片上传内容执行。
一次最多 20 框、200 个遮挡矩形；加 footer 后超限仍拒绝，不静默降采样。

非原型使用 `screenshot-markup-v1` profile，在 footer 固定显示来源种类、来源哈希、来源时间
性质、计划哈希及“已脱敏”标记；它不把派生图称为原件，不宣称“已核验真实”。证书页
继续显示其固定来源 ID 和页码。确认状态由受权视图展示，不重写已归档 PNG。中文水印
使用随构建固定且许可可核验的字体资源，不运行时下载字体。

原型使用 `prototype-clean-v1`：所有原图交接结果、上传归档、预览、缩略图、本机输出、
裁剪和后续派生均无水印、无 footer、无可见原型标签，`footer_height=0`。初稿/导出只用
中性的附件编号、要求引用及必要哈希，不展示原型性质、模型名或“设计原型”等文字；
内部详情和人类决策界面提供完整 provenance 供判断。原型生成提示也不得在 HTML 中
植入此类标签；若产物自带标签须重新生成/修订 HTML 再经沙盒，不用抹除像素假装修复。
这不是通用去水印功能，也不允许改变或清除证书、厂家文件的原有标记。

### 与 annotation.md 的归属

建议合并 B05 的实现边界，**本草案批准后取代其“仅本机副本、不归档、不接卡片”的
交付范围**，复用其 Rust 算法、计划规范化、输入限制、确定性与子进程失败约束。
不修改旧草案，也不表示它已经批准或实施。

具体替代项是：增加像素遮挡及无标签原型 profile；新路径的未脱敏输入改走内存管道；输出
支持受权持久归档；新 profile 使用这里的材料分类与溯源，不把一切输入都写成
`USER-SUPPLIED PDF PAGE`。旧 profile 的固定 ASCII footer 和文件式临时输入不直接
用于新隐私路径。一个共享 Rust 引擎负责本机和 worker 渲染，禁止另做 Python 绘图分支。

建议以 `bid screenshot prepare/annotate` 为这条图片链入口，暂不同时发布旧草案的
`bid evidence source annotate`。设计中的通用 `bid evidence stamp` 名称保留给未来
完整 Evidence 标注入口；本切片不冒充完成全部 B04/B05/B06。旧证书来源 API/CLI 和
Archive 的“恒未确认”契约保持原样。

## 图片成为响应证据的关口

1. 人工放行入库后，资产仍是 `unconfirmed_material`，`confirmed_by=null`，本身永不
   获得 draft/export 资格。生成一张图或得到短签名预览都不等于建立 Evidence。
2. `card create/update` 新增带判别字段的 `image_region` 输入，引用精确资产、派生图
   与最终哈希、可见区域、`claim_scope`、人工填写的观察说明。必须同 org/task/抽取 job，
   功能、产品或证书选择仍有效；服务端解析固定修订、来源和图像描述符，不接收任意 URL/路径。
   同一资产跨卡片使用时分别检查每项要求，不复用另一张卡片的确认。
3. 区域说明是观察，不是逐字摘录。新分支用 `visual_observation`，不得填入旧 `quote`
   字段或伪造 `exact_field_match`。证书页缺少文本时可走图片分支，逐图人工审阅；旧
   `certificate_pdf_page` 分支仍要求原文精确匹配，不让图片分支改变其引用标准。
4. 图种与用途强制匹配：`functional_observation` 接受已知环境的运行/开发截图和来源
   完整的原型，用于描述功能界面；原型可建立与真实截图相同地位的 image_region Evidence，
   不以 planned/developing 状态拒绝。它不自动证明已经上线或已完成业务流程。
   `document_excerpt` 接受固定证书页，`hardware_documentation` 接受已归档的真实厂家
   网页/PDF，`design_explanation` 接受已有图示。原型不得用于硬件参数、报告或证书证明。
   审核人仍须判断条款是否明确要求实际系统、性能结果或完整原件，并如实保留偏离。
5. 人通过既有 `card confirm`，提交预期卡片修订和精确覆盖全部链接的
   `reviewed_evidence_ids`，并处理来源声明、测试环境、裁剪、遮挡和材料义务警示。
   Evidence 在创建时已固定最终图片哈希，换图或换区域必须产生新 Evidence 和卡片修订。
   对应职责的人须打开该版本图片，逐项核对来源、功能版本、画面、观察与负偏离；服务端
   重验文件哈希、隐私放行、未撤下状态、固定选择和当前权限，再同事务确认 Evidence/Card。
6. 未确认、已撤下、哈希不符、来源或选择失效的图片，均不能进入响应行或附件清单。
   缺口只保留要求与原因，不偷偷带入候选图片。初稿固定图片 ID/哈希/区域/计划与
   Evidence 确认信息，不保存长期 URL；读旧初稿重算失效状态。export 须重新核验
   同一组条件，只取审阅过的脱敏图，不能改取未脱敏原件或“最新一张”。

专业权限、人工逐卡确认、重开流程与 API 令牌永不能确认或导出的规则不变；admin 不
因可放行图片入库就获得跨专业确认权。单幅截图只能支持其可观察状态，不自动证明整个
业务流程、真实性、性能或已在客户现场交付。被遮挡或裁掉的关键证明内容视为不可观察，
应补交合适材料或如实记录不足，不从原图暗中补回。

### 正式导出前的原型决定

本关口只管理原型是否作为交付 UI 留在正式文档，不再次确认卡片内容。建议由该卡片
对应专业负责人决定，软件功能通常为 technical；bidder 仍按 export.md 执行人工发布。
决定写入仅允许有效人类 session，复用 `evidence:confirm` 及专业职责检查，不向 token、
agent、worker 开放，不因 admin 身份自动授予。每项原型 Evidence 起始为 `undecided`，
未决定不影响已确认 Evidence 进入 draft/review_copy，也不自动把初稿变为 partial。

- `keep` 必须显式选择 `already_delivered` 或 `will_deliver`，表示“它已是或将成为交付
  界面”。对同一固定 Evidence 只需一个有效决定，不要求每次导出重复选择；保留内部
  origin=prototype、模型与 HTML 哈希，不把保留解释成来源转换或已经交付认证。
- `replace` 表示需换真实截图，不能靠点击动作解除正式导出阻断。负责人按既有流程重开
  卡片，链接真实截图的新 Evidence、逐卡重审并重新组表。服务确认旧原型不再被该响应
  使用后，替换才算完成；新图若仍为原型，仍须自己的保留/替换决定。
- 按模块批量决定时先预检，返回模块标签、固定功能选择集合、逐项 Evidence/卡片修订/
  rendition/图片及 HTML 哈希和集合摘要。现有 Feature 没有模块实体，首版模块是此次
  显式选择的功能分组标签，不用模糊名称或“全部当前项”作授权；每项必须属于列明集合。
  人可对模块统一 keep/replace，也可逐项选择；提交须展开为完整 item 列表。整批原子
  校验和提交，一项无权、遗漏、重复、已变化即整批失败；不因模块决定确认其他卡片。
- 决定为只追加版本，修改需理由和预期旧决定 ID；初次提交预期 null。同一个请求重试
  幂等，服务在任务/卡片锁下核对。Evidence、区域、最终图片、HTML、卡片修订或任务
  选择改变后旧决定不能复用；新引用重新决定，选回旧资源也不复活旧决定。

正式 export 的内部 input manifest 纳入每项决定 ID/版本、精确绑定和决定集合哈希，
所有检查阶段重算；普通 acknowledge_issue_ids、`--force`、隐私放行、卡片确认和
release 都不能补出 keep。决定改变使依赖它的正式 run/候选/导出件失效，按 export.md
重新 prepare/release；已下载副本不能远程收回。审阅件不依赖决定集合，不因 keep/replace
变化单独失效，仍受卡片重开、图片撤下等原有门禁约束。

批准后的 B11 附件消费同时扩展到本草案 image_region：只取已确认 Evidence 固定的
派生 PNG，保持其字节哈希，按现有附录/编号方式排版；不重新生成、在线取证或外发。
同图去重不能丢掉逐 Evidence 决定；同一原型被多条要求引用，每项都必须有有效决定。
未决定项和 replace 待完成项只从正式导出门禁报告，不变成可打印的原型标签。

## 数据模型与单位隔离

以下均为拟新增表。全部具有 `org_id NOT NULL`、`UNIQUE(org_id,id)`、ENABLE/FORCE RLS，
使用带 WITH CHECK 的单位策略；运行角色非属主、无超级用户/BYPASSRLS，缺单位上下文
拒绝。迁移必须同次交付两单位隔离验证，不将只靠应用 WHERE 过滤视为完成。

| 表 | 关键字段与约束 |
| --- | --- |
| `screenshot_search_runs`、`screenshot_search_candidates` | 固定 task/extraction_job、task_resource/product_revision、查询哈希、搜索配置/价格版本、job；候选归属 run 并存局部 ref、URL/标题/发现时间；只追加，候选不能直接成为 Evidence |
| `screenshot_vendor_archives` | task/extraction_job、task_resource/product_revision、可选 search_candidate；URL/final_url、captured_at、title、媒体类型、content/archive SHA、归档副本描述符及私有键、清理计划；只追加，网页/PDF 字节归档与页图同批绑定 |
| `screenshot_prototype_runs` | task/extraction_job、requirement、task_feature/feature_revision、generation_job、脱敏输入哈希、模型/目录/规则身份、html_sha256 与 HTML 归档、沙盒回执标识/哈希；origin 固定 prototype；只追加，不带人工确认字段 |
| `screenshot_assets` | task/extraction_job；source_kind、image_kind、origin；带类型的功能/证书链，或 vendor_archive/产品链，或 prototype_run/功能链；来源哈希与保证等级、来源元数据、接收身份/时间。不可变，不设“确认资产”字段 |
| `screenshot_renditions` | asset、可选 parent_rendition、source/upload/image SHA、处理计划/哈希、profile、内容坐标映射、像素/字节描述符、私有对象键、生成 job/actor、created_at；只追加，禁止覆盖对象或改父链；同父图/计划/profile 可幂等复用 |
| `screenshot_privacy_reviews` | asset、首个 rendition、reviewed_upload_sha256、stored_image_sha256、reviewed_by/at、隐私规则版本；只接受可信人类会话写入，和入库成功同事务。后代只可引用该放行并证明变换未引入新内容 |
| `screenshot_withdrawals` | asset、理由、withdrawn_by/at、关联请求 ID；只追加，一次撤下作用于该资产所有派生图和引用，重复请求幂等；恢复须新资产，不删除撤下记录 |
| `screenshot_analysis_runs`、`screenshot_analysis_inputs`、`screenshot_suggestions` | 分析 job/模型/价格/遮挡/输入哈希；inputs 逐行约束被发送的 requirement 与 rendition/hash/隐私放行；suggestions 关联局部 ref、目的、区域/读字/匹配及置信度，恒未确认；原始厂商返回不持久化 |
| `prototype_decision_batches`、`prototype_evidence_decisions` | batch 固定 task/extraction_job、模块标签/输入集合哈希、人及时间/幂等键；item 固定 Evidence、card_revision、rendition/image/html SHA、功能选择及修订，decision=keep/replace、keep_basis、前决定 ID、理由；只追加，每项一个有效决定，undecided 为缺少有效决定的派生状态 |

不另建截图专用卡片状态机或绕过租户的全局图库。文件复用只发生在同单位、同任务和
固定来源关系内；相同字节关联另一项功能必须新建独立资产，不能通过哈希跨租户查重。

所有业务引用采用包含 org_id 的复合外键，并补足同任务/父对象约束：

- asset → `(org_id, task_id, extraction_job_id)`，抽取 job 须成功且 document 属于任务；
  功能分支 → 固定 `(org_id, task_id, task_feature_id, feature_revision_id)` 及其产品；
  证书分支 → Archive 的同任务 source/selection/revision/file/page 组合。不能只存 JSON ID。
- vendor archive/candidate/run → 同 `(org_id, task_id, extraction_job_id)` 及
  `(org_id, task_id, task_resource_id, product_revision_id)`；PDF 页图还绑定 archive/page。
  prototype run → 同任务要求、抽取 job、功能选择/修订和 generation_job，不能把另一
  功能的 HTML 拼进截图。没有模型/HTML 记录的外部原型不冒充 generation run。
- rendition → `(org_id, task_id, asset_id)`；父图还需同 asset，父链只能指向已有记录，
  防止循环与跨资产换绑。review/withdrawal → 同一资产，review 的 rendition/hash 必须
  与实际入库关系一致；人引用 `(org_id, memberships.user_id)`，令牌引用单位复合键。
- 复用 `evidence`、`card_evidence_links`：Evidence 拟增加 screenshot_asset/rendition
  复合引用、`visual_observation`、区域、用途、固定 image SHA，沿原链接绑定精确卡片修订。
  只对 image_region 分支允许 quote 为空；旧分支继续强制非空逐字 quote。材料性质新增
  `user_screenshot/browser_screenshot/user_diagram/certificate_image/vendor_web/vendor_pdf/prototype`。
- analysis inputs/suggestions → 同单位同任务 run/requirement/rendition/资产链；模型返回
  的自由 ref 不能作为外键。decision item → 同 batch/org/task/job、Evidence/card_revision、
  图/HTML/功能链及前决定，补充复合唯一键并拒绝同单位跨任务或跨卡片拼接。人仍引用
  `(org_id, memberships.user_id)`；复合关系之外，服务/DB 同时检查专业职责和当前修订。
- 扩展组表输入清单，固定图片依赖与撤下状态；Job 的 document_id 继续引用本次抽取
  job 的真实招标 Document，不能新造招标文件或填证书 ID 满足非空约束。
- export_run_evidence 对原型新增包含 org/run/evidence 的决定关联，正式模式强制覆盖
  所有原型 Evidence 且决定有效为 keep；review_copy 不要求。不得只在 manifest JSON
  中塞决定 ID 而缺外键/完整性关口；服务、数据库和下载复核不能仅依赖界面禁用按钮。

分支互斥、来源/图种匹配、JSON 非空与 NULL、像素上限、父链、人工写入关口、证据确认
及组表消费条件均需 SQL 约束/触发器与服务层共同检查。运行角色不能 UPDATE/DELETE
历史资产、归档、生成/分析记录、建议、派生图、隐私放行、决定、撤下或审计记录；
必要的证据首次确认沿既有受控关口进行。新增令牌禁授 screenshot:ingest，原型决定
复用已禁止的 evidence:confirm，不增可供 agent 冒充人的决策范围。
并发锁顺序沿任务 → 卡片 → 关联记录，渲染/读写对象不持有长事务；发布时短事务重验。

## Pydantic 契约草案

以下模型仅为本文内的接口提案，不注册代码、API 或 schema。复用
[`Contract/Result/Cost`](../../server/app/schemas/contracts.py) 的 extra=forbid；字符串
去空白后非空，时间必须带时区。跨字段验证规则在代码块后列出，不只依赖字段类型。

```python
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ImageKind = Literal["screenshot", "diagram", "prototype", "certificate_page", "vendor_page"]
Environment = Literal["production", "test", "development", "prototype", "unknown"]
AnalysisPurpose = Literal["match_requirements", "propose_regions", "read_text"]

class PixelRect(Contract):
    x: int = Field(ge=0, le=8192)
    y: int = Field(ge=0, le=8192)
    width: int = Field(ge=1, le=8192)
    height: int = Field(ge=1, le=8192)

class ImagePlan(Contract):
    redact: list[PixelRect] = Field(default_factory=list, max_length=200)
    crop: PixelRect | None = None
    boxes: list[PixelRect] = Field(default_factory=list, max_length=20)

class UploadSource(Contract):
    kind: Literal["upload"]
    task_feature_id: UUID
    image_kind: Literal["screenshot", "diagram", "prototype"]
    source_label: str = Field(min_length=1, max_length=200)
    software_version: str = Field(min_length=1, max_length=200)
    environment: Environment
    captured_at: datetime | None = None
    prototype_run_id: UUID | None = None

class BrowserSource(Contract):
    kind: Literal["local_browser"]
    task_feature_id: UUID
    image_kind: Literal["screenshot", "prototype"]
    software_version: str = Field(min_length=1, max_length=200)
    environment: Environment
    capture_id: UUID
    captured_at: datetime
    system_origin: str = Field(min_length=1, max_length=300)
    route_label: str = Field(min_length=1, max_length=200)
    browser_version: str = Field(min_length=1, max_length=100)
    tool_version: str = Field(min_length=1, max_length=100)
    viewport_width: int = Field(ge=1, le=8192)
    viewport_height: int = Field(ge=1, le=8192)
    device_scale_factor: float = Field(gt=0, le=4)
    capture_mode: Literal["viewport", "element"]
    prototype_run_id: UUID | None = None

class CertificateSource(Contract):
    kind: Literal["certificate_page"]
    evidence_source_id: UUID

class ArchiveDescriptor(Contract):
    sha256: Sha256
    size_bytes: int = Field(gt=0)
    media_type: Literal["application/pdf", "application/zip", "text/html"]

class VendorSource(Contract):
    kind: Literal["vendor_web", "vendor_pdf"]
    task_resource_id: UUID
    search_candidate_id: UUID | None = None
    source_url: HttpUrl
    final_url: HttpUrl
    title: str = Field(min_length=1, max_length=500)
    captured_at: datetime
    content_sha256: Sha256
    archive: ArchiveDescriptor
    archive_redaction_plan_sha256: Sha256
    page_number: int | None = Field(default=None, ge=1)
    page_count: int | None = Field(default=None, ge=1)

class PrototypeSource(Contract):
    kind: Literal["prototype_render"]
    prototype_run_id: UUID

ScreenshotSource = Annotated[
    UploadSource | BrowserSource | CertificateSource | VendorSource | PrototypeSource,
    Field(discriminator="kind")
]

class ScreenshotPrepareInput(Contract):
    source: Annotated[
        UploadSource | CertificateSource | PrototypeSource, Field(discriminator="kind")
    ]
    plan: ImagePlan

class ScreenshotCaptureInput(Contract):
    task_feature_id: UUID
    image_kind: Literal["screenshot", "prototype"]
    software_version: str = Field(min_length=1, max_length=200)
    environment: Environment
    route_label: str = Field(min_length=1, max_length=200)
    viewport_width: int = Field(ge=1, le=8192)
    viewport_height: int = Field(ge=1, le=8192)
    device_scale_factor: float = Field(gt=0, le=4)
    capture_mode: Literal["viewport", "element"]
    prototype_run_id: UUID | None = None
    plan: ImagePlan

class VendorSearchInput(Contract):
    extraction_job_id: UUID
    task_resource_id: UUID
    requirement_ids: list[UUID] = Field(min_length=1, max_length=50)
    expected_input_hash: Sha256 | None = None
    dry_run: bool = False
    retry: bool = False

class VendorCaptureInput(Contract):
    extraction_job_id: UUID
    task_resource_id: UUID
    search_candidate_id: UUID | None = None
    selected_source_url: HttpUrl | None = None
    page_number: int | None = Field(default=None, ge=1)
    plan: ImagePlan

class PrototypeGenerateInput(Contract):
    extraction_job_id: UUID
    requirement_id: UUID
    task_feature_id: UUID
    expected_input_hash: Sha256 | None = None
    reasoning: str | None = None
    dry_run: bool = False
    retry: bool = False

class PrototypeGenerationView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    generation_job_id: UUID
    requirement_id: UUID
    task_feature_id: UUID
    origin: Literal["prototype"] = "prototype"
    provider: str
    model: str
    catalog_identity: str
    input_hash: Sha256
    html: ArchiveDescriptor
    html_sha256: Sha256
    sandbox_receipt_id: str
    sandbox_receipt_sha256: Sha256
    source_image_sha256: Sha256

class PNGDescriptor(Contract):
    sha256: Sha256
    size_bytes: int = Field(gt=0, le=40 * 1024 * 1024)
    width_px: int = Field(ge=1, le=8192)
    height_px: int = Field(ge=1, le=8192)
    media_type: Literal["image/png"] = "image/png"

class ContentMapping(Contract):
    crop: PixelRect
    content_offset_x: int = Field(ge=0)
    content_offset_y: int = Field(ge=0)
    content_width: int = Field(ge=1)
    content_height: int = Field(ge=1)
    footer_height: int = Field(ge=0)

class PreparedScreenshot(Contract):
    source: ScreenshotSource
    source_sha256: Sha256
    source_width: int = Field(ge=1, le=8192)
    source_height: int = Field(ge=1, le=8192)
    plan: ImagePlan
    plan_sha256: Sha256
    preparation_profile: Literal["screenshot-privacy-v1"]
    prepared_at: datetime
    image: PNGDescriptor
    mapping: ContentMapping

class ScreenshotIngest(Contract):
    extraction_job_id: UUID
    prepared: PreparedScreenshot
    reviewed_upload_sha256: Sha256
    reviewed_archive_sha256: Sha256 | None = None
    idempotency_key: UUID

class ScreenshotAnnotate(Contract):
    parent_rendition_id: UUID
    expected_image_sha256: Sha256
    plan: ImagePlan
    dry_run: bool = False
    retry: bool = False

class ScreenshotWithdraw(Contract):
    reason: str = Field(min_length=1, max_length=2000)

class ScreenshotView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    extraction_job_id: UUID
    source: ScreenshotSource
    image_kind: ImageKind
    origin: Literal["user", "browser", "certificate", "vendor", "prototype"]
    selection_id: UUID
    resource_revision_id: UUID
    source_sha256: Sha256
    source_hash_assurance: Literal["client_declared", "server_verified"]
    source_archive: EvidenceSourceArchive | None
    vendor_archive_id: UUID | None
    prototype_generation: PrototypeGenerationView | None
    received_at: datetime
    active_selection: bool
    withdrawn: bool
    status: Literal["unconfirmed_material"] = "unconfirmed_material"
    confirmed_by: Literal[None] = None
    eligible_for_draft_export: Literal[False] = False
    warning_codes: list[str]

class RenditionView(Contract):
    id: UUID
    asset_id: UUID
    parent_rendition_id: UUID | None
    image: PNGDescriptor
    plan: ImagePlan
    plan_sha256: Sha256
    profile: Literal["screenshot-markup-v1", "prototype-clean-v1"]
    mapping: ContentMapping
    privacy_review_id: UUID
    privacy_basis: Literal["human_upload_review", "safe_derivation"]
    prototype_watermark: Literal[False] = False

class ImageEvidenceInput(Contract):
    kind: Literal["image_region"]
    asset_id: UUID
    rendition_id: UUID
    expected_image_sha256: Sha256
    region: PixelRect
    claim_scope: Literal["functional_observation", "design_explanation",
                         "document_excerpt", "hardware_documentation"]
    visual_observation: str = Field(min_length=1, max_length=4000)

class AnalysisImageInput(Contract):
    rendition_id: UUID
    expected_image_sha256: Sha256

class ScreenshotAnalyzeInput(Contract):
    extraction_job_id: UUID
    requirement_ids: list[UUID] = Field(min_length=1, max_length=50)
    images: list[AnalysisImageInput] = Field(min_length=1, max_length=20)
    purposes: list[AnalysisPurpose] = Field(min_length=1, max_length=3)
    expected_input_hash: Sha256 | None = None
    reasoning: str | None = None
    dry_run: bool = False
    retry: bool = False

class VisionProposal(Contract):
    image_ref: str = Field(min_length=1, max_length=80)
    requirement_ref: str | None = Field(default=None, max_length=80)
    purpose: AnalysisPurpose
    region: PixelRect | None = None
    suggested_text: str | None = Field(default=None, max_length=4000)
    confidence: float | None = Field(default=None, ge=0, le=1)

class ScreenshotSuggestionView(Contract):
    id: UUID
    analysis_run_id: UUID
    rendition_id: UUID
    image_sha256: Sha256
    requirement_id: UUID | None
    proposal: VisionProposal
    status: Literal["unreviewed"] = "unreviewed"
    confirmed_by: Literal[None] = None

class VendorJobPreview(Contract):
    dry_run: Literal[True] = True
    input_hash: Sha256
    outbound_image_hashes: list[Sha256]
    outbound_text_hashes: list[Sha256]
    catalog_identity: str
    price_revision: str
    input_image_count: int = Field(ge=0)
    planned_calls: int = Field(ge=0)
    estimated_cost: Cost
    estimated_charge: Decimal | None = Field(default=None, ge=0)
    billing_currency: str
    cost_basis: Literal["first_pass_upper_bound", "unknown"]
    admission_blocker: str | None

class PrototypeDecisionTarget(Contract):
    evidence_id: UUID
    card_revision_id: UUID
    rendition_id: UUID
    image_sha256: Sha256
    html_sha256: Sha256
    task_feature_id: UUID
    expected_previous_decision_id: UUID | None = None

class PrototypeDecisionPreviewInput(Contract):
    extraction_job_id: UUID
    module_label: str = Field(min_length=1, max_length=200)
    task_feature_ids: list[UUID] = Field(min_length=1, max_length=200)

class PrototypeDecisionPreview(Contract):
    input_hash: Sha256
    module_label: str
    targets: list[PrototypeDecisionTarget]

class PrototypeDecisionItem(PrototypeDecisionTarget):
    decision: Literal["keep", "replace"]
    keep_basis: Literal["already_delivered", "will_deliver"] | None = None
    reason: str | None = Field(default=None, min_length=1, max_length=2000)

class PrototypeDecisionBatch(Contract):
    extraction_job_id: UUID
    module_label: str = Field(min_length=1, max_length=200)
    task_feature_ids: list[UUID] = Field(min_length=1, max_length=200)
    expected_input_hash: Sha256
    items: list[PrototypeDecisionItem] = Field(min_length=1, max_length=500)
    idempotency_key: UUID

class PrototypeDecisionView(Contract):
    id: UUID
    batch_id: UUID
    target: PrototypeDecisionTarget
    decision: Literal["keep", "replace"]
    keep_basis: Literal["already_delivered", "will_deliver"] | None
    decided_by: UUID
    decided_at: datetime
    validity: Literal["current", "stale", "superseded"]

class ScreenshotPreviewLink(Contract):
    url: str
    expires_in: Literal[300] = 300
    rendition_id: UUID
    image: PNGDescriptor

class ScreenshotJobResult(Contract):
    job_id: UUID
    asset_id: UUID
    rendition: RenditionView
    duplicate: bool

class ScreenshotDryRun(Contract):
    dry_run: Literal[True] = True
    parent_rendition_id: UUID
    input_hash: Sha256
    estimated_cost: Cost
    estimated_charge: Decimal
    billing_currency: str
    cost_basis: Literal["known"] = "known"
    estimated_duration_ms: int | None = Field(default=None, ge=0)
```

输入验证及输出约束：

- prepared 是本机回执，不是已批准来源。API 只接受 multipart 中的 PNG 及
  ScreenshotIngest，厂家分支另附受限归档副本；重算 upload SHA、尺寸和计划哈希。不得提交 org、确认人、权限、
  存储键、自由水印/profile 或服务端解析的资源修订；证书来源全关系与 hash 从 Archive
  重验。服务端以可信 Renderer 生成最后一版，最终 hash 可与上传 hash 不同。
  vendor 分支必须有匹配的 reviewed_archive_sha256，其他分支该值为空；生成来源由
  prototype_run_id 解析真实模型、HTML、沙盒回执与源图，客户端不能填这些事实。
- prepare 的 --input 为 ScreenshotPrepareInput，source.kind 与 --file/--source 及
  source ID 须一致；capture 的 --input 为 ScreenshotCaptureInput，工具把真实采集
  回执补为 BrowserSource，再执行相同隐私处理。--config 只定义本机目标、受权会话与
  必要的元素定位，不允许任意脚本、页面变更动作或输出路径，不能进入上传回执。
- 接收 prepared 图时按已验证 mapping 定位脱敏内容区，再应用对应服务端 profile：
  原型无水印/footer，其他来源加固定 footer；不把来源坐标的遮挡/crop 再错用到已裁剪
  图片。服务端保存两段坐标变换，并校验输出只由上传内容与该 profile 允许的标注构成。
  证书分支另在内存重放固定来源的同一计划，
  比对脱敏内容，防止用无关图片冒充该页；上传/浏览器原图不在服务端，不能伪称完成此项核验。
- `system_origin` 仅 scheme/host/port，不接受 userinfo/path/query/fragment；标签、版本
  等自由文字也须隐私检查。BrowserSource 的采集字段由本机工具生成，用户不通过普通
  upload 参数冒充；API 仍标明其保证等级为 client_declared。
- VendorCaptureInput 的 search_candidate_id 与 selected_source_url 恰选一个；前者
  须属于该任务/产品搜索，后者只能来自固定产品修订列明的官方来源。URL 校验除 HttpUrl
  语法外还执行取证边界；PDF 必须有有效页码/页数且 archive 类型为 application/pdf，
  网页页码为空、归档为静态快照包。两种来源均须有非空标题；无法取得时不能编造标题。
  字节包限制、解包大小/数量及 PDF 页数沿来源 profile 校验，拒绝路径穿越、活动预览、
  嵌套归档或解码炸弹，不把归档正文复制进 Result。原型 html 描述符必须为 text/html
  且与 html_sha256 一致；HTML 内容只走受权归档读取，不能作为页面直接打开。
- 本机初次输出与每次服务端派生都保留可逆坐标映射；区域只引用可见内容。限制实际
  解码像素与进程资源，不依靠图片声明或渲染后的检查。prototype 环境须为 prototype
  类型；unknown 环境只能归档待查，不能确认 functional_observation。原型的 origin
  固定 prototype、profile 固定 prototype-clean-v1、footer_height=0，不受客户端开关控制。
- 原有 EvidenceInput 增加 ImageEvidenceInput 判别分支，EvidenceView 增加相应图片
  视图与 `quote_check=unreviewed_image/human_image_review`；旧资源/文本页字段及
  行为保持不变。不能给旧 quote 字段填观察说明以适配旧模型。
- 正常资产/图种/用途/状态组合及跨表检查以[图片成为响应证据的关口](#图片成为响应证据的关口)
  为准；不向卡片提供可写的 confirmed_by。相同图但不同用途仍需独立审阅。
  source_archive 仅在 certificate_page 分支非空，沿用完整 Archive 并恒未确认；其他
  来源必须为空，不能伪造一份证书 Archive。
- PrototypeGenerateInput 每次只指定一个固定功能要求，不能用 free-form URL/脚本
  代替输入；产物只能是该功能的单页 HTML。原型生成、搜索、分析非 dry-run 都须带
  预检 expected_input_hash；dry-run 禁止 retry，不持久化外发快照。模型/价格由平台
  配置解析，不能在输入中塞厂商密钥、端点或未批准的单位模型配置。
- ScreenshotAnalyzeInput 中 ID/目的去重，数量在上限内，并按端点的更低图片/像素/
  payload 上限分批；实际发出的 bytes、hash、尺寸和映射须一致。VisionProposal 的
  match_requirements 必须有本请求 requirement_ref；propose_regions/read_text 必须
  有 region，read_text 另须非空 suggested_text。未知 ref/非有限置信度/越界整项拒绝；
  已发送图的标记区不可用作证明位置。读字失败保留缺项，不尝试还原被遮挡的原文。
- 原型决定预检只列有效已确认的原型 Evidence；同功能多卡分别列出。batch 必须精确
  覆盖预检 targets，集合/前决定/所有 hash 重算，超过 500 项须显式拆分成多份模块清单，
  不静默截断。keep 必须有 keep_basis；replace 的 keep_basis 必须为 null；修改旧决定
  必填 reason。decided_by/at 仅服务生成，validity 读取时重算，内部详情不投射成图注。
- 详情回执包含 `asset: ScreenshotView` 与 `rendition: RenditionView`；初稿的图片
  引用的内部清单保留必要元数据及 Evidence 确认信息，文档呈现遵守无原型标签规则，
  去掉链接和对象路径。输出枚举、nullable
  quote 与 schema 联合的变化须作兼容性审查；不兼容时按 CLI 契约升主版本，不能只把
  新枚举塞给旧的严格客户端。保留旧输入分支，更新 `bid schema` 对应版本。

### Provider 边界

`LocalBrowserProvider.capture(config) -> (bytes, BrowserSource)` 仅在本机返回内存图片
和脱敏采集元数据；config 不入数据库，不允许在服务端反序列化任意脚本或 CDP 指令。
`ScreenshotRenderer.prepare(bytes, source, plan) -> (bytes, PreparedScreenshot)` 及
`derive(bytes, metadata, plan) -> (bytes, rendering)` 由 Python 编排受限 Rust 进程；
后者也供 worker 使用。Python 校验进程输出的字节、hash、尺寸、profile 和来源绑定。

拟扩展现有接入层的逻辑入口为 `LLMProvider.generate_prototype(fixed_text, schema)`
与 `VisionProvider.analyze(redacted_images, requirements, purposes, schema)`；视觉入口
复用平台模型解析、GLM/DeepSeek 对应 HTTP adapter、官方推理档位、结构化输出校验、
safe_metadata 和 accounted_call，不创建绕开 providers/ 的第二套厂商客户端。
`SearchProvider.search(redacted_query)` 返回候选；`BrowserProvider.capture_vendor`
在本机取得同次网页归档与截图，PDF 则按固定页渲染。生成 HTML 的渲染调用 sandbox.md
定义的接口，只消费可核验的 HTML/hash/渲染回执；本文不预设其类名或执行策略。

渲染子进程无网络、无 shell，不继承令牌和服务密钥；输入/输出与错误有界，deadline
建议沿用 20 秒，超时或取消终止并回收自有进程。本机浏览器捕获另设 30 秒 deadline；
不在超时后接收迟到结果。只把已脱敏结果原子写入新文件，拒绝覆盖、symlink 和路径
替换竞态；文件 0600、私有目录 0700，失败清理本次临时产物，不删除用户原件。

## CLI、API、存储与权限

下列命令均支持 `--json`，缺参数返回错误、无交互提问。`--input` 读取上述模型或本机
准备计划；身份来自会话或令牌。prepare/capture 写回执供人查看，add 是后续显式入库。
本地模式仍经本机 PostgreSQL/RLS 与相同服务，不能以本机文件直写跳过隐私放行或确认。

| CLI | API | 输出与权限 |
| --- | --- | --- |
| `bid screenshot prepare --file FILE --input PLAN --output NEW.png --receipt NEW.json` | 无远程渲染路由；本机工具 | 上传图的 PreparedScreenshot 与实际文件；不入库、不确认证据 |
| `bid screenshot prepare --source S --input PLAN --output NEW.png --receipt NEW.json` | 复用既有受权来源预览/读取 | 内存读取固定证书页；与 --file 互斥，保留来源权限交集 |
| `bid screenshot capture --config PRIVATE.json --input PLAN --output NEW.png --receipt NEW.json` | 无 SaaS 浏览器采集路由 | 本机 capture 后执行相同 prepare；不把配置/凭据放进 Result |
| `bid evidence search --task T --input FILE [--dry-run] [--retry] [--wait]` | `POST /tasks/{T}/screenshot-searches` | VendorSearchInput → VendorJobPreview / Job；结果为已归属搜索 run 的候选，非 Evidence；screenshot:write、resource:read |
| `bid screenshot capture-vendor --input FILE --output NEW.png --archive NEW.bin --receipt NEW.json` | 读取受权候选或固定产品来源；本机 BrowserProvider/PDF 取证 | VendorCaptureInput → PreparedScreenshot/归档文件回执；先本机脱敏，不自动入库 |
| `bid ui mock --task T --input FILE [--dry-run] [--retry] [--wait]` | `POST /tasks/{T}/prototype-generations` | PrototypeGenerateInput → VendorJobPreview / Job → PrototypeGenerationView；screenshot:write、resource:read，生成后仍需本机 prepare 和人工 add |
| `bid screenshot prepare --prototype-run G --input PLAN --output NEW.png --receipt NEW.json` | 受权读取固定沙盒产物，交接按 sandbox.md | PrototypeSource → PreparedScreenshot；本机脱敏及无标签 profile，不确认证据 |
| `bid screenshot add --task T --file SAFE.png [--archive SAFE.bin] --input INGEST.json` | `POST /tasks/{T}/screenshots`，multipart | asset/rendition 回执；厂家归档与图同事务关联；screenshot:ingest，仅人类会话 |
| `bid screenshot list --task T --job J [--history] [--cursor C]` | `GET /tasks/{T}/screenshots?job=J&history=...&cursor=...` | items 为资产摘要，data 含 next_cursor；screenshot:read |
| `bid screenshot show --id S` | `GET /screenshots/{S}` | 来源、派生图列表及失效原因；screenshot:read |
| `bid screenshot annotate --id S --input PLAN.json [--dry-run] [--retry] [--wait]` | `POST /screenshots/{S}/renditions` | ScreenshotAnnotate → Job 回执 / ScreenshotDryRun → ScreenshotJobResult；screenshot:write |
| `bid screenshot preview --id R --output NEW.png` | `POST /screenshot-renditions/{R}/preview-link`；`GET /screenshot-renditions/{R}/content` | 短签名及受权读取；CLI 下载验证后返回文件回执；screenshot:read |
| `bid screenshot withdraw --id S --input FILE` | `POST /screenshots/{S}/withdrawals` | 撤下回执；screenshot:ingest，仅人类会话，理由必填 |
| `bid screenshot analyze --task T --input FILE [--dry-run] [--retry] [--wait]` | `POST /tasks/{T}/screenshot-analyses` | ScreenshotAnalyzeInput → VendorJobPreview / Job → 未确认建议与拒绝项；screenshot:write 及所有输入读取权限 |
| `bid screenshot suggestions --analysis A [--cursor C]` | `GET /screenshot-analyses/{A}/suggestions?cursor=...` | ScreenshotSuggestionView items；screenshot:read；无自动确认动作 |
| `bid card create/update ... --input FILE` | 复用 `POST /tasks/{T}/cards`、`PUT /cards/{C}` | CardContent.evidence 增加 image_region；card:write 及图片读取权限 |
| `bid card confirm ... --evidence E` | 复用 `POST /cards/{C}/actions` | 对应职责的人按原契约逐卡确认，没有 screenshot confirm 命令 |
| `bid screenshot prototype-decisions preview --task T --input FILE` | `POST /tasks/{T}/prototype-decisions/preview` | PrototypeDecisionPreviewInput → PrototypeDecisionPreview；只读、零写入；对应职责的人及 evidence:confirm |
| `bid screenshot prototype-decisions apply --task T --input FILE` | `POST /tasks/{T}/prototype-decisions` | PrototypeDecisionBatch → batch/逐项 PrototypeDecisionView；整批原子、仅对应职责人类 |
| `bid screenshot prototype-decisions list --task T --job J` | `GET /tasks/{T}/prototype-decisions?job=J` | 内部决定历史/有效状态；card:read 及材料权限 |
| `bid draft ...`、`bid job status/wait ...` | 复用现有对应路由 | 初稿内部图片引用及作业失败原因；不因原型未决定制造 draft 缺口 |
| `bid export prepare/release/download ...` | 复用 export.md 已批准路由 | 按本草案修订增加图片附件与正式原型拒绝清单，保留人类 bidder 门禁；不新增自动发布 |

新增 `screenshot:read/write/ingest` 范围；read 供四种既有读取角色，write 供
admin/bidder/technical，ingest 按上文人类限定。每次同时检查 task:read 和原材料权限：
功能需 resource:read；证书需 evidence:source:read、certificate:read、certificate:file:read。
厂家来源需 resource:read 并绑定产品选择；生成/分析/搜索 job 的读取同样核对 task 与输入
材料权限。沙盒和模型 worker 只写候选，无 screenshot:ingest、evidence:confirm 或 export。
旧令牌不自动增权；新令牌最多获 read/write，与当前成员角色取交集。原有 API 令牌
禁止 evidence:confirm/export 的签发、服务和 SQL 限制不变，不因新增图片命令而放宽。

图片加密对象键形如 `org/{org_id}/screenshots/{asset_id}/{rendition_id}/{sha256}.png`；
本地存储也同此前缀。客户端不能给对象键，API 不返回存储路径，不提供公开 bucket。
预览链接有效期 300 秒，签名绑定 org/rendition/hash/purpose，实际读取仍需有效身份、
Membership 和全部原材料权限；过期、换租户、改参数、撤下或成员失效均拒绝。
无权与不存在对象统一 404。响应 `Cache-Control: no-store`，不经公开 CDN 缓存。
缩略图若生成，沿用相同授权、脱敏、原型无水印 profile 和哈希关系，不自动生成未脱敏缩略图。
厂家归档、HTML 及分析输入快照也加密保存在 org 前缀下，用专用短签名/受权读取；
网页/HTML 只能下载为源文件或由指定渲染流程读取，不作为同源活动页面托管。内部 provenance
不放进图片嵌入字段、导出附件标签或公开 URL；不存在匿名读取/跨单位哈希命中接口。

API token 的受权预览是材料读取，不是 bid export；它不能读取未确认初稿附件包或
调用组装导出。下载命令验证同源精确路径、禁止重定向，流式限长，检查 PNG/尺寸/SHA
后原子写新文件；只取得链接不能报“已下载”。签名 URL 不进入审计/常规日志。

### Result 与退出码

Result 顶层严格是 `ok`、`command`、`data`、`items`、`warnings`、`cost`、`duration_ms`
七键。详情/写入为 data 回执、items=[]；列表 items 为条目，data 放过滤范围与 cursor；
错误在 data.error，warnings 只含原因代码及受权对象 ID。二进制/base64 不进入 Result。
预览 API 的链接只在专用回执出现；CLI 下载后的 Result 只给新文件路径和校验描述符。

| 退出码 | 本切片语义 |
| --- | --- |
| 0 | 本机新文件与回执均完整写入、查询/入库/动作/dry-run 成功、作业受理或最终成功；受理并非完成 |
| 2 | 参数/格式/坐标/文件超限、已有输出、缺隐私审阅哈希、哈希预期冲突、非法图种用途、卡片修订冲突或确认缺项；正式导出原型未决定/待替换/决定失效 |
| 3 | 可重试网络/队列/存储问题、deadline、临时缺 Rust 工具，或执行前固定输入变化；按原因重新读取或重试 |
| 4 | 身份/角色/范围失败、令牌入库/确认/原型决定/导出、无权或跨单位资源 404、来源损坏、浏览器目标不符、模型不支持图片、价格/余额/调用额度不足或不可重试处理失败 |
| 5 | draft 因图片未确认等产生缺口；分析有已验证建议且部分项失败；或按 export.md 成功发布/下载 review_copy。返回完整有效部分及失败原因，不包含未确认证据的文档内容 |

单张准备/入库/标注为原子结果，没有“PNG 成功但回执丢失”的部分成功；失败不发布最终
文件/业务记录。模块原型决定也必须整批原子，不返回半批决定。单项原型生成/取证不发布
半份成功；多模态分析的部分建议按下节规则处理。所有错误和部分成功 ok=false；--wait
与 job status/wait 采用最终业务码。export 的 review_copy 候选就绪仍为 0，只有人工
发布/下载为 5，沿用 export.md；原型未决定不单独改变 draft 的完成码。
JSON/schema 校验须覆盖实际 API 和两种 CLI，不能泄漏 traceback 或输出约定外退出码。

## 作业、幂等与费用

本机 capture/prepare 有界执行，首个上传归档有界同步完成；服务端重编码和写对象结束
后短事务发布资产、图、隐私放行与审计，不在任务锁下长时间渲染。对象写入成功而 DB
提交失败时，未引用对象保持私有且不可预览；只回收本次失败产生的孤立对象，不能删
既有来源或历史材料。幂等键相同且内容相同返回原记录，内容不同返回 409/退出码 2。

派生图使用 `jobs.kind=screenshot_render`，原型生成使用 `prototype_generate`，厂家搜索
使用 `screenshot_search`，多模态建议使用 `screenshot_analyze`；复用
[background-jobs.md](../notes/background-jobs.md) 的 attempt/run_id、取消与有限重试。
渲染提交固定 asset/parent hash、plan/profile、隐私放行、原材料依赖和发起权限，缓存键包含
org/task/抽取 job 与这些输入；不因同图跨任务命中缓存。worker 恢复 org 上下文、复核
成员/令牌/范围及来源，发布前再验撤下与依赖。取消、过期 attempt、途中撤下或选择变化
均不得发布成功图；重试不能改用新父图、吞掉遮挡错误或覆盖旧图。

截图归档限定在已选成功抽取 job 的任务内，因此作业 document_id 来自其真实招标文件。
脱离任务的通用图库不在首版；不能为了图库把 Job.document_id 放宽为空。`--dry-run`
仅做授权、依赖、坐标与预计尺寸检查，零写入、零浏览器访问、零外部调用，预估耗时不
可靠时为 null。无法事先确定的最终 PNG 大小必须在实际渲染后再次检查。

本机取证、Rust 处理、隐私放行、原型人工决定、draft 组表及 export 渲染本身不调用模型：
该操作实际 `cost={llm_tokens:0, ocr_pages:0, usd:0}`，平台 charge 为 0，不制造空
UsageRecord。不得把生成/分析作业费用改写为零，或把基础设施耗时/存储称为免费。

原型 HTML 生成、多模态分析及付费搜索均纳入既有
[vendor-call admission 与预付费记账](../notes/prepaid-billing.md#admission-and-the-spending-bound)。
作业固定目录/能力配置与价格修订、官方推理档位、prompt/schema/adapter 版本、输入
文本/图片哈希、像素映射与遮挡版本；每次外发前重验权限、材料有效性和执行 attempt，
调用 `JobExecution.admit` 预占余额，再经 `accounted_call` 结算，业务代码不直连厂商。
重试、拆批及重新排队均消耗同一作业累计调用/金额上限，不能用 retry 清零。

必须扩展准入的计价输入以覆盖多模态：现有文本请求的 UTF-8 字节上界不能直接证明图片
token/计价上界。adapter 按实际发送图片数、尺寸、detail/切片方式及目录计价规则计算
保守输入额度，再加相同输出 token 上限；预检与实际 HTTP 请求使用同一组图片/输出限制。
把 base64 长度当作已验证图片 token 上界或只按提示词计费均不允许。付费搜索按每请求
或结果额度的配置价格计算调用上界，并接入同一 vendor_calls/UsageRecord/余额流水，
不伪装成 LLM token 或 OCR 页数。缺图片/搜索计价规则、售价或可靠上界时返回
`billing_price_unavailable` 或 `billing_bound_unavailable`，不外发、不假报零费用。

每次真实调用及重试记录 provider、实际 model、目录与价格身份、图片数、input/output
tokens（含厂商图片用量）、实际耗时、服务商成本 usd、平台 charge/币种，关联
org/task/job/run_id/call_id。图中文字读取由多模态模型完成，ocr_pages=0，不能重复按云
OCR 收费。模型记录和内容日志脱敏，不记录图片/base64、原始厂商错误或响应正文。
HTML/有效建议只留在受权业务记录，失败的任意输出不进入日志。

结算复用既有唯一 `(org_id, job_id, run_id, call_id)` 和 usage/余额流水约束，响应用量
先持久结算再解析；拒绝、截断、无效建议、取消后已发生调用照样记账。未知 usd 为 null；
缺失/无效用量、超时或不明结果保留 pending/unknown 预占待核对，不能释放余额后盲重试。
结算失败停止后续调用和成果发布；实际 charge 超预占按既有规则完整记账并失败。
不因采用预付费而声称任意图片端点天然满足文本端点的零超支证明。

缓存只能在同 org/task/抽取 job 和完全相同材料、目录/价格、规则及遮挡版本下命中。
命中不新增调用/扣费，回执区分缓存复用与原作业历史费用；不重置 UsageRecord。原型
还固定 HTML 和沙盒回执版本，重新生成不能借旧图片或旧 keep 决定；分析的新建议不能
覆盖人工修订。配置、图片、任务选择或遮挡版本变化后要求重新预检，不自动换模型。

原型生成只在 HTML、沙盒回执及完整截图关系均验证通过后发布候选；途中失败不发布
半份可入库结果，已发生模型费保留。分析按完整输入批次返回有效建议；后续厂商/额度
失败且已有验证通过项可发布 partial、stop_reason 与拒绝项，退出 5；没有有效项则失败。
取消、失去租约、权限/依赖变化或结算失败均不发布新建议。搜索可成功返回空候选，
不能因此生成厂家页面或证据。全部模型输出仍无人工确认字段。

dry-run 只列外发类型/ID/hash/数量、价格版本、首轮费用上界、余额/作业额度阻断项和
未知原因，零外部调用、零写入/预占；首轮估算不代表包含所有后续重试，真实调用逐次
重新准入。单位自带模型按 [provider-config.md](provider-config.md) 的后续批准范围处理，
本切片不重做模型目录、充值、配额或余额管理。

## 审计、失效与实施依赖

复用 append-only audit_logs，记录搜索与来源采集、原型生成/沙盒产物关联、分析提交/
建议产生与采纳、入库隐私放行、派生提交/发布、预览发放/实际读取、原型逐项/模块决定、
撤下、卡片链接变更和既有确认/组表/导出阻断事件。保留 org、actor_kind、user/token、对象/修订/
job/run/request ID、hash、profile、原因代码及时间。自由理由留在受权业务记录，日志
只存原因 hash；原型决定另记 keep/replace、keep_basis、前决定与清单哈希，厂家取证
记归档/内容哈希。日志不记录画面、原文、HTML、模型读字、遮挡值、页面 URL、登录信息
或签名链接。可追溯的厂家 URL/标题只在受权归档记录中。
本机未上传的 prepare/capture 不冒充服务端审计；入库时记录采集者声明和实际入库人。
成功业务与审计同事务；失败、冲突与拒绝不能生成成功确认或放行审计。

资源库新增修订不改变任务快照；显式替换任务选择、撤下资产、重开卡片或引用失效会使
依赖的 Evidence/初稿不可消费，旧确认记录保留。新图和追加遮挡不自动替换任何已确认
Evidence；须人重开、换链接、重新确认。原文件缺失或完整性失败应明确报损坏，不从
浏览器重新抓图冒充旧版本。撤下须阻止尚未完成的 job 和已签发链接，不能只从列表隐藏。

拟实施依赖顺序：本草案及沙盒契约批准、维护者同步规则 → schema/新表/复合约束/RLS/
角色关口 → 本机隐私处理与厂家归档 → 模型能力和图片/搜索计价准入 → HTML 生成及沙盒
交接 → 多模态建议/持久派生 → 图片 Evidence/draft → 模块原型决定及 B11 正式拒绝清单
集成 → 下节端到端验收。能独立的真实截图路径可先做，沙盒未批准不视为原型链完成。
迁移保留历史、不用破坏性 downgrade 作回退。集成时核对实际 schema、命令注册及
另一草案的最终接口，不预占并行切片的迁移编号，不假定另一 checkout 已有文件或代码。

## 批准后的端到端验收

以下是未来实施验收条件，不是本次已经运行的功能测试。使用真实 API、PostgreSQL
受限角色、对象存储和 worker，贯通本地/远程 CLI；测试材料明确标记为合成，外部
Provider 使用假实现，不能把合成材料或假调用当作真实取证效果证明。

1. **真实来源链**：从两单位的任务、已成功抽取 job 和固定功能选择开始，分别准备
   用户截图、实际本机测试页面、已有图示和固定证书页，经人工隐私放行入库、预览、
   建卡、技术/商务负责人确认、draft。实际输出图可解码，来源/修订/页码/hash 可追踪；
   本机时间与服务端接收时间分开，证书 Archive 始终未确认。独立的获准真实材料验收
   再核对运行界面与固定软件版本，不以测试夹具证明产品功能真实。
2. **存储前隐私**：页面及图片包含可辨识的合成姓名、证件号、社保信息和凭据哨兵。
   先遮挡再输出/上传；检查实际对象、缓存、临时目录、缩略图、EXIF、回执、日志与错误
   均无哨兵。未提供隐私检查、upload hash 不符、令牌伪造 reviewed_by 均失败；只看原图
   未看脱敏成品不能完成放行步骤。证书旧原件仍按原权限独立存在，不误报已删除。
3. **裁剪与无标签原型**：同图两种区域，经真实 Rust 处理，框外未遮挡内容像素不变，
   坐标能回溯；同输入/计划/profile 得到相同字节。原型多次派生、预览、缩略图、本机
   PNG 均无水印/footer/可见原型标签，内部 provenance 始终保留。不得用类型变更清掉
   origin；真实证书/厂家图的原有标记不能消失，图示不能变为功能观察证据。
4. **卡片关口**：真实截图先作为未确认候选，draft 保留缺口、退出 5；对应人查看精确
   派生图并确认后才进入行内证据清单。跨专业 admin、token、agent、worker 不能确认
   或重开；隐私放行不能代替证据确认。API/CLI/运行 DB 角色伪填确认人、旧图 hash、
   非匹配材料用途均失败。原型可以正常确认并进入 draft，无导出决定不制造 draft 缺口；
   功能状态不自动变 implemented，原型不能作为厂家参数、报告或证书证明。
5. **双单位与原材料权限**：对每张新表逐一以 A/B/缺上下文验证 SELECT/INSERT/UPDATE/
   DELETE 边界，对每个新路由以及卡片、初稿、job、签名实际读取交叉访问。A 不能引用
   B 的功能/source/review/rendition、厂家 archive、prototype_run、analysis/suggestion、
   decision/batch，也不能在同单位拼接不同 task/job 的关系；API 无权和不存在一致 404。
   模块预检/提交、HTML/厂家归档下载、分析作业和正式导出决定外键都覆盖；成员/令牌
   失效或缺原材料权限不能借图片/作业预览绕过。缺 org/actor 上下文的 SQL 同样拒绝。
6. **不可变与失效**：归档后修改功能库，任务旧选择不变；显式替换选择后原 Evidence
   失效，选回旧版本不复活。新裁剪不覆盖旧图；撤下后旧签名和待完成 job 被阻止，旧
   初稿为 stale。重新入库/建 Evidence/逐卡确认才恢复可消费状态，历史审计可重放。
7. **浏览器与文件失败**：真实本机 Playwright 遇登录页、页面不符、超时、取消时没有
   成功图片；会话凭据、profile、内网完整 URL 不离开本机。畸形图片、解码炸弹、矩形
   越界、水印后超限、断流、hash 不符、恶意跳转、已有输出与 symlink 均失败，不留下
   半份最终 PNG/回执，也不改用户原件；缺 Rust 不静默换绘图实现。厂家跳转到私网、
   非网页协议、恶意归档路径或图片与归档不同采集版本均明确失败。
8. **并发、作业与费用**：同幂等请求重复返回同记录，不重复对象/审计；不同请求内容
   复用 key 产生冲突。处理中撤下/换选择、旧 attempt 迟到、对象写成后 DB 失败均无
   可消费半成品。dry-run 零写入/零出站/零预占；取证/Rust/人工决定/组表/导出零模型
   用量，原型生成/分析/付费搜索每次调用均有准入与结算。以假厂商计量覆盖并发余额、
   图片收费上界、缺图片价格、预算不足、有限重试、截断/拒绝、未知用量保留预占、
   结算失败停止、取消后记账、同 call 幂等、缓存不重复扣费；旧起草费用不得被覆盖。
9. **厂家网页与白皮书**：从搜索候选到本机网页/PDF 截图、归档、人工放行、参数卡确认
   和初稿。固定 URL、时间、标题、内容/归档哈希及 PDF 页码，网页改版后仍能核验旧
   副本；型号不符、只有搜索摘要或 URL 声明不能通过确认。模型不得生成厂家内容。
10. **单页原型闭环**：假 LLM 经实际 provider/admission 路径返回合成单页 HTML，通过
    sandbox.md 的真实实施接口渲染截图，再本机脱敏、人工入库、建卡、确认、draft。
    核对模型身份、HTML/hash、沙盒回执与 PNG 的完整链；沙盒拒绝、HTML 变化或来源
    不匹配不发布 Evidence。不能用预存 PNG 假装完成 HTML 渲染，也不以假调用宣称
    已验证实际厂商能力；获准真实 GLM/DeepSeek 验收另用公开或合成材料。
11. **多模态外发与建议**：在 HTTP 边界捕获实际请求，逐字节核对只包含本机脱敏派生图
    和所选脱敏文本，不含原图、PDF、HTML、签名 URL、Cookie 或合成隐私哨兵。分别
    验证要求匹配、区域建议、读字；未知 ref/坐标越界/错读保留拒绝与待核对状态，模型
    不能设置任何人工字段。人工纠正后才确认 Evidence，既有文本 quote 校验不被旁路。
12. **正式与审阅导出矩阵**：原型 Evidence 已确认但 undecided 时，正式预检/prepare
    返回 prototype_decision_required、码 2，review_copy 可实际发布/下载、码 5。
    keep 两种依据分别成功；replace 未完成阻断正式输出，换入真实图并重审组表才解除。
    证据未确认、stale、坏引用/坏文件两种模式仍按原规则拒绝。模块批次遗漏/混入无权
    项/并发变化整批回滚；同图多 Evidence 不得共享一个未覆盖其他项的决定。token/
    agent/worker 在 API/CLI/DB 均不能决定，bidder 发布不能代填专业 keep。
    正式候选保存/release/下载前改变决定、卡片、选择或图/HTML 哈希，旧 run 和签名
    必须失效；只改变原型决定不使有效 review_copy 失效。
13. **文档无标签与大任务工件**：核对 draft 的可见呈现，实际生成并解包 review_copy 和
    final_section DOCX，核对媒体 PNG 哈希、正文/图注/附录/替代文本无原型标识或模型名，
    同时审阅件的既有“不得提交”标记可见，内部 provenance 和决定可追溯。以合成的
    约 217 张图片/图示和 125 个扫描页关系跑同一链路，
    验证游标分页稳定、逐张处理有界、失败可按原 ID 重试，不把整份材料同时解码入内存。
    数量用于工作量回归，不代表自动核验 663 页。两种 CLI 的七键/全部退出码/schema
    一致；正式文件只含已确认且原型门禁通过的精确脱敏图，token 永远不能导出。B11
    尚未集成则报告这项未完成，不以 worker 状态或计划替代实际文件验收。

每次端到端验收在独立 `artifacts/` 或临时产物目录生成可复核、可重跑的工件：脱敏的
命令与参数、合成输入 ID/hash、HTML/沙盒回执、厂家归档、PNG、计划/映射 JSON、
原型逐项/模块决定、脱敏外发清单、实际 CLI Result、DOCX、审计/用量摘要、
断言结果及重跑说明。工件不进入 docs/，不包含真实个人数据、原始 URL 或凭据；仅有
计划、schema 快照或测试跳过不能算完成。此文档任务只运行 Markdown 检查。

## 明确不在本切片内

- 编造厂家网页、证书、检测报告、合同、业绩、人员材料、运行截图，或修改截图内容使
  不满足的功能看起来满足；没有取到证据时如实保留缺口。
- 模型生成厂家网页、报告或证书，图像补全/改字把不满足参数变成满足；直接文生图
  充作运行截图。原型生成仅为单页 HTML → 指定沙盒 → 截图，不在此定义沙盒实现。
- 自动判定功能实现或参数满足、模型读字自动成为逐字引用、模型代人确认或决定保留；
  独立云 OCR 接入及全库图像外发不在本切片，多模态辅助读取图中文字在范围内。
- SaaS 访问客户内网、托管客户登录会话、自动登录/业务表单操作、多页面项目自动开发。
- 投标函/授权函制作、业绩与团队材料管理、完整技术方案生成、将整份 663 页中标文件
  拆解导入为证据库、整本 Word/PDF 排版或新导出格式。响应章节图片附件和原型门禁按
  本文与 export.md 集成，其余目录页码/附件排版沿用 B11，不另起渲染系统。
- 政府或其他电子投标平台集成、电子签章、批量盖章、加密上传与投标提交。
- 新建全功能看板、跨任务图库、跨单位复用、模型配置/余额管理重做、永久存储保留及
  密钥轮换、未经另行批准的历史敏感文件删除或数据迁移。
- 批量证据确认、默认确认、agent 代确认、赋予 API token 确认/原型决定或导出权限。
  已确认 Evidence 的原型保留/替换允许按模块批量决定，不改变逐卡证据确认；在本草案阶段
  实现代码、测试、迁移或修改其它文件。

## 已定决定

以下各项均按推荐方案批准。

原型生成及无水印/无标签、可作 Evidence、正式导出前逐项或按模块决定、厂家网页/PDF
取证、GLM/DeepSeek 多模态辅助、本机脱敏图外发和既有准入计费已按产品决定纳入，
不再列为待选。以下仅保留实施边界未定项，每项给出选项与推荐；它们不允许取消上述决定。

| 决定 | 选项与推荐 |
| --- | --- |
| 图片进入服务器前的隐私关口 | **推荐 A：本机遮挡并由人查看，按精确 hash 放行上传**，如本文；B：先上传加密隔离原图用于内部保管，另定原图权限、留存与销毁。两案发送厂商的图片都必须是本机脱敏派生图，B 不放宽这条约束 |
| 谁能放行入库与撤下 | **推荐 A：admin/bidder/technical 的人类会话**，专业证据确认仍按原职责；B：仅 admin，管理集中但大量截图处理会增加等待。两者均不授予 token/agent/worker |
| 与 annotation.md 的关系 | **推荐 A：本草案取代旧的本机副本交付范围，共用一个 Rust 引擎与 screenshot 命令族**；B：先实现旧草案独立本机命令，再另做归档链，需要明确双入口的维护与 profile 转换 |
| 谁作原型保留/替换决定 | **推荐 A：沿卡片专业职责，由对应负责人决定，软件功能为 technical，bidder 负责 export 发布**；B：由人类 bidder 统一作交付承诺决定，需单独批准该决策范围，仍不能跨专业确认证据。两案均支持模块批量并逐项留痕 |
| 图片证据是否必须另有本地 OCR 引文 | **推荐 A：多模态读字仅辅助，区域观察 + 人工逐图核对即可**；B：另要求可核验本地 OCR 引文，须增加坐标、误识别纠正及逐字验证契约。两案都保留已定的多模态读字能力，不能将模型读字自动当 quote |
| 厂家搜索服务与来源确认 | **推荐 A：平台配置一个 SearchProvider，按固定产品来源与人工核对筛选官网/PDF，价格齐备后付费调用**；B：平台维护厂家域名白名单并限定搜索范围，仍由人核对型号与页内容。具体搜索供应商及请求单价在接入前确定，不硬编码到业务层 |
| 首版能否脱离任务收集截图 | **推荐 A：绑定明确任务和成功抽取 job**，直接复用 Job 的真实 Document 关系与卡片关口；B：建立通用图库，须另定版本选择及无招标文档的作业归属，不能把 Job 约束随意放宽 |


## Phase A 实施记录

| 范围 | 状态与入口 |
| --- | --- |
| 截图/图示上传、固定证书页脱敏、本机 PNG/JPEG 准备 | 已实施；[`screenshot_contracts.py`](../../server/app/schemas/screenshot_contracts.py)、[`screenshots.py`](../../server/app/services/screenshots.py)；实际 Rust 构建与像素验收仍须在具备工具链的环境执行 |
| 同一 Rust 引擎的遮挡、裁剪、区域框、无标签原型 profile | 已实施；[`stamp/src/main.rs`](../../stamp/src/main.rs)；无 Python 绘图替代，缺二进制明确失败 |
| 精确哈希隐私放行、加密保存、签名预览、撤下与审计 | 已实施；[`API`](../../server/app/api/screenshots.py)；人工入库不等于证据确认 |
| image_region 卡片、逐卡确认、draft 图片依赖与失效重算 | 已实施；[`response_cards.py`](../../server/app/services/response_cards.py)、[`drafts.py`](../../server/app/services/drafts.py) |
| 原型逐项/模块 keep/replace 决定、前版本与精确集合门禁 | 已实施；[`prototype_decisions.py`](../../server/app/services/prototype_decisions.py)；正式导出经 `prototype_gate` 消费 |
| 匹配要求、区域建议、读字及调用准入/计费 | 已实施；[`screenshot_vision.py`](../../server/app/providers/screenshot_vision.py)、[`screenshot_jobs.py`](../../server/app/services/screenshot_jobs.py)；只发送已放行派生图，未调用真实厂商 |
| PostgreSQL FORCE RLS 与数据库门禁 | 迁移和两单位测试已实施；[`0023`](../../server/migrations/versions/0023_screenshots.py) 接在沙箱迁移 `0022` 之后 |
| 本机浏览器运行页采集入口 `bid screenshot capture` | 已确认归入 Phase B，未实施；待沙盒分支合并后接入统一回执、脱敏与归档交接，保持客户端本机执行边界 |
| 厂家搜索候选入口 `bid evidence search` | 已确认归入 Phase B，未实施；与沙盒及厂家网页/PDF 采集管线一起接入，候选仍不直接成为 Evidence |
| HTML 原型生成/渲染与厂家网页/PDF 采集 | Phase B，未实施；待沙盒分支合并后接入。已预留生成、回执、搜索候选与厂家归档的单位内关系，无客户端伪造生成记录的 API |
| 正式导出图片消费、决定检查接线和 DOCX 实物验收 | 已实施；见 [human-section-exports.md](../notes/human-section-exports.md)。端到端测试依赖 Rust 渲染器，CI 构建后运行 |

落实的接口决定：

- 对外契约升为 `2.0`，保留旧 Evidence 输入分支。新分支、材料枚举及 nullable quote 不伪装为旧严格客户端的兼容改动。
- 模块预检与提交增加可选 `evidence_ids` 显式子集，用于同功能被多卡引用时的逐项决定；未指定时仍必须覆盖列明功能集合中的全部有效原型 Evidence。
- 模块批次保存固定 target manifest，延迟数据库触发器核对每一项目标及数量，不能提交半批决定。
- 平台选中推理档位的 request_options 中使用服务端 `screenshot_vision` 能力与图片计价规则；未配置可靠规则时拒绝调用，不按 base64 大小推算图片 token。
- 多模态图像每调用一张，整次显式选择的 PNG 合计限制为 40 MiB；按需拆分请求，不隐式裁图或降采样。模型区域在持久化前转换为图片内容区坐标。
- 分析的要求文本始终使用既有遮挡规则；关闭普通模型起草的文本遮挡不会放宽本图片分析入口。
- 本机与 worker 使用相同受限 Rust 管道，工具与构建产物保存在当前 worktree 的 data/work；数据库、Rust 工具链和导出集成分别验收。

实现机制集中在[截图证据笔记](../notes/screenshot-evidence.md)。验证工件保存在 data/work，未写入 docs。
