import { ApiError, orgSession } from "./api.js";
import { orgAccess, errorText } from "./org.js";
import { productReader, rememberProductCursor, validId } from "./products.js";
export { productReader as memoryReader, rememberProductCursor as rememberCursor, validId };
export const memoryPath="/management/memories";
export const statuses={candidate:"候选，待管理员审核",active:"已生效",disabled:"已停用",expired:"已过期",deleted:"已逻辑删除"};
export const kinds={rule:"规则",preference:"偏好"};
export const feedbackKinds={card_edited:"人工修改",card_rejected:"人工驳回",card_confirmed:"人工确认（仅评价样本）"};
export const memoryWriter=()=>["admin","bidder","technical"].includes(orgAccess.role);
export const blankMemory=()=>({content:{kind:"rule",conflict_key:"",text:"",tags:[]},expires_at:null});
const bad=()=>{throw new ApiError(502,"invalid_response","记忆范围或响应边界不符合契约");};
const time=value=>typeof value==="string"&&Number.isFinite(Date.parse(value))&&/(?:Z|[+-]\d{2}:\d{2})$/.test(value);
export function checkRevision(value,id,number){
  if(value?.org_id!==orgSession.get()?.orgId||!validId(value.id)||!validId(value.memory_id)||id&&value.memory_id!==id||!Number.isInteger(value.revision)||value.revision<1||number&&value.revision!==number||value.target?.scope!=="org"||!Object.hasOwn(statuses,value.status)||!["candidate","active","disabled"].includes(value.status)||!Object.hasOwn(kinds,value.content?.kind)||typeof value.content.text!=="string"||!value.content.text.trim()||value.content.text.length>2000||typeof value.content.conflict_key!=="string"||!Array.isArray(value.content.tags)||value.content.tags.length>20||value.content.tags.some(tag=>typeof tag!=="string"||!tag.trim()||tag.length>40)||!time(value.created_at)||!validId(value.created_by)||!value.source)bad();
  const source=value.source;
  if(source.provenance_redacted&&["task_id","card_id","card_revision_id","feedback_event_id","proposal_job_id","proposal_run_id","platform_release_id"].some(key=>source[key]!=null))bad();
  for(const key of ["task_id","card_id","card_revision_id","feedback_event_id","proposal_job_id","proposal_run_id"])if(source[key]!=null&&!validId(source[key]))bad();
  if(value.status==="active"&&(!validId(value.confirmed_by)||!time(value.confirmed_at)||value.actor_kind!=="session"||value.decision!=="approve"))bad();
  if(value.decision_reason_sha256!=null&&!/^[0-9a-f]{64}$/.test(value.decision_reason_sha256))bad();
  return value;
}
export function checkMemory(value,id){
  if(value?.org_id!==orgSession.get()?.orgId||!validId(value.id)||id&&value.id!==id||!Object.hasOwn(statuses,value.effective_status))bad();
  checkRevision(value.current,value.id);
  if(["candidate","active","disabled"].includes(value.effective_status)&&value.effective_status!==value.current.status||value.effective_status==="expired"&&(value.current.status!=="active"||!value.current.expires_at)||value.effective_status==="deleted"&&!value.deleted_at)bad();
  return value;
}
export function checkDetail(value,id,number){
  checkMemory(value?.memory,id);checkRevision(value.memory.current,id,number);
  if(!Number.isInteger(value.current_revision)||value.current_revision<value.memory.current.revision||!Object.hasOwn(statuses,value.current_effective_status)||value.memory.current.revision===value.current_revision&&value.current_effective_status!==value.memory.effective_status||!time(value.revised_at)||!(value.revised_by===null||validId(value.revised_by))||!time(value.as_of)||!Array.isArray(value.actions)||value.actions.length>16||value.actions.some(item=>typeof item.action!=="string"||typeof item.allowed!=="boolean")||new TextEncoder().encode(JSON.stringify(value)).length>1024*1024)bad();
  return value;
}
export function checkPage(result,{historyId=null,taskId=null}={}){
  const {data,items}=result;
  if(data.returned!==items.length||items.length>25||data.next_cursor!==null&&(typeof data.next_cursor!=="string"||!data.next_cursor||data.next_cursor.length>2048)||new TextEncoder().encode(JSON.stringify(result)).length>256*1024)bad();
  if((data.org_id!==orgSession.get()?.orgId||!time(data.as_of)||typeof data.has_more!=="boolean"||data.has_more!==(data.next_cursor!==null)))bad();
  for(const item of items){if(taskId){if(item.org_id!==orgSession.get()?.orgId||item.task_id!==taskId||!validId(item.id)||!validId(item.card_id)||!validId(item.before_revision_id)||!validId(item.after_revision_id)||!Object.hasOwn(feedbackKinds,item.kind)||!time(item.created_at))bad();if(item.candidate_job_id!=null){if(!validId(item.candidate_job_id)||!Array.isArray(item.candidate_event_ids)||!item.candidate_event_ids.length||item.candidate_event_ids.length>100||item.candidate_event_ids.some(id=>!validId(id))||!item.candidate_event_ids.includes(item.id)||item.kind==="card_confirmed")bad();}if(item.candidate_memory_id!=null&&!validId(item.candidate_memory_id))bad();}else if(historyId)checkRevision(item,historyId);else checkMemory(item);}
  return result;
}
export function memoryError(error){
  const messages={memory_sensitive_value:"记忆包含受保护内容，请移除敏感信息后修改提案",memory_conflict_key:"已有同类、同冲突键的有效记忆。请核对并由管理员决定保留哪一项，再审核候选",memory_revision_conflict:"记忆修订已变化，请重新核对精确修订",memory_invalid_transition:"当前状态不能执行此操作。停用后须编辑为候选，再由管理员审核",memory_scope_unavailable:"当前页面只支持单位记忆",invalid_expiry:"到期时间无效或已过，请填写含时区的未来时间"};
  return messages[error.code]??errorText(error);
}
