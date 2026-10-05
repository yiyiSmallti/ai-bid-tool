<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute } from "vue-router";
import AssessmentBudget from "../components/AssessmentBudget.vue";
import JobPanel from "../components/JobPanel.vue";
import { assessmentError, enc, queryPath, stateLabels, suggestedCap, usePreviewInvalidation } from "../assessments.js";
import { formatTime, jobStatuses, orgRequest } from "../org.js";
const route = useRoute();
const taskId = computed(() => String(route.params.taskId)), extractionId = computed(() => String(route.query.job ?? ""));
const task = ref(null), inputs = ref(null), histories = ref([]), historyMeta = ref(null), jobs = ref([]), jobsMeta = ref(null), activeJob = ref("");
const loading = ref(false), busy = ref(false), error = ref(""), preview = ref(null), cached = ref(false), queueRequest = ref(null);
const draftId = ref(""), mode = ref("rules"), assessmentDate = ref(new Date().toLocaleDateString("en-CA")), reasoning = ref(""), cap = ref(""), consent = ref(false), retry = ref(false);
let sequence = 0, previewSerial = 0, previewKey = "";
const canRun = computed(() => inputs.value?.actions?.some(a => a.action === "check_run" && a.allowed));
const blocker = computed(() => preview.value?.budget_preflight?.admission_blocker ?? preview.value?.admission_blocker);
const modelRun = computed(() => mode.value === "combined");
const draftChoices = computed(() => [...new Map([inputs.value?.current_draft, inputs.value?.latest_draft].filter(Boolean).map(d => [d.draft_id, d])).values()]);
const body = (dry = true) => ({ draft_id: draftId.value, assessment_date: assessmentDate.value, mode: mode.value, dry_run: dry, ...(modelRun.value && reasoning.value ? { reasoning: reasoning.value } : {}), ...(modelRun.value && cap.value ? { max_charge: cap.value } : {}), ...(!dry ? { expected_input_hash: preview.value?.input.input_hash, retry: retry.value } : {}) });
const selectionKey = () => JSON.stringify([taskId.value, extractionId.value, draftId.value, mode.value, assessmentDate.value, reasoning.value, cap.value]);
function invalidate() { previewSerial++; preview.value = null; consent.value = false; previewKey = ""; queueRequest.value = null; }
usePreviewInvalidation(invalidate, taskId);
watch([draftId, mode, assessmentDate, reasoning, cap], invalidate, { flush: "sync" });
async function history(cursor = null) {
  const current = sequence;
  const result = await orgRequest("GET", queryPath(`/tasks/${enc(taskId.value)}/checks`, { view: "console", extraction_job_id: extractionId.value, limit: 50, cursor }));
  if (current !== sequence) return;
  histories.value = result.items; historyMeta.value = result.data;
}
async function discover(cursor = null) {
  const current = sequence;
  const result = await orgRequest("GET", queryPath(`/tasks/${enc(taskId.value)}/jobs`, { kind: "check", extraction_job_id: extractionId.value, limit: 50, cursor }));
  if (current !== sequence) return;
  jobs.value = result.items; jobsMeta.value = result.data;
  if (!activeJob.value) activeJob.value = jobs.value.find(j => ["queued", "running"].includes(j.status))?.id ?? "";
}
async function load() {
  const current = ++sequence; loading.value = true; error.value = ""; inputs.value = null; histories.value = []; jobs.value = []; activeJob.value = ""; invalidate();
  if (!extractionId.value) { error.value = "请先在任务中明确选择成功的提取结果"; loading.value = false; return; }
  try {
    const result = await Promise.all([orgRequest("GET", `/tasks/${enc(taskId.value)}`), orgRequest("GET", queryPath(`/tasks/${enc(taskId.value)}/assessment-inputs`, { job: extractionId.value }))]);
    if (current !== sequence) return;
    task.value = result[0].data; inputs.value = result[1].data;
    if (inputs.value.task_id !== taskId.value || inputs.value.extraction_job_id !== extractionId.value) throw new Error("invalid input identity");
    const explicit = String(route.query.draft ?? "");
    if (explicit && !draftChoices.value.some(d => d.draft_id === explicit)) {
      // Explicit historical selections are validated by the existing draft read; never silently substitute.
      const saved = (await orgRequest("GET", `/drafts/${enc(explicit)}`)).data;
      if (saved.task_id !== taskId.value || saved.extraction_job_id !== extractionId.value) throw new Error("invalid draft identity");
      inputs.value = { ...inputs.value, selected_draft: { draft_id: explicit, validity: saved.validity } };
    }
    draftId.value = explicit || inputs.value.current_draft?.draft_id || "";
    await Promise.all([history(), discover()]);
  } catch (exc) { if (current === sequence && exc.name !== "AbortError") error.value = assessmentError(exc); }
  finally { if (current === sequence) loading.value = false; }
}
const selected = computed(() => draftChoices.value.find(d => d.draft_id === draftId.value) ?? inputs.value?.selected_draft);
async function preflight() {
  invalidate(); busy.value = true; error.value = "";
  let key = selectionKey(), attempt = previewSerial;
  try {
    let result = await orgRequest("POST", `/tasks/${enc(taskId.value)}/checks`, body());
    if (key !== selectionKey() || attempt !== previewSerial) return;
    if (modelRun.value && !cap.value && result.data.estimated_charge != null) {
      cap.value = suggestedCap(result.data.estimated_charge); key = selectionKey(); attempt = previewSerial;
      result = await orgRequest("POST", `/tasks/${enc(taskId.value)}/checks`, body());
      if (key !== selectionKey() || attempt !== previewSerial) return;
    }
    const data = result.data, input = data.input;
    if (input?.task_id !== taskId.value || input.org_id !== inputs.value.org_id || input.extraction_job_id !== extractionId.value || input.document_id !== inputs.value.document_id || input.draft_id !== draftId.value || input.assessment_date !== assessmentDate.value || data.mode !== mode.value || !/^[a-f0-9]{64}$/.test(input.input_hash) || data.budget_preflight?.input_hash !== input.input_hash) throw { code: 'check_input_changed' };
    preview.value = data; previewKey = key;
  } catch (exc) { if (exc.name !== "AbortError") error.value = assessmentError(exc); }
  finally { busy.value = false; }
}
async function submit(recover = false) {
  if (busy.value || !canRun.value || (!recover && (!preview.value || previewKey !== selectionKey() || blocker.value || (modelRun.value && (!consent.value || !(Number(cap.value) > 0)))))) return;
  const request = recover ? queueRequest.value : body(false); if (!request) return;
  const generation = sequence;
  busy.value = true; error.value = "";
  try {
    const result = await orgRequest("POST", `/tasks/${enc(taskId.value)}/checks`, request);
    if (generation !== sequence) return;
    activeJob.value = result.data.job_id; cached.value = result.data.cached; invalidate(); await discover();
  } catch (exc) {
    if (exc.name === "AbortError" || generation !== sequence) return;
    error.value = assessmentError(exc); consent.value = false;
    if (exc.code === "queue_unavailable" && exc.payload?.data.job_id) { activeJob.value = exc.payload.data.job_id; queueRequest.value = request; }
    else if (exc.code?.includes("input_changed") || exc.code?.includes("stale")) invalidate();
  } finally { busy.value = false; }
}
async function finished() { try { await Promise.all([history(), discover()]); } catch (exc) { error.value = assessmentError(exc); } }
function clear() { sequence++; inputs.value = null; task.value = null; histories.value = []; jobs.value = []; activeJob.value = ""; invalidate(); }
watch(() => [taskId.value, extractionId.value, route.query.draft], load, { immediate: true });
window.addEventListener("bid:org-reset", clear);
onBeforeUnmount(() => { clear(); window.removeEventListener("bid:org-reset", clear); });
</script>
<template>
  <nav class="breadcrumb" aria-label="位置"><RouterLink to="/org/tasks">招标任务</RouterLink><span>/</span><RouterLink :to="`/org/tasks/${taskId}`">{{ task?.name ?? '任务' }}</RouterLink><span>/</span><span>检查风险</span></nav>
  <header class="page-header"><div><h2>检查风险</h2><p>仅评估已保存初稿 · 提取 {{ extractionId || '尚未选择' }}。检查结果仅供参考。</p></div><RouterLink :to="`/org/tasks/${taskId}/drafts?job=${extractionId}`">返回组表</RouterLink></header>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" />
  <el-skeleton v-if="loading" :rows="5" animated />
  <template v-if="inputs">
    <el-alert v-if="!draftId" title="尚未生成初稿" type="warning" :closable="false" show-icon><RouterLink :to="`/org/tasks/${taskId}/drafts?job=${extractionId}`">前往组表</RouterLink></el-alert>
    <el-alert v-else-if="selected?.validity === 'stale'" title="初稿已过期，请重新组表" type="warning" :closable="false" show-icon />
    <p v-if="inputs.latest_draft">最新初稿：{{ inputs.latest_draft.draft_id }} · {{ stateLabels[inputs.latest_draft.validity] }}</p>
    <el-card class="section" shadow="never">
      <h3>预览检查</h3>
      <el-form label-position="top" :disabled="busy" @submit.prevent="preflight">
        <el-form-item label="本次初稿"><el-select v-model="draftId" aria-label="本次初稿"><el-option v-for="draft in draftChoices" :key="draft.draft_id" :value="draft.draft_id" :label="`${draft.draft_id} · ${stateLabels[draft.validity]}`" /><el-option v-if="inputs.selected_draft" :value="inputs.selected_draft.draft_id" :label="`${inputs.selected_draft.draft_id} · ${stateLabels[inputs.selected_draft.validity]}`" /></el-select></el-form-item>
        <el-form-item label="评估日期"><el-input v-model="assessmentDate" type="date" aria-label="评估日期" /></el-form-item>
        <el-form-item label="检查方式"><el-radio-group v-model="mode" aria-label="检查方式"><el-radio value="rules">规则检查</el-radio><el-radio value="combined">规则与语义检查</el-radio></el-radio-group></el-form-item>
        <p class="hint">规则检查核对覆盖、证照日期与偏离；规则与语义检查还会评估已确认响应的语义，不代表完整法律或投标审核。</p>
        <template v-if="modelRun"><el-form-item label="推理档位（留空使用默认）"><el-input v-model="reasoning" aria-label="推理档位" /></el-form-item><el-form-item label="本次作业平台扣费上限"><el-input v-model="cap" inputmode="decimal" aria-label="本次作业平台扣费上限" /></el-form-item></template>
        <el-button native-type="submit" :loading="busy" :disabled="!canRun || !draftId || selected?.validity !== 'current'" type="primary" plain>预览检查</el-button>
        <span v-if="!canRun">当前角色只读或尚未满足运行条件</span>
      </el-form>
      <AssessmentBudget :preview="preview" :budget="inputs.task_budget" :rules="!modelRun" />
      <section v-if="preview" aria-label="本次检查范围">
        <h3>本次检查范围</h3><p>初稿 {{ preview.input.draft_id }} · 评估日期 {{ preview.input.assessment_date }} · 选中 {{ preview.selected_item_ids.length }} 条 · 规则适用 {{ preview.rules_applicable }} 条 · 语义检查 {{ preview.semantic_items }} 条 · 缺口 {{ preview.gap_requirements }} 条</p>
        <p v-for="item in preview.limitations" :key="item" class="hint">{{ item }}</p>
        <el-checkbox v-if="modelRun" v-model="consent">我已核对外发范围与费用上限</el-checkbox>
        <p><el-checkbox v-model="retry">显式重试已失败或取消的作业</el-checkbox></p>
        <el-button type="primary" :loading="busy" :disabled="!!blocker || (modelRun && (!consent || !(Number(cap) > 0)))" @click="submit()">提交检查</el-button>
      </section>
    </el-card>
  </template>
  <p v-if="cached" role="status">已复用相同输入的结果</p>
  <el-button v-if="queueRequest" :disabled="busy" @click="submit(true)">重新调度已保存作业</el-button>
  <JobPanel assessment-mode v-if="activeJob" :job-id="activeJob" :writable="!!jobs.find(j => j.id === activeJob)?.cancel?.allowed" @finished="finished" />
  <el-card v-if="inputs" class="section" shadow="never"><h3>检查作业</h3><p v-if="!jobs.length">尚无检查作业</p><div v-for="job in jobs" :key="job.id" class="actions"><el-button link @click="activeJob = job.id">查看作业 {{ job.id }}</el-button><span>{{ jobStatuses[job.status] ?? job.status }} · 尝试 {{ job.attempts }} 次</span><RouterLink v-if="job.result_id" :to="`/org/tasks/${taskId}/checks/${job.result_id}`">查看报告</RouterLink></div><el-button v-if="jobsMeta?.next_cursor" @click="discover(jobsMeta.next_cursor)">更多作业</el-button></el-card>
  <el-card v-if="inputs" class="section" shadow="never"><h3>检查历史</h3><el-empty v-if="!histories.length" description="尚未运行检查" /><div v-for="item in histories" :key="item.report.id" class="assessment-history"><RouterLink :to="`/org/tasks/${taskId}/checks/${item.report.id}`">查看检查报告</RouterLink><span>{{ item.mode === 'rules' ? '规则检查' : '规则与语义检查' }} · {{ item.report.assessment_date }} · {{ stateLabels[item.report.completion] }} · {{ stateLabels[item.report.validity] }}</span><small>初稿 {{ item.report.draft_id }} · {{ formatTime(item.report.created_at) }}</small></div><el-button v-if="historyMeta?.next_cursor" @click="history(historyMeta.next_cursor)">更多检查历史</el-button></el-card>
</template>
<style scoped>.assessment-history { display:grid; gap:6px; padding:14px 0; border-bottom:1px solid var(--border); overflow-wrap:anywhere; } .el-form { max-width:740px; }</style>
