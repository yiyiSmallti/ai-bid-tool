---
kind: plan
---

# 契约草案：固定未确认来源的本机 Rust 标注副本

状态：**待批准，未实施。** 本草案对应[路线](roadmap.md)中的 B05。

仓库[agent.md](../../agent.md)要求：
“新功能先写 Pydantic 模型、Provider 接口和 CLI 的 JSON 结构，等确认后再写实现。”
本提案的[Pydantic与Provider草案](annotation/annotation_contracts.py)、
[JSON Schema](annotation/annotation-schemas.json)只在文档中，未注册到运行API/CLI。

## 完整目标与这一最小链路

依据为当前[设计](../AI%20标书工具设计文档.md)的Rust标注边界，以及
[覆盖矩阵](roadmap.md) B05/B07/F03。建议先完整交付一条独立链：
以现有受权source ID读取真实PNG→校验→Rust确定性裁剪/框选/溯源水印→本机新PNG
和可追溯JSON回执。保留原图，始终未确认，不冒充材料真实性或参数分析。

这一步是拟请批准的明确实施顺序，不是缩减完整设计。设计中的服务器作业
`bid evidence stamp`、归档Evidence/Card、人工确认、draft/export等仍待后续契约。
现有[Job](../../server/app/models/entities.py)的document_id为NOT NULL并引用同单位
招标Document；source引用证书原件，不能伪造Document来塞入现有作业。本范围明确
只新增本机派生副本，不宣称已完成服务器stamp作业或完整B05。用户如批准，仅
批准下面具体范围；[完整剩余目标](roadmap.md)不删减。

## 新命令、权限与数据影响

`bid evidence source annotate --id UUID --input PLAN.json --output NEW.png --json`

本地模式通过既有本机PG/RLS服务读取来源；远程模式调用既有受权preview链接和
下载接口。两种模式使用同一Pydantic/标注Provider与结果。所有身份须同时具有
evidence:source:read、task:read、certificate:read、certificate:file:read，与有效
Membership/现有角色取交集。四个既有读取角色可以制作本机副本；旧令牌不扩权。
既有历史来源仍可受权读取，回执保留其active_selection=false并明确警示。

**无新服务器路由、业务表、迁移、授权范围或写入对象。** 仅新本机PNG输出和
脱敏CLI回执；服务器已有原件/来源/审计不改。API令牌与agent仍不能确认/导出，
本机副本不属于draft/export业务命令产物，不自动成为Evidence或已确认输入。
不改变真实账号、持久授权或安全设置；不调用/外传AI、OCR、网页、搜索或云服务。
下载只到调用者本机，测试材料均合成。新增一个命令，既有命令的输出不变。

## 精确输入与像素决定（批准时一并确认）

- SourceAnnotationPlan={crop:PixelRect|null, boxes:PixelRect[]}；默认null和[]，
  意味仅加溯源水印。最多20框，extra=forbid；输入JSON<=128KiB，无任意标签文字、
  覆盖/去水印、confirm/export字段。PixelRect为整数x/y>=0、width/height>=1，
  每项<=8192且右/下端点<=8192；坐标须落在真实源图边界。
- 所有坐标为现有150dpi RGB PNG的原始像素坐标，已尊重PDF原旋转。crop与每框
  都使用原图坐标；有crop时每框必须完整包含于crop，拒绝越界，不自动裁剪框或缩放。
  原图/原PDF不覆盖，框只画2像素红色内边界，不填充、不重绘文字、不做图像补全。
- 输出内容区为原图或原图crop，保持像素/尺寸；画布宽=max(内容宽,1024)，内容
  水平居中且顶边为0。底部加白色溯源区，左右16px、上下16px，固定16px宽/
  20px高ASCII等宽字符格；使用随项目保留的固定小字形，不下载字体。按
  floor((画布宽-32)/16)个字符逐行硬折行；区高=32+20*行数。不允许用户改水印。
