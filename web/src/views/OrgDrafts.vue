<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import JobPanel from "../components/JobPanel.vue";
import { display, errorText, locationLabel, orgAccess, orgRequest } from "../org.js";

const route = useRoute();

const extraction = ref(null);
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
    <header class="page-heading">
      <div>
        <h2>响应表初稿</h2>
        <p class="hint">固定抽取 job {{ jobId || "未知" }}。初稿不会导出，也不代表整份投标文件已经完成。</p>
      </div>
      <RouterLink :to="reviewHref()">返回逐条审阅</RouterLink>
    </header>

    <p v-if="loading" class="notice" role="status">正在核对抽取记录与历史初稿…</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>

    <section v-if="jobReady" class="panel" aria-labelledby="assemble-title">
      <div class="section-heading">
        <div>
          <h3 id="assemble-title">组表</h3>
          <p class="hint">组表只复制已经人工确认的内容，不调用模型或 OCR。</p>
        </div>
        <button
          v-if="canWrite"
          type="button"
          :disabled="previewing || submitting"
          @click="previewDraft"
        >
          {{ previewing ? "预检中…" : "预检组表" }}
        </button>
        <span v-else class="hint">当前角色只读</span>
      </div>

      <div v-if="preview" class="draft-preview">
        <div class="metric-grid">
          <div><span>响应行</span><strong>{{ preview.response_requirements }}</strong></div>
          <div><span>须遵守</span><strong>{{ preview.comply_only_requirements }}</strong></div>
          <div><span>缺口</span><strong>{{ preview.gap_requirements }}</strong></div>
          <div class="negative"><span>负偏离</span><strong>{{ preview.negative_deviations }}</strong></div>
        </div>
        <div class="table-counts">
          <span>实质性 {{ preview.table_rows?.substantive ?? 0 }}</span>
          <span>商务 {{ preview.table_rows?.commercial ?? 0 }}</span>
          <span>技术 {{ preview.table_rows?.technical ?? 0 }}</span>
        </div>
        <details v-if="Object.keys(preview.gap_reasons ?? {}).length">
          <summary>按原因查看缺口</summary>
          <ul>
            <li v-for="(count, reason) in preview.gap_reasons" :key="reason">
              {{ gapLabels[reason] ?? reason }}：{{ count }}
            </li>
          </ul>
        </details>
        <p class="hint">
          本次预检实际模型成本为 {{ display(preview.estimated_cost?.usd) }} USD；这是确定性组表的零成本，不抵消此前模型起草费用。
        </p>
        <p class="hint">
          输入标识 <code>{{ preview.input_hash }}</code> · 预计时长
          {{ preview.estimated_duration_ms == null ? "未知" : `${preview.estimated_duration_ms} ms` }}
        </p>
        <label class="inline"><input v-model="retry" type="checkbox" />显式重试已失败或取消的组表作业</label>
        <button class="primary" type="button" :disabled="submitting" @click="submitDraft">
          {{ submitting ? "提交中…" : "确认生成初稿" }}
        </button>
      </div>
    </section>

    <JobPanel
      v-if="draftJobId"
      :job-id="draftJobId"
      :writable="canWrite"
      @finished="onDraftFinished"
    />

    <section v-if="jobReady" aria-labelledby="history-title">
      <div class="section-heading">
        <h3 id="history-title">历史初稿</h3>
        <span class="hint">{{ drafts.length }} 份</span>
      </div>
      <div v-if="drafts.length" class="history-list">
        <button
          v-for="item in drafts"
          :key="item.id"
          type="button"
          :class="{ selected: selectedDraft?.id === item.id }"
          @click="showDraft(item.id)"
        >
          <span>
            <strong>{{ item.created_at ? new Date(item.created_at).toLocaleString("zh-CN") : "创建时间未知" }}</strong>
            <small><code>{{ item.id }}</code></small>
          </span>
          <span class="history-status">
            <span class="badge" :class="item.completion === 'complete' && item.validity === 'current' ? 'ok' : 'bad'">
              {{ item.completion === "complete" && item.validity === "stale" ? "历史快照完整" : statusLabels[item.completion] }}
            </span>
            <span class="badge" :class="item.validity === 'current' ? 'ok' : 'bad'">{{ statusLabels[item.validity] }}</span>
            <small>行 {{ item.summary.rows }} · 遵守 {{ item.summary.comply_only }} · 缺口 {{ item.summary.gaps }} · 负偏离 {{ item.summary.negative_deviations }}</small>
          </span>
        </button>
      </div>
      <p v-else class="notice">这个抽取 job 还没有初稿。先完成响应审阅，再运行组表预检。</p>
    </section>

    <p v-for="warning in resultWarnings" :key="warning" class="notice">{{ warning }}</p>
    <article v-if="selectedDraft" class="draft-detail">
      <header class="detail-heading">
        <div>
          <h3>初稿详情</h3>
          <p class="hint">status={{ selectedDraft.status }} · 输入 job {{ selectedDraft.extraction_job_id }}</p>
        </div>
        <div class="statuses">
          <span class="badge" :class="selectedDraft.completion === 'complete' && selectedDraft.validity === 'current' ? 'ok' : 'bad'">
            {{ selectedDraft.completion === "complete" && selectedDraft.validity === "stale" ? "历史快照完整（不可交付）" : statusLabels[selectedDraft.completion] }}
          </span>
          <span class="badge" :class="selectedDraft.validity === 'current' ? 'ok' : 'bad'">
            {{ statusLabels[selectedDraft.validity] }}
          </span>
        </div>
      </header>

      <div v-if="selectedDraft.validity === 'stale'" class="stale-warning" role="alert">
        <strong>这份历史快照已经失效。</strong>
        <span>原内容仍保留，但 {{ selectedDraft.invalidated_requirements.length }} 条要求已受当前卡片、材料或引用变化影响。请返回审阅后重新组表。</span>
        <details>
          <summary>查看受影响要求</summary>
          <ul>
            <li v-for="requirementId in selectedDraft.invalidated_requirements" :key="requirementId">
              <RouterLink :to="reviewHref(requirementId)"><code>{{ requirementId }}</code></RouterLink>
            </li>
          </ul>
        </details>
      </div>
      <div v-if="negativeRows.length" class="negative-warning">
        <strong>{{ negativeRows.length }} 条负偏离始终保留在响应表中。</strong>
        <span>负偏离不是缺口，请在对应表内逐条核对。</span>
      </div>

      <nav class="section-tabs" aria-label="初稿分区">
        <button
          v-for="section in sections"
          :key="section.id"
          type="button"
          :class="{ active: activeSection === section.id }"
          @click="activeSection = section.id"
        >
          {{ section.label }} <span>{{ section.rows.length }}</span>
        </button>
      </nav>

      <div class="list-toolbar">
        <label>
          筛选当前分区
          <input v-model="query" type="search" placeholder="原文、位置、响应、原因或要求 ID" />
        </label>
        <span class="hint">匹配 {{ filteredRows.length }} / {{ currentSection.rows.length }}</span>
      </div>

      <div class="table-wrap" tabindex="0" aria-label="初稿表格滚动区域">
        <table v-if="activeSection !== 'comply_only' && activeSection !== 'gaps'">
          <thead>
            <tr><th>要求与位置</th><th>响应</th><th>偏离</th><th>证据</th><th>操作</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in visibleRows" :key="row.requirement_id" :class="{ 'negative-row': row.deviation === 'negative' }">
              <td>
                <div class="row-meta"><span v-if="row.starred" class="star">★</span>{{ row.category }}</div>
                <blockquote>{{ row.tender_clause.quote }}</blockquote>
                <small>{{ sourceLabel(row) }}</small>
              </td>
              <td>
                <span class="badge">{{ row.response_kind === "commitment" ? "承诺" : "证据响应" }}</span>
                <p class="response-text">{{ row.response_text }}</p>
              </td>
              <td>
                <strong>{{ row.deviation }}</strong>
                <p>{{ row.deviation_note }}</p>
              </td>
              <td>
                <details v-if="row.evidence?.length">
                  <summary>{{ row.evidence.length }} 项已确认材料</summary>
                  <ul class="evidence-list">
                    <li v-for="evidence in row.evidence" :key="evidence.id">
                      <code>{{ evidence.id }}</code>
                      <span>{{ evidence.input?.kind }} · {{ evidence.input?.field_path ?? `来源 ${display(evidence.input?.evidence_source_id)}` }}</span>
                      <q>{{ evidence.input?.quote }}</q>
                    </li>
                  </ul>
                </details>
                <span v-else class="hint">承诺不附证据</span>
              </td>
              <td><RouterLink :to="reviewHref(row.requirement_id)">回到审阅</RouterLink></td>
            </tr>
          </tbody>
        </table>

        <table v-else-if="activeSection === 'comply_only'">
          <thead><tr><th>招标原文</th><th>位置</th><th>人工决定</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="row in visibleRows" :key="row.requirement_id">
              <td><blockquote>{{ row.tender_clause.quote }}</blockquote></td>
              <td>{{ sourceLabel(row) }}</td>
              <td>仅需遵守 · {{ new Date(row.disposition_at).toLocaleString("zh-CN") }}</td>
              <td><RouterLink :to="reviewHref(row.requirement_id)">回到审阅</RouterLink></td>
            </tr>
          </tbody>
        </table>

        <table v-else>
          <thead><tr><th>招标原文</th><th>位置</th><th>缺口原因</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="row in visibleRows" :key="row.requirement_id">
              <td><blockquote>{{ row.tender_clause.quote }}</blockquote></td>
              <td>{{ sourceLabel(row) }}</td>
              <td>
                <ul class="reason-list"><li v-for="reason in row.reasons" :key="reason">{{ gapLabels[reason] ?? reason }}</li></ul>
              </td>
              <td><RouterLink :to="reviewHref(row.requirement_id)">回到审阅</RouterLink></td>
            </tr>
          </tbody>
        </table>
      </div>

      <p v-if="!visibleRows.length" class="notice">当前筛选没有结果。</p>
      <div v-if="filteredRows.length > pageSize" class="pagination">
        <button type="button" :disabled="page <= 1" @click="page -= 1">上一页</button>
        <span>第 {{ page }} / {{ totalPages }} 页</span>
        <button type="button" :disabled="page >= totalPages" @click="page += 1">下一页</button>
      </div>
    </article>
  </div>
