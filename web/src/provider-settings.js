import { ApiError, orgSession } from "./api.js";
import { orgRequest } from "./org.js";

export const providerPath = "/management/providers";
const configFields = [
  "capability", "source", "platform_model_id", "provider", "model", "base_url",
  "json_mode", "reasoning", "default_reasoning", "input_usd_per_mtok",
  "output_usd_per_mtok", "expected_revision",
];
const metadataFields = [
  "id", "org_id", "revision", "configuration", "provider", "model", "catalog_state",
  "credential_state", "updated_by", "updated_at", "revised_at", "revised_by",
  "reasoning", "default_reasoning", "catalog_revision", "sale_input_per_mtok",
  "sale_output_per_mtok",
];
const choiceFields = [
  "id", "revision", "provider", "model", "sale_input_per_mtok",
  "sale_output_per_mtok", "default", "reasoning", "default_reasoning",
];
const settingsFields = [
  "org_id", "capability", "effective_source", "current", "default_model",
  "billing_currency", "reasoning", "actions", "connection_status",
];
const levelFields = ["name", "label", "request_options", "effort", "batch_chars"];
const actionReasons = ["role_required", "human_required", "provider_unavailable"];

export const validId = value => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const validCatalogId = value => typeof value === "string" && /^[a-z0-9][a-z0-9_-]{0,39}$/.test(value);
const object = value => value && typeof value === "object" && !Array.isArray(value);
const keys = (value, fields) => object(value) && Object.keys(value).length === fields.length && fields.every(field => Object.hasOwn(value, field));
const bounded = (value, size) => new TextEncoder().encode(JSON.stringify(value)).length <= size;
const time = value => typeof value === "string" && Number.isFinite(Date.parse(value)) && /(?:Z|[+-]\d{2}:\d{2})$/.test(value);
const revision = value => Number.isInteger(value) && value > 0;
const price = value => value === null || typeof value === "number" && Number.isFinite(value) && value >= 0;
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

export function checkModelChoice(value) {
  if (!keys(value, choiceFields) || !validCatalogId(value.id) || !revision(value.revision) ||
    !provider(value.provider) || !model(value.model) || !price(value.sale_input_per_mtok) ||
    value.sale_input_per_mtok === null || !price(value.sale_output_per_mtok) ||
    value.sale_output_per_mtok === null || typeof value.default !== "boolean") invalid();
  choices(value.reasoning, value.default_reasoning);
  return value;
}
export function checkProviderMetadata(value, id = null) {
  if (!keys(value, metadataFields) || value.org_id !== orgSession.get()?.orgId ||
    !validId(value.id) || id && value.id !== id || !revision(value.revision) ||
    !provider(value.provider) || !model(value.model) || !validId(value.updated_by) ||
    !time(value.updated_at) || !time(value.revised_at) ||
    !(value.revised_by === null || validId(value.revised_by)) ||
    !price(value.sale_input_per_mtok) || !price(value.sale_output_per_mtok) ||
    !(value.catalog_revision === null || revision(value.catalog_revision)) ||
    !bounded(value, 1024 * 1024)) invalid();
  const config = value.configuration;
  if (!keys(config, configFields) || config.capability !== "llm_extract" ||
    config.expected_revision !== null || !["platform", "org"].includes(config.source) ||
    !["json_schema", "json_object"].includes(config.json_mode)) invalid();
  editableReasoning(config.reasoning);
  if (config.source === "platform") {
    if (!validCatalogId(config.platform_model_id) || config.provider !== null ||
      config.model !== null || config.base_url !== null || config.input_usd_per_mtok !== null ||
      config.output_usd_per_mtok !== null || config.default_reasoning !== null ||
      config.reasoning.length || !["enabled", "unavailable"].includes(value.catalog_state) ||
      value.credential_state !== "platform_managed") invalid();
  } else if (config.provider !== value.provider || config.model !== value.model ||
    config.platform_model_id !== null || value.catalog_state !== "not_applicable" ||
    value.credential_state !== "configured" || !safeEndpoint(config.base_url) ||
    !price(config.input_usd_per_mtok) || !price(config.output_usd_per_mtok) ||
    value.catalog_revision !== null || value.sale_input_per_mtok !== null || value.sale_output_per_mtok !== null) invalid();
  choices(value.reasoning, value.default_reasoning);
  return value;
}
export function checkProviderSettings(value) {
  if (!keys(value, settingsFields) || value.org_id !== orgSession.get()?.orgId ||
    value.capability !== "llm_extract" || value.connection_status !== "not_checked" ||
    !/^[A-Z]{3}$/.test(value.billing_currency) || !Array.isArray(value.actions) ||
    value.actions.length > 16 || !bounded(value, 1024 * 1024)) invalid();
  if (value.current !== null) checkProviderMetadata(value.current);
  if (value.default_model !== null) checkModelChoice(value.default_model);
  if (value.current !== null && value.default_model !== null) invalid();
  const source = value.current?.configuration.source ?? (value.default_model ? "platform" : "unconfigured");
  if (value.effective_source !== source) invalid();
  choices(value.reasoning);
  if (value.actions.length !== 2 || new Set(value.actions.map(item => item.action)).size !== 2) invalid();
  for (const hint of value.actions) {
    if (!keys(hint, ["action", "allowed", "reason"]) || !["configure", "test"].includes(hint.action) ||
      typeof hint.allowed !== "boolean" || (hint.allowed ? hint.reason !== null : !actionReasons.includes(hint.reason))) invalid();
  }
  return value;
}
export function checkProviderPage(response, kind) {
  const { data, items } = response;
  if (data.org_id !== orgSession.get()?.orgId || data.returned !== items.length ||
    items.length > 25 || !time(data.as_of) || typeof data.has_more !== "boolean" ||
    data.has_more !== (data.next_cursor !== null) ||
    data.next_cursor !== null && (typeof data.next_cursor !== "string" || !data.next_cursor || data.next_cursor.length > 2048) ||
    !bounded(response, 256 * 1024)) invalid();
  items.forEach(item => kind === "catalog" ? checkModelChoice(item) : checkProviderMetadata(item));
  return response;
}

