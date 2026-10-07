<script setup>
import {computed,onMounted,onUnmounted,ref,watch} from "vue";
import {onBeforeRouteLeave,onBeforeRouteUpdate,useRoute} from "vue-router";
import TemplateEditor from "../components/TemplateEditor.vue";
import {confirmAction,errorText,formatTime,orgRequest,orgAccess} from "../org.js";
import {templateUpload,blankTemplate,checkTemplatePage,checkTemplateRevision,templateMaintainer,templatePath,templateReader,rememberTemplateCursor,validId} from "../templates.js";
const route=useRoute(),reader=templateReader();
const search=ref(""),state=ref("active"),rows=ref([]),meta=ref(null),cursor=ref(null),previous=ref([]),error=ref(""),loading=ref(false),busy=ref(false),editing=ref(false),form=ref(blankTemplate()),receipt=ref(null);
const revoked=ref(false),file=ref(null);
const writable=computed(()=>!revoked.value&&templateMaintainer()),dirty=computed(()=>editing.value&&(file.value||JSON.stringify(form.value)!==JSON.stringify(blankTemplate())));
const taskId=computed(()=>validId(route.query.task)?route.query.task:null);let timer=null,generation=0,stopped=false;
function link(row){return {path:`/org/templates/${row.ref.resource_id}`,query:{revision:String(row.revision),...(taskId.value?{task:taskId.value}:{})}};}
async function load(next=null){if(stopped)return;const run=++generation;reader.cancel("list");loading.value=true;error.value="";rows.value=[];meta.value=null;cursor.value=next;
  try{const q=search.value.trim();if(q.length>200)throw new Error("搜索文字不能超过 200 字");const result=checkTemplatePage(await reader.read("list","POST",`${templatePath}/query`,{q:q||null,state:state.value,cursor:next,limit:25}));if(stopped||run!==generation)return;rows.value=result.items;meta.value=result.data;}
  catch(exc){if(run===generation&&exc.name!=="AbortError"){error.value=errorText(exc);if([401,403,404].includes(exc.status))deny(exc);if(["management_cursor_invalid","management_cursor_expired"].includes(exc.code)){cursor.value=null;previous.value=[];}}}finally{if(run===generation)loading.value=false;}
}
function resetList(){previous.value=[];load();}
watch([search,state],()=>{if(stopped)return;generation++;reader.cancel("list");loading.value=true;error.value="";rows.value=[];meta.value=null;clearTimeout(timer);timer=setTimeout(resetList,300);});
function next(){if(!meta.value?.has_more)return;rememberTemplateCursor(previous.value,cursor.value);load(meta.value.next_cursor);}
function back(){if(previous.value.length)load(previous.value.pop());}
async function cancel(){if(dirty.value&&!await confirmAction("放弃未保存的模板内容？","放弃未保存编辑","放弃编辑"))return;editing.value=false;form.value=blankTemplate();file.value=null;}
async function save(){if(!writable.value||busy.value)return;busy.value=true;error.value="";
 try{const result=await orgRequest("POST","/resources/templates",templateUpload(form.value,file.value),{contractVersion:4});receipt.value=checkTemplateRevision(result.data);editing.value=false;form.value=blankTemplate();file.value=null;resetList();}catch(exc){if([401,403,404].includes(exc.status)){revoked.value=true;stopped=true;generation++;reader.stop();editing.value=false;form.value=blankTemplate();file.value=null;receipt.value=null;rows.value=[];meta.value=null;}error.value=errorText(exc);}finally{busy.value=false;}}