- 固定内容依序为：UNCONFIRMED USER-SUPPLIED PDF PAGE、SOURCE_ID、TASK_ID、
  PAGE、DPI、SOURCE_PROFILE、SOURCE_RENDERED_UTC、ORIGINAL_PDF_SHA256、
  SOURCE_PNG_SHA256、PLAN_SHA256。每项KEY=VALUE；时间来自已归档来源rendered_at
  的UTC ISO表示，明确不是证书签发或厂家官网采集时间。ID/page/hash可回溯原档；
  不将用户提供的PDF包装为厂家已认证材料。中文原页像素保持，水印标签使用ASCII。
- 结果与拟结果均每边<=8192px、总像素<=20000000，编码PNG<=40MiB及适用更低
  配置上限。水印扩展超过上限必须拒绝，不偷偷降采样。RGB/无alpha，PNG编码器
  固定设置及profile=source-markup-v1，同源内容/同plan/profile字节确定性相同。
- plan_sha256=校验后model_dump(mode="json")的UTF8 JSON SHA256，包含显式默认值，
  sort_keys=true、separators=(",",":")、ensure_ascii=true、无尾换行。框顺序保留。
  回执映射包含原图crop、content_offset_x、content_offset_y=0、footer_height_px；
  输出图任一内容坐标可回算原图坐标，padding/水印不是原文件区域。
- annotated_at为本机操作UTC时间，与可信服务器来源rendered_at分开；不伪称
  服务端归档时间。status恒unconfirmed_source、confirmed_by=null、
  eligible_for_draft_export=false，并由模型限制；水印也明确UNCONFIRMED。

## Provider、受权读取和本机输出

审查协议AnnotationProvider.annotate(content:bytes, source:EvidenceSourceArchive,
plan:SourceAnnotationPlan)->(bytes,SourceAnnotationRendering)，异步。Python只负责
授权、源下载/校验、子进程边界与回执；裁剪/框/水印/编码在Rust独立可执行文件。
所有来源元数据来自既有授权下载链接Result.items中的唯一完整Archive，不能
从用户plan注入或换绑。原件ID/page/profile/源PNG描述符都保留在结果。

下载复用既有服务精确路径、短签名且身份仍必需、无redirect/外站；读到实际PNG后
核对大小/SHA/格式/尺寸，不把链接请求成功当下载成功。先校验输入与新输出路径，
失败不得启动Rust或产生最终文件。只执行配置明确的本机可信可执行路径，无shell；
不把令牌/服务密钥放入参数、日志或子进程环境。子进程只接收源PNG/公开溯源元数据/
plan，使用本次私有0700临时目录/0600文件，结束清理本次生成的临时文件。

Rust内部CLI拟：`bid-stamp render --request PRIVATE.json --output PRIVATE.png`，
request包括固定私有input_path、授权Archive和plan；stdout只有有界Rendering JSON，
无PNG/密钥/原页文本。新输出路径由Provider生成，不允许plan指定任意Rust路径。
20秒进程deadline，到期终止并等待自有子进程回收，返回失败无最终文件；PNG解码
前限制声明尺寸/输入大小/解码内存，不依靠事后尺寸检查。只用PNG codec，不启用
其他image格式/网络功能。固定ASCII字形如采用第三方数据，先核实许可并保留来源。

Python再核对输出实际PNG/长度/hash/尺寸/profile/plan与源绑定，不盲信Rust回执。
最终通过已验证的原子新文件写入得到0600 PNG，拒绝已有文件/symlink/路径替换竞态；
失败仅清本次临时文件，原件、旧下载、真实目录内容保留。路径不在服务端持久保存。

## JSON与失败契约

