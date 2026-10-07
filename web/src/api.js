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
const REQUIREMENT_REVIEW_PATH = /^\/(?:tasks\/[^/?]+\/(?:extractions\/[^/?]+\/(?:requirement-reviews|rejected-items|requirement-confirmations)|requirements\/(?:manual-preview|manual|repair))|requirements\/[^/?]+\/(?:review|review-history|review-decisions))$/;
const COSIGN_PATH = /^\/(?:tasks\/[^/?]+\/(?:review-rule|requirements\/[^/?]+\/review-policy)|cards\/[^/?]+\/(?:review-rounds|signoffs))$/;
const COLLABORATION_PATH = /^\/(?:tasks\/[^/?]+\/requirements\/[^/?]+\/assignment|cards\/[^/?]+\/threads(?:\/[^/?]+\/comments)?)$/;
const FEATURE_MANAGEMENT_PATH = /^\/management\/resources\/features(?:\/query|\/[^/?]+(?:\/history\/query|\/lifecycle(?:\/history\/query)?)?)$/;
const FEATURE_WRITE_PATH = /^\/resources\/features(?:\/[^/?]+\/revisions)?$/;
const PRODUCT_MANAGEMENT_PATH = /^\/management\/resources\/products(?:\/query|\/[^/?]+(?:\/history\/query|\/lifecycle(?:\/history\/query)?)?)$/;
const QUALIFICATION_MANAGEMENT_PATH = /^\/management\/resources\/(?:certificates|profiles)(?:\/query|\/[^/?]+(?:\/history\/query|\/lifecycle(?:\/history\/query)?)?)$/;
const PRODUCT_WRITE_PATH = /^\/resources\/products(?:\/[^/?]+\/revisions)?$/;
const TEMPLATE_MANAGEMENT_PATH = /^\/management\/(?:resources\/templates(?:\/query|\/[^/?]+(?:\/history\/query|\/lifecycle(?:\/history\/query)?)?)|export-bindings(?:\/query|\/[^/?]+))$/;
const TEMPLATE_WRITE_PATH = /^\/resources\/templates(?:\/[^/?]+\/revisions|\/revisions\/[^/?]+\/(?:download-link|download))?$/;
const PROVIDER_MANAGEMENT_PATH = /^\/management\/providers(?:\/revisions\/[^/?]+|\/(?:history|catalog)\/query)?$/;
const PROVIDER_WRITE_PATH = /^\/providers(?:\/test)?$/;
const TEMPLATE_TASK_PATH = /^\/tasks\/[^/?]+\/templates$/;
const TEMPLATE_BINDING_PATH = /^\/export-template-bindings$/;
const ANNOTATION_PATH = /^\/(?:screenshot-renditions\/[^/?]+\/(?:preview-link|content)|tasks\/[^/?]+\/annotations|annotations\/[^/?]+(?:\/(?:preview|releases|content))?|annotation-releases\/[^/?]+\/(?:preview|content))$/;
const ORG_PATH = /^\/(org\/current|tasks(?:\/[^/?]+(?:\/(?:workflow|progress|members(?:\/[^/?]+(?:\/remove)?)?|member-candidates|handover|archive|unarchive|board|activity|events(?:\/poll)?|documents|jobs|extractions|requirements|products|features|certificates|profiles|certificate-files|evidence-sources|cards(?:\/(?:dispositions|generations))?|drafts|exports|product-simulations|simulated-resources|model-redaction))?)?|documents\/[^/?]+(?:\/(?:chunks|parse|extract|download-link|download|pages\/\d+\/preview))?|exports\/[^/?]+(?:\/(?:download-link|download|preview(?:\/pages\/\d+)?))?|jobs\/[^/?]+(?:\/cancel)?|cards\/[^/?]+(?:\/(?:actions|classification))?|drafts\/[^/?]+|resources\/(?:products|features|certificates|profiles)(?:\/revisions\/[^/?]+\/file\/(?:download-link|download|pages\/\d+\/preview))?|evidence-sources\/[^/?]+\/preview\/(?:download-link|download)|resources\/profiles\/[^/?]+\/revisions|resources\/certificates\/(?:files|[^/?]+\/(?:revisions|file-revisions))|billing(?:\/redeem)?|confidential-fields(?:\/[^/?]+\/(?:revisions|values))?|confidential-values(?:\/[^/?]+\/reveal)?)$/;
function checkedPath(path, org) {
  if (typeof path !== "string" || !path.startsWith("/") || path.startsWith("//") || path.includes("\\")) throw new ApiError(0, "invalid_path", "请求地址不受支持");
  const url = new URL(path, window.location.origin);
  if (url.origin !== window.location.origin || url.hash || (!PUBLIC.has(url.pathname) && !(org ? ORG_PATH.test(url.pathname) || ANNOTATION_PATH.test(url.pathname) || QUALIFICATION_MANAGEMENT_PATH.test(url.pathname) || PRODUCT_MANAGEMENT_PATH.test(url.pathname) || PRODUCT_WRITE_PATH.test(url.pathname) || FEATURE_MANAGEMENT_PATH.test(url.pathname) || FEATURE_WRITE_PATH.test(url.pathname) || TEMPLATE_MANAGEMENT_PATH.test(url.pathname) || TEMPLATE_WRITE_PATH.test(url.pathname) || TEMPLATE_TASK_PATH.test(url.pathname) || TEMPLATE_BINDING_PATH.test(url.pathname) || PROVIDER_MANAGEMENT_PATH.test(url.pathname) || PROVIDER_WRITE_PATH.test(url.pathname) || ASSESSMENT_PATH.test(url.pathname) || COLLABORATION_PATH.test(url.pathname) || COSIGN_PATH.test(url.pathname) || REQUIREMENT_REVIEW_PATH.test(url.pathname) : url.pathname.startsWith("/platform/")))) throw new ApiError(0, "invalid_path", "请求地址不受支持");
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
  const assessmentJob = payload.command === "job status" && ["check", "score_rubric", "score", "annotation_render", "annotation_release"].includes(payload.data.kind) &&
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
export async function request(method, path, body, { org = false, signal, binary = false, contractVersion, providerWrite = false } = {}) {
  const pathname = checkedPath(path, org);
  // The unversioned API deliberately projects legacy v3 costs. Assessment pages
  // and job receipts require enforced budget preflight and actual Result 4 costs.
  const assessmentJobs = /^\/tasks\/[^/]+\/jobs$/.test(pathname) && ["check", "score_rubric", "score"].includes(new URL(path, window.location.origin).searchParams.get("kind"));
  const apiPath = org && (ANNOTATION_PATH.test(pathname) || QUALIFICATION_MANAGEMENT_PATH.test(pathname) || PRODUCT_MANAGEMENT_PATH.test(pathname) || FEATURE_MANAGEMENT_PATH.test(pathname) || TEMPLATE_MANAGEMENT_PATH.test(pathname) || PROVIDER_MANAGEMENT_PATH.test(pathname) || ASSESSMENT_PATH.test(pathname) || REQUIREMENT_REVIEW_PATH.test(pathname) || assessmentJobs || contractVersion === 4) ? `/v4${path}` : path;
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
    const value = providerWrite ? await discardProviderWrite(response) : binary && response.ok ? await response.blob() : await parseResult(response, pathname, org, apiPath.startsWith("/v4/"));
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
  const messages = { revision_conflict: "修订已变化，请核对当前配置", provider_key_required: "首次设置或更换供应商、端点需输入新密钥", invalid_input: "配置参数无效", forbidden: "当前身份无权维护配置", human_required: "需要管理员本人登录", not_found: "模型不可访问", org_inactive: "单位已停用", session_required: "请重新登录单位" };
  if (response.status === 401 || code === "org_inactive") { orgSession.clear(); window.dispatchEvent(new CustomEvent("bid:signed-out", {detail:"org"})); }
  throw new ApiError(response.status, Object.hasOwn(messages, code) ? code : "provider_save_failed", messages[code] ?? "保存结果无法核验，请重新读取配置");
}
// A credential never becomes component state or a read-view serialization. The
// password DOM value is consumed once, cleared before fetch, and the temporary
// wire object is cleared as soon as request has serialized its body.
export function submitProviderConfiguration(fields, control, options = {}) {
  const wire = {capability:"llm_extract",source:fields.source,expected_revision:fields.expected_revision};
  try {
    if (fields.source === "platform") wire.platform_model_id = fields.platform_model_id;
    else if (fields.source === "org") {
      Object.assign(wire, {provider:fields.provider,model:fields.model,base_url:fields.base_url,json_mode:fields.json_mode,reasoning:fields.reasoning,default_reasoning:fields.default_reasoning,input_usd_per_mtok:fields.input_usd_per_mtok,output_usd_per_mtok:fields.output_usd_per_mtok});
      if (control?.value) wire.api_key = control.value;
    } else throw new ApiError(400,"invalid_input","配置来源无效");
    if (control) control.value = "";
    return request("POST","/providers",wire,{...options,org:true,contractVersion:4,providerWrite:true});
  } finally { if (control) control.value = ""; delete wire.api_key; }
}
