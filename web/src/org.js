import { reactive } from "vue";
import { orgSession, request } from "./api.js";
import { confirmAction, formatTime } from "./ui.js";
export { confirmAction, formatTime };
export const orgAccess = reactive({ role: null, userId: null, error: "" });
window.addEventListener("bid:org-reset", () => { orgAccess.role = null; orgAccess.userId = null; orgAccess.error = ""; });
export const orgRequest = (method, path, body, options = {}) => request(method, path, body, { ...options, org: true });
export const display = (value) => value === null || value === undefined ? "未知" : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
// Server messages are English for the CLI; the console shows Chinese for the codes a member can hit.
const errorMessages = {
  requirement_unconfirmed: "要求尚未确认，请先核对要求", requirement_invalidated: "要求确认已失效，请重新核对", review_changed: "要求修订已变化，请重新核对", manual_preview_changed: "补录来源或集合已变化，请重新核验", requirement_set_changed: "要求集合已变化，请重新读取", quote_not_at_position: "逐字引文不在所选位置", nonverbatim_quote: "请填写与原文完全相同的逐字引文", ambiguous_quote: "引文在该位置有多个匹配，请明确引用", unverified_location: "所选原文位置尚未核验",
  cosign_required: "本项需要会签，请在详情中逐个职责签署", stale_round: "会签轮次已变化，请重新核对", stale_policy: "会签策略已变化，请重新核对", policy_conflict: "会签策略修订已变化，请重新核对", review_round_conflict: "会签轮次已变化，请重新核对", cosign_invalidated: "会签已失效，需要重新审阅", task_archived: "任务已归档，请先恢复任务", task_busy: "仍有作业或未确定费用，无法归档", member_has_assignments: "该成员仍有未交接的工作", event_cursor_expired: "进度游标已过期，需要重新读取", invalid_event_cursor: "进度游标无效，需要重新读取", board_cursor_expired: "看板分页已过期，需要重新读取", forbidden: "当前角色无权执行此操作", invalid_transition: "当前状态下不能执行此操作，请重新核对当前修订",
  incomplete_response: "响应种类、正文、偏离和说明都需要填写，说明不能只写“满足”", missing_evidence: "证据响应至少需要关联一项材料才能确认",
  unexpected_evidence: "承诺不能关联证据材料", review_mismatch: "请逐项勾选本修订关联的全部材料", warning_review_required: "请勾选全部警示并填写处理理由",
  revision_conflict: "修订已被他人更新，请重新读取", stale_material: "所选材料已变化，需要重新核对", invalid_citation: "招标原文引用无效",
  needs_reconfirmation: "当前要求需要重新核对并确认", stale_card: "卡片需要按当前要求重新确认", not_parsed: "请先完成文档解析再抽取要求",
  insufficient_balance: "余额不足，请联系单位管理员", generation_input_changed: "预检后输入、模型或价格已变化，请重新预检",
  generation_model_changed: "模型目录已变化，请重新预检", generation_rules_changed: "起草规则已变化，请重新预检", draft_input_changed: "输入已变化，请重新预检后提交",
  unsupported_reasoning: "所选推理档位不受支持", provider_unavailable: "模型服务不可用", invalid_document: "只支持可读取、未加密且在大小限制内的 PDF 或 DOCX",
  file_too_large: "文件超过上传大小限制", invalid_evidence_quote: "摘录没有逐字出现在所选材料位置", inactive_snapshot: "请先选择任务中的有效资源版本",
  empty_requirements: "该抽取没有保存任何要求", queue_unavailable: "作业已保存，但队列暂不可用，请重复提交以排队", conflict: "与已有记录冲突",
  idempotency_conflict: "同一请求的内容不一致", decision_set_mismatch: "处置条目必须与预检集合完全一致", invalid_input: "请求参数无效",
  unknown_confidential_field: "引用了不存在或已归档的保密字段", redacted_placeholder_in_response: "正文含遮挡占位，请改用保密字段或写出原文",
  confidential_key_exists: "已有同名键的保密字段", confidential_field_archived: "该保密字段已归档", human_required: "此操作需要成员本人登录操作",
};
const messageOverrides = {
  "Review role does not match the assigned domain": "当前角色不负责该职责的审阅",
  "Administrator required": "需要单位管理员执行此操作",
  "Human decision required": "此操作需要成员本人决定",
  "Human session required": "此操作需要成员登录会话",
};
export const errorText = (error) => {
  if (error.status === 404) return "不可访问";
  if (error.code === "org_inactive") return "该单位已停用，请联系管理员";
  const text = messageOverrides[error.message] ?? errorMessages[error.code] ?? error.message;
  return `${text}${error.code ? `（${error.code}）` : ""}${error.retryAfter ? `；请在 ${Math.ceil(error.retryAfter / 1000)} 秒后重试` : ""}`;
};
const warningTexts = {
  "Company metadata is a declaration; authenticity, performance and qualification have not been verified": "单位资料是自行声明，真实性、业绩和资质未经核验。",
  "Cost estimate is unavailable without an approved provider.": "当前模型没有可用的计价信息，暂时无法预估抽取费用。",
  "The current model has no reasoning levels configured; the level was ignored.": "当前模型未登记推理档位，所选档位已忽略。",
};
export const warningText = (warning) => warningTexts[warning] ?? warning;
// A Word label already spells out its section path, so the path is only a fallback.
export const locationLabel = (source, name = "") => [name, source?.page != null ? `第 ${source.page} 页` : source?.location?.label || source?.location?.section_path?.join(" / ")].filter(Boolean).join(" · ");