</template>

<style scoped>
.draft-page { min-width: 0; }
.page-heading, .section-heading, .detail-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; margin-bottom: 14px; }
h2, h3 { margin: 0 0 4px; }
.metric-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; margin: 12px 0; }
.metric-grid > div { background: var(--surface); border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; }
.metric-grid span, .table-counts { color: var(--muted); font-size: 12px; }
.metric-grid strong { font-size: 22px; }
.metric-grid .negative { background: #fff0ef; color: var(--danger); }
.table-counts { display: flex; gap: 16px; flex-wrap: wrap; }
.history-list { display: grid; gap: 8px; margin-bottom: 20px; }
.history-list > button { display: flex; justify-content: space-between; gap: 16px; padding: 10px 12px; text-align: left; }
.history-list > button.selected { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
.history-list button > span { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.history-list code { overflow-wrap: anywhere; }
.history-status { align-items: flex-end; }
.draft-detail { border-top: 1px solid var(--border); padding-top: 18px; }
.statuses { display: flex; gap: 6px; }
.stale-warning, .negative-warning { display: flex; flex-direction: column; gap: 3px; border-radius: 8px; padding: 12px 14px; margin-bottom: 10px; }
.stale-warning { background: #fff4dc; color: #765000; }
.negative-warning { background: #fff0ef; color: var(--danger); }
.section-tabs { display: flex; gap: 6px; overflow-x: auto; margin: 16px 0 12px; padding-bottom: 2px; }
.section-tabs button { white-space: nowrap; }
.section-tabs button.active { background: var(--accent); border-color: var(--accent); color: #fff; }
.section-tabs button span { opacity: .75; }
.list-toolbar { display: flex; align-items: end; justify-content: space-between; gap: 12px; margin-bottom: 10px; }
.list-toolbar label { flex: 1; max-width: 520px; }
.table-wrap { overflow-x: auto; }
.table-wrap table { min-width: 780px; }
.table-wrap blockquote { margin: 3px 0 7px; white-space: pre-wrap; }
.row-meta { color: var(--muted); font-size: 12px; }
.star { color: #b26a00; margin-right: 4px; }
.response-text, td p { white-space: pre-wrap; margin: 7px 0; }
.negative-row { background: #fff8f7; }
.negative-row td:nth-child(3) strong { color: var(--danger); }
.evidence-list, .reason-list { margin: 7px 0; padding-left: 18px; }
.evidence-list li { display: flex; flex-direction: column; gap: 3px; margin-bottom: 8px; }
.evidence-list q { white-space: pre-wrap; }
.pagination { display: flex; justify-content: center; align-items: center; gap: 10px; margin-top: 14px; }
@media (max-width: 720px) {
  .page-heading, .section-heading, .detail-heading, .list-toolbar { flex-direction: column; align-items: stretch; }
  .metric-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .history-list > button { flex-direction: column; }
  .history-status { align-items: flex-start; }
}
</style>
