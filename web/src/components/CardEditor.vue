<script setup>
import { ArrowRight, Close, Download, Refresh, Tickets, View } from "@element-plus/icons-vue";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { actorKinds, confirmAction, simulatedSelections, deviations, display, dispositions, domains, downloadOriginal, eligibilities, errorText, formatTime, label, locationLabel, materialKinds, mine, orgAccess, orgRequest, quoteChecks, responseKinds, states, statusTag } from "../org.js";
import DocumentPreview from "./DocumentPreview.vue";
import MaterialPanel from "./MaterialPanel.vue";
import SecretTextEditor from "./SecretTextEditor.vue";
import SourcePreview from "./SourcePreview.vue";
const props = defineProps({ row: Object, taskId: String, jobId: String, documentName: String });
const emit = defineEmits(["updated", "dirty", "next", "close", "materials"]);
const card = ref(null), content = ref(emptyContent()), baseline = ref(""), ready = ref(false), busy = ref(false), error = ref(""), notice = ref("");
const reviewed = ref([]), warnings = ref([]), reason = ref(""), domain = ref("technical"), history = ref(null), sourceChunk = ref(null);
const conflict = ref(null), conflictOpen = ref(false), heading = ref(null), kindKey = ref(0), marks = ref(new Set());
simulatedSelections(props.taskId).then((value) => { marks.value = value; }).catch(() => {});
// Confidential fields the response may name; the export fills their values.
const secretFields = ref([]);
orgRequest("GET", "/confidential-fields").then((result) => { secretFields.value = result.items; }).catch(() => {});
const masked = (value) => /\[REDACTED_[A-Z_]+\]/.test(value ?? "");
let active = true;
const dirty = computed(() => ready.value && JSON.stringify(content.value) !== baseline.value);
const displayedSource = computed(() => card.value?.source ?? props.row.source);
const writable = computed(() => orgAccess.role && orgAccess.role !== "viewer");
const editable = computed(() => ready.value && writable.value && !conflict.value && (!card.value || (["draft", "rejected", "needs_material"].includes(card.value.state) && card.value.disposition !== "comply_only")));
const canDecide = computed(() => card.value && mine(card.value.review_domain));
const invalid = computed(() => ["stale_material", "invalid_citation", "needs_reconfirmation"].includes(card.value?.eligibility));
const completeResponse = computed(() => card.value && ["response_kind", "response_text", "deviation", "deviation_note"].every(key => typeof card.value.content[key] === "string" && card.value.content[key].trim()) && card.value.content.deviation_note.trim() !== "满足");
// The first unmet confirmation condition, shown next to the disabled button.
const confirmBlocker = computed(() => {
  if (!card.value) return "";
  if (dirty.value) return "有未保存的编辑，请先保存并重新提交审阅";
  if (conflict.value) return "修订已变化，请先处理修订冲突";
  if (invalid.value) return `${label(eligibilities, card.value.eligibility)}，不能确认`;
  if (!completeResponse.value) return "响应种类、正文、偏离和说明都需要填写，说明不能只写“满足”";
  if (masked(card.value.content.response_text) || masked(card.value.content.deviation_note)) return "正文含 [REDACTED_…] 遮挡占位，请改用保密字段或写出原文";
  if (card.value.content.response_kind === "evidence" && !card.value.evidence.length) return "证据响应至少需要关联一项材料；没有材料时请标记“需补材料”或驳回";
  if (reviewed.value.length !== card.value.evidence.length) return `请逐项勾选已核对的材料（${reviewed.value.length} / ${card.value.evidence.length}）`;
  if (warnings.value.length !== card.value.warning_codes.length) return `请逐项勾选警示（${warnings.value.length} / ${card.value.warning_codes.length}）`;
  if (warnings.value.length && !reason.value.trim()) return "有警示时需要填写处理理由";
  return "";
});
const confirmReady = computed(() => !confirmBlocker.value);
function emptyContent() { return { response_kind: "commitment", response_text: "", deviation: "none", deviation_note: "", evidence: [] }; }
function clearReview() { reviewed.value = []; warnings.value = []; }
function install(value) {
  card.value = value; content.value = value ? JSON.parse(JSON.stringify(value.content)) : emptyContent();
  for (const key of ["response_text", "deviation_note"]) content.value[key] ??= "";
  content.value.response_kind ??= "commitment"; content.value.deviation ??= "none";
  baseline.value = JSON.stringify(content.value); sourceChunk.value = null; clearReview(); reason.value = ""; history.value = null; ready.value = true; emit("dirty", false);
}
function edit() { emit("dirty", dirty.value); clearReview(); }
async function kindChange(value) {
  if (value === content.value.response_kind) return;
  if (value === "commitment" && content.value.evidence.length && !(await confirmAction(`改为承诺将移除 ${content.value.evidence.length} 项候选材料。继续？`, "改为承诺", "确定", true))) { kindKey.value++; return; }
  content.value.response_kind = value;
  if (value === "commitment") content.value.evidence = [];
  edit();
}
function add(input) {
  if (!editable.value) return;
  if (content.value.response_kind !== "evidence") { error.value = "请先将响应种类改为证据响应，承诺不能关联材料"; return; }
  if (content.value.evidence.length >= 100) { error.value = "一张卡片最多关联 100 项材料"; return; }
  content.value.evidence.push(input); edit();
}
function verifyCard(value) {
  if (value.task_id !== props.taskId || value.extraction_job_id !== props.jobId || value.requirement_id !== props.row.id) throw new Error("卡片与当前任务、抽取或要求不匹配");
  return value;
}
function showConflict(fresh) {
  conflict.value = verifyCard(fresh); clearReview(); error.value = "修订或材料已改变。未保存编辑仍在本页，须对照后重新处理；不会自动合并或重发。";
  if (active) conflictOpen.value = true;
}
// A manual check always reports; an automatic one only when it cleared review ticks.
async function refresh(manual = false) {
  if (!card.value) return;
  const hadTicks = reviewed.value.length + warnings.value.length > 0;
  try {
    const fresh = verifyCard((await orgRequest("GET", `/cards/${card.value.id}`)).data);
    if (!active) return;
    if (fresh.revision !== card.value.revision || fresh.eligibility !== card.value.eligibility || JSON.stringify(fresh.warning_codes) !== JSON.stringify(card.value.warning_codes)) showConflict(fresh);
    else { card.value = fresh; clearReview(); if (manual || hadTicks) notice.value = "已重新核对当前修订；请重新勾选本次审阅项。"; }
  } catch (exc) { error.value = errorText(exc); clearReview(); }
}
async function recoverConflict(exc) {
  clearReview();
  if (exc.status === 409) {
    const fresh = card.value
      ? verifyCard((await orgRequest("GET", `/cards/${card.value.id}`)).data)
      : (await orgRequest("GET", `/tasks/${props.taskId}/cards?job=${props.jobId}`)).items.find(slot => slot.requirement_id === props.row.id)?.card;
    if (fresh) showConflict(fresh);
  }
}
async function write(path, method, body) {
  busy.value = true; error.value = ""; notice.value = "";
  try {
    const result = await orgRequest(method, path, body);
    if (!active) return;
    const value = verifyCard(result.data); install(value); emit("updated", value); notice.value = "操作成功";
    window.dispatchEvent(new CustomEvent("bid:task-cards-changed", { detail: { taskId: props.taskId } }));
  } catch (exc) {
    if (exc.name === "AbortError") return;
    error.value = errorText(exc);
    try { await recoverConflict(exc); } catch (readError) { error.value += `；重新读取失败：${errorText(readError)}`; }
  } finally { busy.value = false; }
}
function save() {
  const body = { ...content.value, response_text: content.value.response_text || null, deviation_note: content.value.deviation_note || null };
  return card.value ? write(`/cards/${card.value.id}`, "PUT", { expected_revision: card.value.revision, content: body }) : write(`/tasks/${props.taskId}/cards`, "POST", { extraction_job_id: props.jobId, requirement_id: props.row.id, content: body });
}
const actionNames = { confirm: "确认响应", reject: "驳回", needs_material: "需补材料", reopen: "重开" };
async function action(action) {
  if (!card.value || dirty.value || conflict.value) return;
  if (["withdraw", "reject", "needs_material", "reopen"].includes(action) && !reason.value.trim()) { error.value = "请填写非空操作原因"; return; }
  if (action === "confirm" && !confirmReady.value) return;
  if (actionNames[action] && !(await confirmAction(`执行“${actionNames[action]}”？仅作用于当前修订 ${card.value.revision}。`, actionNames[action], "确定", action !== "confirm"))) return;
  const body = { expected_revision: card.value.revision, action, reason: reason.value.trim() || null };
  if (action === "confirm") { body.reviewed_evidence_ids = [...reviewed.value]; body.reviewed_warning_codes = [...warnings.value]; }
  await write(`/cards/${card.value.id}/actions`, "POST", body);
}
function classify() { if (!reason.value.trim()) { error.value = "分类必须填写原因"; return; } return write(`/cards/${card.value.id}/classification`, "POST", { expected_revision: card.value.revision, review_domain: domain.value, reason: reason.value }); }
async function readHistory() { try { history.value = (await orgRequest("GET", `/cards/${card.value.id}?history=true`)).data.history; } catch (exc) { error.value = errorText(exc); } }
async function source() {
  try { const chunks = (await orgRequest("GET", `/documents/${displayedSource.value.document_id}/chunks`)).items; sourceChunk.value = chunks.find(chunk => chunk.id === displayedSource.value.chunk_id); if (!sourceChunk.value) throw new Error("所指原文块不可访问"); }
  catch (exc) { error.value = errorText(exc); }
}
async function download() { try { await downloadOriginal(`/documents/${displayedSource.value.document_id}/download-link`, props.documentName); } catch (exc) { error.value = errorText(exc); } }
function useServer() { install(conflict.value); conflict.value = null; conflictOpen.value = false; error.value = ""; emit("updated", card.value); }
function materialChanged() { clearReview(); emit("materials"); refresh(); }
function beforeUnload(event) { if (dirty.value) { event.preventDefault(); event.returnValue = ""; } }
function focus() { if (!document.hidden && ready.value && card.value && !busy.value && !conflict.value) refresh(); }
window.addEventListener("beforeunload", beforeUnload);
document.addEventListener("visibilitychange", focus);
onBeforeUnmount(() => { active = false; window.removeEventListener("beforeunload", beforeUnload); document.removeEventListener("visibilitychange", focus); });
onMounted(async () => {
  try {
    const value = props.row.card ? verifyCard((await orgRequest("GET", `/cards/${props.row.card.id}`)).data) : null;
    if (active) { install(value); heading.value?.focus(); }
  } catch (exc) { error.value = errorText(exc); }
});
</script>
<template>
  <section aria-label="要求审阅详情" class="editor">
    <header class="editor-head">
      <h3 ref="heading" tabindex="-1">要求审阅详情</h3>
      <div class="head-actions"><el-button size="small" :icon="Close" @click="emit('close')">返回要求列表</el-button><el-button size="small" type="primary" plain :icon="ArrowRight" @click="emit('next')">下一条待我审阅</el-button></div>
    </header>
    <div class="editor-body">
      <p class="req-title">{{ props.row.text }}</p>
      <h4>招标原文</h4>
      <p class="hint">{{ locationLabel(displayedSource, documentName) }}</p>
      <blockquote class="quote">{{ displayedSource.quote }}</blockquote>
      <details v-if="props.row.model_quote"><summary>模型原样引文（仅供追溯，不是招标原文）</summary><blockquote class="quote">{{ props.row.model_quote }}</blockquote></details>
      <div class="actions"><DocumentPreview :document-id="displayedSource.document_id" :name="documentName ?? '招标原件'" :page="displayedSource.page" :block="displayedSource.location?.block_id ?? ''" label="在线查看原文位置" size="small" /><el-button size="small" :icon="View" @click="source">对照原文块</el-button><el-button size="small" :icon="Download" @click="download">下载招标原件</el-button></div>
      <details v-if="sourceChunk" open><summary>所指原文块</summary><pre>{{ sourceChunk.text }}</pre><p v-for="block in sourceChunk.blocks" :key="block.block_id" class="hint">{{ block.label }}：{{ block.text }}</p></details>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
      <el-button v-if="conflict" type="warning" plain size="small" @click="conflictOpen = true">查看修订冲突差异</el-button>
      <el-skeleton v-if="!ready" :rows="5" animated aria-label="正在读取当前修订" />
      <template v-else>
        <div class="status-box">
          <div class="status-row"><span class="tag" :class="statusTag[card?.state ?? 'missing_card']">状态：{{ states[card?.state ?? "missing_card"] }}</span><span class="tag">资格：{{ label(eligibilities, card?.eligibility ?? "missing_card") }}</span><span class="tag">修订 {{ card?.revision ?? "未建卡" }}</span><span v-if="content.deviation === 'negative'" class="tag danger">负偏离</span></div>
          <dl class="kv">
            <dt>审阅职责</dt><dd>{{ label(domains, card?.review_domain, "待单位管理员分类") }}</dd>
            <dt>处置</dt><dd>{{ label(dispositions, card?.disposition, "尚未决定") }}</dd>
            <dt>模型建议</dt><dd>{{ label(dispositions, card?.suggested_disposition, "无") }}</dd>
            <template v-if="card?.reason"><dt>最近操作原因</dt><dd>{{ card.reason }}</dd></template>
          </dl>
        </div>
        <p v-if="card?.review_hint === 'needs_material'" class="notice warning">模型提示需要补充材料，这还不是人工的“需补材料”决定。</p>
        <p v-if="invalid" class="notice danger">当前来源或材料已失效：{{ label(eligibilities, card.eligibility) }}。请重新核对；引用修复由单位管理员处理。</p>

        <h4>本修订实际链接材料</h4>
        <el-card v-for="(evidence, index) in card?.evidence ?? []" :key="evidence.id" shadow="never" class="evidence">
          <div class="tags"><span class="tag primary">材料 {{ index + 1 }}</span><span class="tag">{{ label(materialKinds, evidence.material_kind) }}</span><span class="tag">{{ label(quoteChecks, evidence.quote_check) }}</span><span class="tag" :class="evidence.active_selection ? 'success' : 'danger'">{{ evidence.active_selection ? "有效选择" : "已失效" }}</span><span v-if="marks.has(evidence.selection_id)" class="tag warning">模拟材料</span></div>
          <p class="hint mono">{{ evidence.input.field_path ?? `第 ${evidence.source_archive?.page} 页` }} · 选择 {{ evidence.selection_id.slice(0, 8) }} · 资源修订 {{ evidence.resource_revision_id.slice(0, 8) }}</p>
          <blockquote class="quote">{{ evidence.input.quote }}</blockquote>
          <SourcePreview v-if="evidence.source_archive" :source="evidence.source_archive" />
          <label v-if="canDecide && card.state === 'pending_review'" class="check"><input v-model="reviewed" type="checkbox" :value="evidence.id" :disabled="dirty || invalid || !!conflict" />已逐项核对材料 {{ index + 1 }}</label>
        </el-card>
        <p v-if="!card?.evidence.length" class="hint">本修订没有 Evidence。承诺不构成证明材料。</p>

        <h4>响应正文</h4>
        <el-form label-position="top" class="response-form" @submit.prevent="save" @input="edit">
          <el-form-item label="响应种类">
            <el-radio-group :key="kindKey" :model-value="content.response_kind" :disabled="!editable" aria-label="响应种类" @change="kindChange">
              <el-radio-button value="commitment">{{ responseKinds.commitment }}</el-radio-button><el-radio-button value="evidence">{{ responseKinds.evidence }}</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="响应正文">
            <SecretTextEditor v-model="content.response_text" :fields="secretFields" :disabled="!editable" label="响应正文" :maxlength="20000" @change="edit" />
          </el-form-item>
          <el-form-item label="偏离">
            <el-radio-group v-model="content.deviation" :disabled="!editable" aria-label="偏离" @change="edit">
              <el-radio-button v-for="(text, key) in deviations" :key="key" :value="key">{{ text }}</el-radio-button>
            </el-radio-group>
          </el-form-item>
          <p v-if="content.deviation === 'negative'" class="notice danger">负偏离：将如实保留在响应与初稿中。</p>
          <el-form-item label="对应关系或具体偏离说明"><SecretTextEditor v-model="content.deviation_note" :fields="secretFields" :disabled="!editable" label="对应关系或具体偏离说明" :maxlength="10000" :rows="2" @change="edit" /></el-form-item>
          <ol v-if="content.evidence.length" class="candidates"><li v-for="(input, index) in content.evidence" :key="index"><span class="hint">{{ input.kind }} · {{ input.field_path ?? input.evidence_source_id }}</span><blockquote class="quote">{{ input.quote }}</blockquote><el-button v-if="editable" size="small" type="danger" link @click="content.evidence.splice(index, 1); edit()">移除候选材料 {{ index + 1 }}</el-button></li></ol>
          <div class="actions"><el-button v-if="editable" type="primary" native-type="submit" :loading="busy">保存草稿</el-button><span v-if="dirty" class="hint">有未保存编辑；保存和提交审阅是两个动作。</span></div>
        </el-form>
        <MaterialPanel :task-id="taskId" :editable="editable && content.response_kind === 'evidence'" @add="add" @changed="materialChanged" />

        <h4>警示与人工操作</h4>
        <div v-for="code in card?.warning_codes ?? []" :key="code" class="warning-item"><p class="notice warning">{{ code }}</p><label v-if="canDecide && card.state === 'pending_review'" class="check"><input v-model="warnings" type="checkbox" :value="code" :disabled="dirty || invalid || !!conflict" />已核对警示 {{ code }}</label></div>
        <template v-if="writable && card">
          <el-form label-position="top" @submit.prevent>
            <el-form-item label="操作原因 / 警示处理理由"><el-input v-model="reason" type="textarea" maxlength="10000" :autosize="{ minRows: 2, maxRows: 6 }" placeholder="驳回、需补材料、撤回、重开和分类都需要填写原因" /></el-form-item>
          </el-form>
          <div v-if="orgAccess.role === 'admin' && card.state === 'draft' && !card.review_domain" class="actions classify">
            <el-select v-model="domain" aria-label="指定审阅职责" class="domain-select"><el-option value="technical" label="技术" /><el-option value="commercial" label="商务 / 资格" /></el-select>
            <el-button :disabled="busy || dirty || !!conflict" @click="classify">分类并记录理由</el-button>
          </div>
          <div v-if="card.disposition !== 'comply_only'" class="decision-bar">
          <div class="actions decision">
            <el-button v-if="card.state === 'draft'" type="primary" :disabled="busy || dirty || !!conflict" @click="action('submit')">提交审阅</el-button>
            <el-button v-if="card.state === 'pending_review'" :disabled="busy || !!conflict" @click="action('withdraw')">撤回</el-button>
            <template v-if="canDecide && card.state === 'pending_review'">
              <el-button type="success" :disabled="busy || !confirmReady" aria-describedby="confirm-blocker" @click="action('confirm')">确认响应</el-button>
              <el-button type="danger" plain :disabled="busy || !!conflict" @click="action('reject')">驳回</el-button>
              <el-button type="warning" plain :disabled="busy || !!conflict" @click="action('needs_material')">需补材料</el-button>
            </template>
            <el-button v-if="canDecide && card.state === 'confirmed'" :disabled="busy || !!conflict" @click="action('reopen')">重开</el-button>
          </div>
          <p v-if="canDecide && card.state === 'pending_review' && confirmBlocker" id="confirm-blocker" class="hint blocker">暂不能确认：{{ confirmBlocker }}</p>
          </div>
          <p v-if="card.disposition === 'comply_only'" class="hint">已决定“仅需遵守”，这张卡片不再逐项响应；由负责的审核人改回“逐项响应”后才能编辑。</p>
        </template>
        <p v-if="card?.confirmed_by" class="hint">确认人 {{ card.confirmed_by }} · {{ formatTime(card.confirmed_at) }}</p>
        <div v-if="card" class="actions"><el-button size="small" :icon="Refresh" @click="refresh(true)">重新核对当前修订</el-button><el-button size="small" :icon="Tickets" @click="readHistory">读取修订历史</el-button></div>
        <el-timeline v-if="history" class="history" aria-label="不可变修订历史">
          <el-timeline-item v-for="revision in history" :key="revision.revision_id" :timestamp="`修订 ${revision.revision} · ${states[revision.state] ?? revision.state} · ${label(actorKinds, revision.actor_kind)}`" placement="top">
            <p class="hint">原因 {{ display(revision.reason) }} · 确认 {{ display(revision.confirmed_by) }} / {{ revision.confirmed_at ? formatTime(revision.confirmed_at) : "未知" }}</p>
            <details><summary>修订内容</summary><pre>{{ display(revision.content) }}</pre></details>
          </el-timeline-item>
        </el-timeline>
      </template>
    </div>
    <el-dialog v-model="conflictOpen" title="修订冲突" width="860px" append-to-body>
      <template v-if="conflict">
        <p>本地基于修订 {{ card?.revision }}；服务器修订 {{ conflict.revision }} · {{ label(eligibilities, conflict.eligibility) }}</p>
        <div class="grid"><section><h4>本地未保存内容</h4><pre>{{ display(content) }}</pre></section><section><h4>服务器新内容</h4><blockquote class="quote">{{ conflict.source.quote }}</blockquote><pre>{{ display(conflict.content) }}</pre></section></div>
        <p class="hint">旧审阅勾选已清除。保留本地时仅供对照，写入已阻止。</p>
        <div class="actions conflict-actions"><el-button autofocus @click="conflictOpen = false">保留本地继续对照</el-button><el-button type="primary" @click="useServer">放弃本地，读取服务器修订</el-button></div>
      </template>
    </el-dialog>
  </section>
