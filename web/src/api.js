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
const ASSESSMENT_PATH = /^\/(?:tasks\/[^/?]+\/(?:assessment-inputs|assessment-citation|checks|scores(?:\/[^/?]+)?|score-rubrics(?:\/[^/?]+(?:\/(?:history|revisions|decisions|(?:sections|items|coverage)\/[^/?]+\/(?:classification|decisions)))?)?)|checks\/[^/?]+(?:\/findings\/[^/?]+\/decisions)?)$/;
const COSIGN_PATH = /^\/(?:tasks\/[^/?]+\/(?:review-rule|requirements\/[^/?]+\/review-policy)|cards\/[^/?]+\/(?:review-rounds|signoffs))$/;
const COLLABORATION_PATH = /^\/(?:tasks\/[^/?]+\/requirements\/[^/?]+\/assignment|cards\/[^/?]+\/threads(?:\/[^/?]+\/comments)?)$/;
const ORG_PATH = /^\/(org\/current|tasks(?:\/[^/?]+(?:\/(?:workflow|progress|members(?:\/[^/?]+(?:\/remove)?)?|member-candidates|handover|archive|unarchive|board|activity|events(?:\/poll)?|documents|jobs|extractions|requirements|products|features|certificates|profiles|certificate-files|evidence-sources|cards(?:\/(?:dispositions|generations))?|drafts|exports|product-simulations|simulated-resources|model-redaction))?)?|documents\/[^/?]+(?:\/(?:chunks|parse|extract|download-link|download|pages\/\d+\/preview))?|exports\/[^/?]+(?:\/(?:download-link|download|preview(?:\/pages\/\d+)?))?|jobs\/[^/?]+(?:\/cancel)?|cards\/[^/?]+(?:\/(?:actions|classification))?|drafts\/[^/?]+|resources\/(?:products|features|certificates|profiles)(?:\/revisions\/[^/?]+\/file\/(?:download-link|download|pages\/\d+\/preview))?|evidence-sources\/[^/?]+\/preview\/(?:download-link|download)|resources\/profiles\/[^/?]+\/revisions|resources\/certificates\/(?:files|[^/?]+\/(?:revisions|file-revisions))|billing(?:\/redeem)?|confidential-fields(?:\/[^/?]+\/(?:revisions|values))?|confidential-values(?:\/[^/?]+\/reveal)?)$/;
function checkedPath(path, org) {
  if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//") || path.includes("\\")) throw new ApiError(0, "invalid_path", "请求地址不受支持");
  const url = new URL(path, window.location.origin);
  if (url.origin !== window.location.origin || url.hash || (!PUBLIC.has(url.pathname) && !(org ? ORG_PATH.test(url.pathname) || ASSESSMENT_PATH.test(url.pathname) || COLLABORATION_PATH.test(url.pathname) || COSIGN_PATH.test(url.pathname) : url.pathname.startsWith("/platform/")))) throw new ApiError(0, "invalid_path", "请求地址不受支持");
  return url.pathname;
}
function retryDelay(value) {
  if (!value) return 0;
  return /^\d+$/.test(value) ? Number(value) * 1000 : Math.max(0, Date.parse(value) - Date.now()) || 0;
}
async function parseResult(response, pathname, org, version4 = false) {
  let payload;
  try { payload = await response.json(); }
  catch { throw new ApiError(response.status, "invalid_response", "服务返回了无法识别的内容", null, retryDelay(response.headers.get("Retry-After"))); }
  const keys = ["ok", "command", "data", "items", "warnings", "cost", "duration_ms"];
  if (!payload || typeof payload !== "object" || !payload.data || typeof payload.data !== "object" || Array.isArray(payload.data) || typeof payload.ok !== "boolean" || typeof payload.command !== "string" || !payload.command || keys.some((key) => !(key in payload)) || Object.keys(payload).length !== keys.length || !Array.isArray(payload.items) || !Array.isArray(payload.warnings) || !payload.warnings.every(item => typeof item === "string") || !Number.isInteger(payload.duration_ms) || payload.duration_ms < 0) throw new ApiError(response.status, "invalid_response", "服务响应不符合 Result 契约");
  if (version4) {
    const cost = payload.cost, fields = ["llm_tokens", "ocr_pages", "usd", "basis", "charge", "billing_currency", "task_amount", "unpriced_calls", "unresolved_calls"];
    const decimal = value => value === null || typeof value === "string" && /^\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value) && Number.isFinite(Number(value));
    if (!cost || typeof cost !== "object" || Object.keys(cost).length !== fields.length || fields.some(field => !(field in cost)) || !["llm_tokens", "ocr_pages", "unpriced_calls", "unresolved_calls"].every(field => Number.isInteger(cost[field]) && cost[field] >= 0) || !(cost.usd === null || typeof cost.usd === "number" && Number.isFinite(cost.usd) && cost.usd >= 0) || !["actual", "first_pass_upper_bound", "unknown", "zero", "cache_hit"].includes(cost.basis) || !/^[A-Z]{3}$/.test(cost.billing_currency) || !decimal(cost.charge) || !decimal(cost.task_amount)) throw new ApiError(response.status, "invalid_response", "服务成本响应不符合 Result 4.0 契约");
  }
  const assessmentRead = ["check show", "score show", "score rubric show"].includes(payload.command) &&
    (payload.data.report?.completion === "partial" || payload.data.completion === "partial" ||
      ["unavailable", "range_only"].includes(payload.data.total_status) ||
      (typeof payload.data.snapshot === "string" && typeof payload.data.parent_id === "string" && Number.isInteger(payload.data.returned)));
  const assessmentHistory = ["check list", "score list", "score rubric list"].includes(payload.command) && typeof payload.data.task_id === "string" && Number.isInteger(payload.data.total) && payload.items.length > 0 && payload.items.every(item => typeof (item.report?.id ?? item.id) === "string") && payload.items.some(item => item.report?.completion === "partial" || ["unavailable", "range_only"].includes(item.total_status));
  const assessmentJob = payload.command === "job status" && ["check", "score_rubric", "score"].includes(payload.data.kind) &&
    ((payload.data.status === "succeeded" && payload.data.result?.completion === "partial") ||
      (payload.data.status === "cancelled" && (payload.data.error === null || payload.data.error?.code)) ||
      (payload.data.status === "failed" && payload.data.error?.code && [2, 3, 4, 5].includes(payload.data.error.exit_code)));
  const partial = (!payload.data.error && (assessmentRead || assessmentHistory)) || assessmentJob || (payload.command === "draft show" && payload.data.completion === "partial") || (payload.command === "job status" && payload.data.result?.completion === "partial" && ["draft", "card_generate"].includes(payload.data.kind));
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
export async function request(method, path, body, { org = false, signal, binary = false, contractVersion } = {}) {
  const pathname = checkedPath(path, org);
  // The unversioned API deliberately projects legacy v3 costs. Assessment pages
  // and job receipts require enforced budget preflight and actual Result 4 costs.
  const assessmentJobs = /^\/tasks\/[^/]+\/jobs$/.test(pathname) && ["check", "score_rubric", "score"].includes(new URL(path, window.location.origin).searchParams.get("kind"));
  const apiPath = org && (ASSESSMENT_PATH.test(pathname) || assessmentJobs || contractVersion === 4) ? `/v4${path}` : path;
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
    const response = await fetch(apiPath, { method, headers, credentials: "omit", cache: "no-store", redirect: "error", signal: controller.signal, body: body === undefined ? undefined : multipart ? body : JSON.stringify(body) });
    const value = binary && response.ok ? await response.blob() : await parseResult(response, pathname, org, apiPath.startsWith("/v4/"));
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

// A fetch stream carries the same tenant credentials and cancellation epoch as Result reads.
export async function orgEventStream(path, cursor, signal, onMessage) {
  const pathname = checkedPath(path, true), current = orgSession.get(), epoch = orgEpoch;
  if (!current) throw new ApiError(401, "session_required", "请重新登录单位");
  const controller = new AbortController(), abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) abort();
  orgRequests.add(controller);
  let reader;
  try {
    const headers = { Authorization: `Bearer ${current.session}`, "X-Org-Id": current.orgId, Accept: "text/event-stream" };
    if (cursor) headers["Last-Event-ID"] = cursor;
    const response = await fetch(path, { headers, credentials: "omit", cache: "no-store", redirect: "error", signal: controller.signal });
    if (!response.ok) await parseResult(response, pathname, true);
    if (!response.headers.get("Content-Type")?.startsWith("text/event-stream") || !response.body) throw new ApiError(502, "invalid_event_stream", "进度流格式不符合契约");
    reader = response.body.getReader();
    const decoder = new TextDecoder(), encoder = new TextEncoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (controller.signal.aborted || orgEpoch !== epoch) throw new DOMException("单位会话已改变", "AbortError");
      if (done) break;
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
      if (encoder.encode(buffer).length > 262144) throw new ApiError(502, "event_buffer_limit", "进度流超过缓冲上限");
      let end;
      while ((end = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, end); buffer = buffer.slice(end + 2);
        if (encoder.encode(frame).length > 8192) throw new ApiError(502, "invalid_event_stream", "进度帧超过上限");
        const data = frame.split("\n").filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n");
        if (!data) continue;
        if (encoder.encode(data).length > 4096) throw new ApiError(502, "invalid_event_stream", "进度数据超过上限");
        let payload; try { payload = JSON.parse(data); } catch { throw new ApiError(502, "invalid_event_stream", "进度数据无法识别"); }
        if (controller.signal.aborted || orgEpoch !== epoch) throw new DOMException("单位会话已改变", "AbortError");
        await onMessage(payload);
      }
    }
  } finally {
    await reader?.cancel().catch(() => {});
    orgRequests.delete(controller); signal?.removeEventListener("abort", abort);
  }
}
