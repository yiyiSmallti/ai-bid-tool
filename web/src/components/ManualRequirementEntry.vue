<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { categories, confirmAction, errorText, locationLabel, orgRequest } from "../org.js";
import { requirementRequest, checkScope, checkReview } from "../requirement-review.js";
import DocumentPreview from "./DocumentPreview.vue";
const props = defineProps({ taskId: String, scope: Object, documents: Array, rejected: Object, writable: Boolean });
const emit = defineEmits(["created", "dirty", "denied"]);
const form = reactive({ document: "", target: "current", category: "technical", starred: false, text: "", condition: "{}", quote: "", reason: "" });
const chunks = ref([]), position = ref(""), preview = ref(null), error = ref(""), busy = ref(false), conflict = ref(false), loading = ref(false);
let generation = 0, sourceGeneration = 0, pendingRequest = null;
const sources = computed(() => chunks.value.flatMap(chunk => chunk.blocks?.length ? chunk.blocks.map(block => ({ key: `${chunk.id}:${block.block_id}`, text: block.text, source: { document_id: form.document, chunk_id: chunk.id, page: null, location: Object.fromEntries(Object.entries(block).filter(([key]) => key !== "text")), quote: "" }, verified: chunk.citation_verified })) : [{ key: chunk.id, text: chunk.text, source: { document_id: form.document, chunk_id: chunk.id, page: chunk.page, location: null, quote: "" }, verified: chunk.citation_verified }]));
const chosen = computed(() => sources.value.find(item => item.key === position.value));
const targetDocument = computed(() => props.scope?.document_id ?? null);
function canonical(value) { return JSON.stringify(value, (key, item) => item && typeof item === "object" && !Array.isArray(item) ? Object.fromEntries(Object.keys(item).sort().map(name => [name, item[name]])) : item); }
function resetPreview() { generation++; preview.value = null; conflict.value = false; emit("dirty", Boolean(form.text || form.quote || form.reason)); }
watch(() => [form.target, form.category, form.starred, form.text, form.condition, form.quote, form.reason, position.value], resetPreview);
watch(() => props.scope?.revision, () => { if (preview.value) { preview.value = null; conflict.value = true; error.value = "集合已变化，已保留输入，请重新核验来源。"; } });
async function loadSources() {
  const run = ++sourceGeneration; loading.value = true; chunks.value = []; position.value = ""; preview.value = null; error.value = "";
  if (!form.document) { loading.value = false; return; }
  try { const result = await orgRequest("GET", `/documents/${form.document}/chunks`); if (run !== sourceGeneration) return; if (result.items.some(item => item.document_id !== form.document)) throw new Error("原文范围不符合契约"); chunks.value = result.items; }
  catch (exc) { if (run === sourceGeneration) { error.value = errorText(exc); if ([401,403,404].includes(exc.status)) emit("denied", exc); } }
  finally { if (run === sourceGeneration) loading.value = false; }
}
watch(() => form.document, loadSources);
watch(() => props.scope, value => { if (!value) form.target = "new"; }, { immediate: true });
watch(() => props.rejected, () => { preview.value = null; conflict.value = false; }, { deep: true });
function input() {
  if (!chosen.value || !form.quote.trim() || !form.text.trim() || !form.reason.trim()) throw new Error("请选择真实原文位置，并填写逐字引文、要求和原因。");
  if (form.target === "current" && (!props.scope || form.document !== targetDocument.value)) throw new Error("追加集合必须使用该集合的招标文件。");
  const condition = JSON.parse(form.condition); if (!condition || Array.isArray(condition) || typeof condition !== "object") throw new Error("结构条件必须是 JSON 对象。");
  return { extraction_job_id: form.target === "current" ? props.scope.extraction_job_id : null, expected_set_revision: form.target === "current" ? props.scope.revision : null, content: { category: form.category, starred: form.starred, text: form.text, condition, source: { ...chosen.value.source, quote: form.quote } }, rejected_item: props.rejected ? { job_id: props.rejected.job_id, index: props.rejected.index, summary_sha256: props.rejected.summary_sha256 } : null, reason: form.reason };
}
async function verify() {
  if (busy.value || !props.writable) return;
  const run = ++generation; busy.value = true; preview.value = null; error.value = "";
  try { const body = input(), result = await requirementRequest("POST", `/tasks/${props.taskId}/requirements/manual-preview`, body); if (run !== generation) return; if (result.data.task_id !== props.taskId || result.data.extraction_job_id !== body.extraction_job_id || canonical(result.data.verified_source.source) !== canonical(body.content.source)) throw new Error("核验结果与当前输入不一致"); preview.value = result.data; conflict.value = false; }
  catch (exc) { if (run === generation) { error.value = errorText(exc); if ([401,403,404].includes(exc.status)) emit("denied", exc); } }
  finally { busy.value = false; }
}
async function save() {
  if (busy.value || !preview.value || !props.writable || conflict.value || preview.value.duplicate_requirement_id) return;
  busy.value = true; error.value = "";
  try { const body = { ...input(), expected_preview_hash: preview.value.preview_hash }; const key = canonical(body); if (pendingRequest?.key !== key) pendingRequest = { key, id: crypto.randomUUID() }; const result = await requirementRequest("POST", `/tasks/${props.taskId}/requirements/manual`, { ...body, request_id: pendingRequest.id }); checkScope(result.data.scope, props.taskId); checkReview(result.data.requirement, props.taskId, result.data.scope.extraction_job_id); pendingRequest = null; form.text = ""; form.quote = ""; form.reason = ""; preview.value = null; emit("dirty", false); emit("created", result.data); }
  catch (exc) { preview.value = null; conflict.value = true; error.value = `${errorText(exc)}；输入已保留，未自动重试，请重新核验。`; if ([401,403,404].includes(exc.status)) emit("denied", exc); }
  finally { busy.value = false; }
}
async function discard() { return !(form.text || form.quote || form.reason) || await confirmAction("尚有未保存的补录输入，确定离开？"); }
function clear() { pendingRequest = null; generation++; sourceGeneration++; chunks.value = []; preview.value = null; form.document = ""; form.text = ""; form.quote = ""; form.reason = ""; emit("dirty", false); }
window.addEventListener("bid:org-reset", clear);
onBeforeUnmount(() => { generation++; sourceGeneration++; window.removeEventListener("bid:org-reset", clear); });
defineExpose({ discard });
</script>
<template>
  <el-card class="section" shadow="never"><template #header><h3>{{ rejected ? '从原文补录拒绝项' : '补录遗漏要求' }}</h3></template>
    <el-alert v-if="rejected" title="拒绝回执只是截断的摘要，不是完整候选要求；请逐项从原文补全。" type="warning" :closable="false" />
    <el-form label-position="top" @submit.prevent="verify">
      <el-form-item label="目标集合"><el-radio-group v-model="form.target"><el-radio value="current" :disabled="!scope">追加当前明确集合</el-radio><el-radio value="new">新建人工补录集</el-radio></el-radio-group></el-form-item>
      <el-form-item label="已解析的招标文件"><el-select v-model="form.document" aria-label="补录招标文件"><el-option v-for="doc in documents.filter(item => item.status === 'parsed')" :key="doc.id" :label="doc.name" :value="doc.id" :disabled="form.target === 'current' && doc.id !== targetDocument" /></el-select></el-form-item>
      <el-form-item label="原文页或完整 Word 位置"><el-select v-model="position" filterable aria-label="补录原文位置" :loading="loading"><el-option v-for="item in sources" :key="item.key" :value="item.key" :label="locationLabel(item.source)" :disabled="item.verified === false" /></el-select></el-form-item>
      <template v-if="chosen"><DocumentPreview :document-id="form.document" :page="chosen.source.page" :block="chosen.source.location?.block_id ?? ''" label="核对补录原件" /><blockquote class="quote original" tabindex="0">{{ chosen.text }}</blockquote><p class="hint">选择的原文只用于定位；逐字引文需由本人明确填写。</p></template>
      <el-form-item label="逐字引文" required><el-input v-model="form.quote" aria-label="逐字引文" aria-required="true" type="textarea" :rows="3" maxlength="20000" /></el-form-item>
      <el-form-item label="要求正文" required><el-input v-model="form.text" aria-label="要求正文" aria-required="true" type="textarea" :rows="3" maxlength="20000" /></el-form-item>
      <div class="grid"><el-form-item label="类别"><el-select v-model="form.category"><el-option v-for="(text,key) in categories" :key="key" :value="key" :label="text" /></el-select></el-form-item><el-form-item label="★ 条款"><el-checkbox v-model="form.starred">明确标记星标</el-checkbox></el-form-item></div>
      <el-form-item label="结构条件（JSON 对象）"><el-input v-model="form.condition" type="textarea" maxlength="20000" /></el-form-item>
      <el-form-item label="补录原因" required><el-input v-model="form.reason" aria-label="补录原因" aria-required="true" type="textarea" maxlength="10000" /></el-form-item>
      <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" class="section" />
      <el-button native-type="submit" :disabled="!writable || busy" :loading="busy">核验补录来源</el-button>
      <template v-if="preview"><p role="status">{{ preview.creates_manual_scope ? '新建人工补录集' : '追加当前集合' }} · 原文精确跨度 {{ preview.verified_source.start }}–{{ preview.verified_source.end }}</p><blockquote class="quote"><mark>{{ preview.verified_source.source.quote }}</mark></blockquote><p v-if="preview.duplicate_requirement_id" class="warning">此来源位置已有要求 {{ preview.duplicate_requirement_id }}，请打开已有要求。</p><el-button type="primary" :disabled="busy || !writable || Boolean(preview.duplicate_requirement_id)" @click="save">保存待确认要求</el-button></template>
      <p class="hint">保存不会确认要求，也不会确认响应、证据或无遗漏。争议要求保留在集合中，本阶段不提供删除或语义编辑。</p>
    </el-form>
  </el-card>
</template>
<style scoped>.original { max-height: 260px; overflow:auto; white-space:pre-wrap; } .el-select {width:100%} mark{background:#fff1a8;color:#202020}</style>
