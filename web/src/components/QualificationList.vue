<script setup>
import {computed,onMounted,onUnmounted,ref,watch} from 'vue';
import {onBeforeRouteLeave,onBeforeRouteUpdate,useRoute} from 'vue-router';
import QualificationEditor from './QualificationEditor.vue';
import {confirmAction,errorText,formatTime,orgRequest} from '../org.js';
import {blankQualification,checkQualificationPage,checkQualificationRevision,qualificationData,qualificationKinds,qualificationMaintainer,qualificationPath,rememberQualificationCursor,validId} from '../qualifications.js';
const props=defineProps({kind:{type:String,required:true},reader:{type:Object,required:true}}),route=useRoute();
const label=qualificationKinds[props.kind].label,channel=`list-${props.kind}`;
const search=ref(''),state=ref('active'),rows=ref([]),meta=ref(null),cursor=ref(null),previous=ref([]),error=ref(''),loading=ref(false),busy=ref(false),editing=ref(false),form=ref(blankQualification(props.kind)),receipt=ref(null),fields=ref([]),revoked=ref(false);
const writable=computed(()=>!revoked.value&&qualificationMaintainer()),dirty=computed(()=>editing.value&&JSON.stringify(form.value)!==JSON.stringify(blankQualification(props.kind)));
const taskId=computed(()=>validId(route.query.task)?route.query.task:null);let timer=null,generation=0,stopped=false;
function link(row){return {path:`/org/${props.kind}/${row.ref.resource_id}`,query:{revision:String(row.revision),...(taskId.value?{task:taskId.value}:{})}};}
async function load(next=null){if(stopped)return;const run=++generation;props.reader.cancel(channel);loading.value=true;error.value='';rows.value=[];meta.value=null;cursor.value=next;
 try{const q=search.value.trim();if(q.length>200)throw new Error('搜索文字不能超过 200 字');const result=checkQualificationPage(await props.reader.read(channel,'POST',`${qualificationPath(props.kind)}/query`,{q:q||null,state:state.value,cursor:next,limit:25}),props.kind);if(stopped||run!==generation)return;rows.value=result.items;meta.value=result.data;}
 catch(exc){if(run===generation&&exc.name!=='AbortError'){error.value=errorText(exc);if([401,403,404].includes(exc.status))clear(false);if(['management_cursor_invalid','management_cursor_expired'].includes(exc.code)){cursor.value=null;previous.value=[];}}}finally{if(run===generation)loading.value=false;}
}
function resetList(){previous.value=[];load();}
watch([search,state],()=>{if(stopped)return;generation++;props.reader.cancel(channel);loading.value=true;error.value='';rows.value=[];meta.value=null;clearTimeout(timer);timer=setTimeout(resetList,300);});
function next(){if(!meta.value?.has_more)return;rememberQualificationCursor(previous.value,cursor.value);load(meta.value.next_cursor);}
function back(){if(previous.value.length)load(previous.value.pop());}
async function start(){editing.value=true;receipt.value=null;if(props.kind==='profiles')try{const result=await props.reader.read(`fields-${props.kind}`,'GET','/confidential-fields');if(stopped)return;fields.value=result.items.map(({key,label,scope})=>({key,label,scope}));}catch(exc){if([401,403,404].includes(exc.status))clear(false);if(exc.name!=='AbortError')error.value=errorText(exc);}}
async function cancel(){if(dirty.value&&!await confirmAction(`放弃未保存的${label}内容？`,'放弃未保存编辑','放弃编辑'))return;editing.value=false;form.value=blankQualification(props.kind);fields.value=[];}
async function save(){if(!writable.value||busy.value||stopped)return;busy.value=true;error.value='';
 try{const result=await orgRequest('POST',`/resources/${props.kind}`,{data:qualificationData(props.kind,form.value)},{contractVersion:4});if(stopped)return;receipt.value=checkQualificationRevision(result.data,props.kind);editing.value=false;form.value=blankQualification(props.kind);fields.value=[];resetList();}catch(exc){if([401,403,404].includes(exc.status))clear(false);error.value=!exc.status?'保存结果尚未确定，请重新读取资料列表核对后再主动决定；不会自动重试。':errorText(exc);}finally{busy.value=false;}}
