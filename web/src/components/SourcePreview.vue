<script setup>
import { onBeforeUnmount, ref } from "vue";
import { errorText, orgRequest } from "../org.js";
const props = defineProps({ source: Object });
const dialog = ref(null), image = ref(null), error = ref("");
let epoch = 0;
async function open() {
  if (dialog.value.open) return;
  const attempt = ++epoch; error.value = ""; image.value = null; dialog.value.showModal();
  try {
    const link = await orgRequest("GET", `/evidence-sources/${props.source.id}/preview/download-link`);
    const blob = await orgRequest("GET", link.data.url, undefined, { binary: true });
    const signature = new Uint8Array(await blob.slice(0, 8).arrayBuffer());
    if (blob.type !== "image/png" || signature.join(",") !== "137,80,78,71,13,10,26,10") throw new Error("预览不是有效 PNG");
    const value = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error("读取图片失败")); reader.readAsDataURL(blob); });
    if (attempt === epoch) image.value = value;
  } catch (exc) { if (exc.name !== "AbortError") error.value = errorText(exc); }
}
function close() { epoch++; image.value = null; dialog.value?.close(); }
window.addEventListener("bid:org-reset", close);
onBeforeUnmount(() => { close(); window.removeEventListener("bid:org-reset", close); });
</script>
<template>
  <button @click="open">核对材料原页（第 {{ source.page }} 页）</button>
  <dialog ref="dialog" aria-label="证书原页预览" @close="image = null; epoch++" @cancel="close">
    <h3>{{ source.original.name }} · 第 {{ source.page }} 页</h3><button autofocus @click="close">关闭原页预览</button>
    <p v-if="error" role="alert" class="error">{{ error }}</p><p v-else-if="!image" role="status">正在读取受权原页…</p>
    <div v-if="image" class="image-scroll" tabindex="0" aria-label="可滚动放大的原页"><img :src="image" :alt="`${source.original.name} 第 ${source.page} 页`" /></div>
  </dialog>
</template>
