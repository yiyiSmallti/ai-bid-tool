<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute } from "vue-router";
import AssessmentBudget from "../components/AssessmentBudget.vue";
import JobPanel from "../components/JobPanel.vue";
import { assessmentError, enc, queryPath, stateLabels, suggestedCap, usePreviewInvalidation } from "../assessments.js";
import { jobStatuses, orgRequest } from "../org.js";
const route = useRoute(), taskId = computed(() => String(route.params.taskId)), extractionId = computed(() => String(route.query.job ?? ""));
const task = ref(null), inputs = ref(null), rubrics = ref([]), historyMeta = ref(null), jobs = ref([]), jobsMeta = ref(null), activeJob = ref("");
const preview = ref(null), cap = ref(""), reasoning = ref(""), consent = ref(false), retry = ref(false), busy = ref(false), loading = ref(false), error = ref(""), queueRequest = ref(null), cached = ref(false);
let sequence = 0, previewSerial = 0, previewKey = "";
const base = computed(() => `/tasks/${enc(taskId.value)}/score-rubrics`);
const canGenerate = computed(() => inputs.value?.actions?.some(a => a.action === 'rubric_generate' && a.allowed));
const blocker = computed(() => preview.value?.budget_preflight?.admission_blocker ?? preview.value?.admission_blocker);
const key = () => JSON.stringify([taskId.value, extractionId.value, cap.value, reasoning.value]);
function invalidate() { previewSerial++; preview.value = null; consent.value = false; previewKey = ""; queueRequest.value = null; }
usePreviewInvalidation(invalidate, taskId);
watch([cap, reasoning], invalidate, { flush: "sync" });
async function history(cursor = null) { const current = sequence; const result = await orgRequest("GET", queryPath(base.value, { view: 'console', extraction_job_id: extractionId.value, limit: 50, cursor })); if (current !== sequence) return; rubrics.value = result.items; historyMeta.value = result.data; }
async function discover(cursor = null) { const current = sequence; const result = await orgRequest("GET", queryPath(`/tasks/${enc(taskId.value)}/jobs`, { kind: 'score_rubric', extraction_job_id: extractionId.value, limit: 50, cursor })); if (current !== sequence) return; jobs.value = result.items; jobsMeta.value = result.data; if (!activeJob.value) activeJob.value = jobs.value.find(j => ['queued', 'running'].includes(j.status))?.id ?? ''; }
async function load() {
  const current = ++sequence; loading.value = true; error.value = ''; inputs.value = null; rubrics.value = []; jobs.value = []; activeJob.value = ''; invalidate();
  if (!extractionId.value) { error.value = '请先在任务中明确选择成功的提取结果'; loading.value = false; return; }
  try { const result = await Promise.all([orgRequest('GET', `/tasks/${enc(taskId.value)}`), orgRequest('GET', queryPath(`/tasks/${enc(taskId.value)}/assessment-inputs`, { job: extractionId.value }))]); if (current !== sequence) return; task.value = result[0].data; inputs.value = result[1].data; if (inputs.value.task_id !== taskId.value || inputs.value.extraction_job_id !== extractionId.value) throw { status: 404 }; await Promise.all([history(), discover()]); }
  catch (exc) { if (exc.name !== 'AbortError' && current === sequence) error.value = assessmentError(exc); } finally { if (current === sequence) loading.value = false; }
}
function requestBody(dry) { return { extraction_job_id: extractionId.value, dry_run: dry, ...(cap.value ? { max_charge: cap.value } : {}), ...(reasoning.value ? { reasoning: reasoning.value } : {}), ...(!dry ? { expected_input_hash: preview.value.input.input_hash, retry: retry.value } : {}) }; }
async function preflight() {
  invalidate(); busy.value = true; error.value = ''; let selected = key(), attempt = previewSerial;
  try {
    let result = await orgRequest('POST', `${base.value}/preview`, requestBody(true));
    if (selected !== key() || attempt !== previewSerial) return;
    if (!cap.value && result.data.estimated_charge != null) {
      cap.value = suggestedCap(result.data.estimated_charge); selected = key(); attempt = previewSerial;
      result = await orgRequest('POST', `${base.value}/preview`, requestBody(true));
      if (selected !== key() || attempt !== previewSerial) return;
    }
    const data = result.data, input = data.input;
    if (input?.task_id !== taskId.value || input.org_id !== inputs.value.org_id || input.extraction_job_id !== extractionId.value || input.document_id !== inputs.value.document_id || !/^[a-f0-9]{64}$/.test(input.input_hash) || data.budget_preflight?.input_hash !== input.input_hash) throw { code: 'rubric_input_changed' };
    preview.value = data; previewKey = selected;
  } catch (exc) { if (exc.name !== 'AbortError') error.value = assessmentError(exc); }
  finally { busy.value = false; }
}
async function submit(recover = false) {
  if (busy.value || !canGenerate.value || (!recover && (!preview.value || previewKey !== key() || blocker.value || !consent.value || !(Number(cap.value) > 0)))) return;
  const body = recover ? queueRequest.value : requestBody(false); if (!body) return; const generation = sequence; busy.value = true; error.value = '';
  try { const result = await orgRequest('POST', base.value, body); if (generation !== sequence) return; activeJob.value = result.data.job_id; cached.value = result.data.cached; invalidate(); await discover(); }
  catch (exc) { if (exc.name === 'AbortError' || generation !== sequence) return; error.value = assessmentError(exc); consent.value = false; if (exc.code === 'queue_unavailable' && exc.payload?.data.job_id) { activeJob.value = exc.payload.data.job_id; queueRequest.value = body; } else if (exc.code?.includes('input_changed')) invalidate(); } finally { busy.value = false; }
}
async function finished() { try { await Promise.all([history(), discover()]); } catch (exc) { error.value = assessmentError(exc); } }
function clear() { sequence++; inputs.value = null; task.value = null; rubrics.value = []; jobs.value = []; activeJob.value = ''; invalidate(); }
watch(() => [taskId.value, extractionId.value], load, { immediate: true }); window.addEventListener('bid:org-reset', clear); onBeforeUnmount(() => { clear(); window.removeEventListener('bid:org-reset', clear); });
</script>
<template>
  <nav class="breadcrumb" aria-label="位置"><RouterLink :to="`/org/tasks/${taskId}`">{{ task?.name ?? '任务' }}</RouterLink><span>/</span><span>评分规则</span></nav>
  <header class="page-header"><div><h2>评分规则</h2><p>提取 {{ extractionId || '尚未选择' }} · 从评分要求及其固定原文生成待审核规则。评分只评估已保存初稿。</p></div><RouterLink :to="`/org/tasks/${taskId}/scores?job=${extractionId}`">评分预估</RouterLink></header>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" /><el-skeleton v-if="loading" :rows="4" animated />
  <template v-if="inputs">
    <p>下一步：管理员分配审核职责，商务负责人（投标专员）核对评分要求覆盖，再由各职责负责人逐项审核。</p>
    <RouterLink :to="`/org/tasks/${taskId}/review?job=${extractionId}&category=scoring`">查看所选提取的评分要求</RouterLink>
    <el-card class="section" shadow="never"><h3>预览生成评分规则</h3><el-form label-position="top" :disabled="busy" @submit.prevent="preflight"><el-form-item label="推理档位（留空使用默认）"><el-input v-model="reasoning" aria-label="推理档位" /></el-form-item><el-form-item label="本次作业平台扣费上限"><el-input v-model="cap" aria-label="本次作业平台扣费上限" inputmode="decimal" /></el-form-item><el-button native-type="submit" type="primary" plain :loading="busy" :disabled="!canGenerate">预览生成评分规则</el-button><span v-if="!canGenerate">当前角色只读或尚未满足生成条件</span></el-form>
      <AssessmentBudget :preview="preview" :budget="inputs.task_budget" />
      <section v-if="preview" aria-label="本次评分规则范围"><h3>本次评分规则范围</h3><p>评分要求 {{ preview.scoring_requirement_ids.length }} 条 · 固定提取 {{ preview.input.extraction_job_id }}</p><el-alert v-if="!preview.scoring_requirement_ids.length" title="所选提取结果没有评分要求，请核对提取范围" type="warning" :closable="false" /><el-checkbox v-model="consent">我已核对外发范围与费用上限</el-checkbox><p><el-checkbox v-model="retry">显式重试已失败或取消的作业</el-checkbox></p><el-button type="primary" :loading="busy" :disabled="!!blocker || !preview.scoring_requirement_ids.length || !consent || !(Number(cap) > 0)" @click="submit()">提交生成评分规则</el-button></section>
    </el-card>
  </template>
  <p v-if="cached" role="status">已复用相同输入的结果</p><el-button v-if="queueRequest" :disabled="busy" @click="submit(true)">重新调度已保存作业</el-button>
  <JobPanel assessment-mode v-if="activeJob" :job-id="activeJob" :writable="!!jobs.find(j => j.id === activeJob)?.cancel?.allowed" @finished="finished" />
  <el-card v-if="inputs" class="section" shadow="never"><h3>评分规则生成作业</h3><p v-if="!jobs.length">尚无生成作业</p><div v-for="job in jobs" :key="job.id" class="actions"><el-button link @click="activeJob = job.id">查看作业 {{ job.id }}</el-button><span>{{ jobStatuses[job.status] ?? job.status }} · 尝试 {{ job.attempts }} 次</span><RouterLink v-if="job.result_id" :to="`/org/tasks/${taskId}/score-rubrics/${job.result_id}`">审核评分规则</RouterLink></div><el-button v-if="jobsMeta?.next_cursor" @click="discover(jobsMeta.next_cursor)">更多作业</el-button></el-card>
  <el-card v-if="inputs" class="section" shadow="never"><h3>评分规则版本</h3><el-empty v-if="!rubrics.length" description="尚未生成评分规则" /><div v-for="item in rubrics" :key="item.id" class="rubric-version"><RouterLink :to="`/org/tasks/${taskId}/score-rubrics/${item.id}`">审核第 {{ item.version }} 版评分规则</RouterLink><p>{{ stateLabels[item.state] }} · {{ stateLabels[item.validity] }} · 覆盖 {{ item.completeness.covered_requirement_count }} / {{ item.completeness.scoring_requirement_count }} · 待确认分节 {{ item.completeness.unconfirmed_sections }} / 条目 {{ item.completeness.unconfirmed_items }}</p><p v-if="item.prior_rubric_id">前一版 {{ item.prior_rubric_id }}</p></div><el-button v-if="historyMeta?.next_cursor" @click="history(historyMeta.next_cursor)">更多评分规则版本</el-button></el-card>
</template>
<style scoped>.el-form { max-width:740px; }.rubric-version { padding:12px 0; border-bottom:1px solid var(--border); overflow-wrap:anywhere; }</style>
