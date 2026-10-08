<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { money } from "../api.js";
import { orgRequest, formatTime } from "../org.js";
import { reviewError } from "../bid-review.js";
import JobPanel from "./JobPanel.vue";
import BidReviewFindings from "./BidReviewFindings.vue";
const props = defineProps({ taskId: String, submissionId: String, writable: Boolean, prepared: Boolean, authorizationEpoch: Number, authority: Object });
const asOf = ref(new Date().toISOString().slice(0, 10)), preview = ref(null), consent = ref(false), retry = ref(false), busy = ref(false), error = ref(""), runs = ref([]), nextCursor = ref(null), selected = ref(null), jobId = ref("");
const obligations = ref([]), signing = ref([]), obligationCursor = ref(null), signingCursor = ref(null);
const path = computed(() => `/tasks/${encodeURIComponent(props.taskId)}/bid-reviews`);
const expired = computed(() => !preview.value || Date.parse(preview.value.expires_at) <= Date.now());
let generation = 0, selectionGeneration = 0, controller, expiryTimer, requestId = crypto.randomUUID();
function invalidate() { clearTimeout(expiryTimer); preview.value = null; consent.value = false; requestId = crypto.randomUUID(); }
async function list(more = false) {
  const token = generation, query = new URLSearchParams({ limit: "50" }); if (more && nextCursor.value) query.set("cursor", nextCursor.value);
  const response = await orgRequest("GET", `${path.value}?${query}`, undefined, { signal: controller?.signal });
  if (token !== generation) return;
  if (response.data.task_id !== props.taskId || response.items.length > 50 || response.items.some(row => row.task_id !== props.taskId)) throw new Error("review list parent mismatch");
  runs.value = more ? [...runs.value, ...response.items] : response.items; nextCursor.value = response.data.next_cursor;
}
async function section(id, name, more = false) {
  const token = generation, selectionToken = selectionGeneration, query = new URLSearchParams({ section: name, limit: "50" });
  const cursor = name === "obligations" ? obligationCursor : signingCursor, rows = name === "obligations" ? obligations : signing;
  if (more && cursor.value !== null) query.set("cursor", cursor.value);
  const response = await orgRequest("GET", `/bid-reviews/${encodeURIComponent(id)}?${query}`, undefined, { signal: controller?.signal });
  if (token !== generation || selectionToken !== selectionGeneration) return;
  if (response.data.run.id !== id || response.data.run.task_id !== props.taskId || response.data.run.submission_id !== props.submissionId || !Array.isArray(response.data[name]) || response.data[name].length > 50) throw new Error("review parent mismatch");
  selected.value = response.data.run; rows.value = more ? [...rows.value, ...response.data[name]] : response.data[name]; cursor.value = response.data.next_cursor;
}
async function show(id) {
  selectionGeneration++;
  error.value = ""; selected.value = null; obligations.value = []; signing.value = [];
  try { await Promise.all([section(id, "obligations"), section(id, "signing_requirements")]); }
  catch (exc) { if (exc.name !== "AbortError") error.value = reviewError(exc); }
}
async function inspect() {
  const token = generation; busy.value = true; error.value = ""; invalidate();
  try {
    const result = await orgRequest("POST", path.value, { request_id: requestId, submission_id: props.submissionId, assessment_date: asOf.value, scope: "uploaded_bid", review_slice: "compliance", dry_run: true }, { signal: controller?.signal });
    if (token !== generation) return;
    const fixed = result.data;
    if (fixed.input.task_id !== props.taskId || fixed.input.submission_id !== props.submissionId || !/^[a-f0-9]{64}$/.test(fixed.input.input_hash) || fixed.budget.input_hash !== fixed.input.input_hash || !Number.isFinite(Date.parse(fixed.expires_at))) throw new Error("review preview parent mismatch");
    preview.value = fixed; expiryTimer = setTimeout(() => { invalidate(); error.value = "预检已过期，请重新预检"; }, Math.max(0, Date.parse(fixed.expires_at) - Date.now()));
  } catch (exc) { if (token === generation && exc.name !== "AbortError") error.value = reviewError(exc); }
  finally { if (token === generation) busy.value = false; }
}
async function submit() {
  if (!consent.value || expired.value || preview.value.admission_blockers.length) return;
  const token = generation; busy.value = true; error.value = "";
  const body = { request_id: requestId, submission_id: props.submissionId, assessment_date: asOf.value, scope: "uploaded_bid", review_slice: "compliance", dry_run: false, retry: retry.value, expected_input_hash: preview.value.input.input_hash, preflight_token: preview.value.preflight_token };
  try { const result = await orgRequest("POST", path.value, body, { signal: controller?.signal }); if (token !== generation) return; jobId.value = result.data.job_id; invalidate(); await list(); }
  catch (exc) { if (token === generation && exc.name !== "AbortError") { error.value = reviewError(exc); if (exc.code === "queue_unavailable" && exc.payload?.data?.job_id) jobId.value = exc.payload.data.job_id; if (exc.status === 409 || exc.code?.includes("preflight")) invalidate(); } }
  finally { if (token === generation) busy.value = false; }
}
async function finished(job) {
  try { await list(); if (job.result?.review_id) await show(job.result.review_id); }
  catch (exc) { if (exc.name !== "AbortError") error.value = reviewError(exc); }
}
async function moreSection(name) { try { await section(selected.value.id, name, true); } catch (exc) { error.value = reviewError(exc); } }
async function moreRuns() { try { await list(true); } catch (exc) { if (exc.name !== "AbortError") error.value = reviewError(exc); } }
async function load() {
  generation++; controller?.abort(); controller = new AbortController(); invalidate(); busy.value = false; error.value = ""; runs.value = []; nextCursor.value = null; selected.value = null; obligations.value = []; signing.value = []; jobId.value = "";
  try { await list(); } catch (exc) { if (exc.name !== "AbortError") error.value = reviewError(exc); }
}
watch(() => [props.taskId, props.submissionId], load, { immediate: true });
watch(() => [props.authorizationEpoch, props.writable, props.prepared, asOf.value, retry.value], invalidate);
onBeforeUnmount(() => { generation++; controller?.abort(); invalidate(); });
const applicability = { applies: "适用", not_applicable: "不适用", alternative: "存在替代签章方式", unknown: "适用性未确定" };
const citationPosition = citation => {
  const page = `${citation.page_label === "rendered_docx" ? "固定转换第" : "原件第"} ${citation.page} 页`;
  const location = citation.location;
  if (!location) return page;
  const pieces = [location.label, location.section_path?.join(" / "), location.block_id ? `结构块 ${location.block_id}` : ""];
  if (location.paragraph) pieces.push(`段落 ${location.paragraph}`);
  if (location.table) pieces.push(`表 ${location.table} · 行 ${location.row} · 列 ${location.column}`);
  return [page, ...pieces.filter(Boolean)].join(" · ");
};
const marks = { company_seal: "单位公章", legal_representative_signature: "法定代表人签字", authorized_agent_signature: "授权代理人签字", personal_seal: "个人印章", date: "日期", seam_seal: "骑缝章", every_page_electronic_seal: "逐页电子章", pdf_digital_signature: "PDF 数字签名" };
</script>
<template>
  <el-card class="section" shadow="never"><template #header><h3>检验运行</h3></template>
    <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" />
    <p>抽取招标义务、核对已授权文本并形成签章检查清单。检查结论仅供人工审查；本次不生成评分或签章存在性结论。</p>
    <template v-if="writable && prepared">
      <el-form label-position="top"><el-form-item label="检验基准日期"><el-input v-model="asOf" type="date" aria-label="检验基准日期" :disabled="busy" /></el-form-item></el-form>
      <el-checkbox v-model="retry" :disabled="busy">明确重试检验运行</el-checkbox>
      <el-button type="primary" plain :loading="busy" @click="inspect">预检检验运行</el-button>
      <div v-if="preview" role="status" data-testid="review-preview" class="section">
        <p>预检未创建作业或调用模型。有效至 {{ formatTime(preview.expires_at) }}</p>
        <p>输入哈希 <code>{{ preview.input.input_hash }}</code></p>
        <p>预估平台扣费 {{ money(preview.budget.estimate.charge, preview.budget.estimate.billing_currency) }} · 计入任务预算 {{ money(preview.budget.estimate.task_amount, preview.budget.estimate.billing_currency) }} · 计划调用 {{ preview.budget.planned_calls ?? '未确定' }}</p>
        <el-alert v-for="code in preview.admission_blockers" :key="code" :title="reviewError({ code })" type="warning" :closable="false" />
        <p v-for="code in preview.uncovered_codes" :key="code">未覆盖：{{ reviewError({ code }) }}</p>
        <el-checkbox v-model="consent" :disabled="busy">确认费用与已授权文本，提交检验运行</el-checkbox>
        <div class="actions"><el-button type="primary" :loading="busy" :disabled="!consent || expired || preview.admission_blockers.length > 0" @click="submit">提交检验运行</el-button></div>
      </div>
    </template>
    <p v-else class="hint">检验运行需要已完成的本地准备与任务商务或技术成员权限。</p>
    <JobPanel :job-id="jobId" :writable="writable" assessment-mode @finished="finished" />
    <el-table :data="runs.filter(row => row.submission_id === submissionId)" role="table" aria-label="检验运行列表">
      <el-table-column label="运行"><template #default="{ row }"><code>{{ row.id }}</code></template></el-table-column>
      <el-table-column label="状态"><template #default="{ row }">{{ ({ queued: '排队中', running: '运行中', succeeded: '已完成', failed: '失败', cancelled: '已取消' })[row.status] ?? '状态未知' }}</template></el-table-column>
      <el-table-column label="覆盖"><template #default="{ row }">{{ row.completion === 'partial' ? '部分完成' : row.completion === 'complete' ? '已完成' : '待完成' }}</template></el-table-column>
      <el-table-column label="操作"><template #default="{ row }"><el-button size="small" @click="show(row.id)">查看检验结果</el-button></template></el-table-column>
    </el-table>
    <el-button v-if="nextCursor" @click="moreRuns">更多检验运行</el-button>
    <template v-if="selected">
      <BidReviewFindings :key="selected.id" :task-id="taskId" :run="selected" :authority="authority" :authorization-epoch="authorizationEpoch" />
      <p role="status">检验结果：{{ selected.completion === 'partial' ? '部分完成' : selected.completion === 'complete' ? '已完成' : '待完成' }} · 仅供辅助审查</p>
      <el-alert v-if="selected.validity === 'stale'" title="此检验结果的输入或授权已变化；请重新预检并运行，当前仅显示安全状态信息。" type="warning" :closable="false" />
      <p v-if="selected.coverage.tender_pages_total !== undefined">招标页面覆盖：已检验 {{ selected.coverage.tender_pages_assessed }} / 共 {{ selected.coverage.tender_pages_total }} 页；已授权 {{ selected.coverage.tender_pages_authorized }} 页</p>
      <p v-for="code in selected.uncovered_codes" :key="code">未覆盖：{{ reviewError({ code }) }}</p>
      <h4>招标义务与引用</h4>
      <el-table :data="obligations" role="table" aria-label="招标义务与引用"><el-table-column label="义务"><template #default="{ row }">{{ row.text }}<span v-if="row.starred"> · ★ 条款</span><span v-if="row.rejection_trigger"> · 废标条件</span></template></el-table-column><el-table-column label="原文位置"><template #default="{ row }"><p>{{ citationPosition(row.citation) }}</p><blockquote>{{ row.citation.quote }}</blockquote></template></el-table-column></el-table>
      <el-button v-if="obligationCursor !== null" @click="moreSection('obligations')">更多招标义务</el-button>
      <h4>签章检查清单与未解决位置</h4>
      <el-table :data="signing" role="table" aria-label="签章检查清单"><el-table-column label="签章要求"><template #default="{ row }"><p>{{ applicability[row.applicability] ?? '适用性未确定' }}</p><p>{{ row.mark_types.map(mark => marks[mark] ?? '待核对签章类型').join('、') }}</p><p v-if="row.date_required">需填写日期</p><template v-if="row.citation"><blockquote>{{ row.citation.quote }}</blockquote><p>招标{{ citationPosition(row.citation) }}</p></template><p v-else>原文位置未确定</p></template></el-table-column><el-table-column label="目标位置"><template #default="{ row }"><p v-for="(location, index) in row.required_locations" :key="index">{{ location.page ? `投标第 ${location.page} 页` : '投标页码未确定' }} · 位置未解决<span v-if="location.group_id"> · 骑缝组 {{ location.group_id }}</span></p><p v-if="!row.required_locations.length">{{ row.applicability === "not_applicable" ? "无适用签章位置" : "目标位置未确定" }}</p></template></el-table-column></el-table>
      <el-button v-if="signingCursor !== null" @click="moreSection('signing_requirements')">更多签章要求</el-button>
    </template>
  </el-card>
</template>
<style scoped>.el-checkbox { display:block; } code { overflow-wrap:anywhere; } h3 { margin:0; } blockquote { margin:8px 0; white-space:pre-wrap; }</style>
