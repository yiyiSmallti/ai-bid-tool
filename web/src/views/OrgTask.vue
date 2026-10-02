<script setup>
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { display, downloadOriginal, errorText, orgAccess, orgRequest, recalled, remember } from "../org.js";
import JobPanel from "../components/JobPanel.vue";
const route = useRoute(), taskId = route.params.taskId;
const task = ref(null), documents = ref([]), history = ref([]), parseJobs = ref([]), selected = ref("");
const file = ref(null), preview = ref(null), reasoning = ref(""), warnings = ref([]), error = ref(""), busy = ref(false), receipt = ref(null);
const reasoningLevels = ref([]);
const jobId = ref(recalled(`task.${taskId}`)?.jobId ?? null);
const writable = computed(() => orgAccess.role && orgAccess.role !== "viewer");
const currentDocument = computed(() => documents.value.find((item) => item.id === selected.value));
async function load() {
  try {
    const results = await Promise.all([orgRequest("GET", `/tasks/${taskId}`), orgRequest("GET", `/tasks/${taskId}/documents`), orgRequest("GET", `/tasks/${taskId}/extractions`), orgRequest("GET", `/tasks/${taskId}/jobs?kind=parse`)]);
    task.value = results[0].data; documents.value = results[1].items; history.value = results[2].items; parseJobs.value = results[3].items;
    if (!selected.value) selected.value = documents.value.find(d => d.id === recalled(`task.${taskId}`)?.documentId)?.id ?? documents.value[0]?.id ?? "";
    if (!jobId.value) jobId.value = parseJobs.value.find(j => ["queued", "running"].includes(j.status))?.id ?? null;
  } catch (exc) { error.value = errorText(exc); }
}
function rememberIds() { remember(`task.${taskId}`, { documentId: selected.value, jobId: jobId.value }); }
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
onMounted(async () => { await load(); if (writable.value && selected.value) await run("extract", true); });
</script>
<template>
  <RouterLink to="/org/tasks">返回任务列表</RouterLink>
  <h2>{{ task?.name ?? "任务" }}</h2>
  <p v-if="task">招标编号 {{ display(task.tender_number) }} · 截止 {{ display(task.deadline) }} · 预算记录 {{ display(task.budget_usd) }} USD</p>
  <p v-if="error" role="alert" class="error">{{ error }}</p>
  <form v-if="writable" class="panel" @submit.prevent="upload">
    <label>招标文件（PDF / DOCX）<input type="file" accept=".pdf,.docx" required @change="file = $event.target.files[0]" /></label>
    <p><button :disabled="busy" class="primary">{{ busy ? "处理中…" : "上传文件" }}</button></p>
    <p class="hint">上传后单独开始解析；格式、大小与页数限制由服务器校验。</p>
    <p v-if="receipt" role="status">{{ receipt.duplicate ? "同内容文件已存在，已恢复原文档" : "上传成功" }}：{{ receipt.name }} · {{ receipt.id }}</p>
  </form>
  <section class="panel"><h3>任务文档与解析</h3>
    <label>文档<select :disabled="busy" v-model="selected" @change="changeDocument"><option value="" disabled>选择文档</option><option v-for="doc in documents" :key="doc.id" :value="doc.id">{{ doc.name }} · {{ doc.status }}</option></select></label>
    <p v-if="currentDocument">状态 {{ currentDocument.status }} · 页数 {{ display(currentDocument.page_count) }} · 引用方式 {{ currentDocument.citation_mode }}</p>
    <div v-if="selected" class="actions"><button @click="download">下载招标原件</button><template v-if="writable"><button :disabled="busy" @click="run('parse')">开始解析</button><button :disabled="busy" @click="run('parse', false, true)">显式重试解析</button></template></div>
    <details><summary>解析作业历史（{{ parseJobs.length }}）</summary><ul><li v-for="job in parseJobs" :key="job.id"><button @click="jobId = job.id; rememberIds()">{{ job.id }} · {{ job.status }}</button></li></ul></details>
  </section>
  <section v-if="writable && selected" class="panel"><h3>要求抽取</h3>
    <button :disabled="busy" @click="run('extract', true)">抽取预检</button>
    <label v-if="reasoningLevels.length">官方推理档位<select :disabled="busy" v-model="reasoning" @change="preview = null"><option value="">服务端默认</option><option v-for="level in reasoningLevels" :key="level.name" :value="level.name">{{ level.label || level.name }} · {{ level.name }}{{ level.default ? '（默认）' : '' }}</option></select></label>
    <p v-if="preview && !preview.reasoning_levels.length">此模型未提供可选推理档位</p>
    <p v-if="preview">预检 parsed：{{ preview.parsed }} · 档位 {{ display(preview.reasoning) }} · {{ preview.estimated_cost_usd == null ? "费用暂不可估" : `${preview.estimated_cost_usd} USD` }}</p>
    <p class="hint">模型、单价、时长未返回时均为未知；预检不锁定费用。</p>
    <div class="actions"><button :disabled="busy || !preview || currentDocument?.status !== 'parsed'" class="primary" @click="run('extract')">开始抽取（可能产生费用）</button><button :disabled="busy || !preview || currentDocument?.status !== 'parsed'" @click="run('extract', false, true)">显式重试抽取（可能产生费用）</button></div>
  </section>
  <p v-for="warning in warnings" :key="warning" class="notice">{{ warning }}</p>
  <JobPanel :job-id="jobId" :writable="writable" @finished="load" />
  <div class="table-scroll" tabindex="0"><table><caption>抽取历史：选择成功的固定 job 进入审阅</caption><thead><tr><th>文档 / Job</th><th>档位 / 模型</th><th>状态</th><th>开始 / 结束</th><th>保存 / 拒绝 / tokens</th><th>操作</th></tr></thead><tbody>
    <tr v-for="entry in history" :key="entry.job_id"><td>{{ documents.find(d => d.id === entry.document_id)?.name ?? entry.document_id }}<br /><code>{{ entry.job_id }}</code></td><td>{{ display(entry.reasoning) }} / {{ display(entry.model) }}</td><td>{{ entry.status }}{{ entry.latest ? ' · latest' : '' }}<p v-if="entry.error" class="error">{{ display(entry.error) }}</p></td><td>{{ display(entry.created_at) }} / {{ display(entry.finished_at) }}</td><td>{{ display(entry.saved) }} / {{ display(entry.rejected) }} / {{ display(entry.tokens) }}</td><td><button @click="jobId = entry.job_id; rememberIds()">查看作业</button><template v-if="entry.status === 'succeeded'"><RouterLink :to="`/org/tasks/${taskId}/review?job=${entry.job_id}`">审阅要求</RouterLink> <RouterLink :to="`/org/tasks/${taskId}/drafts?job=${entry.job_id}`">查看初稿</RouterLink></template></td></tr>
  </tbody></table></div>
</template>
