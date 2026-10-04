<script setup>
import { onBeforeUnmount, ref, watch } from "vue";
import { display, errorText, jobKinds, jobStatuses, jobTag, label, orgRequest, warningText } from "../org.js";
const props = defineProps({ jobId: String, writable: Boolean });
const emit = defineEmits(["finished"]);
const job = ref(null), warnings = ref([]), error = ref(""), cancelling = ref(false);
let timer, controller, failures = 0, finished = null;
const terminal = (status) => ["succeeded", "failed", "cancelled"].includes(status);
function schedule(delay) { clearTimeout(timer); if (!document.hidden) timer = setTimeout(load, delay); }
async function load() {
  clearTimeout(timer); controller?.abort();
  if (!props.jobId || document.hidden) return;
  controller = new AbortController();
  try {
    const result = await orgRequest("GET", `/jobs/${props.jobId}`, undefined, { signal: controller.signal });
    job.value = result.data; warnings.value = result.warnings; error.value = ""; failures = 0;
    if (terminal(job.value.status)) { if (finished !== props.jobId) { finished = props.jobId; emit("finished", job.value); } }
    else schedule(2000);
  } catch (exc) {
    if (exc.name === "AbortError") return;
    error.value = errorText(exc); failures++;
    if (!exc.status || exc.status === 429 || exc.status >= 500) schedule(Math.max(exc.retryAfter || 0, Math.min(30000, 2000 * 2 ** failures)));
  }
}
async function cancel() {
  cancelling.value = true;
  try { await orgRequest("POST", `/jobs/${props.jobId}/cancel`); await load(); }
  catch (exc) { error.value = errorText(exc); }
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
      <el-progress v-if="job && !terminal(job.status)" :percentage="100" :indeterminate="true" :show-text="false" :stroke-width="4" class="job-progress" />
    </div>
    <p class="hint">作业 ID <code>{{ jobId }}</code> · 取消后已发出的调用仍可能产生费用；断网不会自动取消或重新提交作业。</p>
    <el-alert v-if="job?.error" :title="display(job.error)" type="error" :closable="false" show-icon />
    <el-alert v-for="warning in warnings" :key="warning" :title="warningText(warning)" type="warning" :closable="false" role="note" class="section" />
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" />
    <details v-if="job?.result"><summary>作业结果、拒绝条目与费用</summary><pre>{{ display(job.result) }}</pre></details>
  </el-card>
</template>
<style scoped>
.job-line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.job-progress { flex: 1 1 160px; max-width: 240px; }
.section-title .actions { margin: 0; }
</style>
