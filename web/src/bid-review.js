import { ApiError, orgSession } from "./api.js";
import { orgAccess, orgRequest } from "./org.js";

export const fileKinds = { tender: "招标文件", qualification: "资格材料", commercial_technical: "商务技术文件", price: "报价文件", declaration: "声明文件", other: "其他投标材料" };
export const submissionStates = { uploaded: "已上传，待准备", prepared: "准备完成", withdrawn: "已停用" };
export const preparationStatuses = { queued: "排队中", running: "准备中", succeeded: "准备完成", failed: "准备失败", cancelled: "已取消" };
export const warningLabels = {
  docx_heading_inferred: "Word 标题层级含推断结果",
  docx_structural_content_skipped: "Word 中存在未纳入结构解析的内容",
  docx_no_structural_text: "Word 未发现可用结构文本",
  docx_page_map_unknown: "Word 结构块尚未映射到分页",
  image_page_not_transcribed: "图片页未做 OCR 或转录",
  unparsed_image_regions: "页面含未解析的图片区域",
  render_resolution_reduced: "页面渲染分辨率因处理上限而降低",
  pdf_signature_fields_not_validated: "仅发现 PDF 签名字段，尚未验证",
};
export const safeWarning = value => warningLabels[value] ?? "存在解析范围限制，请按原件核对";
export const mib = bytes => `${(bytes / 1024 / 1024).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} MiB`;
const enc = value => encodeURIComponent(value);
export const submissionPath = task => `/tasks/${enc(task)}/bid-submissions`;
const reviewErrors = { converter_profile_unavailable: "当前部署未固定 Word 转换器与字体配置，请联系管理员后重新预检", bid_upload_limit: "文件数量或大小超过当前部署限制", submission_too_large: "本次提交超过总大小限制", converter_unavailable: "当前部署未配置 Word 转换服务，暂时不能准备 DOCX", bid_preflight_mismatch: "预检回执与当前身份或输入不一致，请重新预检", idempotency_conflict: "同一请求编号的内容不一致，请刷新并重新预检", bid_submission_withdrawn: "提交已停用，不能开始准备", bid_preflight_expired: "预检已过期，请重新预检", preflight_expired: "预检已过期，请重新预检", preflight_invalid: "预检回执无法核验，请重新预检", bid_input_changed: "输入已变化，请重新预检", preparation_input_changed: "输入已变化，请重新预检", request_id_conflict: "请求编号已用于不同内容，请重新建立提交", invalid_document: "只支持未加密、无需修复的 PDF 或有效 DOCX", bid_file_mismatch: "文件内容或顺序与清单不一致，请重新选择", bid_pdf_invalid: "PDF 无法直接读取；请使用未加密且无需修复的原件", bid_docx_invalid: "DOCX 结构无效或超过处理限制，请核对原件", bid_file_type: "文件内容与声明类型不一致；仅支持 PDF、DOCX", bid_retry_not_terminal: "上一次准备仍在运行，不能重试", bid_retry_missing: "没有可重试的准备记录，请先预检并提交", task_archived: "任务已归档，不能上传或准备", not_found: "不可访问", forbidden: "当前身份无权执行此操作", human_required: "需要商务成员或管理员本人登录", file_too_large: "文件超过当前部署的上传限制" };
Object.assign(reviewErrors, {
  bid_name_revision_conflict: "补充名单修订已变化，请刷新页面后填写完整名单", bid_name_list_limit: "单位与人员脱敏名单超过处理上限，请缩小名单", bid_name_limit: "单位与人员脱敏名单超过处理上限，请缩小名单",
  bid_preparation_missing: "请先完成此固定版本的本地准备", provider_unavailable: "当前模型服务不可用，请联系管理员核对配置", outbound_authorization_required: "缺少有效的逐页脱敏文本授权，请由任务负责人核对并授权", outbound_authorization_missing: "缺少有效的逐页脱敏文本授权", bid_outbound_authorization_missing: "缺少有效的逐页脱敏文本授权", bid_outbound_authorization_stale: "外发授权已因输入或配置变化失效，请重新核对文本并授权", bid_outbound_authority_expired: "授权人的任务职责已变化，请由当前负责人重新授权", bid_outbound_revision_conflict: "授权修订已变化，请刷新外发授权", bid_outbound_input_changed: "准备、脱敏规则或模型配置已变化，请重新核对文本", bid_outbound_page_changed: "页面脱敏文本已变化，请重新核对并授权", bid_outbound_context_mismatch: "授权页面集合与文本哈希不一致，请重新核对", bid_outbound_authorization_changed: "外发授权已变化，请重新预检", bid_outbound_use_revoke: "请使用撤销外发授权操作", bid_price_page_excluded: "报价页或报价分类未确定页禁止外发", bid_text_unavailable: "页面文本不可用，需要核对原件", bid_redaction_integrity: "页面或脱敏清单无法核验，请重新准备并核对", redaction_required: "请先启用任务模型脱敏，再核对外发文本", bid_review_input_changed: "检验输入已变化，请重新预检", bid_review_page_context_limit: "单页完整文本超出模型上下文上限，请调整模型配置后重新预检", bid_review_unresolved_usage: "存在未确定费用，核对处理结果后才能重试", bid_review_output_limit: "检验结果超出显示上限，请缩小查询范围", invalid_cursor: "分页已失效，请重新读取检验结果", tender_pages_excluded: "部分招标页未获授权或无法外发，义务检验覆盖不完整", preparation_coverage_partial: "本地准备存在解析范围限制，需要按原件核对", signing_applicability_unknown: "部分签章条款适用性尚未确定", signing_presence_unchecked: "尚未核验签章是否存在", signing_unresolved: "签章目标位置尚未解决", presence_not_checked: "尚未核验签章是否存在", required_location_unmapped: "签章目标位置尚未映射", candidate_not_assessed: "签章候选尚未评估", applicability_unknown: "签章条款适用性尚未确定", insufficient_balance: "余额不足，请联系单位管理员", task_budget_exceeded: "本次调用超过任务预算", job_max_charge_exceeded: "本次运行达到扣费上限", budget_exceeded: "任务预算不足，部分内容未检验", provider_refusal: "模型拒绝处理，部分内容未检验", queue_unavailable: "检验作业已保存但暂未排队，请刷新状态后重试排队",
});
export const reviewError = error => reviewErrors[error.code] ?? (error.status === 404 ? "不可访问" : "操作未完成，请刷新状态后再试");

