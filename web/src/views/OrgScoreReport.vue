<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute } from "vue-router";
import AssessmentCitation from "../components/AssessmentCitation.vue";
import JobPanel from "../components/JobPanel.vue";
import { assessmentError, enc, queryPath, stateLabels } from "../assessments.js";
import { formatTime, orgRequest } from "../org.js";

const route = useRoute();
const taskId = computed(() => String(route.params.taskId)), reportId = computed(() => String(route.params.reportId));
const base = computed(() => `/tasks/${enc(taskId.value)}/scores/${enc(reportId.value)}`);
const summary = ref(null), taskName = ref(""), error = ref(""), loading = ref(false), showJob = ref(false);
const part = ref("items"), sectionKey = ref(""), outcome = ref(""), requirementId = ref("");
const rows = ref([]), meta = ref(null), pageError = ref(""), pageLoading = ref(false), position = ref(0), cursors = ref([null]);
let sequence = 0, pageSerial = 0;
const aggregationLabels = { sum: "加总", weighted_sum: "加权合计", capped_sum: "封顶合计", formula: "公式（不能自动合计）", non_additive: "非加总规则（不能自动合计）" };
const totalLabels = { estimated: "已预估", range_only: "仅有可能范围", unavailable: "暂不可用" };
const filters = computed(() => ({ part: part.value, ...(["sections", "items"].includes(part.value) ? { section_key: sectionKey.value } : {}), ...(part.value === "items" ? { outcome: outcome.value, requirement_id: requirementId.value } : {}) }));
const scope = () => JSON.stringify([taskId.value, reportId.value]);
const current = (generation, context) => generation === sequence && context === scope();
const stale = computed(() => summary.value?.report.validity === "stale");
const contradiction = computed(() => summary.value && ((route.query.job && route.query.job !== summary.value.report.extraction_job_id) || (route.query.draft && route.query.draft !== summary.value.report.draft_id) || (route.query.rubric && route.query.rubric !== summary.value.rubric_id)));
const scoreText = value => value == null ? "未知" : String(value);
const rangeText = value => value ? `${value.minimum}–${value.maximum}` : "未知";
const reviewHref = requirement => queryPath(`/org/tasks/${enc(taskId.value)}/review`, { job: summary.value.report.extraction_job_id, requirement });
function rowMatches(row, selectedPart) { return selectedPart === "items" ? row.task_id === taskId.value && row.report_id === reportId.value : selectedPart === "sections" ? typeof row.section_key === "string" : typeof row.code === "string" && typeof row.message === "string"; }
async function readSummary() {
  const generation = sequence, context = scope(), id = reportId.value;
  const result = await orgRequest("GET", queryPath(base.value, { view: "console", part: "summary" }));
  if (!current(generation, context)) return false;
  if (result.data.report?.id !== id || result.data.report.task_id !== taskId.value) throw { status: 404 };
  summary.value = result.data;
  return true;
}
async function loadPage(cursor = null, target = 0) {
  if (!summary.value) return;
  const generation = sequence, context = scope(), query = { ...filters.value }, filterKey = JSON.stringify(query), request = ++pageSerial;
  const active = () => current(generation, context) && request === pageSerial && filterKey === JSON.stringify(filters.value);
  pageLoading.value = true; pageError.value = "";
  try {
    const result = await orgRequest("GET", queryPath(base.value, { view: "console", ...query, limit: 50, cursor }));
    if (!active()) return;
    if (result.data.task_id !== taskId.value || result.data.parent_id !== reportId.value || result.data.part !== query.part || result.data.returned !== result.items.length || !result.items.every(row => rowMatches(row, query.part))) throw { code: "invalid_response" };
    rows.value = result.items; meta.value = result.data; position.value = target;
    if (!target) cursors.value = [null]; else cursors.value[target] = cursor;
  } catch (exc) {
    if (!active() || exc.name === "AbortError") return;
    pageError.value = assessmentError(exc);
    if (exc.code === "assessment_view_changed") {
      rows.value = []; meta.value = null; cursors.value = [null]; position.value = 0;
      try { await readSummary(); } catch (reload) { if (active() && reload.name !== "AbortError") pageError.value += `；${assessmentError(reload)}`; }
    }
  } finally { if (active()) pageLoading.value = false; }
}
function forward() { if (meta.value?.next_cursor) return loadPage(meta.value.next_cursor, position.value + 1); }
function back() { if (position.value) return loadPage(cursors.value[position.value - 1], position.value - 1); }
async function load() {
  clear(); const generation = sequence, context = scope(); loading.value = true;
  try {
    const taskResult = await orgRequest("GET", `/tasks/${enc(taskId.value)}`);
    if (!current(generation, context)) return;
    if (taskResult.data.id !== taskId.value) throw { status: 404 };
    taskName.value = taskResult.data.name;
    if (await readSummary() && current(generation, context)) await loadPage();
  } catch (exc) { if (current(generation, context) && exc.name !== "AbortError") error.value = assessmentError(exc); }
  finally { if (current(generation, context)) loading.value = false; }
}
function clear() { sequence++; pageSerial++; summary.value = null; taskName.value = ""; rows.value = []; meta.value = null; error.value = ""; pageError.value = ""; loading.value = false; pageLoading.value = false; position.value = 0; cursors.value = [null]; showJob.value = false; part.value = "items"; sectionKey.value = ""; outcome.value = ""; requirementId.value = ""; }
function resetFilters() { if (!sectionKey.value && !outcome.value && !requirementId.value) return loadPage(); sectionKey.value = ""; outcome.value = ""; requirementId.value = ""; }
watch(filters, () => { rows.value = []; meta.value = null; cursors.value = [null]; position.value = 0; loadPage(); });
watch(() => [taskId.value, reportId.value], load, { immediate: true });
window.addEventListener("bid:org-reset", clear);
onBeforeUnmount(() => { clear(); window.removeEventListener("bid:org-reset", clear); });
</script>

