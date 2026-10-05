<script setup>
import { View } from "@element-plus/icons-vue";
import { onBeforeUnmount, ref } from "vue";
import { errorText, orgRequest } from "../org.js";
import PageViewer from "./PageViewer.vue";
// The released DOCX is converted to PDF once per file; pages appear when conversion succeeds.
const props = defineProps({ exportId: { type: String, required: true }, title: { type: String, required: true } });
const state = ref(null), open = ref(false), busy = ref(false), error = ref("");
let timer = null, active = true;
function applyState(next) {
  state.value = next;
  if (next.status === "invalidated") { busy.value = false; open.value = false; }
  else if (next.status === "succeeded") { busy.value = false; open.value = true; }
  else if (!["queued", "running"].includes(next.status)) busy.value = false;
}
async function poll() {
  clearTimeout(timer);
  try {
    const next = (await orgRequest("GET", `/exports/${props.exportId}/preview`)).data;
    if (!active) return;
    applyState(next);
    if (["queued", "running"].includes(next.status)) timer = setTimeout(poll, 2000);
  } catch (exc) { busy.value = false; error.value = errorText(exc); }
}
async function start(retry = false) {
  busy.value = true; error.value = "";
  try {
    const next = (await orgRequest("POST", `/exports/${props.exportId}/preview${retry ? "?retry=true" : ""}`)).data;
    applyState(next);
    if (["queued", "running"].includes(next.status)) poll();
  } catch (exc) { busy.value = false; error.value = errorText(exc); }
}
const load = (page, zoom) => orgRequest("GET", `/exports/${props.exportId}/preview/pages/${page}?zoom=${zoom}`, undefined, { binary: true });
onBeforeUnmount(() => { active = false; clearTimeout(timer); });
</script>
<template>
  <span class="export-preview">
    <el-button size="small" type="primary" plain :icon="View" :loading="busy" :disabled="state?.status === 'invalidated'" :aria-label="`在线预览：${title}`" @click="start()">{{ busy ? "正在生成预览…" : "在线预览" }}</el-button>
    <span v-if="state?.status === 'invalidated'" class="error" role="alert">导出内容或发起人权限已变化，在线预览已失效，请重新生成并发布。</span>
    <template v-if="state?.status === 'failed'">
      <span class="error">预览生成失败：{{ state.error?.message ?? "未知原因" }}</span>
      <el-button size="small" link type="primary" @click="start(true)">重试</el-button>
    </template>
    <span v-if="error" class="error" role="alert">{{ error }}</span>
  </span>
  <PageViewer v-if="state?.status === 'succeeded' && state?.page_count" v-model="open" :title="title" :page-count="state.page_count" :load="load" />
</template>
<style scoped>
.export-preview { display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 12px; }
</style>
