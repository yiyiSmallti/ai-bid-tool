<script setup>
import {computed,onMounted,onUnmounted,ref} from "vue";
import {onBeforeRouteLeave,onBeforeRouteUpdate,useRoute} from "vue-router";
import ProductEditor from "../components/ProductEditor.vue";
import {confirmAction,errorText,formatTime,orgRequest} from "../org.js";
import {ApiError,orgSession} from "../api.js";
import {blankProduct,checkProductDetail,checkProductPage,checkProductRevision,productMaintainer,productPath,productReader,productTaskAuthority,reasonLabels,rememberProductCursor,validId} from "../products.js";
const route=useRoute(),id=route.params.productId,reader=productReader();
const requestedRevision=route.query.revision===undefined?null:Number(route.query.revision);
const taskId=typeof route.query.task==="string"?route.query.task:null;
const detail=ref(null),error=ref(""),taskError=ref(""),loading=ref(false),busy=ref(false),editing=ref(false),form=ref(blankProduct()),initial=ref(""),receipt=ref(null),conflict=ref(null),authority=ref(null);
const history=ref([]),historyMeta=ref(null),historyCursor=ref(null),historyPrevious=ref([]),lifecycleHistory=ref([]),lifecycleMeta=ref(null),lifecycleCursor=ref(null),lifecyclePrevious=ref([]),tab=ref("content"),pinDialog=ref(false),lot=ref(""),reason=ref("obsolete");
let generation=0,stopped=false;
const revision=computed(()=>detail.value?.detail.revision),current=computed(()=>revision.value?.revision===detail.value?.current_revision),dirty=computed(()=>editing.value&&JSON.stringify(form.value)!==initial.value);
const allowed=action=>productMaintainer()&&detail.value?.actions.some(hint=>hint.action===action&&hint.allowed);
const canRevise=computed(()=>current.value&&allowed("revise"));
const canPin=computed(()=>!!authority.value?.canPin&&!!revision.value);
const baseLink=computed(()=>({path:"/org/products",query:taskId&&validId(taskId)?{task:taskId}:{}}));
function exactLink(number){return {path:`/org/products/${id}`,query:{revision:String(number),...(taskId&&validId(taskId)?{task:taskId}:{})}};}
async function load(){if(stopped)return;const run=++generation;loading.value=true;error.value="";detail.value=null;
 try{if(!validId(id)||requestedRevision!==null&&(!Number.isInteger(requestedRevision)||requestedRevision<1))throw new ApiError(400,"invalid_input","产品 ID 或修订无效");const suffix=requestedRevision===null?"":`?revision=${requestedRevision}`;const result=await reader.read("detail","GET",`${productPath}/${id}${suffix}`);const checked=checkProductDetail(result.data,id,requestedRevision);if(stopped||run!==generation)return;detail.value=checked;}
 catch(exc){if(run===generation&&exc.name!=="AbortError"){error.value=errorText(exc);history.value=[];lifecycleHistory.value=[];receipt.value=null;authority.value=null;}}
 finally{if(run===generation)loading.value=false;}
}
async function loadHistory(kind="content",cursor=null){if(!detail.value)return;const lifecycle=kind==="lifecycle";
 try{const response=checkProductPage(await reader.read(kind,"POST",`${productPath}/${id}/${lifecycle?"lifecycle/history":"history"}/query`,{cursor,limit:25}),lifecycle?"lifecycle":"history",id);if(stopped)return;if(lifecycle){lifecycleHistory.value=response.items;lifecycleMeta.value=response.data;lifecycleCursor.value=cursor;}else{history.value=response.items;historyMeta.value=response.data;historyCursor.value=cursor;}}
 catch(exc){if(exc.name!=="AbortError"){error.value=errorText(exc);if(lifecycle){lifecycleHistory.value=[];lifecycleMeta.value=null;}else{history.value=[];historyMeta.value=null;}if(["management_cursor_invalid","management_cursor_expired"].includes(exc.code)){if(lifecycle)lifecyclePrevious.value=[];else historyPrevious.value=[];}}}
}
function discardContent(){stopped=true;generation++;reader.stop();detail.value=null;history.value=[];lifecycleHistory.value=[];historyMeta.value=null;lifecycleMeta.value=null;authority.value=null;form.value=blankProduct();editing.value=false;pinDialog.value=false;lot.value="";receipt.value=null;conflict.value=null;}
async function refreshAuthority(){authority.value=null;taskError.value="";if(!taskId)return;
 try{const value=await productTaskAuthority(reader,taskId);if(stopped)return;authority.value=value;}catch(exc){if([401,403,404].includes(exc.status)){discardContent();error.value=errorText(exc);}if(exc.name!=="AbortError")taskError.value=errorText(exc);}}
