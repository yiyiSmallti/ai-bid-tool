import { orgSession } from "./api.js";
import { orgRequest } from "./org.js";
export const reviewStates = { unconfirmed: "待确认", legacy_unconfirmed: "历史要求待确认", confirmed: "要求已确认", invalidated: "要求确认已失效" };
export const entryOrigins = { extracted: "模型抽取", legacy: "历史抽取", manual_rejected: "拒绝项补录", manual_missing: "遗漏补录" };
export const reviewActions = { seed: "创建待确认要求", manual_add: "人工补录", confirm: "确认要求", reopen: "重新打开", invalidate: "确认失效", source_repair: "修复引用" };
export const requirementRequest = (method, path, body) => orgRequest(method, path, body, { contractVersion: 4 });
export function checkScope(scope, taskId, jobId = null) {
  if (!scope || scope.org_id !== orgSession.get()?.orgId || scope.task_id !== taskId || jobId && scope.extraction_job_id !== jobId) throw new Error("要求集合范围不符合契约");
}
export function checkReview(row, taskId, jobId) {
  checkScope(row, taskId, jobId);
  if (!reviewStates[row.state] || !entryOrigins[row.origin] || !Number.isInteger(row.revision) || row.revision < 1 || !/^[a-f0-9]{64}$/.test(row.review_hash) || !row.content?.source || row.content.source.document_id == null) throw new Error("要求审阅响应不符合契约");
}

export const preparationStates = { preparation: "评分来源准备中", ready: "评分来源已确认", stale: "评分来源已过期" };
export const requirementRefreshErrors = ["requirement_unconfirmed", "requirement_invalidated", "review_changed"];
export function preparationBlocker(review) {
  if (review?.state === "ready") return "";
  if (review?.state === "preparation") return "评分来源要求尚未全部确认，请先核对要求，再重新读取评分规则";
  if (review?.state === "stale") return "评分来源要求或确认已变化，请重新核对要求，再重新读取评分规则";
  return "评分来源确认状态尚未就绪，请重新读取评分规则并核对要求";
}
