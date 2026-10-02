import { reactive } from "vue";
import { orgSession, request } from "./api.js";
export const orgAccess = reactive({ role: null, error: "" });
window.addEventListener("bid:org-reset", () => { orgAccess.role = null; orgAccess.error = ""; });
export const orgRequest = (method, path, body, options = {}) => request(method, path, body, { ...options, org: true });
export const display = (value) => value === null || value === undefined ? "未知" : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
export const errorText = (error) => error.status === 404 ? "不可访问" : error.code === "org_inactive" ? "该单位已停用，请联系管理员" : error.code === "insufficient_balance" ? "余额不足，请联系单位管理员" : `${error.message}${error.code ? `（${error.code}）` : ""}${error.retryAfter ? `；请在 ${Math.ceil(error.retryAfter / 1000)} 秒后重试` : ""}`;
export const locationLabel = (source, name = "") => [name, source?.page != null ? `第 ${source.page} 页` : [source?.location?.section_path?.join(" / "), source?.location?.label].filter(Boolean).join(" · ")].filter(Boolean).join(" · ");
export const categories = { qualification: "资格", technical: "技术", scoring: "评分", substantive: "实质性" };
export const states = { missing_card: "缺卡片", draft: "草稿", pending_review: "待审阅", confirmed: "已确认", rejected: "已驳回", needs_material: "需补材料" };
export const domainFor = (row) => row.card ? row.card.review_domain : (row.category === "technical" ? "technical" : row.category === "qualification" ? "commercial" : null);
export const mine = (domain) => (orgAccess.role === "bidder" && domain === "commercial") || (orgAccess.role === "technical" && domain === "technical");
export function remember(key, value) { const org = orgSession.get(); if (org) sessionStorage.setItem(`bid.org.context.${org.orgId}.${key}`, JSON.stringify(value)); }
export function recalled(key) { const org = orgSession.get(); return org ? JSON.parse(sessionStorage.getItem(`bid.org.context.${org.orgId}.${key}`) ?? "null") : null; }
export async function downloadOriginal(linkPath, name) {
  const signed = await orgRequest("GET", linkPath);
  const blob = await orgRequest("GET", signed.data.url, undefined, { binary: true });
  if (!["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"].includes(blob.type)) throw new Error("原件类型不符合约定");
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = name; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
