import { ApiError, orgSession } from "./api.js";
import { orgAccess, orgRequest } from "./org.js";

export const reportSections = [
  ["overall", "一、总体结论"], ["basic_information", "二、基本信息"],
  ["compliance", "三、废标判定"], ["signatures", "签章校验"],
  ["risks", "四、高风险缺陷"], ["scores", "五、得分预估"],
  ["evidence", "六、证据核对"], ["remediation", "七、补救清单"],
  ["methodology", "八、检验说明"],
].map(([key, title]) => ({ key, title }));
export const reportRequest = (method, path, body, options = {}) => orgRequest(method, path, body, { ...options, contractVersion: 4 });
export const invalidReport = () => new ApiError(502, "invalid_response", "报告响应无法核验");
const hash = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
export function checkedReportPage(result, taskId, reviewId, section, snapshotId = null) {
  const data = result.data;
  if (data.task_id !== taskId || data.review_id !== reviewId || data.section !== section || data.snapshot_id !== snapshotId || !hash(data.input_hash) || !hash(data.report_input_hash) || !hash(data.decisions_snapshot_sha256) || !hash(data.current_decisions_snapshot_sha256) || !["protected", "cleared", "safe"].includes(data.projection) || data.projection === "protected" && !["admin", "bidder"].includes(orgAccess.role) || !["complete", "partial"].includes(data.completion) || !Array.isArray(data.sections) || data.sections.length !== 9 || data.sections.some((item, index) => item.key !== reportSections[index].key || item.title !== reportSections[index].title) || typeof data.advisory_statement !== "string" || !data.advisory_statement.includes("评标委员会") || result.items.length > 50 || !(data.next_cursor === null || typeof data.next_cursor === "string")) throw invalidReport();
  if (result.items.some(row => !row || !["notice", "basic_information", "obligation", "pdf_validation", "signing_requirement", "usage", "cost", "finding"].includes(row.kind) || row.kind === "finding" && (!row.finding || row.finding.task_id !== taskId || row.finding.review_id !== reviewId || row.decision && row.decision.finding_id !== row.finding.id) || row.kind === "basic_information" && (row.task_id !== taskId || row.review_id !== reviewId))) throw invalidReport();
  if (!Array.isArray(data.artifacts) || data.artifacts.length > 2 || data.projection !== "protected" && data.artifacts.length || data.artifacts.some(row => row.org_id !== orgSession.get()?.orgId || row.task_id !== taskId || row.report_id !== reviewId || snapshotId && row.snapshot_id !== snapshotId || !["docx", "console"].includes(row.format) || !hash(row.sha256) || !Number.isInteger(row.size_bytes) || row.size_bytes <= 0)) throw invalidReport();
  return result;
}
export async function downloadReport(artifact, taskId, reviewId, signal) {
  const id = encodeURIComponent(artifact.id), descriptor = (await reportRequest("GET", `/bid-review-artifacts/${id}/download-link`, undefined, { signal })).data;
  const fixed = descriptor.artifact, url = new URL(descriptor.url, window.location.origin);
  if (!fixed || fixed.id !== artifact.id || fixed.task_id !== taskId || fixed.report_id !== reviewId || fixed.format !== "docx" || fixed.sha256 !== artifact.sha256 || fixed.size_bytes !== artifact.size_bytes || !Number.isInteger(fixed.size_bytes) || fixed.size_bytes <= 0 || fixed.size_bytes > 100 * 1024 * 1024 || url.origin !== window.location.origin || url.pathname !== `/bid-review-artifacts/${id}/download` || url.hash || url.searchParams.size !== 1 || !url.searchParams.get("signature") || descriptor.expires_in !== 300) throw invalidReport();
  const blob = await reportRequest("GET", `${url.pathname}${url.search}`, undefined, { signal, binary: true });
  if (blob.type !== "application/vnd.openxmlformats-officedocument.wordprocessingml.document" || blob.size !== fixed.size_bytes) throw invalidReport();
  const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  if (Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, "0")).join("") !== fixed.sha256 || signal?.aborted) throw invalidReport();
  const local = URL.createObjectURL(blob), anchor = document.createElement("a");
  anchor.href = local; anchor.download = "bid-review-report.docx"; anchor.click();
  setTimeout(() => URL.revokeObjectURL(local), 1000);
}
