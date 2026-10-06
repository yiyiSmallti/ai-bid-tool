import { ApiError, orgSession } from "./api.js";
import { productMaintainer, productReader, productTaskAuthority, rememberProductCursor, validId } from "./products.js";
export { validId, reasonLabels } from "./products.js";
export const featurePath = "/management/resources/features";
export const featureMaintainer = productMaintainer;
export const featureReader = productReader;
export const featureTaskAuthority = productTaskAuthority;
export const rememberFeatureCursor = rememberProductCursor;
export const implementationLabels = { implemented: "已实现", developing: "开发中", planned: "计划中" };
export const blankFeature = () => ({product_id: "", name: "", description: "", status: "planned"});
const validFeatureData = value => validId(value.product_id) && ["name", "description"].every(key => typeof value[key] === "string" && value[key].trim()) && value.name.length <= 200 && value.description.length <= 10000 && Object.hasOwn(implementationLabels, value.status);
const invalid = message => { throw new ApiError(502, "invalid_response", message); };
const revision = value => Number.isInteger(value) && value > 0;
function checkRef(value, id) { if (value?.kind !== "features" || !validId(value.resource_id) || id && value.resource_id !== id) invalid("功能范围不符合契约"); }
function checkLifecycle(value) { if (!value || !["active", "inactive"].includes(value.state) || !Number.isInteger(value.revision) || value.revision < 0 || value.revision === 0 && value.state !== "active") invalid("功能生命周期不符合契约"); }
function checkActions(actions) { if (!Array.isArray(actions) || actions.length > 16 || actions.some(item => typeof item.action !== "string" || typeof item.allowed !== "boolean" || item.allowed === (item.reason != null))) invalid("功能操作提示不符合契约"); }
export function checkFeatureRevision(value, id, number) {
  if (!value || value.org_id !== orgSession.get()?.orgId || !validId(value.id) || !validId(value.feature_id) || id && value.feature_id !== id || !revision(value.revision) || number && value.revision !== number || !value.data || !validFeatureData(value.data)) invalid("功能修订范围不符合契约");
  return value;
}
export function checkFeatureDetail(value, id, number) {
  if (value?.org_id !== orgSession.get()?.orgId || value.detail?.kind !== "features" || !revision(value.current_revision) || !["declared", "simulated"].includes(value.provenance) || !Array.isArray(value.actions)) invalid("功能详情范围不符合契约");
  if (typeof value.revised_at !== "string" || !Number.isFinite(Date.parse(value.revised_at)) || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value.revised_at) || !(value.revised_by === null || validId(value.revised_by))) invalid("功能精确修订的作者或时间不符合契约");
  checkRef(value.ref, id); checkLifecycle(value.lifecycle); checkActions(value.actions); checkFeatureRevision(value.detail.revision, id, number);
  if (value.detail.revision.revision > value.current_revision || new TextEncoder().encode(JSON.stringify(value)).length > 1024 * 1024) invalid("功能详情超过契约边界");
  return value;
}
export function checkFeaturePage(result, kind="list", id=null) {
  const {data,items}=result;
  if (data.org_id !== orgSession.get()?.orgId || !Number.isInteger(data.returned) || data.returned !== items.length || items.length > 100 || typeof data.has_more !== "boolean" || data.has_more !== (data.next_cursor !== null) || data.next_cursor !== null && (typeof data.next_cursor !== "string" || !data.next_cursor || data.next_cursor.length > 2048) || !data.as_of || !Number.isFinite(Date.parse(data.as_of))) invalid("功能分页范围不符合契约");
  if (new TextEncoder().encode(JSON.stringify(result)).length > 256 * 1024) invalid("功能分页结果超过契约边界");
  for (const item of items) {
    if (item.org_id !== data.org_id || !revision(item.revision)) invalid("功能分页范围不符合契约");
    checkRef(item.ref,id);
    if (kind === "list") { checkLifecycle(item.lifecycle); checkActions(item.actions); if (!validId(item.revision_id) || !["declared","simulated"].includes(item.provenance) || !Array.isArray(item.actions)) invalid("功能列表范围不符合契约"); }
    else if (kind === "history" && !validId(item.revision_id)) invalid("功能历史范围不符合契约");
    else if (kind === "lifecycle" && (!validId(item.id) || !["active","inactive"].includes(item.before) || !["active","inactive"].includes(item.after) || item.before === item.after)) invalid("功能生命周期历史范围不符合契约");
  }
  return result;
}
