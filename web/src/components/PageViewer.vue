<script setup>
import { ZoomIn, ZoomOut } from "@element-plus/icons-vue";
import { nextTick, onBeforeUnmount, ref, watch } from "vue";
import { errorText } from "../org.js";
// Server-rendered page images, loaded as they scroll into view; img-src allows only data: URLs.
const props = defineProps({
  modelValue: Boolean,
  title: { type: String, required: true },
  pageCount: { type: Number, required: true },
  startPage: { type: Number, default: 1 },
  // (page, zoom) => Promise<Blob> of image/png
  load: { type: Function, required: true },
  maxConcurrent: { type: Number, default: 3 },
});
const emit = defineEmits(["update:modelValue"]);
const scales = [100, 150, 200];
const scale = ref(100), current = ref(1), jump = ref(1), pages = ref([]), scroller = ref(null);
let observer = null, epoch = 0, active = 0;
const waiting = [], controllers = new Set();
const zoomFor = () => (scale.value > 100 ? 2 : 1);
function reset() {
  for (const controller of controllers) controller.abort(); controllers.clear();
  epoch++; waiting.length = 0; active = 0;
  pages.value = Array.from({ length: props.pageCount }, (_, index) => ({ page: index + 1, src: null, error: "", zoom: 0 }));
}
async function readImage(blob) {
  const signature = new Uint8Array(await blob.slice(0, 8).arrayBuffer());
  if (blob.type !== "image/png" || signature.join(",") !== "137,80,78,71,13,10,26,10") throw new Error("预览不是有效的 PNG");
  return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error("图片读取失败")); reader.readAsDataURL(blob); });
}
// At most three page requests run at once; scrolling queues the rest in view order.
function request(entry) {
  const zoom = zoomFor();
  if (entry.zoom === zoom || waiting.includes(entry)) return;
  waiting.push(entry); pump();
}
function pump() {
  while (active < props.maxConcurrent && waiting.length) {
    const entry = waiting.shift(), zoom = zoomFor(), attempt = epoch;
    active++;
    const controller = new AbortController(); controllers.add(controller);
    props.load(entry.page, zoom, controller.signal).then(readImage)
      .then((src) => { if (attempt === epoch) { entry.src = src; entry.zoom = zoom; entry.error = ""; } })
      .catch((exc) => { if (attempt === epoch && exc.name !== "AbortError") entry.error = errorText(exc); })
      .finally(() => { controllers.delete(controller); if (attempt === epoch) { active--; pump(); } });
  }
}
function observe() {
  observer?.disconnect();
  observer = new IntersectionObserver((records) => {
    for (const record of records) {
      const entry = pages.value[Number(record.target.dataset.page) - 1];
      if (record.isIntersecting && entry) request(entry);
    }
    const visible = records.filter((record) => record.isIntersecting && record.intersectionRatio > 0.3).map((record) => Number(record.target.dataset.page));
    if (visible.length) { current.value = Math.min(...visible); jump.value = current.value; }
  }, { root: scroller.value, rootMargin: "800px 0px", threshold: [0, 0.3, 0.6] });
  for (const element of scroller.value.querySelectorAll("[data-page]")) observer.observe(element);
}
function goTo(page) {
  const target = Math.min(props.pageCount, Math.max(1, Number(page) || 1));
  scroller.value?.querySelector(`[data-page="${target}"]`)?.scrollIntoView({ block: "start" });
}
function setScale(value) {
  const page = current.value;
  scale.value = value;
  for (const entry of pages.value) if (entry.zoom && entry.zoom !== zoomFor()) request(entry);
  nextTick(() => goTo(page));
}
async function opened() {
  reset(); await nextTick(); observe();
  if (props.startPage > 1) goTo(props.startPage);
}
function closed() { observer?.disconnect(); observer = null; reset(); pages.value = []; }
watch(() => props.pageCount, () => { if (props.modelValue) opened(); });
onBeforeUnmount(closed);
</script>
<template>
  <el-dialog :model-value="modelValue" :title="title" width="min(1100px, 96vw)" top="3vh" append-to-body class="page-viewer" @update:model-value="emit('update:modelValue', $event)" @opened="opened" @closed="closed">
    <div class="viewer-bar">
      <span class="pager">第 <el-input-number v-model="jump" :min="1" :max="pageCount" size="small" controls-position="right" aria-label="跳转到页" @change="goTo" /> / {{ pageCount }} 页</span>
      <span class="zoom">
        <el-button size="small" :icon="ZoomOut" :disabled="scale === scales[0]" aria-label="缩小" @click="setScale(scales[scales.indexOf(scale) - 1])" />
        <span class="scale">{{ scale }}%</span>
        <el-button size="small" :icon="ZoomIn" :disabled="scale === scales.at(-1)" aria-label="放大" @click="setScale(scales[scales.indexOf(scale) + 1])" />
      </span>
      <slot name="actions" />
    </div>
    <div ref="scroller" class="viewer-pages" tabindex="0" aria-label="可滚动的文件页面">
      <figure v-for="entry in pages" :key="entry.page" :data-page="entry.page" class="viewer-page" :style="{ width: `${scale}%` }">
        <img v-if="entry.src" :src="entry.src" :alt="`${title} 第 ${entry.page} 页`" />
        <div v-else class="placeholder"><span v-if="entry.error" class="error">{{ entry.error }}</span><span v-else class="hint">正在渲染第 {{ entry.page }} 页…</span></div>
        <figcaption class="hint">{{ entry.page }} / {{ pageCount }}</figcaption>
      </figure>
    </div>
  </el-dialog>
</template>
<style scoped>
.viewer-bar { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; padding-bottom: 10px; border-bottom: 1px solid var(--border); }
.pager, .zoom { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; }
.pager .el-input-number { width: 96px; }
.scale { min-width: 44px; text-align: center; }
.viewer-pages { height: 78vh; overflow: auto; background: #e9ecf2; padding: 16px; border-radius: 0 0 6px 6px; }
.viewer-page { margin: 0 auto 16px; max-width: none; }
.viewer-page img { display: block; width: 100%; background: #fff; box-shadow: 0 2px 10px #0002; }
.placeholder { aspect-ratio: 1 / 1.414; background: #fff; display: grid; place-items: center; box-shadow: 0 2px 10px #0001; }
.viewer-page figcaption { text-align: center; margin-top: 4px; }
</style>