// Chinese labels for API codes. A code without a label is shown as-is so new values stay visible.
export const label = (map, value, fallback = "未知") => value === null || value === undefined ? fallback : map[value] ?? String(value);
export const categories = { qualification: "资格", technical: "技术", scoring: "评分", substantive: "实质性" };
export const states = { missing_card: "缺卡片", draft: "草稿", pending_review: "待审阅", confirmed: "已确认", rejected: "已驳回", needs_material: "需补材料" };
export const roles = { admin: "单位管理员", bidder: "商务审核", technical: "技术审核", viewer: "只读成员" };
export const domains = { commercial: "商务 / 资格", technical: "技术" };
export const eligibilities = { requirement_unconfirmed: "要求尚未确认", requirement_invalidated: "要求确认已失效", eligible: "可进入初稿", unconfirmed: "待确认", unclassified: "待分类", comply_only: "仅需遵守", stale_material: "材料已变化", invalid_citation: "引用无效", needs_reconfirmation: "需重新确认", missing_card: "缺卡片" };
export const dispositions = { respond: "逐项响应", comply_only: "仅需遵守" };
export const deviations = { none: "无偏离", positive: "正偏离", negative: "负偏离" };
export const responseKinds = { commitment: "承诺", evidence: "证据响应" };
export const jobStatuses = { queued: "排队中", running: "运行中", succeeded: "已成功", failed: "失败", cancelled: "已取消" };
export const jobKinds = { parse: "解析", extract: "要求抽取", card_generate: "模型起草", draft: "组表", export_render: "导出", provider_test: "模型测试", check: "检查", score_rubric: "评分规则生成", score: "评分预估" };
export const documentStatuses = { uploaded: "已上传", parsing: "解析中", parsed: "已解析", failed: "解析失败" };
export const citationModes = { page: "按页码", block: "按段落 / 单元格" };
export const actorKinds = { session: "成员", token: "令牌", agent: "代理", worker: "后台作业" };
export const materialKinds = { declaration: "资源声明", user_supplied_pdf_page: "证书页", user_screenshot: "上传截图", browser_screenshot: "网页截图", user_diagram: "示意图", certificate_image: "证书图片", vendor_web: "厂商网页", vendor_pdf: "厂商 PDF", prototype: "原型" };
export const quoteChecks = { exact_field_match: "字段逐字匹配", unreviewed_page: "原页待核对", human_page_review: "原页已人工核对", unreviewed_image: "图片待核对", human_image_review: "图片已人工核对" };
export const statusTag = { confirmed: "success", pending_review: "warning", rejected: "danger", needs_material: "danger", draft: "info", missing_card: "info" };
export const jobTag = { succeeded: "success", failed: "danger", cancelled: "info", running: "primary", queued: "warning" };

export const domainFor = (row) => row.card ? row.card.review_domain : (row.category === "technical" ? "technical" : row.category === "qualification" ? "commercial" : null);
export const mine = (domain) => (orgAccess.role === "bidder" && domain === "commercial") || (orgAccess.role === "technical" && domain === "technical");
export function remember(key, value) { const org = orgSession.get(); if (org) sessionStorage.setItem(`bid.org.context.${org.orgId}.${key}`, JSON.stringify(value)); }
export function recalled(key) { const org = orgSession.get(); return org ? JSON.parse(sessionStorage.getItem(`bid.org.context.${org.orgId}.${key}`) ?? "null") : null; }
// Selection IDs of simulated materials per task; refreshed whenever materials change.
const simulated = new Map();
window.addEventListener("bid:task-materials-changed", (event) => simulated.delete(event.detail?.taskId));
window.addEventListener("bid:org-reset", () => simulated.clear());
export function simulatedSelections(taskId) {
  if (!simulated.has(taskId)) {
    const pending = orgRequest("GET", `/tasks/${taskId}/simulated-resources`).then((result) => new Set(result.data.selection_ids)).catch((exc) => { simulated.delete(taskId); throw exc; });
    simulated.set(taskId, pending);
  }
  return simulated.get(taskId);
}
export async function downloadOriginal(linkPath, name) {
  const signed = await orgRequest("GET", linkPath);
  const blob = await orgRequest("GET", signed.data.url, undefined, { binary: true });
  if (!["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"].includes(blob.type)) throw new Error("原件类型不符合约定");
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = name; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