</template>
<style scoped>
.editor { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; }
.editor-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; padding: 12px 16px; border-bottom: 1px solid var(--border); position: sticky; top: 0; background: var(--surface); z-index: 2; border-radius: 8px 8px 0 0; }
.editor-head h3 { margin: 0; }
.head-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.head-actions .el-button + .el-button { margin-left: 0; }
.editor-body { padding: 4px 16px 16px; }
.req-title { font-weight: 600; font-size: 15px; }
.status-box { background: var(--surface-muted); border-radius: 8px; padding: 10px 12px; margin: 12px 0; }
.status-row { display: flex; gap: 6px; flex-wrap: wrap; }
.evidence { margin-bottom: 10px; }
.evidence :deep(.el-card__body) { padding: 12px; }
.response-form .el-radio-group { flex-wrap: wrap; }
.candidates { padding-left: 20px; }
.warning-item { margin-bottom: 8px; }
.classify .domain-select { width: 160px; }
/* Decisions stay reachable while the reviewer reads the content above them. */
.decision-bar { position: sticky; bottom: 0; z-index: 2; background: var(--surface); border-top: 1px solid var(--border); margin: 12px -16px 0; padding: 4px 16px 8px; box-shadow: 0 -4px 10px #0000000a; }
.decision { margin: 8px 0 4px; }
.decision-bar .blocker { margin: 0 0 4px; }
.blocker { color: var(--el-color-warning-dark-2, #b88230); }
.history { margin-top: 12px; }
.conflict-actions { justify-content: flex-end; }
</style>
