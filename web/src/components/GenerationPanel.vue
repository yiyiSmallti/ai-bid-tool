<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { display, errorText, orgRequest } from "../org.js";
import JobPanel from "./JobPanel.vue";

const props = defineProps({
  task: { type: Object, required: true },
  job: { type: Object, required: true },
  requirementIds: { type: Array, default: null },
  role: { type: String, default: "viewer" },
  // A task material picker can advance this value to invalidate an open preview.
  materialRevision: { type: [String, Number], default: null },
});

const emit = defineEmits(["changed"]);

const levels = ref([]);
const reasoning = ref("");
const loadingLevels = ref(false);
const previewing = ref(false);
const changingRedaction = ref(false);
const preview = ref(null);
const warnings = ref([]);
const error = ref("");
const redactionOverride = ref(null);
const capInput = ref("");
const authorized = ref(false);
const running = ref(false);
const runJobId = ref("");

const extractionJobId = computed(() => props.job?.job_id ?? props.job?.id ?? "");
const documentId = computed(() => props.job?.document_id ?? "");
const extractionSucceeded = computed(() => props.job?.status === "succeeded");
const explicitIdsValid = computed(
  () => props.requirementIds == null || props.requirementIds.length > 0,
);
const canOperate = computed(() => ["admin", "bidder", "technical"].includes(props.role));
const canChangeRedaction = computed(() => props.role === "admin");
const taskRedaction = computed(() => redactionOverride.value ?? props.task);
const redactionEnabled = computed(() => taskRedaction.value?.model_redaction_enabled !== false);
const redactionRevision = computed(() => taskRedaction.value?.model_redaction_revision ?? null);
const skippedEntries = computed(() => Object.entries(preview.value?.skipped ?? {}));
const redactedEntries = computed(() => Object.entries(preview.value?.redacted_counts ?? {}));
const previewIsStale = computed(() => {
  if (!preview.value) return false;
  const manifestRevision = preview.value.input_manifest?.model_redaction_revision;
  return (
    preview.value.extraction_job_id !== extractionJobId.value ||
    preview.value.reasoning !== (reasoning.value || null) ||
    preview.value.model_redaction_enabled !== redactionEnabled.value ||
    (manifestRevision != null && manifestRevision !== redactionRevision.value)
  );
});

const redactionLabels = {
  amount: "报价与金额",
  contact: "联系人与电话",
  identity: "身份信息",
  bank_account: "银行账号",
};

const blockerLabels = {
  insufficient_balance: "单位余额不足",
  job_charge_limit_exceeded: "估算首批扣款超过单作业上限",
  spend_cap_below_first_call: "填写的扣款上限低于首个调用的预留金额",
};

const capValue = computed(() => {
  const text = capInput.value.trim();
  if (!/^\d+(\.\d{1,8})?$/.test(text)) return null;
  return Number(text) > 0 ? text : null;
});
const canRunPaid = computed(
  () =>
    !!preview.value &&
    !previewIsStale.value &&
    !preview.value.admission_blocker &&
    (preview.value.selected_requirements?.length ?? 0) > 0 &&
    capValue.value != null &&
    authorized.value &&
    canOperate.value &&
    !running.value,
);

function defaultCap(estimate) {
  // Rounded up to whole cents so the default never sits below the first-pass estimate.
  const value = Number(estimate ?? 0);
  return (Math.max(0.01, Math.ceil(value * 100) / 100)).toFixed(2);
}

function invalidatePreview() {
  preview.value = null;
  warnings.value = [];
  authorized.value = false;
}

function previewSignature() {
  return JSON.stringify({
    task: props.task?.id,
    job: extractionJobId.value,
    requirements: props.requirementIds,
    reasoning: reasoning.value || null,
    redactionRevision: redactionRevision.value,
    materialRevision: props.materialRevision,
  });
}

let lastSignature = "";
watch(
  previewSignature,
  (signature) => {
    if (lastSignature && signature !== lastSignature) invalidatePreview();
    lastSignature = signature;
  },
  { immediate: true },
);

watch(
  () => [props.task?.id, props.task?.model_redaction_revision],
  () => {
    redactionOverride.value = null;
  },
);

async function loadReasoningLevels() {
  levels.value = [];
  reasoning.value = "";
  error.value = "";
  invalidatePreview();
  if (!documentId.value || !extractionSucceeded.value || !canOperate.value) return;

  loadingLevels.value = true;
  try {
    const result = await orgRequest("POST", `/documents/${documentId.value}/extract`, {
      dry_run: true,
    });
    levels.value = result.data?.reasoning_levels ?? [];
    reasoning.value = result.data?.reasoning ?? "";
  } catch (exc) {
    error.value = errorText(exc);
  } finally {
    loadingLevels.value = false;
  }
}

