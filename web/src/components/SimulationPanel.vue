<script setup>
import { MagicStick, Search } from "@element-plus/icons-vue";
import { computed, ref, watch } from "vue";
import { confirmAction, errorText, orgRequest } from "../org.js";
import JobPanel from "./JobPanel.vue";
// Demonstration only: the model proposes a product per hardware item, the worker searches and
// fetches its official page and keeps verbatim parameter quotes as simulated materials.
const props = defineProps({ taskId: { type: String, required: true }, extractions: { type: Array, default: () => [] } });
const emit = defineEmits(["changed"]);
const succeeded = computed(() => props.extractions.filter((entry) => entry.status === "succeeded"));
const extraction = ref(""), preview = ref(null), jobId = ref(""), result = ref(null), busy = ref(false), error = ref("");
watch(succeeded, (rows) => { if (!extraction.value && rows.length) extraction.value = rows.find((row) => row.latest)?.job_id ?? rows[0].job_id; }, { immediate: true });
const kinds = { hardware: "硬件", software: "软件", service: "服务" };
const statuses = { not_proposed: "模型未给出结果", quoted: "已录入", not_hardware: "软件或服务，不拟投产品", no_product: "未给出候选厂商", no_search_result: "未搜到候选厂商的官网页面", no_page: "官网页面打不开或未介绍此类产品", no_parameters: "页面没有相关参数" };
const attempts = { quoted: "已采用", no_product: "非此类产品页", no_parameters: "无相关参数", fetch_timeout: "超时", fetch_transport_failed: "连接失败", http_status_denied: "页面返回错误", page_too_short: "页面无正文", page_unreadable: "无法解析" };
const statusTag = { quoted: "success", no_page: "warning", no_parameters: "warning", no_search_result: "warning", no_product: "warning" };
async function check() {
  busy.value = true; error.value = ""; preview.value = null;
  try { preview.value = (await orgRequest("POST", `/tasks/${props.taskId}/product-simulations`, { extraction_job_id: extraction.value, dry_run: true })).data; }
  catch (exc) { error.value = errorText(exc); } finally { busy.value = false; }
}
async function start() {
  if (!(await confirmAction(`模型将为 ${preview.value.items.length} 个采购项中的硬件提出候选厂商，搜索并抓取官网页面，摘取参数后作为“模拟材料”录入资源库并固定到本任务。模拟材料只用于演示，不能进入正式件。继续？`, "开始模拟拟投"))) return;
  busy.value = true; error.value = ""; result.value = null;
  try {
    const data = (await orgRequest("POST", `/tasks/${props.taskId}/product-simulations`, { extraction_job_id: extraction.value, expected_input_hash: preview.value.input_hash, retry: true })).data;
    jobId.value = data.job_id;
  } catch (exc) { error.value = errorText(exc); } finally { busy.value = false; }
}
function finished(job) {
  result.value = job.status === "succeeded" ? job.result : null;
  if (job.status === "succeeded") {
    // New simulated selections must refresh any cached material marks for this task.
    window.dispatchEvent(new CustomEvent("bid:task-materials-changed", { detail: { taskId: props.taskId } }));
    emit("changed");
  }
}
</script>
<template>
  <el-card class="section" shadow="never">
    <template #header><div class="section-title"><h3>模拟拟投（演示）</h3><el-tag type="warning" effect="plain">模拟材料不能进入正式件</el-tag></div></template>
    <p class="hint">按招标表格中的采购项，由模型为硬件提出候选厂商，搜索并抓取厂商官网页面，从页面读取产品型号，只保留页面上逐字出现的型号和参数，录入资源库并固定到本任务，之后即可起草响应。软件开发和服务类项目不拟投产品。</p>
    <el-form label-position="top" class="sim-row" @submit.prevent="check">
      <el-form-item label="抽取记录" class="sim-extraction">
        <el-select v-model="extraction" placeholder="选择成功的抽取" :disabled="busy" @change="preview = null">
          <el-option v-for="entry in succeeded" :key="entry.job_id" :value="entry.job_id" :label="`${entry.reasoning ?? '默认档位'} · ${entry.job_id.slice(0, 8)}${entry.latest ? '（最新）' : ''}`" />
        </el-select>
      </el-form-item>
      <el-button native-type="submit" :icon="Search" :loading="busy && !preview" :disabled="busy || !extraction">模拟预检</el-button>
    </el-form>
    <div v-if="preview" class="preview-box">
      <span>可模拟采购项 {{ preview.items.length }} 个，涉及技术要求 {{ preview.requirement_count }} 条</span>
      <span v-if="preview.admission_blocker" class="error">当前不能运行：{{ preview.admission_blocker === "search_unavailable" ? "未配置厂商搜索服务" : "未配置网页抓取策略" }}</span>
      <details><summary>查看采购项</summary><ol class="items"><li v-for="item in preview.items" :key="item.key">{{ item.name }}<span class="hint"> · {{ item.requirements }} 条要求</span></li></ol></details>
    </div>
    <div class="actions"><el-button type="primary" :icon="MagicStick" :disabled="busy || !preview || !!preview.admission_blocker || !preview.items.length" @click="start">开始模拟拟投</el-button></div>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" />
    <JobPanel v-if="jobId" :job-id="jobId" :writable="true" @finished="finished" />
    <div v-if="result" class="table-scroll">
      <table class="data-table"><caption class="sr-only">模拟拟投结果</caption>
        <thead><tr><th>采购项</th><th>类型</th><th>拟投型号</th><th>结果</th><th class="num">参数</th><th>来源</th></tr></thead>
        <tbody>
          <tr v-for="item in result.items" :key="item.key">
            <td>{{ item.name }}</td>
            <td>{{ kinds[item.kind] ?? item.kind }}</td>
            <td>{{ item.model ? `${item.vendor} ${item.model}` : "—" }}</td>
            <td><span class="tag" :class="statusTag[item.status]">{{ statuses[item.status] ?? item.status }}</span></td>
            <td class="num">{{ item.parameters?.length ?? 0 }}</td>
            <td class="source">
              <a v-if="item.url" :href="item.url" target="_blank" rel="noopener noreferrer">{{ item.url }}</a>
              <ul v-else-if="item.tried?.length" class="tried"><li v-for="attempt in item.tried" :key="attempt.url">{{ attempt.vendor }}：{{ attempts[attempt.result] ?? attempt.result }}</li></ul>
              <span v-else class="hint">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="result" class="hint">共录入 {{ result.counts.products }} 个模拟产品、{{ result.counts.parameters }} 条参数。到审阅页运行模型起草即可引用这些模拟材料；它们在材料和证据上都标有“模拟”。</p>
  </el-card>
</template>
<style scoped>
.sim-row { display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; }
.sim-extraction { flex: 0 1 360px; margin-bottom: 0; }
.sim-extraction .el-select { width: 100%; }
.preview-box { display: flex; flex-direction: column; gap: 4px; background: var(--surface-muted); padding: 10px 14px; border-radius: 6px; margin-top: 14px; }
.items { margin: 6px 0; padding-left: 20px; columns: 2; }
.source { max-width: 320px; overflow-wrap: anywhere; font-size: 13px; }
.tried { margin: 0; padding-left: 16px; color: var(--muted); }
</style>
