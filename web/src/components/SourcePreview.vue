<script setup>
import { onBeforeUnmount, ref } from "vue";
import { errorText, orgRequest } from "../org.js";
const props = defineProps({ source: Object });
const visible = ref(false), image = ref(null), error = ref("");
let epoch = 0;
async function open() {
  if (visible.value) return;
  const attempt = ++epoch; error.value = ""; image.value = null; visible.value = true;
  try {
    const link = await orgRequest("GET", `/evidence-sources/${props.source.id}/preview/download-link`);
    const blob = await orgRequest("GET", link.data.url, undefined, { binary: true });
    const signature = new Uint8Array(await blob.slice(0, 8).arrayBuffer());
    if (blob.type !== "image/png" || signature.join(",") !== "137,80,78,71,13,10,26,10") throw new Error("预览不是有效 PNG");
    const value = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error("读取图片失败")); reader.readAsDataURL(blob); });
    if (attempt === epoch) image.value = value;
  } catch (exc) { if (exc.name !== "AbortError") error.value = errorText(exc); }
}
function close() { epoch++; image.value = null; visible.value = false; }
window.addEventListener("bid:org-reset", close);
onBeforeUnmount(() => { close(); window.removeEventListener("bid:org-reset", close); });
</script>
<template>
  <el-button size="small" @click="open">核对材料原页（第 {{ source.page }} 页）</el-button>
  <el-dialog v-model="visible" :title="`${source.original.name} · 第 ${source.page} 页`" width="min(900px, 92vw)" append-to-body aria-label="证书原页预览" @closed="close">
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" />
    <el-skeleton v-else-if="!image" :rows="6" animated aria-label="正在读取受权原页" />
    <div v-if="image" class="image-scroll" tabindex="0" aria-label="可滚动放大的原页"><img :src="image" :alt="`${source.original.name} 第 ${source.page} 页`" /></div>
    <template #footer><el-button @click="close">关闭原页预览</el-button></template>
  </el-dialog>
</template>
<style scoped>
.image-scroll { max-height: 65vh; overflow: auto; }
.image-scroll img { max-width: none; display: block; }
</style>
