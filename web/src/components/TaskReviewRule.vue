<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { errorText, orgRequest } from "../org.js";
const props = defineProps({ taskId: String, workflow: Object, manage: Boolean });
const emit = defineEmits(["updated", "dirty", "denied"]);
const rule = ref(null), enabled = ref(false), reason = ref(""), preview = ref(null), busy = ref(false), error = ref(""), notice = ref("");
let active = true, generation = 0;
const writable = computed(() => props.manage && props.workflow?.state === "active");
const dirty = computed(() => !!rule.value && (enabled.value !== rule.value.co_sign_starred || !!reason.value));
watch(dirty, value => emit("dirty", value));
watch([enabled, reason], () => { preview.value = null; });
watch(() => props.workflow?.revision, () => { preview.value = null; if (rule.value) load(); });
async function load() {
  const run = ++generation;
  try {
    const result = await orgRequest("GET", `/tasks/${props.taskId}/review-rule`);
    if (!active || run !== generation) return;
    if (result.data.rule.task_id !== props.taskId || result.data.rule.org_id !== props.workflow.org_id) throw new Error("审阅规则范围不符合契约");
    const wasDirty = dirty.value;
    rule.value = result.data.rule;
    if (!wasDirty) enabled.value = rule.value.co_sign_starred;
    error.value = "";
  } catch (exc) { if (active && run === generation) { error.value = errorText(exc); if ([401,403,404].includes(exc.status)) emit("denied", exc); } }
}
async function write(dryRun) {
  if (!writable.value || !rule.value || !reason.value.trim() || (!dryRun && !preview.value)) return;
  const body = { expected_revision: rule.value.workflow_revision, reason: reason.value.trim(), co_sign_starred: enabled.value, dry_run: dryRun };
  if (!dryRun && preview.value.request !== JSON.stringify({ ...body, dry_run: true })) { preview.value = null; error.value = "规则或修改内容已变化，请重新预览影响。"; return; }
  busy.value = true; error.value = ""; notice.value = "";
  try {
    const result = await orgRequest("PUT", `/tasks/${props.taskId}/review-rule`, body);
    if (!active) return;
    if (result.data.rule.task_id !== props.taskId || result.data.rule.org_id !== props.workflow.org_id || result.data.dry_run !== dryRun || !Number.isInteger(result.data.affected_requirements)) throw new Error("规则影响预览范围不符合契约");
    if (dryRun) {
      if (rule.value.workflow_revision !== body.expected_revision) throw new Error("工作流已更新，请重新读取并预览规则影响");
      preview.value = { ...result.data, request: JSON.stringify(body) };
    }
    else { rule.value = result.data.rule; enabled.value = rule.value.co_sign_starred; reason.value = ""; preview.value = null; notice.value = "审阅规则已保存，受影响条目需要重新审阅。"; emit("updated"); }
  } catch (exc) {
    if (!active) return;
    preview.value = null; error.value = errorText(exc);
    if (exc.status === 409) { await load(); error.value = `${errorText(exc)}；修改未重试。已保留修改内容，请重新读取并预览当前规则。`; }
    else if ([401,403,404].includes(exc.status)) emit("denied", exc);
  } finally { busy.value = false; }
}
onMounted(load);
onBeforeUnmount(() => { active = false; generation++; });
</script>
<template>
  <el-card shadow="never" class="section" aria-label="任务审阅规则">
    <h3>任务审阅规则</h3>
    <p v-if="rule" class="hint">规则修订 {{rule.rule_revision}} · 工作流修订 {{rule.workflow_revision}}</p>
    <label class="check"><input v-model="enabled" type="checkbox" :disabled="!writable || busy || !rule" />★ 条款要求商务与技术会签</label>
    <p class="hint">规则适用的 ★ 条款保留主职责，并要求商务与技术分别签署。保存会使受影响的既有会签失效。</p>
    <el-form v-if="writable" label-position="top" @submit.prevent="write(true)">
      <el-form-item label="审阅规则修改原因" required><el-input v-model="reason" type="textarea" aria-label="审阅规则修改原因" aria-required="true" maxlength="500" :disabled="busy" /></el-form-item>
      <p v-if="preview" data-testid="rule-impact" role="status">将影响 {{preview.affected_requirements}} 项要求；保存后需重新审阅。</p>
      <div class="actions"><el-button native-type="submit" :disabled="busy || !rule || !reason.trim()">预览规则影响</el-button><el-button type="primary" :disabled="busy || !preview || !reason.trim()" @click="write(false)">保存审阅规则</el-button></div>
    </el-form>
    <p v-else class="hint">{{workflow?.state === 'archived' ? '任务已归档，规则只读。' : '仅任务负责人或单位管理员可以修改规则。'}}</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="section" />
    <p v-if="notice" role="status">{{notice}}</p>
  </el-card>
</template>