function clear(reset=true){loading.value=false;stopped=true;revoked.value=true;generation++;clearTimeout(timer);props.reader.cancel(channel);props.reader.cancel(`fields-${props.kind}`);rows.value=[];meta.value=null;form.value=blankQualification(props.kind);fields.value=[];editing.value=false;receipt.value=null;search.value='';if(reset)error.value=`单位会话已改变，请重新打开${label}列表`;}
function beforeUnload(event){if(dirty.value){event.preventDefault();event.returnValue='';}}
const canLeave=async()=>!dirty.value||await confirmAction(`放弃未保存的${label}内容？`,'放弃未保存编辑','放弃编辑');
onBeforeRouteLeave(canLeave);onBeforeRouteUpdate(canLeave);
onMounted(()=>{load();window.addEventListener('bid:org-reset',clear);window.addEventListener('beforeunload',beforeUnload);});
onUnmounted(()=>{stopped=true;generation++;clearTimeout(timer);props.reader.cancel(channel);props.reader.cancel(`fields-${props.kind}`);window.removeEventListener('bid:org-reset',clear);window.removeEventListener('beforeunload',beforeUnload);});
</script>
<template>
 <el-card shadow="never" class="section" :data-testid="`${kind}-list`">
  <template #header><div class="section-title"><h3>{{kind==='profiles'?'资料':'证照与附件'}}</h3><el-button v-if="writable&&!editing" type="primary" @click="start">{{kind==='profiles'?'新建资料':'添加证照'}}</el-button></div></template>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" class="section"/>
  <el-alert v-if="receipt" :title="`已创建${label} · 内容修订 ${receipt.revision} · 修订 ID ${receipt.id}`" type="success" :closable="false" role="status" class="section"/>
  <p v-if="receipt"><RouterLink :to="link({ref:{resource_id:receipt[qualificationKinds[kind].id]},revision:receipt.revision})">{{kind==='certificates'?'查看精确修订并上传原件':'查看刚保存的精确修订'}}</RouterLink></p>
  <QualificationEditor v-if="editing" v-model="form" :kind="kind" :fields="fields" :busy="busy" @save="save" @cancel="cancel"/>
  <div class="actions"><div class="search"><label :for="`${kind}-search`">搜索{{label}}</label><el-input :id="`${kind}-search`" v-model="search" :aria-label="`搜索${label}`" maxlength="200" :placeholder="kind==='certificates'?'名称、证号的前缀词':'名称的前缀词'" clearable/></div><div><label :for="`${kind}-state`">生命周期</label><el-select :id="`${kind}-state`" v-model="state" :aria-label="`${label}生命周期`" style="width:140px"><el-option label="可用" value="active"/><el-option label="已停用" value="inactive"/><el-option label="全部" value="all"/></el-select></div><el-button :disabled="loading||stopped" @click="resetList">重新读取{{label}}</el-button></div>
  <p v-if="loading" role="status">正在读取{{label}}…</p>
  <!-- Keep the table mounted so rows=[] clears its store; column teardown can
       rearm layout callbacks that retain an unmounted table's previous page. -->
  <el-table v-show="!loading&&rows.length" :data="rows" row-key="revision_id" :aria-label="`${label}列表`"><el-table-column :label="`${label} / 修订`" min-width="200"><template #default="{row}"><RouterLink :to="link(row)">{{row.name}}</RouterLink><p class="hint">内容修订 {{row.revision}} · 自行声明</p></template></el-table-column><el-table-column label="生命周期" width="110"><template #default="{row}"><el-tag :type="row.lifecycle.state==='active'?'success':'info'">{{row.lifecycle.state==='active'?'可用':'已停用'}}</el-tag></template></el-table-column><el-table-column label="最近修订" min-width="210"><template #default="{row}">{{formatTime(row.revised_at)}}<p class="hint">修订者：{{row.revised_by??'未知'}}</p></template></el-table-column><el-table-column label="下一步" width="150"><template #default="{row}"><RouterLink :to="link(row)">{{kind==='certificates'?'查看修订与原件':'查看修订'}}</RouterLink></template></el-table-column></el-table>
  <el-empty v-if="!loading&&meta&&!error&&!rows.length" :description="`没有符合条件的${label}`"/>
  <div class="actions"><el-button :disabled="loading||!previous.length" @click="back">上一页{{label}}</el-button><el-button :disabled="loading||!meta?.has_more" @click="next">下一页{{label}}</el-button><span v-if="meta" class="hint">本页 {{meta.returned}} 项 · 读取时间 {{formatTime(meta.as_of)}}；分页反映各页读取时的当前状态。</span></div>
 </el-card>
</template>
<style scoped>.search{width:min(360px,100%)}.actions{margin-top:16px}.actions>div>label{display:block;margin-bottom:4px}</style>