async function startEdit(){if(!canRevise.value)return;form.value={...revision.value.data};initial.value=JSON.stringify(form.value);editing.value=true;conflict.value=null;receipt.value=null;}
async function cancel(){if(dirty.value&&!await confirmAction("放弃未保存的产品内容？","放弃未保存编辑","放弃编辑"))return;editing.value=false;form.value=blankProduct();conflict.value=null;}
async function freshHead(){const response=await reader.read("head","GET",`${productPath}/${id}`);return checkProductDetail(response.data,id);}
async function save(){if(!editing.value||!allowed("revise")||busy.value)return;busy.value=true;error.value="";
 try{const result=await orgRequest("POST",`/resources/products/${id}/revisions`,{expected_revision:detail.value.current_revision,data:form.value},{contractVersion:4});receipt.value={type:"revision",data:checkProductRevision(result.data,id)};editing.value=false;form.value=blankProduct();conflict.value=null;historyPrevious.value=[];await load();await loadHistory();}
 catch(exc){error.value=errorText(exc);if([401,403,404].includes(exc.status)){discardContent();}
 else if(exc.status===409||exc.code==="revision_conflict"){
   try{const head=await freshHead();detail.value={...detail.value,current_revision:head.current_revision,lifecycle:head.lifecycle,actions:head.actions};conflict.value={revision:head.current_revision,data:head.detail.revision.data};error.value="修订已变化，草稿已保留。请对照当前服务器内容，再主动保存。";historyPrevious.value=[];await loadHistory();}catch(readError){error.value=errorText(readError);detail.value=null;}
 }else if(!exc.status){error.value="保存结果尚未确定，草稿已保留。请重新读取服务器修订后，再决定是否保存。";try{const head=await freshHead();detail.value={...detail.value,current_revision:head.current_revision,lifecycle:head.lifecycle,actions:head.actions};conflict.value={revision:head.current_revision,data:head.detail.revision.data};}catch(readError){error.value=errorText(readError);detail.value=null;}}
 }finally{busy.value=false;}}
async function lifecycle(){if(busy.value||!detail.value)return;const state=detail.value.lifecycle.state==="active"?"inactive":"active",action=state==="inactive"?"deactivate":"restore",text=state==="inactive"?"停用":"恢复";if(!allowed(action))return;
 if(!await confirmAction(`${text}此产品？已有任务继续保留各自固定的修订；停用阻止新的选择和版本替换，不删除内容或确认任何证据。`,`${text}产品`,`确认${text}`,state==="inactive"))return;
 busy.value=true;error.value="";
 try{const result=await orgRequest("POST",`${productPath}/${id}/lifecycle`,{expected_revision:detail.value.current_revision,expected_lifecycle_revision:detail.value.lifecycle.revision,state,reason_code:state==="active"?"restored":reason.value},{contractVersion:4});const value=result.data;if(value.event?.org_id!==orgSession.get()?.orgId||value.event.ref?.resource_id!==id||value.lifecycle?.state!==state||value.existing_selections!=="preserved")throw new ApiError(502,"invalid_response","生命周期回执范围不符合契约");receipt.value={type:"lifecycle",data:value};await load();lifecyclePrevious.value=[];await loadHistory("lifecycle");}
 catch(exc){error.value=errorText(exc);if([401,403,404].includes(exc.status)){discardContent();}
 else if(exc.status===409){await load();error.value="产品或生命周期版本已变化，请核对新状态后重新决定。";}
 else if(!exc.status){try{const head=await freshHead();detail.value=head;error.value="停用或恢复的结果尚未确定，已重新读取服务器状态。请核对生命周期修订后重新决定，不会自动重试。";}catch(readError){discardContent();error.value=errorText(readError);}}}
 finally{busy.value=false;}}
