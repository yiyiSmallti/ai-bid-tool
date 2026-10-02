<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { display, downloadOriginal, errorText, locationLabel, mine, orgAccess, orgRequest, states } from "../org.js";
import MaterialPanel from "./MaterialPanel.vue";
import SourcePreview from "./SourcePreview.vue";
const props = defineProps({ row: Object, taskId: String, jobId: String, documentName: String });
const emit = defineEmits(["updated", "dirty", "next", "close", "materials"]);
const card = ref(null), content = ref(emptyContent()), baseline = ref(""), ready = ref(false), busy = ref(false), error = ref(""), notice = ref("");
const reviewed = ref([]), warnings = ref([]), reason = ref(""), domain = ref("technical"), history = ref(null), sourceChunk = ref(null);
const conflict = ref(null), conflictDialog = ref(null), heading = ref(null);
let active = true;
const dirty = computed(() => ready.value && JSON.stringify(content.value) !== baseline.value);
const displayedSource = computed(() => card.value?.source ?? props.row.source);
const writable = computed(() => orgAccess.role && orgAccess.role !== "viewer");
const editable = computed(() => ready.value && writable.value && !conflict.value && (!card.value || (["draft", "rejected", "needs_material"].includes(card.value.state) && card.value.disposition !== "comply_only")));
const canDecide = computed(() => card.value && mine(card.value.review_domain));
const invalid = computed(() => ["stale_material", "invalid_citation", "needs_reconfirmation"].includes(card.value?.eligibility));
const completeResponse = computed(() => card.value && ["response_kind", "response_text", "deviation", "deviation_note"].every(key => typeof card.value.content[key] === "string" && card.value.content[key].trim()) && card.value.content.deviation_note.trim() !== "满足");
const confirmReady = computed(() => completeResponse.value && !dirty.value && !conflict.value && !invalid.value && card.value && reviewed.value.length === card.value.evidence.length && warnings.value.length === card.value.warning_codes.length && (!warnings.value.length || reason.value.trim()) && (card.value.content.response_kind !== "evidence" || card.value.evidence.length > 0));
function emptyContent() { return { response_kind: "commitment", response_text: "", deviation: "none", deviation_note: "", evidence: [] }; }
function clearReview() { reviewed.value = []; warnings.value = []; }
function install(value) {
  card.value = value; content.value = value ? JSON.parse(JSON.stringify(value.content)) : emptyContent();
  for (const key of ["response_text", "deviation_note"]) content.value[key] ??= "";
  content.value.response_kind ??= "commitment"; content.value.deviation ??= "none";
  baseline.value = JSON.stringify(content.value); sourceChunk.value = null; clearReview(); reason.value = ""; history.value = null; ready.value = true; emit("dirty", false);
}
function edit() { emit("dirty", dirty.value); clearReview(); }
function kindChange(event) {
  const value = event.target.value;
  if (value === "commitment" && content.value.evidence.length && !window.confirm(`改为承诺将移除 ${content.value.evidence.length} 项候选材料。继续？`)) { event.target.value = content.value.response_kind; return; }
  content.value.response_kind = value;
  if (value === "commitment") content.value.evidence = [];
  edit();
}
function add(input) {
  if (!editable.value) return;
  if (content.value.response_kind !== "evidence") { error.value = "请先将响应种类改为 evidence，承诺不能关联 Evidence"; return; }
  if (content.value.evidence.length >= 100) { error.value = "一张卡片最多关联 100 项材料"; return; }
  content.value.evidence.push(input); edit();
}
function verifyCard(value) {
  if (value.task_id !== props.taskId || value.extraction_job_id !== props.jobId || value.requirement_id !== props.row.id) throw new Error("卡片与当前任务、抽取或要求不匹配");
  return value;
}
async function showConflict(fresh) {
  conflict.value = verifyCard(fresh); clearReview(); error.value = "修订或材料已改变。未保存编辑仍在本页，须对照后重新处理；不会自动合并或重发。";
  await nextTick(); if (active && !conflictDialog.value.open) conflictDialog.value.showModal();
}
async function refresh() {
  if (!card.value) return;
  try {
    const fresh = verifyCard((await orgRequest("GET", `/cards/${card.value.id}`)).data);
    if (!active) return;
    if (fresh.revision !== card.value.revision || fresh.eligibility !== card.value.eligibility || JSON.stringify(fresh.warning_codes) !== JSON.stringify(card.value.warning_codes)) await showConflict(fresh);
    else { card.value = fresh; clearReview(); notice.value = "已重新核对当前修订；请重新勾选本次审阅项。"; }
  } catch (exc) { error.value = errorText(exc); clearReview(); }
}
async function recoverConflict(exc) {
  clearReview();
  if (exc.status === 409) {
    const fresh = card.value
      ? verifyCard((await orgRequest("GET", `/cards/${card.value.id}`)).data)
      : (await orgRequest("GET", `/tasks/${props.taskId}/cards?job=${props.jobId}`)).items.find(slot => slot.requirement_id === props.row.id)?.card;
    if (fresh) await showConflict(fresh);
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
async function action(action) {
  if (!card.value || dirty.value || conflict.value) return;
  if (["withdraw", "reject", "needs_material", "reopen"].includes(action) && !reason.value.trim()) { error.value = "请填写非空操作原因"; return; }
  if (action === "confirm" && !confirmReady.value) return;
  if (["confirm", "reject", "needs_material", "reopen"].includes(action) && !window.confirm(`执行${{confirm:'确认响应',reject:'驳回',needs_material:'需补材料',reopen:'重开'}[action]}？仅作用于当前修订。`)) return;
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
function useServer() { install(conflict.value); conflict.value = null; conflictDialog.value.close(); error.value = ""; emit("updated", card.value); }
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
  <section aria-label="要求审阅详情" class="review-detail">
    <h3 ref="heading" tabindex="-1">要求审阅详情</h3><div class="actions"><button @click="emit('close')">返回要求列表</button><button @click="emit('next')">下一条待我审阅</button></div>
    <p>{{ props.row.text }}</p><h4>招标原文</h4><p>{{ locationLabel(displayedSource, documentName) }}</p><blockquote class="quote">{{ displayedSource.quote }}</blockquote>
    <details v-if="props.row.model_quote"><summary>模型原样引文（仅供追溯，不是招标原文）</summary><blockquote class="quote">{{ props.row.model_quote }}</blockquote></details>
    <div class="actions"><button @click="source">对照原文块</button><button @click="download">下载招标原件</button></div>
    <details v-if="sourceChunk" open><summary>所指原文块</summary><pre>{{ sourceChunk.text }}</pre><p v-for="block in sourceChunk.blocks" :key="block.block_id">{{ block.label }}：{{ block.text }}</p></details>
    <p v-if="error" class="error" role="alert">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p><button v-if="conflict" @click="conflictDialog.showModal()">查看修订冲突差异</button>
    <p v-if="!ready">正在读取当前修订…</p>
    <template v-else>
      <p>状态：{{ states[card?.state ?? 'missing_card'] }} · 资格 eligibility：{{ card?.eligibility ?? 'missing_card' }} · 修订 {{ card?.revision ?? '未建卡' }}</p>
      <p>职责：{{ card?.review_domain ?? '待单位管理员分类' }} · 处置：{{ card?.disposition ?? '尚未决定' }} · 模型建议：{{ card?.suggested_disposition ?? '无' }}</p>
      <p v-if="card?.reason">最近操作原因：{{ card.reason }}</p>
      <p v-if="card?.review_hint === 'needs_material'" class="notice">模型/服务提示待补材料，尚不代表人工补材料决定。</p>
      <p v-if="invalid" class="error">当前来源或材料已失效：{{ card.eligibility }}。请重新核对；引用修复由管理员通过 CLI 完成。</p>
      <section><h4>本修订实际链接材料</h4>
        <article v-for="(evidence, index) in card?.evidence ?? []" :key="evidence.id" class="panel">
          <p>材料 {{ index + 1 }} · {{ evidence.material_kind }} · {{ evidence.quote_check }} · {{ evidence.active_selection ? '有效选择' : '已失效' }}</p>
          <p>选择 {{ evidence.selection_id }} · 资源修订 {{ evidence.resource_revision_id }} · {{ evidence.input.field_path ?? `第 ${evidence.source_archive?.page} 页` }}</p>
          <blockquote class="quote">{{ evidence.input.quote }}</blockquote>
          <SourcePreview v-if="evidence.source_archive" :source="evidence.source_archive" />
          <label v-if="canDecide && card.state === 'pending_review'" class="inline"><input v-model="reviewed" type="checkbox" :value="evidence.id" :disabled="dirty || invalid || !!conflict" />已逐项核对材料 {{ index + 1 }}</label>
        </article>
        <p v-if="!card?.evidence.length">本修订没有 Evidence。承诺不构成证明材料。</p>
      </section>
      <form @submit.prevent="save" @input="edit"><h4>响应正文</h4>
        <label>响应种类<select :value="content.response_kind" :disabled="!editable" @change="kindChange"><option value="commitment">承诺（commitment）</option><option value="evidence">证据响应（evidence）</option></select></label>
        <label>响应正文<textarea v-model="content.response_text" :disabled="!editable" maxlength="20000" rows="6" /></label>
        <label>偏离<select v-model="content.deviation" :disabled="!editable" @change="edit"><option value="none">无偏离</option><option value="positive">正偏离</option><option value="negative">负偏离</option></select></label>
        <p v-if="content.deviation === 'negative'" class="error">负偏离：将如实保留在响应与初稿中。</p>
        <label>对应关系或具体偏离说明<textarea v-model="content.deviation_note" :disabled="!editable" maxlength="10000" rows="3" /></label>
        <ol><li v-for="(input, index) in content.evidence" :key="index">{{ input.kind }} · {{ input.field_path ?? input.evidence_source_id }}<blockquote class="quote">{{ input.quote }}</blockquote><button v-if="editable" type="button" @click="content.evidence.splice(index, 1); edit()">移除候选材料 {{ index + 1 }}</button></li></ol>
        <button v-if="editable" class="primary" :disabled="busy">保存草稿</button><p v-if="dirty" class="notice">有未保存编辑；保存和提交审阅是两个动作。</p>
      </form>
      <MaterialPanel :task-id="taskId" :editable="editable && content.response_kind === 'evidence'" @add="add" @changed="materialChanged" />
      <section><h4>警示与人工操作</h4>
        <div v-for="code in card?.warning_codes ?? []" :key="code"><p class="notice">{{ code }}</p><label v-if="canDecide && card.state === 'pending_review'" class="inline"><input v-model="warnings" type="checkbox" :value="code" :disabled="dirty || invalid || !!conflict" />已核对警示 {{ code }}</label></div>
        <template v-if="writable && card">
          <label>操作原因 / 警示处理理由<textarea v-model="reason" maxlength="10000" rows="3" /></label>
          <div v-if="orgAccess.role === 'admin' && card.state === 'draft' && !card.review_domain" class="actions"><label>指定审阅职责<select v-model="domain"><option value="technical">技术</option><option value="commercial">商务 / 资格</option></select></label><button :disabled="busy || dirty || !!conflict" @click="classify">分类并记录理由</button></div>
          <div v-if="card.disposition !== 'comply_only'" class="actions">
            <button v-if="card.state === 'draft'" :disabled="busy || dirty || !!conflict" @click="action('submit')">提交审阅</button>
            <button v-if="card.state === 'pending_review'" :disabled="busy || !!conflict" @click="action('withdraw')">撤回</button>
            <template v-if="canDecide && card.state === 'pending_review'"><button class="primary" :disabled="busy || !confirmReady" @click="action('confirm')">确认响应</button><button :disabled="busy || !!conflict" @click="action('reject')">驳回</button><button :disabled="busy || !!conflict" @click="action('needs_material')">需补材料</button></template>
            <button v-if="canDecide && card.state === 'confirmed'" :disabled="busy || !!conflict" @click="action('reopen')">重开</button>
          </div>
        </template>
        <p v-if="card?.confirmed_by">确认人 {{ card.confirmed_by }} · {{ card.confirmed_at }}</p>
        <button v-if="card" @click="refresh">重新核对当前修订</button> <button v-if="card" @click="readHistory">读取修订历史</button>
        <details v-if="history" open><summary>不可变修订历史</summary><article v-for="revision in history" :key="revision.revision_id"><h5>修订 {{ revision.revision }} · {{ revision.state }} · {{ revision.actor_kind }}</h5><p>原因 {{ display(revision.reason) }} · 确认 {{ display(revision.confirmed_by) }} / {{ display(revision.confirmed_at) }}</p><pre>{{ display(revision.content) }}</pre></article></details>
      </section>
    </template>
    <dialog ref="conflictDialog" aria-label="修订冲突"><h3>修订冲突：服务器与本地编辑</h3><template v-if="conflict"><p>本地基于修订 {{ card?.revision }}；服务器修订 {{ conflict.revision }} · {{ conflict.eligibility }}</p><div class="grid"><section><h4>本地未保存内容</h4><pre>{{ display(content) }}</pre></section><section><h4>服务器新内容</h4><blockquote class="quote">{{ conflict.source.quote }}</blockquote><pre>{{ display(conflict.content) }}</pre></section></div><p>旧审阅勾选已清除。保留本地时仅供对照，写入已阻止。</p><button autofocus @click="conflictDialog.close()">保留本地继续对照</button><button @click="useServer">放弃本地，读取服务器修订</button></template></dialog>
  </section>
</template>
