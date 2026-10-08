<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { confirmAction, display, errorText, jobKinds, jobStatuses, jobTag, label, orgRequest, warningText } from "../org.js";
import { assessmentError } from "../assessments.js";
import { reviewError, safeWarning } from "../bid-review.js";
import { money } from "../api.js";
const props = defineProps({ jobId: String, writable: Boolean, assessmentMode: Boolean, bidPreparation: Boolean });
const emit = defineEmits(["finished"]);
const job = ref(null), warnings = ref([]), error = ref(""), cancelling = ref(false), actualCost = ref(null);
const preparation = computed(() => props.bidPreparation || job.value?.kind === "bid_review_prepare");
const bidReview = computed(() => job.value?.kind === "bid_review");
const assessment = computed(() => ["check", "score_rubric", "score", "bid_review"].includes(job.value?.kind));
const progress = computed(() => job.value?.progress ?? job.value?.result?.progress);
const stageLabel = computed(() => ({ whole_table: "正在分析整张评分表", sections: "第 1 步：确定分节和总分规则", items: "第 2 步：生成评分条目", validation: "正在核对引用与完整性", publication: "正在保存待审核规则" })[progress.value?.stage] ?? "正在生成评分规则");
const knownBatches = computed(() => progress.value?.stage === "items" && progress.value?.scheme === "two_stage" && /^[a-f0-9]{64}$/.test(progress.value?.sections_sha256 ?? "") && Number.isInteger(progress.value?.completed_batches) && progress.value.completed_batches >= 0 && Number.isInteger(progress.value?.total_batches) && progress.value.total_batches > 0 && progress.value.completed_batches <= progress.value.total_batches);
let timer, controller, failures = 0, finished = null;
const terminal = (status) => ["succeeded", "failed", "cancelled"].includes(status);
function schedule(delay) { clearTimeout(timer); if (!document.hidden) timer = setTimeout(load, delay); }
async function load() {
  clearTimeout(timer); controller?.abort();
  if (!props.jobId || document.hidden) return;
  controller = new AbortController();
  try {
    const result = await orgRequest("GET", `/jobs/${props.jobId}`, undefined, { signal: controller.signal, contractVersion: props.assessmentMode ? 4 : undefined });
    job.value = result.data; actualCost.value = result.cost; warnings.value = result.warnings; error.value = ""; failures = 0;
    if (terminal(job.value.status)) { if (finished !== props.jobId) { finished = props.jobId; emit("finished", job.value); } }
    else schedule(2000);
  } catch (exc) {
    if (exc.name === "AbortError") return;
    error.value = preparation.value || bidReview.value ? reviewError(exc) : errorText(exc); failures++;
    if (failures <= 5 && (!exc.status || exc.status === 429 || exc.status >= 500)) schedule(Math.max(exc.retryAfter || 0, Math.min(30000, 2000 * 2 ** failures)));
  }
}
async function cancel() {
  if (!await confirmAction(job.value?.kind === "bid_review_prepare" ? "取消本地准备后将不发布页面清单；可以明确重试。" : "取消后，已获准发出的调用仍可能产生费用。", "取消作业", "确认取消", true)) return;
  cancelling.value = true;
  try { await orgRequest("POST", `/jobs/${props.jobId}/cancel`, undefined, { contractVersion: props.assessmentMode ? 4 : undefined }); await load(); }
  catch (exc) { error.value = preparation.value ? reviewError(exc) : errorText(exc); }
  finally { cancelling.value = false; }
}
function visibility() { if (document.hidden) { clearTimeout(timer); controller?.abort(); } else if (!terminal(job.value?.status)) load(); }
watch(() => props.jobId, () => { job.value = null; finished = null; failures = 0; load(); }, { immediate: true });
document.addEventListener("visibilitychange", visibility);
onBeforeUnmount(() => { clearTimeout(timer); controller?.abort(); document.removeEventListener("visibilitychange", visibility); });
</script>
<template>
  <el-card v-if="jobId" class="section job-panel" shadow="never" aria-label="作业">
    <template #header>
      <div class="section-title">
        <h3>作业{{ job ? `：${label(jobKinds, job.kind)}` : "" }}</h3>
        <div class="actions">
          <el-button size="small" @click="load">刷新作业</el-button>
          <el-button v-if="writable && job && !terminal(job.status)" size="small" type="danger" plain :loading="cancelling" @click="cancel">取消作业</el-button>
        </div>
      </div>
    </template>
    <div class="job-line">
      <el-tag v-if="job" :type="jobTag[job.status] ?? 'info'" effect="light">{{ label(jobStatuses, job.status) }}</el-tag>
      <span v-if="job" role="status" data-testid="job-status">作业状态：{{ label(jobStatuses, job.status) }} · {{ label(jobKinds, job.kind) }} · 尝试 {{ display(job.attempts) }} 次 · 档位 {{ display(job.reasoning) }}</span>
      <span v-if="job && !terminal(job.status)" role="progressbar" :aria-label="job.kind === 'score_rubric' ? stageLabel : '正在处理作业'" :aria-valuenow="knownBatches ? progress.completed_batches : undefined" :aria-valuemin="knownBatches ? 0 : undefined" :aria-valuemax="knownBatches ? progress.total_batches : undefined" class="job-progress">{{ job.kind === 'score_rubric' ? stageLabel : '正在处理作业' }}<template v-if="knownBatches">，已完成 {{ progress.completed_batches }} / {{ progress.total_batches }} 批</template></span>
    </div>
    <p class="hint">作业 ID <code>{{ jobId }}</code> · {{ job?.kind === "bid_review_prepare" ? "本地准备费用为 0；取消后不发布部分页面清单。" : "取消后已发出的调用仍可能产生费用；断网不会自动取消或重新提交作业。" }}</p>
    <el-alert v-if="job?.error" :title="preparation || bidReview ? reviewError(job.error) : assessment ? assessmentError(job.error) : display(job.error)" type="error" :closable="false" show-icon />
    <el-alert v-for="warning in warnings" :key="warning" :title="preparation ? safeWarning(warning) : warningText(warning)" type="warning" :closable="false" role="note" class="section" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" />
    <template v-if="assessment && job">
      <p v-if="job.result?.completion === 'partial'">部分完成；请查看已保存报告中的未完成内容</p>
      <p v-if="job.result?.stop_reason">停止原因：{{ bidReview ? reviewError({ code: job.result.stop_reason }) : assessmentError({ code: job.result.stop_reason }) }}</p>
      <p v-if="actualCost">实际服务用量成本：{{ money(actualCost.usd, 'USD') }} · 实际平台扣费：{{ money(actualCost.charge, actualCost.billing_currency) }} · 计入任务预算：{{ money(actualCost.task_amount, actualCost.billing_currency) }}</p>
    </template>
    <details v-else-if="job?.result"><summary>作业结果、拒绝条目与费用</summary><pre>{{ display(job.result) }}</pre></details>
  </el-card>
</template>
<style scoped>
.job-line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.job-progress { flex: 1 1 160px; max-width: 240px; }
.section-title .actions { margin: 0; }
</style>
