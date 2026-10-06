<script setup>
import {computed,onMounted,onUnmounted,ref,watch} from "vue";
import {onBeforeRouteLeave,onBeforeRouteUpdate,useRoute} from "vue-router";
import FeatureProductPicker from "../components/FeatureProductPicker.vue";
import FeatureEditor from "../components/FeatureEditor.vue";
import {confirmAction,errorText,formatTime,orgRequest,orgAccess} from "../org.js";
import {blankFeature,checkFeaturePage,checkFeatureRevision,featureMaintainer,featurePath,featureReader,rememberFeatureCursor,validId,implementationLabels} from "../features.js";
const route=useRoute(),reader=featureReader();
const search=ref(""),state=ref("active"),rows=ref([]),meta=ref(null),cursor=ref(null),previous=ref([]),error=ref(""),loading=ref(false),busy=ref(false),editing=ref(false),form=ref(blankFeature()),receipt=ref(null);
const revoked=ref(false),productId=ref(""),implementationStatus=ref("");
const writable=computed(()=>!revoked.value&&featureMaintainer()),dirty=computed(()=>editing.value&&JSON.stringify(form.value)!==JSON.stringify(blankFeature()));
const taskId=computed(()=>validId(route.query.task)?route.query.task:null);let timer=null,generation=0,stopped=false;
function link(row){return {path:`/org/features/${row.ref.resource_id}`,query:{revision:String(row.revision),...(taskId.value?{task:taskId.value}:{})}};}
async function load(next=null){if(stopped)return;const run=++generation;reader.cancel("list");loading.value=true;error.value="";rows.value=[];meta.value=null;cursor.value=next;
  try{const q=search.value.trim();if(q.length>200)throw new Error("搜索文字不能超过 200 字");const result=checkFeaturePage(await reader.read("list","POST",`${featurePath}/query`,{q:q||null,state:state.value,product_id:productId.value||null,implementation_status:implementationStatus.value||null,cursor:next,limit:25}));if(stopped||run!==generation)return;rows.value=result.items;meta.value=result.data;}
  catch(exc){if(run===generation&&exc.name!=="AbortError"){error.value=errorText(exc);if([401,403,404].includes(exc.status))deny(exc);if(["management_cursor_invalid","management_cursor_expired"].includes(exc.code)){cursor.value=null;previous.value=[];}}}finally{if(run===generation)loading.value=false;}
}
function resetList(){previous.value=[];load();}
watch([search,state,productId,implementationStatus],()=>{if(stopped)return;generation++;reader.cancel("list");loading.value=true;error.value="";rows.value=[];meta.value=null;clearTimeout(timer);timer=setTimeout(resetList,300);});
function next(){if(!meta.value?.has_more)return;rememberFeatureCursor(previous.value,cursor.value);load(meta.value.next_cursor);}
function back(){if(previous.value.length)load(previous.value.pop());}
async function cancel(){if(dirty.value&&!await confirmAction("放弃未保存的功能内容？","放弃未保存编辑","放弃编辑"))return;editing.value=false;form.value=blankFeature();}
async function save(){if(!writable.value||busy.value)return;busy.value=true;error.value="";
 try{const result=await orgRequest("POST","/resources/features",{data:form.value},{contractVersion:4});receipt.value=checkFeatureRevision(result.data);editing.value=false;form.value=blankFeature();resetList();}catch(exc){if([401,403,404].includes(exc.status)){revoked.value=true;stopped=true;generation++;reader.stop();editing.value=false;form.value=blankFeature();receipt.value=null;rows.value=[];meta.value=null;}error.value=errorText(exc);}finally{busy.value=false;}}
