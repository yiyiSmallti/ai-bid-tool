<script setup>
import { ArrowDown, ArrowUp, Delete, RefreshRight, UploadFilled } from "@element-plus/icons-vue";
import { computed, onMounted, ref } from "vue";
import { downloadOriginal, errorText, orgAccess, orgRequest } from "../org.js";
import PageViewer from "./PageViewer.vue";
// Licenses and other certificates with their scanned images or PDFs. Several files form
// one original, a page per image in the order shown; tasks select it as qualification proof.
const kinds = { qualification: "单位证照", personnel: "人员证书" };
const ACCEPT = ".pdf,.png,.jpg,.jpeg";
const certificates = ref([]), files = ref(new Map()), error = ref(""), busy = ref(false);
const editing = ref(null), form = ref(blank()), uploads = ref([]), viewing = ref(null), viewerOpen = ref(false);
const writable = computed(() => ["admin", "bidder"].includes(orgAccess.role));
const today = new Date().toISOString().slice(0, 10);
const soon = new Date(Date.now() + 30 * 86400000).toISOString().slice(0, 10);
function blank() { return { kind: "qualification", name: "", number: "", valid_from: null, valid_until: null }; }
function expiry(data) {
  if (!data.valid_until) return null;
  if (data.valid_until < today) return { type: "danger", text: "已过期" };
  if (data.valid_until <= soon) return { type: "warning", text: "30 天内到期" };
  return null;
}
async function load() {
  error.value = "";
  try {
    const [listed, scanned] = await Promise.all([orgRequest("GET", "/resources/certificates"), orgRequest("GET", "/resources/certificates/files")]);
    certificates.value = listed.items;
    files.value = new Map(scanned.items.map((item) => [item.certificate_id, item]));
  } catch (exc) { error.value = errorText(exc); }
}
function open(entry) {
  editing.value = entry ?? { certificate_id: null };
  form.value = entry ? { ...blank(), ...entry.data } : blank();
  clearUploads();
}
function clearUploads() { uploads.value = []; }
function pick(file) {
  const raw = file.raw;
  if (!/\.(pdf|png|jpe?g)$/i.test(raw.name)) { error.value = "只支持 PDF、PNG、JPG 文件"; return false; }
  if (uploads.value.length >= 20) { error.value = "一个证照最多 20 个文件"; return false; }
  const item = { id: `${raw.name}-${raw.lastModified}-${uploads.value.length}`, raw, rotation: 0, url: null };
  uploads.value.push(item);
  // The console's CSP admits only data: images, so thumbnails are read into memory.
  if (raw.type.startsWith("image/")) {
    const reader = new FileReader();
    reader.onload = () => { const entry = uploads.value.find((value) => value.id === item.id); if (entry) entry.url = reader.result; };
    reader.readAsDataURL(raw);
  }
  return false;
}
function move(index, step) {
  const list = uploads.value, target = index + step;
  if (target < 0 || target >= list.length) return;
  [list[index], list[target]] = [list[target], list[index]];
}
function remove(index) { uploads.value.splice(index, 1); }
async function save() {
  busy.value = true; error.value = "";
  const data = { ...form.value, valid_from: form.value.valid_from || null, valid_until: form.value.valid_until || null };
  try {
    let id = editing.value.certificate_id, revision = editing.value.revision;
    if (!id) {
      const created = (await orgRequest("POST", "/resources/certificates", { data })).data;
      id = created.certificate_id; revision = created.revision;
    }
    if (uploads.value.length) {
      const body = new FormData();
      body.append("metadata", JSON.stringify({ expected_revision: revision, data, parts: uploads.value.map((item) => ({ rotation: item.rotation })) }));
      for (const item of uploads.value) body.append("file", item.raw, item.raw.name);
      await orgRequest("POST", `/resources/certificates/${id}/file-revisions`, body);
    } else if (editing.value.certificate_id) {
      await orgRequest("POST", `/resources/certificates/${id}/revisions`, { expected_revision: revision, data });
    }
    editing.value = null; clearUploads(); await load();
  } catch (exc) { error.value = errorText(exc); } finally { busy.value = false; }
}
function view(entry) { viewing.value = { ...files.value.get(entry.certificate_id), name: entry.data.name }; viewerOpen.value = true; }
const loadPage = (page, zoom) => orgRequest("GET", `/resources/certificates/revisions/${viewing.value.certificate_revision_id}/file/pages/${page}/preview?zoom=${zoom}`, undefined, { binary: true });
async function download(entry) {
  const file = files.value.get(entry.certificate_id);
  try { await downloadOriginal(`/resources/certificates/revisions/${file.certificate_revision_id}/file/download-link`, file.file.name); }
  catch (exc) { error.value = errorText(exc); }
}
onMounted(load);
</script>
<template>
  <el-card class="section" shadow="never" body-class="flush" data-testid="certificate-section">
    <template #header>
      <div class="section-title">
        <h3>证照与附件</h3>
        <span class="hint">营业执照、资质证书等。可上传多张图片或 PDF，按顺序合成一份原件；图片不发给模型</span>
        <el-button v-if="writable" size="small" type="primary" @click="open(null)">添加证照</el-button>
      </div>
    </template>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="inset" />
    <div class="table-scroll flat">
      <table class="data-table">
        <thead><tr><th>名称</th><th>类别</th><th>证号</th><th>有效期至</th><th>文件</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="entry in certificates" :key="entry.certificate_id">
            <td>{{ entry.data.name }}</td>
            <td>{{ kinds[entry.data.kind] ?? entry.data.kind }}</td>
            <td>{{ entry.data.number }}</td>
            <td>{{ entry.data.valid_until ?? "长期 / 未填" }} <el-tag v-if="expiry(entry.data)" :type="expiry(entry.data).type" size="small">{{ expiry(entry.data).text }}</el-tag></td>
            <td>
              <template v-if="files.get(entry.certificate_id)">
                {{ files.get(entry.certificate_id).file.page_count }} 页
                <span v-if="files.get(entry.certificate_id).parts.length" class="hint">（{{ files.get(entry.certificate_id).parts.length }} 个文件合成）</span>
              </template>
              <span v-else class="tag warning">未上传</span>
            </td>
            <td>
              <div class="row-actions">
                <el-button v-if="files.get(entry.certificate_id)" link type="primary" @click="view(entry)">预览</el-button>
                <el-button v-if="files.get(entry.certificate_id)" link @click="download(entry)">下载</el-button>
                <el-button v-if="writable" link @click="open(entry)">{{ files.get(entry.certificate_id) ? "更新" : "上传文件" }}</el-button>
              </div>
            </td>
          </tr>
          <tr v-if="!certificates.length"><td colspan="6" class="empty">还没有证照。点“添加证照”上传营业执照等文件。</td></tr>
        </tbody>
      </table>
    </div>
  </el-card>
  <el-dialog :model-value="!!editing" :title="editing?.certificate_id ? `更新：${editing.data.name}` : '添加证照'" width="640px" @close="editing = null; clearUploads()">
    <el-form label-position="top" @submit.prevent="save">
      <div class="grid">
        <el-form-item label="名称"><el-input v-model="form.name" maxlength="200" placeholder="营业执照" /></el-form-item>
        <el-form-item label="类别"><el-select v-model="form.kind"><el-option v-for="(text, key) in kinds" :key="key" :label="text" :value="key" /></el-select></el-form-item>
        <el-form-item label="证号"><el-input v-model="form.number" maxlength="200" placeholder="统一社会信用代码等" /></el-form-item>
        <el-form-item label="有效期"><el-date-picker v-model="form.valid_from" type="date" value-format="YYYY-MM-DD" placeholder="起" style="width: 48%" /> <el-date-picker v-model="form.valid_until" type="date" value-format="YYYY-MM-DD" placeholder="止（长期可不填）" style="width: 48%" /></el-form-item>
      </div>
      <el-form-item :label="editing?.certificate_id ? '新文件（不选则只改信息；选了会替换为新版本，旧版本保留）' : '文件'">
        <el-upload drag multiple :accept="ACCEPT" :auto-upload="false" :show-file-list="false" :on-change="pick" class="drop">
          <el-icon :size="28"><UploadFilled /></el-icon>
          <div>拖入或点击选择 PDF、PNG、JPG，可多选</div>
        </el-upload>
      </el-form-item>
      <ol v-if="uploads.length" class="uploads">
        <li v-for="(item, index) in uploads" :key="item.id">
          <span class="thumb"><img v-if="item.url" :src="item.url" :style="{ transform: `rotate(${item.rotation}deg)` }" alt="" /><span v-else class="pdf">PDF</span></span>
          <span class="name">{{ index + 1 }}. {{ item.raw.name }}</span>
          <el-button link :icon="RefreshRight" :title="'顺时针旋转 90°'" @click="item.rotation = (item.rotation + 90) % 360">{{ item.rotation }}°</el-button>
          <el-button link :icon="ArrowUp" :disabled="index === 0" title="上移" @click="move(index, -1)" />
          <el-button link :icon="ArrowDown" :disabled="index === uploads.length - 1" title="下移" @click="move(index, 1)" />
          <el-button link type="danger" :icon="Delete" title="移除" @click="remove(index)" />
        </li>
      </ol>
      <p class="hint">证照是单位自行上传的材料，系统不核验真伪。照片会去掉拍摄位置等元数据。</p>
    </el-form>
    <template #footer><el-button @click="editing = null; clearUploads()">取消</el-button><el-button type="primary" :loading="busy" :disabled="!form.name.trim() || !form.number.trim() || (!editing?.certificate_id && !uploads.length)" @click="save">保存</el-button></template>
  </el-dialog>
  <PageViewer v-if="viewing" v-model="viewerOpen" :title="`${viewing.name} · ${viewing.file.name}`" :page-count="viewing.file.page_count" :load="loadPage" />
</template>
<style scoped>
.inset { margin: 12px 16px; }
.flat { border: none; border-radius: 0; }
:deep(.flush) { padding: 0; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
.drop { width: 100%; }
.uploads { list-style: none; margin: 0 0 8px; padding: 0; display: grid; gap: 6px; }
.uploads li { display: flex; align-items: center; gap: 8px; }
.thumb { width: 44px; height: 44px; display: inline-flex; align-items: center; justify-content: center; border: 1px solid var(--el-border-color); border-radius: 4px; overflow: hidden; flex: none; }
.thumb img { max-width: 100%; max-height: 100%; }
.pdf { font-size: 11px; color: var(--el-color-danger); font-weight: 600; }
.name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
h3 { margin: 0; }
@media (max-width: 720px) { .grid { grid-template-columns: 1fr; } }
</style>
