import { SESSION_KEY } from "./session-storage.js";
// The console keeps one bearer session from POST /auth/session in sessionStorage.
const CONTEXT_PREFIX = "bid.context.";
let sessionEpoch = 0;
const inflight = new Set();
export const contextKey = (key) => `${CONTEXT_PREFIX}${key}`;
function resetSession() {
  sessionEpoch++;
  for (const controller of inflight) controller.abort();
  inflight.clear();
  for (const key of Object.keys(sessionStorage)) if (key.startsWith(CONTEXT_PREFIX)) sessionStorage.removeItem(key);
  window.dispatchEvent(new Event("bid:session-reset"));
}
export const session = {
  get: () => sessionStorage.getItem(SESSION_KEY),
  set(value) { resetSession(); sessionStorage.setItem(SESSION_KEY, value); },
  clear() { resetSession(); sessionStorage.removeItem(SESSION_KEY); },
};
export class ApiError extends Error {
  constructor(status, code, message, payload = null, retryAfter = 0) {
    super(message); Object.assign(this, { status, code, payload, retryAfter });
  }
}
const PUBLIC = new Set(["/health"]);
const isExchange = (method, pathname) => method === "POST" && pathname === "/auth/session";
const SETTINGS_PATH = /^\/platform\/(?:credentials(?:\/import-env|\/[^/?]+(?:\/(?:active|replace|remove|test))?)?|clef(?:\/check)?|trust-anchors(?:\/preview|\/[^/?]+\/disable)?)$/;
const BID_REVIEW_PATH = /^\/(?:tasks\/[^/?]+\/(?:bid-submissions(?:\/[^/?]+\/prepare)?|bid-reviews)|bid-submissions\/[^/?]+(?:\/(?:signing-candidates|redaction(?:\/names)?|outbound-authorizations(?:\/revoke)?|presence-preparations|presence-preview|presence-authorizations))?|bid-reviews\/[^/?]+(?:\/(?:findings(?:\/[^/?]+\/(?:decisions|classification))?|report|reports|artifacts))?|bid-presence-images\/[^/?]+\/content|bid-review-artifacts\/[^/?]+\/(?:download-link|download))$/;
const ASSESSMENT_PATH = /^\/(?:tasks\/[^/?]+\/(?:assessment-inputs|assessment-citation|checks|scores(?:\/[^/?]+)?|score-rubrics(?:\/[^/?]+(?:\/(?:history|revisions|decisions|(?:sections|items|coverage)\/[^/?]+\/(?:classification|decisions)))?)?)|checks\/[^/?]+(?:\/findings\/[^/?]+\/decisions)?)$/;
const REQUIREMENT_REVIEW_PATH = /^\/(?:tasks\/[^/?]+\/(?:extractions\/[^/?]+\/(?:requirement-reviews|rejected-items|requirement-confirmations)|requirements\/(?:manual-preview|manual|repair))|requirements\/[^/?]+\/(?:review|review-history|review-decisions))$/;
const FEATURE_MANAGEMENT_PATH = /^\/management\/resources\/features(?:\/query|\/[^/?]+(?:\/history\/query|\/lifecycle(?:\/history\/query)?)?)$/;
const FEATURE_WRITE_PATH = /^\/resources\/features(?:\/[^/?]+\/revisions)?$/;
const PRODUCT_MANAGEMENT_PATH = /^\/management\/resources\/products(?:\/query|\/[^/?]+(?:\/history\/query|\/lifecycle(?:\/history\/query)?)?)$/;
const QUALIFICATION_MANAGEMENT_PATH = /^\/management\/resources\/(?:certificates|profiles)(?:\/query|\/[^/?]+(?:\/history\/query|\/lifecycle(?:\/history\/query)?)?)$/;
const PRODUCT_WRITE_PATH = /^\/resources\/products(?:\/[^/?]+\/revisions)?$/;
const TEMPLATE_MANAGEMENT_PATH = /^\/management\/(?:resources\/templates(?:\/query|\/[^/?]+(?:\/history\/query|\/lifecycle(?:\/history\/query)?)?)|export-bindings(?:\/query|\/[^/?]+))$/;
const TEMPLATE_WRITE_PATH = /^\/resources\/templates(?:\/[^/?]+\/revisions|\/revisions\/[^/?]+\/(?:download-link|download))?$/;
const PROVIDER_MANAGEMENT_PATH = /^\/management\/providers(?:\/revisions\/[^/?]+|\/history\/query)?$/;
const PROVIDER_WRITE_PATH = /^\/providers(?:\/test)?$/;
const TEMPLATE_TASK_PATH = /^\/tasks\/[^/?]+\/templates$/;
const TEMPLATE_BINDING_PATH = /^\/export-template-bindings$/;
const MEMORY_PATH = /^\/(?:management\/memories(?:\/query|\/[^/?]+(?:\/history\/query)?)|memories(?:\/[^/?]+(?:\/decisions|\/disable)?)?|tasks\/[^/?]+\/(?:memory-feedback|memory-candidates))$/;
const CONFIDENTIAL_MANAGEMENT_PATH = /^\/management\/(?:confidential-fields\/(?:query|[^/?]+\/values(?:\/history\/query)?)|confidential-values\/query)$/;
const ANNOTATION_PATH = /^\/(?:screenshot-renditions\/[^/?]+\/(?:preview-link|content)|tasks\/[^/?]+\/annotations|annotations\/[^/?]+(?:\/(?:preview|releases|content))?|annotation-releases\/[^/?]+\/(?:preview|content))$/;
const ATTACHMENT_PATH = /^\/(?:management\/resources\/attachments\/query|resources\/attachments(?:\/[^/?]+(?:\/(?:revisions|deactivate))?|\/revisions\/[^/?]+(?:\/(?:reviews|file\/(?:download-link|download)|parts\/\d+\/(?:download-link|download)|pages\/\d+\/(?:preview-link|preview)))?)?|resources\/profiles\/revisions\/[^/?]+\/attachments|profile-attachment-links\/[^/?]+\/deactivate|tasks\/[^/?]+\/(?:attachments|attachment-sources)|task-attachments\/[^/?]+\/deactivate|attachment-sources\/[^/?]+(?:\/(?:privacy|preview\/(?:download-link|download)))?)$/;
const CONSOLE_PATH = /^\/(tokens|tasks(?:\/[^/?]+(?:\/(?:progress|board|activity|events(?:\/poll)?|documents|jobs|extractions|requirements|products|features|certificates|profiles|certificate-files|evidence-sources|cards(?:\/(?:dispositions|generations))?|drafts|exports|product-simulations|simulated-resources|model-redaction))?)?|documents\/[^/?]+(?:\/(?:chunks|parse|extract|download-link|download|pages\/\d+\/preview))?|exports\/[^/?]+(?:\/(?:download-link|download|preview(?:\/pages\/\d+)?))?|jobs\/[^/?]+(?:\/cancel)?|cards\/[^/?]+(?:\/(?:actions|classification))?|drafts\/[^/?]+|resources\/(?:products|features|certificates|profiles)(?:\/revisions\/[^/?]+\/file\/(?:download-link|download|pages\/\d+\/preview))?|evidence-sources\/[^/?]+\/preview\/(?:download-link|download)|resources\/profiles\/[^/?]+\/revisions|resources\/certificates\/(?:files|[^/?]+\/(?:revisions|file-revisions))|confidential-fields(?:\/[^/?]+\/(?:revisions|values))?|confidential-values(?:\/[^/?]+\/reveal)?)$/;
const ALLOWED_PATHS = [CONSOLE_PATH, BID_REVIEW_PATH, ATTACHMENT_PATH, MEMORY_PATH, CONFIDENTIAL_MANAGEMENT_PATH, ANNOTATION_PATH, QUALIFICATION_MANAGEMENT_PATH, PRODUCT_MANAGEMENT_PATH, PRODUCT_WRITE_PATH, FEATURE_MANAGEMENT_PATH, FEATURE_WRITE_PATH, TEMPLATE_MANAGEMENT_PATH, PROVIDER_MANAGEMENT_PATH, PROVIDER_WRITE_PATH, TEMPLATE_WRITE_PATH, TEMPLATE_TASK_PATH, TEMPLATE_BINDING_PATH, ASSESSMENT_PATH, REQUIREMENT_REVIEW_PATH, SETTINGS_PATH];
function checkedPath(path) {
  if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//") || path.includes("\\")) throw new ApiError(0, "invalid_path", "请求地址不受支持");
  const url = new URL(path, window.location.origin);
  if (url.origin !== window.location.origin || url.hash || (!PUBLIC.has(url.pathname) && url.pathname !== "/auth/session" && !ALLOWED_PATHS.some((pattern) => pattern.test(url.pathname)))) throw new ApiError(0, "invalid_path", "请求地址不受支持");
  return url.pathname;
}
function retryDelay(value) {
  if (!value) return 0;
  return /^\d+$/.test(value) ? Number(value) * 1000 : Math.max(0, Date.parse(value) - Date.now()) || 0;
}
async function parseResult(response, method, pathname) {
  if (response.status === 401 && !isExchange(method, pathname)) {
    session.clear();
    window.dispatchEvent(new Event("bid:signed-out"));
  }
  let payload;
  try { payload = await response.json(); }
  catch { throw new ApiError(response.status, "invalid_response", "服务返回了无法识别的内容", null, retryDelay(response.headers.get("Retry-After"))); }
  const keys = ["ok", "command", "data", "items", "warnings", "cost", "duration_ms"];
  if (!payload || typeof payload !== "object" || !payload.data || typeof payload.data !== "object" || Array.isArray(payload.data) || typeof payload.ok !== "boolean" || typeof payload.command !== "string" || !payload.command || keys.some((key) => !(key in payload)) || Object.keys(payload).length !== keys.length || !Array.isArray(payload.items) || !Array.isArray(payload.warnings) || !payload.warnings.every(item => typeof item === "string") || !Number.isInteger(payload.duration_ms) || payload.duration_ms < 0) throw new ApiError(response.status, "invalid_response", "服务响应不符合 Result 契约");
  const cost = payload.cost, fields = ["calls", "llm_tokens", "ocr_pages", "search_requests", "browser_units", "unresolved_calls"];
  if (!cost || typeof cost !== "object" || Object.keys(cost).length !== fields.length || !fields.every(field => Number.isInteger(cost[field]) && cost[field] >= 0)) throw new ApiError(response.status, "invalid_response", "服务用量响应不符合 Result 5.0 契约");
  const assessmentRead = ["check show", "score show", "score rubric show"].includes(payload.command) &&
    (payload.data.report?.completion === "partial" || payload.data.completion === "partial" ||
      ["unavailable", "range_only"].includes(payload.data.total_status) ||
      (typeof payload.data.snapshot === "string" && typeof payload.data.parent_id === "string" && Number.isInteger(payload.data.returned)));
  const assessmentHistory = ["check list", "score list", "score rubric list"].includes(payload.command) && typeof payload.data.task_id === "string" && Number.isInteger(payload.data.total) && payload.items.length > 0 && payload.items.every(item => typeof (item.report?.id ?? item.id) === "string") && payload.items.some(item => item.report?.completion === "partial" || ["unavailable", "range_only"].includes(item.total_status));
  const assessmentJob = payload.command === "job status" && ["check", "score_rubric", "score", "annotation_render", "annotation_release", "memory_candidate", "bid_review_prepare", "bid_review", "bid_review_report"].includes(payload.data.kind) &&
    ((payload.data.status === "succeeded" && payload.data.result?.completion === "partial") ||
      (payload.data.status === "cancelled" && (payload.data.error === null || payload.data.error?.code)) ||
      (payload.data.status === "failed" && payload.data.error?.code && [2, 3, 4, 5].includes(payload.data.error.exit_code)));
  const reviewRead = payload.command === "review show" && payload.data.run?.completion === "partial" && typeof payload.data.run.id === "string" && Array.isArray(payload.data.obligations) && Array.isArray(payload.data.signing_requirements);
  const reportRead = ["review report show", "review report list"].includes(payload.command) && payload.data.completion === "partial" && typeof payload.data.review_id === "string";
  const partial = (!payload.data.error && (assessmentRead || assessmentHistory || reviewRead || reportRead)) || assessmentJob || (payload.command === "draft show" && payload.data.completion === "partial") || (payload.command === "job status" && payload.data.result?.completion === "partial" && ["draft", "card_generate", "memory_candidate"].includes(payload.data.kind));
  if (!response.ok || (!payload.ok && !partial)) {
    const error = payload.data?.error ?? payload.data ?? {};
    throw new ApiError(response.status, error.code ?? "invalid_response", error.message ?? "服务返回了未约定的失败结果", payload, retryDelay(response.headers.get("Retry-After")));
  }
  return payload;
}
export async function request(method, path, body, { signal, binary = false, providerWrite = false } = {}) {
  const pathname = checkedPath(path);
  const headers = {};
  const current = session.get();
  if (!current && !PUBLIC.has(pathname) && !isExchange(method, pathname)) throw new ApiError(401, "session_required", "请先输入访问密钥");
  const epoch = sessionEpoch;
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) controller.abort();
  if (current && !isExchange(method, pathname)) headers.Authorization = `Bearer ${current}`;
  inflight.add(controller);
  const multipart = body instanceof FormData;
  if (body !== undefined && !multipart) headers["Content-Type"] = "application/json";
  try {
    const response = await fetch(path, { method, headers, credentials: "omit", cache: "no-store", redirect: "error", signal: controller.signal, body: body === undefined ? undefined : multipart ? body : JSON.stringify(body) });
    const value = providerWrite ? await discardProviderWrite(response) : binary && response.ok ? await response.blob() : await parseResult(response, method, pathname);
    if (controller.signal.aborted || epoch !== sessionEpoch) throw new DOMException("会话已改变", "AbortError");
    return value;
  } finally { inflight.delete(controller); signal?.removeEventListener("abort", abort); }
}
export const count = (value) => Number(value ?? 0).toLocaleString("zh-CN");
// The desktop shell opens /app/#key=...; the key is read once and the fragment leaves history before any navigation.
export function takeAccessKeyFragment() {
  const match = /^#key(?:=(.*))?$/.exec(window.location.hash);
  if (match === null) return null;
  history.replaceState(history.state, "", `${window.location.pathname}${window.location.search}`);
  try { return decodeURIComponent(match[1] ?? ""); }
  catch { return ""; }
}
export async function exchangeAccessKey(key) {
  const result = await request("POST", "/auth/session", { access_key: key });
  if (typeof result.data.session !== "string" || !result.data.session || !Number.isInteger(result.data.expires_in) || result.data.expires_in <= 0) throw new ApiError(502, "invalid_response", "会话响应不符合契约");
  session.set(result.data.session);
}

