<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { confirmAction, display, errorText, orgRequest, warningText } from "../org.js";
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

const blockerLabels = { provider_unavailable: "模型服务不可用", context_limit_exceeded: "输入超过模型上下文上限" };



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
  } catch (exc) {
    error.value = errorText(exc);
  } finally {
    previewing.value = false;
  }
}

async function runGeneration() {
  if (!canRun.value) return;
  error.value = "";
  running.value = true;
  try {
    const body = {
      extraction_job_id: extractionJobId.value,
      expected_input_hash: preview.value.input_hash,
    };
    if (props.requirementIds != null) body.requirement_ids = [...props.requirementIds];
    if (reasoning.value) body.reasoning = reasoning.value;
    const result = await orgRequest("POST", `/tasks/${props.task.id}/cards/generations`, body);
    runJobId.value = result.data.job_id;
    invalidatePreview();
  } catch (exc) {
    if (exc.code === "generation_input_changed") {
      invalidatePreview();
      error.value = "预检后输入或模型已变化，本次未提交，请重新预检。";
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
  if (redactionEnabled.value && !(await confirmAction("关闭遮挡后，所选报价、联系人、身份和银行信息可能原文外发。确认关闭？", "关闭外发遮挡", "确定", true))) return;
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
  <section class="generation-panel" aria-labelledby="generation-title">
    <div class="panel-heading">
      <div>
        <h3 id="generation-title">模型起草响应卡</h3>
        <p class="hint">模型只生成草稿修订，不会送审、确认材料或采用处置建议。</p>
      </div>
      <el-tag :type="extractionSucceeded ? 'success' : 'danger'">抽取{{ extractionSucceeded ? "已成功" : "不可用" }}</el-tag>
    </div>

    <div class="stat-grid">
      <div class="stat"><span class="label">抽取</span><code>{{ extractionJobId || "未知" }}</code></div>
      <div class="stat"><span class="label">起草范围</span><strong>{{ requirementIds == null ? "本次抽取的全部要求" : `${requirementIds.length} 条明确要求` }}</strong></div>
      <div class="stat"><span class="label">外发遮挡</span><strong>{{ redactionEnabled ? "已开启" : "已关闭" }}</strong><span class="hint">修订 {{ display(redactionRevision) }}</span></div>
    </div>

    <div class="redaction-row">
      <div>
        <strong>{{ redactionEnabled ? "敏感信息按服务端规则遮挡" : "未遮挡文本会发送给已配置的模型服务商" }}</strong>
        <p v-if="!redactionEnabled" class="error">报价与金额、联系人与电话、身份信息和银行账号可能以原文外发。</p>
      </div>
      <el-button v-if="canChangeRedaction" :type="redactionEnabled ? 'default' : 'primary'" :loading="changingRedaction" :disabled="redactionRevision == null" @click="toggleRedaction">
        {{ redactionEnabled ? "关闭遮挡" : "开启遮挡" }}
      </el-button>
      <span v-else class="hint">只有单位管理员可以修改遮挡设置。</span>
    </div>

    <el-form label-position="top" class="controls" @submit.prevent="runPreview">
      <el-form-item label="官方推理档位">
        <el-select v-model="reasoning" :disabled="loadingLevels || !levels.length || !canOperate" :placeholder="levels.length ? '请选择' : '此模型未提供可选推理档位'">
          <el-option v-for="level in levels" :key="level.name" :value="level.name" :label="`${level.label || level.name}${level.default ? '（默认）' : ''}`" />
        </el-select>
      </el-form-item>
      <el-button type="primary" native-type="submit" :loading="previewing" :disabled="loadingLevels || !canOperate || !extractionSucceeded || !explicitIdsValid">预检外发范围与配置</el-button>
    </el-form>

    <p v-if="!canOperate" class="notice">当前角色只能查看起草信息，不能预检或运行模型起草。</p>
    <p v-if="!extractionSucceeded" class="error">请选择已成功的抽取记录。</p>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" />

    <div v-if="preview" class="preview" :class="{ stale: previewIsStale }">
      <div class="panel-heading">
        <h4>本次预检</h4>
        <el-tag v-if="previewIsStale" type="danger">已失效，请重新预检</el-tag>
        <el-tag v-else-if="preview.admission_blocker" type="danger">当前不可准入</el-tag>
        <el-tag v-else type="success">预检完成</el-tag>
      </div>

      <div class="stat-grid">
        <div class="stat"><span class="label">目标 / 跳过</span><strong>{{ preview.selected_requirements?.length ?? 0 }} / {{ skippedEntries.length }}</strong></div>
        <div class="stat"><span class="label">模型</span><strong>{{ display(preview.model) }}</strong><small class="hint">目录 {{ display(preview.platform_model_id) }} · 修订 {{ display(preview.model_revision) }}</small></div>
        <div class="stat"><span class="label">推理档位</span><strong>{{ display(preview.reasoning) }}</strong></div>
        <div class="stat"><span class="label">估算 tokens</span><strong>{{ display(preview.preflight?.estimate?.llm_tokens) }}</strong></div>
      </div>

      <dl class="details-list">
        <div><dt>估算类型</dt><dd>{{ display(preview.estimate_kind) }}</dd></div>
        <div><dt>预计时长</dt><dd>{{ preview.estimated_duration_ms == null ? "未知" : `${preview.estimated_duration_ms} ms` }}</dd></div>
        <div><dt>输入引用</dt><dd>{{ preview.input_refs?.length ? preview.input_refs.join("、") : "无材料引用" }}</dd></div>
        <div><dt>遮挡规则</dt><dd>{{ display(preview.redaction_rule_version) }}</dd></div>
        <div><dt>输入标识</dt><dd><code>{{ display(preview.input_hash) }}</code></dd></div>
      </dl>

      <div v-if="redactedEntries.length" class="tags compact-list">
        <strong>遮挡命中</strong>
        <span v-for="entry in redactedEntries" :key="entry[0]" class="tag warning">{{ redactionLabels[entry[0]] ?? entry[0] }} {{ entry[1] }}</span>
      </div>

      <details v-if="skippedEntries.length">
        <summary>查看 {{ skippedEntries.length }} 条跳过原因</summary>
        <ul>
          <li v-for="entry in skippedEntries" :key="entry[0]"><code>{{ entry[0] }}</code>：{{ entry[1] }}</li>
        </ul>
      </details>

      <ul v-if="warnings.length" class="warning-list" aria-label="预检警示">
        <li v-for="warning in warnings" :key="warning">{{ warningText(warning) }}</li>
      </ul>
      <el-alert v-if="preview.admission_blocker" :title="blockerLabels[preview.admission_blocker] ?? preview.admission_blocker" type="error" show-icon :closable="false" role="alert" />

      <div class="run-gate">
        <el-form label-position="top" @submit.prevent="runGeneration">
          <label class="check authorize">
            <input v-model="authorized" type="checkbox" :disabled="previewIsStale || !!preview.admission_blocker" />
            我已核对上方外发范围与遮挡结果，授权按本次预检运行
          </label>
          <el-button type="primary" native-type="submit" :loading="running" :disabled="!canRun">确认并运行</el-button>
        </el-form>
        <p class="hint">
          服务端会核对本次预检的输入标识与模型配置，不一致则拒绝。达到调用次数限制后停止后续调用，保留已完成部分。授权运行不会确认任何响应或材料。
        </p>
      </div>
    </div>

    <JobPanel v-if="runJobId" :job-id="runJobId" :writable="canOperate" @finished="generationFinished" />
  </section>
</template>

<style scoped>
.generation-panel { padding: 4px 0 12px; }
.panel-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
h3, h4 { margin: 0 0 4px; }
.stat-grid { margin: 14px 0; }
.stat strong { overflow-wrap: anywhere; }
.redaction-row { display: flex; justify-content: space-between; align-items: center; gap: 16px; border-block: 1px solid var(--border); padding: 12px 0; }
.redaction-row p { margin: 4px 0 0; }
.controls { display: flex; align-items: flex-end; gap: 12px; margin-top: 14px; flex-wrap: wrap; }
.controls .el-form-item { margin-bottom: 0; flex: 1 1 220px; }
.controls .el-select { width: 100%; }
.preview { border-top: 1px solid var(--border); margin-top: 16px; padding-top: 16px; }
.preview.stale { opacity: .72; }
.details-list { margin: 12px 0; }
.details-list > div { display: grid; grid-template-columns: 110px 1fr; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 13px; }
.details-list dt { color: var(--muted); }
.details-list dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.compact-list { align-items: center; margin: 12px 0; }
.warning-list { color: #865b00; padding-left: 20px; }
.run-gate { background: var(--surface-muted); border: 1px solid var(--border); border-radius: 8px; padding: 14px; margin-top: 14px; }
.run-gate .authorize { display: flex; margin: 4px 0 12px; }
.run-gate p { margin-bottom: 0; }
@media (max-width: 720px) {
  .redaction-row { align-items: stretch; flex-direction: column; }
  .details-list > div { grid-template-columns: 1fr; gap: 2px; }
}
</style>
