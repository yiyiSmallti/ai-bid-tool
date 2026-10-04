<script setup>
import { Download, Search, Upload } from "@element-plus/icons-vue";
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { citationModes, display, documentStatuses, downloadOriginal, errorText, formatTime, jobStatuses, jobTag, label, orgAccess, orgRequest, recalled, remember, warningText } from "../org.js";
import JobPanel from "../components/JobPanel.vue";
const route = useRoute(), taskId = route.params.taskId;
const task = ref(null), documents = ref([]), history = ref([]), parseJobs = ref([]), selected = ref("");
const file = ref(null), fileInput = ref(null), preview = ref(null), reasoning = ref(""), warnings = ref([]), error = ref(""), busy = ref(false), receipt = ref(null);
const reasoningLevels = ref([]);
const jobId = ref(recalled(`task.${taskId}`)?.jobId ?? null);
const writable = computed(() => orgAccess.role && orgAccess.role !== "viewer");
const currentDocument = computed(() => documents.value.find((item) => item.id === selected.value));
// The furthest step this task has reached, for orientation only; every action stays explicit.
const step = computed(() => {
  if (history.value.some((entry) => entry.status === "succeeded")) return 3;
  if (documents.value.some((doc) => doc.status === "parsed")) return 2;
  return documents.value.length ? 1 : 0;
});
async function load() {
  try {
    const results = await Promise.all([orgRequest("GET", `/tasks/${taskId}`), orgRequest("GET", `/tasks/${taskId}/documents`), orgRequest("GET", `/tasks/${taskId}/extractions`), orgRequest("GET", `/tasks/${taskId}/jobs?kind=parse`)]);
    task.value = results[0].data; documents.value = results[1].items; history.value = results[2].items; parseJobs.value = results[3].items;
    if (!selected.value) selected.value = documents.value.find(d => d.id === recalled(`task.${taskId}`)?.documentId)?.id ?? documents.value[0]?.id ?? "";
    if (!jobId.value) jobId.value = parseJobs.value.find(j => ["queued", "running"].includes(j.status))?.id ?? null;
  } catch (exc) { error.value = errorText(exc); }
}
function rememberIds() { remember(`task.${taskId}`, { documentId: selected.value, jobId: jobId.value }); }
function showJob(id) { jobId.value = id; rememberIds(); }
async function upload() {
  if (!file.value) return;
  busy.value = true; error.value = "";
  try {
    const body = new FormData(); body.append("file", file.value);
    receipt.value = (await orgRequest("POST", `/tasks/${taskId}/documents`, body)).data;
    selected.value = receipt.value.id; preview.value = null; rememberIds(); await load();
  } catch (exc) { error.value = errorText(exc); } finally { busy.value = false; }
}
async function run(kind, dry = false, retry = false) {
  busy.value = true; error.value = "";
  if (dry) preview.value = null;
  try {
    const body = { dry_run: dry, retry };
    if (kind === "extract" && reasoning.value) body.reasoning = reasoning.value;
    const result = await orgRequest("POST", `/documents/${selected.value}/${kind}`, body);
    warnings.value = result.warnings;
    if (dry) { preview.value = result.data; reasoningLevels.value = result.data.reasoning_levels; }
    else { jobId.value = result.data.job_id; rememberIds(); await load(); }
  } catch (exc) { error.value = errorText(exc); if (exc.code === "unsupported_reasoning") { preview.value = null; reasoning.value = ""; } }
  finally { busy.value = false; }
}
async function download() { try { await downloadOriginal(`/documents/${selected.value}/download-link`, currentDocument.value.name); } catch (exc) { error.value = errorText(exc); } }
function changeDocument() { preview.value = null; reasoning.value = ""; reasoningLevels.value = []; warnings.value = []; rememberIds(); if (writable.value) run("extract", true); }
const documentName = (id) => documents.value.find(d => d.id === id)?.name ?? id;
onMounted(async () => { await load(); if (writable.value && selected.value) await run("extract", true); });
</script>
<template>
  <nav class="breadcrumb" aria-label="位置"><RouterLink to="/org/tasks">招标任务</RouterLink><span>/</span><span>{{ task?.name ?? "任务" }}</span></nav>
  <div class="page-header">
    <div>
      <h2>{{ task?.name ?? "任务" }}</h2>
      <p v-if="task" class="subtitle">招标编号 {{ display(task.tender_number) }} · 截止 {{ task.deadline ? formatTime(task.deadline) : "未知" }} · 预算记录 {{ display(task.budget_usd) }} USD</p>
    </div>
  </div>
  <el-card class="section" shadow="never">
    <el-steps :active="step" finish-status="success" align-center>
      <el-step title="上传招标文件" /><el-step title="解析文档" /><el-step title="抽取要求" /><el-step title="审阅响应与初稿" />
    </el-steps>
  </el-card>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
  <div class="task-grid">
    <el-card class="section" shadow="never">
      <template #header><div class="section-title"><h3>招标文件与解析</h3><el-tag v-if="currentDocument" :type="currentDocument.status === 'parsed' ? 'success' : 'info'">{{ label(documentStatuses, currentDocument.status) }}</el-tag></div></template>
      <form v-if="writable" class="upload" @submit.prevent="upload">
        <label class="file-picker">招标文件（PDF / DOCX）<input ref="fileInput" type="file" accept=".pdf,.docx" required @change="file = $event.target.files[0]" /></label>
        <el-button type="primary" native-type="submit" :icon="Upload" :loading="busy" :disabled="busy">上传文件</el-button>
        <p class="hint">上传后单独开始解析；格式、大小与页数限制由服务器校验。</p>
        <el-alert v-if="receipt" type="success" :closable="false" show-icon role="status" :title="`${receipt.duplicate ? '同内容文件已存在，已恢复原文档' : '上传成功'}：${receipt.name}`" />
      </form>
      <el-form label-position="top" class="doc-form">
        <el-form-item label="文档">
          <el-select v-model="selected" :disabled="busy" placeholder="选择文档" @change="changeDocument">
            <el-option v-for="doc in documents" :key="doc.id" :value="doc.id" :label="`${doc.name} · ${label(documentStatuses, doc.status)}`" />
          </el-select>
        </el-form-item>
      </el-form>
      <p v-if="currentDocument" class="doc-meta">状态：{{ label(documentStatuses, currentDocument.status) }} · 页数 {{ display(currentDocument.page_count) }} · 引用方式 {{ label(citationModes, currentDocument.citation_mode) }}</p>
      <div v-if="selected" class="actions">
        <el-button :icon="Download" @click="download">下载招标原件</el-button>
        <template v-if="writable"><el-button type="primary" plain :disabled="busy" @click="run('parse')">开始解析</el-button><el-button :disabled="busy" @click="run('parse', false, true)">显式重试解析</el-button></template>
      </div>
      <details><summary>解析作业历史（{{ parseJobs.length }}）</summary>
        <div class="actions"><el-button v-for="job in parseJobs" :key="job.id" size="small" @click="showJob(job.id)">{{ label(jobStatuses, job.status) }} · {{ job.id.slice(0, 8) }}</el-button></div>
      </details>
    </el-card>
    <el-card v-if="writable && selected" class="section" shadow="never">
      <template #header><div class="section-title"><h3>要求抽取</h3></div></template>
      <p class="hint">先预检确认档位与费用，再开始抽取。预检不创建作业、不调用模型。</p>
      <el-form label-position="top">
        <el-form-item v-if="reasoningLevels.length" label="官方推理档位">
          <el-select v-model="reasoning" :disabled="busy" placeholder="服务端默认" @change="preview = null">
            <el-option value="" label="服务端默认" />
            <el-option v-for="level in reasoningLevels" :key="level.name" :value="level.name" :label="`${level.label || level.name} · ${level.name}${level.default ? '（默认）' : ''}`" />
          </el-select>
        </el-form-item>
      </el-form>
      <el-button :icon="Search" :loading="busy && !preview" :disabled="busy" @click="run('extract', true)">抽取预检</el-button>
      <p v-if="preview && !preview.reasoning_levels.length" class="hint">此模型未提供可选推理档位</p>
      <p v-if="preview" class="preview">预检结果：{{ preview.parsed ? "可抽取" : "文档尚未解析" }} · 档位 {{ display(preview.reasoning) }} · {{ preview.estimated_cost_usd == null ? "费用暂不可估" : `${preview.estimated_cost_usd} USD` }}</p>
      <p class="hint">模型、单价、时长未返回时均为未知；预检不锁定费用。</p>
      <div class="actions"><el-button type="primary" :disabled="busy || !preview || currentDocument?.status !== 'parsed'" @click="run('extract')">开始抽取（可能产生费用）</el-button><el-button :disabled="busy || !preview || currentDocument?.status !== 'parsed'" @click="run('extract', false, true)">显式重试抽取（可能产生费用）</el-button></div>
      <el-alert v-for="warning in warnings" :key="warning" :title="warningText(warning)" type="info" :closable="false" role="note" class="section" />
    </el-card>
  </div>
  <JobPanel :job-id="jobId" :writable="writable" @finished="load" />
  <el-card class="section" shadow="never" body-class="flush">
    <template #header><div class="section-title"><h3>抽取历史</h3><span class="hint">选择成功的抽取进入审阅与初稿</span></div></template>
    <div class="table-scroll flat" tabindex="0"><table class="data-table"><caption class="sr-only">抽取历史：选择成功的固定 job 进入审阅</caption>
      <thead><tr><th>文档 / 抽取</th><th>档位 / 模型</th><th>状态</th><th>开始 / 结束</th><th class="num">保存 / 拒绝 / tokens</th><th>操作</th></tr></thead>
      <tbody>
        <tr v-for="entry in history" :key="entry.job_id">
          <td>{{ documentName(entry.document_id) }}<div class="hint mono">{{ entry.job_id }}</div></td>
          <td>{{ display(entry.reasoning) }}<div class="hint">{{ display(entry.model) }}</div></td>
          <td><el-tag :type="jobTag[entry.status] ?? 'info'" size="small">{{ label(jobStatuses, entry.status) }}</el-tag> <el-tag v-if="entry.latest" size="small" effect="plain">最新</el-tag><p v-if="entry.error" class="error">{{ display(entry.error) }}</p></td>
          <td class="hint">{{ formatTime(entry.created_at) }}<br />{{ entry.finished_at ? formatTime(entry.finished_at) : "未结束" }}</td>
          <td class="num">{{ display(entry.saved) }} / {{ display(entry.rejected) }} / {{ display(entry.tokens) }}</td>
          <td><div class="row-actions"><el-button link type="primary" @click="showJob(entry.job_id)">查看作业</el-button><template v-if="entry.status === 'succeeded'"><RouterLink :to="`/org/tasks/${taskId}/review?job=${entry.job_id}`">审阅要求</RouterLink><RouterLink :to="`/org/tasks/${taskId}/drafts?job=${entry.job_id}`">查看初稿</RouterLink></template></div></td>
        </tr>
        <tr v-if="!history.length"><td colspan="6" class="empty">还没有抽取记录。解析文档后在上方预检并开始抽取。</td></tr>
      </tbody>
    </table></div>
  </el-card>
</template>
<style scoped>
.task-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; align-items: start; }
.upload { display: flex; flex-direction: column; gap: 10px; align-items: flex-start; margin-bottom: 8px; }
.upload .file-picker { width: 100%; }
.doc-form .el-select { width: 100%; }
.preview { background: var(--surface-muted); padding: 10px 12px; border-radius: 6px; }
.row-actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; white-space: nowrap; }
.flat { border: none; border-radius: 0; }
:deep(.flush) { padding: 0; }
@media (max-width: 1000px) { .task-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
