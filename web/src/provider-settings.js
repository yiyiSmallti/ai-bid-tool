import { ApiError, request } from "./api.js";

export const providerPath = "/management/providers";
const configFields = [
  "capability", "provider", "model", "base_url",
  "json_mode", "reasoning", "default_reasoning", "expected_revision",
];
const metadataFields = [
  "id", "revision", "configuration", "provider", "model", "credential_state",
  "updated_at", "revised_at", "reasoning", "default_reasoning",
];
const settingsFields = [
  "capability", "configured", "current", "reasoning", "actions", "connection_status",
];
const levelFields = ["name", "label", "request_options", "effort", "batch_chars"];
const actionReasons = ["human_required", "provider_unavailable"];

export const validId = value => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const object = value => value && typeof value === "object" && !Array.isArray(value);
const keys = (value, fields) => object(value) && Object.keys(value).length === fields.length && fields.every(field => Object.hasOwn(value, field));
const bounded = (value, size) => new TextEncoder().encode(JSON.stringify(value)).length <= size;
const time = value => typeof value === "string" && Number.isFinite(Date.parse(value)) && /(?:Z|[+-]\d{2}:\d{2})$/.test(value);
const revision = value => Number.isInteger(value) && value > 0;
const model = value => typeof value === "string" && value.length > 0 && value.length <= 100;
const provider = value => ["anthropic", "openai"].includes(value);
const levelName = value => typeof value === "string" && /^[a-z0-9_-]{1,20}$/.test(value);
function invalid() { throw new ApiError(502, "invalid_response", "模型元数据范围不符合契约"); }

function choices(value, defaultName) {
  if (!Array.isArray(value) || value.length > 8) invalid();
  const names = new Set();
  for (const level of value) {
    if (!keys(level, ["name", "label"]) || !levelName(level.name) || names.has(level.name) ||
      !(level.label === null || typeof level.label === "string" && level.label.length <= 100)) invalid();
    names.add(level.name);
  }
  if (defaultName !== undefined && !(defaultName === null && !value.length || names.has(defaultName))) invalid();
}
function safeEndpoint(value) {
  if (value === null) return true;
  if (typeof value !== "string" || value.length > 300 || /[\s\\\x00-\x1f]/.test(value)) return false;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !!url.hostname && !url.username && !url.password && !url.search && !url.hash;
  } catch { return false; }
}
function editableReasoning(value) {
  if (!Array.isArray(value) || value.length > 8) invalid();
  for (const level of value) {
    if (!keys(level, levelFields) || !levelName(level.name) ||
      !(level.label === null || typeof level.label === "string" && level.label.length <= 60) ||
      !object(level.request_options) ||
      !(level.effort === null || typeof level.effort === "string" && /^[a-z]{1,10}$/.test(level.effort)) ||
      !Number.isInteger(level.batch_chars) || level.batch_chars < 1000 || level.batch_chars > 200000) invalid();
  }
}

export function checkProviderMetadata(value, id = null) {
  if (!keys(value, metadataFields) ||
    !validId(value.id) || id && value.id !== id || !revision(value.revision) ||
    !provider(value.provider) || !model(value.model) || value.credential_state !== "configured" ||
    !time(value.updated_at) || !time(value.revised_at) ||
    !bounded(value, 1024 * 1024)) invalid();
  const config = value.configuration;
  if (!keys(config, configFields) || config.capability !== "llm_extract" ||
    config.expected_revision !== null || config.provider !== value.provider ||
    config.model !== value.model || !safeEndpoint(config.base_url) ||
    !["json_schema", "json_object"].includes(config.json_mode)) invalid();
  editableReasoning(config.reasoning);
  choices(value.reasoning, value.default_reasoning);
  return value;
}
export function checkProviderSettings(value) {
  if (!keys(value, settingsFields) ||
    value.capability !== "llm_extract" || value.connection_status !== "not_checked" ||
    typeof value.configured !== "boolean" || value.configured !== (value.current !== null) ||
    !Array.isArray(value.actions) ||
    value.actions.length > 16 || !bounded(value, 1024 * 1024)) invalid();
  if (value.current !== null) checkProviderMetadata(value.current);
  choices(value.reasoning);
  if (value.actions.length !== 2 || new Set(value.actions.map(item => item.action)).size !== 2) invalid();
  for (const hint of value.actions) {
    if (!keys(hint, ["action", "allowed", "reason"]) || !["configure", "test"].includes(hint.action) ||
      typeof hint.allowed !== "boolean" || (hint.allowed ? hint.reason !== null : !actionReasons.includes(hint.reason))) invalid();
  }
  return value;
}
export function checkProviderPage(response) {
  const { data, items } = response;
  if (!keys(data, ["as_of", "returned", "next_cursor", "has_more"]) || data.returned !== items.length ||
    items.length > 25 || !time(data.as_of) || typeof data.has_more !== "boolean" ||
    data.has_more !== (data.next_cursor !== null) ||
    data.next_cursor !== null && (typeof data.next_cursor !== "string" || !data.next_cursor || data.next_cursor.length > 2048) ||
    !bounded(response, 256 * 1024)) invalid();
  items.forEach(item => checkProviderMetadata(item));
  return response;
}

