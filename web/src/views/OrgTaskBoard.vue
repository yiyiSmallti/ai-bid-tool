<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { request } from "../api.js";
import { reviewStates } from "../requirement-review.js";
import { categories, domains, errorText, formatTime, label } from "../org.js";
import { buckets, blockers, actions, activityActions } from "../task-labels.js";
import { useTaskLive } from "../task-live.js";
import TaskNavigation from "../components/TaskNavigation.vue";
import TaskJobList from "../components/TaskJobList.vue";
const route=useRoute(),router=useRouter(),taskId=String(route.params.taskId);
const extended=ref(null);
const responseCounts=computed(()=>extended.value?.buckets.responses ?? data.value?.counts);
const history=ref([]),data=ref(null),rows=ref([]),error=ref(""),loading=ref(false),cursor=ref(null),prior=ref([]),requestGeneration=ref(0);
const job=computed(()=>String(route.query.job??""));
const filters=computed(()=>({bucket:String(route.query.bucket??""),category:String(route.query.category??""),review_domain:String(route.query.domain??""),blocker:String(route.query.blocker??""),starred:route.query.starred==="1",mine:route.query.mine==="1",limit:[25,50,100].includes(Number(route.query.limit))?Number(route.query.limit):50}));
function clear(exc){requestGeneration.value++;data.value=null;extended.value=null;rows.value=[];history.value=[];error.value=`任务不可访问：${errorText(exc)}`;}
async function load(){
  if(!job.value){data.value=null;rows.value=[];return null;}
  if(!history.value.some(item=>item.job_id===job.value&&item.status==="succeeded"))throw new Error("请选择一个成功且可访问的抽取");
  const epoch=++requestGeneration.value;loading.value=true;error.value="";
  const params=new URLSearchParams({extraction_job_id:job.value,view:"requirement-review",limit:String(filters.value.limit)});
  for(const [key,value] of Object.entries(filters.value))if(value&&key!=="limit")params.set(key,typeof value==="boolean"?"true":String(value));
  if(cursor.value)params.set("cursor",cursor.value);
  try{const result=await request("GET",`/tasks/${taskId}/board?${params}`, undefined, {});if(epoch!==requestGeneration.value)return null;
    if(result.data.board?.task_id!==taskId||result.data.board?.extraction_job_id!==job.value||result.data.scope?.extraction_job_id!==job.value||result.items.length>filters.value.limit||result.items.some(item=>item.response?.requirement_id!==item.requirement?.requirement_id))throw new Error("看板范围不符合契约");
    extended.value=result.data;data.value=result.data.board;rows.value=result.items.map(item=>({...item.response,requirementReview:item.requirement}));return result.data.board.event_cursor;
  }catch(exc){if(epoch===requestGeneration.value){data.value=null;rows.value=[];error.value=errorText(exc);if(["board_cursor_expired","invalid_board_cursor"].includes(exc.code)){cursor.value=null;prior.value=[];error.value+="；请重新读取首页。";}}throw exc;}
  finally{if(epoch===requestGeneration.value)loading.value=false;}
}
const live=useTaskLive(taskId,load,clear);
async function update(key,value){cursor.value=null;prior.value=[];const query={...route.query};if(value)query[key]=typeof value==="boolean"?"1":String(value);else delete query[key];await router.replace({query});}
async function next(){if(!data.value?.next_cursor)return;prior.value.push(cursor.value);cursor.value=data.value.next_cursor;await live.refresh();}
async function previous(){cursor.value=prior.value.pop()??null;await live.refresh();}
function actionPath(row,action){if(["inspect_annotated_material","confirm_annotated_material","retry_annotation_release","replace_annotation_source"].includes(action.code)&&row.card_id)return `/org/tasks/${taskId}/cards/${row.card_id}/annotation?job=${job.value}`;if(row.requirementReview?.bucket==="requirement_review")return `/org/tasks/${taskId}/requirements?job=${job.value}&requirement=${row.requirement_id}`;if(action.code==="view_job")return `/org/tasks/${taskId}/progress`;return `/org/tasks/${taskId}/review?job=${job.value}&requirement=${row.requirement_id}`;}
watch(()=>route.query,async()=>{await live.start();},{deep:true});
onMounted(async()=>{try{history.value=(await request("GET",`/tasks/${taskId}/extractions`)).items.filter(item=>item.status==="succeeded");if(job.value)await live.start();}catch(exc){clear(exc);}});
</script>
<template>
  <div class="page-header"><div><h2>任务看板</h2><p class="subtitle">进度只覆盖明确选定的抽取集合。计数和可执行动作来自当前授权的后端快照。已确认不代表无遗漏。</p></div><div><span role="status" aria-live="polite">{{live.status.value}}</span><el-button v-if="job" @click="cursor=null;prior=[];live.refresh()">重新读取首页</el-button></div></div>
  <TaskNavigation :task-id="taskId" />
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="section" />
  <el-card class="section" shadow="never"><el-form label-position="top"><el-form-item label="成功的抽取集合"><el-select :model-value="job" data-testid="extraction-select" placeholder="请明确选择抽取" @update:model-value="update('job',$event)"><el-option v-for="item in history" :key="item.job_id" :label="`${item.job_id}${item.created_at ? ' · '+formatTime(item.created_at) : ''}`" :value="item.job_id" /></el-select></el-form-item></el-form><p v-if="!job" class="hint">选择抽取后读取看板；不会自动选用最近一次结果。</p><template v-if="data"><p class="hint">快照 {{formatTime(data.as_of)}}；匹配 {{data.matching}} / {{data.counts.total}} 项</p></template></el-card>
  <template v-if="data">
    <div class="buckets section" role="group" aria-label="看板状态"><el-button :type="!filters.bucket?'primary':'default'" @click="update('bucket','')">全部 {{data.counts.total}}</el-button><el-button data-testid="bucket-requirement_review" :type="filters.bucket==='requirement_review'?'primary':'default'" @click="update('bucket','requirement_review')">要求待确认 {{extended.buckets.requirement_review}}</el-button><el-button v-for="(text,key) in buckets" :key="key" :data-testid="`bucket-${key}`" :type="filters.bucket===key?'primary':'default'" @click="update('bucket',key)">{{text}} {{responseCounts[key]}}</el-button></div>
    <el-card class="section" shadow="never"><el-form class="filters" label-position="top"><el-form-item label="分类"><el-select :model-value="filters.category" clearable @update:model-value="update('category',$event)"><el-option v-for="(text,key) in categories" :key="key" :label="text" :value="key" /></el-select></el-form-item><el-form-item label="审阅职责"><el-select :model-value="filters.review_domain" clearable @update:model-value="update('domain',$event)"><el-option v-for="(text,key) in domains" :key="key" :label="text" :value="key" /></el-select></el-form-item><el-form-item label="阻塞原因"><el-select :model-value="filters.blocker" clearable @update:model-value="update('blocker',$event)"><el-option v-for="(text,key) in blockers" :key="key" :label="text" :value="key" /></el-select></el-form-item><el-form-item label="每页条数"><el-select :model-value="filters.limit" @update:model-value="update('limit',$event)"><el-option v-for="size in [25,50,100]" :key="size" :label="String(size)" :value="size" /></el-select></el-form-item></el-form><div class="actions"><el-checkbox :model-value="filters.starred" @update:model-value="update('starred',$event)">★ 条款</el-checkbox><el-checkbox :model-value="filters.mine" @update:model-value="update('mine',$event)">待我处理</el-checkbox></div></el-card>
    <el-card class="section" shadow="never"><ul class="board-rows"><li v-for="row in rows" :key="row.requirement_id"><div><h3>{{row.starred?'★ ':''}}{{row.title}}</h3><p>{{label(categories,row.category)}} · {{row.requirementReview?.bucket==='requirement_review'?'要求待确认':label(buckets,row.bucket)}} · {{label(domains,row.review_domain,'待分类')}}</p><p class="hint">要求：{{label(reviewStates,row.requirementReview?.state)}} · 响应：{{label({draft:"草稿",pending_review:"待审阅",confirmed:"响应已确认",rejected:"已驳回",needs_material:"需补材料"},row.card_state,"尚无响应卡")}}</p><p class="hint" v-if="row.blockers.length || row.requirementReview?.blockers.length">{{[...new Set([...row.blockers,...(row.requirementReview?.blockers??[])])].map(code=>label(blockers,code)).join('；')}}</p></div><div class="actions"><RouterLink v-if="row.requirementReview?.bucket==='requirement_review'" :to="`/org/tasks/${taskId}/requirements?job=${job}&requirement=${row.requirement_id}`">{{row.requirementReview.next_action==='repair_requirement'?'核对引用修复':'核对并确认要求'}}</RouterLink><RouterLink :to="actionPath(row,row.next_actions[0])" v-else-if="row.next_actions[0]&&actions[row.next_actions[0].code]">{{actions[row.next_actions[0].code]}}</RouterLink><RouterLink v-else :to="`/org/tasks/${taskId}/review?job=${job}&requirement=${row.requirement_id}`">查看</RouterLink></div></li><li v-if="!rows.length" class="empty">当前筛选没有要求。</li></ul><div class="actions"><el-button :disabled="!prior.length||loading" @click="previous">上一页</el-button><el-button :disabled="!data.next_cursor||loading" @click="next">下一页</el-button><span class="hint">本页 {{rows.length}} 项</span></div></el-card>
    <div class="grid"><el-card class="section" shadow="never"><template #header><h3>作业进度</h3></template><TaskJobList :jobs="data.jobs" /></el-card><el-card class="section" shadow="never"><template #header><h3>最近活动</h3></template><ul class="activity"><li v-for="item in data.activity" :key="item.id">{{label(activityActions,item.action)}} · {{formatTime(item.created_at)}}</li><li v-if="!data.activity.length" class="empty">没有可见的活动。</li></ul></el-card></div>
  </template>
</template>
<style scoped>.buckets{display:flex;flex-wrap:wrap;gap:8px}.buckets .el-button{margin:0}.filters{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}.board-rows,.activity{padding:0;list-style:none}.board-rows li{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:16px 0;border-bottom:1px solid var(--border)}.board-rows h3{margin:0}.board-rows p{margin:6px 0}.board-rows a{flex-shrink:0}.activity li{padding:10px 0}@media(max-width:760px){.filters{grid-template-columns:repeat(2,minmax(0,1fr))}.board-rows li{align-items:flex-start;flex-direction:column}}</style>
