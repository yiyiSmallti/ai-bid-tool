<script setup>
import { View } from "@element-plus/icons-vue";
import { onBeforeUnmount, ref } from "vue";
import { errorText, orgRequest } from "../org.js";
import PageViewer from "./PageViewer.vue";
// The released DOCX is converted to PDF once per file; pages appear when conversion succeeds.
const props = defineProps({ exportId: { type: String, required: true }, title: { type: String, required: true } });
const state = ref(null), open = ref(false), busy = ref(false), error = ref("");
let timer = null, active = true;
async function poll() {
  clearTimeout(timer);
  try {
    state.value = (await orgRequest("GET", `/exports/${props.exportId}/preview`)).data;
    if (!active) return;
    if (["queued", "running"].includes(state.value.status)) timer = setTimeout(poll, 2000);
    else if (state.value.status === "succeeded") { busy.value = false; open.value = true; }
    else busy.value = false;
  } catch (exc) { busy.value = false; error.value = errorText(exc); }
}
async function start(retry = false) {
  busy.value = true; error.value = "";
  try {
    state.value = (await orgRequest("POST", `/exports/${props.exportId}/preview${retry ? "?retry=true" : ""}`)).data;
    if (state.value.status === "succeeded") { busy.value = false; open.value = true; }
    else if (state.value.status === "failed") busy.value = false;
    else poll();
  } catch (exc) { busy.value = false; error.value = errorText(exc); }
}
const load = (page, zoom) => orgRequest("GET", `/exports/${props.exportId}/preview/pages/${page}?zoom=${zoom}`, undefined, { binary: true });
onBeforeUnmount(() => { active = false; clearTimeout(timer); });
</script>
<template>
  <span class="export-preview">
    <el-button size="small" type="primary" plain :icon="View" :loading="busy" :aria-label="`在线预览：${title}`" @click="start()">{{ busy ? "正在生成预览…" : "在线预览" }}</el-button>
    <template v-if="state?.status === 'failed'">
      <span class="error">预览生成失败：{{ state.error?.message ?? "未知原因" }}</span>
      <el-button size="small" link type="primary" @click="start(true)">重试</el-button>
    </template>
    <span v-if="error" class="error" role="alert">{{ error }}</span>
  </span>
  <PageViewer v-if="state?.page_count" v-model="open" :title="title" :page-count="state.page_count" :load="load" />
</template>
<style scoped>
.export-preview { display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap; font-size: 12px; }
</style>
