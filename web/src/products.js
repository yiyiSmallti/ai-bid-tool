import { ApiError, orgSession } from "./api.js";
import { orgAccess, orgRequest } from "./org.js";
import { canEditTask } from "./task-authority.js";
export const productPath = "/management/resources/products";
export const PRODUCT_BACKSTACK_LIMIT = 50;
// Retain navigation anchors only; old pages and unbounded cursor history are evicted.
export function rememberProductCursor(stack, cursor) {
  stack.push(cursor);
  if (stack.length > PRODUCT_BACKSTACK_LIMIT) stack.splice(0, stack.length - PRODUCT_BACKSTACK_LIMIT);
}
export const productMaintainer = () => ["admin", "technical"].includes(orgAccess.role);
export const blankProduct = () => ({ name: "", vendor: "", model: "", model_version: null, official_url: null, whitepaper_url: null });
export const validId = value => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const invalid = message => { throw new ApiError(502, "invalid_response", message); };
const revision = value => Number.isInteger(value) && value > 0;
function checkRef(value, id) { if (value?.kind !== "products" || !validId(value.resource_id) || id && value.resource_id !== id) invalid("产品范围不符合契约"); }
function checkLifecycle(value) { if (!value || !["active", "inactive"].includes(value.state) || !Number.isInteger(value.revision) || value.revision < 0 || value.revision === 0 && value.state !== "active") invalid("产品生命周期不符合契约"); }
function checkActions(actions) { if (!Array.isArray(actions) || actions.length > 16 || actions.some(item => typeof item.action !== "string" || typeof item.allowed !== "boolean" || item.allowed === (item.reason != null))) invalid("产品操作提示不符合契约"); }
export function checkProductRevision(value, id, number) {
  if (!value || value.org_id !== orgSession.get()?.orgId || !validId(value.id) || !validId(value.product_id) || id && value.product_id !== id || !revision(value.revision) || number && value.revision !== number || !value.data || !["name", "vendor", "model"].every(key => typeof value.data[key] === "string" && value.data[key].trim())) invalid("产品修订范围不符合契约");
  return value;
}
export function checkProductDetail(value, id, number) {
  if (value?.org_id !== orgSession.get()?.orgId || value.detail?.kind !== "products" || !revision(value.current_revision) || !["declared", "simulated"].includes(value.provenance) || !Array.isArray(value.actions)) invalid("产品详情范围不符合契约");
  if (typeof value.revised_at !== "string" || !Number.isFinite(Date.parse(value.revised_at)) || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value.revised_at) || !(value.revised_by === null || validId(value.revised_by))) invalid("产品精确修订的作者或时间不符合契约");
  checkRef(value.ref, id); checkLifecycle(value.lifecycle); checkActions(value.actions); checkProductRevision(value.detail.revision, id, number);
  if (value.detail.revision.revision > value.current_revision || new TextEncoder().encode(JSON.stringify(value)).length > 1024 * 1024) invalid("产品详情超过契约边界");
  return value;
}
export function checkProductPage(result, kind="list", id=null) {
  const {data,items}=result;
  if (data.org_id !== orgSession.get()?.orgId || !Number.isInteger(data.returned) || data.returned !== items.length || items.length > 100 || typeof data.has_more !== "boolean" || data.has_more !== (data.next_cursor !== null) || data.next_cursor !== null && (typeof data.next_cursor !== "string" || !data.next_cursor || data.next_cursor.length > 2048) || !data.as_of || !Number.isFinite(Date.parse(data.as_of))) invalid("产品分页范围不符合契约");
  if (new TextEncoder().encode(JSON.stringify(result)).length > 256 * 1024) invalid("产品分页结果超过契约边界");
  for (const item of items) {
    if (item.org_id !== data.org_id || !revision(item.revision)) invalid("产品分页范围不符合契约");
    checkRef(item.ref,id);
    if (kind === "list") { checkLifecycle(item.lifecycle); checkActions(item.actions); if (!validId(item.revision_id) || !["declared","simulated"].includes(item.provenance) || !Array.isArray(item.actions)) invalid("产品列表范围不符合契约"); }
    else if (kind === "history" && !validId(item.revision_id)) invalid("产品历史范围不符合契约");
    else if (kind === "lifecycle" && (!validId(item.id) || !["active","inactive"].includes(item.before) || !["active","inactive"].includes(item.after) || item.before === item.after)) invalid("产品生命周期历史范围不符合契约");
  }
  return result;
}
// A page owns at most two reads; superseded channels abort before another slot runs.
export function productReader() {
  const channels=new Map(), pending=[];let active=0, closed=false;
  function pump() { while (!closed && active<2 && pending.length) { const job=pending.shift(); if(job.controller.signal.aborted){job.reject(new DOMException("读取已取消","AbortError"));continue;} active++;job.execute().then(job.resolve,job.reject).finally(()=>{active--;pump();}); } }
  function read(channel,method,path,body) {
    channels.get(channel)?.abort();const controller=new AbortController();channels.set(channel,controller);
    return new Promise((resolve,reject)=>{pending.push({controller,resolve,reject,execute:()=>orgRequest(method,path,body,{signal:controller.signal,contractVersion:4})});pump();});
  }
  function cancel(channel) { channels.get(channel)?.abort(); }
  function stop(){closed=true;for(const controller of channels.values())controller.abort();for(const job of pending.splice(0))job.reject(new DOMException("读取已取消","AbortError"));}
  return {read,cancel,stop};
}
// Task membership is separate from library authority. Keep only the current actor.
export async function productTaskAuthority(reader, taskId) {
  if (!validId(taskId)) throw new ApiError(400,"invalid_input","任务 ID 无效");
  const orgId=orgSession.get()?.orgId;
  const identity=await reader.read("authority","GET","/org/current");
  if(identity.data.org_id!==orgId || !validId(identity.data.user_id) || !["admin","technical","bidder","viewer"].includes(identity.data.role)) invalid("单位身份范围不符合契约");
  const response=await reader.read("authority","GET",`/tasks/${taskId}/workflow`),workflow=response.data.workflow;
  if(workflow?.org_id!==orgId || workflow.task_id!==taskId || !["active","archived"].includes(workflow.state)) invalid("任务身份范围不符合契约");
  let cursor=null,member=null;const seen=new Set();
  do {
    const params=new URLSearchParams({limit:"100"});if(cursor)params.set("cursor",cursor);
    const page=await reader.read("authority","GET",`/tasks/${taskId}/members?${params}`);
    if(page.data.org_id!==orgId || page.data.task_id!==taskId || page.items.length>100 || page.data.returned!==page.items.length || typeof page.data.has_more!=="boolean" || page.data.has_more!==(page.data.next_cursor!==null) || page.items.some(item=>item.org_id!==orgId || item.task_id!==taskId || !validId(item.user_id) || typeof item.active!=="boolean" || !["owner","contributor","reviewer","observer"].includes(item.role))) invalid("任务成员范围不符合契约");
    member=page.items.find(item=>item.user_id===identity.data.user_id)??null;cursor=page.data.next_cursor;
    if(cursor && (typeof cursor!=="string" || seen.has(cursor)))invalid("任务成员分页重复");if(cursor)seen.add(cursor);
  }while(!member&&cursor);
  orgAccess.role=identity.data.role;orgAccess.userId=identity.data.user_id;
  return {workflow,member,canPin:canEditTask({workflow,member})&&identity.data.role!=="viewer"};
}
export const reasonLabels={obsolete:"已过时",duplicate:"重复资源",unavailable:"无法供应",restored:"恢复使用",other:"其他"};
