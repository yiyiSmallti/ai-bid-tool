import { ApiError, orgSession } from './api.js';
import { orgAccess, orgRequest } from './org.js';
import { productTaskAuthority, rememberProductCursor, validId } from './products.js';
export { validId, reasonLabels } from './products.js';
// Lists, history, authority and binary originals share two slots. Closing a page
// aborts active requests and prevents old previews from entering a new org view.
export function qualificationReader() {
 const channels=new Map(),pending=[];let active=0,closed=false;
 function pump(){while(!closed&&active<2&&pending.length){const job=pending.shift();if(job.controller.signal.aborted){job.reject(new DOMException('读取已取消','AbortError'));continue;}active++;job.execute().then(job.resolve,job.reject).finally(()=>{active--;pump();});}}
 function read(channel,method,path,body,options={}){channels.get(channel)?.abort();const controller=new AbortController();channels.set(channel,controller);return new Promise((resolve,reject)=>{pending.push({controller,resolve,reject,execute:()=>orgRequest(method,path,body,{...options,signal:controller.signal,contractVersion:4})});pump();});}
 function cancel(channel){channels.get(channel)?.abort();}
 function stop(){closed=true;for(const controller of channels.values())controller.abort();for(const job of pending.splice(0))job.reject(new DOMException('读取已取消','AbortError'));}
 return {read,cancel,stop};
}
export const qualificationTaskAuthority = productTaskAuthority;
export const rememberQualificationCursor = rememberProductCursor;
export const qualificationMaintainer = () => ['admin', 'bidder'].includes(orgAccess.role);
export const qualificationKinds = { profiles: { label: '资料', id: 'profile_id' }, certificates: { label: '证照', id: 'certificate_id' } };
export const qualificationPath = kind => `/management/resources/${kind}`;
export const profileTexts = [{ key: 'registration_details', label: '注册信息', rows: 3, max: 10000 }, { key: 'performance_summary', label: '业绩概述', rows: 4, max: 20000 }, { key: 'standard_wording', label: '标准表述', rows: 6, max: 20000 }];
export const blankQualification = kind => kind === 'profiles' ? { name: '', registration_details: '', performance_summary: '', standard_wording: '' } : { kind: 'qualification', name: '', number: '', valid_from: null, valid_until: null };
export function qualificationData(kind, form) {
 return kind === 'profiles' ? { name: form.name.trim(), ...Object.fromEntries(profileTexts.map(({key}) => [key, form[key]?.trim() || null])) } : { kind: form.kind, name: form.name.trim(), number: form.number.trim(), valid_from: form.valid_from || null, valid_until: form.valid_until || null };
}
const invalid = message => { throw new ApiError(502, 'invalid_response', message); };
const positive = value => Number.isInteger(value) && value > 0;
const stamp = value => typeof value === 'string' && Number.isFinite(Date.parse(value)) && /(?:Z|[+-]\d{2}:\d{2})$/.test(value);
const date = value => value === null || typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value));
const bytes = value => new TextEncoder().encode(JSON.stringify(value)).length;
function ref(value, kind, id) { if (value?.kind !== kind || !validId(value.resource_id) || id && value.resource_id !== id) invalid('资源范围不符合契约'); }
function lifecycle(value) { if (!value || !['active','inactive'].includes(value.state) || !Number.isInteger(value.revision) || value.revision < 0 || value.revision === 0 && value.state !== 'active') invalid('资源生命周期不符合契约'); }
function actions(value) { if (!Array.isArray(value) || value.length > 16 || value.some(item => typeof item.action !== 'string' || typeof item.allowed !== 'boolean' || item.allowed === (item.reason != null))) invalid('资源操作提示不符合契约'); }
export function checkQualificationRevision(value, kind, id, number) {
 const data=value?.data, cfg=qualificationKinds[kind];
 if (!cfg || value?.org_id !== orgSession.get()?.orgId || !validId(value.id) || !validId(value[cfg.id]) || id && value[cfg.id] !== id || !positive(value.revision) || number && value.revision !== number || !data || typeof data.name !== 'string' || !data.name.trim() || data.name.length > 200) invalid('资源修订范围不符合契约');
 if (kind === 'profiles') { if (profileTexts.some(({key,max}) => data[key] !== null && (typeof data[key] !== 'string' || !data[key].trim() || data[key].length > max))) invalid('资料正文不符合契约'); }
 else if (!['qualification','personnel'].includes(data.kind) || typeof data.number !== 'string' || !data.number.trim() || data.number.length > 200 || !date(data.valid_from) || !date(data.valid_until) || data.valid_from && data.valid_until && data.valid_from > data.valid_until) invalid('证照声明不符合契约');
 return value;
}
const sameData = (a,b) => a && b && ['kind','name','number','valid_from','valid_until'].every(key => a[key] === b[key]);
export function checkCertificateFile(value, revision) {
 const original=value?.file;
 if (!value || value.org_id !== revision.org_id || !validId(value.id) || value.certificate_id !== revision.certificate_id || value.certificate_revision_id !== revision.id || value.revision !== revision.revision || !sameData(value.data,revision.data) || !original || typeof original.name !== 'string' || !original.name.toLowerCase().endsWith('.pdf') || /[\\/\x00-\x1f\x7f]/.test(original.name) || !/^[a-f0-9]{64}$/.test(original.sha256) || original.media_type !== 'application/pdf' || !positive(original.size_bytes) || original.size_bytes > 40 * 1024 * 1024 || !positive(original.page_count) || original.page_count > 200 || !Array.isArray(value.parts) || value.parts.length > 20) invalid('证照原件修订范围不符合契约');
 for (const [index,part] of value.parts.entries()) if (part.ordinal !== index+1 || typeof part.name !== 'string' || /[\\/\x00-\x1f\x7f]/.test(part.name) || !['application/pdf','image/png','image/jpeg'].includes(part.media_type) || !/^[a-f0-9]{64}$/.test(part.sha256) || !positive(part.size_bytes) || part.size_bytes>40*1024*1024 || !positive(part.page_start) || !positive(part.page_count) || part.page_start+part.page_count-1>original.page_count || ![0,90,180,270].includes(part.rotation)) invalid('证照原件部件范围不符合契约');
 return value;
}
export function checkQualificationDetail(value, kind, id, number, asOf=null) {
 if (value?.org_id !== orgSession.get()?.orgId || value.detail?.kind !== kind || !positive(value.current_revision) || !stamp(value.revised_at) || !(value.revised_by === null || validId(value.revised_by)) || !['declared','simulated'].includes(value.provenance)) invalid('资源详情范围不符合契约');
 ref(value.ref,kind,id);lifecycle(value.lifecycle);actions(value.actions);const revision=checkQualificationRevision(value.detail.revision,kind,id,number);
 if (revision.revision>value.current_revision || bytes(value)>1024*1024) invalid('资源详情超过契约边界');
 if (kind === 'certificates') { if (!Object.hasOwn(value.detail,'file')) invalid('证照原件状态缺失');if(value.detail.file!==null)checkCertificateFile(value.detail.file,revision);if(asOf!==null && (value.date_advisory?.as_of!==asOf || !['unknown','expired','not_yet_valid','valid'].includes(value.date_advisory?.state)))invalid('证照日期提示范围不符合契约'); }
 return value;
}
export function checkQualificationEvent(value,kind,id) {
 if (value?.org_id!==orgSession.get()?.orgId || !validId(value.id) || !positive(value.revision) || !positive(value.resource_revision) || !['active','inactive'].includes(value.before) || !['active','inactive'].includes(value.after) || value.before===value.after || !['obsolete','duplicate','unavailable','restored','other'].includes(value.reason_code) || (value.after==='active')!==(value.reason_code==='restored') || !validId(value.actor_user_id) || value.actor_kind!=='session' || !stamp(value.created_at))invalid('生命周期记录范围不符合契约');
 ref(value.ref,kind,id);return value;
}
export function checkQualificationPage(result, kind, type='list', id=null) {
 const {data,items}=result;
 if (data.org_id!==orgSession.get()?.orgId || !Number.isInteger(data.returned) || data.returned!==items.length || items.length>100 || typeof data.has_more!=='boolean' || data.has_more!==(data.next_cursor!==null) || data.next_cursor!==null && (typeof data.next_cursor!=='string' || !data.next_cursor || data.next_cursor.length>2048) || !stamp(data.as_of) || bytes(result)>256*1024)invalid('资源分页范围或边界不符合契约');
 for(const item of items){if(item.org_id!==data.org_id || !positive(item.revision))invalid('资源分页范围不符合契约');ref(item.ref,kind,id);if(type==='lifecycle')checkQualificationEvent(item,kind,id);else{if(!validId(item.revision_id)||typeof item.name!=='string'||!item.name.trim())invalid('资源历史范围不符合契约');if(type==='list'){lifecycle(item.lifecycle);actions(item.actions);if(!stamp(item.created_at)||!stamp(item.revised_at)||!(item.revised_by===null||validId(item.revised_by))||!['declared','simulated'].includes(item.provenance))invalid('资源列表范围不符合契约');}else if(!stamp(item.created_at)||!(item.created_by===null||validId(item.created_by))||typeof item.current!=='boolean'||typeof item.has_file!=='boolean')invalid('资源历史范围不符合契约');}}
 return result;
}
