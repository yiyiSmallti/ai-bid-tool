import { ApiError, orgSession } from "./api.js";
import { orgAccess, orgRequest } from "./org.js";
import { productReader, productTaskAuthority, validId } from "./products.js";

export const confidentialReader = productReader;
export const confidentialMaintainer = () => ["admin", "bidder"].includes(orgAccess.role);
export const confidentialKinds = { amount: "金额", contact: "联系人 / 电话", identity: "证件号", bank_account: "银行账号", other: "其他" };
export const confidentialScopes = { org: "全单位共用", task: "每个任务单独填写" };
export const blankConfidentialField = () => ({ key: "", label: "", kind: "amount", scope: "task" });
const fieldKeys = ["id", "key", "placeholder", "label", "kind", "scope", "archived", "revision", "created_at"];
const valueKeys = ["field_id", "key", "placeholder", "label", "kind", "scope", "task_id", "status", "value_id", "version", "tail", "set_by", "set_at"];
const invalid = () => { throw new ApiError(502, "invalid_response", "保密数据范围或响应边界不符合契约"); };
const exact = (item, keys) => item && typeof item === "object" && Object.keys(item).length === keys.length && keys.every(key => Object.hasOwn(item, key));
const time = value => typeof value === "string" && Number.isFinite(Date.parse(value));
function base(item) {
  if (!/^[a-z][a-z0-9_]{1,47}$/.test(item?.key) || item.placeholder !== `{{secret.${item.key}}}` || typeof item.label !== "string" || !item.label.trim() || item.label.length > 100 || !Object.hasOwn(confidentialKinds, item.kind) || !Object.hasOwn(confidentialScopes, item.scope)) invalid();
}
export function checkConfidentialField(item, id = null) {
  if (!exact(item, fieldKeys) || !validId(item.id) || id && item.id !== id || typeof item.archived !== "boolean" || !Number.isInteger(item.revision) || item.revision < 1 || !time(item.created_at)) invalid();
  base(item); return item;
}
export function checkConfidentialValue(item, taskId = null, fieldId = null, field = null) {
  if (!exact(item, valueKeys) || !validId(item.field_id) || fieldId && item.field_id !== fieldId || !["filled", "missing"].includes(item.status) || item.task_id !== (item.scope === "task" ? taskId : null) || item.scope === "task" && !validId(taskId)) invalid();
  base(item);
  if (field && ["key", "placeholder", "kind", "scope"].some(key => item[key] !== field[key])) invalid();
  if (item.status === "filled") {
    if (!validId(item.value_id) || !Number.isInteger(item.version) || item.version < 1 || !time(item.set_at) || !(item.set_by === null || validId(item.set_by))) invalid();
    if (!(item.tail === null || ["identity", "bank_account", "contact"].includes(item.kind) && typeof item.tail === "string" && [...item.tail].length === 4 && /^[\p{L}\p{N}]+$/u.test(item.tail))) invalid();
  } else if (["value_id", "version", "tail", "set_by", "set_at"].some(key => item[key] !== null)) invalid();
  return item;
}
export function checkConfidentialPage(result, kind, taskId = null, fieldId = null) {
  const { data, items } = result;
  if (data.org_id !== orgSession.get()?.orgId || data.returned !== items.length || items.length > 25 || typeof data.has_more !== "boolean" || data.has_more !== (data.next_cursor !== null) || data.next_cursor !== null && (typeof data.next_cursor !== "string" || !data.next_cursor || data.next_cursor.length > 2048) || !time(data.as_of) || new TextEncoder().encode(JSON.stringify(result)).length > 256 * 1024) invalid();
  const seen = new Set();
  for (const item of items) {
    if (kind === "fields") checkConfidentialField(item); else checkConfidentialValue(item, taskId, fieldId);
    const id = kind === "fields" ? item.id : kind === "history" ? item.value_id : item.field_id;
    if (seen.has(id) || kind === "history" && item.status !== "filled") invalid();
    seen.add(id);
  }
  return result;
}
export async function confidentialAuthority(reader, taskId) {
  if (taskId) return productTaskAuthority(reader, taskId);
  const result = await reader.read("authority", "GET", "/org/current");
  if (result.data.org_id !== orgSession.get()?.orgId || !validId(result.data.user_id) || !["admin", "bidder", "technical", "viewer"].includes(result.data.role)) invalid();
  orgAccess.role = result.data.role; orgAccess.userId = result.data.user_id;
  return null;
}
// Secret transports construct the single transient body explicitly. Do not pass a
// whole reactive form or serialize it into navigation, telemetry or persistent state.
export const submitConfidentialValue = (fieldId, value, taskId, revision, valueId) => orgRequest("POST", `/management/confidential-fields/${fieldId}/values`, { value, task_id: taskId, expected_field_revision: revision, expected_value_id: valueId }, { contractVersion: 4 });
export function checkConfidentialReveal(item, value) {
  if (!exact(item, ["field_id", "value_id", "key", "value"]) || item.field_id !== value.field_id || item.value_id !== value.value_id || item.key !== value.key || typeof item.value !== "string" || !item.value || item.value.length > 2000) invalid();
  return item;
}