watch(
  () => [documentId.value, extractionJobId.value, props.job?.status, props.role],
  loadReasoningLevels,
  { immediate: true },
);

async function runPreview() {
  error.value = "";
  invalidatePreview();
  if (!extractionSucceeded.value) {
    error.value = "只能对明确选择的成功抽取记录进行起草预检。";
    return;
  }
  if (!explicitIdsValid.value) {
    error.value = "明确选择要求时至少需要一条要求。";
    return;
  }

  previewing.value = true;
  const submittedSignature = previewSignature();
  try {
    const body = {
      extraction_job_id: extractionJobId.value,
      dry_run: true,
    };
    if (props.requirementIds != null) body.requirement_ids = [...props.requirementIds];
    if (reasoning.value) body.reasoning = reasoning.value;
    const result = await orgRequest(
      "POST",
      `/tasks/${props.task.id}/cards/generations`,
      body,
    );
    if (submittedSignature !== previewSignature()) {
      error.value = "预检期间要求、档位或材料已变化，结果已作废，请重新预检。";
      return;
    }
    preview.value = result.data;
    warnings.value = result.warnings ?? [];
    capInput.value = defaultCap(result.data.estimated_charge);
  } catch (exc) {
    error.value = errorText(exc);
  } finally {
    previewing.value = false;
  }
}

async function runPaid() {
  if (!canRunPaid.value) return;
  error.value = "";
  running.value = true;
  try {
    const body = {
      extraction_job_id: extractionJobId.value,
      expected_input_hash: preview.value.input_hash,
      max_charge: capValue.value,
    };
    if (props.requirementIds != null) body.requirement_ids = [...props.requirementIds];
    if (reasoning.value) body.reasoning = reasoning.value;
    const result = await orgRequest("POST", `/tasks/${props.task.id}/cards/generations`, body);
    runJobId.value = result.data.job_id;
    invalidatePreview();
  } catch (exc) {
    if (exc.code === "generation_input_changed") {
      invalidatePreview();
      error.value = "预检后输入、模型或价格已变化，本次未提交也未扣费，请重新预检。";
    } else {
      error.value = errorText(exc);
    }
  } finally {
    running.value = false;
  }
}

function generationFinished(job) {
  emit("changed", { kind: "generation", job });
  window.dispatchEvent(new CustomEvent("bid:task-cards-changed", { detail: { taskId: props.task.id } }));
}

async function toggleRedaction() {
  if (!canChangeRedaction.value || changingRedaction.value) return;
  error.value = "";
  if (redactionEnabled.value && !window.confirm("关闭遮挡后，所选报价、联系人、身份和银行信息可能原文外发。确认关闭？")) return;
  changingRedaction.value = true;
  try {
    const result = await orgRequest("PUT", `/tasks/${props.task.id}/model-redaction`, {
      expected_revision: redactionRevision.value,
      model_redaction_enabled: !redactionEnabled.value,
    });
    redactionOverride.value = {
      ...props.task,
      model_redaction_enabled: result.data.model_redaction_enabled,
      model_redaction_revision: result.data.revision,
    };
    invalidatePreview();
    emit("changed", { kind: "redaction", task: result.data });
  } catch (exc) {
    error.value = errorText(exc);
  } finally {
    changingRedaction.value = false;
  }
}

function materialChanged(event) {
  const changedTask = event.detail?.taskId ?? event.detail?.task_id;
  if (!changedTask || changedTask === props.task.id) invalidatePreview();
}

onMounted(() => {
  window.addEventListener("bid:task-materials-changed", materialChanged);
  window.addEventListener("bid:task-cards-changed", materialChanged);
});

onBeforeUnmount(() => {
  window.removeEventListener("bid:task-materials-changed", materialChanged);
  window.removeEventListener("bid:task-cards-changed", materialChanged);
});

defineExpose({ invalidate: invalidatePreview });
</script>