<template>
  <nav class="breadcrumb" aria-label="位置"><RouterLink :to="`/org/tasks/${taskId}`">{{ taskName || '任务' }}</RouterLink><span>/</span><RouterLink :to="`/org/tasks/${taskId}/scores?job=${summary?.report.extraction_job_id ?? ''}`">评分预估</RouterLink><span>/</span><span>评分报告</span></nav>
  <header class="page-header"><div><h2>评分报告</h2><p>仅评估已保存初稿；预估与建议仅供参考。</p></div><el-button @click="load">重新读取报告</el-button></header>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" /><el-skeleton v-if="loading" :rows="4" animated />
  <template v-if="summary">
    <el-alert v-if="stale" title="报告已过期，仅供追溯。请重新组表、核对评分规则并评分" type="warning" :closable="false" show-icon role="alert" />
    <el-alert v-if="contradiction" title="地址中的选择与报告不一致，当前显示报告固定的提取、初稿和评分规则" type="info" :closable="false" />
    <p>提取 {{ summary.report.extraction_job_id }} · 初稿 {{ summary.report.draft_id }} · 第 {{ summary.rubric_version }} 版评分规则</p><p>评估日期 {{ summary.report.assessment_date }} · {{ stateLabels[summary.report.completion] }} · {{ stateLabels[summary.report.validity] }} · {{ formatTime(summary.report.created_at) }}</p>
    <el-alert v-if="summary.report.completion === 'partial'" title="部分内容未完成评分，请核对已保存的评分项与停止原因" type="warning" :closable="false" show-icon />
    <section class="score-summary" aria-label="评分结果概况" aria-live="polite">
      <h3>{{ summary.total_status === 'estimated' ? '总分预估' : summary.total_status === 'range_only' ? '可能区间，不能作为得分' : '总分暂不可用' }}</h3><p v-if="summary.total_status === 'estimated'" class="score-value" data-testid="score-total">{{ scoreText(summary.estimated_total) }}</p><p v-else-if="summary.total_status === 'range_only'">仅有可能范围，不能作为总分</p>
      <dl><dt>已评估部分小计（不是总分）</dt><dd>{{ scoreText(summary.assessed_subtotal) }}</dd><dt>可能范围</dt><dd>{{ rangeText(summary.possible_range) }}</dd><dt>已评估评分项</dt><dd>{{ summary.assessed_items }}</dd><dt>无法评估评分项</dt><dd>{{ summary.unassessable_items }}</dd><dt>分节数量</dt><dd>{{ summary.section_count }}</dd></dl>
      <p class="hint">小计与可能范围不代表总分；无法评估的评分项保持未知，不计为零分。</p><p>总分规则：{{ aggregationLabels[summary.overall_aggregation] }} · {{ summary.overall_rule_text }}</p><p v-if="summary.overall_cap != null">总分封顶 {{ summary.overall_cap }}</p><p v-if="!summary.overall_aggregation_assessable">评分规则已核对后，系统仍无法自动合计。</p>
    </section>
    <div class="actions"><RouterLink :to="`/org/tasks/${taskId}/score-rubrics/${summary.rubric_id}`">查看本次评分规则</RouterLink><RouterLink :to="queryPath(`/org/tasks/${taskId}/scores`, { job: summary.report.extraction_job_id, draft: summary.report.draft_id, rubric: summary.rubric_id })">重新预览评分</RouterLink><el-button @click="showJob = !showJob">查看报告作业与实际费用</el-button></div>
    <JobPanel v-if="showJob" assessment-mode :job-id="summary.report.job_id" :writable="false" />
    <el-tabs v-model="part" aria-label="评分报告分区"><el-tab-pane label="分节合计" name="sections" /><el-tab-pane label="逐项评分" name="items" /><el-tab-pane label="提示" name="notices" /></el-tabs>
    <div v-if="part !== 'notices'" class="actions"><el-input v-model="sectionKey" aria-label="按分节筛选" placeholder="分节标识" clearable /><template v-if="part === 'items'"><el-select v-model="outcome" aria-label="评估结果" placeholder="全部评估结果" clearable><el-option value="assessed" label="已评估" /><el-option value="unassessable" label="无法评估" /></el-select><el-input v-model="requirementId" aria-label="按要求筛选" placeholder="要求 ID" clearable /></template><el-button @click="resetFilters">重置筛选</el-button></div>
    <p v-if="meta" role="status" aria-live="polite">匹配 {{ meta.filtered_total }} / 全部 {{ meta.total }} 条；第 {{ position + 1 }} 页，本页 {{ meta.returned }} 条</p><el-alert v-if="pageError" :title="pageError" type="error" :closable="false" show-icon role="alert" /><el-skeleton v-if="pageLoading" :rows="3" animated /><el-empty v-else-if="!rows.length && !pageError" description="当前筛选下没有记录"><el-button @click="resetFilters">重置筛选</el-button></el-empty>
    <template v-if="part === 'items'">
      <el-card v-for="row in rows" :key="row.id" class="section" shadow="never">
        <h3>评分项 {{ row.rubric_item_id }}</h3><p>分节 {{ row.section_key }} · {{ row.outcome === 'assessed' ? '已评估' : '无法评估' }} · {{ stateLabels[row.anchor_partition] }}</p>
        <p v-if="row.outcome === 'assessed'" class="item-score">条目预估：{{ scoreText(row.estimated_score) }} · 约定范围 {{ rangeText(row.score_range) }}</p><p v-else>条目分数：未知 · 约定范围 {{ rangeText(row.score_range) }}</p>
        <p>{{ row.reason }}（{{ row.reason_code }}）</p>
        <section v-if="row.deduction_reasons.length" aria-label="失分原因"><h4>失分原因</h4><ul><li v-for="reason in row.deduction_reasons" :key="reason">{{ reason }}</li></ul></section><section v-if="row.strengthening_actions.length" aria-label="改进建议"><h4>改进建议</h4><ul><li v-for="action in row.strengthening_actions" :key="action">{{ action }}</li></ul></section>
        <p v-if="!row.citations.length">此条目没有已保存的评分引用，请按原因核对。</p>
        <details v-else><summary>查看保存的原文、响应和证据引用</summary><article v-for="(citation, index) in row.citations" :key="index"><p>{{ citation.kind === 'tender' ? '招标原文' : citation.kind === 'draft' ? '固定初稿响应' : '已保存证据' }}</p><blockquote class="quote">{{ citation.quote ?? citation.source?.quote }}</blockquote><AssessmentCitation :task-id="taskId" parent-kind="score" :parent-id="reportId" part="score_item" :entry-id="row.id" origin="citations" :citation-index="index" /></article></details>
        <RouterLink :to="reviewHref(row.requirement_id)">前往响应卡修改</RouterLink><p class="hint">修改后须由职责负责人审核并重新组表，再运行新评分；本报告保留原有结果。</p>
      </el-card>
    </template>
    <div v-else-if="part === 'sections'" class="table-scroll" tabindex="0" aria-label="分节合计滚动区域"><table class="data-table"><caption>保存的分节合计与无法评估状态</caption><thead><tr><th>分节</th><th>合计状态</th><th>分节分数</th><th>小计与范围</th><th>合计规则</th></tr></thead><tbody><tr v-for="row in rows" :key="row.section_key"><td>{{ row.title }}<small>{{ row.section_key }}</small></td><td>{{ totalLabels[row.status] }}<p>已评估 {{ row.assessed_items }} · 无法评估 {{ row.unassessable_items }}</p></td><td>{{ row.status === 'estimated' ? scoreText(row.estimated_score) : '未知' }}</td><td><p>已评估小计 {{ scoreText(row.assessed_subtotal) }}</p><p>可能范围 {{ rangeText(row.possible_range) }}</p><p>约定范围 {{ rangeText(row.configured_range) }}</p></td><td>{{ aggregationLabels[row.aggregation] }}<p>{{ row.aggregation_rule_text }}</p><p v-if="row.cap != null">封顶 {{ row.cap }}</p><p v-if="!row.aggregation_assessable">无法自动合计</p></td></tr></tbody></table></div>
    <ul v-else aria-label="评分提示"><li v-for="row in rows" :key="row.code">{{ row.message }}（{{ row.code }}）</li></ul>
    <div class="actions"><el-button :disabled="position === 0 || pageLoading" @click="back">上一页</el-button><el-button :disabled="!meta?.next_cursor || pageLoading" @click="forward">下一页</el-button><el-button :disabled="pageLoading" @click="loadPage()">重新读取当前分区</el-button></div>
  </template>
</template>
<style scoped>.score-summary { padding:16px; background:var(--surface-muted); margin:14px 0; border-radius:6px; } .score-summary dl { display:grid; grid-template-columns:max-content 1fr; gap:6px 18px; } dd { margin:0; } .score-value { font-size:2rem; font-weight:700; } .quote { white-space:pre-wrap; overflow-wrap:anywhere; } .section { overflow-wrap:anywhere; } .actions .el-input, .actions .el-select { width:240px; } td small { display:block; } article { margin:12px 0; }</style>
