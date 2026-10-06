<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { assessmentError, enc, queryPath } from "../assessments.js";
import { locationLabel, orgRequest } from "../org.js";
import DocumentPreview from "./DocumentPreview.vue";
const props = defineProps({ taskId: String, parentKind: String, parentId: String, part: String, entryId: String, origin: { type: String, default: "source" }, citationIndex: { type: Number, default: 0 } });
const opened = ref(false), context = ref(null), error = ref(""), busy = ref(false);
let controller, serial = 0;
const segments = computed(() => {
  const value = context.value; if (!value) return [];
  const text = value.window.text, start = Math.max(0, value.quote_start - value.window.offset), end = Math.min(text.length, value.quote_end - value.window.offset);
  return end > start ? [{ text: text.slice(0, start) }, { text: text.slice(start, end), mark: true }, { text: text.slice(end) }] : [{ text }];
});
async function load(offset = 0) {
  controller?.abort(); controller = new AbortController(); const request = ++serial; busy.value = true; error.value = "";
  try {
    const result = await orgRequest("GET", queryPath(`/tasks/${enc(props.taskId)}/assessment-citation`, { parent_kind: props.parentKind, parent_id: props.parentId, part: props.part, entry_id: props.entryId, origin: props.origin, citation_index: props.citationIndex, offset, limit: 4000 }), undefined, { signal: controller.signal });
    if (request === serial) context.value = result.data;
  } catch (exc) { if (request === serial && exc.name !== "AbortError") error.value = assessmentError(exc); }
  finally { if (request === serial) busy.value = false; }
}
function clear() { serial++; controller?.abort(); opened.value = false; context.value = null; error.value = ""; }
watch(opened, (value) => { if (value) load(); else { serial++; controller?.abort(); context.value = null; } });
watch(() => [props.taskId, props.parentKind, props.parentId, props.part, props.entryId, props.origin, props.citationIndex], clear);
window.addEventListener("bid:org-reset", clear);
onBeforeUnmount(() => { clear(); window.removeEventListener("bid:org-reset", clear); });
</script>
<template>
  <el-button link type="primary" @click="opened = true">查看引用原文</el-button>
  <el-dialog v-model="opened" title="引用原文" width="min(760px, 94vw)" destroy-on-close>
    <el-skeleton v-if="busy" :rows="3" animated />
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" />
    <template v-if="context">
      <p>{{ context.kind === 'tender' ? locationLabel(context) : context.kind === 'draft' ? `已保存初稿 ${context.draft_id} · 响应修订 ${context.card_revision_id} · ${context.field === 'response_text' ? '响应正文' : '偏离说明'}` : `已授权证据 ${context.evidence_id}` }}</p>
      <DocumentPreview v-if="context.kind === 'tender'" :document-id="context.document_id" :page="context.page" :block="context.location?.block_id ?? ''" label="按原位置预览招标文件" />
      <blockquote class="quote citation-text"><template v-for="(segment, index) in segments" :key="index"><mark v-if="segment.mark">{{ segment.text }}</mark><span v-else>{{ segment.text }}</span></template></blockquote>
      <p role="status">原文位置 {{ context.window.offset + 1 }}–{{ context.window.offset + context.window.text.length }} / {{ context.window.total_characters }} 字符</p>
      <p v-if="context.window.next_offset != null">还有原文未展开</p>
      <el-button v-if="context.window.next_offset != null" :disabled="busy" @click="load(context.window.next_offset)">继续展开原文</el-button>
      <p><RouterLink :to="`/org/tasks/${enc(taskId)}/review?job=${enc(context.fix.extraction_job_id)}&requirement=${enc(context.fix.requirement_id)}${context.fix.current_card_id ? `&card=${enc(context.fix.current_card_id)}` : ''}`">前往响应卡修改</RouterLink></p>
    </template>
    <template #footer><el-button @click="opened = false">关闭原文</el-button></template>
  </el-dialog>
</template>
<style scoped>.citation-text { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 55vh; overflow: auto; } mark { background: #fff1a8; color: #202020; }</style>
