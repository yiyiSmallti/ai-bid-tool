// Only authentication and non-text navigation IDs belong in sessionStorage.
const PLATFORM_KEY = "bid.platform.session";
const ORG_KEY = "bid.org.session";
let orgEpoch = 0;
const orgRequests = new Set();
function resetOrg() {
  orgEpoch++;
  for (const controller of orgRequests) controller.abort();
  orgRequests.clear();
  for (const key of Object.keys(sessionStorage)) if (key.startsWith("bid.org.context.")) sessionStorage.removeItem(key);
  window.dispatchEvent(new Event("bid:org-reset"));
}
export const session = {
  get: () => sessionStorage.getItem(PLATFORM_KEY),
  set: (value) => sessionStorage.setItem(PLATFORM_KEY, value),
  clear: () => sessionStorage.removeItem(PLATFORM_KEY),
};
export const orgSession = {
  get: () => JSON.parse(sessionStorage.getItem(ORG_KEY) ?? "null"),
  set(value) { resetOrg(); sessionStorage.setItem(ORG_KEY, JSON.stringify(value)); },
  clear() { resetOrg(); sessionStorage.removeItem(ORG_KEY); },
};
export class ApiError extends Error {
  constructor(status, code, message, payload = null, retryAfter = 0) {
    super(message); Object.assign(this, { status, code, payload, retryAfter });
  }
}
const PUBLIC = new Set(["/platform/auth/login", "/auth/login", "/auth/orgs", "/auth/setup-password"]);
const ORG_PATH = /^\/(org\/current|tasks(?:\/[^/?]+(?:\/(?:documents|jobs|extractions|requirements|products|features|certificates|profiles|certificate-files|evidence-sources|cards(?:\/(?:dispositions|generations))?|drafts|exports|model-redaction))?)?|documents\/[^/?]+(?:\/(?:chunks|parse|extract|download-link|download|pages\/\d+\/preview))?|exports\/[^/?]+(?:\/(?:download-link|download|preview(?:\/pages\/\d+)?))?|jobs\/[^/?]+(?:\/cancel)?|cards\/[^/?]+(?:\/(?:actions|classification))?|drafts\/[^/?]+|resources\/(?:products|features|certificates|profiles)(?:\/revisions\/[^/?]+\/file\/(?:download-link|download|pages\/\d+\/preview))?|evidence-sources\/[^/?]+\/preview\/(?:download-link|download)|billing(?:\/redeem)?)$/;
function checkedPath(path, org) {
  if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//") || path.includes("\\")) throw new ApiError(0, "invalid_path", "请求地址不受支持");
  const url = new URL(path, window.location.origin);
  if (url.origin !== window.location.origin || url.hash || (!PUBLIC.has(url.pathname) && !(org ? ORG_PATH.test(url.pathname) : url.pathname.startsWith("/platform/")))) throw new ApiError(0, "invalid_path", "请求地址不受支持");
  return url.pathname;
}
function retryDelay(value) {
  if (!value) return 0;
  return /^\d+$/.test(value) ? Number(value) * 1000 : Math.max(0, Date.parse(value) - Date.now()) || 0;
}
async function parseResult(response, pathname, org) {
  let payload;
  try { payload = await response.json(); }
  catch { throw new ApiError(response.status, "invalid_response", "服务返回了无法识别的内容", null, retryDelay(response.headers.get("Retry-After"))); }
  const keys = ["ok", "command", "data", "items", "warnings", "cost", "duration_ms"];
  if (!payload || typeof payload !== "object" || !payload.data || typeof payload.data !== "object" || typeof payload.ok !== "boolean" || keys.some((key) => !(key in payload)) || Object.keys(payload).length !== keys.length || !Array.isArray(payload.items) || !Array.isArray(payload.warnings)) throw new ApiError(response.status, "invalid_response", "服务响应不符合 Result 契约");
  const partial = (payload.command === "draft show" && payload.data.completion === "partial") || (payload.command === "job status" && payload.data.result?.completion === "partial" && ["draft", "card_generate"].includes(payload.data.kind));
  if (!response.ok || (!payload.ok && !partial)) {
    const error = payload.data?.error ?? {};
    if ((response.status === 401 || (org && error.code === "org_inactive")) && !PUBLIC.has(pathname)) {
      (org ? orgSession : session).clear();
      window.dispatchEvent(new CustomEvent("bid:signed-out", { detail: org ? "org" : "platform" }));
    }
    throw new ApiError(response.status, error.code ?? "invalid_response", error.message ?? "服务返回了未约定的失败结果", payload, retryDelay(response.headers.get("Retry-After")));
  }
  return payload;
}
export async function request(method, path, body, { org = false, signal, binary = false } = {}) {
  const pathname = checkedPath(path, org);
  const headers = {};
  const epoch = orgEpoch;
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  if (org) {
    const current = orgSession.get();
    if (!current) throw new ApiError(401, "session_required", "请重新登录单位");
    orgRequests.add(controller);
    headers.Authorization = `Bearer ${current.session}`;
    headers["X-Org-Id"] = current.orgId;
  } else if (session.get() && pathname.startsWith("/platform/") && !PUBLIC.has(pathname)) headers.Authorization = `Bearer ${session.get()}`;
  const multipart = body instanceof FormData;
  if (body !== undefined && !multipart) headers["Content-Type"] = "application/json";
  try {
    const response = await fetch(path, { method, headers, credentials: "omit", cache: "no-store", redirect: "error", signal: controller.signal, body: body === undefined ? undefined : multipart ? body : JSON.stringify(body) });
    const value = binary && response.ok ? await response.blob() : await parseResult(response, pathname, org);
    if (controller.signal.aborted || (org && epoch !== orgEpoch)) throw new DOMException("单位会话已改变", "AbortError");
    return value;
  } finally { orgRequests.delete(controller); signal?.removeEventListener("abort", abort); }
}
export const money = (value, currency = "") => {
  if (value === null || value === undefined) return "未知";
  const amount = Number(value).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  return currency ? `${amount} ${currency}` : amount;
};
export const count = (value) => Number(value ?? 0).toLocaleString("zh-CN");
