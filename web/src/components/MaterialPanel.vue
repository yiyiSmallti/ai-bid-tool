<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { display, downloadOriginal, errorText, orgAccess, orgRequest } from "../org.js";
const props = defineProps({ taskId: String, editable: Boolean });
const emit = defineEmits(["add", "changed"]);
// These are the scalar fields in RESOURCE_FIELD_PATHS, never a user-supplied path.
const kinds = {
  product: { path: "products", label: "产品声明", id: "product_id", fields: ["name", "vendor", "model", "model_version", "official_url", "whitepaper_url"] },
  feature: { path: "features", label: "功能声明", id: "feature_id", fields: ["product_id", "name", "description", "status"] },
  certificate: { path: "certificates", label: "证书声明", id: "certificate_id", fields: ["kind", "name", "number", "valid_from", "valid_until"] },
  org_profile: { path: "profiles", label: "单位资料声明", id: "profile_id", fields: ["name", "registration_details", "performance_summary", "standard_wording"] },
};
const selections = ref([]), sources = ref([]), files = ref([]), loaded = ref(false), error = ref(""), busy = ref(false);
const chosen = ref(""), field = ref(""), quote = ref(""), pageSource = ref(""), pageQuote = ref("");
const sourceImage = ref(null), previewName = ref(""), previewBusy = ref(false);
const libraryKind = ref("product"), library = ref([]), libraryChoice = ref(""), lot = ref("");
const certificate = ref(""), page = ref(1);
let previewEpoch = 0;
const selection = computed(() => selections.value.find(s => s.id === chosen.value));
const allowedFields = computed(() => selection.value ? kinds[selection.value.kind].fields.filter(key => typeof selection.value.data[key] === "string") : []);
const writable = computed(() => orgAccess.role && orgAccess.role !== "viewer");
async function load() {
  busy.value = true; error.value = ""; loaded.value = false;
  try {
    const requests = Object.entries(kinds).map(async ([kind, info]) => {
      const result = await orgRequest("GET", `/tasks/${props.taskId}/${info.path}?history=true`);
      return result.items.map(item => ({ ...item, kind, active: result.data.active_snapshot_ids.includes(item.id), validity: result.data.validity_by_revision?.[item.certificate_revision_id] }));
    });
    const values = await Promise.all([...requests, orgRequest("GET", `/tasks/${props.taskId}/evidence-sources?history=true`), orgRequest("GET", `/tasks/${props.taskId}/certificate-files`)]);
    selections.value = values.slice(0, 4).flat(); sources.value = values[4].items; files.value = values[5].items; loaded.value = true;
  } catch (exc) { error.value = errorText(exc); } finally { busy.value = false; }
}
function addField() {
  if (!selection.value?.active || !allowedFields.value.includes(field.value) || !quote.value.trim()) return;
  if (!selection.value.data[field.value].includes(quote.value)) { error.value = "摘录须逐字出现在固定字段中"; return; }
  emit("add", { kind: selection.value.kind, selection_id: selection.value.id, field_path: field.value, quote: quote.value }); quote.value = "";
}
function addPage() { if (sources.value.some(s => s.id === pageSource.value && s.active_selection) && pageQuote.value.trim()) { emit("add", { kind: "certificate_pdf_page", evidence_source_id: pageSource.value, quote: pageQuote.value }); pageQuote.value = ""; } }
async function preview(source) {
  const epoch = ++previewEpoch; sourceImage.value = null; previewBusy.value = true;
  try {
    const link = await orgRequest("GET", `/evidence-sources/${source.id}/preview/download-link`);
    const blob = await orgRequest("GET", link.data.url, undefined, { binary: true });
    const signature = new Uint8Array(await blob.slice(0, 8).arrayBuffer());
    if (blob.type !== "image/png" || signature.join(",") !== "137,80,78,71,13,10,26,10") throw new Error("预览不是有效的 PNG");
    const data = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = () => reject(new Error("预览读取失败")); reader.readAsDataURL(blob); });
    if (epoch === previewEpoch) { sourceImage.value = data; previewName.value = `${source.original.name} 第 ${source.page} 页`; }
  } catch (exc) { if (exc.name !== "AbortError") error.value = errorText(exc); }
  finally { if (epoch === previewEpoch) previewBusy.value = false; }
}
function closePreview() { previewEpoch++; sourceImage.value = null; previewName.value = ""; previewBusy.value = false; }
async function original(file) { try { await downloadOriginal(`/resources/certificates/revisions/${file.certificate_revision_id}/file/download-link`, file.file.name); } catch (exc) { error.value = errorText(exc); } }
async function loadLibrary() {
  library.value = []; libraryChoice.value = "";
  try { library.value = (await orgRequest("GET", `/resources/${kinds[libraryKind.value].path}`)).items; }
  catch (exc) { error.value = errorText(exc); }
}
async function pin() {
  const entry = library.value.find(item => item.id === libraryChoice.value); if (!entry) return;
  if (!window.confirm("固定此资源版本到任务？同一资源和分包的旧选择将被替换，依赖它的响应可能失效，需要重新审阅。")) return;
  busy.value = true;
  try { const info = kinds[libraryKind.value]; await orgRequest("POST", `/tasks/${props.taskId}/${info.path}`, { [info.id]: entry[info.id], revision: entry.revision, lot: lot.value || null }); emit("changed"); await load(); }
  catch (exc) { error.value = errorText(exc); } finally { busy.value = false; }
}
async function archivePage() {
  busy.value = true;
  try { await orgRequest("POST", `/tasks/${props.taskId}/evidence-sources`, { task_certificate_id: certificate.value, page: Number(page.value) }); emit("changed"); await load(); }
  catch (exc) { error.value = errorText(exc); } finally { busy.value = false; }
}
window.addEventListener("bid:org-reset", closePreview);
onBeforeUnmount(() => { closePreview(); window.removeEventListener("bid:org-reset", closePreview); });
onMounted(load);
</script>
<template>
  <section aria-label="固定材料" class="panel"><h3>任务固定材料</h3>
    <p class="hint">资源字段是声明；产品 URL 不是网页取证，功能状态与业绩文字不是交付或合同证明。缺材料时使用 bid resource 维护真实资源。</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="!loaded" role="status">材料正在读取或读取失败，不能视为空集合。</p>
    <details v-if="writable"><summary>从资源库固定版本 / 显式替换</summary>
      <label>资源种类<select v-model="libraryKind" @change="library = []; libraryChoice = ''"><option v-for="(info, kind) in kinds" :key="kind" :value="kind">{{ info.label }}</option></select></label>
      <button @click="loadLibrary">读取资源库</button>
      <label>库中修订<select v-model="libraryChoice"><option value="">请选择</option><option v-for="item in library" :key="item.id" :value="item.id">{{ item.data.name }} · 修订 {{ item.revision }}</option></select></label>
      <label>分包（可选）<input v-model="lot" maxlength="100" /></label><button :disabled="busy || !libraryChoice" @click="pin">固定所选版本到任务</button>
    </details>
    <label>固定选择<select v-model="chosen" @change="field = ''; quote = ''"><option value="">请选择</option><option v-for="item in selections" :key="item.id" :value="item.id">{{ kinds[item.kind].label }} · {{ item.data.name }} · r{{ item.revision }} · {{ item.active ? '有效选择' : '已失效' }}</option></select></label>
    <template v-if="selection"><p>选择 ID {{ selection.id }} · 修订 {{ selection.revision }} · {{ selection.active ? "有效选择" : "非活动选择，不可添加" }}</p><p v-if="selection.validity">日期检查：{{ display(selection.validity) }}</p>
      <label>可摘录字段<select v-model="field" @change="quote = ''"><option value="">请选择</option><option v-for="key in allowedFields" :key="key">{{ key }}</option></select></label>
      <blockquote v-if="field" class="quote">{{ selection.data[field] }}</blockquote>
      <form v-if="editable && selection.active && field" @submit.prevent="addField"><label>字段逐字摘录<textarea v-model="quote" required maxlength="20000" /></label><button :disabled="busy">添加字段材料</button></form>
    </template>
    <details><summary>证书原件与页来源</summary>
      <ul><li v-for="file in files" :key="file.id">{{ file.data.name }} · 固定修订 {{ file.revision }} · {{ file.file?.name ?? '没有原件' }} <button v-if="file.file" @click="original(file)">下载证书 PDF</button></li></ul>
      <form v-if="writable" @submit.prevent="archivePage"><label>固定证书<select v-model="certificate" required><option value="">请选择</option><option v-for="file in files.filter(f => f.file)" :key="file.id" :value="file.id">{{ file.data.name }}（{{ file.file.page_count }} 页）</option></select></label><label>真实页码<input v-model="page" type="number" min="1" :max="files.find(f => f.id === certificate)?.file?.page_count ?? 200" required /></label><button :disabled="busy || !certificate">归档证书页来源</button></form>
      <ul><li v-for="source in sources" :key="source.id">{{ source.original.name }} 第 {{ source.page }} 页 · {{ source.status }} · {{ source.active_selection ? '有效选择' : '已失效' }} <button @click="preview(source)">预览第 {{ source.page }} 页</button></li></ul>
      <form v-if="editable" @submit.prevent="addPage"><label>已有证书页来源<select v-model="pageSource" required><option value="">请选择</option><option v-for="source in sources.filter(s => s.active_selection)" :key="source.id" :value="source.id">{{ source.original.name }} 第 {{ source.page }} 页</option></select></label><label>证书页逐字摘录<textarea v-model="pageQuote" required maxlength="20000" /></label><button>添加页材料</button></form>
      <p class="hint">来源始终是 unconfirmed_source。无可提取文本的扫描页只能预览，不能生成精确摘录证据；服务端会拒绝无效摘录。</p>
    </details>
    <p v-if="previewBusy" role="status">正在读取受权预览…</p>
    <figure v-if="sourceImage" class="preview"><figcaption>{{ previewName }}</figcaption><button @click="closePreview">关闭预览</button><a :href="sourceImage" :download="`${previewName}.png`">下载本页图片以放大核对</a><img :src="sourceImage" :alt="previewName" /></figure>
  </section>
</template>
