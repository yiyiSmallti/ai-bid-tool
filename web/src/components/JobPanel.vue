<script setup>
import { onBeforeUnmount, ref, watch } from "vue";
import { display, errorText, orgRequest } from "../org.js";
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
  <section v-if="jobId" class="panel" aria-label="作业">
    <p>作业 ID：<code>{{ jobId }}</code></p>
    <p v-if="job" role="status" data-testid="job-status">作业状态：{{ job.status }} · {{ job.kind }} · 尝试 {{ display(job.attempts) }} · 档位 {{ display(job.reasoning) }}</p>
    <p v-if="job?.error" class="error">{{ display(job.error) }}</p>
    <p v-for="warning in warnings" :key="warning" class="notice">{{ warning }}</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <button @click="load">刷新作业</button>
    <button v-if="writable && job && !terminal(job.status)" :disabled="cancelling" @click="cancel">取消作业</button>
    <p class="hint">取消后已发出的调用仍可能产生费用。断网不会自动取消或重新提交作业。</p>
    <details v-if="job?.result"><summary>作业结果、拒绝条目与费用</summary><pre>{{ display(job.result) }}</pre></details>
  </section>
</template>