export async function reviewAuthorityInfo(taskId) {
  const current = orgSession.get()?.orgId;
  const [identity, task] = await Promise.all([orgRequest("GET", "/org/current"), orgRequest("GET", `/tasks/${enc(taskId)}/workflow`)]);
  if (identity.data.org_id !== current || task.data.workflow?.org_id !== current || task.data.workflow?.task_id !== taskId) throw new ApiError(502, "invalid_response", "任务身份无法核验");
  orgAccess.role = identity.data.role; orgAccess.userId = identity.data.user_id;
  let cursor = null, member = null;
  const cursors = new Set();
  do {
    const query = new URLSearchParams({ limit: "100" }); if (cursor) query.set("cursor", cursor);
    const response = await orgRequest("GET", `/tasks/${enc(taskId)}/members?${query}`);
    if (response.data.org_id !== current || response.data.task_id !== taskId || response.items.length > 100) throw new ApiError(502, "invalid_response", "任务成员范围无法核验");
    member = response.items.find(row => row.user_id === identity.data.user_id) ?? null;
    cursor = response.data.next_cursor;
    if (cursor && cursors.has(cursor)) throw new ApiError(502, "invalid_response", "成员分页重复");
    if (cursor) cursors.add(cursor);
  } while (!member && cursor);
  const writable = Boolean(task.data.workflow.state === "active" && member?.active && ["owner", "contributor"].includes(member.role) && ["admin", "bidder"].includes(identity.data.role));
  const runWritable = Boolean(task.data.workflow.state === "active" && member?.active && ["owner", "contributor"].includes(member.role) && ["admin", "bidder", "technical"].includes(identity.data.role));
  return { writable, runWritable, owner: writable && member.role === "owner" && task.data.workflow.owner_user_id === identity.data.user_id };
}
export async function reviewAuthority(taskId) { return (await reviewAuthorityInfo(taskId)).writable; }

export async function uploadManifest(rows, requestId, dryRun) {
  if (rows.length < 2 || rows.length > 20 || !rows.some(row => row.role === "tender") || !rows.some(row => row.role === "bid")) throw new ApiError(400, "bid_upload_limit", "必须同时选择招标文件和投标文件，合计最多 20 份");
  let total = 0;
  const files = [];
  for (const row of rows) {
    const suffix = row.file.name.toLowerCase().split(".").at(-1);
    if (!["pdf", "docx"].includes(suffix) || !row.file.size || row.file.size > 100 * 1024 * 1024) throw new ApiError(400, "invalid_document", "文件类型或大小不受支持");
    const mediaType = suffix === "pdf" ? "application/pdf" : "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    if (row.file.type && row.file.type !== mediaType) throw new ApiError(400, "invalid_document", "文件扩展名与类型不一致");
    total += row.file.size;
    if (total > 500 * 1024 * 1024) throw new ApiError(400, "submission_too_large", "本次提交超过总大小限制");
    const bytes = await row.file.arrayBuffer(), digest = await crypto.subtle.digest("SHA-256", bytes);
    const sha256 = [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("");
    files.push({ role: row.role, kind: row.role === "tender" ? "tender" : row.kind, media_type: mediaType, sha256, size_bytes: row.file.size });
  }
  return { request_id: requestId, files, dry_run: dryRun };
}
export function multipart(rows, metadata) {
  const body = new FormData(); body.append("metadata", JSON.stringify(metadata));
  for (const row of rows) body.append("files", row.file);
  return body;
}