// A fetch stream carries the same session and cancellation epoch as Result reads.
export async function eventStream(path, cursor, signal, onMessage) {
  const pathname = checkedPath(path), current = session.get(), epoch = sessionEpoch;
  if (!current) throw new ApiError(401, "session_required", "请先输入访问密钥");
  const controller = new AbortController(), abort = () => controller.abort();
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) abort();
  inflight.add(controller);
  let reader;
  try {
    const headers = { Authorization: `Bearer ${current}`, Accept: "text/event-stream" };
    if (cursor) headers["Last-Event-ID"] = cursor;
    const response = await fetch(path, { headers, credentials: "omit", cache: "no-store", redirect: "error", signal: controller.signal });
    if (!response.ok) await parseResult(response, "GET", pathname);
    if (!response.headers.get("Content-Type")?.startsWith("text/event-stream") || !response.body) throw new ApiError(502, "invalid_event_stream", "进度流格式不符合契约");
    reader = response.body.getReader();
    const decoder = new TextDecoder(), encoder = new TextEncoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (controller.signal.aborted || sessionEpoch !== epoch) throw new DOMException("会话已改变", "AbortError");
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
        if (controller.signal.aborted || sessionEpoch !== epoch) throw new DOMException("会话已改变", "AbortError");
        await onMessage(payload);
      }
    }
  } finally {
    await reader?.cancel().catch(() => {});
    inflight.delete(controller); signal?.removeEventListener("abort", abort);
  }
}

