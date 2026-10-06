<script setup>
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { orgSession } from "../api.js";
import { errorText, orgRequest } from "../org.js";
import { useTaskLive } from "../task-live.js";
import TaskNavigation from "../components/TaskNavigation.vue";
import TaskJobList from "../components/TaskJobList.vue";
const route=useRoute(),taskId=String(route.params.taskId),scope=ref(null),data=ref(null),jobs=ref([]),error=ref(""),cursor=ref(null),prior=ref([]),generation=ref(0);
function clear(exc){generation.value++;scope.value=null;data.value=null;jobs.value=[];error.value=`任务不可访问：${errorText(exc)}`;}
async function load(){const run=++generation.value;const params=new URLSearchParams({limit:"20"});if(route.query.job){params.set("view","requirement-review");params.set("extraction_job_id",String(route.query.job));}if(cursor.value)params.set("cursor",cursor.value);try{const result=await orgRequest("GET",`/tasks/${taskId}/progress?${params}`,undefined,{contractVersion:route.query.job?4:undefined});if(route.query.job){scope.value=result.data.scope;result.data=result.data.progress;if(scope.value?.task_id!==taskId||scope.value?.org_id!==orgSession.get()?.orgId||scope.value?.extraction_job_id!==String(route.query.job))throw new Error("要求进度集合不符合契约");}if(run!==generation.value)return null;if(result.data.task_id!==taskId||result.data.org_id!==orgSession.get()?.orgId||result.items.length>20)throw new Error("进度范围不符合契约");data.value=result.data;jobs.value=result.items;error.value="";return result.data.event_cursor;}catch(exc){if(run===generation.value){data.value=null;jobs.value=[];error.value=errorText(exc);}throw exc;}}
const live=useTaskLive(taskId,load,clear);
async function next(){prior.value.push(cursor.value);cursor.value=data.value.next_cursor;await live.refresh();}
async function previous(){cursor.value=prior.value.pop()??null;await live.refresh();}
onMounted(live.start);
</script>
<template><div class="page-header"><div><h2>作业进度</h2><p class="subtitle">显示已提交的作业状态和真实工作量；重连只读取记录，不重复提交作业。</p></div><span role="status" aria-live="polite">{{live.status.value}}</span></div><TaskNavigation :task-id="taskId" /><el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="section" /><el-card v-if="data" shadow="never" class="section"><p>任务状态：{{data.workflow.state==='archived'?'已归档':'进行中'}}</p><p v-if="scope">{{scope.origin==='manual'?'人工补录集':'模型抽取集'}} · 已确认 {{scope.summary.confirmed}} / {{scope.summary.total}} · 待确认 {{scope.summary.unconfirmed}} · 历史待确认 {{scope.summary.legacy_unconfirmed}} · 已失效 {{scope.summary.invalidated}} · 补录 {{scope.summary.manual_added}} · 报告拒绝 {{scope.summary.rejected_reported??'未知'}}。已确认不代表无遗漏。</p><TaskJobList :jobs="jobs" /><div class="actions"><el-button :disabled="!prior.length" @click="previous">上一页</el-button><el-button :disabled="!data.next_cursor" @click="next">下一页</el-button><el-button @click="cursor=null;prior=[];live.refresh()">重新读取首页</el-button></div></el-card></template>
