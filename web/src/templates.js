import { ApiError, orgSession } from "./api.js";
import { orgAccess, orgRequest } from "./org.js";
import { canEditTask } from "./task-authority.js";
export const templatePath = "/management/resources/templates";
export const TEMPLATE_BACKSTACK_LIMIT = 50;
// Retain navigation anchors only; old pages and unbounded cursor history are evicted.
export function rememberTemplateCursor(stack, cursor) {
  stack.push(cursor);
  if (stack.length > TEMPLATE_BACKSTACK_LIMIT) stack.splice(0, stack.length - TEMPLATE_BACKSTACK_LIMIT);
}
export const templateMaintainer = () => orgAccess.role === "admin";
export const blankTemplate = () => ({ name: "", project_types: null, chapters: null });
export const validId = value => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
const invalid = message => { throw new ApiError(502, "invalid_response", message); };
const revision = value => Number.isInteger(value) && value > 0;
function checkRef(value, id) { if (value?.kind !== "templates" || !validId(value.resource_id) || id && value.resource_id !== id) invalid("模板范围不符合契约"); }
function checkLifecycle(value) { if (!value || !["active", "inactive"].includes(value.state) || !Number.isInteger(value.revision) || value.revision < 0 || value.revision === 0 && value.state !== "active") invalid("模板生命周期不符合契约"); }
function checkActions(actions) { if (!Array.isArray(actions) || actions.length > 16 || actions.some(item => typeof item.action !== "string" || typeof item.allowed !== "boolean" || item.allowed === (item.reason != null))) invalid("模板操作提示不符合契约"); }
export function checkTemplateRevision(value, id, number) {
  if (!value || value.org_id !== orgSession.get()?.orgId || !validId(value.id) || !validId(value.template_id) || id && value.template_id !== id || !revision(value.revision) || number && value.revision !== number || !value.data || !validTemplateData(value.data) || !validTemplateFile(value.file)) invalid("模板修订范围不符合契约");
  return value;
}
export function checkTemplateDetail(value, id, number) {
  if (value?.org_id !== orgSession.get()?.orgId || value.detail?.kind !== "templates" || !revision(value.current_revision) || value.provenance !== "declared" || !Array.isArray(value.actions)) invalid("模板详情范围不符合契约");
  if (typeof value.revised_at !== "string" || !Number.isFinite(Date.parse(value.revised_at)) || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value.revised_at) || !(value.revised_by === null || validId(value.revised_by))) invalid("模板精确修订的作者或时间不符合契约");
  checkRef(value.ref, id); checkLifecycle(value.lifecycle); checkActions(value.actions); checkTemplateRevision(value.detail.revision, id, number);
  if (value.detail.revision.revision > value.current_revision || new TextEncoder().encode(JSON.stringify(value)).length > 1024 * 1024) invalid("模板详情超过契约边界");
  return value;
}
export function checkTemplatePage(result, kind="list", id=null) {
  const {data,items}=result;
  if (data.org_id !== orgSession.get()?.orgId || !Number.isInteger(data.returned) || data.returned !== items.length || items.length > 100 || typeof data.has_more !== "boolean" || data.has_more !== (data.next_cursor !== null) || data.next_cursor !== null && (typeof data.next_cursor !== "string" || !data.next_cursor || data.next_cursor.length > 2048) || !data.as_of || !Number.isFinite(Date.parse(data.as_of))) invalid("模板分页范围不符合契约");
  if (new TextEncoder().encode(JSON.stringify(result)).length > 256 * 1024) invalid("模板分页结果超过契约边界");
  for (const item of items) {
    if (item.org_id !== data.org_id || !revision(item.revision)) invalid("模板分页范围不符合契约");
    checkRef(item.ref,id);
    if (kind === "list") { checkLifecycle(item.lifecycle); checkActions(item.actions); if (!validId(item.revision_id) || item.provenance !== "declared" || !Array.isArray(item.actions)) invalid("模板列表范围不符合契约"); }
    else if (kind === "history" && !validId(item.revision_id)) invalid("模板历史范围不符合契约");
    else if (kind === "lifecycle" && (!validId(item.id) || !["active","inactive"].includes(item.before) || !["active","inactive"].includes(item.after) || item.before === item.after)) invalid("模板生命周期历史范围不符合契约");
  }
  return result;
}
// A page owns at most two reads; superseded channels abort before another slot runs.
export function templateReader() {
  const channels=new Map(), pending=[];let active=0, closed=false;
  function pump() { while (!closed && active<2 && pending.length) { const job=pending.shift(); if(job.controller.signal.aborted){job.reject(new DOMException("读取已取消","AbortError"));continue;} active++;job.execute().then(job.resolve,job.reject).finally(()=>{active--;pump();}); } }
  function read(channel,method,path,body,options={}) {
    channels.get(channel)?.abort();const controller=new AbortController();channels.set(channel,controller);
    return new Promise((resolve,reject)=>{pending.push({controller,resolve,reject,execute:()=>orgRequest(method,path,body,{...options,signal:controller.signal,contractVersion:4})});pump();});
  }
  function cancel(channel) { channels.get(channel)?.abort(); }
  function stop(){closed=true;for(const controller of channels.values())controller.abort();for(const job of pending.splice(0))job.reject(new DOMException("读取已取消","AbortError"));}
  return {read,cancel,stop,isStopped:()=>closed};
}
// Task membership is separate from library authority. Keep only the current actor.
export async function templateTaskAuthority(reader, taskId) {
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

export const SECTION_ORDER = ["substantive","commercial","technical","comply_only","gaps","evidence_appendix"];
export const sectionLabels = {substantive:"实质性响应",commercial:"商务响应",technical:"技术响应",comply_only:"仅符合项",gaps:"缺口",evidence_appendix:"证据附录"};
export const columnLabels = {ordinal:"序号",requirement:"招标要求",response:"投标响应",compliance:"偏离情况"};
export const hashValid = value => typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
export function validTemplateFile(value) { return value && typeof value.name === "string" && value.name.length <= 200 && hashValid(value.sha256) && Number.isInteger(value.size_bytes) && value.size_bytes > 0 && value.size_bytes <= 40*1024*1024 && value.media_type === "application/vnd.openxmlformats-officedocument.wordprocessingml.document"; }
export function validTemplateData(value) {
 if(!value || typeof value.name !== "string" || !value.name.trim() || value.name.length>200) return false;
 if(value.project_types!==null && (!Array.isArray(value.project_types)||value.project_types.length>100||value.project_types.some(entry=>typeof entry!=="string"||!entry.trim()||entry.length>200)))return false;
 if(value.chapters===null)return true;if(!Array.isArray(value.chapters)||value.chapters.length>100)return false;
 const pending=value.chapters.map(chapter=>[chapter,1]);let count=0;
 while(pending.length){const [chapter,depth]=pending.pop();if(++count>200||depth>6||!chapter||typeof chapter.title!=="string"||!chapter.title.trim()||chapter.title.length>200||!Array.isArray(chapter.children)||chapter.children.length>100)return false;pending.push(...chapter.children.map(child=>[child,depth+1]));}return true;
}
export const blankSections = () => SECTION_ORDER.map((section,index)=>({section,heading_style_id:"",table_style_id:"",columns:index<3?Object.keys(columnLabels).map(key=>({key,width_percent:"25"})):[]}));
export function validSections(sections) {return Array.isArray(sections)&&sections.length===6&&sections.every((section,index)=>section.section===SECTION_ORDER[index]&&["heading_style_id","table_style_id"].every(key=>typeof section[key]==="string"&&section[key].trim()&&section[key].length<=200)&&Array.isArray(section.columns)&&(index<3?section.columns.length===4&&section.columns.every((col,i)=>col.key===Object.keys(columnLabels)[i]&&Number(col.width_percent)>0&&Number(col.width_percent)<=100)&&Math.abs(section.columns.reduce((sum,col)=>sum+Number(col.width_percent),0)-100)<1e-8:section.columns.length===0));}
export function checkBinding(value,revisionId,templateHash,id=null){
 if(!value||value.org_id!==orgSession.get()?.orgId||value.template_revision_id!==revisionId||value.template_sha256!==templateHash||!validId(value.id)||id&&value.id!==id||!hashValid(value.binding_hash)||!hashValid(value.static_content_hash)||typeof value.current!=="boolean"||!validId(value.reviewed_by)||!Number.isFinite(Date.parse(value.reviewed_at))||typeof value.adapter_version!=="string"||!Array.isArray(value.sections)||value.sections.length!==6||value.sections.some((section,index)=>section.section!==SECTION_ORDER[index])||value.current&&!validSections(value.sections)||new TextEncoder().encode(JSON.stringify(value)).length>1024*1024)invalid("绑定范围不符合契约");return value;
}
export function checkBindingPage(result,revisionId,templateHash){
 const {data,items}=result;if(data.org_id!==orgSession.get()?.orgId||data.returned!==items.length||items.length>100||typeof data.has_more!=="boolean"||data.has_more!==(data.next_cursor!==null)||data.next_cursor!==null&&(typeof data.next_cursor!=="string"||!data.next_cursor||data.next_cursor.length>2048)||!Number.isFinite(Date.parse(data.as_of))||new TextEncoder().encode(JSON.stringify(result)).length>256*1024)invalid("绑定分页范围不符合契约");items.forEach(value=>checkBinding(value,revisionId,templateHash));return result;
}
export function checkBindingPreview(value,revisionId,templateHash){
 if(!value||value.dry_run!==true||value.template_revision_id!==revisionId||value.template_sha256!==templateHash||!hashValid(value.static_content_hash)||typeof value.adapter_version!=="string"||!Array.isArray(value.anchors)||value.anchors.length>6||!Array.isArray(value.issues)||value.issues.length>100||new TextEncoder().encode(JSON.stringify(value)).length>1024*1024)invalid("绑定预览范围不符合契约");
 for(const issue of value.issues){if(!issue||!hashValid(issue.issue_id)||typeof issue.code!=="string"||!issue.code||issue.code.length>100||!["block","acknowledge"].includes(issue.severity)||![issue.requirement_ids,issue.evidence_ids].every(ids=>Array.isArray(ids)&&ids.every(validId)&&new Set(ids).size===ids.length))invalid("绑定警示范围不符合契约");}
 let previous=-1;for(const anchor of value.anchors){const i=SECTION_ORDER.indexOf(anchor.section);if(i<=previous||!Number.isInteger(anchor.paragraph)||anchor.paragraph<1||!Number.isInteger(anchor.section_index)||anchor.section_index<0)invalid("绑定锚点范围不符合契约");previous=i;}return value;
}
export function templateUpload(data,file,expectedRevision=null){
 if(!validTemplateData(data)||!(file instanceof File)||!file.name.toLowerCase().endsWith(".docx")||file.size===0||file.size>40*1024*1024)throw new ApiError(400,"invalid_input","填写有效模板声明并选择新的 DOCX（不超过 40 MiB）");
 const body=new FormData();body.append("metadata",JSON.stringify({data,...(expectedRevision===null?{}:{expected_revision:expectedRevision})}));body.append("file",file);return body;
}

export async function downloadTemplateOriginal(reader,revisionId,file){
 const context=orgSession.get();
 const signed=await reader.read("original","GET",`/resources/templates/revisions/${revisionId}/download-link`);
 const url=signed.data.url,prefix=`/resources/templates/revisions/${revisionId}/download?`;
 if(typeof url!=="string"||!url.startsWith(prefix)||url.length>8192)invalid("原件下载范围不符合契约");
 const blob=await reader.read("original","GET",url,undefined,{binary:true});
 if(blob.type!==file.media_type||blob.size!==file.size_bytes)invalid("原件类型或大小不符合契约");
 const digest=await crypto.subtle.digest("SHA-256",await blob.arrayBuffer());
 if(Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,"0")).join("")!==file.sha256)invalid("原件哈希不符合契约");
 if(reader.isStopped()||context?.orgId!==orgSession.get()?.orgId||context?.session!==orgSession.get()?.session)throw new DOMException("单位会话已改变","AbortError");
 const objectUrl=URL.createObjectURL(blob);const anchor=document.createElement("a");anchor.href=objectUrl;anchor.download=file.name;anchor.click();setTimeout(()=>URL.revokeObjectURL(objectUrl),1000);
}