function deny(exc){revoked.value=true;clear();error.value=errorText(exc);}
function clear(){revoked.value=true;loading.value=false;stopped=true;generation++;clearTimeout(timer);reader.stop();rows.value=[];meta.value=null;form.value=blankTemplate();file.value=null;editing.value=false;receipt.value=null;search.value="";error.value="单位会话已改变，请重新打开模板库";}
watch(()=>orgAccess.role,(role,previous)=>{if(previous==="admin"&&role!=="admin"){clear();error.value="维护权限已改变，未保存内容已清除，请重新打开模板库";}});
function beforeUnload(event){if(dirty.value){event.preventDefault();event.returnValue="";}}
const canLeave=async()=>!dirty.value||await confirmAction("放弃未保存的模板内容？","放弃未保存编辑","放弃编辑");
onBeforeRouteLeave(canLeave);onBeforeRouteUpdate(canLeave);
onMounted(()=>{load();window.addEventListener("bid:org-reset",clear);window.addEventListener("beforeunload",beforeUnload);});
onUnmounted(()=>{stopped=true;clearTimeout(timer);reader.stop();window.removeEventListener("bid:org-reset",clear);window.removeEventListener("beforeunload",beforeUnload);});
</script>
<template>
  <div class="page-header"><div><h2>模板库</h2><p class="subtitle">由单位管理员上传真实 DOCX 并维护声明。任务固定精确修订，模板库更新不会自动升级任务。上传校验、绑定审阅与任务导出分别处理。</p></div><el-button v-if="writable&&!editing" type="primary" @click="editing=true;receipt=null">新增模板</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" class="section" />
  <el-alert v-if="receipt" :title="`已创建模板：${receipt.data.name} · 内容修订 ${receipt.revision} · 修订 ID ${receipt.id}`" type="success" :closable="false" role="status" class="section" />
  <el-card v-if="editing" shadow="never" class="section"><template #header><h3>新增模板</h3></template><TemplateEditor v-model="form" :busy="busy" :file="file" @file="file=$event" @save="save" @cancel="cancel" /></el-card>
  <el-card shadow="never" class="section">
    <div class="actions"><div class="template-search"><label for="template-search">搜索模板</label><el-input id="template-search" v-model="search" maxlength="200" aria-label="搜索模板" placeholder="模板名称前缀" clearable /></div><div><label for="template-state">生命周期</label><el-select id="template-state" v-model="state" aria-label="生命周期" style="width:140px"><el-option label="可用" value="active"/><el-option label="已停用" value="inactive"/><el-option label="全部" value="all"/></el-select></div><el-button :disabled="loading" @click="resetList">重新读取模板</el-button></div>
    <p v-if="loading" role="status">正在读取模板…</p>
    <el-table v-else-if="rows.length" :data="rows" row-key="revision_id" aria-label="模板列表"><el-table-column label="模板 / 修订" min-width="200"><template #default="{row}"><RouterLink :to="link(row)">{{row.name}}</RouterLink><p class="hint">内容修订 {{row.revision}} · 模板声明</p></template></el-table-column><el-table-column label="生命周期" width="110"><template #default="{row}"><el-tag :type="row.lifecycle.state==='active'?'success':'info'">{{row.lifecycle.state==='active'?'可用':'已停用'}}</el-tag></template></el-table-column><el-table-column label="最近修订" min-width="210"><template #default="{row}">{{formatTime(row.revised_at)}}<p class="hint">修订者：{{row.revised_by??'未知'}}</p></template></el-table-column><el-table-column label="下一步" width="110"><template #default="{row}"><RouterLink :to="link(row)">查看修订</RouterLink></template></el-table-column></el-table>
    <el-empty v-else-if="meta&&!error" description="没有符合条件的模板" />
    <div class="actions"><el-button :disabled="loading||!previous.length" @click="back">上一页模板</el-button><el-button :disabled="loading||!meta?.has_more" @click="next">下一页模板</el-button><span v-if="meta" class="hint">本页 {{meta.returned}} 项 · 读取时间 {{formatTime(meta.as_of)}}；分页反映各页读取时的当前状态。</span></div>
  </el-card>
</template>
<style scoped>.template-search{width:min(360px,100%)}.actions>div>label{display:block;margin-bottom:4px}</style>
