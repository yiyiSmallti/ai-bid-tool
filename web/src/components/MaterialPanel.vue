<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { confirmAction, display, downloadOriginal, errorText, orgAccess, orgRequest } from "../org.js";
import PageViewer from "./PageViewer.vue";
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
const certificate = ref(""), page = ref(1), open = ref([]), viewing = ref(null), viewerOpen = ref(false);
function view(file) { viewing.value = file; viewerOpen.value = true; }
const loadCertificatePage = (pageNumber, zoom) => orgRequest("GET", `/resources/certificates/revisions/${viewing.value.certificate_revision_id}/file/pages/${pageNumber}/preview?zoom=${zoom}`, undefined, { binary: true });
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
  if (!(await confirmAction("固定此资源版本到任务？同一资源和分包的旧选择将被替换，依赖它的响应可能失效，需要重新审阅。", "固定资源版本", "确定", true))) return;
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
  <section aria-label="固定材料" class="materials">
    <h4>任务固定材料</h4>
    <p class="hint">资源字段是声明；产品 URL 不是网页取证，功能状态与业绩文字不是交付或合同证明。缺少材料时，请联系单位管理员在资源库补充产品、证书或单位资料，再在这里固定到任务。</p>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
    <p v-if="!loaded" class="hint">材料正在读取或读取失败，不能视为空集合。</p>
    <el-collapse v-model="open">
      <el-collapse-item v-if="writable" name="library" title="从资源库固定版本 / 显式替换">
        <el-form label-position="top" @submit.prevent>
          <div class="grid">
            <el-form-item label="资源种类"><el-select v-model="libraryKind" @change="library = []; libraryChoice = ''"><el-option v-for="(info, kind) in kinds" :key="kind" :value="kind" :label="info.label" /></el-select></el-form-item>
            <el-form-item label="分包（可选）"><el-input v-model="lot" maxlength="100" /></el-form-item>
          </div>
          <el-button size="small" @click="loadLibrary">读取资源库</el-button>
          <el-form-item label="库中修订" class="top-gap"><el-select v-model="libraryChoice" placeholder="请选择" :disabled="!library.length"><el-option v-for="item in library" :key="item.id" :value="item.id" :label="`${item.data.name} · 修订 ${item.revision}`" /></el-select></el-form-item>
          <el-button size="small" type="primary" plain :disabled="busy || !libraryChoice" @click="pin">固定所选版本到任务</el-button>
        </el-form>
      </el-collapse-item>
      <el-collapse-item name="fields" :title="`固定选择与字段摘录（${selections.length}）`">
        <el-form label-position="top" @submit.prevent>
          <el-form-item label="固定选择"><el-select v-model="chosen" placeholder="请选择" @change="field = ''; quote = ''"><el-option v-for="item in selections" :key="item.id" :value="item.id" :label="`${kinds[item.kind].label} · ${item.data.name} · r${item.revision} · ${item.active ? '有效选择' : '已失效'}`" /></el-select></el-form-item>
          <template v-if="selection">
            <p class="hint">修订 {{ selection.revision }} · {{ selection.active ? "有效选择" : "非活动选择，不可添加" }}<template v-if="selection.validity"> · 日期检查 {{ display(selection.validity) }}</template></p>
            <el-form-item label="可摘录字段"><el-select v-model="field" placeholder="请选择" @change="quote = ''"><el-option v-for="key in allowedFields" :key="key" :value="key" :label="key" /></el-select></el-form-item>
            <blockquote v-if="field" class="quote">{{ selection.data[field] }}</blockquote>
            <template v-if="editable && selection.active && field">
              <el-form-item label="字段逐字摘录"><el-input v-model="quote" type="textarea" maxlength="20000" :rows="2" /></el-form-item>
              <el-button size="small" type="primary" plain :disabled="busy || !quote.trim()" @click="addField">添加字段材料</el-button>
            </template>
          </template>
        </el-form>
      </el-collapse-item>
      <el-collapse-item name="pages" :title="`证书原件与页来源（${files.length} 份证书 · ${sources.length} 个页来源）`">
        <ul class="plain-list"><li v-for="file in files" :key="file.id"><span>{{ file.data.name }} · 固定修订 {{ file.revision }} · {{ file.file?.name ?? "没有原件" }}</span><span v-if="file.file" class="file-actions"><el-button size="small" link type="primary" @click="view(file)">在线预览</el-button><el-button size="small" link type="primary" @click="original(file)">下载证书 PDF</el-button></span></li></ul>
        <el-form v-if="writable" label-position="top" @submit.prevent="archivePage">
          <div class="grid">
            <el-form-item label="固定证书"><el-select v-model="certificate" placeholder="请选择"><el-option v-for="file in files.filter(f => f.file)" :key="file.id" :value="file.id" :label="`${file.data.name}（${file.file.page_count} 页）`" /></el-select></el-form-item>
            <el-form-item label="真实页码"><el-input-number v-model="page" :min="1" :max="files.find(f => f.id === certificate)?.file?.page_count ?? 200" controls-position="right" /></el-form-item>
          </div>
          <el-button size="small" native-type="submit" :disabled="busy || !certificate">归档证书页来源</el-button>
        </el-form>
        <ul class="plain-list"><li v-for="source in sources" :key="source.id"><span>{{ source.original.name }} 第 {{ source.page }} 页 · <span class="tag" :class="source.active_selection ? 'success' : 'danger'">{{ source.active_selection ? "有效选择" : "已失效" }}</span></span><el-button size="small" link type="primary" @click="preview(source)">预览第 {{ source.page }} 页</el-button></li></ul>
        <el-form v-if="editable" label-position="top" @submit.prevent="addPage">
          <el-form-item label="已有证书页来源"><el-select v-model="pageSource" placeholder="请选择"><el-option v-for="source in sources.filter(s => s.active_selection)" :key="source.id" :value="source.id" :label="`${source.original.name} 第 ${source.page} 页`" /></el-select></el-form-item>
          <el-form-item label="证书页逐字摘录"><el-input v-model="pageQuote" type="textarea" maxlength="20000" :rows="2" /></el-form-item>
          <el-button size="small" type="primary" plain native-type="submit" :disabled="!pageSource || !pageQuote.trim()">添加页材料</el-button>
        </el-form>
        <p class="hint">页来源在人工核对前始终是未确认状态。没有可提取文本的扫描页只能预览，不能生成精确摘录证据；服务端会拒绝无效摘录。</p>
      </el-collapse-item>
    </el-collapse>
    <p v-if="previewBusy" class="hint" role="status">正在读取受权预览…</p>
    <PageViewer v-if="viewing" v-model="viewerOpen" :title="`${viewing.data.name} · ${viewing.file.name}`" :page-count="viewing.file.page_count" :load="loadCertificatePage" />
    <figure v-if="sourceImage" class="preview"><figcaption class="preview-head"><span>{{ previewName }}</span><span class="actions"><a :href="sourceImage" :download="`${previewName}.png`">下载本页图片以放大核对</a><el-button size="small" @click="closePreview">关闭预览</el-button></span></figcaption><img :src="sourceImage" :alt="previewName" /></figure>
  </section>
</template>
<style scoped>
.materials { margin: 16px 0; }
.plain-list { list-style: none; padding: 0; margin: 8px 0; }
.plain-list li { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 13px; flex-wrap: wrap; }
.top-gap { margin-top: 12px; }
.preview img { max-width: 100%; display: block; margin-top: 8px; border: 1px solid var(--border); border-radius: 6px; }
.preview-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap; }
.preview-head .actions { margin: 0; }
</style>
