<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import JobPanel from "../components/JobPanel.vue";
import { categories, deviations, display, errorText, formatTime, label, locationLabel, orgAccess, orgRequest, responseKinds, warningText } from "../org.js";

const route = useRoute(), router = useRouter();

const extraction = ref(null);
const taskName = ref("");
const drafts = ref([]);
const selectedDraft = ref(null);
const preview = ref(null);
const draftJobId = ref("");
const error = ref("");
const loading = ref(false);
const previewing = ref(false);
const submitting = ref(false);
const retry = ref(false);
const resultWarnings = ref([]);
let detailSequence = 0;
const activeSection = ref("substantive");
const query = ref("");
const page = ref(1);
const pageSize = 50;

const taskId = computed(() => String(route.params.taskId ?? ""));
const jobId = computed(() => {
  const value = route.query.job;
  return Array.isArray(value) ? String(value[0] ?? "") : String(value ?? "");
});
const canWrite = computed(() => ["admin", "bidder", "technical"].includes(orgAccess.role));
const jobReady = computed(() => extraction.value?.status === "succeeded");
const negativeRows = computed(() => {
  if (!selectedDraft.value) return [];
  return Object.values(selectedDraft.value.tables ?? {})
    .flat()
    .filter((row) => row.deviation === "negative");
});

const sections = computed(() => {
  const detail = selectedDraft.value;
  return [
    { id: "substantive", label: "实质性响应", rows: detail?.tables?.substantive ?? [] },
    { id: "commercial", label: "商务响应", rows: detail?.tables?.commercial ?? [] },
    { id: "technical", label: "技术响应", rows: detail?.tables?.technical ?? [] },
    { id: "comply_only", label: "须遵守清单", rows: detail?.comply_only ?? [] },
    { id: "gaps", label: "缺口", rows: detail?.gaps ?? [] },
  ];
});
const currentSection = computed(
  () => sections.value.find((section) => section.id === activeSection.value) ?? sections.value[0],
);
const filteredRows = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase();
  if (!needle) return currentSection.value.rows;
  return currentSection.value.rows.filter((row) =>
    [
      row.requirement_id,
      row.category,
      row.location_label,
      row.tender_clause?.quote,
      row.response_text,
      row.deviation,
      row.deviation_note,
      ...(row.reasons ?? []),
    ]
      .filter((value) => value != null)
      .join(" ")
      .toLocaleLowerCase()
      .includes(needle),
  );
});
const totalPages = computed(() => Math.max(1, Math.ceil(filteredRows.value.length / pageSize)));
const visibleRows = computed(() =>
  filteredRows.value.slice((page.value - 1) * pageSize, page.value * pageSize),
);

const statusLabels = {
  draft: "初稿",
  complete: "完整",
  partial: "有缺口",
  current: "当前有效",
  stale: "已失效",
};

const gapLabels = {
  missing_card: "尚无响应卡",
  unconfirmed: "响应卡尚未确认",
  rejected: "响应卡已驳回",
  needs_material: "响应卡需要补充材料",
  unclassified: "响应职责尚未分类",
  stale_material: "所选材料已变化",
  invalid_citation: "招标原文引用无效",
  needs_reconfirmation: "引用修复后需要重新确认",
};

function reviewHref(requirementId) {
  const base = `/org/tasks/${encodeURIComponent(taskId.value)}/review?job=${encodeURIComponent(jobId.value)}`;
  return requirementId ? `${base}&requirement=${encodeURIComponent(requirementId)}` : base;
}

function sourceLabel(row) {
  return row.location_label || locationLabel(row.tender_clause);
}

function resetListing() {
  page.value = 1;
}

watch([activeSection, query], resetListing);
watch(totalPages, (count) => {
  if (page.value > count) page.value = count;
});