async function openPin(){await refreshAuthority();if(canPin.value){pinDialog.value=true;receipt.value=null;}}
async function pin(){if(!canPin.value||busy.value)return;busy.value=true;error.value="";
 try{await refreshAuthority();if(!canPin.value){pinDialog.value=false;return;}const number=revision.value.revision,revisionId=revision.value.id;const result=await orgRequest("POST",`/tasks/${taskId}/products`,{product_id:id,revision:number,lot:lot.value.trim()||null},{contractVersion:4});const value=result.data;if(value.org_id!==orgSession.get()?.orgId||value.task_id!==taskId||value.product_revision_id!==revisionId||value.revision!==number||!validId(value.id)||typeof value.duplicate!=="boolean")throw new ApiError(502,"invalid_response","任务固定回执范围不符合契约");receipt.value={type:"pin",data:value};pinDialog.value=false;lot.value="";window.dispatchEvent(new CustomEvent("bid:task-materials-changed",{detail:{taskId}}));}
 catch(exc){if([401,403,404].includes(exc.status)){discardContent();error.value=errorText(exc);}
 else if(!exc.status){pinDialog.value=false;error.value="任务选择结果尚未确定。请核对服务器固定版本与回执后，再决定是否提交；不会自动重试或升级。";}
 else {if(exc.code==="resource_inactive")pinDialog.value=false;error.value=exc.code==="resource_inactive"?"产品已停用，不能新建或替换固定版本；已有任务固定版本继续保留。":errorText(exc);}}
 finally{busy.value=false;}}
