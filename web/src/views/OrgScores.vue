<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import AssessmentBudget from "../components/AssessmentBudget.vue";
import JobPanel from "../components/JobPanel.vue";
import { assessmentError, enc, queryPath, stateLabels, suggestedCap, usePreviewInvalidation } from "../assessments.js";
import { preparationBlocker, preparationStates, requirementRefreshErrors } from "../requirement-review.js";
import { formatTime, orgRequest } from "../org.js";

const route = useRoute();
const taskId = computed(() => String(route.params.taskId));
const extractionId = computed(() => String(route.query.job ?? ""));
const base = computed(() => `/tasks/${enc(taskId.value)}/scores`);
const task = ref(null), inputs = ref(null), explicitDraft = ref(null), selectedRubric = ref(null);
const draftId = ref(""), rubricId = ref(""), assessmentDate = ref(localDate()), reasoning = ref(""), cap = ref("");
const preview = ref(null), consent = ref(false), retry = ref(false), busy = ref(false), loading = ref(false), error = ref("");
const submitting = ref(false), sourceNeedsRefresh = ref(false);
const activeJob = ref(""), cached = ref(false), queueRequest = ref(null), queuedKey = ref("");
const rubricLoading = ref(false), unassessableDetail = ref(null), detailError = ref(""), detailLoading = ref(false), detailPage = ref(0);
const rubricPage = collection(), scorePage = collection(), jobPage = collection();
let sequence = 0, rubricSerial = 0, actionSerial = 0, submitSerial = 0, detailSerial = 0, previewKey = "";
const modeLabels = { model_assessable: "可由模型评估", ambiguous: "表述存在歧义", price_comparison: "价格比较", external_comparison: "外部比较", manual_only: "需人工评估", unsupported_formula: "不支持的公式" };
const jobLabels = { queued: "排队中", running: "处理中", succeeded: "已完成", failed: "处理失败", cancelled: "已取消" };
const totalLabels = { estimated: "总分已预估", range_only: "仅有可能范围", unavailable: "总分暂不可用" };
function localDate() { const date = new Date(); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`; }
function collection() { return reactive({ rows: [], meta: null, error: "", loading: false, cursors: [null], position: 0, request: 0 }); }
const scope = () => JSON.stringify([taskId.value, extractionId.value]);
const selectionKey = () => JSON.stringify([scope(), draftId.value, rubricId.value, assessmentDate.value, reasoning.value, cap.value]);
const current = (generation, key) => generation === sequence && key === scope();
const draftChoices = computed(() => [...new Map([inputs.value?.current_draft, inputs.value?.latest_draft, explicitDraft.value].filter(Boolean).map(item => [item.draft_id, item])).values()]);
const selectedDraft = computed(() => draftChoices.value.find(item => item.draft_id === draftId.value));
const rubricChoices = computed(() => [...new Map([...rubricPage.rows, selectedRubric.value].filter(Boolean).map(item => [item.id, item])).values()]);
const canRun = computed(() => inputs.value?.actions?.some(item => item.action === "score_run" && item.allowed));
const sourceBlocker = computed(() => sourceNeedsRefresh.value ? "要求确认已变化，请先重新核对要求，再刷新评分规则来源" : selectedRubric.value ? preparationBlocker(selectedRubric.value.requirement_review) : "");
const prerequisite = computed(() => {
  if (!draftId.value) return "尚未生成初稿，请先完成响应审阅并组表";
  if (selectedDraft.value?.validity !== "current") return "所选初稿已过期，请重新组表后评分";
  if (!rubricId.value) return "请明确选择本次评分规则";
  if (!selectedRubric.value) return rubricLoading.value ? "正在核对所选评分规则" : "所选评分规则尚未读取，请重新核对";
  if (selectedRubric.value.extraction_job_id !== extractionId.value || selectedRubric.value.document_id !== inputs.value?.document_id) return "所选评分规则与当前提取或招标文件不一致，请重新选择";
  if (sourceBlocker.value) return sourceBlocker.value;
  if (selectedRubric.value.validity !== "current" || selectedRubric.value.state === "superseded") return "所选评分规则已过期或被替代，请选择当前版本";
  if (selectedRubric.value.state !== "confirmed" || !selectedRubric.value.completeness.complete) return "请先完成所选评分规则的审核清单并确认整套规则";
  return "";
});
const blocker = computed(() => preview.value?.budget_preflight?.admission_blocker ?? preview.value?.admission_blocker);
const noEligibleItems = computed(() => preview.value?.cost_basis_reason === "no_assessable_items" && preview.value?.budget_preflight?.planned_calls === 0);
const ready = computed(() => !busy.value && canRun.value && !prerequisite.value && preview.value && previewKey === selectionKey() && !blocker.value && consent.value && Number(cap.value) > 0);
const unassessableIds = computed(() => preview.value?.preflight_unassessable_item_ids ?? []);
const visibleIds = computed(() => unassessableIds.value.slice(detailPage.value * 50, (detailPage.value + 1) * 50));

function invalidate() { actionSerial++; detailSerial++; preview.value = null; previewKey = ""; consent.value = false; queueRequest.value = null; queuedKey.value = ""; unassessableDetail.value = null; detailError.value = ""; detailLoading.value = false; detailPage.value = 0; busy.value = submitting.value; }
watch([draftId, rubricId, assessmentDate, reasoning, cap], invalidate, { flush: "sync" });
usePreviewInvalidation(invalidate, taskId);
async function loadCollection(page, path, values, validate, cursor = null, position = 0) {
  const generation = sequence, context = scope(), request = ++page.request;
  const active = () => current(generation, context) && request === page.request;
  page.loading = true; page.error = "";
  try {
    const result = await orgRequest("GET", queryPath(path, { ...values, limit: 50, cursor }));
    if (!active()) return;
    if (result.data.task_id !== taskId.value || !result.items.every(validate)) throw { code: "invalid_response" };
    page.rows = result.items; page.meta = result.data; page.position = position;
    if (!position) page.cursors = [null]; else page.cursors[position] = cursor;
    if (page === jobPage && !activeJob.value) activeJob.value = result.items.find(item => ["queued", "running"].includes(item.status))?.id ?? "";
  } catch (exc) { if (active() && exc.name !== "AbortError") page.error = assessmentError(exc); }
  finally { if (active()) page.loading = false; }
}
const rubricHistory = (cursor = null, position = 0) => loadCollection(rubricPage, `/tasks/${enc(taskId.value)}/score-rubrics`, { view: "console", extraction_job_id: extractionId.value }, item => item.task_id === taskId.value && item.extraction_job_id === extractionId.value, cursor, position);
const scoreHistory = (cursor = null, position = 0) => loadCollection(scorePage, base.value, { view: "console", extraction_job_id: extractionId.value }, item => item.report?.task_id === taskId.value && item.report.extraction_job_id === extractionId.value, cursor, position);
const discover = (cursor = null, position = 0) => loadCollection(jobPage, `/tasks/${enc(taskId.value)}/jobs`, { kind: "score", extraction_job_id: extractionId.value }, item => item.kind === "score" && item.task_id === taskId.value && item.extraction_job_id === extractionId.value, cursor, position);
function pageForward(page, loader) { if (page.meta?.next_cursor) return loader(page.meta.next_cursor, page.position + 1); }
function pageBack(page, loader) { if (page.position) return loader(page.cursors[page.position - 1], page.position - 1); }
async function readRubric() {
  const generation = sequence, context = scope(), id = rubricId.value, request = ++rubricSerial;
  const active = () => current(generation, context) && id === rubricId.value && request === rubricSerial;
  selectedRubric.value = null; invalidate();
  if (!id) return;
  rubricLoading.value = true; error.value = "";
  try {
    const result = await orgRequest("GET", queryPath(`/tasks/${enc(taskId.value)}/score-rubrics/${enc(id)}`, { view: "console", part: "summary" }));
    if (!active()) return;
    if (result.data.task_id !== taskId.value || result.data.id !== id) throw { status: 404 };
    selectedRubric.value = result.data; sourceNeedsRefresh.value = false;
  } catch (exc) { if (active() && exc.name !== "AbortError") error.value = assessmentError(exc); }
  finally { if (active()) rubricLoading.value = false; }
}
async function load() {
  clear(); const generation = sequence, context = scope(); loading.value = true;
  if (!extractionId.value) { error.value = "请先在任务中明确选择成功的提取结果"; loading.value = false; return; }
  try {
    const [taskResult, inputResult] = await Promise.all([orgRequest("GET", `/tasks/${enc(taskId.value)}`), orgRequest("GET", queryPath(`/tasks/${enc(taskId.value)}/assessment-inputs`, { job: extractionId.value }))]);
    if (!current(generation, context)) return;
    if (taskResult.data.id !== taskId.value || inputResult.data.task_id !== taskId.value || inputResult.data.extraction_job_id !== extractionId.value) throw { status: 404 };
    task.value = taskResult.data; inputs.value = inputResult.data;
    const requestedDraft = String(route.query.draft ?? "");
    if (requestedDraft && !draftChoices.value.some(item => item.draft_id === requestedDraft)) {
      const saved = (await orgRequest("GET", `/drafts/${enc(requestedDraft)}`)).data;
      if (!current(generation, context)) return;
      if (saved.id !== requestedDraft || saved.task_id !== taskId.value || saved.extraction_job_id !== extractionId.value) throw { status: 404 };
      explicitDraft.value = { draft_id: saved.id, extraction_job_id: saved.extraction_job_id, validity: saved.validity, completion: saved.completion };
    }
    draftId.value = requestedDraft || inputs.value.current_draft?.draft_id || "";
    rubricId.value = String(route.query.rubric ?? "");
    await Promise.all([rubricHistory(), scoreHistory(), discover(), readRubric()]);
  } catch (exc) { if (current(generation, context) && exc.name !== "AbortError") error.value = assessmentError(exc); }
  finally { if (current(generation, context)) loading.value = false; }
}
function requestBody(dry) { return { draft_id: draftId.value, rubric_id: rubricId.value, assessment_date: assessmentDate.value, dry_run: dry, ...(reasoning.value ? { reasoning: reasoning.value } : {}), ...(cap.value ? { max_charge: cap.value } : {}), ...(!dry ? { expected_input_hash: preview.value.input.input_hash, retry: retry.value } : {}) }; }
async function preflight() {
  if (busy.value || !canRun.value || prerequisite.value) return;
  invalidate(); const generation = sequence, context = scope(), key = selectionKey(), request = ++actionSerial;
  const active = () => current(generation, context) && key === selectionKey() && request === actionSerial;
  busy.value = true; error.value = "";
  try {
    const result = await orgRequest("POST", `${base.value}/preview`, requestBody(true));
    if (!active()) return;
    const data = result.data, input = data.input, budget = data.budget_preflight;
    if (input?.task_id !== taskId.value || input.org_id !== inputs.value.org_id || input.extraction_job_id !== extractionId.value || input.document_id !== inputs.value.document_id || input.draft_id !== draftId.value || input.assessment_date !== assessmentDate.value || data.rubric_id !== rubricId.value || data.rubric_version !== selectedRubric.value.version || data.rubric_input_hash !== selectedRubric.value.input_hash || !/^[a-f0-9]{64}$/.test(input.input_hash) || budget?.task_id !== taskId.value || budget.input_hash !== input.input_hash) throw { code: "score_input_changed" };
    const suggested = suggestedCap(data.estimated_charge);
    if (!cap.value && suggested) { cap.value = suggested; await preflight(); return; }
    preview.value = data; previewKey = key;
  } catch (exc) { if (active() && exc.name !== "AbortError") { error.value = assessmentError(exc); if (requirementRefreshErrors.includes(exc.code)) { sourceNeedsRefresh.value = true; invalidate(); } } }
  finally { if (active()) busy.value = false; }
}
async function submit(recover = false) {
  if (busy.value || !canRun.value || prerequisite.value || (recover ? !queueRequest.value || queuedKey.value !== selectionKey() : !ready.value)) return;
  const generation = sequence, context = scope(), key = selectionKey(), request = ++submitSerial;
  const active = () => current(generation, context) && key === selectionKey() && request === submitSerial;
  const body = recover ? { ...queueRequest.value } : requestBody(false);
  submitting.value = true; busy.value = true; error.value = "";
  try {
    const result = await orgRequest("POST", base.value, body);
    if (!active()) return;
    if (typeof result.data.job_id !== "string" || typeof result.data.cached !== "boolean") throw { code: "invalid_response" };
    activeJob.value = result.data.job_id; cached.value = result.data.cached; invalidate(); await discover();
  } catch (exc) {
    if (!active() || exc.name === "AbortError") return;
    error.value = assessmentError(exc); consent.value = false;
    if (requirementRefreshErrors.includes(exc.code)) { sourceNeedsRefresh.value = true; invalidate(); }
    if (exc.code === "queue_unavailable" && exc.payload?.data.job_id) { activeJob.value = exc.payload.data.job_id; queueRequest.value = { ...body }; queuedKey.value = key; }
    else if (exc.code?.includes("input_changed") || exc.code?.includes("stale") || ["score_rubric_unconfirmed", "rubric_superseded"].includes(exc.code)) invalidate();
  } finally { if (active()) { submitting.value = false; busy.value = false; } }
}
async function showUnassessable(id) {
  const generation = sequence, context = scope(), key = selectionKey(), request = ++detailSerial, rubric = rubricId.value;
  const active = () => current(generation, context) && key === selectionKey() && request === detailSerial;
  unassessableDetail.value = null; detailError.value = ""; detailLoading.value = true;
  try {
    const result = await orgRequest("GET", queryPath(`/tasks/${enc(taskId.value)}/score-rubrics/${enc(rubric)}`, { view: "console", part: "items", entry_id: id, limit: 1 }));
    if (!active()) return;
    if (result.data.parent_id !== rubric || result.data.task_id !== taskId.value || result.data.parent_revision !== selectedRubric.value.revision || result.items.length !== 1 || result.items[0].id !== id) throw { code: "assessment_view_changed" };
    unassessableDetail.value = result.items[0];
  } catch (exc) { if (active() && exc.name !== "AbortError") detailError.value = assessmentError(exc); }
  finally { if (active()) detailLoading.value = false; }
}
function finished() { return Promise.all([scoreHistory(), discover()]); }
function clear() {
  sequence++; rubricSerial++; submitSerial++; submitting.value = false; invalidate(); task.value = null; inputs.value = null; explicitDraft.value = null; selectedRubric.value = null; sourceNeedsRefresh.value = false; draftId.value = ""; rubricId.value = ""; reasoning.value = ""; cap.value = ""; retry.value = false; cached.value = false; activeJob.value = ""; error.value = ""; loading.value = false; rubricLoading.value = false;
  for (const page of [rubricPage, scorePage, jobPage]) { page.request++; page.rows = []; page.meta = null; page.error = ""; page.loading = false; page.cursors = [null]; page.position = 0; }
}
watch(() => [taskId.value, extractionId.value, route.query.draft, route.query.rubric], load, { immediate: true });
window.addEventListener("bid:org-reset", clear);
onBeforeUnmount(() => { clear(); window.removeEventListener("bid:org-reset", clear); });
</script>

<template>
  <nav class="breadcrumb" aria-label="位置"><RouterLink to="/org/tasks">招标任务</RouterLink><span>/</span><RouterLink :to="`/org/tasks/${taskId}`">{{ task?.name ?? '任务' }}</RouterLink><span>/</span><span>评分预估</span></nav>
  <header class="page-header"><div><h2>评分预估</h2><p>仅评估已保存初稿 · 提取 {{ extractionId || '尚未选择' }}。评分结果仅供参考。</p></div><div class="actions"><el-button :disabled="busy" @click="load">重新读取评分条件</el-button><RouterLink :to="`/org/tasks/${taskId}/score-rubrics?job=${extractionId}`">审核评分规则</RouterLink></div></header>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" />
  <el-skeleton v-if="loading" :rows="5" animated />
  <template v-if="inputs">
    <el-alert v-if="sourceBlocker" :title="sourceBlocker" type="warning" :closable="false" show-icon><RouterLink :to="`/org/tasks/${taskId}/requirements?job=${selectedRubric?.extraction_job_id ?? extractionId}`">核对评分来源要求</RouterLink><el-button :disabled="busy || rubricLoading" @click="readRubric">重新核对评分规则来源</el-button></el-alert>
    <el-alert v-if="prerequisite && !sourceBlocker" :title="prerequisite" type="warning" :closable="false" show-icon><RouterLink v-if="!draftId || selectedDraft?.validity !== 'current'" :to="`/org/tasks/${taskId}/drafts?job=${extractionId}`">前往组表</RouterLink><RouterLink v-else-if="rubricId" :to="`/org/tasks/${taskId}/score-rubrics/${rubricId}`">核对评分规则</RouterLink></el-alert>
    <el-card class="section" shadow="never"><h3>预览评分</h3>
      <el-form label-position="top" @submit.prevent="preflight">
        <el-form-item label="本次初稿"><el-select v-model="draftId" aria-label="本次初稿" :disabled="busy"><el-option v-for="draft in draftChoices" :key="draft.draft_id" :value="draft.draft_id" :label="`${draft.draft_id} · ${stateLabels[draft.validity]}`" /></el-select></el-form-item>
        <el-form-item label="本次评分规则"><el-select v-model="rubricId" aria-label="本次评分规则" :disabled="busy" :loading="rubricLoading" @change="readRubric"><el-option v-for="rubric in rubricChoices" :key="rubric.id" :value="rubric.id" :label="`第 ${rubric.version} 版 · ${stateLabels[rubric.state]} · ${rubric.id}`" /></el-select></el-form-item>
        <p v-if="selectedRubric" data-testid="score-source-state"><el-tag :type="!sourceNeedsRefresh && selectedRubric.requirement_review?.state==='ready'?'success':'warning'">{{sourceNeedsRefresh ? '评分来源待刷新' : preparationStates[selectedRubric.requirement_review?.state] ?? '评分来源待核对'}}</el-tag><span v-if="selectedRubric.requirement_review"> · 固定来源 {{selectedRubric.requirement_review.fixed_count}} 项 / 已确认 {{selectedRubric.requirement_review.confirmed_count}} 项</span></p>
        <p v-if="selectedRubric">所选规则：第 {{ selectedRubric.version }} 版 · 修订 {{ selectedRubric.revision }} · {{ stateLabels[selectedRubric.state] }} · {{ stateLabels[selectedRubric.validity] }}</p>
        <el-form-item label="评估日期"><el-input v-model="assessmentDate" type="date" aria-label="评估日期" :disabled="busy" /></el-form-item>
        <el-form-item label="推理档位（留空使用默认）"><el-input v-model="reasoning" aria-label="推理档位" :disabled="busy" /></el-form-item>
        <el-form-item label="本次作业平台扣费上限"><el-input v-model="cap" inputmode="decimal" aria-label="本次作业平台扣费上限" :disabled="busy" /></el-form-item>
        <el-button native-type="submit" type="primary" plain :loading="busy" :disabled="!canRun || !!prerequisite || rubricLoading">预览评分</el-button><span v-if="!canRun">当前角色只读或尚未满足运行条件</span>
      </el-form>
      <AssessmentBudget :preview="preview" :budget="inputs.task_budget" />
      <section v-if="preview" aria-label="本次评分范围"><h3>本次评分范围</h3><p>初稿 {{ preview.input.draft_id }} · 第 {{ preview.rubric_version }} 版评分规则 · 评估日期 {{ preview.input.assessment_date }}</p><p>选中评分项 {{ preview.selected_item_ids.length }} 条 · 预检无法评估 {{ unassessableIds.length }} 条</p><p v-for="limitation in preview.limitations" :key="limitation" class="hint">{{ limitation }}</p>
        <el-alert v-if="noEligibleItems" title="没有可由模型评估的评分项" type="warning" :closable="false" show-icon><p>本次预检计划调用为 0。可以明确选择生成无法评估报告，以保存各项无法评估的原因；未知分数不会计为零分。</p></el-alert>
        <details v-if="unassessableIds.length"><summary>查看预检无法评估的评分项</summary><p>具体原因需读取保存的评分项；预检的 ID 本身不说明原因。</p><el-button v-for="id in visibleIds" :key="id" link @click="showUnassessable(id)">核对评分项 {{ id }}</el-button><p role="status">第 {{ detailPage + 1 }} 页，本页 {{ visibleIds.length }} / 全部 {{ unassessableIds.length }} 条</p><div class="actions"><el-button :disabled="detailPage === 0" @click="detailPage--">上一页评分项</el-button><el-button :disabled="(detailPage + 1) * 50 >= unassessableIds.length" @click="detailPage++">下一页评分项</el-button></div><el-skeleton v-if="detailLoading" :rows="2" animated /><el-alert v-if="detailError" :title="detailError" type="error" :closable="false" role="alert" /><article v-if="unassessableDetail"><h4>{{ unassessableDetail.title }}</h4><p>{{ modeLabels[unassessableDetail.assessment_mode] }}</p><p>{{ unassessableDetail.ambiguity_reason ?? '该条目未保存歧义说明；具体评分原因请查看作业完成后的评分报告' }}</p><blockquote class="quote">{{ unassessableDetail.rule_text }}</blockquote></article></details>
        <el-checkbox v-model="consent">我已核对外发范围与费用上限</el-checkbox><p><el-checkbox v-model="retry">显式重试已失败或取消的作业</el-checkbox></p><el-button type="primary" :loading="busy" :disabled="!ready" @click="submit()">{{ noEligibleItems ? '生成无法评估报告' : '提交评分' }}</el-button>
      </section>
    </el-card>
    <el-card class="section" shadow="never"><h3>可选评分规则</h3><el-alert v-if="rubricPage.error" :title="rubricPage.error" type="error" :closable="false" role="alert" /><el-skeleton v-if="rubricPage.loading" :rows="2" animated /><p v-else-if="!rubricPage.rows.length && !rubricPage.error">尚未生成评分规则</p><p v-if="rubricPage.meta" role="status">全部 {{ rubricPage.meta.total }} 个版本 · 第 {{ rubricPage.position + 1 }} 页</p><div v-for="rubric in rubricPage.rows" :key="rubric.id" class="history-row"><RouterLink :to="`/org/tasks/${taskId}/score-rubrics/${rubric.id}`">审核第 {{ rubric.version }} 版评分规则</RouterLink><span>{{ stateLabels[rubric.state] }} · {{ stateLabels[rubric.validity] }}</span></div><div class="actions"><el-button :disabled="rubricPage.position === 0 || rubricPage.loading" @click="pageBack(rubricPage, rubricHistory)">上一页评分规则</el-button><el-button :disabled="!rubricPage.meta?.next_cursor || rubricPage.loading" @click="pageForward(rubricPage, rubricHistory)">下一页评分规则</el-button></div></el-card>
  </template>
  <p v-if="cached" role="status">已复用相同输入的结果</p><el-button v-if="queueRequest" :disabled="busy || queuedKey !== selectionKey()" @click="submit(true)">重新调度已保存作业</el-button>
  <JobPanel v-if="activeJob" assessment-mode :job-id="activeJob" :writable="!!jobPage.rows.find(job => job.id === activeJob)?.cancel.allowed" @finished="finished" />
  <el-card v-if="inputs" class="section" shadow="never"><h3>评分作业</h3><el-alert v-if="jobPage.error" :title="jobPage.error" type="error" :closable="false" role="alert" /><el-skeleton v-if="jobPage.loading" :rows="2" animated /><p v-else-if="!jobPage.rows.length && !jobPage.error">尚无评分作业</p><div v-for="job in jobPage.rows" :key="job.id" class="history-row"><el-button link @click="activeJob = job.id">查看作业 {{ job.id }}</el-button><span>{{ jobLabels[job.status] }} · 尝试 {{ job.attempts }} 次</span><RouterLink v-if="job.result_id" :to="`/org/tasks/${taskId}/scores/${job.result_id}`">查看评分报告</RouterLink></div><div class="actions"><el-button :disabled="!jobPage.position || jobPage.loading" @click="pageBack(jobPage, discover)">上一页作业</el-button><el-button :disabled="!jobPage.meta?.next_cursor || jobPage.loading" @click="pageForward(jobPage, discover)">下一页作业</el-button></div></el-card>
  <el-card v-if="inputs" class="section" shadow="never"><h3>评分历史</h3><el-alert v-if="scorePage.error" :title="scorePage.error" type="error" :closable="false" role="alert" /><el-skeleton v-if="scorePage.loading" :rows="2" animated /><el-empty v-else-if="!scorePage.rows.length && !scorePage.error" description="尚未运行评分" /><p v-if="scorePage.meta" role="status">全部 {{ scorePage.meta.total }} 份报告 · 第 {{ scorePage.position + 1 }} 页</p><div v-for="item in scorePage.rows" :key="item.report.id" class="history-row"><RouterLink :to="`/org/tasks/${taskId}/scores/${item.report.id}`">查看评分报告</RouterLink><span>{{ item.report.assessment_date }} · {{ stateLabels[item.report.completion] }} · {{ stateLabels[item.report.validity] }} · {{ totalLabels[item.total_status] }}</span><small>初稿 {{ item.report.draft_id }} · 第 {{ item.rubric_version }} 版规则 · {{ formatTime(item.report.created_at) }}</small></div><div class="actions"><el-button :disabled="!scorePage.position || scorePage.loading" @click="pageBack(scorePage, scoreHistory)">上一页评分历史</el-button><el-button :disabled="!scorePage.meta?.next_cursor || scorePage.loading" @click="pageForward(scorePage, scoreHistory)">下一页评分历史</el-button></div></el-card>
</template>
<style scoped>.el-form { max-width:740px; } .history-row { display:grid; gap:6px; padding:12px 0; border-bottom:1px solid var(--border); overflow-wrap:anywhere; } .quote { white-space:pre-wrap; overflow-wrap:anywhere; } details { margin:12px 0; } details .el-button { display:block; }</style>