let loadSequence = 0;
// The draft the user last asked for; a background reload must not override that choice.
let wantedDraftId = null;
async function loadPage() {
  const sequence = ++loadSequence;
  error.value = "";
  extraction.value = null;
  drafts.value = [];
  selectedDraft.value = null;
  preview.value = null;
  draftJobId.value = "";
  if (!taskId.value || !jobId.value) {
    error.value = "地址中必须包含明确的抽取 job，例如 ?job=J。";
    return;
  }

  loading.value = true;
  try {
    orgRequest("GET", `/tasks/${encodeURIComponent(taskId.value)}`).then((result) => { if (sequence === loadSequence) taskName.value = result.data.name; }).catch(() => {});
    const history = await orgRequest(
      "GET",
      `/tasks/${encodeURIComponent(taskId.value)}/extractions`,
    );
    if (sequence !== loadSequence) return;
    extraction.value = (history.items ?? []).find((item) => item.job_id === jobId.value) ?? null;
    if (!extraction.value) {
      error.value = "所选抽取 job 不属于当前任务，或当前单位无权读取。";
      return;
    }
    if (!jobReady.value) {
      error.value = `所选抽取 job 状态为 ${display(extraction.value.status)}；只有成功记录可以组表。`;
      return;
    }
    await loadDrafts(sequence);
  } catch (exc) {
    if (sequence === loadSequence) error.value = errorText(exc);
  } finally {
    if (sequence === loadSequence) loading.value = false;
  }
}

async function loadDrafts(sequence = loadSequence) {
  const result = await orgRequest(
    "GET",
    `/tasks/${encodeURIComponent(taskId.value)}/drafts?job=${encodeURIComponent(jobId.value)}`,
  );
  if (sequence !== loadSequence) return;
  drafts.value = [...(result.items ?? [])].reverse();
  if (!drafts.value.length) {
    selectedDraft.value = null;
    return;
  }
  const currentId = wantedDraftId ?? selectedDraft.value?.id;
  const target = drafts.value.find((item) => item.id === currentId) ?? drafts.value[0];
  await showDraft(target.id, sequence);
}

async function showDraft(id, sequence = loadSequence) {
  error.value = "";
  wantedDraftId = id;
  const detailRequest = ++detailSequence;
  try {
    const result = await orgRequest("GET", `/drafts/${encodeURIComponent(id)}`);
    if (sequence !== loadSequence || detailRequest !== detailSequence) return;
    if (result.data.task_id !== taskId.value || result.data.extraction_job_id !== jobId.value) throw new Error("初稿不属于当前任务与抽取");
    selectedDraft.value = result.data;
    resultWarnings.value = result.warnings;
    activeSection.value = "substantive";
    query.value = "";
    page.value = 1;
  } catch (exc) {
    if (sequence === loadSequence) error.value = errorText(exc);
  }
}

async function previewDraft() {
  if (!jobReady.value || !canWrite.value) return;
  error.value = "";
  preview.value = null;
  previewing.value = true;
  try {
    const result = await orgRequest("POST", `/tasks/${encodeURIComponent(taskId.value)}/drafts`, {
      extraction_job_id: jobId.value,
      dry_run: true,
    });
    preview.value = result.data;
  } catch (exc) {
    error.value = errorText(exc);
  } finally {
    previewing.value = false;
  }
}

async function submitDraft() {
  if (!preview.value || !jobReady.value || !canWrite.value) return;
  error.value = "";
  submitting.value = true;
  try {
    const result = await orgRequest("POST", `/tasks/${encodeURIComponent(taskId.value)}/drafts`, {
      extraction_job_id: jobId.value,
      dry_run: false,
      retry: retry.value,
    });
    draftJobId.value = result.data?.job_id ?? result.data?.generation_job_id ?? "";
    preview.value = null;
  } catch (exc) {
    error.value = errorText(exc);
  } finally {
    submitting.value = false;
  }
}

async function onDraftFinished() {
  preview.value = null;
  selectedDraft.value = null;
  try {
    await loadDrafts();
  } catch (exc) {
    error.value = errorText(exc);
  }
}

function invalidateAssemblyPreview(event) {
  const changedTask = event?.detail?.taskId ?? event?.detail?.task_id;
  if (!changedTask || changedTask === taskId.value) preview.value = null;
}

function visibilityChanged() {
  if (document.hidden) preview.value = null;
}

watch(
  () => [taskId.value, jobId.value],
  loadPage,
);
onMounted(() => {
  loadPage();
  window.addEventListener("bid:task-materials-changed", invalidateAssemblyPreview);
  window.addEventListener("bid:task-cards-changed", invalidateAssemblyPreview);
  document.addEventListener("visibilitychange", visibilityChanged);
});
onBeforeUnmount(() => {
  window.removeEventListener("bid:task-materials-changed", invalidateAssemblyPreview);
  window.removeEventListener("bid:task-cards-changed", invalidateAssemblyPreview);
  document.removeEventListener("visibilitychange", visibilityChanged);
});
</script>

