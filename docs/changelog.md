---
kind: changelog
---

# 变更记录

按日期记录已交付的范围。每条范围的机制说明见[机制笔记](README.md#机制笔记)。

## 2026-10-04：控制台改用 Element Plus 重新设计

- 单位后台和平台后台改用 Element Plus（按需引入、中文语言包）：深色侧边导航、顶栏显示单位和中文角色名，
  任务页带流程步骤条，审阅页为统计卡片、筛选区、要求列表和固定在右侧的审阅详情，初稿按分区标签页展示，
  手机宽度下导航变为顶部横条、要求列表按卡片堆叠。浏览器标题按页面和后台区分，不再统一显示“平台后台”。
- 状态、职责、资格、偏离、作业和文档状态等代码统一显示中文；成员会遇到的服务端错误码和抽取预检提示显示中文
  说明，未收录的仍显示原文与代码。
- 确认、驳回、需补材料等操作改用确认对话框；“确认响应”不可用时在按钮下写明缺少的条件；打开卡片或切回页面时
  只有清除了已勾选的审阅项才提示重新勾选；材料面板不再提示网页成员使用命令行维护资源。
- 列表中的状态标签用样式实现，1,200 条要求的筛选与翻页仍满足浏览器门槛；e2e 用例改为按选项文字操作下拉框并
  点击确认对话框。

## 2026-10-04：起草不再把缺材料标为负偏离

- 起草提示词升为 `card-draft-v2`：负偏离只用于材料或承诺内容未达到要求；仅缺材料时保留证据类、偏离标无，
  正文只写需补哪类材料，不断言满足、不写材料中没有的型号参数或名单。交付期、工期、质保期、服务响应等投标人
  自行履行的义务按招标原值起草为承诺。此前缺材料的卡片会被记成负偏离，补齐材料后再起草会因
  `negative_deviation_weakened` 被跳过，需人工改写。

## 2026-10-03：导出件改为中标标书的表格版式

- 三张响应表改为“序号｜招标文件要求｜投标文件响应内容｜响应情况”四列，序号按表从 1 起；要求逐字照录原文，
  ★ 要求以 ★ 开头；响应末尾以可跳转的“（见附件 E003、声明 D001）”引用材料；响应情况写“响应”“响应且无负偏离”，
  偏离写“正偏离：/负偏离：”加具体差异，负偏离加粗。须遵守与缺口清单、证据附件索引和附件说明不再印内部标签、
  原文坐标、ID、确认人、时间戳或哈希；索引写材料名称、原件页与摘录和对应的表与序号。渲染 profile 升为
  `docx-export-v3`，导出清单升为 `human-export-manifest-v2` 并记录材料名称。
- 新增 `bid export provenance` / `GET /exports/{id}/provenance`：以与正文相同的编号返回确认人、时间、证据与资源
  修订、附件哈希等留痕。新增 `bid export template-sample`：写出 A4、宋体小四、黑体三号标题、页脚页码并放好
  六个锚点的起步模板，返回可直接使用的绑定。
- 绑定列改为 `ordinal`/`requirement`/`response`/`compliance`，对外契约升为 `3.0`。旧的七列绑定仍可列出并标
  `current: false`，预检以 `export_binding_outdated` 阻止，不再接受新建七列绑定。

## 2026-10-03：沙箱双单位与生命周期验收

- 新增 [sandbox_two_org_acceptance.py](../scripts/sandbox_two_org_acceptance.py)，在开发实例上以两个合成单位、
  真实 worker 与 gVisor 节点运行：B 对 A 的运行、任务列表、产物链接、作业状态与取消、在 A 任务上提交和 A 的签名
  链接得到与未知资源相同的 404；运行时数据库角色无单位上下文读不到任何沙箱行，B 上下文读不到也改不动 A 的行，
  越权插入分别被行级安全与复合外键拒绝（各有成功的对照）。运行中取消和运行中 SIGKILL worker 均不发布产物，
  容器在约 1.5 秒内回收，随后的渲染成功。租约过期接管与断连、存储失败注入仍待执行。

## 2026-10-03：导出件的 Word 版面验收

- 在 macOS Microsoft Word 中打开合成长响应正式件（150 条要求、130 页已确认证书附件）：三张响应表合计 150 行、
  文档未加保护、130 张独立内嵌图与 130 个书签一一对应。发现每份附件的说明独占一页、图片被挤到下一页；渲染器
  现在为说明预留两英寸并令其与图片同页，附件页数减半。渲染 profile 升为 `docx-export-v2`，旧缓存不复用。
- 新增可选的规模场景 `server/tests/test_export_scale.py` 与 `scripts/word_inspect.applescript`，步骤见
  [development.md](guides/development.md#check-an-export-in-word)。

## 2026-10-03：拆分 API 入口与合并版本化资源服务

- 产品、功能、证书、资质档案和模板共用 `services/versioned.py`：修订锁、修订冲突、任务选择替换与审计只有一份
  实现，各类以 `VersionedKind` 声明表、权限范围和审计动作；功能的产品校验和模板文件存储以钩子接入。
- `api/main.py` 只保留应用组装、中间件、错误结构和认证上下文；路由按领域拆到 `api/account.py`、
  `api/tenders.py`、`api/resources.py` 和 `api/jobs.py`，上传、解析/抽取提交、要求查询、作业状态和令牌签发移入
  `services/`。四处签名下载链接共用 `api/common.py`。接口、路由名和 OpenAPI 文档不变。

## 2026-10-03：沙箱代理攻防验收驱动

- 新增 [sandbox_proxy_acceptance.py](../scripts/sandbox_proxy_acceptance.py)：生产代码中的 `FetchBroker` 与
  `SocketBrowserProvider` 对回环上的真实 TLS 合成源运行，各目标统计实际收到的请求。覆盖恶意 query/path、
  userinfo、编码 IP、内网与元数据目标、混合 A/AAAA、DNS 重绑定、跨域与 HTTPS 降级跳转、无限跳转、
  不受信证书、Cookie、POST、压缩炸弹和超大 chunk，以及伪造 supervisor 直接发送的 fetch 帧；违规目标收到
  零请求，每次连接都使用校验过的地址。代理节点的内核级出网过滤与真实 DNS 不在其内。

## 2026-10-03：CI 用时与 main 分支保护

- CI 只在拉取请求和 main 上运行，分支推送不再重复跑一遍；同一拉取请求的新推送取消旧运行。只改 Markdown
  时两项必需检查照常报告成功但跳过测试与构建。缓存 uv 与 Cargo 下载及渲染器构建。
- main 设为受保护分支：只能经拉取请求合并，`python` 与 `web` 检查须通过，禁止强推与删除，管理员同样受限。

## 2026-10-03：独立令牌密钥与数据密钥轮换

- 会话、平台会话、密码设置链接和各类签名下载链接改用新的必填 `BID_TOKEN_KEY`，与 `BID_ENCRYPTION_KEY`、
  `BID_SECRETS_KEY` 及已退役的数据密钥都必须不同，否则启动失败。升级时须先配置该变量；部署后现有会话和
  链接全部失效一次。
- 数据密钥可轮换：`BID_ENCRYPTION_KEY_PREVIOUS` 中的退役密钥只用于解密，`python -m app.admin
  rotate-encryption` 逐单位把加密字段和存储对象改写到当前密钥，可重复执行。步骤见
  [development.md](guides/development.md#keys-and-storage)。
- 迁移 `0027` 删除 `api_tokens.encrypted_secret`：令牌只保存摘要，不再保存可解密的副本。回退迁移只恢复空列，
  已删除的密文不可恢复。

## 2026-10-03：切片 1 公开 PDF 验收与审查缺陷修复

- 用公开的硬件招标 PDF（德邦基金信创交换机项目招标文件）经 CLI 远程模式走完登录、建任务上传、解析、
  抽取和列出要求，平台默认模型完成抽取；入库要求的引用均逐字落在所引页，原文带 ★ 的行均有对应要求，
  引用不符的条目逐条拒绝并报告。切片 1 的完成标准已满足。
- 服务端未预期的异常返回 Result 结构的 `500 internal_error`（退出码 4），不含异常文本；本地模式得到同样
  的结果，CLI 自身的未预期异常也以 `internal_error` 和退出码 4 输出，不再打印 traceback、退出码 1。
- 登录时密码正确但不是该单位成员，与密码错误一样返回 `401 invalid_login`。
- PDF 解析只打开一次文件，逐页渲染并立即 OCR，内存中至多保留一页扫描图。
- 证书页来源存档在任务锁外读取原件和渲染，取锁后重新核对任务选择与重复存档；每个进程至多同时渲染两页，
  超时仍在运行的渲染占用名额直到结束，名额用尽返回可重试的 `source_render_busy`。
- 缺少 `BID_TEST_ADMIN_URL` 时数据库测试失败而不是跳过。

## 2026-10-03：自托管厂家来源搜索

- 新增 `bid evidence search`、`evidence candidates`、`evidence adopt`：预检显示只含厂家与型号的检索词及输入
  哈希，提交后由 worker 调用自托管 SearXNG，候选经抓取 URL 规范化过滤（去掉 HTTP、带凭据与歧义地址）、
  去重合并来源引擎，本单位产品库已记录的同厂家域名优先、PDF 其次，最多 20 条入库。搜索服务不可用时
  作业可重试且不保存结果。选定候选后写入产品新修订的 `official_url`/`whitepaper_url` 并改选到任务，
  过期的产品修订被拒绝。
- `deploy/docker-compose.yml` 新增按摘要固定的 SearXNG 服务（只在内部网络），配置见
  `deploy/searxng/settings.yml`；本地运行步骤见 [development.md](guides/development.md#run-vendor-search-locally)。
  机制见 [screenshot-evidence.md](notes/screenshot-evidence.md#vendor-search)。

## 2026-10-03：厂家网页缺失资源时仍可采集

- 厂家网页的入口文档加载成功即截图：失败资源以逐请求 `fetch_failed` 应答跳过，代理不再因单个资源被拒而
  关闭整次运行；等待 `load` 超出导航预算时也照常截图，报告前停止新请求并等待在途请求返回。产物标
  `vendor_resources_incomplete`。入口文档失败、策略变更或撤销仍整次失败；主资源取不到时报告
  `source_fetch_failed`。开放的开发策略不再受单位每分钟 60 次请求限制。
- 厂家归档记录是否不完整及被拒请求数，迁移 `0026` 要求两者与沙箱回执一致，并要求不完整采集的卡片确认
  复核 `vendor_capture_incomplete`。`VendorArchiveView` 增加对应字段，对外契约升为 `2.2`。
- 在真实 gVisor 节点上连续两次完成新华三产品页（139 个请求，3 个失败）与白皮书 PDF 的采集和入库。

## 2026-10-03：厂家网页采集并发转发与 gVisor 渲染修复

- 厂家采集的资源请求带顺序编号，最多 4 个同时在途；runner、supervisor 与调用方按编号匹配应答，
  supervisor 拒绝未知、重复或乱序编号。同一来源的两个连接租约占满时等待释放，不再立即拒绝。
- 按已批准的决定，runsc 下的厂家采集以 `--disable-seccomp-filter-sandbox` 启动 Chromium（保留命名空间
  沙箱），修复渲染进程在 gVisor 中因 `sched_getaffinity` 被拦截而崩溃；原型渲染不变。决定见
  [sandbox.md](plan/sandbox.md#已定决定)。镜像已重建并重新固定摘要。

## 2026-10-03：开发节点放开厂家取证网络与沙箱 runner 错误码

- 抓取策略新增仅限开发节点的 `open_public_https` 修订：节点设置 `BID_SANDBOX_DEV_OPEN_EGRESS=1` 时允许任意
  公网 HTTPS 地址，仍执行 URL 规范化、凭据参数拒绝、公网地址校验与字节预算；另接受本机 fake-IP 代理的
  `198.18.0.0/15` 应答。未设置该开关时策略文件整体无效。步骤见
  [sandbox-runtime.md](guides/sandbox-runtime.md#open-vendor-egress-on-a-development-node)。
- 修复沙箱 runner 遇到浏览器库异常时直接退出、被报告为 `invalid_frame`：导航超时、导航失败与其他未预期
  异常分别以 `source_timeout`、`source_navigation_failed`、`runner_unexpected_failure` 报告；厂家页面导航
  期限改为按运行预算计算。镜像已重建并重新固定摘要。
- 在真实 gVisor 节点上完成新华三官网白皮书 PDF 的采集、入库全链路。网页采集仍受逐个转发请求过慢和
  gVisor 下 Chromium 自身沙箱崩溃限制，见 [sandbox-execution.md](notes/sandbox-execution.md#pitfalls)。

## 2026-10-03：厂家网页与白皮书截图证据

- `screenshot prepare --sandbox-artifact` 下载沙箱 `vendor_capture` 运行的网页或 PDF 页图并按本机计划处理；
  `screenshot add` 以 `vendor_web`/`vendor_pdf` 来源入库，须复核归档哈希。服务端从运行与代理回执解析
  URL 哈希、最终来源、入口页标题、抓取时间与内容哈希，重放计划并重验页图与归档字节；网页须
  `archive=bundle`，同一运行多页共用一条归档记录。
- 迁移 `0025` 将厂家归档绑定到沙箱运行、归档产物与入口回执，并要求厂家图片是该次采集的页面；
  厂家图片卡片确认须复核 `vendor_model_scope`。`VendorSource` 改为只含页图产物 ID，删除未实施的
  `VendorCaptureInput`，对外契约升为 `2.1`。机制见
  [screenshot-evidence.md](notes/screenshot-evidence.md#vendor-captures)。

## 2026-10-03：模型生成 HTML 原型（ui mock）

- 新增 `bid ui mock` 与 `POST /tasks/{T}/prototype-generations`：固定一条要求与一个已选功能修订，
  只外发遮挡后的要求原文与功能声明，预检报价单次调用，付费运行须带预检哈希。worker 持租约调用模型生成
  单页 HTML，经离线沙箱渲染截图，HTML 与 PNG 加密保存并与沙箱回执一起写入原型记录；功能选择变化或沙箱
  不可用均在调用前阻断。
- 修复 gVisor 下大于 64 KiB 的沙箱产物偶发丢失 4 KiB（哈希校验报 `artifact_hash_mismatch`）：
  runner 改为按 `PIPE_BUF` 原子写出帧；以模型生成的 238 KiB 页面连续 10 次渲染验证。
- 新增 `GET /prototype-runs/{id}` 与源图读取，`screenshot prepare --prototype-run` 下载源图后按本机
  计划处理，再经人工入库成为 origin=prototype 资产。机制见
  [screenshot-evidence.md](notes/screenshot-evidence.md#prototype-generation)。

## 2026-10-03：沙箱运行时定为专用 VM 内 rootful Docker + runsc

- supervisor 预检改为：runsc 必须运行在 rootful 守护进程上且不得跳过 cgroup，rootless 仅用于 runc
  合成模式；不满足时以 `sandbox_runsc_cgroups_unenforced` 或 `sandbox_rootless_required` 拒绝接单。
- 开发 VM 切换到 runsc（带 `--oci-seccomp`）业务模式后，隔离验收子集全部通过：文件与秘密边界、
  零网络外发（含正向对照）、CPU 死循环与内存炸弹被限额终止、调用方断连与 supervisor 重启后的容器回收。
  驱动见 `scripts/sandbox_colima_acceptance.py`，决定理由见[沙箱契约](plan/sandbox.md#已定决定)。

## 2026-10-03：沙箱开发节点与真实管道修复

- 在 macOS Colima 专用 VM 上跑通真实沙箱管道：rootless Docker、固定摘要镜像、mTLS 控制通道、
  渲染容器与独立验证容器，合成 HTML 渲染为 PNG。节点准备脚本、systemd 服务与环境模板见
  `deploy/sandbox-node/`，步骤见[沙箱运行时指南](guides/sandbox-runtime.md)。
- 修复 `deploy/sandbox-seccomp.json` 缺少 `chroot`，导致 Chromium 自身沙箱无法启动；
  Chromium 启动失败现以固定代码 `browser_launch_failed` 报告，不再只显示帧错误。
- 实测 gVisor 在 rootless Docker 下无法施加 cgroup 限额，运行时组合列为待定决定。

## 2026-10-02：付费起草的预览绑定与扣款上限

- `CardGenerateRequest` 新增可选 `expected_input_hash` 与 `max_charge`：提交时核对预览哈希（覆盖输入、
  模型与价格修订），不一致以 `generation_input_changed` 拒绝且不建作业；作业累计平台扣费加下一次预留超过
  上限即停止准入，以 `spend_cap_reached` 保存部分结果。同键进行中作业的上限更高或缺失时以
  `generation_cap_conflict` 拒绝，重试按新上限约束累计扣费。预检回显上限并报告
  `spend_cap_below_first_call`。CLI 增加 `--expect-input-hash`、`--max-charge`。
- 单位后台启用付费起草：预检后填写扣款上限并勾选授权才能运行，提交携带预览哈希，作业状态在面板内跟踪。
  契约见 [drafting-binding.md](plan/drafting-binding.md)。

## 2026-10-02：导出图片证据与原型决定门禁

- 人工导出消费已确认的 `image_region` 证据：固定派生 PNG 字节与哈希作为附件嵌入，所有图片统一标为
  证据图片，文档不显示来源种类或原型性质。
- 正式件要求每项原型图片有当前有效的保留决定，否则以 `prototype_decision_required`、
  `prototype_replacement_pending` 或 `prototype_decision_stale` 拒绝；决定集合进入输入哈希，
  之后改变决定会让已发布导出失效。审阅件不依赖决定。迁移 `0024` 在导出证据行记录保留决定，
  并在完成关口核对。CI 构建 Rust 截图渲染器，使图片链路测试实际运行。机制见
  [human-section-exports.md](notes/human-section-exports.md)。

## 2026-10-02：单位模型、人工导出、单位后台、沙箱与截图证据

- 单位模型配置：单位自带模型与平台目录模型选择，独立密钥加密、不可变修订和
  `bid provider set/list/history/test`；抽取与卡片起草共用解析，自带密钥零平台扣费，仍逐次准入并
  记录用量，支持厂商额度提示与余额查询。迁移 `0020`，机制见
  [provider-config.md](notes/provider-config.md)。
- 人工导出：人类 bidder 按单位固定模板修订预检、prepare、release 和下载 Word 响应章节；正式件拒绝缺口，
  审阅件逐页标注不得提交；证书页附件保留原始字节，存储加密、签名下载、审计与限额。迁移 `0021`，
  机制见 [human-section-exports.md](notes/human-section-exports.md)。
- 单位后台：`web/` 新增招标任务、解析、官方推理档位抽取、千条响应卡审阅与按职责处置、起草费用预览
  和三表初稿页面；初稿读取改为批量加载。付费起草按钮等待预览绑定契约。机制见
  [org-console.md](notes/org-console.md)。
- 沙箱：HTML 原型离线渲染、精确允许名单的厂家网页与 PDF 采集、一次性容器与独立验证、资源预算、
  清理对账、加密产物与来源回执；原型不加可见标记，真实隔离运行时默认关闭。迁移 `0022`，机制见
  [sandbox-execution.md](notes/sandbox-execution.md)、[sandbox-fetch.md](notes/sandbox-fetch.md)，
  部署步骤见[沙箱运行时指南](guides/sandbox-runtime.md)。
- 截图证据 Phase A：本机 Rust 像素脱敏、裁剪与区域标注，人工按精确哈希放行入库，`image_region`
  响应证据进入卡片与初稿依赖，原型逐项保留或替换决定，经准入计费的多模态匹配、区域建议与读字。
  CLI 契约升为 `2.0`，保留旧 Evidence 输入分支。迁移 `0023`，机制见
  [screenshot-evidence.md](notes/screenshot-evidence.md)。

## 2026-10-02：调用预留、引用边界与星号判定修复

- 抽取、起草的两种 HTTP adapter 共用输出上限选项校验，运营模型目录禁止保存这些字段及别名；
  预留与起草预估统一取实际请求中最大的输出上限，避免较小别名造成少预留。接入缓存版本更新。
- 新增迁移 `0019`，让人工确认与组表使用和 Python 相同的原文区间、规范化及分段边界判定，
  修复 `3.5mm/5mm`、`内存/扩展内存` 的内部匹配误判；保留逐字引用要求，不改写历史数据。
- 结算连接池超时、不可恢复的数据库或记账错误先停止后续准入；写入未知状态再失败也保留停止状态
  和原预留，已发送调用继续结算，明确可恢复的 DBAPI 错误仍有限重试。
- 星号合并按引用在完整原文中的区间判断归属，不再用子串标星或遮蔽缺失星号段；后处理缓存版本更新。
- 增加 MockTransport 驱动的 API 与作业回归场景，覆盖目录校验、调用计费、引用修复到人工确认及组表、
  星号补入和 SQL/Python 一致性。机制见 [LLM 接入层](notes/llm-providers.md)、
  [预付计费](notes/prepaid-billing.md)、[Word 引用](notes/docx-citations.md)及
  [响应卡片](notes/response-cards.md)。

- 由 Codex 按第二轮挑战式复审实现，在真实数据库上验证并修正一处测试字段名；当时的完整回归：819 项通过。
## 2026-10-02：模型响应起草与外发遮挡

- 新增 `bid card generate`、对应 API 和后台作业：按指定抽取作业及要求生成 model/worker 草稿，
  提出处置建议、响应、偏离与候选引用；受保护卡片跳过，落盘复核版本，模型不能弱化已记录负偏离。
- 提交固定并加密保存输入正文；清单限定为已选要求原文/位置、任务固定资源可引用字段及证书来源页
  本地文本，公开预检只返回标识、哈希、遮挡状态、版本与命中数。默认遮挡金额、联系人/电话、
  身份证号、银行账号，仅单位人类 admin 能关闭；每次厂商调用前重新检查权限和设置修订。
- Anthropic 与 OpenAI 兼容 adapter 增加结构化起草，共用默认模型、官方 reasoning、逐次准入、
  用量结算和预付扣费；起草分批、截断/格式错误拆分及瞬时重试均计费，首轮计划参与调用上限。
  有效部分结果保留并返回退出码 5，未完成项和引用拒绝只报告标识及原因，不记录原始收发文本。
- 模型局部引用同时核验实际发送文本和固定原文；无有效引用仍为需补材料的 evidence 草稿，
  不自动转承诺。承诺多余引用丢弃并警示；候选证据和文字始终需要人工审阅确认。
- 迁移 `0018` 扩大起草记录的 adapter 目录标识字段，并补充模型输入依赖在人工确认、旧稿失效和
  组表时的数据库关口；不新增业务表，不改写历史。组表规则更新为 `response-draft-v3`。
- 注册命令与 CLI JSON 快照，Result 七键及版本保持 `1.2`。原批准契约转换为
  [ADR 0005](adr/0005-human-confirmed-responses.md)，机制见
  [响应卡片](notes/response-cards.md)、[模型外发与遮挡](notes/model-drafting-redaction.md)和
  [LLM 接入层](notes/llm-providers.md)，操作见 [CLI 指南](guides/cli.md#generate-model-response-proposals)。
- 凭据未配置的平台模型不再先检查余额，抽取与起草直接以 `provider_unavailable` 说明原因。
  由 Codex 实现，在真实数据库上验证并修正；当时的完整回归：768 项通过。

## 2026-10-02：要求引用精确原文与受控修复

- 抽取引用先按 NFKC、弯直引号和空白规范化定位，再保存唯一命中的原文连续片段；模型原始引文另存
  `model_quote`。无匹配、重复匹配和未知位置按条拒绝，其余已核验结果继续保存。
- 参数补漏不再把覆盖多个参数的整段引文当成逐项覆盖；★ 规则按分号和换行分段，只标记明确带星号的
  分段并补入缺失项。`gap_fill.remaining` 报告最终保存及规则补入后仍未覆盖的参数数。
- 抽取提示词保持 `req-v3`；新增后处理缓存版本 `exact-spans-v1`，HTTP adapter 更新为 v4，使所有服务商
  在引用语义变化后重新抽取。Result 契约版本保持 `1.2`。
- 新增仅单位人类 admin 可用的 `bid req repair-citations`：默认只读预览，执行需提交预览哈希和原因；
  范围、来源或当前卡片变化时报 `repair_preview_changed`，不能唯一定位的历史要求保持不变。审计只记
  要求、任务、作业、卡片标识及新旧引用、模型引文和操作原因的哈希，不记录这些原始文本。
- 迁移 `0017` 增加可空的 `requirements.model_quote` 与卡片修订引用哈希，不改写既有修订历史。
  修复后引用哈希变化的卡片派生 `needs_reconfirmation`，仅遵守项需重新人工处置；
  `response-draft-v2` 组表将其列为缺口，读取旧初稿时重新计算有效性并保留原快照。机制见
  [LLM 抽取](notes/llm-providers.md)、[Word 引用](notes/docx-citations.md)和
  [响应卡片](notes/response-cards.md)，操作见[CLI 指南](guides/cli.md#repair-legacy-requirement-citations)。
- 同一位置出现多处规范化匹配时，选择两侧以文本边界、空白或列表标点分隔的那一处，避免 `5mm插孔`
  与 `3.5mm插孔`、`内存` 与 `扩展内存` 互相误判。由 Codex 实现，在真实数据库上验证并修正；
  当时的完整回归：727 项通过。

## 2026-10-02：模型调用预算、即时记账与作业租约

- 新增共享作业执行上下文；提取的首轮、拆分、补漏和重试均在调用前检查 attempt 归属、
  取消状态、累计调用上限和平台费用上限，按单位事务预占调用费用，避免并发重复使用余额。
- 迁移 `0016` 增加强制单位隔离的调用记录和 usage 幂等关联。收到有效用量后立即将记录、
  扣款、账目和累计费用同事务提交；记账重试不重复扣款，旧 attempt 的迟到费用仍保留。
  超限的提取作业失败且不保存半份要求，已有用量不丢弃。
- 作业按 `run_id` 续租，长请求期间继续心跳；取消、租约失效或接管阻止旧 attempt 继续调用。
  普通取消等待已发出的调用记账，未知结果的预占留待对账。
- 增加基于 API、processor 与 `httpx.MockTransport` 的预算、并发、取消、记账重试、心跳、
  接管及租户隔离场景。费用上界与恢复限制见[预付费机制](notes/prepaid-billing.md)，
  参数见[开发指南](guides/development.md#configure-job-guards)。
- 每个作业的调用次数上限随首轮批次数增长（`BID_JOB_VENDOR_CALLS_PER_BATCH`，默认每批 4 次，不低于
  `BID_JOB_MAX_VENDOR_CALLS`），大型招标文件不会在首轮中途被截停。由 Codex 实现，在真实数据库上验证；
  当时的完整回归：717 项通过。
## 2026-10-02：统一密码登录限速与 TOTP 原子消费

- 单位列表查询、单位登录和平台登录共用归一化账号的失败计数；复用现有审计表及
  PostgreSQL 事务锁，补充来源限制，未知、停用及未设置密码账号沿用统一失败响应。
- 密码校验使用独立线程池、有限等待队列和跨 API 进程的计算槽位；饱和或等待超时
  返回可重试错误，取消请求后仍保留执行中的容量并完成失败记账。
- 平台 TOTP 在同一账号锁及事务内读取并消费计数，提交成功记录后才签发会话，
  防止并发请求复用验证码。不新增迁移或依赖。
- 规则和边界见 [平台认证机制](notes/platform-console.md#password-admission-and-totp-consumption)，
  重试步骤见 [开发指南](guides/development.md#run-the-platform-console)。

## 2026-10-02：人工响应卡片与偏离表初稿第一阶段

- 新增卡片创建、编辑、分类、提交、确认、驳回、补材料、撤回和重开，以及同抽取作业的
  原子批量处置。按技术/商务职责逐项人工决策，令牌、agent、worker 不能确认或处置。
- 迁移 `0015` 增加响应修订、真实材料 Evidence 与链接、起草运行记录和三表快照；
  新表强制单位隔离，数据库检查人工身份、状态迁移、不可变历史、关联完整性和全集覆盖。
  任务遮挡设置默认开启，仅人类 admin 可修改；预留第二阶段模型起草与固定输入字段。
- `bid draft` 后台确定性组表，复制确认内容，分别输出实质性、商务、技术表、须遵守清单和
  缺口，保留负偏离；材料替换或卡片修订后读取旧稿标记失效。组表不调用模型、不产生模型费用。
- API、本地/远程 CLI 和 `bid schema` 新增对应命令；保留 Result 七键及版本 `1.2`，
  有缺口的组表及其作业查询/等待使用部分成功退出码。机制见
  [response-cards.md](notes/response-cards.md)，操作见 [CLI 指南](guides/cli.md#review-responses-and-assemble-a-draft)。
- 第一阶段预留模型起草与实际外发遮挡，未提供起草命令或导出；后续决策归档于
  [ADR 0005](adr/0005-human-confirmed-responses.md)。
- 样例招标文件开发环境冒烟：1,056 条要求全部列为无卡片缺口，其中 9 条为仅规范化匹配的 `invalid_citation`，
  组表以部分成功退出。当时的完整回归：680 项通过。

## 2026-10-02：参数清单逐项抽取

- 抽取提示词要求硬件、软件参数逐项输出，各自引用原文，保留数值、单位和限定条件，不得用“等”省略；
  跳过只有标题的条目，★ 规则也不再补入“★3.合同的终止：”一类空标题。提示词缓存版本更新。
- 首轮抽取后扫描分号、换行分隔的参数片段，只把有效引用尚未覆盖的片段补发给模型，保留原块标识或页码；
  补抽沿用分批、重试、引用校验和去重，仅执行一轮。
- 抽取作业结果新增 `gap_fill`，报告待补片段、补抽调用和实际新增要求数量。每次已计费调用分别记录用量，
  包括补抽失败、截断和格式错误的调用；原有 CLI/API 字段、Result 契约版本和数据库结构保持不变。
- 机制、调用次数上界与额外成本见 [llm-providers.md](notes/llm-providers.md)。
- 样例 Word 招标文件、`low` 档实测：保存 1,056 条（原 484 条），补抽 1 次调用新增 37 条，335 秒、22 万 token；
  参数密集的技术参数块覆盖 144/161 个参数（原 `low` 73、原 `max` 123）。
- 当时的完整回归：584 项通过。

## 2026-10-02：按官方档位选择推理强度与抽取历史

- 平台模型目录登记服务商公布的推理强度档位（如智谱 GLM-5.3 的 low、high、max），每档带请求参数、
  批次大小和 Anthropic effort，并标出官方默认档；运营后台可编辑档位并逐档测试。
- `bid req extract --reasoning LEVEL` 选择档位，不选时用官方默认档；未登记的档位以
  `unsupported_reasoning` 失败，未分档的模型忽略并警告。`--dry-run` 列出可用档位。
- 每次抽取的要求独立保存：`req list` 默认显示每个文档最近一次成功的抽取，`--job` 查看指定一次，
  `req history` 列出全部抽取；要求带 `job_id` 与 `reasoning`。Result 契约升为 1.2。
- 迁移 `0014`。决定见 [ADR 0004](adr/0004-extractions-per-reasoning-level.md)，机制见
  [reasoning-levels.md](notes/reasoning-levels.md)。
- 样例 Word 招标文件、GLM-5.3-Flash 实测：`low` 141 秒保存 484 条、15 万 token；`max`（4,000 字一批）
  51 分钟保存 914 条、95 万 token；两档引用不通过各 1、2 条，★ 条款均 23/23 覆盖。
- 模型返回空引用或空要求文字的条目按单条拒绝（`empty_quote`、`empty_text`）；抽取中的意外错误也会
  保留已发生调用的用量，日志只记录异常类型与调用栈。
- 网络中断、超时、限流等临时错误先在批次内重试两次（10 秒、30 秒后），不再让整个作业从头重排；
  长时间请求中被断开的 TLS 连接（httpx 抛出的原始 `ssl.SSLError`）也按网络中断处理。
- 当时的完整回归：572 项通过；Playwright 端到端检查通过。

## 2026-10-02：服务商额度用完的提示

- 服务商返回额度用完、欠费或套餐失效（HTTP 402、`insufficient_quota`、`billing_error`、
  智谱 1113、1308–1321 中的额度与套餐类错误码）时，作业以 `provider_quota_exhausted` 失败，
  不再重试三次；报错写明重置时间（服务商给出时）并提示联系系统管理员。智谱 1302、1305
  限流仍按可重试处理。机制见 [llm-providers.md](notes/llm-providers.md)。

## 2026-10-01：Word 招标文件按文档位置引用

- Word 直接解析为段落块和表格单元格块，按标题样式或编号识别章节；合并单元格、嵌套表格、
  内容控件都有稳定位置，页眉页脚、文本框等跳过的内容列在解析警告里。
- Word 来源的要求引用章节路径加段落或单元格，`page` 为 `null`，新增 `location`；
  Result 契约升为 1.1。引用原文必须落在所指的那一个块里。硬性规则 6 相应修改，
  决定见 [ADR 0003](adr/0003-word-structural-citations.md)，机制见
  [docx-citations.md](notes/docx-citations.md)。
- 引用比对忽略全角半角与弯直引号差异；要求按原文顺序列出。
- 模型输出被截断或不符合格式时自动把批次对半拆开重发：先按章节，再按块，长页面或长单元格再按行；
  只有单行仍超限才失败。
- 抽取批次并发发送（`BID_LLM_CONCURRENCY`），默认批次 8,000 字、输出上限 32,000 token，
  每次调用有总时限；`BID_LLM_REQUEST_OPTIONS` 可向请求附加服务商参数。
- [evals/extract_tender.py](../evals/extract_tender.py) 支持 Word，报告引用通过数与 ★ 召回。
- 迁移 `0013`。
- 样例招标文件（WPS，2,117 块）实测：GLM 关闭思考 135 秒抽出 449 条，446 条引用通过，
  ★ 条款 23/23 覆盖（含规则补抽）。
- 引用不通过改为逐条拒绝：其余条目照常保存，被拒条目的位置、原文与原因写入作业结果
  `rejected` 并给出警告；全部不通过时仍以 `invalid_citation` 失败。
- 当时的完整回归：556 项通过。

## 2026-10-01：预付余额与充值卡密

- 单位预付余额与只能新增的流水；平台计费调用按售价扣除，余额必须大于 0 才能提交平台计费作业，
  不设透支额度。
- 平台管理员批量生成、作废卡密，直接增减或设定单位余额；单位管理员在 `/app/org/billing`
  或 `bid billing redeem` 兑换卡密。
- 计费币种由 `BID_BILLING_CURRENCY` 配置；售价与应收字段去掉 `usd` 后缀。
- 单位登录页按账号列出所属单位（`/auth/orgs`）。
- 迁移 `0012`。决定见 [ADR 0002](adr/0002-prepaid-billing.md)，机制见
  [prepaid-billing.md](notes/prepaid-billing.md)。
- 当时的完整回归：540 项通过；Playwright 端到端检查通过。

## 2026-10-01：平台运营后台

- 平台管理员由部署配置指定，登录需密码与 TOTP，验证码只能用一次，15 分钟内失败 5 次锁定；
  平台会话 30 分钟，与单位会话和 API 令牌互不通用。
- 运营后台（`/app`）与 `bid platform` 命令：开通、停用、启用单位，一次性设置密码链接，
  平台模型目录与测试，按月用量与应收（可导出 CSV），平台审计。
- 停用单位后，其登录、会话和令牌立即失效。
- 设为默认的目录模型用于所有单位的要求抽取，用量按成本价与售价分别记录。
- 迁移 `0010`、`0011`。跨单位访问的决定见 [ADR 0001](adr/0001-platform-console-access.md)，
  机制见 [platform-console.md](notes/platform-console.md)。
- 当时的完整回归：514 项通过；Playwright 端到端检查通过。

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
