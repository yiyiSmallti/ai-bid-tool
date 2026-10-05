<script setup>
import { Back, Document, Search } from "@element-plus/icons-vue";
import { computed, nextTick, onMounted, reactive, ref, shallowRef, watch } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from "vue-router";
import { orgSession } from "../api.js";
import { categories, confirmAction, display, domainFor, domains, eligibilities, errorText, label, locationLabel, mine, orgAccess, orgRequest, recalled, remember, states, statusTag, warningText } from "../org.js";
import CardEditor from "../components/CardEditor.vue";
import GenerationPanel from "../components/GenerationPanel.vue";
const route = useRoute(), router = useRouter(), taskId = route.params.taskId, jobId = String(route.query.job ?? "");
const task = ref(null), job = ref(null), docs = ref([]), rows = shallowRef(null), selectedRow = ref(null), dirty = ref(false), error = ref(""), notice = ref(""), warnings = ref([]);
const filters = reactive({ category: "", state: "", domain: "", disposition: "", starred: false, mine: false, gaps: false, query: "" });
const page = ref(1), pageSize = ref(50), selected = ref([]), batch = ref([]), batchOpen = ref(false), batchBusy = ref(false), batchStale = ref(false), batchError = ref(""), materialRevision = ref(0);
const title = ref(null), lastTrigger = ref(null), generationOpen = ref([]);
const writable = computed(() => orgAccess.role && orgAccess.role !== "viewer");
const isGap = (row) => !["eligible", "comply_only"].includes(row.card?.eligibility);
const filtered = computed(() => (rows.value ?? []).filter(row => {
  const query = filters.query.toLocaleLowerCase();
  return (!filters.category || row.category === filters.category) && (!filters.state || row.status === filters.state) && (!filters.domain || (domainFor(row) ?? "unclassified") === filters.domain) && (!filters.disposition || (row.card?.disposition ?? "undecided") === filters.disposition) && (!filters.starred || row.starred) && (!filters.mine || (row.status === "pending_review" && mine(domainFor(row)))) && (!filters.gaps || isGap(row)) && (!query || `${row.text}\n${row.source.quote}`.toLocaleLowerCase().includes(query));
}));
const pages = computed(() => Math.max(1, Math.ceil(filtered.value.length / pageSize.value)));
const visible = computed(() => filtered.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value));
const summary = computed(() => rows.value ? { total: rows.value.length, confirmed: rows.value.filter(r => r.card?.state === "confirmed").length, comply: rows.value.filter(r => r.card?.eligibility === "comply_only").length, gaps: rows.value.filter(isGap).length, negative: rows.value.filter(r => r.card?.content.deviation === "negative").length } : null);
const documentName = (id) => docs.value.find(d => d.id === id)?.name;
function batchAllowed(row) { return mine(domainFor(row)) && !["pending_review", "confirmed"].includes(row.status); }
async function discard() { return !dirty.value || !orgSession.get() || await confirmAction("有未保存的响应编辑。离开将丢弃这些编辑，继续？", "放弃未保存的编辑", "放弃编辑", true); }
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
    if (route.query.category === 'scoring') filters.category = 'scoring';
    const id = String(route.query.requirement ?? saved?.requirementId ?? "");
    if (id) { const row = rows.value.find(r => r.id === id); if (row) await open(row); else error.value = "指定要求不属于当前集合"; }
  } catch (exc) { rows.value = null; error.value = errorText(exc); }
}
async function open(row, event) {
  const trigger = event?.currentTarget ?? null;
  if (!(await discard())) return;
  dirty.value = false; selectedRow.value = row; lastTrigger.value = trigger;
  storePosition(); await router.replace({ query: { job: jobId, requirement: row.id } });
}
async function close() {
  if (!(await discard())) return; dirty.value = false; selectedRow.value = null; storePosition();
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
async function selectForPreview(all = false) {
  const candidates = all ? filtered.value : visible.value;
  if (candidates.length > 1000) { error.value = "选中要求超过 1,000 项，请缩小筛选范围"; return; }
  if (!all || await confirmAction(`固定全部匹配的 ${candidates.length} 个要求 ID 用于起草预检？受保护修订由后端报告跳过。`)) selected.value = candidates.map(row => row.id);
}
function selectPage() { selected.value = visible.value.filter(batchAllowed).map(r => r.id); notice.value = `已选择本页职责范围内 ${selected.value.length} 项。`; }
async function selectAll() {
  const candidates = filtered.value.filter(batchAllowed);
  if (candidates.length > 1000) { error.value = "匹配项超过单批 1,000 项上限，请缩小筛选范围"; return; }
  if (await confirmAction(`全部匹配中职责可处理 ${candidates.length} 项；待审、已确认和其他职责不纳入。固定这些 ID 进入处置批次？`)) selected.value = candidates.map(r => r.id);
}
function prepareBatch() {
  batchError.value = ""; batchStale.value = false;
  const ids = new Set(selected.value);
  if (!ids.size || ids.size > 1000 || ids.size !== selected.value.length) { error.value = "批次须有 1 至 1,000 个不重复的要求 ID"; return; }
  const candidates = rows.value.filter(r => ids.has(r.id));
  if (candidates.length !== ids.size || candidates.some(r => !batchAllowed(r))) { error.value = "所选条目包含不可处置范围"; return; }
  batch.value = candidates.map(row => ({ row, expected_revision: row.card?.revision ?? null, disposition: "comply_only", reason: "" }));
  batchOpen.value = true;
}
async function submitBatch() {
  if (batchStale.value || batch.value.some(item => !item.reason.trim())) { if (!batchStale.value) batchError.value = "每一项都需要填写逐项理由。"; return; }
  batchBusy.value = true; batchError.value = "";
  try {
    await orgRequest("POST", `/tasks/${taskId}/cards/dispositions`, { extraction_job_id: jobId, items: batch.value.map(item => ({ requirement_id: item.row.id, expected_revision: item.expected_revision, disposition: item.disposition, reason: item.reason })) });
    selected.value = []; batchOpen.value = false; await loadRows();
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
  <nav class="breadcrumb" aria-label="位置"><RouterLink to="/org/tasks">招标任务</RouterLink><span>/</span><RouterLink :to="`/org/tasks/${taskId}`">{{ task?.name ?? "任务" }}</RouterLink><span>/</span><span>要求与响应审阅</span></nav>
  <div class="page-header">
    <div><h2 ref="title" tabindex="-1">要求与响应审阅</h2><p class="subtitle">任务 {{ task?.name }} · 固定抽取 <code>{{ jobId }}</code></p></div>
    <div class="actions"><el-button :icon="Back" @click="router.push(`/org/tasks/${taskId}`)">返回任务</el-button><el-button type="primary" plain :icon="Document" @click="router.push(`/org/tasks/${taskId}/drafts?job=${jobId}`)">三表与缺口</el-button><RouterLink :to="`/org/tasks/${taskId}/checks?job=${jobId}`">检查风险</RouterLink><RouterLink :to="`/org/tasks/${taskId}/scores?job=${jobId}`">评分预估</RouterLink></div>
  </div>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
  <p v-if="notice" class="notice" role="status">{{ notice }}</p>
  <p v-for="warning in warnings" :key="warning" class="notice warning">{{ warningText(warning) }}</p>
  <el-skeleton v-if="!rows && !error" :rows="6" animated />
  <p v-if="!rows" class="hint">要求全集尚未就绪，统计与操作不可用。</p>
  <template v-else>
    <div v-if="summary" class="stat-strip">
      <div class="strip-item"><span class="label">所选集合</span><strong>{{ summary.total }}</strong></div>
      <div class="strip-item success"><span class="label">已确认</span><strong>{{ summary.confirmed }}</strong></div>
      <div class="strip-item"><span class="label">有效仅需遵守</span><strong>{{ summary.comply }}</strong></div>
      <div class="strip-item warning"><span class="label">缺口</span><strong>{{ summary.gaps }}</strong></div>
      <div class="strip-item danger"><span class="label">负偏离</span><strong>{{ summary.negative }}</strong></div>
      <span class="hint strip-hint">统计只覆盖所选抽取集合，不表示招标文件没有漏抽或完整标书已交付。</span>
    </div>
    <el-collapse v-if="writable" v-model="generationOpen" class="section generation"><el-collapse-item name="generation" title="模型起草费用预览"><GenerationPanel :task="task" :job="job" :requirement-ids="selected.length ? selected : undefined" :role="orgAccess.role" :material-revision="materialRevision" @changed="taskChanged" /></el-collapse-item></el-collapse>
    <el-card shadow="never" class="section filters-card">
      <el-form label-position="top" class="filters" @submit.prevent>
        <el-form-item label="搜索要求或引文" class="filter-search"><el-input v-model="filters.query" maxlength="200" type="search" clearable :prefix-icon="Search" placeholder="要求文字或招标原文" /></el-form-item>
        <el-form-item label="类别"><el-select v-model="filters.category" placeholder="全部"><el-option value="" label="全部" /><el-option v-for="(text, key) in categories" :key="key" :value="key" :label="text" /></el-select></el-form-item>
        <el-form-item label="状态"><el-select v-model="filters.state" placeholder="全部"><el-option value="" label="全部" /><el-option v-for="(text, key) in states" :key="key" :value="key" :label="text" /></el-select></el-form-item>
        <el-form-item label="职责"><el-select v-model="filters.domain" placeholder="全部"><el-option value="" label="全部" /><el-option value="commercial" label="商务 / 资格" /><el-option value="technical" label="技术" /><el-option value="unclassified" label="待分类" /></el-select></el-form-item>
        <el-form-item label="处置"><el-select v-model="filters.disposition" placeholder="全部"><el-option value="" label="全部" /><el-option value="respond" label="逐项响应" /><el-option value="comply_only" label="仅需遵守" /><el-option value="undecided" label="尚未决定" /></el-select></el-form-item>
        <div class="checks"><label class="check"><input v-model="filters.starred" type="checkbox" />只看星标</label><label class="check"><input v-model="filters.mine" type="checkbox" />待我审阅</label><label class="check"><input v-model="filters.gaps" type="checkbox" />只看缺口</label></div>
      </el-form>
    </el-card>
    <div class="review-layout" :class="{ detail: selectedRow }">
      <section aria-label="要求列表" class="review-list">
        <div class="list-bar">
          <span class="count" data-testid="requirement-count" aria-live="polite">匹配 {{ filtered.length }} / 全集 {{ rows.length }}</span>
          <span v-if="writable" class="hint">已选 {{ selected.length }} 项</span>
        </div>
        <div v-if="writable" class="tool-groups">
          <div class="tool-group"><span class="group-label">起草预检范围</span><el-button size="small" @click="selectForPreview()">选择本页用于起草预检</el-button><el-button size="small" @click="selectForPreview(true)">选择全部匹配用于起草预检</el-button><el-button size="small" @click="clearSelection">清除选择</el-button></div>
          <div v-if="['technical', 'bidder'].includes(orgAccess.role)" class="tool-group"><span class="group-label">批量处置</span><el-button size="small" @click="selectPage">选择本页可处置项</el-button><el-button size="small" @click="selectAll">选择全部匹配可处置项</el-button><el-button size="small" type="primary" :disabled="!selected.length || dirty" @click="prepareBatch">准备批量处置</el-button></div>
        </div>
        <div class="table-scroll" tabindex="0"><table class="data-table review-table"><caption class="sr-only">原文顺序；缺卡片也计入全集</caption>
          <thead><tr><th class="col-check"><span class="sr-only">选择</span></th><th class="col-meta">类别 / 状态</th><th>要求、逐字引文与位置</th><th class="col-open">操作</th></tr></thead>
          <tbody>
            <tr v-for="row in visible" :key="row.id" data-testid="requirement-row" :class="{ selected: selectedRow?.id === row.id }">
              <td><input v-if="writable" v-model="selected" :value="row.id" type="checkbox" :aria-label="`选择要求 ${row.text}`" /></td>
              <td class="meta">
                <div class="tags"><span class="tag" :class="statusTag[row.status]">{{ states[row.status] }}</span><span v-if="row.starred" class="tag star">★ 星标</span><span v-if="row.card?.content.deviation === 'negative'" class="tag danger">负偏离</span></div>
                <div class="meta-line">{{ categories[row.category] }} · {{ label(domains, domainFor(row), "待分类") }}</div>
                <div class="meta-line">{{ label(eligibilities, row.card?.eligibility ?? "missing_card") }}</div>
              </td>
              <td><div class="req-text">{{ row.text }}</div><blockquote class="quote">{{ row.source.quote }}</blockquote><small class="hint">{{ locationLabel(row.source, documentName(row.source.document_id)) }}</small></td>
              <td><el-button size="small" :type="selectedRow?.id === row.id ? 'primary' : 'default'" :aria-label="`打开审阅：${row.text}`" @click="open(row, $event)">打开审阅</el-button></td>
            </tr>
            <tr v-if="!visible.length"><td colspan="4" class="empty">当前筛选没有结果</td></tr>
          </tbody>
        </table></div>
        <el-pagination v-model:current-page="page" v-model:page-size="pageSize" class="pager" background layout="total, sizes, prev, pager, next" :page-sizes="[25, 50, 100]" :total="filtered.length" />
      </section>
      <CardEditor v-if="selectedRow" :key="selectedRow.id" :row="selectedRow" :task-id="taskId" :job-id="jobId" :document-name="documentName(selectedRow.source.document_id)" class="review-detail" @updated="updateCard" @dirty="dirty = $event" @next="nextMine" @close="close" @materials="materialsChanged" />
      <aside v-else class="review-empty"><el-empty description="从左侧列表打开一条要求进行审阅" :image-size="80"><p class="hint">可用 Tab、Enter 和空格完成筛选、翻页、编辑与逐项核对。</p></el-empty></aside>
    </div>
  </template>
  <el-dialog v-model="batchOpen" title="批量处置预检" width="760px" :close-on-click-modal="false">
    <p class="hint">逐项处置，整批原子提交。固定 {{ batch.length }} 个要求 ID；单批最多 1,000 项。不会批量确认响应或 Evidence。</p>
    <el-form label-position="top" @submit.prevent="submitBatch">
      <el-card v-for="(item, index) in batch" :key="item.row.id" shadow="never" class="section batch-item">
        <h4>{{ index + 1 }} · {{ item.row.text }}</h4>
        <p class="hint">职责 {{ label(domains, domainFor(item.row), "待分类") }} · 预期修订 {{ display(item.expected_revision) }}</p>
        <blockquote class="quote">{{ item.row.source.quote }}</blockquote>
        <div class="grid">
          <el-form-item :label="`处置 ${index + 1}`"><el-select v-model="item.disposition"><el-option value="comply_only" label="仅需遵守" /><el-option value="respond" label="恢复逐项响应" /></el-select></el-form-item>
        </div>
        <el-form-item :label="`逐项理由 ${index + 1}`" required><el-input v-model="item.reason" type="textarea" :rows="2" maxlength="10000" /></el-form-item>
      </el-card>
      <el-alert v-if="batchError" :title="batchError" type="error" show-icon :closable="false" role="alert" class="section" />
      <div class="actions dialog-actions"><el-button @click="batchOpen = false">关闭，保留选择</el-button><el-button v-if="batchStale" @click="reloadBatch">重新读取受影响范围</el-button><el-button type="primary" native-type="submit" :loading="batchBusy" :disabled="batchStale">提交整批处置</el-button></div>
    </el-form>
  </el-dialog>
</template>
<style scoped>
.stat-strip { display: flex; align-items: stretch; gap: 0; margin-bottom: 16px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; flex-wrap: wrap; }
.strip-item { display: flex; flex-direction: column; gap: 2px; padding: 12px 24px; border-right: 1px solid var(--border); min-width: 120px; }
.strip-item .label { color: var(--muted); font-size: 13px; }
.strip-item strong { font-size: 22px; line-height: 1.2; }
.strip-item.success strong { color: var(--success); }
.strip-item.warning strong { color: var(--el-color-warning); }
.strip-item.danger strong { color: var(--danger); }
.strip-hint { align-self: center; padding: 8px 20px; flex: 1 1 260px; }
.generation { border: 1px solid var(--border); border-radius: 8px; background: var(--surface); padding: 0 16px; }
.review-layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; align-items: start; }
.review-layout.detail, .review-layout:has(.review-empty) { grid-template-columns: minmax(440px, 3fr) minmax(400px, 2fr); }
.review-list { min-width: 0; }
.review-detail { position: sticky; top: 76px; max-height: calc(100vh - 92px); overflow: auto; }
.review-empty { position: sticky; top: 76px; background: var(--surface); border: 1px dashed var(--border); border-radius: 8px; }
.filters { display: flex; flex-wrap: wrap; gap: 0 16px; align-items: flex-end; }
.filters .el-form-item { margin-bottom: 12px; }
.filters .filter-search { flex: 1 1 320px; }
.filters .el-select { width: 150px; }
.checks { display: flex; gap: 18px; flex-wrap: wrap; height: 32px; align-items: center; margin-bottom: 12px; }
.filters-card :deep(.el-card__body) { padding: 14px 20px 2px; }
.list-bar { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 8px; }
.list-bar .count { font-weight: 600; }
.tool-groups { display: flex; flex-wrap: wrap; gap: 8px 16px; margin-bottom: 10px; }
.tool-group { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; padding: 6px 10px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; }
.tool-group .el-button + .el-button { margin-left: 0; }
.group-label { font-size: 13px; color: var(--muted); margin-right: 4px; }
.review-table .col-check { width: 36px; }
.review-table .col-meta { width: 170px; }
.review-table .meta .tags { margin-bottom: 6px; }
.meta-line { font-size: 13px; color: var(--muted); line-height: 1.6; }
.review-table .col-open { width: 96px; }
.req-text { font-weight: 500; }
.pager { margin-top: 12px; justify-content: flex-end; flex-wrap: wrap; row-gap: 8px; }
.batch-item h4 { margin-top: 0; }
.dialog-actions { justify-content: flex-end; }
@media (max-width: 640px) {
  .review-table thead { display: none; }
  .review-table tr { display: grid; grid-template-columns: 28px minmax(0, 1fr); border-bottom: 1px solid var(--border); padding: 8px 0; }
  .review-table td { border: none; padding: 4px 8px; }
  .review-table td:nth-child(n+3) { grid-column: 2; }
  .table-scroll:has(.review-table) { overflow: visible; }
}
@media (max-width: 1100px) { .review-layout.detail, .review-layout:has(.review-empty) { grid-template-columns: minmax(0, 1fr); } .review-detail, .review-empty { position: static; max-height: none; } }
</style>
