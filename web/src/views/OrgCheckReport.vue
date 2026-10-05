<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { useRoute } from "vue-router";
import AssessmentCitation from "../components/AssessmentCitation.vue";
import JobPanel from "../components/JobPanel.vue";
import { assessmentError, domainLabels, enc, queryPath, severityLabels, stateLabels, useAssessmentPage, useUnsaved } from "../assessments.js";
import { formatTime, orgRequest } from "../org.js";
const route = useRoute(), summary = ref(null), taskName = ref(""), error = ref(""), loading = ref(false);
const taskId = computed(() => String(route.params.taskId)), reportId = computed(() => String(route.params.reportId));
const path = computed(() => `/checks/${enc(reportId.value)}`);
const part = ref("findings"), severity = ref(""), domain = ref(""), status = ref(""), requirement = ref("");
const filters = computed(() => ({ part: part.value, ...(part.value === "findings" ? { severity: severity.value, domain: domain.value, status: status.value } : {}), ...(["findings", "coverage"].includes(part.value) ? { requirement_id: requirement.value } : {}) }));
const listing = useAssessmentPage(path, filters, refreshSummary);
const { rows, meta, error: pageError, loading: pageLoading, next, position } = listing;
const dialog = ref(false), decision = ref(null), reason = ref(""), reasonInput = ref(null), decisionError = ref(""), busy = ref(false), historyRow = ref(null), historyItems = ref([]), historyCursor = ref(null), showJob = ref(false);
let sequence = 0;
const dirty = computed(() => !!reason.value.trim());
useUnsaved(dirty, () => { sequence++; summary.value = null; dialog.value = false; reason.value = ""; historyItems.value = []; decision.value = null; });
const stale = computed(() => summary.value?.report.validity === "stale");
const contradiction = computed(() => summary.value && ((route.query.job && route.query.job !== summary.value.report.extraction_job_id) || (route.query.draft && route.query.draft !== summary.value.report.draft_id)));
const groupedRows = computed(() => {
  const groups = [];
  for (const row of rows.value) {
    const key = `${row.severity}:${row.review_domain ?? 'unclassified'}`;
    let group = groups.find(g => g.key === key);
    if (!group) { group = { key, severity: row.severity, domain: row.review_domain, rows: [] }; groups.push(group); }
    group.rows.push(row);
  }
  return groups;
});
const allowed = (row, action) => !stale.value && meta.value?.subject_actions?.find(s => s.subject_id === row.id)?.actions.some(a => a.action === `finding_${action}` && a.allowed);
const reviewHref = (requirementId) => `/org/tasks/${enc(taskId.value)}/review?job=${enc(summary.value.report.extraction_job_id)}&requirement=${enc(requirementId)}`;
async function refreshSummary() {
  const current = sequence, id = reportId.value;
  const result = await orgRequest("GET", queryPath(path.value, { view: "console", part: "summary" }));
  if (current !== sequence) return;
  if (result.data.report.id !== id || result.data.report.task_id !== taskId.value) throw { status: 404 };
  summary.value = result.data;
}
async function load() {
  const current = ++sequence; loading.value = true; error.value = ""; summary.value = null; dialog.value = false; reason.value = ""; historyRow.value = null;
  try { await refreshSummary(); if (current !== sequence) return; taskName.value = (await orgRequest("GET", `/tasks/${enc(taskId.value)}`)).data.name; await listing.load(); }
  catch (exc) { if (exc.name !== "AbortError" && current === sequence) error.value = assessmentError(exc); }
  finally { if (current === sequence) loading.value = false; }
}
async function openDecision(row, action) { decision.value = { row, action }; reason.value = ""; decisionError.value = ""; dialog.value = true; await nextTick(); reasonInput.value?.focus(); }
async function saveDecision() {
  if (!reason.value.trim()) { decisionError.value = "请填写处理理由"; reasonInput.value?.focus(); return; }
  if (!decision.value || !allowed(decision.value.row, decision.value.action)) return;
  busy.value = true; decisionError.value = "";
  try {
    const row = decision.value.row;
    await orgRequest("POST", `${path.value}/findings/${enc(row.id)}/decisions`, { action: decision.value.action, reason: reason.value.trim(), expected_revision: row.revision, expected_input_hash: summary.value.report.input_hash });
    dialog.value = false; reason.value = ""; await refreshSummary(); await listing.load(); await loadHistory(row);
  } catch (exc) {
    if (exc.name === "AbortError") return;
    decisionError.value = assessmentError(exc);
    if (["revision_conflict", "assessment_view_changed", "check_input_changed"].includes(exc.code)) {
      try { await refreshSummary(); await listing.load(); const result = await orgRequest("GET", queryPath(path.value, { view: "console", part: "findings", entry_id: decision.value.row.id })); if (result.items[0]) decision.value = { ...decision.value, row: result.items[0] }; } catch (reload) { decisionError.value += `；${assessmentError(reload)}`; }
    }
  } finally { busy.value = false; }
}
async function loadHistory(row, cursor = null) {
  historyRow.value = row; error.value = "";
  try { await refreshSummary(); const result = await orgRequest("GET", queryPath(`${path.value}/findings/${enc(row.id)}/decisions`, { limit: 50, cursor })); historyItems.value = result.items; historyCursor.value = result.data.next_cursor; }
  catch (exc) { if (exc.name !== "AbortError") error.value = assessmentError(exc); }
}
watch(filters, () => { historyRow.value = null; listing.load(); });
watch(() => [taskId.value, reportId.value], load, { immediate: true });
</script>
<template>
  <nav class="breadcrumb" aria-label="位置"><RouterLink :to="`/org/tasks/${taskId}`">{{ taskName || '任务' }}</RouterLink><span>/</span><RouterLink :to="`/org/tasks/${taskId}/checks?job=${summary?.report.extraction_job_id ?? ''}`">检查风险</RouterLink><span>/</span><span>检查报告</span></nav>
  <header class="page-header"><div><h2>检查报告</h2><p>仅评估已保存初稿；风险标签不是最终废标或扣分结论。</p></div><el-button @click="load">重新读取报告</el-button></header>
  <el-skeleton v-if="loading" :rows="4" animated />
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" />
  <template v-if="summary">
    <el-alert v-if="stale" title="报告已过期，仅供追溯。请重新组表并检查" type="warning" show-icon :closable="false" role="alert" />
    <el-alert v-if="contradiction" title="地址中的选择与报告不一致，当前显示报告固定的提取和初稿" type="info" :closable="false" />
    <p>提取 {{ summary.report.extraction_job_id }} · 初稿 {{ summary.report.draft_id }} · 评估日期 {{ summary.report.assessment_date }}</p>
    <p>{{ summary.mode === 'rules' ? '规则检查' : '规则与语义检查' }} · {{ stateLabels[summary.report.completion] }} · {{ stateLabels[summary.report.validity] }}</p>
    <div class="stat-grid" aria-live="polite"><div class="stat"><span class="label">全部要求</span><strong>{{ summary.item_count }}</strong></div><div class="stat"><span class="label">机器发现风险（含已忽略）</span><strong>{{ summary.finding_count }}</strong></div><div class="stat"><span class="label">未评估</span><strong>{{ summary.unassessed_count }}</strong></div><div class="stat"><span class="label">证照日期</span><strong>{{ summary.certificate_count }}</strong></div></div>
    <p v-if="summary.unassessed_count > 0">部分内容未完成检查</p><p v-else-if="summary.finding_count === 0">在本次{{ summary.mode === 'rules' ? '规则' : '规则与语义' }}检查的已评估范围内未发现风险</p>
    <p v-for="group in summary.groups" :key="`${group.severity}-${group.review_domain}-${group.status}`">{{ severityLabels[group.severity] }} · {{ domainLabels[group.review_domain ?? 'unclassified'] }} · {{ stateLabels[group.status] }}：{{ group.count }}</p>
    <el-button @click="showJob = !showJob">查看报告作业与实际费用</el-button><JobPanel assessment-mode v-if="showJob" :job-id="summary.report.job_id" :writable="false" />
    <el-tabs v-model="part" aria-label="检查报告分区"><el-tab-pane label="风险条目" name="findings" /><el-tab-pane label="覆盖情况" name="coverage" /><el-tab-pane label="证照日期" name="certificates" /><el-tab-pane label="提示" name="notices" /></el-tabs>
    <div v-if="part === 'findings'" class="actions">
      <el-select v-model="severity" aria-label="风险级别" placeholder="全部级别" clearable><el-option v-for="(label, value) in severityLabels" :key="value" :value="value" :label="label" /></el-select>
      <el-select v-model="domain" aria-label="负责职责" placeholder="全部职责" clearable><el-option v-for="(label, value) in domainLabels" :key="value" :value="value" :label="label" /></el-select>
      <el-select v-model="status" aria-label="处理状态" placeholder="全部状态" clearable><el-option value="open" label="待处理" /><el-option value="dismissed" label="已忽略" /></el-select>
    </div>
    <el-input v-if="['findings', 'coverage'].includes(part)" v-model="requirement" placeholder="要求 ID" aria-label="按要求筛选" clearable />
    <el-alert v-if="pageError" :title="pageError" type="error" :closable="false" show-icon role="alert" />
    <p v-if="meta" role="status" aria-live="polite">匹配 {{ meta.filtered_total }} / 全部 {{ meta.total }} 条；第 {{ position + 1 }} 页，本页 {{ meta.returned }} 条</p>
    <el-skeleton v-if="pageLoading" :rows="3" animated />
    <el-empty v-else-if="!rows.length && !pageError" description="当前筛选下没有记录"><el-button @click="severity = ''; domain = ''; status = ''; requirement = ''; listing.load()">重置筛选</el-button></el-empty>
    <template v-if="part === 'findings'">
      <section v-for="group in groupedRows" :key="group.key" :aria-label="`${severityLabels[group.severity]} ${domainLabels[group.domain ?? 'unclassified']}`">
        <h3>{{ severityLabels[group.severity] }} · {{ domainLabels[group.domain ?? 'unclassified'] }}</h3>
        <el-card v-for="row in group.rows" :key="row.id" shadow="never" class="section">
          <div class="section-title"><h4>{{ row.reason }}</h4><el-tag>{{ stateLabels[row.status] }} · 修订 {{ row.revision }}</el-tag></div>
          <p>{{ row.method === 'deterministic' ? '确定性规则' : '语义检查' }} · {{ row.code }}</p><blockquote class="quote">{{ row.source.quote }}</blockquote>
          <AssessmentCitation :task-id="taskId" parent-kind="check" :parent-id="reportId" part="finding" :entry-id="row.id" />
          <details v-if="row.citations.length"><summary>查看保存的响应与证据引用</summary><div v-for="(entry, index) in row.citations" :key="entry.id"><p>{{ entry.citation.kind === 'draft' ? '固定初稿响应' : entry.citation.kind === 'evidence' ? '证据' : '招标原文' }}：{{ entry.citation.quote ?? entry.citation.source?.quote }}</p><AssessmentCitation :task-id="taskId" parent-kind="check" :parent-id="reportId" part="finding" :entry-id="row.id" origin="citations" :citation-index="index" /></div></details>
          <p v-if="!row.review_domain">请管理员到响应卡分配职责，再重新组表检查</p>
          <div class="actions"><el-button v-if="allowed(row, 'dismiss')" @click="openDecision(row, 'dismiss')">忽略此风险</el-button><el-button v-if="allowed(row, 'reopen')" @click="openDecision(row, 'reopen')">重新打开</el-button><el-button @click="loadHistory(row)">查看处理记录</el-button><RouterLink :to="reviewHref(row.requirement_id)">前往响应卡修改</RouterLink></div>
        </el-card>
      </section>
    </template>
    <div v-else class="table-scroll" tabindex="0" aria-label="检查明细滚动区域"><table class="data-table"><caption>{{ part === 'coverage' ? '全部要求覆盖与未评估原因' : part === 'certificates' ? '证照评估日期与状态' : '检查提示' }}</caption><thead><tr><th>要求或对象</th><th>检查结果</th><th>定位</th></tr></thead><tbody><tr v-for="row in rows" :key="row.id ?? row.code">
      <td>{{ row.requirement_id ?? row.task_certificate_id ?? row.code }}<blockquote v-if="row.source" class="quote">{{ row.source.quote }}</blockquote></td>
      <td v-if="part === 'coverage'"><p>{{ stateLabels[row.partition] }} · {{ stateLabels[row.semantic_status] }} · {{ row.semantic_outcome ? stateLabels[row.semantic_outcome] : '' }}</p><p v-if="row.semantic_reason_code">{{ row.semantic_reason_code }}</p><p v-for="rule in row.rules" :key="`${rule.code}-${rule.task_certificate_id}`">{{ rule.code }}：{{ stateLabels[rule.outcome] ?? rule.outcome }}（{{ rule.reason_code }}）</p></td>
      <td v-else-if="part === 'certificates'">{{ stateLabels[row.date_status] }} · 评估日期 {{ row.assessment_date }}</td><td v-else>{{ row.message }}</td>
      <td><template v-if="part === 'coverage'"><AssessmentCitation :task-id="taskId" parent-kind="check" :parent-id="reportId" part="coverage" :entry-id="row.id" /><div v-for="(citation, index) in row.semantic_citations" :key="index"><p>{{ citation.quote ?? citation.source?.quote }}</p><AssessmentCitation :task-id="taskId" parent-kind="check" :parent-id="reportId" part="coverage" :entry-id="row.id" origin="citations" :citation-index="index" /></div></template><RouterLink v-if="row.requirement_id" :to="reviewHref(row.requirement_id)">前往响应卡修改</RouterLink><RouterLink v-for="id in row.requirement_ids ?? []" :key="id" :to="reviewHref(id)">要求 {{ id }}</RouterLink></td>
    </tr></tbody></table></div>
    <div class="actions"><el-button :disabled="position === 0 || pageLoading" @click="listing.back">上一页</el-button><el-button :disabled="!next || pageLoading" @click="listing.forward">下一页</el-button></div>
    <el-card v-if="historyRow" class="section" shadow="never"><h3>风险处理记录</h3><p>父报告：{{ stateLabels[summary.report.validity] }} · {{ historyRow.reason }}</p><p v-if="!historyItems.length">尚无处理记录</p><ol><li v-for="event in historyItems" :key="event.id">修订 {{ event.revision }} · {{ event.action === 'dismiss' ? '忽略' : '重新打开' }} · {{ event.reason }} · {{ event.decided_by }} · {{ formatTime(event.decided_at) }}</li></ol><el-button v-if="historyCursor" @click="loadHistory(historyRow, historyCursor)">更多处理记录</el-button></el-card>
  </template>
  <el-dialog v-model="dialog" title="风险处理" width="min(640px, 94vw)" :close-on-click-modal="false" @opened="reasonInput?.focus()">
    <p>{{ decision?.row.reason }} · {{ decision?.action === 'dismiss' ? '忽略此风险' : '重新打开' }} · 当前修订 {{ decision?.row.revision }}</p>
    <el-alert v-if="decisionError" :title="decisionError" type="error" :closable="false" role="alert" id="decision-error" />
    <el-form label-position="top" @submit.prevent="saveDecision"><el-form-item label="处理理由" required><el-input ref="reasonInput" v-model="reason" type="textarea" aria-label="处理理由" aria-describedby="decision-error" :rows="4" /></el-form-item><el-button type="primary" native-type="submit" :loading="busy" :disabled="stale">提交处理</el-button><el-button @click="dialog = false; reason = ''">取消</el-button></el-form>
  </el-dialog>
</template>
<style scoped>.actions .el-select { width:220px; } .quote { white-space:pre-wrap; overflow-wrap:anywhere; } .section h4 { margin:0; } td { overflow-wrap:anywhere; } </style>
