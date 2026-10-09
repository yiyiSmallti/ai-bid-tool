<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";
import TaskNavigation from "../components/TaskNavigation.vue";
import JobPanel from "../components/JobPanel.vue";
import BidReportRow from "../components/BidReportRow.vue";
import { orgAccess, formatTime } from "../org.js";
import { reviewError } from "../bid-review.js";
import { checkedReportPage, downloadReport, invalidReport, reportRequest, reportSections } from "../bid-review-report.js";
const route = useRoute();
const taskId = computed(() => String(route.params.taskId)), reviewId = computed(() => String(route.params.reviewId));
const metadata = ref(null), snapshotId = ref(""), history = ref([]), historyCursor = ref(null), preview = ref(null), consent = ref(false), retry = ref(false), busy = ref(false), error = ref(""), jobId = ref(""), jobAttempt = ref(0), downloaded = ref(false);
const pages = reactive({});
const canRender = computed(() => metadata.value?.projection === "protected" && ["admin", "bidder"].includes(orgAccess.role));
const stale = computed(() => Boolean(snapshotId.value && metadata.value?.current_decisions_snapshot_sha256 && metadata.value.current_decisions_snapshot_sha256 !== metadata.value.decisions_snapshot_sha256));
const expired = computed(() => !preview.value || Date.parse(preview.value.expires_at) <= Date.now());
const artifacts = computed(() => metadata.value?.artifacts?.filter(row => row.format === "docx") ?? []);
const retryAvailable = computed(() => Boolean(preview.value && history.value.some(row => row.can_retry === true && row.input_hash === preview.value.input_hash && ["failed", "cancelled"].includes(row.artifact_status))));
const artifactStatus = value => ({ ready: "已发布", pending: "生成中", failed: "失败或已取消", cancelled: "已取消" })[value] ?? "尚未发布";
let epoch = 0, controller, expiryTimer, requestId = crypto.randomUUID();
const completedJobs = new Set();
function invalidate() { clearTimeout(expiryTimer); preview.value = null; consent.value = false; retry.value = false; requestId = crypto.randomUUID(); }
function clearPages() { metadata.value = null; for (const key of Object.keys(pages)) delete pages[key]; }
async function readSection(key, cursor = null, trail = [], token = epoch) {
  const chosen = snapshotId.value, query = new URLSearchParams({ section: key, limit: "50" });
  if (chosen) query.set("snapshot_id", chosen); if (cursor) query.set("cursor", cursor);
  const response = await reportRequest("GET", `/bid-reviews/${encodeURIComponent(reviewId.value)}/report?${query}`, undefined, { signal: controller.signal });
  if (token !== epoch || chosen !== snapshotId.value) return;
  checkedReportPage(response, taskId.value, reviewId.value, key, chosen || null);
  if (metadata.value && ["input_hash", "report_input_hash", "decisions_snapshot_sha256", "projection"].some(field => metadata.value[field] !== response.data[field])) { clearPages(); invalidate(); throw invalidReport(); }
  if (response.data.next_cursor && [cursor, ...trail].includes(response.data.next_cursor)) throw invalidReport();
  metadata.value = response.data; pages[key] = { rows: response.items, cursor, trail, next: response.data.next_cursor };
}
async function historyPage(more = false, token = epoch) {
  const query = new URLSearchParams({ limit: "50" }); if (more && historyCursor.value) query.set("cursor", historyCursor.value);
  const response = await reportRequest("GET", `/bid-reviews/${encodeURIComponent(reviewId.value)}/reports?${query}`, undefined, { signal: controller.signal });
  if (token !== epoch) return;
  if (response.data.review_id !== reviewId.value || response.data.task_id !== taskId.value || response.items.length > 50 || !(response.data.next_cursor === null || typeof response.data.next_cursor === "string") || response.items.some(row => typeof row.snapshot_id !== "string")) throw invalidReport();
  if (more && response.data.next_cursor === historyCursor.value) throw invalidReport();
  history.value = more ? [...history.value, ...response.items] : response.items; historyCursor.value = response.data.next_cursor;
}
async function operate(action) {
  const token = epoch; busy.value = true; error.value = "";
  try { await action(); } catch (exc) { if (token === epoch && exc.name !== "AbortError") { error.value = reviewError(exc); if (exc.status === 409) invalidate(); } }
  finally { if (token === epoch) busy.value = false; }
}
async function load() {
  const token = ++epoch; controller?.abort(); controller = new AbortController(); invalidate(); clearPages(); history.value = []; historyCursor.value = null; busy.value = true; error.value = ""; downloaded.value = false;
  try { await Promise.all([readSection("overall", null, [], token), historyPage(false, token)]); }
  catch (exc) { if (token === epoch && exc.name !== "AbortError") { clearPages(); error.value = reviewError(exc); } }
  finally { if (token === epoch) busy.value = false; }
}
async function chooseSnapshot() { jobId.value = ""; await load(); }
async function inspect() {
  if (!canRender.value) return;
  await operate(async () => {
    invalidate();
    // Rendering always freezes the decisions current at this new preview.
    const query = new URLSearchParams({ section: "overall", limit: "50" });
    const current = checkedReportPage(await reportRequest("GET", `/bid-reviews/${encodeURIComponent(reviewId.value)}/report?${query}`, undefined, { signal: controller.signal }), taskId.value, reviewId.value, "overall");
    await historyPage();
    const response = await reportRequest("POST", `/bid-reviews/${encodeURIComponent(reviewId.value)}/artifacts`, { request_id: requestId, report_id: reviewId.value, dry_run: true, expected_decisions_snapshot_sha256: current.data.decisions_snapshot_sha256 }, { signal: controller.signal });
    const fixed = response.data;
    if (fixed.report_id !== reviewId.value || fixed.decisions_snapshot_sha256 !== current.data.decisions_snapshot_sha256 || fixed.report_input_hash !== current.data.report_input_hash || fixed.budget?.input_hash !== fixed.input_hash || fixed.budget.planned_calls !== 0 || !Number.isFinite(Date.parse(fixed.expires_at)) || typeof fixed.preflight_token !== "string") throw invalidReport();
    preview.value = fixed; expiryTimer = setTimeout(() => { invalidate(); error.value = "预检已过期，请重新预检"; }, Math.max(0, Date.parse(fixed.expires_at) - Date.now()));
  });
}
async function submit() {
  if (!canRender.value || !consent.value || expired.value || preview.value.admission_blockers?.length || retryAvailable.value && !retry.value) return;
  await operate(async () => {
    const fixed = preview.value;
    const response = await reportRequest("POST", `/bid-reviews/${encodeURIComponent(reviewId.value)}/artifacts`, { request_id: requestId, report_id: reviewId.value, dry_run: false, retry: retryAvailable.value && retry.value, expected_input_hash: fixed.input_hash, preflight_token: fixed.preflight_token, expected_decisions_snapshot_sha256: fixed.decisions_snapshot_sha256 }, { signal: controller.signal });
    if (typeof response.data.job_id !== "string") throw invalidReport();
    completedJobs.delete(response.data.job_id); jobAttempt.value++; jobId.value = response.data.job_id; invalidate();
  });
}
async function finished(job) {
  if (completedJobs.has(jobId.value)) return;
  completedJobs.add(jobId.value);
  invalidate();
  if (job.status === "succeeded" && job.result?.snapshot_id) { snapshotId.value = job.result.snapshot_id; await load(); }
  else await operate(() => historyPage());
}
async function nextSection(key) { const page = pages[key]; await operate(() => readSection(key, page.next, [...page.trail, page.cursor])); }
async function previousSection(key) { const page = pages[key], trail = [...page.trail], cursor = trail.pop(); await operate(() => readSection(key, cursor, trail)); }
async function download(artifact) { downloaded.value = false; await operate(async () => { await downloadReport(artifact, taskId.value, reviewId.value, controller.signal); downloaded.value = true; }); }
watch([taskId, reviewId], () => { snapshotId.value = ""; jobId.value = ""; load(); }, { immediate: true });
window.addEventListener("bid:org-reset", load);
onBeforeUnmount(() => { epoch++; controller?.abort(); invalidate(); clearPages(); history.value = []; window.removeEventListener("bid:org-reset", load); });
</script>
<template>
  <TaskNavigation :task-id="taskId" />
  <div class="page-heading"><div><h2>标书检验报告</h2><p>控制台与 Word 使用相同章节和固定决定快照。</p></div><el-button :disabled="busy" @click="load">刷新报告</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" />
  <template v-if="metadata">
    <el-alert :title="metadata.advisory_statement" type="info" :closable="false" />
    <el-alert v-if="metadata.completion === 'partial'" title="本次检验部分完成；未覆盖与未确定项保留在报告内，不能据此确认全部符合。" type="warning" :closable="false" />
    <el-alert v-if="stale" title="此固定快照之后已有新的人工决定；历史内容保持不变，重新预检并渲染可生成新快照。" type="warning" :closable="false" data-testid="snapshot-stale" />
    <el-card class="section" shadow="never"><template #header><h3>报告快照</h3></template>
      <el-select v-model="snapshotId" aria-label="报告快照选择" style="width:min(540px,100%)" :disabled="busy" @change="chooseSnapshot"><el-option label="当前决定（待渲染）" value="" /><el-option v-for="row in history" :key="row.snapshot_id" :value="row.snapshot_id" :disabled="row.artifact_status !== 'ready'" :label="`${artifactStatus(row.artifact_status)} · ${formatTime(row.created_at)} · ${row.snapshot_id}`" /></el-select>
      <el-button v-if="historyCursor" :disabled="busy" @click="operate(() => historyPage(true))">更多历史快照</el-button>
      <p>快照编号 <code>{{ metadata.snapshot_id ?? '尚未创建；显式提交渲染后固定' }}</code></p><p>决定快照哈希 <code>{{ metadata.decisions_snapshot_sha256 }}</code></p><p>报告数据哈希 <code>{{ metadata.report_input_hash }}</code></p>
      <p v-if="metadata.projection !== 'protected'">当前显示隐私清理后的报告；受保护报告与 Word 需要有原件读取权限的商务成员或管理员本人操作。</p>
    </el-card>
    <el-card v-for="section in reportSections" :key="section.key" class="section" shadow="never" :data-testid="`report-section-${section.key}`"><template #header><div class="section-title"><h3>{{ section.title }}</h3><el-button v-if="!pages[section.key]" size="small" :disabled="busy" @click="operate(() => readSection(section.key))">查看本节</el-button></div></template>
      <template v-if="pages[section.key]"><BidReportRow v-for="(row,index) in pages[section.key].rows" :key="index" :row="row" /><p v-if="!pages[section.key].rows.length">本节没有逐项记录；不代表检验通过。</p><div class="actions"><el-button v-if="pages[section.key].trail.length" :disabled="busy" @click="previousSection(section.key)">上一页 {{ section.title }}</el-button><el-button v-if="pages[section.key].next" :disabled="busy" @click="nextSection(section.key)">下一页 {{ section.title }}</el-button></div></template>
      <p v-else class="hint">逐节读取，每页最多 50 条记录。</p>
    </el-card>
    <el-card v-if="canRender" class="section" shadow="never"><template #header><h3>Word 报告</h3></template><p>本地渲染费用为 0。预检不写入、不调用模型；明确提交后建立不可变快照与 Word 作业。</p>
      <el-button :loading="busy" @click="inspect">预检 Word 报告</el-button>
      <div v-if="preview" class="section" data-testid="report-render-preview"><p>费用 0 · 模型调用 0 · 有效至 {{ formatTime(preview.expires_at) }}</p><p>输入哈希 <code>{{ preview.input_hash }}</code></p><p>将固定决定哈希 <code>{{ preview.decisions_snapshot_sha256 }}</code> · 渲染器 {{ preview.renderer_identity }}</p><el-alert v-for="code in preview.admission_blockers" :key="code" :title="reviewError({code})" type="warning" :closable="false" /><template v-if="retryAvailable"><p>此决定快照已有失败或取消的渲染作业；再次预检已生成新的请求编号，需明确选择重试。</p><el-checkbox v-model="retry" :disabled="busy">明确重试此决定快照的失败或取消作业</el-checkbox></template><el-checkbox v-model="consent" :disabled="busy">确认此决定快照，提交 Word 渲染</el-checkbox><el-button type="primary" :loading="busy" :disabled="!consent || expired || Boolean(preview.admission_blockers?.length) || retryAvailable && !retry" @click="submit">提交 Word 渲染</el-button></div>
      <JobPanel :key="jobAttempt" :job-id="jobId" assessment-mode @finished="finished" />
      <p v-if="!artifacts.length">此处没有已发布的 Word 文件；运行中或失败的作业不能下载部分文件。</p>
      <div v-for="artifact in artifacts" :key="artifact.id" class="section"><p>固定 Word 文件 <code>{{ artifact.id }}</code> · {{ artifact.size_bytes }} 字节 · {{ artifact.renderer_identity }}</p><p>文件 SHA-256 <code>{{ artifact.sha256 }}</code></p><el-button :disabled="busy" @click="download(artifact)">下载 Word 报告</el-button></div><p v-if="downloaded" role="status">已核验文件长度与 SHA-256，开始下载 bid-review-report.docx。</p>
    </el-card>
  </template>
</template>
<style scoped>h3 { margin:0; } code { overflow-wrap:anywhere; } .section-title { display:flex;justify-content:space-between;align-items:center;gap:12px; } .el-checkbox { display:block; } .actions { flex-wrap:wrap; }</style>