<template>
  <section class="generation-panel panel" aria-labelledby="generation-title">
    <div class="panel-heading">
      <div>
        <h3 id="generation-title">模型起草响应卡</h3>
        <p class="hint">模型只生成 draft 修订，不会送审、确认材料或采用处置建议。</p>
      </div>
      <span class="badge" :class="extractionSucceeded ? 'ok' : 'bad'">
        抽取 {{ job?.status ?? "未知" }}
      </span>
    </div>

    <div class="summary-grid">
      <div>
        <span class="label">抽取 job</span>
        <code>{{ extractionJobId || "未知" }}</code>
      </div>
      <div>
        <span class="label">起草范围</span>
        <strong>{{ requirementIds == null ? "本次抽取的全部要求" : `${requirementIds.length} 条明确要求` }}</strong>
      </div>
      <div>
        <span class="label">外发遮挡</span>
        <strong>{{ redactionEnabled ? "已开启" : "已关闭" }}</strong>
        <span class="hint">修订 {{ display(redactionRevision) }}</span>
      </div>
    </div>

    <div class="redaction-row">
      <div>
        <strong>{{ redactionEnabled ? "敏感信息按服务端规则遮挡" : "未遮挡文本会发送给已配置的模型服务商" }}</strong>
        <p v-if="!redactionEnabled" class="error">报价与金额、联系人与电话、身份信息和银行账号可能以原文外发。</p>
      </div>
      <button
        v-if="canChangeRedaction"
        type="button"
        :disabled="changingRedaction || redactionRevision == null"
        @click="toggleRedaction"
      >
        {{ changingRedaction ? "保存中…" : redactionEnabled ? "关闭遮挡" : "开启遮挡" }}
      </button>
      <span v-else class="hint">只有单位管理员可以修改遮挡设置。</span>
    </div>

    <div class="controls">
      <label>
        官方推理档位
        <select v-model="reasoning" :disabled="loadingLevels || !levels.length || !canOperate">
          <option v-if="!levels.length" value="">此模型未提供可选推理档位</option>
          <option v-for="level in levels" :key="level.name" :value="level.name">
            {{ level.label || level.name }}{{ level.default ? "（默认）" : "" }}
          </option>
        </select>
      </label>
      <button
        class="primary"
        type="button"
        :disabled="previewing || loadingLevels || !canOperate || !extractionSucceeded || !explicitIdsValid"
        @click="runPreview"
      >
        {{ previewing ? "预检中…" : "预检外发范围与费用" }}
      </button>
    </div>

    <p v-if="!canOperate" class="notice">当前角色只能查看起草信息，不能预检或运行模型起草。</p>
    <p v-if="!extractionSucceeded" class="error">请选择 status=succeeded 的抽取历史记录。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>

    <div v-if="preview" class="preview" :class="{ stale: previewIsStale }">
      <div class="panel-heading">
        <h4>本次预检</h4>
        <span v-if="previewIsStale" class="badge bad">已失效，请重新预检</span>
        <span v-else-if="preview.admission_blocker" class="badge bad">当前不可准入</span>
        <span v-else class="badge ok">预检完成</span>
      </div>

      <div class="preview-cards">
        <div class="card">
          <span class="label">目标 / 跳过</span>
          <strong>{{ preview.selected_requirements?.length ?? 0 }} / {{ skippedEntries.length }}</strong>
        </div>
        <div class="card">
          <span class="label">模型</span>
          <strong>{{ display(preview.model) }}</strong>
          <small>目录 {{ display(preview.platform_model_id) }} · 修订 {{ display(preview.model_revision) }}</small>
        </div>
        <div class="card">
          <span class="label">推理档位</span>
          <strong>{{ display(preview.reasoning) }}</strong>
        </div>
        <div class="card">
          <span class="label">估算 tokens</span>
          <strong>{{ display(preview.estimated_cost?.llm_tokens) }}</strong>
        </div>
        <div class="card">
          <span class="label">服务商 USD 估算</span>
          <strong>{{ preview.estimated_cost?.usd == null ? "费用暂不可估" : `${preview.estimated_cost.usd} USD` }}</strong>
        </div>
        <div class="card">
          <span class="label">平台扣款估算</span>
          <strong>{{ preview.estimated_charge == null ? "费用暂不可估" : `${preview.estimated_charge} ${preview.billing_currency}` }}</strong>
        </div>
      </div>

      <dl class="details-list">
        <div><dt>估算口径</dt><dd>{{ display(preview.cost_basis) }} · {{ display(preview.cost_basis_reason) }}</dd></div>
        <div><dt>估算类型</dt><dd>{{ display(preview.estimate_kind) }}</dd></div>
        <div><dt>预计时长</dt><dd>{{ preview.estimated_duration_ms == null ? "未知" : `${preview.estimated_duration_ms} ms` }}</dd></div>
        <div><dt>输入引用</dt><dd>{{ preview.input_refs?.length ? preview.input_refs.join("、") : "无材料引用" }}</dd></div>
        <div><dt>遮挡规则</dt><dd>{{ display(preview.redaction_rule_version) }}</dd></div>
        <div><dt>输入标识</dt><dd><code>{{ display(preview.input_hash) }}</code></dd></div>
      </dl>

      <div v-if="redactedEntries.length" class="compact-list">
        <strong>遮挡命中</strong>
        <span v-for="entry in redactedEntries" :key="entry[0]" class="badge">
          {{ redactionLabels[entry[0]] ?? entry[0] }} {{ entry[1] }}
        </span>
      </div>

      <details v-if="skippedEntries.length">
        <summary>查看 {{ skippedEntries.length }} 条跳过原因</summary>
        <ul>
          <li v-for="entry in skippedEntries" :key="entry[0]">
            <code>{{ entry[0] }}</code>：{{ entry[1] }}
          </li>
        </ul>
      </details>

      <ul v-if="warnings.length" class="warning-list" aria-label="预检警示">
        <li v-for="warning in warnings" :key="warning">{{ warning }}</li>
      </ul>
      <p v-if="preview.admission_blocker" class="error" role="alert">
        {{ blockerLabels[preview.admission_blocker] ?? preview.admission_blocker }}
      </p>

      <div class="paid-gate">
        <label>
          本次平台扣款上限（{{ preview.billing_currency }}）
          <input v-model="capInput" inputmode="decimal" maxlength="20" :disabled="previewIsStale" />
        </label>
        <p v-if="capInput && capValue == null" class="error">请输入大于 0、最多 8 位小数的金额。</p>
        <label class="authorize">
          <input v-model="authorized" type="checkbox" :disabled="previewIsStale || !!preview.admission_blocker" />
          我已核对上方外发范围与遮挡结果，授权按本次预检运行
        </label>
        <button class="primary" type="button" :disabled="!canRunPaid" @click="runPaid">
          {{ running ? "提交中…" : "确认并付费运行" }}
        </button>
        <p class="hint">
          服务端会核对本次预检的输入标识（含输入、模型与价格修订），不一致则拒绝且不扣费。累计扣款达到上限即停止后续调用，已完成部分保存为部分结果。
          上限只约束平台扣费；单位自带密钥时，厂商账单不受此上限约束。授权运行不会确认任何响应或材料。
        </p>
      </div>
    </div>

    <JobPanel v-if="runJobId" :job-id="runJobId" :writable="canOperate" @finished="generationFinished" />
  </section>
