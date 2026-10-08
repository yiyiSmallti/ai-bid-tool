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
export const reviewError = error => reviewErrors[error.code] ?? (error.status === 404 ? "不可访问" : "操作未完成，请刷新状态后再试");

export async function reviewAuthority(taskId) {
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
  return task.data.workflow.state === "active" && member?.active && ["owner", "contributor"].includes(member.role) && ["admin", "bidder"].includes(identity.data.role);
}

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