<template>
  <div class="draft-page">
    <nav class="breadcrumb" aria-label="位置"><RouterLink to="/org/tasks">招标任务</RouterLink><span>/</span><RouterLink :to="`/org/tasks/${taskId}`">{{ taskName || "任务" }}</RouterLink><span>/</span><span>响应表初稿</span></nav>
    <header class="page-header">
      <div>
        <h2>响应表初稿</h2>
        <p class="subtitle">固定抽取 <code>{{ jobId || "未知" }}</code>。初稿不会导出，也不代表整份投标文件已经完成。</p>
      </div>
      <el-button @click="router.push(reviewHref())">返回逐条审阅</el-button>
    </header>

    <el-skeleton v-if="loading" :rows="4" animated aria-label="正在核对抽取记录与历史初稿" />
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />

    <div class="draft-top">
    <el-card v-if="jobReady" class="section" shadow="never" aria-labelledby="assemble-title">
      <template #header>
        <div class="section-title">
          <div><h3 id="assemble-title">组表</h3><p class="hint">组表只复制已经人工确认的内容，不调用模型或 OCR。</p></div>
          <el-button v-if="canWrite" type="primary" plain :loading="previewing" :disabled="submitting" @click="previewDraft">预检组表</el-button>
          <span v-else class="hint">当前角色只读</span>
        </div>
      </template>
      <div v-if="preview" class="draft-preview">
        <div class="stat-grid">
          <div class="stat"><span class="label">响应行</span><span class="value">{{ preview.response_requirements }}</span></div>
          <div class="stat"><span class="label">须遵守</span><span class="value">{{ preview.comply_only_requirements }}</span></div>
          <div class="stat"><span class="label">缺口</span><span class="value">{{ preview.gap_requirements }}</span></div>
          <div class="stat danger"><span class="label">负偏离</span><span class="value">{{ preview.negative_deviations }}</span></div>
        </div>
        <div class="tags"><span class="tag">实质性 {{ preview.table_rows?.substantive ?? 0 }}</span><span class="tag">商务 {{ preview.table_rows?.commercial ?? 0 }}</span><span class="tag">技术 {{ preview.table_rows?.technical ?? 0 }}</span></div>
        <details v-if="Object.keys(preview.gap_reasons ?? {}).length">
          <summary>按原因查看缺口</summary>
          <ul><li v-for="(count, reason) in preview.gap_reasons" :key="reason">{{ gapLabels[reason] ?? reason }}：{{ count }}</li></ul>
        </details>
        <p class="hint">本次预检实际模型成本为 {{ display(preview.estimated_cost?.usd) }} USD；这是确定性组表的零成本，不抵消此前模型起草费用。</p>
        <p class="hint">输入标识 <code>{{ preview.input_hash }}</code> · 预计时长 {{ preview.estimated_duration_ms == null ? "未知" : `${preview.estimated_duration_ms} ms` }}</p>
        <div class="actions"><label class="check"><input v-model="retry" type="checkbox" />显式重试已失败或取消的组表作业</label><el-button type="primary" :loading="submitting" @click="submitDraft">确认生成初稿</el-button></div>
      </div>
      <p v-else class="hint">先预检，核对响应行、须遵守、缺口和负偏离数量后再生成。</p>
    </el-card>


    <el-card v-if="jobReady" class="section" shadow="never" aria-labelledby="history-title">
      <template #header><div class="section-title"><h3 id="history-title">历史初稿</h3><span class="hint">{{ drafts.length }} 份</span></div></template>
      <div v-if="drafts.length" class="history-list">
        <button v-for="item in drafts" :key="item.id" type="button" :class="{ selected: selectedDraft?.id === item.id }" @click="showDraft(item.id)">
          <span class="history-main"><strong>{{ item.created_at ? formatTime(item.created_at) : "创建时间未知" }}</strong><small class="hint mono">{{ item.id }}</small></span>
          <span class="history-status">
            <span class="tags">
              <span class="tag" :class="item.completion === 'complete' && item.validity === 'current' ? 'success' : 'danger'">{{ item.completion === "complete" && item.validity === "stale" ? "历史快照完整" : statusLabels[item.completion] }}</span>
              <span class="tag" :class="item.validity === 'current' ? 'success' : 'danger'">{{ statusLabels[item.validity] }}</span>
            </span>
            <small class="hint">行 {{ item.summary.rows }} · 遵守 {{ item.summary.comply_only }} · 缺口 {{ item.summary.gaps }} · 负偏离 {{ item.summary.negative_deviations }}</small>
          </span>
        </button>
      </div>
      <el-empty v-else description="这个抽取还没有初稿。先完成响应审阅，再运行组表预检。" :image-size="64" />
    </el-card>

    </div>
    <JobPanel v-if="draftJobId" :job-id="draftJobId" :writable="canWrite" @finished="onDraftFinished" />
    <p v-for="warning in resultWarnings" :key="warning" class="notice warning">{{ warningText(warning) }}</p>
    <el-card v-if="selectedDraft" class="section draft-detail" shadow="never">
      <template #header>
        <div class="section-title">
          <div><h3>初稿详情</h3><p class="hint">输入抽取 <code>{{ selectedDraft.extraction_job_id }}</code></p></div>
          <div class="tags">
            <el-tag :type="selectedDraft.completion === 'complete' && selectedDraft.validity === 'current' ? 'success' : 'danger'">{{ selectedDraft.completion === "complete" && selectedDraft.validity === "stale" ? "历史快照完整（不可交付）" : statusLabels[selectedDraft.completion] }}</el-tag>
            <el-tag :type="selectedDraft.validity === 'current' ? 'success' : 'danger'">{{ statusLabels[selectedDraft.validity] }}</el-tag>
          </div>
        </div>
      </template>

      <el-alert v-if="selectedDraft.validity === 'stale'" type="warning" show-icon :closable="false" role="alert" class="section">
        <template #title>这份历史快照已经失效。</template>
        <p>原内容仍保留，但 {{ selectedDraft.invalidated_requirements.length }} 条要求已受当前卡片、材料或引用变化影响。请返回审阅后重新组表。</p>
        <details>
          <summary>查看受影响要求</summary>
          <ul><li v-for="requirementId in selectedDraft.invalidated_requirements" :key="requirementId"><RouterLink :to="reviewHref(requirementId)"><code>{{ requirementId }}</code></RouterLink></li></ul>
        </details>
      </el-alert>
      <p v-if="negativeRows.length" class="notice danger"><strong>{{ negativeRows.length }} 条负偏离始终保留在响应表中。</strong>负偏离不是缺口，请在对应表内逐条核对。</p>

      <el-tabs v-model="activeSection" class="section-tabs" aria-label="初稿分区">
        <el-tab-pane v-for="section in sections" :key="section.id" :name="section.id">
          <template #label>{{ section.label }} <span class="count">{{ section.rows.length }}</span></template>
        </el-tab-pane>
      </el-tabs>

      <div class="list-toolbar">
        <el-input v-model="query" type="search" clearable placeholder="原文、位置、响应、原因或要求 ID" aria-label="筛选当前分区" class="filter" />
        <span class="hint">匹配 {{ filteredRows.length }} / {{ currentSection.rows.length }}</span>
      </div>

      <div class="table-scroll" tabindex="0" aria-label="初稿表格滚动区域">
        <table v-if="activeSection !== 'comply_only' && activeSection !== 'gaps'" class="data-table">
          <thead><tr><th>要求与位置</th><th>响应</th><th>偏离</th><th>证据</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="row in visibleRows" :key="row.requirement_id" :class="{ 'negative-row': row.deviation === 'negative' }">
              <td class="col-req">
                <div class="tags"><span v-if="row.starred" class="tag star">★</span><span class="tag">{{ label(categories, row.category) }}</span></div>
                <blockquote class="quote">{{ row.tender_clause.quote }}</blockquote>
                <small class="hint">{{ sourceLabel(row) }}</small>
              </td>
              <td><span class="tag primary">{{ label(responseKinds, row.response_kind) }}</span><p class="response-text">{{ row.response_text }}</p></td>
              <td><span class="tag" :class="row.deviation === 'negative' ? 'danger' : row.deviation === 'positive' ? 'success' : ''">{{ label(deviations, row.deviation) }}</span><p class="response-text">{{ row.deviation_note }}</p></td>
              <td>
                <details v-if="row.evidence?.length">
                  <summary>{{ row.evidence.length }} 项已确认材料</summary>
                  <ul class="evidence-list"><li v-for="evidence in row.evidence" :key="evidence.id"><span class="hint">{{ evidence.input?.kind }} · {{ evidence.input?.field_path ?? `来源 ${display(evidence.input?.evidence_source_id)}` }}</span><q>{{ evidence.input?.quote }}</q></li></ul>
                </details>
                <span v-else class="hint">承诺不附证据</span>
              </td>
              <td><RouterLink :to="reviewHref(row.requirement_id)">回到审阅</RouterLink></td>
            </tr>
          </tbody>
        </table>
        <table v-else-if="activeSection === 'comply_only'" class="data-table">
          <thead><tr><th>招标原文</th><th>位置</th><th>人工决定</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="row in visibleRows" :key="row.requirement_id">
              <td><blockquote class="quote">{{ row.tender_clause.quote }}</blockquote></td>
              <td class="hint">{{ sourceLabel(row) }}</td>
              <td>仅需遵守 · {{ formatTime(row.disposition_at) }}</td>
              <td><RouterLink :to="reviewHref(row.requirement_id)">回到审阅</RouterLink></td>
            </tr>
          </tbody>
        </table>
        <table v-else class="data-table">
          <thead><tr><th>招标原文</th><th>位置</th><th>缺口原因</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="row in visibleRows" :key="row.requirement_id">
              <td><blockquote class="quote">{{ row.tender_clause.quote }}</blockquote></td>
              <td class="hint">{{ sourceLabel(row) }}</td>
              <td><div class="tags"><span v-for="reason in row.reasons" :key="reason" class="tag warning">{{ gapLabels[reason] ?? reason }}</span></div></td>
              <td><RouterLink :to="reviewHref(row.requirement_id)">回到审阅</RouterLink></td>
            </tr>
          </tbody>
        </table>
      </div>
      <el-empty v-if="!visibleRows.length" description="当前筛选没有结果" :image-size="64" />
      <el-pagination v-if="filteredRows.length > pageSize" v-model:current-page="page" class="pager" background layout="total, prev, pager, next" :page-size="pageSize" :total="filteredRows.length" />
    </el-card>
  </div>