</template>

<style scoped>
.generation-panel { background: #fff; }
.panel-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
h3, h4 { margin: 0 0 4px; }
.summary-grid, .preview-cards { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin: 14px 0; }
.summary-grid > div { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.summary-grid code, .details-list code { overflow-wrap: anywhere; }
.label { color: var(--muted); font-size: 12px; }
.redaction-row { display: flex; justify-content: space-between; align-items: center; gap: 16px; border-block: 1px solid var(--border); padding: 12px 0; }
.redaction-row p { margin: 4px 0 0; }
.controls { display: flex; align-items: end; gap: 10px; margin-top: 14px; }
.controls label { flex: 1; }
.preview { border-top: 1px solid var(--border); margin-top: 16px; padding-top: 16px; }
.preview.stale { opacity: .72; }
.preview-cards { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.preview-cards .card { display: flex; flex-direction: column; gap: 4px; }
.preview-cards strong { overflow-wrap: anywhere; }
.preview-cards small { color: var(--muted); }
.details-list { margin: 12px 0; }
.details-list > div { display: grid; grid-template-columns: 110px 1fr; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--border); }
.details-list dt { color: var(--muted); }
.details-list dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.compact-list { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin: 12px 0; }
.warning-list { color: #865b00; padding-left: 20px; }
.paid-gate { background: var(--surface); border-radius: 8px; padding: 12px; margin-top: 14px; }
.paid-gate button { margin-top: 10px; }
.paid-gate label { display: block; }
.paid-gate .authorize { display: flex; gap: 8px; align-items: center; margin-top: 10px; }
.paid-gate .authorize input { width: auto; }
.paid-gate p { margin-bottom: 0; }
details { margin-top: 10px; }
summary { cursor: pointer; }
@media (max-width: 720px) {
  .summary-grid, .preview-cards { grid-template-columns: 1fr; }
  .redaction-row, .controls { align-items: stretch; flex-direction: column; }
  .details-list > div { grid-template-columns: 1fr; gap: 2px; }
}
</style>