function deny(exc){revoked.value=true;clear();error.value=errorText(exc);}
function clear(){revoked.value=true;loading.value=false;stopped=true;generation++;clearTimeout(timer);reader.stop();rows.value=[];meta.value=null;form.value=blankFeature();editing.value=false;receipt.value=null;search.value="";productId.value="";implementationStatus.value="";error.value="单位会话已改变，请重新打开功能库";}
watch(()=>orgAccess.role,(role,previous)=>{if(["admin","technical"].includes(previous)&&!["admin","technical"].includes(role)){clear();error.value="维护权限已改变，未保存内容已清除，请重新打开功能库";}});
function beforeUnload(event){if(dirty.value){event.preventDefault();event.returnValue="";}}
const canLeave=async()=>!dirty.value||await confirmAction("放弃未保存的功能内容？","放弃未保存编辑","放弃编辑");
onBeforeRouteLeave(canLeave);onBeforeRouteUpdate(canLeave);
onMounted(()=>{load();window.addEventListener("bid:org-reset",clear);window.addEventListener("beforeunload",beforeUnload);});
onUnmounted(()=>{stopped=true;clearTimeout(timer);reader.stop();window.removeEventListener("bid:org-reset",clear);window.removeEventListener("beforeunload",beforeUnload);});
</script>
<template>
  <div class="page-header"><div><h2>功能库</h2><p class="subtitle">由技术负责人或单位管理员维护。任务固定所选内容修订，功能库更新不会自动升级任务。实现声明仍需对应材料，维护功能不会替换截图或确认原型。</p></div><el-button v-if="writable&&!editing" type="primary" @click="editing=true;receipt=null">新增功能</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" class="section" />
  <el-alert v-if="receipt" :title="`已创建功能：${receipt.data.name} · 内容修订 ${receipt.revision} · 修订 ID ${receipt.id}`" type="success" :closable="false" role="status" class="section" />
  <el-card v-if="editing" shadow="never" class="section"><template #header><h3>新增功能</h3></template><FeatureEditor v-model="form" :busy="busy" :reader="reader" @denied="deny" @save="save" @cancel="cancel" /></el-card>
  <el-card shadow="never" class="section">
    <div class="actions"><div class="feature-search"><label for="feature-search">搜索功能</label><el-input id="feature-search" v-model="search" maxlength="200" aria-label="搜索功能" placeholder="功能名称前缀" clearable /></div><div><label for="feature-state">生命周期</label><el-select id="feature-state" v-model="state" aria-label="生命周期" style="width:140px"><el-option label="可用" value="active"/><el-option label="已停用" value="inactive"/><el-option label="全部" value="all"/></el-select></div><el-button :disabled="loading" @click="resetList">重新读取功能</el-button></div>
    <FeatureProductPicker v-if="!revoked" v-model="productId" :reader="reader" channel="filter-products" optional @denied="deny"/><div class="section"><label for="implementation-filter">筛选实现状态</label><select id="implementation-filter" v-model="implementationStatus" aria-label="筛选实现状态"><option value="">全部实现状态</option><option v-for="(label,value) in implementationLabels" :key="value" :value="value">{{label}}</option></select></div>
    <p v-if="loading" role="status">正在读取功能…</p>
    <el-table v-else-if="rows.length" :data="rows" row-key="revision_id" aria-label="功能列表"><el-table-column label="功能 / 修订" min-width="200"><template #default="{row}"><RouterLink :to="link(row)">{{row.name}}</RouterLink><p class="hint">内容修订 {{row.revision}} · {{row.provenance==='simulated'?'模拟拟投声明':'功能声明'}}</p></template></el-table-column><el-table-column label="生命周期" width="110"><template #default="{row}"><el-tag :type="row.lifecycle.state==='active'?'success':'info'">{{row.lifecycle.state==='active'?'可用':'已停用'}}</el-tag></template></el-table-column><el-table-column label="最近修订" min-width="210"><template #default="{row}">{{formatTime(row.revised_at)}}<p class="hint">修订者：{{row.revised_by??'未知'}}</p></template></el-table-column><el-table-column label="下一步" width="110"><template #default="{row}"><RouterLink :to="link(row)">查看修订</RouterLink></template></el-table-column></el-table>
    <el-empty v-else-if="meta&&!error" description="没有符合条件的功能" />
    <div class="actions"><el-button :disabled="loading||!previous.length" @click="back">上一页功能</el-button><el-button :disabled="loading||!meta?.has_more" @click="next">下一页功能</el-button><span v-if="meta" class="hint">本页 {{meta.returned}} 项 · 读取时间 {{formatTime(meta.as_of)}}；分页反映各页读取时的当前状态。</span></div>
  </el-card>
</template>
<style scoped>.feature-search{width:min(360px,100%)}.actions>div>label{display:block;margin-bottom:4px}</style>
