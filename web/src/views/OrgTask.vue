<script setup>
import { useTaskAuthority } from "../task-authority.js";
import TaskNavigation from "../components/TaskNavigation.vue";
import { Download, MoreFilled, Search, Upload, UploadFilled } from "@element-plus/icons-vue";
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { citationModes, display, documentStatuses, downloadOriginal, errorText, formatTime, jobStatuses, jobTag, label, orgAccess, orgRequest, recalled, remember, warningText } from "../org.js";
import DocumentPreview from "../components/DocumentPreview.vue";
import ExportPreview from "../components/ExportPreview.vue";
import JobPanel from "../components/JobPanel.vue";
import SimulationPanel from "../components/SimulationPanel.vue";
import ConfidentialPanel from "../components/ConfidentialPanel.vue";
const route = useRoute(), taskId = route.params.taskId;
const task = ref(null), documents = ref([]), history = ref([]), parseJobs = ref([]), selected = ref(""), exportList = ref(null);
const file = ref(null), fileInput = ref(null), preview = ref(null), reasoning = ref(""), warnings = ref([]), error = ref(""), busy = ref(false), receipt = ref(null);
const reasoningLevels = ref([]);
const jobId = ref(recalled(`task.${taskId}`)?.jobId ?? null);
let accessLost = false, readGeneration = 0;
const authority = useTaskAuthority(taskId, (exc) => { accessLost = true; readGeneration++; task.value = null; documents.value = []; history.value = []; parseJobs.value = []; exportList.value = null; preview.value = null; receipt.value = null; file.value = null; error.value = errorText(exc); });
const writable = computed(() => authority.canWrite.value && orgAccess.role && orgAccess.role !== "viewer");
const currentDocument = computed(() => documents.value.find((item) => item.id === selected.value));
// The furthest step this task has reached, for orientation only; every action stays explicit.
const step = computed(() => {
  if (history.value.some((entry) => entry.status === "succeeded")) return 3;
  if (documents.value.some((doc) => doc.status === "parsed")) return 2;
  return documents.value.length ? 1 : 0;
});
async function load() {
  const run = ++readGeneration;
  try {
    const results = await Promise.all([orgRequest("GET", `/tasks/${taskId}`), orgRequest("GET", `/tasks/${taskId}/documents`), orgRequest("GET", `/tasks/${taskId}/extractions`), orgRequest("GET", `/tasks/${taskId}/jobs?kind=parse`)]);
    if (accessLost || run !== readGeneration) return;
    task.value = results[0].data; documents.value = results[1].items; history.value = results[2].items; parseJobs.value = results[3].items;
    if (!selected.value) selected.value = documents.value.find(d => d.id === recalled(`task.${taskId}`)?.documentId)?.id ?? documents.value[0]?.id ?? "";
    if (!jobId.value) jobId.value = parseJobs.value.find(j => ["queued", "running"].includes(j.status))?.id ?? null;
  } catch (exc) { error.value = errorText(exc); }
}
function rememberIds() { remember(`task.${taskId}`, { documentId: selected.value, jobId: jobId.value }); }
function showJob(id) { jobId.value = id; rememberIds(); }
function moreDocument(command) { if (command === "retry") run("parse", false, true); else showJob(command); }
const parsedCount = computed(() => documents.value.filter((doc) => doc.status === "parsed").length);
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
// Released exports are visible to commercial reviewers only; other roles see no export card.
async function loadExports() {
  try { exportList.value = (await orgRequest("GET", `/tasks/${taskId}/exports`)).items; }
  catch (exc) { exportList.value = null; if (exc.status !== 403) error.value = errorText(exc); }
}
async function downloadExport(item) {
  try {
    const signed = await orgRequest("GET", `/exports/${item.id}/download-link`);
    const blob = await orgRequest("GET", signed.data.url, undefined, { binary: true });
    const url = URL.createObjectURL(blob);
    const anchor = window.document.createElement("a"); anchor.href = url; anchor.download = item.file.name ?? `export-${item.id}.docx`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (exc) { error.value = errorText(exc); }
}
const exportModes = { review_copy: "审阅件", final_section: "正式件" };
onMounted(async () => { await load(); loadExports(); if (writable.value && selected.value) await run("extract", true); });
</script>
<template>
  <TaskNavigation :task-id="taskId" />
  <el-alert v-if="authority.access.value?.workflow.state === 'archived'" title="任务已归档，当前只能读取已有记录。" type="info" :closable="false" class="section" />
  <nav class="breadcrumb" aria-label="位置"><RouterLink to="/org/tasks">招标任务</RouterLink><span>/</span><span>{{ task?.name ?? "任务" }}</span></nav>
  <p v-if="authority.access.value"><RouterLink :to="{path:'/org/products',query:{task:taskId}}">前往产品库核对并选择精确修订</RouterLink> · <RouterLink :to="{path:'/org/features',query:{task:taskId}}">前往功能库核对并选择精确修订</RouterLink> · <RouterLink :to="{path:'/org/profiles',query:{task:taskId}}">前往资料和证照库核对并选择精确修订</RouterLink> · <RouterLink :to="{path:'/org/confidential',query:{task:taskId}}">前往保密字段管理（当前任务）</RouterLink></p>
  <div class="page-header">
    <div>
      <h2>{{ task?.name ?? "任务" }}</h2>
      <p class="subtitle">上传并解析招标文件，抽取要求后进入逐条审阅与初稿。</p>
    </div>
  </div>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
  <p><RouterLink :to="{path:'/org/templates',query:{task:taskId}}">在模板库选择精确修订到此任务</RouterLink></p>
  <div class="task-layout">
    <div class="task-main">
      <el-card class="section" shadow="never">
        <template #header><div class="section-title"><h3>招标文件与解析</h3><el-tag v-if="currentDocument" :type="currentDocument.status === 'parsed' ? 'success' : 'info'">{{ label(documentStatuses, currentDocument.status) }}</el-tag></div></template>
        <form v-if="writable" class="upload" @submit.prevent="upload">
          <label class="dropzone" :class="{ chosen: file }">
            <el-icon :size="22"><UploadFilled /></el-icon>
            <span class="dropzone-text"><strong>{{ file ? file.name : "选择招标文件" }}</strong><span class="hint">{{ file ? "点击可重新选择" : "PDF 或 DOCX，大小与页数由服务器校验" }}</span></span>
            <span class="sr-only">招标文件（PDF / DOCX）</span>
            <input ref="fileInput" class="sr-only" type="file" accept=".pdf,.docx" required aria-label="招标文件（PDF / DOCX）" @change="file = $event.target.files[0]" />
          </label>
          <el-button type="primary" native-type="submit" :icon="Upload" :loading="busy" :disabled="busy || !file">上传文件</el-button>
        </form>
        <el-alert v-if="receipt" type="success" :closable="false" show-icon role="status" class="receipt" :title="`${receipt.duplicate ? '同内容文件已存在，已恢复原文档' : '上传成功'}：${receipt.name}`" />
        <el-form label-position="top" class="doc-form" @submit.prevent>
          <el-form-item label="文档">
            <el-select v-model="selected" :disabled="busy" placeholder="选择文档" @change="changeDocument">
              <el-option v-for="doc in documents" :key="doc.id" :value="doc.id" :label="`${doc.name} · ${label(documentStatuses, doc.status)}`" />
            </el-select>
          </el-form-item>
        </el-form>
        <p v-if="currentDocument" class="doc-meta">状态：{{ label(documentStatuses, currentDocument.status) }} · 页数 {{ display(currentDocument.page_count) }} · 引用方式 {{ label(citationModes, currentDocument.citation_mode) }}</p>
        <div v-if="selected" class="actions action-bar">
          <el-button v-if="writable" :type="currentDocument?.status === 'parsed' ? 'default' : 'primary'" :disabled="busy" @click="run('parse')">开始解析</el-button>
          <DocumentPreview :key="selected" :document-id="selected" :name="currentDocument?.name ?? '招标原件'" />
          <el-button :icon="Download" @click="download">下载招标原件</el-button>
          <el-dropdown trigger="click" @command="moreDocument">
            <el-button :icon="MoreFilled">更多</el-button>
            <template #dropdown><el-dropdown-menu>
              <el-dropdown-item v-if="writable" command="retry" :disabled="busy">显式重试解析</el-dropdown-item>
              <el-dropdown-item v-for="job in parseJobs" :key="job.id" :command="job.id" :divided="writable && job === parseJobs[0]">查看解析作业 · {{ label(jobStatuses, job.status) }} · {{ job.id.slice(0, 8) }}</el-dropdown-item>
              <el-dropdown-item v-if="!parseJobs.length && !writable" disabled>没有解析作业</el-dropdown-item>
            </el-dropdown-menu></template>
          </el-dropdown>
        </div>
      </el-card>
      <el-card v-if="writable && selected" class="section" shadow="never">
        <template #header><div class="section-title"><h3>要求抽取</h3><span class="hint">先预检确认档位与费用，再开始抽取；预检不创建作业、不调用模型</span></div></template>
        <el-form label-position="top" class="extract-row" @submit.prevent="run('extract', true)">
          <el-form-item v-if="reasoningLevels.length" label="官方推理档位" class="level">
            <el-select v-model="reasoning" :disabled="busy" placeholder="服务端默认" @change="preview = null">
              <el-option value="" label="服务端默认" />
              <el-option v-for="level in reasoningLevels" :key="level.name" :value="level.name" :label="`${level.label || level.name} · ${level.name}${level.default ? '（默认）' : ''}`" />
            </el-select>
          </el-form-item>
          <el-button :icon="Search" native-type="submit" :loading="busy && !preview" :disabled="busy">抽取预检</el-button>
        </el-form>
        <p v-if="preview && !preview.reasoning_levels.length" class="hint">此模型未提供可选推理档位</p>
        <div v-if="preview" class="preview-box">
          <span>预检结果：{{ preview.parsed ? "可抽取" : "文档尚未解析" }} · 档位 {{ display(preview.reasoning) }} · {{ preview.estimated_cost_usd == null ? "费用暂不可估" : `${preview.estimated_cost_usd} USD` }}</span>
          <span v-for="warning in warnings" :key="warning" class="hint">{{ warningText(warning) }}</span>
        </div>
        <div class="actions action-bar">
          <el-button type="primary" :disabled="busy || !preview || currentDocument?.status !== 'parsed'" @click="run('extract')">开始抽取（可能产生费用）</el-button>
          <el-dropdown trigger="click" :disabled="busy || !preview || currentDocument?.status !== 'parsed'" @command="run('extract', false, true)">
            <el-button :icon="MoreFilled" :disabled="busy || !preview || currentDocument?.status !== 'parsed'">更多</el-button>
            <template #dropdown><el-dropdown-menu><el-dropdown-item command="retry">显式重试抽取（可能产生费用）</el-dropdown-item></el-dropdown-menu></template>
          </el-dropdown>
          <span class="hint">模型、单价、时长未返回时均为未知；预检不锁定费用。</span>
        </div>
      </el-card>
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
            <td><div class="row-actions"><el-button link type="primary" @click="showJob(entry.job_id)">查看作业</el-button><template v-if="entry.status === 'succeeded'"><RouterLink :to="`/org/tasks/${taskId}/requirements?job=${entry.job_id}`">确认要求</RouterLink><RouterLink :to="`/org/tasks/${taskId}/review?job=${entry.job_id}`">审阅响应</RouterLink><RouterLink :to="`/org/tasks/${taskId}/drafts?job=${entry.job_id}`">查看初稿</RouterLink><RouterLink :to="`/org/tasks/${taskId}/checks?job=${entry.job_id}`">检查风险</RouterLink><RouterLink :to="`/org/tasks/${taskId}/scores?job=${entry.job_id}`">评分预估</RouterLink></template></div></td>
          </tr>
          <tr v-if="!history.length"><td colspan="6" class="empty">还没有抽取记录。解析文档后在上方预检并开始抽取。</td></tr>
        </tbody>
      </table></div>
    </el-card>
    <SimulationPanel v-if="writable && ['admin', 'technical'].includes(orgAccess.role)" :task-id="taskId" :extractions="history" @changed="load" />
    <ConfidentialPanel v-if="!accessLost" :task-id="taskId" />
    <el-card v-if="exportList" class="section" shadow="never" body-class="flush">
      <template #header><div class="section-title"><h3>导出文件</h3><span class="hint">在线预览按 Word 版式转换成页面，转换只在第一次打开时进行</span></div></template>
      <div class="table-scroll flat"><table class="data-table"><caption class="sr-only">已发布的导出文件</caption>
        <thead><tr><th>导出文件</th><th>类型</th><th>状态</th><th>发布时间</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="item in exportList" :key="item.id">
            <td>{{ item.file.name ?? "导出文件" }}<div class="hint mono">{{ item.id }}</div></td>
            <td><span class="tag" :class="item.mode === 'final_section' ? 'primary' : ''">{{ exportModes[item.mode] ?? item.mode }}</span></td>
            <td><span class="tag" :class="item.validity === 'current' ? 'success' : 'danger'">{{ item.validity === "current" ? "当前有效" : "已失效" }}</span></td>
            <td class="hint">{{ formatTime(item.released_at) }}</td>
            <td><div class="row-actions"><ExportPreview v-if="item.validity === 'current'" :export-id="item.id" :title="item.file.name ?? '导出文件'" /><el-button size="small" link type="primary" :disabled="item.validity !== 'current'" @click="downloadExport(item)">下载</el-button></div></td>
          </tr>
          <tr v-if="!exportList.length"><td colspan="5" class="empty">还没有发布的导出文件。</td></tr>
        </tbody>
      </table></div>
    </el-card>
    </div>
    <aside class="task-side">
      <el-card class="section" shadow="never">
        <template #header><h3>任务进度</h3></template>
        <el-steps :active="step" finish-status="success" direction="vertical" class="steps">
          <el-step title="上传招标文件" :description="documents.length ? `${documents.length} 份文档` : '尚未上传'" />
          <el-step title="解析文档" :description="parsedCount ? `${parsedCount} 份已解析` : '等待解析'" />
          <el-step title="抽取要求" :description="history.length ? `${history.length} 次抽取` : '等待抽取'" />
          <el-step title="审阅响应与初稿" description="在抽取历史中进入审阅" />
        </el-steps>
      </el-card>
      <el-card v-if="task" class="section" shadow="never">
        <template #header><h3>任务信息</h3></template>
        <dl class="kv">
          <dt>招标编号</dt><dd>{{ display(task.tender_number) }}</dd>
          <dt>截止时间</dt><dd>{{ task.deadline ? formatTime(task.deadline) : "未知" }}</dd>
          <dt>预算记录</dt><dd>{{ display(task.budget_usd) }} USD</dd>
          <dt>外发遮挡</dt><dd>{{ task.model_redaction_enabled === false ? "已关闭" : "已开启" }}</dd>
        </dl>
      </el-card>
      <JobPanel :job-id="jobId" :writable="writable" @finished="load" />
    </aside>
  </div>
</template>
<style scoped>
.task-layout { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 0 20px; align-items: start; }
.task-main { min-width: 0; }
.task-side { position: sticky; top: 76px; }
.task-side h3 { margin: 0; }
.upload { display: flex; gap: 12px; align-items: stretch; margin-bottom: 16px; }
.dropzone { flex: 1; display: flex; align-items: center; gap: 12px; padding: 12px 16px; border: 1px dashed var(--el-color-primary-light-5); border-radius: 8px; background: var(--el-color-primary-light-9); color: var(--el-color-primary); cursor: pointer; position: relative; }
.dropzone:hover, .dropzone:focus-within { border-color: var(--el-color-primary); }
.dropzone.chosen { border-style: solid; }
.dropzone-text { display: flex; flex-direction: column; color: var(--text); min-width: 0; }
.dropzone-text strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.upload > .el-button { align-self: center; }
.receipt { margin-bottom: 12px; }
.doc-form .el-select { width: 100%; }
.doc-form .el-form-item { margin-bottom: 8px; }
.doc-meta { color: var(--muted); margin: 0 0 4px; }
.action-bar { margin-bottom: 0; }
.action-bar .el-dropdown { margin: 0; }
.action-bar .hint { margin-left: 4px; }
.extract-row { display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; }
.extract-row .level { flex: 0 1 360px; margin-bottom: 0; }
.extract-row .level .el-select { width: 100%; }
.preview-box { display: flex; flex-direction: column; gap: 4px; background: var(--surface-muted); padding: 10px 14px; border-radius: 6px; margin-top: 14px; }
.steps { height: 280px; }
.row-actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; white-space: nowrap; }
.flat { border: none; border-radius: 0; }
:deep(.flush) { padding: 0; }
@media (max-width: 1200px) { .task-layout { grid-template-columns: minmax(0, 1fr); } .task-side { position: static; } }
</style>
