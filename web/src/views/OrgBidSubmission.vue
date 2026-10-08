<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute } from "vue-router";
import TaskNavigation from "../components/TaskNavigation.vue";
import JobPanel from "../components/JobPanel.vue";
import BidSignatureResults from "../components/BidSignatureResults.vue";
import BidOutboundAuthorization from "../components/BidOutboundAuthorization.vue";
import BidReviewRuns from "../components/BidReviewRuns.vue";
import { formatTime, orgRequest } from "../org.js";
import { fileKinds, mib, preparationStatuses, reviewAuthorityInfo, reviewError, safeWarning, submissionPath, submissionStates } from "../bid-review.js";
const route = useRoute();
const taskId = computed(() => String(route.params.taskId)), submissionId = computed(() => String(route.params.submissionId));
const detail = ref(null), preview = ref(null), consent = ref(false), writable = ref(false), busy = ref(false), error = ref(""), activeJob = ref(""), retry = ref(false);
const submission = computed(() => detail.value?.submission), documents = computed(() => submission.value?.documents ?? submission.value?.files ?? []);
const canPrepare = computed(() => writable.value && submission.value?.state === "uploaded" && !["queued", "running"].includes(detail.value?.preparation?.status));
const expired = computed(() => preview.value && Date.parse(preview.value.expires_at) <= Date.now());
const candidatesBusy = ref(false);
const owner = ref(false), runWritable = ref(false), authorizationEpoch = ref(0);
let serial = 0, controller, requestId = crypto.randomUUID(), expiryTimer;
function invalidate() { clearTimeout(expiryTimer); preview.value = null; consent.value = false; requestId = crypto.randomUUID(); }
async function reread() {
  const result = await orgRequest("GET", `/bid-submissions/${encodeURIComponent(submissionId.value)}`, undefined, { signal: controller?.signal });
  if (result.data.submission?.task_id !== taskId.value || result.data.submission?.id !== submissionId.value) throw new Error("submission mismatch");
  detail.value = result.data; activeJob.value = result.data.preparation?.job_id ?? "";
}
async function load() {
  const run = ++serial; controller?.abort(); controller = new AbortController(); detail.value = null; writable.value = false; busy.value = false; candidatesBusy.value = false; activeJob.value = ""; retry.value = false; error.value = ""; invalidate();
  owner.value = false;
  runWritable.value = false;
  try { const allowed = await reviewAuthorityInfo(taskId.value); if (run !== serial) return; writable.value = allowed.writable; runWritable.value = allowed.runWritable; owner.value = allowed.owner; await reread(); }
  catch (exc) { if (exc.name !== "AbortError") error.value = reviewError(exc); }
}
async function inspect() {
  const run = serial;
  busy.value = true; error.value = ""; invalidate();
  try {
    const result = await orgRequest("POST", `${submissionPath(taskId.value)}/${encodeURIComponent(submissionId.value)}/prepare`, { request_id: requestId, submission_id: submissionId.value, dry_run: true }, { signal: controller?.signal });
    if (run !== serial) return;
    preview.value = result.data;
    expiryTimer = setTimeout(() => { invalidate(); error.value = "预检已过期，请重新预检"; }, Math.max(0, Date.parse(result.data.expires_at) - Date.now()));
  } catch (exc) { if (run === serial && exc.name !== "AbortError") error.value = reviewError(exc); }
  finally { if (run === serial) busy.value = false; }
}
async function submit() {
  const run = serial;
  if (!consent.value || !preview.value || expired.value || preview.value.admission_blockers.length) return;
  busy.value = true; error.value = "";
  const body = { request_id: requestId, submission_id: submissionId.value, dry_run: false, retry: retry.value, expected_input_hash: preview.value.input_hash, preflight_token: preview.value.preflight_token };
  try { const result = await orgRequest("POST", `${submissionPath(taskId.value)}/${encodeURIComponent(submissionId.value)}/prepare`, body, { signal: controller?.signal }); if (run !== serial) return; activeJob.value = result.data.job_id; invalidate(); await reread(); }
  catch (exc) { if (run !== serial || exc.name === "AbortError") return; error.value = reviewError(exc); if (exc.status === 409 || exc.code?.includes("preflight")) invalidate(); }
  finally { if (run === serial) busy.value = false; }
}
async function finished() { invalidate(); try { await reread(); } catch (exc) { error.value = reviewError(exc); } }
async function moreCandidates() {
  const current = detail.value, cursor = current?.signing_candidates_next_cursor, run = serial;
  if (cursor === null || cursor === undefined || candidatesBusy.value) return;
  candidatesBusy.value = true;
  try {
    const result = await orgRequest("GET", `/bid-submissions/${encodeURIComponent(submissionId.value)}/signing-candidates?cursor=${cursor}&limit=20`, undefined, { signal: controller?.signal });
    if (run !== serial || detail.value !== current) return;
    const known = new Set(current.signing_candidates.map(item => item.id));
    if (!Array.isArray(result.data.items) || result.data.items.length > 20 || result.data.total !== current.signing_candidate_count || (result.data.next_cursor !== null && (!Number.isInteger(result.data.next_cursor) || result.data.next_cursor <= cursor))) throw new Error("invalid candidate page");
    for (const item of result.data.items) {
      if (!documents.value.some(doc => doc.id === item.document_id) || known.has(item.id)) throw new Error("invalid candidate parent");
      known.add(item.id);
    }
    detail.value.signing_candidates.push(...result.data.items); detail.value.signing_candidates_next_cursor = result.data.next_cursor;
  } catch (exc) { if (run === serial && exc.name !== "AbortError") error.value = reviewError(exc); }
  finally { if (run === serial) candidatesBusy.value = false; }
}
watch([taskId, submissionId], load, { immediate: true });
window.addEventListener("bid:org-reset", load);
onBeforeUnmount(() => { serial++; controller?.abort(); invalidate(); detail.value = null; window.removeEventListener("bid:org-reset", load); });
</script>
<template>
  <TaskNavigation :task-id="taskId" />
  <div class="page-heading"><div><h2>标书提交与本地准备</h2><RouterLink :to="`/org/tasks/${taskId}/bid-submissions`">返回提交列表</RouterLink></div><el-button :disabled="busy" @click="load">刷新提交状态</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" class="section" />
  <template v-if="submission">
    <el-card class="section" shadow="never"><template #header><h3>固定提交版本 {{ submission.revision }}</h3></template>
      <dl class="kv"><dt>提交编号</dt><dd class="mono">{{ submission.id }}</dd><dt>状态</dt><dd>{{ submissionStates[submission.state] }}</dd><dt>上传时间</dt><dd>{{ formatTime(submission.created_at) }}</dd><dt>清单哈希</dt><dd class="mono">{{ submission.manifest_sha256 }}</dd></dl>
      <el-table role="table" :data="documents" aria-label="固定文件清单">
        <el-table-column label="文件" min-width="220"><template #default="{ row, $index }">{{ row.role === 'tender' ? '招标' : '投标' }}文件 {{ $index + 1 }}<div class="hint mono">{{ row.id }}</div></template></el-table-column>
        <el-table-column label="材料类型"><template #default="{ row }">{{ fileKinds[row.kind] }}</template></el-table-column>
        <el-table-column label="格式" width="90"><template #default="{ row }">{{ row.media_type === 'application/pdf' ? 'PDF' : 'DOCX' }}</template></el-table-column>
        <el-table-column label="大小" width="120"><template #default="{ row }">{{ mib(row.size_bytes) }}</template></el-table-column>
      </el-table>
    </el-card>
    <el-card v-if="canPrepare" class="section" shadow="never"><template #header><h3>本地准备</h3></template>
      <p>解析与分页渲染生成固定页面清单，合计最多 1,000 页，并对原件运行本地数字签名验证与签章条款扫描；不运行 OCR 或模型。准备费用为 0。</p>
      <el-button type="primary" plain :loading="busy" @click="inspect">预检本地准备</el-button>
      <div v-if="preview" class="preview section" role="status">
        <p>本地处理 · 费用 0 · 预检有效至 {{ formatTime(preview.expires_at) }}</p>
        <p>输入哈希 <code>{{ preview.input_hash }}</code> · 预检未创建作业或保存页面。</p>
        <el-alert v-for="blocker in preview.admission_blockers" :key="blocker" :title="reviewError({code: blocker})" type="warning" :closable="false" />
        <el-checkbox v-if="['failed', 'cancelled'].includes(detail.preparation?.status)" v-model="retry" :disabled="busy">明确重试本地准备</el-checkbox>
        <el-checkbox v-model="consent" :disabled="busy">确认此固定版本，开始本地准备</el-checkbox>
        <div class="actions section"><el-button type="primary" :loading="busy" :disabled="!consent || expired || preview.admission_blockers.length > 0" @click="submit">提交本地准备</el-button></div>
      </div>
    </el-card>
    <p v-if="detail.preparation" role="status">准备状态：{{ preparationStatuses[detail.preparation.status] }}</p>
    <JobPanel :job-id="activeJob" :writable="writable" assessment-mode bid-preparation @finished="finished" />
    <BidSignatureResults :validations="detail.signature_validations" :candidates="detail.signing_candidates" :candidate-count="detail.signing_candidate_count" :next-cursor="detail.signing_candidates_next_cursor ?? null" :busy="candidatesBusy" @more="moreCandidates" />
    <el-card class="section" shadow="never"><template #header><h3>页面清单</h3></template>
      <p class="hint">PDF 使用原件页码；DOCX 使用固定转换后的页码，原文引用仍使用结构块。图片页未做 OCR；签名字段数量仅代表发现字段。</p>
      <el-table role="table" :data="detail.inventory" aria-label="页面清单" empty-text="准备完成后显示固定页面清单">
        <el-table-column label="文件" min-width="220"><template #default="{ row }">{{ fileKinds[documents.find(doc => doc.id === row.document_id)?.kind] ?? '文件' }}<div class="hint mono">{{ row.document_id }}</div></template></el-table-column>
        <el-table-column prop="page_count" label="页数" width="75" />
        <el-table-column prop="text_pages" label="文本页" width="85" />
        <el-table-column prop="image_pages" label="图片页" width="85" />
        <el-table-column prop="signature_fields" label="签名字段" width="100" />
        <el-table-column label="解析警示" min-width="220"><template #default="{ row }"><p v-for="(warning, index) in row.parsing_warnings" :key="index">{{ safeWarning(warning) }}</p><span v-if="!row.parsing_warnings.length">无警示</span></template></el-table-column>
      </el-table>
    </el-card>
    <BidOutboundAuthorization :task-id="taskId" :submission-id="submissionId" :owner="owner" :prepared="submission.state === 'prepared'" @changed="authorizationEpoch++" />
    <BidReviewRuns :task-id="taskId" :submission-id="submissionId" :writable="runWritable" :prepared="submission.state === 'prepared'" :authorization-epoch="authorizationEpoch" />
  </template>
</template>
<style scoped>
.preview { padding:16px;background:var(--surface-muted);border-radius:8px; }
.preview .el-checkbox { display:block; }
.mono { overflow-wrap:anywhere; }
h3 { margin:0; }
</style>
