<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { categories, confirmAction, display, errorText, formatTime, label, locationLabel, orgAccess, orgRequest } from "../org.js";
import { checkReview, checkScope, entryOrigins, requirementRequest, reviewActions, reviewStates } from "../requirement-review.js";
import DocumentPreview from "./DocumentPreview.vue";
const props = defineProps({ taskId: String, jobId: String, requirementId: String, writable: Boolean, response: Object });
const emit = defineEmits(["changed", "dirty", "denied", "inspected"]);
const repairPreview = ref(null);
const data = ref(null), events = ref([]), next = ref(null), error = ref(""), reason = ref(""), reviewed = ref(false), busy = ref(false), stale = ref(false), original = ref(null);
let generation = 0;
const originalReady = computed(() => Boolean(original.value));
const row = computed(() => data.value?.requirement);
const quoteParts = computed(() => {
  if (!row.value) return [];
  const text = original.value, pin = row.value.source_pin;
  const characters = text ? Array.from(text) : [];
  if (!text || !pin || characters.slice(pin.start, pin.end).join("") !== row.value.content.source.quote) return [{ text: row.value.content.source.quote, mark: true }];
  return [{text:characters.slice(0,pin.start).join("")},{text:characters.slice(pin.start,pin.end).join(""),mark:true},{text:characters.slice(pin.end).join("")}];
});
async function history(cursor = null) {
  const run = generation, params = new URLSearchParams({limit:"50"}); if(cursor) params.set("cursor", cursor);
  const result = await requirementRequest("GET", `/requirements/${props.requirementId}/review-history?${params}`);
  if (run !== generation) return;
  if (result.data.task_id !== props.taskId || result.data.extraction_job_id !== props.jobId || result.items.length > 50 || result.items.some(item => item.requirement_id !== props.requirementId || item.task_id !== props.taskId || item.extraction_job_id !== props.jobId)) throw new Error("要求历史范围不符合契约");
  events.value = cursor ? [...events.value, ...result.items] : result.items; next.value = result.data.next_cursor;
}
async function load() {
  const run = ++generation; busy.value = true; error.value = ""; reviewed.value = false; data.value = null; original.value = null; events.value = []; next.value = null;
  try {
    const result = await requirementRequest("GET", `/requirements/${props.requirementId}/review`); if(run !== generation) return;
    checkScope(result.data.scope, props.taskId, props.jobId); checkReview(result.data.requirement, props.taskId, props.jobId); if(result.data.requirement.requirement_id !== props.requirementId) throw new Error("要求对象不符合契约");
    data.value = result.data; stale.value = false;
    await history();
    const source = row.value.content.source;
    const chunks = await orgRequest("GET", `/documents/${source.document_id}/chunks`); if(run !== generation) return;
    const chunk = chunks.items.find(item => item.id === source.chunk_id && item.document_id === source.document_id);
    original.value = source.page != null ? chunk?.text : chunk?.blocks?.find(item => item.block_id === source.location?.block_id)?.text;
    if (!original.value) error.value = "无法读取引用的完整原文位置，请通过原件预览核对。";
    else emit("inspected", result.data.requirement);
  } catch(exc) { if(run===generation){ error.value = errorText(exc); if([401,403,404].includes(exc.status)){data.value=null;events.value=[];original.value=null;emit("denied",exc);} } }
  finally { if(run===generation) busy.value=false; }
}
async function decide(action) {
  if (!row.value || busy.value || stale.value || !props.writable || !reason.value.trim() || action === "confirm" && (!reviewed.value || !originalReady.value || !row.value.citation_valid)) return;
  busy.value=true;error.value="";const snapshot=row.value;
  try { const result=await requirementRequest("POST",`/requirements/${snapshot.requirement_id}/review-decisions`,{request_id:crypto.randomUUID(),action,expected_revision:snapshot.revision,expected_review_hash:snapshot.review_hash,reason:reason.value});checkScope(result.data.scope,props.taskId,props.jobId);checkReview(result.data.requirement,props.taskId,props.jobId);reason.value="";reviewed.value=false;emit("dirty",false);emit("changed",result.data);await load(); }
  catch(exc){ reviewed.value=false; stale.value=true;error.value=`${errorText(exc)}；原因已保留，未自动重试。请重新读取并核对当前修订。`;if([401,403,404].includes(exc.status))emit("denied",exc); }
  finally{busy.value=false;}
}
async function moreHistory(){try{await history(next.value);}catch(exc){error.value=errorText(exc);if([401,403,404].includes(exc.status))emit("denied",exc);}}
async function previewRepair() {
  if(orgAccess.role!=="admin"||!props.writable)return;busy.value=true;error.value="";
  try { const result=await requirementRequest("GET",`/tasks/${props.taskId}/requirements/repair?job=${props.jobId}`);repairPreview.value=result; }
  catch(exc){error.value=errorText(exc);if([401,403,404].includes(exc.status))emit("denied",exc);}finally{busy.value=false;}
}
async function repair() {
  if(!repairPreview.value||!reason.value.trim()||orgAccess.role!=="admin"||!props.writable||!await confirmAction("按展示的预检修复引用，受影响要求确认将失效。继续？"))return;
  busy.value=true;
  try { await requirementRequest("POST",`/tasks/${props.taskId}/requirements/repair`,{extraction_job_id:props.jobId,expected_preview:repairPreview.value.data.preview_hash,reason:reason.value});repairPreview.value=null;emit("changed");await load(); }
  catch(exc){repairPreview.value=null;if([401,403,404].includes(exc.status))emit("denied",exc);error.value=`${errorText(exc)}；请重新预检修复，原因已保留。`;}finally{busy.value=false;}
}
async function discard(){return !reason.value||await confirmAction("尚有未保存的要求审阅原因，确定离开？");}
watch(reason,value=>emit("dirty",Boolean(value)));
watch(()=>props.requirementId,()=>{reason.value="";load();},{immediate:true});
function clear(){generation++;repairPreview.value=null;data.value=null;events.value=[];original.value=null;reason.value="";reviewed.value=false;}
window.addEventListener("bid:org-reset",clear);onBeforeUnmount(()=>{generation++;window.removeEventListener("bid:org-reset",clear);});
defineExpose({discard,load});
</script>
<template>
  <el-card shadow="never" class="section" data-testid="requirement-detail">
    <template #header><h3>核对要求原文与决策</h3></template>
    <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" class="section" />
    <el-button :disabled="busy" @click="load">重新读取要求修订</el-button>
    <template v-if="row">
      <p>{{label(reviewStates,row.state)}} · {{label(entryOrigins,row.origin)}} · 修订 {{row.revision}}</p>
      <p>{{locationLabel(row.content.source)}}</p>
      <DocumentPreview :document-id="row.content.source.document_id" :page="row.content.source.page" :block="row.content.source.location?.block_id ?? ''" label="核对要求原件" />
      <blockquote class="quote original" tabindex="0" aria-label="要求完整原文及逐字引文"><template v-for="(part,index) in quoteParts" :key="index"><mark v-if="part.mark">{{part.text}}</mark><span v-else>{{part.text}}</span></template></blockquote>
      <p v-if="row.source_pin" class="hint">已绑定的精确跨度 {{row.source_pin.start}}–{{row.source_pin.end}} · 校验器 {{row.source_pin.verifier_version}}</p>
      <p v-if="row.model_quote && row.model_quote !== row.content.source.quote">原模型引文：{{row.model_quote}}</p>
      <h4>{{row.content.starred?'★ ':''}}{{row.content.text}}</h4><p>类别：{{label(categories,row.content.category)}}</p><pre class="condition">{{display(row.content.condition)}}</pre>
      <p>响应状态：{{response ? label({draft:'草稿',pending_review:'待审阅',confirmed:'响应已确认',rejected:'已驳回',needs_material:'需补材料'},response.card_state,'尚无响应卡') : '请在响应页读取当前状态'}}；要求确认与响应、证据确认分别处理。</p><RouterLink :to="`/org/tasks/${taskId}/review?job=${jobId}&requirement=${row.requirement_id}`">进入响应与证据审阅</RouterLink>
      <p v-if="row.confirmed_by_user_id">要求确认人 {{row.confirmed_by_user_id}} · {{formatTime(row.confirmed_at)}}</p>
      <p v-if="row.next_actor">下一位：{{row.next_actor.user_id ?? '单位管理员恢复负责人'}} · {{row.next_actor.basis==='assignee'?'已分配成员':row.next_actor.basis==='owner'?'任务负责人':'负责人恢复'}} · {{row.next_actor.action}}</p>
      <template v-if="!row.citation_valid"><p class="warning">引用无效，必须修复后重新核对。请任务负责人协调管理员修复引用。</p><el-button v-if="orgAccess.role==='admin' && writable" :disabled="busy" @click="previewRepair">预检修复集合引用</el-button><RouterLink :to="`/org/tasks/${taskId}/members`">协调负责人或恢复任务</RouterLink></template>
      <template v-if="repairPreview"><p>引用修复预检：{{display(repairPreview.data)}}</p><ul><li v-for="item in repairPreview.items" :key="item.requirement_id">{{item.status}} · {{item.proposed_quote ?? item.source.quote}}</li></ul><el-button :disabled="busy || !reason.trim()" @click="repair">按预检修复引用</el-button></template>
      <el-form label-position="top" @submit.prevent>
        <el-form-item label="要求审阅原因" required><el-input v-model="reason" type="textarea" :rows="3" maxlength="10000" :disabled="!writable" /></el-form-item>
        <el-checkbox v-if="row.state!=='confirmed'" v-model="reviewed" :disabled="!writable || stale || !originalReady || !row.citation_valid">已核对正文、类别、星标、结构条件和逐字原文</el-checkbox>
        <div class="actions"><el-button v-if="row.state!=='confirmed'" type="primary" :disabled="!writable || busy || stale || !originalReady || !row.citation_valid || !reviewed || !reason.trim()" @click="decide('confirm')">确认要求</el-button><el-button v-else type="warning" :disabled="!writable || busy || stale || !reason.trim()" @click="decide('reopen')">重新打开要求</el-button></div>
        <p v-if="!writable" class="hint">当前身份或任务状态仅允许读取。任务负责人 / 协作者本人可在有效任务中确认要求。</p>
      </el-form>
      <h4>不可变决策历史</h4><ol><li v-for="event in events" :key="event.id"><p>{{label(reviewActions,event.action)}} · 修订 {{event.revision}} · {{formatTime(event.occurred_at)}} · {{event.actor_user_id ?? event.actor_kind}}</p><p>{{label(reviewStates,event.state_after)}} · {{event.content.text}}</p><blockquote class="quote">{{event.content.source.quote}}</blockquote><p class="hint">{{locationLabel(event.content.source)}} · {{label(categories,event.content.category)}} · {{event.content.starred?'★ 星标':'非星标'}}</p><pre class="condition">{{display(event.content.condition)}}</pre></li></ol><el-button v-if="next" @click="moreHistory">继续读取历史</el-button>
    </template>
  </el-card>
</template>
<style scoped>.original{max-height:360px;overflow:auto;white-space:pre-wrap}mark{background:#fff1a8;color:#202020}.condition{white-space:pre-wrap;overflow-wrap:anywhere;max-height:220px;overflow:auto}li{border-bottom:1px solid var(--border);padding:8px 0}</style>