export const blankProvider = () => ({
  source: "platform", platform_model_id: "", provider: "openai", model: "", base_url: "",
  json_mode: "json_schema", reasoning: [], default_reasoning: null,
  input_usd_per_mtok: null, output_usd_per_mtok: null,
});
// Construct an input from named editable fields. Never spread a read projection.
export function providerInput(form, expectedRevision) {
  if (form.source === "platform") return {
    capability: "llm_extract", source: "platform",
    platform_model_id: form.platform_model_id, expected_revision: expectedRevision,
  };
  return {
    capability: "llm_extract", source: "org", provider: form.provider,
    model: form.model.trim(), base_url: form.base_url.trim() || null, json_mode: form.json_mode,
    reasoning: form.reasoning.map(level => ({
      name: level.name, label: level.label || null, request_options: level.request_options,
      effort: level.effort || null, batch_chars: level.batch_chars,
    })),
    default_reasoning: form.default_reasoning,
    input_usd_per_mtok: form.input_usd_per_mtok === "" ? null : form.input_usd_per_mtok,
    output_usd_per_mtok: form.output_usd_per_mtok === "" ? null : form.output_usd_per_mtok,
    expected_revision: expectedRevision,
  };
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
      pending.push({ controller, resolve, reject, execute: () => orgRequest(method, path, body, { signal: controller.signal, contractVersion: 4 }) });
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
    forbidden: "当前身份无权维护配置。", human_required: "需要单位管理员本人登录。",
    invalid_input: "配置参数无效，请核对非密钥字段并重新输入新密钥。",
    provider_unavailable: "模型服务不可用。", insufficient_balance: "余额不足，请充值后重新预检。",
    management_cursor_invalid: "分页已失效，请从第一页读取。",
    management_cursor_expired: "分页已过期，请从第一页读取。",
    invalid_response: "模型元数据范围不符合契约。",
    provider_save_failed: "保存结果无法核验，请重新读取配置。",
  };
  return error.status === 404 ? "模型或修订不可访问。" : text[error.code] ?? "操作结果无法核验，请主动重新读取。不会自动重试。";
}