function nextHistory(kind){const life=kind==="lifecycle",meta=life?lifecycleMeta.value:historyMeta.value;if(!meta?.has_more)return;rememberProductCursor(life?lifecyclePrevious.value:historyPrevious.value,life?lifecycleCursor.value:historyCursor.value);loadHistory(kind,meta.next_cursor);}
function restartHistory(kind){if(kind==="lifecycle")lifecyclePrevious.value=[];else historyPrevious.value=[];loadHistory(kind);}
function previousHistory(kind){const previous=kind==="lifecycle"?lifecyclePrevious.value:historyPrevious.value;if(previous.length)loadHistory(kind,previous.pop());}
async function chooseTab(kind){tab.value=kind;if(kind==="lifecycle"&&!lifecycleMeta.value)await loadHistory(kind);}
function clear(){stopped=true;generation++;reader.stop();detail.value=null;history.value=[];lifecycleHistory.value=[];historyMeta.value=null;lifecycleMeta.value=null;authority.value=null;form.value=blankProduct();editing.value=false;pinDialog.value=false;lot.value="";receipt.value=null;conflict.value=null;error.value="单位会话已改变，请重新打开产品详情";}
function beforeUnload(event){if(dirty.value){event.preventDefault();event.returnValue="";}}
const canLeave=async()=>!dirty.value||await confirmAction("放弃未保存的产品内容？","放弃未保存编辑","放弃编辑");
onBeforeRouteLeave(canLeave);onBeforeRouteUpdate(canLeave);
onMounted(async()=>{window.addEventListener("bid:org-reset",clear);window.addEventListener("beforeunload",beforeUnload);await load();if(detail.value){await loadHistory();await refreshAuthority();}});
onUnmounted(()=>{stopped=true;generation++;reader.stop();window.removeEventListener("bid:org-reset",clear);window.removeEventListener("beforeunload",beforeUnload);});
</script>
<template>
  <nav class="breadcrumb" aria-label="位置"><RouterLink :to="baseLink">产品库</RouterLink><span>/</span><span>产品详情</span></nav>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
  <p v-if="loading" role="status">正在读取精确产品修订…</p>
  <template v-if="detail">
    <div class="page-header"><div><h2>{{revision.data.name}}</h2><p class="subtitle">由技术负责人或单位管理员维护 · 内容修订 {{revision.revision}} · 当前修订 {{detail.current_revision}}</p></div><el-tag :type="detail.lifecycle.state==='active'?'success':'info'">{{detail.lifecycle.state==='active'?'可用':'已停用'}}</el-tag></div>
    <el-alert :title="detail.provenance==='simulated'?'模拟拟投声明':'产品声明'" description="产品资料及来源地址是声明，尚未证明真实性、性能或证据确认；任务不会自动升级到新内容修订。" type="warning" :closable="false" class="section" />
    <el-alert v-if="receipt?.type==='revision'" :title="`已保存内容修订 ${receipt.data.revision} · 修订 ID ${receipt.data.id}`" type="success" :closable="false" role="status" class="section" />
    <p v-if="receipt?.type==='revision'"><RouterLink :to="exactLink(receipt.data.revision)">查看刚保存的精确修订</RouterLink></p>
    <el-alert v-if="receipt?.type==='lifecycle'" :title="`已${receipt.data.lifecycle.state==='active'?'恢复':'停用'} · 生命周期修订 ${receipt.data.lifecycle.revision} · 已有任务固定版本保留`" type="success" :closable="false" role="status" class="section" />
    <el-alert v-if="receipt?.type==='pin'" :title="`${receipt.data.duplicate?'已有相同固定版本':'任务已固定修订 '+receipt.data.revision} · 修订 ${receipt.data.revision} · 选择 ID ${receipt.data.id} · 分包 ${receipt.data.lot??'未指定'}${receipt.data.replaced_snapshot_id?' · 替换选择 '+receipt.data.replaced_snapshot_id:''}`" type="success" :closable="false" role="status" class="section" />
    <el-card shadow="never" class="section"><template #header><div class="section-title"><h3>精确型号与来源</h3><el-button v-if="canRevise&&!editing" :disabled="busy" @click="startEdit">修订产品</el-button><RouterLink v-if="!current" :to="exactLink(detail.current_revision)">查看当前修订</RouterLink></div></template>
      <dl class="product-details"><dt>厂家</dt><dd>{{revision.data.vendor}}</dd><dt>精确型号</dt><dd>{{revision.data.model}}</dd><dt>型号版本</dt><dd>{{revision.data.model_version??'未声明'}}</dd><dt>官网规格页</dt><dd>{{revision.data.official_url??'未声明'}}</dd><dt>白皮书</dt><dd>{{revision.data.whitepaper_url??'未声明'}}</dd><dt>内容修订 ID</dt><dd class="mono">{{revision.id}}</dd><dt>修订者 / 时间</dt><dd>{{detail.revised_by??'未知'}} · {{formatTime(detail.revised_at)}}</dd><dt>生命周期修订</dt><dd>{{detail.lifecycle.revision}}（与内容修订分别记录）</dd></dl>
      <template v-if="editing"><el-alert v-if="conflict" :title="`当前服务器内容修订 ${conflict.revision}，草稿已保留`" type="warning" :closable="false" class="section"/><dl v-if="conflict" class="product-details"><template v-for="(value,key) in conflict.data" :key="key"><dt>{{key}}</dt><dd>{{value??'未声明'}}</dd></template></dl><ProductEditor v-model="form" :busy="busy" @save="save" @cancel="cancel" /></template>
      <div v-if="!editing&&current&&(allowed('deactivate')||allowed('restore'))" class="actions"><el-select v-if="detail.lifecycle.state==='active'" v-model="reason" aria-label="停用原因" style="width:150px"><el-option v-for="code in ['obsolete','duplicate','unavailable','other']" :key="code" :value="code" :label="reasonLabels[code]" /></el-select><el-button :disabled="busy" @click="lifecycle">{{detail.lifecycle.state==='active'?'停用产品':'恢复产品'}}</el-button><span class="hint">停用仅阻止新的选择或版本替换。</span></div>
    </el-card>
    <el-card v-if="taskId" shadow="never" class="section"><template #header><h3>任务固定版本</h3></template><el-alert v-if="taskError" :title="taskError" type="error" :closable="false" role="alert"/><p class="mono">任务 ID：{{taskId}}</p><template v-if="authority"><p v-if="authority.workflow.state==='archived'">任务已归档，只能读取已有记录。</p><p v-else-if="!canPin">需要任务负责人或协作者并具备单位资源选择权限；审阅者、观察者和非成员管理员不能选择。</p><template v-else><p v-if="detail.lifecycle.state==='inactive'">停用产品只允许核对已有的相同固定版本；新的选择和替换会被服务器拒绝。</p><el-button :disabled="busy||editing" type="primary" @click="openPin">{{detail.lifecycle.state==='inactive'?'核对已有固定版本':'选择到任务'}}</el-button></template><p><RouterLink :to="`/org/tasks/${taskId}`">返回已授权任务</RouterLink></p></template></el-card>
    <el-card shadow="never" class="section"><div class="actions" role="group" aria-label="历史类型"><el-button :type="tab==='content'?'primary':'default'" @click="chooseTab('content')">内容修订历史</el-button><el-button :type="tab==='lifecycle'?'primary':'default'" @click="chooseTab('lifecycle')">生命周期记录</el-button></div>
      <template v-if="tab==='content'"><el-table :data="history" aria-label="内容修订历史"><el-table-column label="修订" width="100"><template #default="{row}"><RouterLink :to="exactLink(row.revision)">修订 {{row.revision}}</RouterLink></template></el-table-column><el-table-column prop="name" label="产品名称" min-width="160"/><el-table-column label="修订者 / 时间" min-width="210"><template #default="{row}">{{row.created_by??'未知'}} · {{formatTime(row.created_at)}}</template></el-table-column><el-table-column label="当前" width="90"><template #default="{row}">{{row.current?'当前修订':'历史修订'}}</template></el-table-column></el-table><div class="actions"><el-button @click="restartHistory('content')">从第一页读取内容历史</el-button><el-button :disabled="!historyPrevious.length" @click="previousHistory('content')">上一页内容历史</el-button><el-button :disabled="!historyMeta?.has_more" @click="nextHistory('content')">下一页内容历史</el-button></div></template>
      <template v-else><el-empty v-if="lifecycleMeta&&!lifecycleHistory.length" description="暂无停用或恢复记录"/><el-table v-else :data="lifecycleHistory" aria-label="生命周期记录"><el-table-column label="状态变化" min-width="140"><template #default="{row}">{{row.before==='active'?'可用':'已停用'}} → {{row.after==='active'?'可用':'已停用'}}</template></el-table-column><el-table-column label="版本" min-width="150"><template #default="{row}">生命周期 {{row.revision}} · 内容 {{row.resource_revision}}</template></el-table-column><el-table-column label="原因" min-width="110"><template #default="{row}">{{reasonLabels[row.reason_code]??row.reason_code}}</template></el-table-column><el-table-column label="操作人 / 时间" min-width="220"><template #default="{row}">{{row.actor_user_id}} · {{formatTime(row.created_at)}}</template></el-table-column></el-table><div class="actions"><el-button @click="restartHistory('lifecycle')">从第一页读取生命周期</el-button><el-button :disabled="!lifecyclePrevious.length" @click="previousHistory('lifecycle')">上一页生命周期</el-button><el-button :disabled="!lifecycleMeta?.has_more" @click="nextHistory('lifecycle')">下一页生命周期</el-button></div></template>
    </el-card>
    <el-dialog v-model="pinDialog" title="固定精确产品修订" width="min(580px, calc(100vw - 24px))" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy"><p>将 {{revision.data.name}} · {{revision.data.model}} · 修订 {{revision.revision}} 固定到此任务。</p><p class="hint">选择以任务、产品和分包为单位；选择其他产品不会移除此产品。相同产品和分包改选修订会使依赖响应需要重新审阅；产品库后续更新不会自动替换固定版本。</p><label for="product-lot">分包（可选）</label><el-input id="product-lot" v-model="lot" maxlength="100" aria-label="分包（可选）" :disabled="busy"/><template #footer><el-button :disabled="busy" @click="pinDialog=false">取消</el-button><el-button type="primary" :disabled="busy||!canPin" :loading="busy" @click="pin">确认选择修订 {{revision.revision}}</el-button></template></el-dialog>
  </template>
</template>
<style scoped>.product-details{display:grid;grid-template-columns:140px minmax(0,1fr);gap:8px 16px}.product-details dt{color:var(--muted)}.product-details dd{margin:0;overflow-wrap:anywhere}@media(max-width:600px){.product-details{grid-template-columns:1fr;gap:4px}.product-details dd{margin-bottom:8px}}</style>