Result1.0保留ok、command、data、items、warnings、cost、duration_ms。
成功command="evidence source annotate"，data=SourceAnnotationReceipt，items=[]；
cost.llm_tokens=0、ocr_pages=0、usd=0，duration_ms实际测量。receipt字段：
source（完整原Archive）、plan、plan_sha256、annotation_profile、annotated_at、
output_path、file（PNG名称/SHA/bytes/宽高）、mapping、status、confirmed_by、
eligible_for_draft_export。不把图像/base64塞入JSON；CLI schema批准后只新增1项。

| 退出码 | 明确情形 |
| --- | --- |
| 0 | 真正输出新PNG并校验完毕 |
| 2 | 缺参、非法UUID/JSON/坐标/超限、已有输出或非法输出路径 |
| 3 | 缺可信可执行文件、临时网络/存储故障、子进程deadline |
| 4 | 身份/权限/资源失败、源或输出完整性失败、Rust不可重试失败 |

无部分成功场景，不宣称exit5。复用既有401/403/404语义；跨单位/无权资源404。
错误脱敏，不回显源页/任意输入/令牌。失败不得显示成功或保留部分最终PNG。

## 编译器预检与普通依赖准备影响

本机只读预检：当前PATH没有cargo/rustc，且~/.cargo/bin与/opt/homebrew/bin的
对应精确路径不存在；不是对全盘工具位置的穷尽搜索。尚未安装、下载或改配置。
若批准，允许按官方来源准备本次隔离工具目录，使用任务专用CARGO_HOME/RUSTUP_HOME
与单条命令PATH，不修改用户shell启动文件或持久PATH；工具目录与日志留本机。
官方[rustup安装说明](https://rust-lang.github.io/rustup/installation/index.html)与
[环境变量说明](https://rust-lang.github.io/rustup/environment-variables.html)支持
独立工具目录；实际版本/校验/平台适配在实施时记录，不在此猜测新版本。

Rust依赖限设计选型clap、serde、image、sha2及小型JSON codec serde_json；批准后
核对官方/crates.io源、许可、锁Cargo.lock与实际版本，PNG-only。无新大型依赖或
付费服务、账号/密钥/持久授权。只下载普通工具/依赖代码，不向下载站上传项目资料。
若网络或权限阻止工具准备，尽早记录确切阻塞，不长时间反复重试，不假报cargo通过。

## 批准后验收标准与分步计划

1. 工具与锁文件：隔离Rust可执行环境、依赖与许可来源；cargo fmt/check/clippy/test
   和release构建。缺工具不绕成Python标注或把未运行测试标成通过。
2. Rust真实像素验证：两份明显不同的合成页；无裁剪/裁剪/多框，源图未改变、框外
   内容逐像素相同、坐标可逆、固定水印可见且回执一致；同输入确定性、多页绑定。
   畸形PNG/尺寸/内存/编码上限、越界/超框/去水印字段、timeout/取消都无假成功。
3. 授权与文件边界：两单位/无上下文、角色/范围交集、旧令牌无扩权、失效成员/
   历史来源；源/输出篡改、重定向/断流、缺二进制/非执行文件、路径注入、symlink/
   并发覆盖/原子输出/0600/临时文件清理。只有本次输出可写，原档/表/权限不变。
4. 两种真正CLI与Result/schema：新增一个快照，既有输出和schema项保持；native
   PG/API/既有真实worker与隔离Compose/MinIO回归，全量适用pytest/ruff/pyright/
   Python包与Rust构建。测试Provider只fake，不声称真实AI结果；所有样例合成。
5. Mac支持工具验证既有真实来源读取/下载/错误/重复/返回取消及本机派生PNG实际
   落盘与可见水印/裁剪/框；这是本机标注链验收，没有新Vue表单，不能冒充产品UI。
   保存本机像素/文件证据、英文机制笔记与报告；停自有服务，保留历史/数据/卷。

以上是待实施的验收目标，尚未执行。其余未实现项目列在[路线](roadmap.md)，包括完整服务器
stamp作业/持久派生图/Evidence/Card/人工确认、其他来源、响应校验评分导出、Vue、
记忆、服务配置、预算agent/生产等。
