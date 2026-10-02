<script setup>
import { computed, nextTick, onMounted, reactive, ref, shallowRef, watch } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from "vue-router";
import { orgSession } from "../api.js";
import { categories, display, domainFor, errorText, locationLabel, mine, orgAccess, orgRequest, recalled, remember, states } from "../org.js";
import CardEditor from "../components/CardEditor.vue";
import GenerationPanel from "../components/GenerationPanel.vue";
const route = useRoute(), router = useRouter(), taskId = route.params.taskId, jobId = String(route.query.job ?? "");
const task = ref(null), job = ref(null), docs = ref([]), rows = shallowRef(null), selectedRow = ref(null), dirty = ref(false), error = ref(""), notice = ref(""), warnings = ref([]);
const filters = reactive({ category: "", state: "", domain: "", disposition: "", starred: false, mine: false, gaps: false, query: "" });
const page = ref(1), pageSize = ref(50), selected = ref([]), batch = ref([]), batchDialog = ref(null), batchBusy = ref(false), batchStale = ref(false), batchError = ref(""), materialRevision = ref(0);
const title = ref(null), lastTrigger = ref(null);
const writable = computed(() => orgAccess.role && orgAccess.role !== "viewer");
const isGap = (row) => !["eligible", "comply_only"].includes(row.card?.eligibility);
const filtered = computed(() => (rows.value ?? []).filter(row => {
  const query = filters.query.toLocaleLowerCase();
  return (!filters.category || row.category === filters.category) && (!filters.state || row.status === filters.state) && (!filters.domain || (domainFor(row) ?? "unclassified") === filters.domain) && (!filters.disposition || (row.card?.disposition ?? "undecided") === filters.disposition) && (!filters.starred || row.starred) && (!filters.mine || (row.status === "pending_review" && mine(domainFor(row)))) && (!filters.gaps || isGap(row)) && (!query || `${row.text}\n${row.source.quote}`.toLocaleLowerCase().includes(query));
}));
const pages = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize.value)));
const visible = computed(() => filtered.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value));
const summary = computed(() => rows.value ? { total: rows.value.length, confirmed: rows.value.filter(r => r.card?.state === "confirmed").length, comply: rows.value.filter(r => r.card?.eligibility === "comply_only").length, gaps: rows.value.filter(isGap).length, negative: rows.value.filter(r => r.card?.content.deviation === "negative").length } : null);
function batchAllowed(row) { return mine(domainFor(row)) && !["pending_review", "confirmed"].includes(row.status); }
function discard() { return !dirty.value || !orgSession.get() || window.confirm("有未保存的响应编辑。离开将丢弃这些编辑，继续？"); }
onBeforeRouteLeave(discard);
onBeforeRouteUpdate((to) => (to.query.job !== jobId || to.params.taskId !== taskId || String(to.query.requirement ?? "") !== (selectedRow.value?.id ?? "")) ? discard() : true);
function storePosition() { remember(`review.${taskId}.${jobId}`, { requirementId: selectedRow.value?.id ?? null, page: page.value, pageSize: pageSize.value, category: filters.category, state: filters.state, domain: filters.domain, disposition: filters.disposition, starred: filters.starred, mine: filters.mine, gaps: filters.gaps }); }
function clearSelection() { if (selected.value.length) notice.value = "筛选或范围已改变，批次选择已清除。"; selected.value = []; }
watch(filters, () => { page.value = 1; clearSelection(); storePosition(); });
watch(pageSize, () => { page.value = 1; storePosition(); });
watch(page, storePosition);
watch(pages, value => { page.value = Math.min(page.value, value); });
async function loadRows() {
  const [requirements, slots] = await Promise.all([orgRequest("GET", `/tasks/${taskId}/requirements?job=${jobId}`), orgRequest("GET", `/tasks/${taskId}/cards?job=${jobId}`)]);
  if (slots.data.task_id !== taskId || slots.data.extraction_job_id !== jobId) throw new Error("响应卡范围与页面不匹配");
  const byId = new Map(slots.items.map(slot => [slot.requirement_id, slot]));
  if (byId.size !== slots.items.length || byId.size !== requirements.items.length || new Set(requirements.items.map(r => r.id)).size !== byId.size) throw new Error("要求与卡片集合不完整或重复，操作已停止");
  rows.value = requirements.items.map(row => {
    const slot = byId.get(row.id);
    if (!slot || row.job_id !== jobId || row.source.document_id !== job.value.document_id || slot.source.document_id !== row.source.document_id || slot.source.chunk_id !== row.source.chunk_id || slot.source.quote !== row.source.quote || (slot.card && (slot.card.task_id !== taskId || slot.card.extraction_job_id !== jobId || slot.card.requirement_id !== row.id))) throw new Error("要求、引文或卡片关联不一致，操作已停止");
    return { ...row, ...slot };
  });
  warnings.value = [...requirements.warnings, ...slots.warnings];
}
async function load() {
  error.value = "";
  try {
    if (!jobId) throw new Error("请从任务页选择一个成功的抽取 job");
    const [detail, history, documents] = await Promise.all([orgRequest("GET", `/tasks/${taskId}`), orgRequest("GET", `/tasks/${taskId}/extractions`), orgRequest("GET", `/tasks/${taskId}/documents`)]);
    task.value = detail.data; docs.value = documents.items; job.value = history.items.find(j => j.job_id === jobId && j.status === "succeeded");
    if (!job.value) throw new Error("所选抽取不可访问或尚未成功");
    await loadRows();
    const saved = recalled(`review.${taskId}.${jobId}`);
    if (saved) { for (const key of Object.keys(filters)) if (key !== "query" && key in saved) filters[key] = saved[key]; pageSize.value = [25,50,100].includes(saved.pageSize) ? saved.pageSize : 50; await nextTick(); page.value = Math.min(pages.value, Math.max(1, saved.page || 1)); }
    const id = String(route.query.requirement ?? saved?.requirementId ?? "");
    if (id) { const row = rows.value.find(r => r.id === id); if (row) await open(row); else error.value = "指定要求不属于当前集合"; }
  } catch (exc) { rows.value = null; error.value = errorText(exc); }
}
async function open(row, event) {
  if (!discard()) return;
  dirty.value = false; selectedRow.value = row; lastTrigger.value = event?.currentTarget ?? null;
  storePosition(); await router.replace({ query: { job: jobId, requirement: row.id } });
}
async function close() {
  if (!discard()) return; dirty.value = false; selectedRow.value = null; storePosition();
  await router.replace({ query: { job: jobId } }); await nextTick(); (lastTrigger.value?.isConnected ? lastTrigger.value : title.value)?.focus();
}
async function nextMine() {
  const start = rows.value.findIndex(r => r.id === selectedRow.value?.id);
  const ordered = [...rows.value.slice(start + 1), ...rows.value.slice(0, start + 1)];
  const row = ordered.find(r => r.id !== selectedRow.value?.id && r.status === "pending_review" && mine(domainFor(r)));
  if (row) await open(row); else notice.value = "所选集合中没有下一条待我审阅。";
}
function updateCard(card) {
  rows.value = rows.value.map(row => row.id === card.requirement_id ? { ...row, source: card.source, card, status: card.state } : row);
  // Keep the detail instance alive; it owns its freshly read revision.
  dirty.value = false; materialRevision.value++;
}
function selectForPreview(all = false) {
  const candidates = all ? filtered.value : visible.value;
  if (candidates.length > 1000) { error.value = "选中要求超过 1,000 项，请缩小筛选范围"; return; }
  if (!all || window.confirm(`固定全部匹配的 ${candidates.length} 个要求 ID 用于起草预检？受保护修订由后端报告跳过。`)) selected.value = candidates.map(row => row.id);
}
function selectPage() { selected.value = visible.value.filter(batchAllowed).map(r => r.id); notice.value = `已选择本页职责范围内 ${selected.value.length} 项。`; }
function selectAll() {
  const candidates = filtered.value.filter(batchAllowed);
  if (candidates.length > 1000) { error.value = "匹配项超过单批 1,000 项上限，请缩小筛选范围"; return; }
  if (window.confirm(`全部匹配中职责可处理 ${candidates.length} 项；待审、已确认和其他职责不纳入。固定这些 ID 进入处置批次？`)) selected.value = candidates.map(r => r.id);
}
async function prepareBatch() {
  batchError.value = ""; batchStale.value = false;
  const ids = new Set(selected.value);
  if (!ids.size || ids.size > 1000 || ids.size !== selected.value.length) { error.value = "批次须有 1 至 1,000 个不重复的要求 ID"; return; }
  const candidates = rows.value.filter(r => ids.has(r.id));
  if (candidates.length !== ids.size || candidates.some(r => !batchAllowed(r))) { error.value = "所选条目包含不可处置范围"; return; }
  batch.value = candidates.map(row => ({ row, expected_revision: row.card?.revision ?? null, disposition: "comply_only", reason: "" }));
  await nextTick(); batchDialog.value.showModal();
}
async function submitBatch() {
  if (batchStale.value || batch.value.some(item => !item.reason.trim())) return;
  batchBusy.value = true; batchError.value = "";
  try {
    await orgRequest("POST", `/tasks/${taskId}/cards/dispositions`, { extraction_job_id: jobId, items: batch.value.map(item => ({ requirement_id: item.row.id, expected_revision: item.expected_revision, disposition: item.disposition, reason: item.reason })) });
    selected.value = []; batchDialog.value.close(); await loadRows();
    if (selectedRow.value && batch.value.some(item => item.row.id === selectedRow.value.id)) { selectedRow.value = null; dirty.value = false; }
    materialRevision.value++;
    // Announce success only once the reloaded list and revision are in place.
    notice.value = "整批处置已保存；仅需遵守不会确认证据或响应。";
    window.dispatchEvent(new CustomEvent("bid:task-cards-changed", { detail: { taskId } }));
  } catch (exc) {
    batchError.value = `${errorText(exc)}。整批未写入，已保留选择与原因；请重新读取并核对全部条目。`;
    if (exc.status === 409 || exc.status === 403 || exc.status === 404) batchStale.value = true;
  } finally { batchBusy.value = false; }
}
async function reloadBatch() {
  try { await loadRows(); batchStale.value = false; batchError.value = "已重新读取。关闭本对话框后重新准备批次，逐项核对新的修订和原因。"; batchStale.value = true; }
  catch (exc) { batchError.value = errorText(exc); }
}
async function materialsChanged() {
  materialRevision.value++; clearSelection();
  window.dispatchEvent(new CustomEvent("bid:task-materials-changed", { detail: { taskId } }));
  try { await loadRows(); } catch (exc) { error.value = errorText(exc); }
}
async function taskChanged() { try { task.value = (await orgRequest("GET", `/tasks/${taskId}`)).data; } catch (exc) { error.value = errorText(exc); } }
watch(() => route.query.requirement, async (id) => {
  if (!rows.value || String(id ?? "") === (selectedRow.value?.id ?? "")) return;
  dirty.value = false;
  if (!id) { selectedRow.value = null; storePosition(); return; }
  const row = rows.value.find(item => item.id === String(id));
  if (!row) { selectedRow.value = null; error.value = "指定要求不属于当前集合"; return; }
  selectedRow.value = row; storePosition();
});
onMounted(load);
</script>
<template>
  <RouterLink :to="`/org/tasks/${taskId}`">返回任务</RouterLink> · <RouterLink :to="`/org/tasks/${taskId}/drafts?job=${jobId}`">三表与缺口</RouterLink>
  <h2 ref="title" tabindex="-1">要求与响应审阅</h2><p>任务 {{ task?.name }} · 固定抽取 job <code>{{ jobId }}</code></p>
  <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p><p v-for="warning in warnings" :key="warning" class="notice">{{ warning }}</p>
  <p v-if="!rows" role="status">要求全集尚未就绪，统计与操作不可用。</p>
  <template v-else>
    <p v-if="summary" class="notice">所选集合 {{ summary.total }} 项 · 已确认 {{ summary.confirmed }} · 有效仅需遵守 {{ summary.comply }} · 缺口 {{ summary.gaps }} · <strong>负偏离 {{ summary.negative }}</strong>。不表示招标文件没有漏抽或完整标书已交付。</p>
    <details v-if="writable"><summary>模型起草费用预览</summary><GenerationPanel :task="task" :job="job" :requirement-ids="selected.length ? selected : undefined" :role="orgAccess.role" :material-revision="materialRevision" @changed="taskChanged" /></details>
    <div class="review-layout">
      <section aria-label="要求列表">
        <div class="filters panel">
          <label>搜索要求或引文<input v-model="filters.query" maxlength="200" type="search" /></label>
          <label>类别<select v-model="filters.category"><option value="">全部</option><option v-for="(label, key) in categories" :key="key" :value="key">{{ label }}</option></select></label>
          <label>状态<select v-model="filters.state"><option value="">全部</option><option v-for="(label, key) in states" :key="key" :value="key">{{ label }}</option></select></label>
          <label>职责<select v-model="filters.domain"><option value="">全部</option><option value="commercial">商务 / 资格</option><option value="technical">技术</option><option value="unclassified">待分类</option></select></label>
          <label>处置<select v-model="filters.disposition"><option value="">全部</option><option value="respond">逐项响应</option><option value="comply_only">仅需遵守</option><option value="undecided">尚未决定</option></select></label>
          <label class="inline"><input v-model="filters.starred" type="checkbox" />只看星标</label><label class="inline"><input v-model="filters.mine" type="checkbox" />待我审阅</label><label class="inline"><input v-model="filters.gaps" type="checkbox" />只看缺口</label>
        </div>
        <p data-testid="requirement-count" aria-live="polite">匹配 {{ filtered.length }} / 全集 {{ rows.length }}</p>
        <div class="actions"><label>每页条数<select v-model.number="pageSize"><option :value="25">25</option><option :value="50">50</option><option :value="100">100</option></select></label><button :disabled="page <= 1" @click="page--">上一页</button><span aria-live="polite">第 {{ page }} / {{ pages }} 页</span><button :disabled="page >= pages" @click="page++">下一页</button></div>
        <div v-if="writable" class="actions"><button @click="selectForPreview()">选择本页用于起草预检</button><button @click="selectForPreview(true)">选择全部匹配用于起草预检</button><button @click="clearSelection">清除选择</button></div>
        <div v-if="['technical', 'bidder'].includes(orgAccess.role)" class="actions"><button @click="selectPage">选择本页可处置项</button><button @click="selectAll">选择全部匹配可处置项</button><span>已选 {{ selected.length }} 项</span><button :disabled="!selected.length || dirty" @click="prepareBatch">准备批量处置</button></div>
        <div class="table-scroll" tabindex="0"><table><caption>原文顺序；缺卡片也计入全集</caption><thead><tr><th>选择</th><th>类别 / 状态</th><th>要求、逐字引文与位置</th><th>操作</th></tr></thead><tbody>
          <tr v-for="row in visible" :key="row.id" data-testid="requirement-row"><td><input v-if="writable" v-model="selected" :value="row.id" type="checkbox" :aria-label="`选择要求 ${row.text}`" /></td><td>{{ categories[row.category] }}{{ row.starred ? ' · ★ 星标' : '' }}<br />{{ states[row.status] }}<br />{{ domainFor(row) ?? '待单位管理员分类' }}<br />{{ row.card?.eligibility ?? 'missing_card' }}<p v-if="row.card?.content.deviation === 'negative'" class="error">负偏离</p></td><td>{{ row.text }}<blockquote class="quote">{{ row.source.quote }}</blockquote><small>{{ locationLabel(row.source, docs.find(d => d.id === row.source.document_id)?.name) }}</small></td><td><button :aria-label="`打开审阅：${row.text}`" @click="open(row, $event)">打开审阅</button></td></tr>
        </tbody></table></div>
      </section>
      <CardEditor v-if="selectedRow" :key="selectedRow.id" :row="selectedRow" :task-id="taskId" :job-id="jobId" :document-name="docs.find(d => d.id === selectedRow.source.document_id)?.name" @updated="updateCard" @dirty="dirty = $event" @next="nextMine" @close="close" @materials="materialsChanged" />
      <aside v-else class="panel">从要求列表打开一条要求。可用 Tab、Enter 和空格完成筛选、翻页、编辑与逐项核对。</aside>
    </div>
  </template>
  <dialog ref="batchDialog" aria-label="批量处置预检"><h3>逐项处置（整批原子提交）</h3><p>固定 {{ batch.length }} 个要求 ID；单批最多 1,000 项。不会批量确认响应或 Evidence。</p>
    <form @submit.prevent="submitBatch"><div v-for="(item, index) in batch" :key="item.row.id" class="panel"><h4>{{ index + 1 }} · {{ item.row.text }}</h4><p>职责 {{ domainFor(item.row) }} · 预期修订 {{ display(item.expected_revision) }}</p><blockquote class="quote">{{ item.row.source.quote }}</blockquote><label>处置 {{ index + 1 }}<select v-model="item.disposition"><option value="comply_only">仅需遵守</option><option value="respond">恢复逐项响应</option></select></label><label>逐项理由 {{ index + 1 }}<textarea v-model="item.reason" required maxlength="10000" /></label></div>
      <p v-if="batchError" role="alert" class="error">{{ batchError }}</p><button type="button" @click="batchDialog.close()">关闭，保留选择</button><button v-if="batchStale" type="button" @click="reloadBatch">重新读取受影响范围</button><button class="primary" :disabled="batchBusy || batchStale">提交整批处置</button>
    </form>
  </dialog>
</template>
