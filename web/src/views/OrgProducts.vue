<script setup>
import {computed,onMounted,onUnmounted,ref,watch} from "vue";
import {onBeforeRouteLeave,onBeforeRouteUpdate,useRoute} from "vue-router";
import ProductEditor from "../components/ProductEditor.vue";
import {confirmAction,errorText,formatTime,orgRequest} from "../org.js";
import {blankProduct,checkProductPage,checkProductRevision,productMaintainer,productPath,productReader,rememberProductCursor,validId} from "../products.js";
const route=useRoute(),reader=productReader();
const search=ref(""),state=ref("active"),rows=ref([]),meta=ref(null),cursor=ref(null),previous=ref([]),error=ref(""),loading=ref(false),busy=ref(false),editing=ref(false),form=ref(blankProduct()),receipt=ref(null);
const revoked=ref(false);
const writable=computed(()=>!revoked.value&&productMaintainer()),dirty=computed(()=>editing.value&&JSON.stringify(form.value)!==JSON.stringify(blankProduct()));
const taskId=computed(()=>validId(route.query.task)?route.query.task:null);let timer=null,generation=0,stopped=false;
function link(row){return {path:`/org/products/${row.ref.resource_id}`,query:{revision:String(row.revision),...(taskId.value?{task:taskId.value}:{})}};}
async function load(next=null){if(stopped)return;const run=++generation;reader.cancel("list");loading.value=true;error.value="";rows.value=[];meta.value=null;cursor.value=next;
  try{const q=search.value.trim();if(q.length>200)throw new Error("搜索文字不能超过 200 字");const result=checkProductPage(await reader.read("list","POST",`${productPath}/query`,{q:q||null,state:state.value,cursor:next,limit:25}));if(stopped||run!==generation)return;rows.value=result.items;meta.value=result.data;}
  catch(exc){if(run===generation&&exc.name!=="AbortError"){error.value=errorText(exc);if(["management_cursor_invalid","management_cursor_expired"].includes(exc.code)){cursor.value=null;previous.value=[];}}}finally{if(run===generation)loading.value=false;}
}
function resetList(){previous.value=[];load();}
watch([search,state],()=>{if(stopped)return;generation++;reader.cancel("list");rows.value=[];meta.value=null;clearTimeout(timer);timer=setTimeout(resetList,300);});
function next(){if(!meta.value?.has_more)return;rememberProductCursor(previous.value,cursor.value);load(meta.value.next_cursor);}
function back(){if(previous.value.length)load(previous.value.pop());}
async function cancel(){if(dirty.value&&!await confirmAction("放弃未保存的产品内容？","放弃未保存编辑","放弃编辑"))return;editing.value=false;form.value=blankProduct();}
async function save(){if(!writable.value||busy.value)return;busy.value=true;error.value="";
 try{const result=await orgRequest("POST","/resources/products",{data:form.value},{contractVersion:4});receipt.value=checkProductRevision(result.data);editing.value=false;form.value=blankProduct();resetList();}catch(exc){if([401,403,404].includes(exc.status)){revoked.value=true;stopped=true;generation++;reader.stop();editing.value=false;form.value=blankProduct();receipt.value=null;rows.value=[];meta.value=null;}error.value=errorText(exc);}finally{busy.value=false;}}
function clear(){stopped=true;generation++;clearTimeout(timer);reader.stop();rows.value=[];meta.value=null;form.value=blankProduct();editing.value=false;receipt.value=null;search.value="";error.value="单位会话已改变，请重新打开产品库";}
function beforeUnload(event){if(dirty.value){event.preventDefault();event.returnValue="";}}
const canLeave=async()=>!dirty.value||await confirmAction("放弃未保存的产品内容？","放弃未保存编辑","放弃编辑");
onBeforeRouteLeave(canLeave);onBeforeRouteUpdate(canLeave);
onMounted(()=>{load();window.addEventListener("bid:org-reset",clear);window.addEventListener("beforeunload",beforeUnload);});
onUnmounted(()=>{stopped=true;clearTimeout(timer);reader.stop();window.removeEventListener("bid:org-reset",clear);window.removeEventListener("beforeunload",beforeUnload);});
</script>
<template>
  <div class="page-header"><div><h2>产品库</h2><p class="subtitle">由技术负责人或单位管理员维护。任务固定所选内容修订，产品库更新不会自动升级任务。</p></div><el-button v-if="writable&&!editing" type="primary" @click="editing=true;receipt=null">新增产品</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" class="section" />
  <el-alert v-if="receipt" :title="`已创建产品：${receipt.data.name} · 内容修订 ${receipt.revision} · 修订 ID ${receipt.id}`" type="success" :closable="false" role="status" class="section" />
  <el-card v-if="editing" shadow="never" class="section"><template #header><h3>新增产品</h3></template><ProductEditor v-model="form" :busy="busy" @save="save" @cancel="cancel" /></el-card>
  <el-card shadow="never" class="section">
    <div class="actions"><div class="product-search"><label for="product-search">搜索产品</label><el-input id="product-search" v-model="search" maxlength="200" aria-label="搜索产品" placeholder="名称、厂家、型号的前缀词" clearable /></div><div><label for="product-state">生命周期</label><el-select id="product-state" v-model="state" aria-label="生命周期" style="width:140px"><el-option label="可用" value="active"/><el-option label="已停用" value="inactive"/><el-option label="全部" value="all"/></el-select></div><el-button :disabled="loading" @click="resetList">重新读取产品</el-button></div>
    <p v-if="loading" role="status">正在读取产品…</p>
    <el-table v-else-if="rows.length" :data="rows" row-key="revision_id" aria-label="产品列表"><el-table-column label="产品 / 修订" min-width="200"><template #default="{row}"><RouterLink :to="link(row)">{{row.name}}</RouterLink><p class="hint">内容修订 {{row.revision}} · {{row.provenance==='simulated'?'模拟拟投声明':'产品声明'}}</p></template></el-table-column><el-table-column label="生命周期" width="110"><template #default="{row}"><el-tag :type="row.lifecycle.state==='active'?'success':'info'">{{row.lifecycle.state==='active'?'可用':'已停用'}}</el-tag></template></el-table-column><el-table-column label="最近修订" min-width="210"><template #default="{row}">{{formatTime(row.revised_at)}}<p class="hint">修订者：{{row.revised_by??'未知'}}</p></template></el-table-column><el-table-column label="下一步" width="110"><template #default="{row}"><RouterLink :to="link(row)">查看修订</RouterLink></template></el-table-column></el-table>
    <el-empty v-else-if="!error" description="没有符合条件的产品" />
    <div class="actions"><el-button :disabled="loading||!previous.length" @click="back">上一页产品</el-button><el-button :disabled="loading||!meta?.has_more" @click="next">下一页产品</el-button><span v-if="meta" class="hint">本页 {{meta.returned}} 项 · 读取时间 {{formatTime(meta.as_of)}}；分页反映各页读取时的当前状态。</span></div>
  </el-card>
</template>
<style scoped>.product-search{width:min(360px,100%)}.actions>div>label{display:block;margin-bottom:4px}</style>
