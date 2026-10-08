<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import TaskNavigation from "../components/TaskNavigation.vue";
import { formatTime, orgRequest } from "../org.js";
import { fileKinds, mib, multipart, reviewAuthority, reviewError, submissionPath, submissionStates, uploadManifest } from "../bid-review.js";
const route = useRoute(), router = useRouter();
const taskId = computed(() => String(route.params.taskId));
const rows = ref([]), submissions = ref([]), listMeta = ref(null), preview = ref(null), consent = ref(false), writable = ref(false), busy = ref(false), error = ref("");
let sequence = 0, requestId = crypto.randomUUID(), controller;
const total = computed(() => rows.value.reduce((sum, row) => sum + row.file.size, 0));
function invalidate() { preview.value = null; consent.value = false; requestId = crypto.randomUUID(); }
function choose(event, role) { invalidate(); rows.value.push(...[...event.target.files].map(file => ({ id: crypto.randomUUID(), file, role, kind: role === "tender" ? "tender" : "commercial_technical" }))); event.target.value = ""; }
function remove(id) { invalidate(); rows.value = rows.value.filter(row => row.id !== id); }
async function list(cursor = null) {
  const run = sequence;
  const query = new URLSearchParams({ limit: "50" }); if (cursor) query.set("cursor", cursor);
  const result = await orgRequest("GET", `${submissionPath(taskId.value)}?${query}`, undefined, { signal: controller?.signal });
  if (run !== sequence) return;
  if (result.data.task_id !== taskId.value) throw new Error("task mismatch");
  submissions.value = result.items; listMeta.value = result.data;
}
async function load() {
  const run = ++sequence; controller?.abort(); controller = new AbortController(); error.value = ""; writable.value = false; busy.value = false; rows.value = []; submissions.value = []; listMeta.value = null; invalidate();
  try { const allowed = await reviewAuthority(taskId.value); if (run !== sequence) return; writable.value = allowed; await list(); }
  catch (exc) { if (exc.name !== "AbortError") error.value = reviewError(exc); }
}
async function inspect() {
  const run = sequence, task = taskId.value;
  busy.value = true; error.value = ""; preview.value = null; consent.value = false;
  try { const metadata = await uploadManifest(rows.value, requestId, true); if (run !== sequence) return; const result = await orgRequest("POST", submissionPath(task), multipart(rows.value, metadata), { signal: controller?.signal }); if (run === sequence) preview.value = result.data; }
  catch (exc) { if (run === sequence && exc.name !== "AbortError") error.value = reviewError(exc); }
  finally { if (run === sequence) busy.value = false; }
}
async function upload() {
  const run = sequence, task = taskId.value;
  if (!preview.value || !consent.value) return;
  busy.value = true; error.value = "";
  try {
    const metadata = await uploadManifest(rows.value, requestId, false);
    if (run !== sequence) return;
    const result = await orgRequest("POST", submissionPath(task), multipart(rows.value, metadata), { signal: controller?.signal });
    if (run !== sequence) return;
    rows.value = []; invalidate(); await router.push(`/org/tasks/${taskId.value}/bid-submissions/${result.data.id}`);
  } catch (exc) { if (run === sequence && exc.name !== "AbortError") error.value = reviewError(exc); }
  finally { if (run === sequence) busy.value = false; }
}
watch(taskId, load, { immediate: true });
window.addEventListener("bid:org-reset", load);
onBeforeUnmount(() => { sequence++; controller?.abort(); rows.value = []; preview.value = null; window.removeEventListener("bid:org-reset", load); });
</script>
<template>
  <TaskNavigation :task-id="taskId" />
  <div class="page-heading"><div><h2>标书检验</h2><p class="hint">将招标文件和已编写的投标文件固定为一次提交，然后明确开始本地准备。</p></div><el-button :disabled="busy" @click="load">刷新提交列表</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" class="section" />
  <el-card v-if="writable" class="section" shadow="never">
    <template #header><h3>上传标书</h3></template>
    <el-steps :active="preview ? 1 : 0" finish-status="success" simple><el-step title="选择文件与角色" /><el-step title="预览限制" /><el-step title="上传固定版本" /></el-steps>
    <p class="hint">仅支持 PDF、DOCX；招标和投标合计最多 20 份。文件上限 100 MiB，总上限 500 MiB，实际以预览显示的部署限制为准。不接受加密、密码保护或需要修复的 PDF。</p>
    <div class="actions section">
      <label class="file-picker">选择招标文件<input aria-label="选择招标文件" type="file" multiple accept=".pdf,.docx" :disabled="busy" @change="choose($event, 'tender')" /></label>
      <label class="file-picker">选择投标文件<input aria-label="选择投标文件" type="file" multiple accept=".pdf,.docx" :disabled="busy" @change="choose($event, 'bid')" /></label>
    </div>
    <el-table role="table" :data="rows" aria-label="待上传文件">
      <el-table-column label="文件"><template #default="{ row }">{{ row.file.name }}<div class="hint">{{ mib(row.file.size) }}</div></template></el-table-column>
      <el-table-column label="角色" width="130"><template #default="{ row }">{{ row.role === 'tender' ? '招标文件' : '投标文件' }}</template></el-table-column>
      <el-table-column label="投标材料类型" width="220"><template #default="{ row }"><el-select v-if="row.role === 'bid'" v-model="row.kind" :aria-label="`${row.file.name}材料类型`" :disabled="busy" @change="invalidate"><el-option v-for="(text, kind) in fileKinds" v-show="kind !== 'tender'" :key="kind" :label="text" :value="kind" :disabled="kind === 'tender'" /></el-select><span v-else>招标文件</span></template></el-table-column>
      <el-table-column label="操作" width="85"><template #default="{ row }"><el-button link type="danger" :disabled="busy" @click="remove(row.id)">移除</el-button></template></el-table-column>
    </el-table>
    <p>{{ rows.length }} 份文件 · {{ mib(total) }}</p>
    <el-button type="primary" plain :loading="busy" :disabled="rows.length < 2 || rows.length > 20" @click="inspect">预览上传限制</el-button>
    <div v-if="preview" class="preview section" role="status">
      <h4>上传预览</h4><p>最多 {{ preview.limits.files }} 份 · 每份 {{ mib(preview.limits.file_bytes) }} · 合计 {{ mib(preview.limits.submission_bytes) }}</p>
      <p>预览未保存文件。本次上传与后续本地准备费用为 0；上传后不能替换原件，修改文件需创建新的提交。</p>
      <el-checkbox v-model="consent" :disabled="busy">确认文件角色与材料类型，上传此固定版本</el-checkbox>
      <div class="actions section"><el-button type="primary" :loading="busy" :disabled="!consent" @click="upload">上传固定版本</el-button></div>
    </div>
  </el-card>
  <el-alert v-else title="当前身份可查看安全提交信息；上传和准备需要任务负责人或协作成员中的管理员、商务成员。" type="info" :closable="false" class="section" />
  <el-card class="section" shadow="never"><template #header><h3>提交列表</h3></template>
    <el-table role="table" :data="submissions" aria-label="提交列表" empty-text="还没有标书提交">
      <el-table-column label="提交版本" min-width="200"><template #default="{ row }"><RouterLink :to="`/org/tasks/${taskId}/bid-submissions/${row.id}`">版本 {{ row.revision }}</RouterLink><div class="hint mono">{{ row.id }}</div></template></el-table-column>
      <el-table-column label="文件数" width="90"><template #default="{ row }">{{ (row.documents ?? row.files).length }}</template></el-table-column>
      <el-table-column label="状态" width="170"><template #default="{ row }">{{ submissionStates[row.state] }}</template></el-table-column>
      <el-table-column label="提交时间" min-width="180"><template #default="{ row }">{{ formatTime(row.created_at) }}</template></el-table-column>
    </el-table>
    <div class="actions section"><span class="hint">共 {{ listMeta?.total ?? 0 }} 次提交</span><el-button v-if="listMeta?.next_cursor" @click="list(listMeta.next_cursor).catch(exc => error = reviewError(exc))">下一页</el-button></div>
  </el-card>
</template>
<style scoped>
.file-picker { padding:12px 16px;border:1px dashed var(--el-color-primary);border-radius:6px;display:flex;gap:12px;align-items:center;flex-wrap:wrap; }
.preview { padding:16px;background:var(--surface-muted);border-radius:8px; }
h3,h4 { margin:0 0 12px; }
</style>
