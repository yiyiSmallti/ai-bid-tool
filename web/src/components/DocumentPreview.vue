<script setup>
import { Reading } from "@element-plus/icons-vue";
import { onBeforeUnmount, ref, watch } from "vue";
import { loadAuthorizedImage } from "../annotation.js";
import { errorText, orgRequest } from "../org.js";
import DocxViewer from "./DocxViewer.vue";
import PageViewer from "./PageViewer.vue";
// A tender original opened in place: PDF pages as images, Word as its parsed structure.
const props = defineProps({
  documentId: { type: String, default: null },
  // AuthorizedImageSource uses its own download handler, never a document ID.
  source: { type: /** @type {import("vue").PropType<import("../annotation.js").AuthorizedImageSource>} */ (Object), default: null },
  name: { type: String, default: "招标原件" },
  page: { type: Number, default: null },
  block: { type: String, default: "" },
  label: { type: String, default: "在线预览" },
  size: { type: String, default: "default" },
});
const document = ref(null), pdfOpen = ref(false), docxOpen = ref(false), busy = ref(false), error = ref("");
let controller = null;
function clear() { controller?.abort(); controller = null; document.value = null; pdfOpen.value = false; docxOpen.value = false; }
watch(() => [props.documentId, JSON.stringify(props.source)], clear);
window.addEventListener("bid:org-reset", clear);
onBeforeUnmount(() => { clear(); window.removeEventListener("bid:org-reset", clear); });
async function open() {
  busy.value = true; error.value = "";
  try {
    if (props.source) { controller?.abort(); controller = new AbortController(); document.value = { page_count: 1 }; pdfOpen.value = true; return; }
    if (!props.documentId) throw new Error("请选择文档或受权图片来源");
    document.value ??= (await orgRequest("GET", `/documents/${props.documentId}`)).data;
    // Page count and citation mode are known once the document is parsed.
    if (document.value.citation_mode === "block") docxOpen.value = true;
    else if (document.value.page_count) pdfOpen.value = true;
    else { document.value = null; throw new Error("文档解析完成后才能在线预览"); }
  } catch (exc) { error.value = errorText(exc); } finally { busy.value = false; }
}
const load = (page, zoom, signal) => props.source ? loadAuthorizedImage(props.source, signal) : orgRequest("GET", `/documents/${props.documentId}/pages/${page}/preview?zoom=${zoom}`, undefined, { binary: true, signal });
</script>
<template>
  <el-button :size="size" :icon="Reading" :loading="busy" :aria-label="`${label}：${name}`" @click="open">{{ label }}</el-button>
  <span v-if="error" class="error preview-error" role="alert">{{ error }}</span>
  <PageViewer v-if="document?.page_count" v-model="pdfOpen" :title="name" :page-count="document.page_count" :start-page="source ? 1 : page ?? 1" :max-concurrent="source ? 1 : 3" :load="load" />
  <DocxViewer v-if="document?.citation_mode === 'block'" v-model="docxOpen" :title="name" :document-id="documentId" :highlight="block" />
</template>
<style scoped>
.preview-error { font-size: 12px; margin-left: 6px; }
</style>