export const blankProvider = () => ({
  provider: "openai", model: "", base_url: "",
  json_mode: "json_schema", reasoning: [], default_reasoning: null,
});
// Construct an input from named editable fields. Never spread a read projection.
export function providerInput(form, expectedRevision) {
  return {
    capability: "llm_extract", provider: form.provider,
    model: form.model.trim(), base_url: form.base_url.trim() || null, json_mode: form.json_mode,
    reasoning: form.reasoning.map(level => ({
      name: level.name, label: level.label || null, request_options: level.request_options,
      effort: level.effort || null, batch_chars: level.batch_chars,
    })),
    default_reasoning: form.default_reasoning,
    expected_revision: expectedRevision,
  };
}
// A credential never becomes component state or a read-view serialization. The
// password DOM value is consumed once, cleared before fetch, and the temporary
// wire object is cleared as soon as request has serialized its body.
export function submitProviderConfiguration(fields, control, options = {}) {
  const wire = {
    capability: "llm_extract", expected_revision: fields.expected_revision,
    provider: fields.provider, model: fields.model, base_url: fields.base_url, json_mode: fields.json_mode,
    reasoning: fields.reasoning, default_reasoning: fields.default_reasoning,
  };
  try {
    if (control?.value) wire.api_key = control.value;
    if (control) control.value = "";
    return request("POST", "/providers", wire, { ...options, providerWrite: true });
  } finally { if (control) control.value = ""; delete wire.api_key; }
}

export function providerReader() {
  const channels = new Map(), pending = [];
  let active = 0, stopped = false;
  function pump() {
    while (!stopped && active < 2 && pending.length) {
      const job = pending.shift();
      if (job.controller.signal.aborted) {
        job.reject(new DOMException("读取已取消", "AbortError"));
        continue;
      }
      active++;
      job.execute().then(job.resolve, job.reject).finally(() => { active--; pump(); });
    }
  }
  function read(channel, method, path, body) {
    channels.get(channel)?.abort();
    const controller = new AbortController();
    channels.set(channel, controller);
    return new Promise((resolve, reject) => {
      pending.push({ controller, resolve, reject, execute: () => request(method, path, body, { signal: controller.signal}) });
      pump();
    });
  }
  function stop() {
    stopped = true;
    for (const controller of channels.values()) controller.abort();
    for (const job of pending.splice(0)) job.reject(new DOMException("读取已取消", "AbortError"));
  }
  return { read, stop, cancel: channel => channels.get(channel)?.abort() };
}
// Never display an arbitrary validation/vendor error body from a secret write/test.
export function providerError(error) {
  const text = {
    revision_conflict: "修订已变化，草稿已保留；请重新读取后核对并主动保存。",
    provider_key_required: "首次设置或更换供应商、端点需要输入新密钥。",
    forbidden: "当前身份无权维护配置。", human_required: "需要本人登录后操作。",
    invalid_input: "配置参数无效，请核对非密钥字段并重新输入新密钥。",
    provider_unavailable: "模型服务不可用。",
    management_cursor_invalid: "分页已失效，请从第一页读取。",
    management_cursor_expired: "分页已过期，请从第一页读取。",
    invalid_response: "模型元数据范围不符合契约。",
    provider_save_failed: "保存结果无法核验，请重新读取配置。",
  };
  return error.status === 404 ? "模型或修订不可访问。" : text[error.code] ?? "操作结果无法核验，请主动重新读取。不会自动重试。";
}