// This write channel discards the legacy suffix-bearing receipt, including all
// arbitrary server error text. The page rereads only management metadata.
async function discardProviderWrite(response) {
  let payload;
  try { payload = await response.json(); } catch { throw new ApiError(response.status, "invalid_response", "保存响应无法核验，请重新读取配置"); }
  if (response.ok && payload?.ok === true) {
    if (typeof payload.data?.id !== "string" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(payload.data.id) || !Number.isInteger(payload.data.revision) || payload.data.revision < 1) throw new ApiError(response.status, "invalid_response", "保存响应无法核验，请重新读取配置");
    return { id: payload.data.id, revision: payload.data.revision };
  }
  const code = payload?.data?.error?.code;
  const messages = { revision_conflict: "修订已变化，请核对当前配置", provider_key_required: "首次设置或更换供应商、端点需输入新密钥", invalid_input: "配置参数无效", forbidden: "当前身份无权维护配置", human_required: "需要本人登录操作", not_found: "模型不可访问", session_required: "请重新输入访问密钥" };
  if (response.status === 401) { session.clear(); window.dispatchEvent(new Event("bid:signed-out")); }
  throw new ApiError(response.status, Object.hasOwn(messages, code) ? code : "provider_save_failed", messages[code] ?? "保存结果无法核验，请重新读取配置");
}