</template>

<style scoped>
.draft-page { min-width: 0; }
.draft-top { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 0 16px; align-items: start; }
@media (max-width: 1200px) { .draft-top { grid-template-columns: minmax(0, 1fr); } }
.draft-preview .tags { margin-bottom: 8px; }
.history-list { display: grid; gap: 8px; }
.history-list > button { display: flex; justify-content: space-between; gap: 16px; padding: 12px 14px; text-align: left; font: inherit; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; cursor: pointer; }
.history-list > button:hover { border-color: var(--el-color-primary-light-5); }
.history-list > button.selected { border-color: var(--el-color-primary); box-shadow: 0 0 0 1px var(--el-color-primary); background: var(--el-color-primary-light-9); }
.history-main, .history-status { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.history-status { align-items: flex-end; }
.section-tabs .count { display: inline-block; min-width: 20px; padding: 0 6px; margin-left: 4px; border-radius: 10px; background: var(--surface-muted); color: var(--muted); font-size: 12px; line-height: 18px; text-align: center; }
.list-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.list-toolbar .filter { max-width: 420px; }
.data-table { min-width: 780px; }
.col-req { min-width: 260px; }
.response-text { white-space: pre-wrap; margin: 6px 0; }
.negative-row { background: #fff8f7; }
.evidence-list { margin: 6px 0; padding-left: 18px; }
.evidence-list li { display: flex; flex-direction: column; gap: 3px; margin-bottom: 8px; }
.evidence-list q { white-space: pre-wrap; }
.pager { margin-top: 12px; justify-content: center; }
@media (max-width: 720px) {
  .list-toolbar { flex-direction: column; align-items: stretch; }
  .history-list > button { flex-direction: column; }
  .history-status { align-items: flex-start; }
}
</style>
