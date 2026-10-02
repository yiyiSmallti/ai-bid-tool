---
kind: plan
status: "部分已实施"
---

# 契约：人工导出响应章节 Word

状态：**部分已实施**。对应[路线图](roadmap.md#覆盖矩阵解析要求证据响应与校验)
B11，依赖 R01、B07、B08。批准范围按[已定决定](#已定决定)执行，交付与待集成边界如下。

| 部分 | 状态与边界 |
| --- | --- |
| 人类模板绑定、预检、prepare → worker 候选 → release → 下载、正式件/审阅件、证书页附件、加密、审计、限额与哈希 | **已实施**；机制和代码入口见 [human-section-exports.md](../notes/human-section-exports.md) |
| `prototype_decision_required`、`prototype_replacement_pending`、`prototype_decision_stale` | **未实施，待合并截图流**；单一接入函数为 [exports.py](../../server/app/services/exports.py) 的 `collect_additional_refusal_issues`，保留指向 [screenshots.md](screenshots.md) 的 TODO |
| PostgreSQL 迁移、RLS、并发和完整 API/processor 验收 | 已编写端到端关口测试；本沙箱无法连接 PostgreSQL 测试实例，须由维护者运行，不能视为已通过 |
| Word/WPS 视觉分页及隔离 S3 完整下载验收 | 保留为验收项；DOCX 解包/重开和合成工件验证不能替代此项 |

## 目标与边界

把一个明确选定的偏离表初稿及其已确认 Evidence，套入本单位固定修订的 Word 模板，
导出可编辑的 `.docx` 响应章节。保留实质性响应一览表、商务响应偏离表、技术响应偏离表、
须遵守条款清单、缺口清单及证据附件索引；响应文字、偏离和说明逐字复制，不调用模型改写。

人工确认、三类输出的完整覆盖及失效规则以
[ADR 0005](../adr/0005-human-confirmed-responses.md)为契约，已交付的卡片与组表机制见
[response-cards.md](../notes/response-cards.md)。`DraftView.status` 仍为 `draft`，
不假设存在“已确认初稿”状态：本契约的人工导出决定绑定整份初稿的输入哈希，
其响应行和 Evidence 必须先各自满足上游确认关口，`comply_only` 则须有有效人工处置。
导出决定不能代替逐卡确认，也不改变上游状态。

模型起草和外发遮挡由上述契约单独定义。B11 只消费明确的 DraftView、固定卡片修订和
Evidence，不依赖另一 checkout 的文件、未交付命令或模型作业内部结构。导出不新增模型、
OCR、搜索、网页取证或外发遮挡；确认后的原文与真实附件在受权下载的文件中保留。

产品背景是一份 663 页的真实中标 PDF：投标函及授权表、三类响应表，约 305 页逐项技术
响应、约 217 张截图或图示、约 125 页证照或检测报告扫描件，以及业绩合同/中标通知、
团队社保/证书、164 页技术方案和几乎逐页的签章。这是规模与边界输入，不是本契约已读取、
可复制或可作为测试数据的材料。本切片交付其中的响应章节与已具备确认链的证书页附件，
不承诺复刻整本中标文件，也不把扫描图片称为可编辑文字。

硬规则引用 [agent.md](../../agent.md#硬性规则任何情况下都不得违反)。文件约束分别沿用
[版本化模板](../notes/versioned-templates.md)、
[版本化证书原件](../notes/versioned-certificate-files.md)及
[单位隔离](../notes/tenant-isolation.md)。模板上传、文件留存和人员确认均不等于系统认证
原件真伪、所有招标义务已覆盖或响应已满足招标要求。

## 人类身份与发布关口

建议仅有效单位的有效人类 `bidder` 成员具有 `export`；`admin` 维护模板适配，
不自动获得投标发布职责，`technical/viewer` 可沿用现有权限审阅初稿。角色决定见
[已定决定](#已定决定)。操作同时要求 `task:read`、`draft:read`、`card:read`、
`template:read`，以及初稿实际引用的材料读取权限；证书页须同时具备
`evidence:source:read`、`certificate:read`、`certificate:file:read`。

路由、服务、数据库均检查可信认证上下文：`actor_kind=session`、`token_id=null`、
用户/单位/成员有效且当前角色允许。所有导出预检、提交、发布、历史读取、下载链接和
文件下载入口均适用。内置 agent 即使由人发起也保留 agent 身份，不能冒充 session。
API token 不能申请或存入 `export`、`evidence:confirm`；旧令牌不扩权，不能借
`job:read`、`template:read` 或原件下载权限读取导出候选、导出件或签名链接。

为兼容[后台作业](../notes/background-jobs.md)且不授予 worker 导出权，建议分两步：

1. **人工准备**：人预检后以 `expected_input_hash` 和逐项警示确认提交固定输入。
   服务写入不可变 export run，再派发 `export_render` 作业。worker 只能读取该 run
   所列材料并产生私有渲染候选，不能写入正式导出记录、填写发布人或获取下载链接。
2. **人工发布**：worker 完成仅表示 `awaiting_release`。人用候选哈希提交 release，
   服务重新核验全部身份、输入及消费条件，才创建不可变导出件与成功审计。CLI `--wait`
   只等到候选就绪，不自动调用 release；没有 worker 定时发布或凭保存的人类身份发布。

可信服务提供 actor 上下文，调用方不得提交 `org_id`、actor、发布人或发布时间。
数据库关口拒绝 token/agent/worker 直接插入发布记录、改写状态或设置人工字段；
运行 DB 角色不能改关口或历史。任意 SQL 执行者伪造可信认证上下文不属于受支持的认证
入口，沿用 response-cards 的信任边界，不能宣称 RLS 可识别盗用的人类会话。

## 导出门禁与文档内容

预检、提交、worker 取材、候选保存、人工发布、下载时都核对各自适用的关口。
初稿锁定的抽取 job、招标 Document、全部 Requirement、卡片修订、人工处置、引文哈希、
Evidence、任务材料选择及模板选择必须同单位、同任务且一致；不隐式选择最新初稿或模板。
旧抽取 job 可显式选择并警示，覆盖只针对该 job 的已保存要求。

| 条件 | `final_section` 正式响应章节 | `review_copy` 审阅件 |
| --- | --- | --- |
| 初稿 `validity=stale`、卡片需重新确认、任务选择被替换 | 拒绝；回上游重审、重新组表 | 同样拒绝，不把过期响应加水印后放行 |
| 任一要求引用失效，包括须遵守或缺口条目的引用 | 拒绝，报告要求 ID 和原因 | 同样拒绝，不伪造位置或引用 |
| gaps 非空，引用本身有效 | 拒绝，`export_gaps_present` | 允许明确缺口清单，全文标“审阅件·存在缺口·不得提交”，完成码 5 |
| 未确认、驳回、待补材料的卡片 | 作为上游 gap，故阻止正式输出 | 只输出要求原文、位置和缺口原因；不带候选响应、候选 Evidence、截图或材料摘录 |
| 响应行链接未确认 Evidence、确认记录不完整 | 硬拒绝，属于不合法输入 | 同样拒绝；不得仅删掉 Evidence 或改成承诺 |
| 原件/模板/页图哈希、长度不符或文件缺失 | 硬拒绝，不输出半份文件 | 同样拒绝，不用占位图或空白附件降级 |
| 有效的已确认负偏离 | 如实输出并要求确认警示，不因此自动拒绝或变为 partial | 同样如实输出 |
| 提取遗漏、被拒条目、历史抽取、声明材料或材料义务警示 | 显式展示并绑定人工警示确认；不宣称全文齐备 | 同样展示，不用审阅模式消除警示 |
| 缺模板绑定、锚点/样式不支持、附件或资源超限 | 拒绝并给出明确原因 | 同样拒绝，不悄悄换模板、删页或降画质 |
| 引用原型的 Evidence 已确认，保留或替换尚未决定 | 拒绝，`prototype_decision_required` | 允许，决定未完成本身不构成拒绝 |
| 原型决定为替换，尚未换成经确认的真实截图 | 拒绝，`prototype_replacement_pending` | 允许仍有效且已确认的原图 |
| 原型决定绑定的 Evidence、卡片修订、图片或 HTML 哈希已变化 | 拒绝，`prototype_decision_stale`，需重新决定 | 决定失效本身不阻止；材料或卡片失效按上文拒绝 |

原型决定规则见 [screenshots.md](screenshots.md#正式导出前的原型决定)，原型门禁原因只在内部预检和审阅工作区显示，不写进初稿、图注或导出文档。

`review_copy` 即使没有缺口仍标“审阅件·不得提交”，完成码 5，不能在下载时改名变成正式
章节；正式输出需重新按 `final_section` 提交。没有确认行的审阅件也只能列有效的须遵守
决定和缺口；不制造默认满足行。`final_section` 的“正式”仅指章节导出条件满足，
不表示整本投标文件定稿或合规通过。

每个要求在三表之一、须遵守清单或缺口中恰好出现一次；保持上游原文顺序和三表归属，
不按包号、类别或偏离值做隐式过滤，不跨抽取 job 合并。三表即使为空也保留表头和空表说明。
每行含原始类别/★、招标原文与真实位置、响应种类/文字、偏离、具体差异和证据索引。
“负偏离”必须有可见文字，不能只靠颜色，不能变为“无偏离”或藏在批注/修订里。
承诺标“承诺”，Evidence 为空；声明标“声明”，没有原件时不生成虚构的附件编号。

须遵守清单保留原文、位置及人工处置，不变成材料响应；无缺口时缺口区明确显示“本次
抽取范围内无缺口”。PDF 引文保留来源页码，Word 引文使用真实结构位置且 `page=null`。
输出附件通过稳定编号和书签引用，不把 Word 重排后的页码当作招标原文页码。

对每次 gate 生成稳定 `issue_id`，绑定原因代码、受影响对象及固定修订。提交须精确确认
所有 `acknowledge` 级别的 issue，不能发送一个宽泛的 `force=true` 或“忽略全部”。
`block` 级别不可确认放行。警示确认仅确认本次导出范围，不补写上游确认、真实性或材料。

## 模板固定、占位符与章节映射

输入必须给出当前有效 `task_template_id`，服务解析其固定 `template_revision_id`、原件
SHA-256 和包号。不得接受资源 current 指针、外部 URL 或任意磁盘路径。模板资源新增修订
不影响旧选择；任务显式替换后旧 run 失效，选回旧模板也须新的选择和导出决定。
一个 run 仅使用一个模板选择；包号只用于识别模板选择，不自动缩小初稿的要求范围。

新增不可变 `export_template_bindings`，由人类 admin 在 `template:write` 权限下维护，
绑定精确模板修订及原件哈希；修改映射追加新 ID，旧绑定留存。模板本身仍按既有接口上传
新修订，B11 不覆盖模板原件、不自动推断章节、不假定声明的 chapters 已适配。

建议首版采用正文独立段落占位符，映射固定六个区域：

| section | 必须且仅出现一次的锚点 | 固定内容 |
| --- | --- | --- |
| `substantive` | `{{bid.substantive}}` | 实质性响应一览表 |
| `commercial` | `{{bid.commercial}}` | 商务响应偏离表 |
| `technical` | `{{bid.technical}}` | 技术响应偏离表 |
| `comply_only` | `{{bid.comply_only}}` | 须遵守条款清单 |
| `gaps` | `{{bid.gaps}}` | 缺口清单或范围内无缺口说明 |
| `evidence_appendix` | `{{bid.evidence_appendix}}` | 证据索引、声明摘录及证书页附件 |

锚点顺序固定为上表顺序，不能缺失、重复、嵌套或放入文本框、页眉页脚、表格单元格。
同一段落内跨 run 分割的完整标记允许按可见段落文本识别，段落不能含其他文字。
`{{bid.task_name}}`、`{{bid.tender_number}}` 是可选元数据标记，只能从固定 Task 快照填充，
使用的字段缺值则拒绝；不接受通用表达式、脚本、任意对象路径或自由业务文本参数。
未知标记一律报错。元数据不能充当响应或资格证明。

每个映射指定模板内真实的标题/表格样式 ID；三表还指定列顺序和宽度比例。
列固定为 `ordinal`、`tender_clause`、`source_location`、`response`、`deviation`、
`deviation_note`、`evidence`，必须各出现一次，宽度为正且总和 100%。允许顺序和宽度适配，
不能删除字段。`response` 包含响应种类，`tender_clause` 包含类别及 ★。
其他三区域使用固定字段，不支持任意脚本化行模板。标题、缺口警示和负偏离字样由渲染器
保证可见，不能被空样式、隐藏字体或模板条件吞掉。

模板适配验证除既有上传校验外，还须限制为支持的 OOXML 子集：保留页面尺寸、页边距、
横竖方向、分页/分节及批准的样式；拒绝宏、OLE、外链资源/域、altChunk、批注、修订、
隐藏正文及不支持的文本框。页码域仅允许 PAGE/NUMPAGES，不执行域或访问外部资源。
静态正文仅允许登记的章节标题、空白布局和页眉页脚格式，不能夹带示例响应、已填业务表、
旧项目名称、证书页或未经确认的图片。首版不接收含嵌入媒体的模板；企业标志等扩展另议。
绑定时由 admin 核对静态内容及模板哈希；这项适配决定不是 Evidence 确认。

预检列出六个锚点的段落/章节定位、样式解析、静态内容摘要哈希及不支持项。缺失或不支持
必须修订模板或映射，不自行删除内容修补。Word 表格使用真实段落与单元格，重复表头、
固定列宽，保留长响应换行；扫描页作为图片。可编辑不意味着任意复杂模板均可无损往返。
不承诺自动目录的最终页码、固定整本页数或不同 Word/WPS 字体环境下的相同分页。

## 证据索引与 PDF 页附件

只从初稿响应行的固定卡片修订遍历确认后的 Evidence。资源声明的字段摘录、精确选择与
修订进入证据索引，仍标明声明性质；不能据产品 URL 获取新截图，也不能把功能声明绘成
已实现界面。未链接的证书、整个资源库和任意补充上传均不进入输出。

证书页必须沿 Evidence → evidence_source → task_certificate → certificate_revision →
certificate_file 解析，核对 `confirmed_by/at`、`quote_check=human_page_review`、
选择有效性、原件哈希/页码及已归档 PNG 哈希。来源 Archive 仍保持其永久未确认属性；
消费资格来自 Evidence，不修改 Archive 的确认字段。

建议按响应首次引用顺序建立附件编号 `E001` 等，对同一固定选择、修订、原件哈希、页码和
PNG 哈希的页面去重；同页多个 Evidence 的摘录、确认记录及引用行仍分别可追溯。
原件中的其他页不因某一页确认而自动入选，多页附件逐页要求确认。缺失任何必要页不输出
半份附件。整本原 PDF 不作为 OLE、隐藏文件或额外附件塞入 DOCX。

每个已确认页在证据附录新起一页，按比例放入页面可用区域，页外加编号、材料性质、
证书修订、原件页码、SHA-256 和对应要求标识；标题/脚注不得覆盖原图。复用既有
`pdf-page-preview-v1` 的整页 150 dpi RGB PNG，嵌入前后 PNG 字节哈希一致。
仅改变 Word 中显示尺寸，不重采样、不裁剪、不补字、不去除已有水印、不改证书像素。
照片/扫描文字仍为不可编辑图片，表格、标题、索引和响应文字可编辑。

每项索引保留 Evidence ID、确认人/时间、选择/修订、性质、字段摘录或原件页码和哈希，
不包含存储路径、服务凭据或签名链接。正文使用附件编号与书签，不猜测最终 Word 页码。
首版不提供图片内联/附录二选一开关；替代排版方案须另行批准并进入输入哈希。

纯扫描页没有可核验文本时，上游可能只有 source preview，不能因 export 需要就直接
确认或附入。此类页须待独立的可追溯 OCR/人工转录确认契约；B11 不补这个关口。
背景材料中的检测报告、业绩合同、社保证明或截图，只有已由受支持来源和确认链表示的
页面才能消费；其余保持人工编排或后续材料类型扩展。

## 数据模型与数据库约束

下列每张业务表均须 `org_id NOT NULL`、`UNIQUE(org_id,id)`、ENABLE/FORCE RLS；
运行角色无表所有权、SUPERUSER、BYPASSRLS、TRUNCATE、改删历史权限。缺单位上下文
不可读写。新表、策略、约束与双单位隔离验收在未来同次实施，不以事后补测试替代。

| 新表 | 字段、归属和写入边界 |
| --- | --- |
| `export_template_bindings` | `template_revision_id, template_sha256, binding_hash, sections, static_content_hash, adapter_version, reviewed_by/at`；同修订/映射哈希唯一；只追加，人类 admin 创建 |
| `export_runs` | `task_id, extraction_job_id, document_id, draft_run_id, task_template_id, binding_id, render_job_id, mode, input_hash, manifest, issue_snapshot, acknowledged_issue_ids, initiated_by/at`；固定 manifest 不可改；人类 exporter 创建，任务/选择/初稿不得换绑 |
| `export_run_items` | `run_id, response_item_id, requirement_id, card_revision_id?, kind, ordinal`；run/requirement 唯一；覆盖该初稿全部 row/comply_only/gap，内容仍引用不可变 response_items，禁用无外键的 JSON 冒充条目 |
| `export_run_evidence` | `run_id, run_item_id, card_revision_id, evidence_id, attachment_ordinal?`；只允许 row 的确认链接；页附件从 Evidence 的类型化外键链解析，不另设无约束 resource_id；同条目/Evidence 唯一 |
| `export_render_candidates` | `run_id, render_job_id, attempt_id, input_hash, plaintext_sha256, size_bytes, object_key, renderer_profile, manifest_hash, created_at`；仅绑定的 worker 在有效 attempt 内追加一条；私有暂存，不是导出件，不提供文件读取入口 |
| `exports` | `run_id, candidate_id, task_id, mode, input_hash, manifest_hash, file_sha256, size_bytes, media_type, object_key, released_by/at`；一个 run 最多一个不可变发布结果；仅受权人类创建，禁止 worker 插入及运行角色改删 |

所有父子关系使用包含 `org_id` 的复合外键，必要时补父键唯一约束。人引用
`(memberships.org_id,user_id)`；run 同时约束 task/document/extraction/draft 的完整链，
模板选择必须属于该 task 且 binding 指向同一修订。条目、卡片和 Evidence 必须同 task、
job、requirement/card；同单位错误父项也拒绝，不能只防跨单位。

导出表的读取策略在单位 RLS 之外再限制 actor：`exports` 仅当前受权人类可 SELECT，
token/agent/worker 不能直接读取文件描述符；run、条目、证据链接和候选仅人类 exporter
可查，worker 另有严格绑定当前 run/attempt 的内部取材策略。binding 可由人类 admin/
bidder 读取，worker 只能读取被该 run 固定的 binding。worker 的窄策略不能成为通用
`job:read` 权限，也不能获得 exports SELECT；缺 actor 上下文同样无可读记录。

数据库即时 actor 关口与延迟完整性约束至少保证：

1. 新建 run 和 exports 必须是有效人类 exporter；binding 必须是人类 admin。
   `export` 仍不在 token 白名单内，直接 SQL 写入禁止范围失败。API、服务和数据库的
   非人类拒绝一致，不能凭 `released_by` 外键有效便当成人类操作。
2. 提交和发布的 row 必须指向当前已确认、有效的响应修订，evidence 类全部链接已确认，
   commitment 恰为零链接；comply_only 为有效人工决定。final_section 没有 gap。
   非 row 不得挂 Evidence；完整集合与 response_items 一致，不少行、不多行、不混 job。
3. candidate 必须绑定当前运行的 export_render job、run 和 attempt，不能换输入哈希；
   worker 的内部入口只授予这些写入，不能修改 run、确认字段、exports 或人类审计身份。
   通用作业查询不暴露候选对象路径/文件；通用 job 结果不是下载旁路。
4. 发布必须引用完整、成功且未失效的 candidate，模式、manifest、hash、文件描述符匹配；
   取消、失败、旧 attempt、未确认或缺上下文的发布事务失败。服务验证文件字节及引用文本，
   数据库负责身份、状态、关系及完整性，不声称 SQL 能验证 DOCX 排版或原件真伪。
5. 锁序沿用任务 → 按 ID 排序的卡片 → 关联记录，再锁 run/job；发布时重读全部依赖。
   任务材料/模板替换、卡片重开、引用修复、成员停用与发布必须有一致的串行化结果。
   文件 I/O 和渲染在长事务外完成，最终事务复核快照，变化则拒绝且不写成功审计。

manifest 是受 RLS 保护的固定事实清单，至少含每条要求/引用哈希、条目分类/顺序、
卡片/处置修订及确认信息、Evidence 与全部材料选择/修订/文件哈希、模板/绑定、模式、
任务元数据、完整警示及确认集合、组表规则/适配/渲染版本。规范化关联表是权限与外键依据，
manifest 必须由这些行产生并校验一致，不能仅持有一个不可检查的总哈希。

## Pydantic 契约

以下为批准的接口字段；可执行字段验证以
[export_contracts.py](../../server/app/schemas/export_contracts.py) 为准。复用
[`Contract、Cost、Result`](../../server/app/schemas/contracts.py)和上游 DraftView/Source，
统一 `extra=forbid`，不另建顶层 Result、不冻结当前契约版本号。

```python
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ExportMode = Literal["final_section", "review_copy"]
SectionKind = Literal["substantive", "commercial", "technical", "comply_only",
                      "gaps", "evidence_appendix"]
ColumnKind = Literal["ordinal", "tender_clause", "source_location", "response",
                     "deviation", "deviation_note", "evidence"]

class ExportColumn(Contract):
    key: ColumnKind
    width_percent: Decimal = Field(gt=0, le=100)

class ExportSectionBinding(Contract):
    section: SectionKind
    heading_style_id: str = Field(min_length=1, max_length=200)
    table_style_id: str = Field(min_length=1, max_length=200)
    columns: list[ExportColumn] = Field(default_factory=list, max_length=7)

class ExportBindingCreate(Contract):
    template_revision_id: UUID
    expected_template_sha256: Sha256
    sections: list[ExportSectionBinding] = Field(min_length=6, max_length=6)
    expected_static_content_hash: Sha256 | None = None
    dry_run: bool = False

class ExportBindingView(Contract):
    id: UUID
    org_id: UUID
    template_revision_id: UUID
    template_sha256: Sha256
    binding_hash: Sha256
    static_content_hash: Sha256
    adapter_version: str
    sections: list[ExportSectionBinding]
    reviewed_by: UUID
    reviewed_at: datetime

class ExportIssue(Contract):
    issue_id: Sha256
    code: str = Field(min_length=1, max_length=100)
    severity: Literal["block", "acknowledge"]
    requirement_ids: list[UUID] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)

class ExportPrepare(Contract):
    draft_id: UUID
    task_template_id: UUID
    binding_id: UUID
    mode: ExportMode
    expected_input_hash: Sha256 | None = None
    acknowledged_issue_ids: list[Sha256] = Field(default_factory=list)
    dry_run: bool = False
    retry: bool = False

class ExportPreview(Contract):
    dry_run: Literal[True] = True
    task_id: UUID
    draft_id: UUID
    mode: ExportMode
    input_hash: Sha256
    ready: bool
    requirement_count: int = Field(ge=0)
    table_rows: dict[Literal["substantive", "commercial", "technical"], int]
    comply_only_count: int = Field(ge=0)
    gap_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    attachment_pages: int = Field(ge=0)
    issues: list[ExportIssue]
    estimated_output_bytes: int | None = Field(default=None, ge=0)
    estimated_duration_ms: int | None = Field(default=None, ge=0)
    estimated_cost: Cost

class ExportRunView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    draft_id: UUID
    render_job_id: UUID
    mode: ExportMode
    input_hash: Sha256
    state: Literal["queued", "rendering", "awaiting_release", "released",
                   "failed", "cancelled", "invalidated"]
    candidate_sha256: Sha256 | None
    export_id: UUID | None
    issues: list[ExportIssue]

class ExportRelease(Contract):
    expected_input_hash: Sha256
    expected_candidate_sha256: Sha256

class ExportFile(Contract):
    name: str = Field(min_length=1, max_length=200)
    sha256: Sha256
    size_bytes: int = Field(gt=0)
    media_type: Literal[
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ] = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

class ExportView(Contract):
    id: UUID
    org_id: UUID
    task_id: UUID
    run_id: UUID
    draft_id: UUID
    task_template_id: UUID
    template_revision_id: UUID
    binding_id: UUID
    mode: ExportMode
    completion: Literal["complete", "partial"]
    validity: Literal["current", "stale"]
    input_hash: Sha256
    manifest_hash: Sha256
    file: ExportFile
    released_by: UUID
    released_at: datetime
    issues: list[ExportIssue]
    invalidated_requirement_ids: list[UUID]

class ExportDownloadLink(Contract):
    export_id: UUID
    file: ExportFile
    url: str
    expires_in: Literal[300] = 300

class ExportDownloadReceipt(Contract):
    export_id: UUID
    output_path: str
    file: ExportFile
```

字段间验证及返回补充：

- 六个 section 无重复且顺序固定；三表 columns 恰为七列，其余 section 的 columns 为空。
  样式必须真实存在且类型匹配，字符串非空白，时间带时区，哈希只接受小写 SHA-256。
  binding dry-run 的 data 返回模板哈希、静态内容哈希、锚点定位、适配版本及 issues，
  不伪造 binding ID/审阅人；创建时必须提交匹配的静态内容哈希。
- prepare 非 dry-run 必须有 `expected_input_hash`，确认 issue 集合无重复且精确匹配
  预检要求；dry-run 不接受 retry 或确认集合。预检哈希只固定事实/issue 集合，不把
  客户端确认动作纳入循环计算；完整 manifest 另含确认集合并生成 `manifest_hash`。
- `ready` 表示没有 block，仍需逐项人工确认 acknowledge。计数非负，三表键齐全，
  row/comply_only/gap 计数之和等于 requirement_count；输出 bytes/耗时未知为 null。
- run.state 从 job、candidate、exports 及实时失效信息派生，不开放状态更新接口；
  candidate_sha256 仅在候选就绪或已发布时非空，export_id 仅在存在发布记录时非空。
- `completion=partial` 当且仅当 mode=review_copy；final_section 必须零 gap。
  `validity` 和影响项读取时重算，不重写历史文件。模板变更等非要求原因通过 issues 返回。
- file 名称必须为安全 `.docx` 名，不含分隔符/控制字符；大小另受下文限额约束。
  URL 必须是同服务固定相对路由。输入不能指定确认字段、存储键、自由文字/证据、
  SHA 以外的文件副本或绕过 gate 的开关。两项 expected hash 均由服务重算验证。

## CLI、API 与 Result

命令均支持 `--json`，缺参数直接失败，无交互式默认确认；本地和远程模式经过同一服务、
PostgreSQL RLS 与身份关口。`--input FILE` 内容为上述模型，dry-run/retry 由相应 CLI
选项设置，不允许两处冲突值。`--wait` 是客户端等待参数，不写入输入 manifest。

| CLI | API | 输入与输出 |
| --- | --- | --- |
| `bid export binding create --input FILE [--dry-run]` | `POST /export-template-bindings` | ExportBindingCreate → 适配预检 / ExportBindingView；仅人类 admin |
| `bid export binding list --template-revision V` | `GET /export-template-bindings?template_revision_id=V` | 同修订不可变绑定列表；人类 admin/bidder，仍需 template:read |
| `bid export prepare --task T --input FILE [--dry-run] [--retry] [--wait]` | `POST /tasks/{T}/export-runs` | ExportPrepare → ExportPreview / ExportRunView；只能人类 exporter |
| `bid export run show --id R` | `GET /export-runs/{R}` | ExportRunView；包含就绪与失效信息，无候选下载 |
| `bid export release --run R --input FILE` | `POST /export-runs/{R}/release` | ExportRelease → ExportView；只能人类 exporter |
| `bid export list --task T` | `GET /tasks/{T}/exports` | 不可变发布历史摘要及实时 validity |
| `bid export show --id E` | `GET /exports/{E}` | ExportView；不能据历史 completion 推断可下载 |
| `bid export download --id E --output NEW.docx` | `GET /exports/{E}/download-link`，随后 `GET /exports/{E}/download?signature=...` | ExportDownloadLink → 验证后的 ExportDownloadReceipt |
| `bid job status/wait/cancel J` | 复用既有 `/jobs/{J}`、`/jobs/{J}/cancel` | export_render 作业额外要求人类 export 权限；不向 token/agent 泄露候选或正文 |

binding 只属于模板适配维护，不赋予 admin 导出材料的能力；所有其他表中命令按 export
关口校验。取消只取消未发布的渲染；不能用 job cancel 撤销或抹除已有发布历史。
API 成功受理不是导出成功，发布成功也不是文件已经下载到本机。

Result 顶层严格保留 **`ok、command、data、items、warnings、cost、duration_ms`** 七键。
详情和写操作的 data 为模型回执、items=[]；列表 items 为各项、data 为过滤范围。
失败使用 data.error，含稳定 code 及必要对象标识，不回显输入、文件正文或凭据。
预检阻断项和审阅件问题放 data.issues，warnings 只放脱敏原因代码/标识。
文件下载成功路由返回二进制流，其下载链接及 CLI 完成回执仍为 Result；错误在流开始前
返回 Result，传输中断由 CLI 报失败，不把半文件当 JSON 或成功文件。

| 退出码 | 语义与示例 |
| --- | --- |
| 0 | 无阻断的预检、受理、绑定操作、历史查询、候选就绪；或正式章节发布/完整下载成功。候选就绪明确 `state=awaiting_release` 且无 export_id |
| 2 | 缺参、非法输入、预期哈希冲突、缺警示确认、final_section 有缺口、stale 初稿、无效引用、模板不支持、超限、输出路径已存在；修正输入后重提 |
| 3 | 可重试网络/存储/队列故障、渲染超时；提交后固定输入发生变化用 `export_input_changed`，需重读预检并人工重提，不自动改绑 |
| 4 | 身份/角色失败、token/agent/worker 发布、无权对象、文件损坏、内部关系/确认完整性破坏或不可重试渲染失败 |
| 5 | 明确请求的 review_copy 成功发布或成功下载；回执仍 `ok=false`、`completion=partial`，含有效输出和全部问题，不表示有半份损坏 DOCX |

错误与部分成功 `ok=false`，其余 `ok=true`；HTTP 401/403 保持认证/动作权限语义，
资源不存在、跨单位或无权访问统一 404。输入问题 400/422、修订/输入冲突 409、临时故障
503；各自对应上表退出码。dry-run 有 block 返回 2 并带 ExportPreview；单纯读取历史
仍为 0，通过 mode/completion/validity 说明文件状态。`job status/wait` 只报告渲染状态，
review_copy 候选就绪也为 0，最终发布/下载才使用 5，避免把渲染成功误认成人工发布。
不可重试的协议内异常须归入 Result/退出码 4，不允许本地 traceback/退出码 1 作为出口。

实施时新增命令及 Pydantic Schema 进入 `bid schema`，已有命令结构保持；确有不兼容
变化按 [CLI 契约](../../agent.md#硬性规则任何情况下都不得违反)升版本。

## 作业、费用与资源限额

新增 `jobs.kind=export_render`，document_id 使用初稿所选抽取 job 的真实招标 Document，
不拿证书代替、不放宽为空。run/job 同事务固定输入后再派发；派发失败保留可重试作业。
恢复单位上下文、重新核验发起人成员/读取/导出角色，使用既有 lease、run_id、取消及
有限重试规则。worker 不继承 session，仅有受 run/attempt 约束的渲染能力；内部签名或
作业参数不是人类发布授权。

渲染接口建议为内部 `ExportRenderer.render(manifest, authorized_inputs) -> candidate`，
输入由服务解析的受权模板和页面组成，不接收外部 URL 或调用方路径，不是可调用模型的
Provider。沿用 python-docx、PyMuPDF 及标准 ZIP 工具；无新增大依赖、云转换服务或
真实厂商调用。工作进程可终止，deadline/取消后终止并回收自有渲染进程，不能留下继续
写对象的超时线程。媒体逐页读取/校验并暂存，不把全部 PDF 或扫描图一次解码入内存。

建议上限由服务端固定 profile 管理：2,000 个要求、300 个唯一附件页、512 MiB 输出 DOCX、
1 GiB 解包体积、1 GiB 渲染进程内存、15 分钟作业 deadline；部署配置可下调。单模板、
原件和 PNG 继续适用各自已有更严格限制，不能借导出扩大上传范围。300 页指附件页面，
不是承诺总 Word 页数；217 张尚无支持来源的截图不算作已支持能力。
实际序列化超限须失败，不静默减页、压低分辨率或改为不完整正式件。阈值按推荐方案批准。

dry-run 完成相同授权、引用、清单、文件完整性及模板映射检查，返回三表/须遵守/缺口/
负偏离/附件计数、所有 gate 和可确定的估算；不排队、不落业务/审计/用量行、不写对象。
不渲染整份结果，因此输出字节/耗时无法可靠估算时为 null，不能把总页数写成已知事实。

导出不调用模型/OCR，实际及预估模型 `Cost` 均为 `llm_tokens=0, ocr_pages=0, usd=0`，
不新建空 UsageRecord、不扣模型余额、不受模型余额不足阻挡。记录实际渲染耗时和存储
字节，不能把它们当“基础设施免费”承诺；导出收费、存储配额计费不在本切片。
上游模型起草费用留在原作业，不因后续导出零调用而被清零。

## 文件存储、下载与历史

候选和正式件都通过现有 Storage 加密后保存，路径分别限定为
`org/{org_id}/export-candidates/{run_id}/{attempt_id}/{sha256}.docx` 和
`org/{org_id}/exports/{export_id}/{sha256}.docx`，对象不可覆盖。明文字节哈希用于验证，
加密绑定完整 org/object key；不同密文随机数不影响明文 DOCX 的 hash。
暂存文件只在私有目录，权限 0700/0600，完成或失败清理本次临时文件。

发布服务读取并验证 candidate，把相同明文经 Storage 写入正式键后，在短事务内再次核验
并提交 exports 与审计；不能直接把未经验证的候选 descriptor 当成发布。DB 与对象存储
不是分布式事务，失败可留加密无引用对象，不能有可下载的半份发布记录。
自动垃圾回收、历史删除和密钥轮换不在本切片，不以删除旧材料回滚。

下载签名沿用现有 300 秒有效期，绑定 org、export_id、file_sha256 和专用 kind；取得链接
及实际下载均要求当前人类身份、成员、角色、原材料权限与实时 gate。旧链接不绕过成员
停用、卡片重开、模板/材料替换或失效。服务先核验解密字节长度/哈希再发送。
历史记录和原文件保持不可变，但 `validity=stale` 时拒绝重新签发或下载，包括已发签名。
不提供 `--allow-stale` 旁路；已经下载的本机副本不能远程撤回。

CLI 仅接收匹配的同服务相对下载路由，不跟随跳转；限制流长度、核验 SHA-256 和 DOCX
类型，私有临时文件 fsync 后原子写入新的 0600 输出路径。拒绝覆盖已有文件、symlink
及路径替换竞态；失败只清本次临时文件。签名 URL、密钥和服务端路径不进入普通日志，
API 不提供永久公开地址。本地模式同样用 PostgreSQL、org 前缀和加密存储，不能直读对象
绕过权限；文件下载到本机后在 Word 中编辑不会反向修改服务器留存的发布件。

历史列表关联 task、draft、模板选择/修订、binding、输入/清单/文件哈希、mode、发布人/
时间及当前失效原因。重复 release 在权限和 gate 仍通过时返回同一个 export；不增成功
发布审计。下载链接请求、服务端文件交付尝试另记审计，服务端发送完成不能证明本机保存
完成，CLI 只有哈希核验落盘后才报告下载成功。

## 可复现性与审计

同单位相同固定输入、模式、映射和 renderer_profile 应生成相同的**明文 DOCX SHA-256**。
input_hash 由规范 JSON 的 UTF-8 字节计算，含显式默认值、固定字段顺序规则、稳定数组
顺序、UTC 时间和小写哈希；JSON 使用 sort_keys、紧凑分隔符、ensure_ascii=true、无尾
换行。manifest_hash 另绑定全部人工警示确认，重现数据来自实际固定清单而非重新查 current。

渲染固定 ZIP 条目顺序/时间戳/压缩参数、OOXML 属性序列化、书签/关系/媒体编号、核心
属性及渲染器依赖版本；清除运行机器用户名等不相关元数据。文档不嵌入 job/attempt/export
随机 ID、墙上时钟或发布人时间；这些留在历史和审计，证据本身的确认人/时间是固定输入，
继续留在索引。文件名不参与 DOCX 内容哈希，mode 和可见审阅标记必须参与。

缓存仅单位内使用：render 输入键包含上述全部依赖与 profile，run 幂等键再包含发起人，
避免一个人的失效授权被另一人继承。相同提交复用自己的 run/job，失败/取消必须显式 retry；
内容已变先重新预检，不能用 retry 更新旧 manifest。不同人或不同 run 可生成同字节，
仍须独立取得人工发布授权。资源库新增未被任务选择的修订不改变哈希。

跨渲染器/依赖版本、不同模板或排版配置不承诺字节一致，新 profile 必须改变输入键并保留
旧档。同 profile 重跑字节不一致视为 `export_nondeterministic`，停止发布且保留原产物；
不能只比较解包文本便声称文件哈希一致。Word/WPS 打开后重存、更新目录或人改文档都会
改变文件哈希，不算服务端复现失败。不保证异环境屏幕排版或打印页数相同。

复用 audit_logs，追加 `export.binding_created`、`export.prepared`、
`export.render_completed/failed/cancelled`、`export.released`、
`export.download_link_issued`、`export.download_served` 等事件。记录真实 actor_kind、
用户/令牌/作业标识、单位、task/draft/binding/run/export ID、输入/文件哈希、模式、
原因代码和关联 ID；worker 渲染事件不能记作人工发布。业务成功与对应审计同事务。
权限拒绝、冲突及失败不得留下成功事件；必要的拒绝事件仅用脱敏安全元数据。

审计、普通日志、错误、用量记录不复制招标正文、响应/材料摘录、报价、身份证号、银行
账号、文件内容、凭据或签名 URL。正文只在受权初稿、固定业务清单及加密文件中留存；
不可通过放宽日志来补充审计。发布日志记录 gate 结果和警示 ID，不记录敏感处理文字。

## 批准后的端到端验收

验收顺序：契约/schema 与身份边界 → 迁移/数据库关口 → 模板适配 → 固定清单与渲染
worker → 人工发布/下载 → 下列端到端检查。代码实施状态不等于这些验收条件全部通过；
数据库、隔离 S3 和 Word/WPS 的未完成验收范围见本文开头。验证日志和合成工件仅放在
工作树的 `data/work/`，不写入文档目录。

1. **真实入口闭环**：在隔离环境用两个人类职责账号从上传合成招标、固定资源、逐卡确认、
   draft、模板绑定、prepare、真实 worker、release 到 download 跑通本地和远程 CLI/API。
   Word/WPS 打开产物，三表文字/单元格可编辑，证据是独立图片，三类全集覆盖和原文顺序
   与初稿一致；重开下载的 DOCX 检查正文/媒体关系，不能只以 HTTP 200 判定成功。
2. **人工身份及 SQL 关口**：bidder 成功，admin/technical/viewer 按角色建议拒绝发布；
   token、agent、worker 分别尝试提交、release、下载及通用 job 旁路均失败。用运行 DB
   角色在真实事务中伪填发布人、写 exports、token 禁止范围或缺 actor 上下文也失败。
   worker 只能留下候选，不能自发发布；`--wait` 后没有 exports 行或可用下载链接。
3. **两单位和关系完整性**：为 A/B 建全链路，逐一覆盖每张新表、每个新路由、候选、历史、
   签名下载、作业与对象前缀；A 访问 B 和未知资源均 404，缺 org 上下文无行可读写。
   同单位不同 task/job/card/模板修订混绑及跨单位复合 FK 插入全部失败，不只检查列表。
4. **导出门禁矩阵**：缺口、无效引用、未确认 Evidence、stale draft、未处理警示逐一从
   实际接口进入；正式件拒绝，review_copy 仅允许规则规定的缺口并有持续可见标记，码 5。
   解包文件确认没有未确认候选文字/媒体；全须遵守、全缺口和有效负偏离的结果各自正确，
   负偏离在表中明示，不能因正式模式消失。无效输入不写 exports 或成功发布审计。
5. **模板绑定和内容泄漏**：上传两个不同修订及映射，检查固定旧选择不追随 current；
   测试缺/重复/错位标记、跨 run 标记、未知样式、七列遗漏、静态业务内容、隐藏文字、
   修订、外链、OLE 和嵌入图片。支持者正确渲染，不支持者明确拒绝且无外部访问。
6. **证书附件**：一份合成多页 PDF 只确认指定页，产物只含这些页；同页多 Evidence 去重
   图片但保留所有引用与确认记录。逐个提取 DOCX 媒体核对源 PNG 哈希，核对原件 SHA、
   页码、整页边缘、旋转、图外标签及书签；未确认页、纯扫描候选、无文件修订不能混入。
   没有整份 PDF/OLE、猜测页码、占位扫描件或去水印。材料损坏两模式都失败。
7. **并发与撤销授权**：分别在排队、渲染、候选保存、发布提交、链接签发后替换选择、
   重开卡片、修复引文或停用成员；旧 run/签名不能放行，重新选择旧资源不恢复旧授权。
   同时发布仅一条 exports/成功审计，旧 attempt/取消后不能落盘或覆盖新 attempt。
8. **存储与下载**：在本地加密存储和隔离 S3 兼容存储走完整下载，校验密文不含明文、
   复制跨 org 对象失败、300 秒失效、错误 kind/hash、断流、重定向、已有路径、symlink
   和落盘竞态。仅完整核验后出现 0600 最终文件；失败保留旧文件，无部分成功下载。
9. **幂等、复现与费用**：相同输入重复请求和同 profile 独立重渲染得到同明文 SHA；
   改模式/映射/确认修订使输入变化，升级 profile 不复用旧缓存。dry-run 零业务写入，
   export 全链零 Provider 调用、零 UsageRecord/模型扣费，历史起草费用不变。
10. **规模和失败工件**：用标记为合成的长响应材料与 125 页以上已确认页附件测试分页、
    目录/书签、七列表格及人工可读性，再覆盖批准的附件/字节/内存/deadline 上限。
    超限或存储/DB 提交失败无发布记录、无静默删页；不以 663 页真实投标文件冒充已授权
    测试集。CI 不调用真实外部服务，真实材料另经明确授权验证。
11. **契约与可重复交付物**：上述链路核验 Result 七键、0/2/3/4/5、schema、两模式一致。
    保存可重复运行说明、脱敏命令/JSON、输入清单和哈希、合成 `.docx`、提取媒体哈希及
    打开后核验记录到独立验收产物目录，不在 `docs/` 写日志、截图或证据文件；材料须明确
    标为测试合成，不包含真实投标正文、令牌、签名 URL。不能用计划或 worker 状态代替
    实际下载工件。

## 明确不在范围内

- 整本标书自动编纂、投标函/授权书生成、164 页技术方案起草、报价策略、自动承诺或
  响应改写、语义 check/score、缺失要求补录、多抽取作业/多文档/分包内容合并。
- 网页/厂家证据生成或取证、新截图/图示、原型生成、Rust 标注、OCR/人工转录链、新增
  合同/中标通知/社保等附件类型，以及把尚无确认链的材料直接包装为“已附证明”。
- 任意 Word 模板自动匹配、公共模板共享、DOCM、PDF 成品导出、原 PDF 整本拼接、
  最终页码保证、自动合规认证或盖章校验、多人会签、批量自动发布。
- 电子签章、手写签名、逐页盖章、骑缝章、政府电子投标客户端、加密投标包、上传/提交
  电子投标平台及任何平台 API 对接。导出后由人完成整本合并、排版核对、签章和客户端上传。
- Vue 看板、角色/成员管理、自动过期件重发、导出收费、历史删除/对象清理、密钥轮换、
  生产部署；Word 本机修改后的版本回传与再次审定另立契约。

## 已定决定

以下各项均按推荐方案批准。

| 决定 | 具体选项与推荐 |
| --- | --- |
| 缺口如何处理 | **推荐正式章节必须零缺口，另提供显式 review_copy**，只带有效确认内容和缺口元数据，永久标“不得提交”、码 5；备选首版所有缺口一律拒绝、不提供审阅件。stale、坏引用、未确认 Evidence 入行两案都拒绝 |
| 谁可导出 | **推荐仅人类 bidder**，对应投标专员发布职责；备选人类 admin + bidder。technical/viewer/token/agent/worker 两案都无导出权，维护模板不等于发布授权 |
| 人工与 worker 分工 | **推荐 prepare 渲染候选后单独人工 release**，服务和 DB 都禁止 worker 发布；备选首次人类请求等待渲染后仍在有效人类请求中完成发布，断线则需重新人工发起，不留后台自动发布路径 |
| 模板适配 | **推荐固定六个正文标记、显式列/样式绑定、受限无业务内容模板**，不兼容模板先上传新修订；备选另立契约支持任意现成表格/书签映射，增加模板结构与往返保真验收后再交付 |
| 证书页排版与清晰度 | **推荐复用已核验的 150 dpi 整页 PNG，附录逐页、重复页去重**，对当前确认链改动最小；备选先扩展更高分辨率的版本化页面渲染与 hash 契约再导出，或另立内联图排版。均不默认附整本 PDF |
| 历史失效件下载 | **推荐历史元数据可查、失效文件禁止重新下载，旧签名同样拒绝**；备选后续增加仅人类专职归档读取能力并单独设计醒目标识和权限，本切片不加 allow-stale |
| 首版规模上限 | **推荐 2,000 条要求、300 个唯一附件页、512 MiB DOCX、1 GiB 解包/进程内存、15 分钟 deadline**，合成大工件验收后采用；备选先限定 1,000 条/150 页/256 MiB/10 分钟，以更小范围完成首版。任一方案都不静默删减材料 |


## 实施时明确的细节

- 静态正文标题白名单取所选 `TemplateRevision.data.chapters` 的完整章节树，不新增
  可自由注入正文的请求字段；缺少可用锚点或样式时拒绝绑定。
- `input_hash` 固定事实、issue 集合与渲染 profile；`manifest_hash` 再绑定精确警示确认。
  run 幂等键额外包含发起人。声明仅有证据索引，只有确认证书页分配附件编号。
- 已发布 run 失效后返回 `state=invalidated`，保留已有 `export_id`，
  `candidate_sha256=null`；历史读取仍成功，签发和实际下载拒绝。
- CLI 下载先读取 ExportView，再取签名链接，核对同一文件描述符。审阅件下载回执用
  `ExportDownloadResult` 补充 mode/completion/validity/issues，不改变 Result 七个顶层键。
- `bid schema` 仅为新增 export 命令追加 output JSON Schema，既有命令定义不变。
- Linux 用进程地址空间上限，macOS 用父进程采样 RSS 后终止渲染进程；达到任何限额均失败，
  不删页或降采样。部署配置只允许下调批准上限。
- 已有材料表保留单位 RLS，worker 的逐材料读取收窄由受信任的固定输入加载服务执行；
  新导出表另有 run/attempt 级 worker RLS，不宣称旧材料表新增了该策略。
