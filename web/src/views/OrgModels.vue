<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { onBeforeRouteLeave } from "vue-router";
import { ApiError, request } from "../api.js";
import { confirmAction } from "../org.js";
import ProviderMetadata from "../components/ProviderMetadata.vue";
import { blankProvider, checkProviderMetadata, checkProviderPage, checkProviderSettings, providerError, providerInput, providerPath, providerReader, submitProviderConfiguration, validId } from "../provider-settings.js";
const reader = providerReader();
const settings = ref(null);
const loading = ref(false);
const error = ref("");
const busy = ref(false);
const editing = ref(false);
const form = ref(blankProvider());
const initial = ref("");
const password = ref(null);
const hasFreshKey = ref(false);
const conflict = ref(false);
const history = ref([]);
const historyMeta = ref(null);
const historyCursor = ref(null);
const historyBack = ref([]);
const historyLoading = ref(false);
const selectedRevision = ref(null);
const preflight = ref(null);
const testReceipt = ref(null);
const testedRevision = ref(null);
const testReasoning = ref(null);
const saveReceipt = ref(null);
let stopped = false, generation = 0, revisionRun = 0, testedRun = 0, historyRun = 0;
const writes = new Set();
const dirty = computed(() => editing.value && (JSON.stringify(form.value) !== initial.value || hasFreshKey.value));
const current = computed(() => settings.value?.current), reasoning = computed(() => settings.value?.reasoning ?? []);
const canConfigure = computed(() => settings.value?.actions.some(item => item.action === "configure" && item.allowed));
const canTest = computed(() => settings.value?.actions.some(item => item.action === "test" && item.allowed) && !editing.value && !busy.value);
const reusable = computed(() => !!current.value && form.value.provider === current.value.provider && (form.value.base_url.trim().replace(/\/$/, "") || null) === current.value.configuration.base_url);
const validForm = computed(() => !!form.value.model.trim() && (!form.value.base_url || /^https:\/\//.test(form.value.base_url)) && (hasFreshKey.value || reusable.value));
function clearKey() {
  if (password.value) password.value.value = "";
  hasFreshKey.value = false;
}
function teardown() {
  stopped = true;
  generation++;
  revisionRun++;
  testedRun++;
  historyRun++;
  reader.stop();
  for (const controller of writes) controller.abort();
  writes.clear();
  clearKey();
  settings.value = null;
  editing.value = false;
  form.value = blankProvider();
  initial.value = "";
  history.value = [];
  historyMeta.value = null;
  historyBack.value = [];
  selectedRevision.value = null;
  preflight.value = null;
  testReceipt.value = null;
  testedRevision.value = null;
  saveReceipt.value = null;
  conflict.value = false;
}
function failed(exc) {
  if (exc.name === "AbortError") return;
  if ([401, 403, 404].includes(exc.status)) {
    teardown();
    error.value = "配置不可访问，未保存内容已清除。";
  } else error.value = providerError(exc);
}
async function load() {
  if (stopped) return;
  const run = ++generation;
  loading.value = true;
  error.value = "";
  try {
    const response = await reader.read("settings", "GET", providerPath);
    if (stopped || run !== generation) return;
    const value = checkProviderSettings(response.data);
    settings.value = value;
    testReasoning.value = null;
    preflight.value = null;
    return value;
  } catch (exc) {
    if (!stopped && run === generation) failed(exc);
  } finally {
    if (run === generation) loading.value = false;
  }
}
async function loadPage(cursor = null) {
  if (stopped) return;
  const run = ++historyRun;
  historyLoading.value = true;
  error.value = "";
  try {
    const payload = await reader.read("history", "POST", `${providerPath}/history/query`, { cursor, limit: 25 });
    if (stopped || run !== historyRun) return;
    const response = checkProviderPage(payload);
    history.value = response.items;
    historyMeta.value = response.data;
    historyCursor.value = cursor;
  } catch (exc) {
    if (stopped || run !== historyRun || exc.name === "AbortError") return;
    history.value = [];
    historyMeta.value = null;
    if (["management_cursor_invalid", "management_cursor_expired"].includes(exc.code)) historyBack.value = [];
    failed(exc);
  } finally {
    if (!stopped && run === historyRun) historyLoading.value = false;
  }
}
function restart() {
  historyBack.value = [];
  loadPage();
}
function next() {
  if (!historyMeta.value?.has_more) return;
  historyBack.value.push(historyCursor.value);
  if (historyBack.value.length > 50) historyBack.value.shift();
  loadPage(historyMeta.value.next_cursor);
}
function previous() {
  if (historyBack.value.length) loadPage(historyBack.value.pop());
}
async function revision(id) {
  if (!validId(id)) return;
  const run = ++revisionRun;
  try {
    const response = await reader.read("revision", "GET", `${providerPath}/revisions/${id}`);
    if (stopped || run !== revisionRun) return;
    selectedRevision.value = checkProviderMetadata(response.data, id);
  } catch (exc) {
    if (!stopped && run === revisionRun && exc.name !== "AbortError") {
      selectedRevision.value = null;
      failed(exc);
    }
  }
}
function startEdit() {
  if (!canConfigure.value || busy.value) return;
  clearKey();
  form.value = blankProvider();
  const c = current.value?.configuration;
  if (c) {
    form.value.provider = c.provider;
    form.value.model = c.model;
    form.value.base_url = c.base_url ?? "";
    form.value.json_mode = c.json_mode;
    form.value.reasoning = c.reasoning.map((level) => ({ name: level.name, label: level.label, request_options: { ...level.request_options }, effort: level.effort, batch_chars: level.batch_chars }));
    form.value.default_reasoning = c.default_reasoning;
  }
  initial.value = JSON.stringify(form.value);
  conflict.value = false;
  saveReceipt.value = null;
  preflight.value = null;
  testReceipt.value = null;
  testedRevision.value = null;
  editing.value = true;
}
async function cancel() {
  if (dirty.value && !await confirmAction("放弃未保存的配置？密钥输入会清除。", "放弃未保存编辑", "放弃编辑")) return;
  clearKey();
  form.value = blankProvider();
  editing.value = false;
  conflict.value = false;
  preflight.value = null;
}
function addReasoning() {
  if (form.value.reasoning.length < 8) form.value.reasoning.push({ name: "", label: null, request_options: {}, effort: null, batch_chars: 8e3 });
}
function removeReasoning(index) {
  const removed = form.value.reasoning.splice(index, 1)[0];
  if (removed.name === form.value.default_reasoning) form.value.default_reasoning = null;
}
function optionsChanged(event, index) {
  try {
    const value = JSON.parse(event.target.value || "{}");
    if (!value || Array.isArray(value) || typeof value !== "object") throw new Error();
    form.value.reasoning[index].request_options = value;
    event.target.setCustomValidity("");
  } catch {
    event.target.setCustomValidity("请求选项需要 JSON 对象");
  }
}
async function save() {
  if (!editing.value || !canConfigure.value || busy.value || !validForm.value) return;
  busy.value = true;
  error.value = "";
  const controller = new AbortController();
  writes.add(controller);
  try {
    if (stopped) return;
    const input = providerInput(form.value, current.value?.revision ?? null);
    const submission = submitProviderConfiguration(input, password.value, { signal: controller.signal });
    hasFreshKey.value = false;
    const receipt = await submission;
    if (stopped) return;
    const reread = await load();
    if (stopped) return;
    if (!reread || !settings.value?.current || settings.value.current.revision < receipt.revision) throw new ApiError(502, "invalid_response", "");
    editing.value = false;
    form.value = blankProvider();
    initial.value = "";
    conflict.value = false;
    historyBack.value = [];
    saveReceipt.value = receipt.id;
    await loadPage();
  } catch (exc) {
    clearKey();
    if (stopped) return;
    failed(exc);
    if (!stopped && (exc.status === 409 || !exc.status || exc.status >= 500 || ["invalid_response", "provider_save_failed"].includes(exc.code))) {
      conflict.value = true;
      await load();
      if (!stopped) error.value = exc.status === 409 ? "修订已变化，非密钥草稿已保留。核对当前服务器修订后，再主动保存；密钥需重新输入。" : "保存结果尚未确定，已重新读取配置；非密钥草稿保留。核对后再决定是否保存，不会自动重试。";
    }
  } finally {
    clearKey();
    writes.delete(controller);
    busy.value = false;
  }
}
async function test(dryRun) {
  if (!canTest.value || !dryRun && !preflight.value) return;
  busy.value = true;
  error.value = "";
  const controller = new AbortController();
  writes.add(controller);
  const displayedId = current.value?.id ?? null, displayedModel = current.value?.model ?? null, displayedProvider = current.value?.provider ?? null;
  try {
    const result = await request("POST", "/providers/test", { capability: "llm_extract", reasoning: testReasoning.value, dry_run: dryRun }, { signal: controller.signal});
    if (stopped) return;
    if (dryRun) {
      if (result.data.dry_run !== true || !result.data.preflight) throw new ApiError(502, "invalid_response", "");
      preflight.value = { cost: result.cost, blocker: result.data.preflight.admission_blocker ?? null };
      testReceipt.value = null;
      testedRevision.value = null;
    } else {
      preflight.value = null;
      await recordTest(result, displayedId, displayedModel, displayedProvider);
    }
  } catch (exc) {
    preflight.value = null;
    if (!dryRun && !stopped && exc.payload?.command === "provider test" && validId(exc.payload.data?.job_id)) {
      try {
        await recordTest(exc.payload, displayedId, displayedModel, displayedProvider);
        error.value = "连接测试未成功，实际用量与配置回执已保留。请核对后重新预检，不会自动重试。";
        return;
      } catch (readError) {
        failed(readError);
      }
    }
    if (!dryRun && exc.name !== "AbortError" && !stopped) error.value = "连接测试结果无法核验，可能已发出调用。不会自动重试；请核对用量或作业后再明确开始新的测试。";
    if ([401, 403, 404].includes(exc.status)) failed(exc);
    else if (dryRun) failed(exc);
  } finally {
    writes.delete(controller);
    busy.value = false;
  }
}
async function recordTest(result, displayedId, displayedModel, displayedProvider) {
  const run = ++testedRun;
  const data = result.data;
  if (!validId(data.job_id) || !(data.provider_config_id === null || validId(data.provider_config_id)) || !["succeeded", "failed", "cancelled", "queued", "running"].includes(data.status)) throw new ApiError(502, "invalid_response", "");
  testReceipt.value = { jobId: data.job_id, configId: data.provider_config_id, status: data.status, model: typeof data.usage?.model === "string" ? data.usage.model : null, provider: typeof data.usage?.provider === "string" ? data.usage.provider : null, cost: result.cost, changed: data.provider_config_id !== displayedId || (typeof data.usage?.model === "string" && data.usage.model !== displayedModel || typeof data.usage?.provider === "string" && data.usage.provider !== displayedProvider) };
  if (data.provider_config_id) {
    const response = await reader.read("tested-revision", "GET", `${providerPath}/revisions/${data.provider_config_id}`);
    if (stopped || run !== testedRun) return;
    testedRevision.value = checkProviderMetadata(response.data, data.provider_config_id);
  }
}
watch(testReasoning, () => {
  preflight.value = null;
});
watch(canConfigure, (allowed, previous) => {
  if (previous && !allowed && editing.value && !stopped) {
    teardown();
    error.value = "维护权限已改变，未保存内容已清除。请重新打开配置页面。";
  }
});
function reset() {
  teardown();
  error.value = "会话已改变，未保存内容已清除。";
}
function beforeUnload(event) {
  if (dirty.value) {
    event.preventDefault();
    event.returnValue = "";
  }
}
onBeforeRouteLeave(async () => !dirty.value || await confirmAction("放弃未保存的配置？密钥输入会清除。", "放弃未保存编辑", "放弃编辑"));
onMounted(async () => {
  window.addEventListener("bid:session-reset", reset);
  window.addEventListener("beforeunload", beforeUnload);
  await load();
  if (settings.value) await loadPage();
});
onUnmounted(() => {
  teardown();
  window.removeEventListener("bid:session-reset", reset);
  window.removeEventListener("beforeunload", beforeUnload);
});
</script>
<template>
  <div class="page-header"><div><h2>模型与服务配置</h2><p class="subtitle">要求抽取、响应卡起草等模型任务共用（llm_extract）</p></div><el-button :disabled="busy||editing||loading" @click="load">重新读取配置</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" class="section"/>
  <p v-if="loading" role="status">正在读取配置元数据…</p>
  <template v-if="settings">
    <el-card shadow="never" class="section"><template #header><div class="section-title"><h3>已保存的有效配置</h3><el-button v-if="canConfigure&&!editing" :disabled="busy" @click="startEdit">修改模型配置</el-button></div></template>
      <ProviderMetadata v-if="current" :value="current" />
      <el-empty v-else description="尚未配置有效模型，请修改模型配置"/>
      <p class="hint">仅显示配置元数据，尚未检查连接。新配置影响后续选择；已排队作业保留各自配置修订。模型或密钥不可用时不会改用其他模型。</p>
      <p v-if="saveReceipt" role="status">配置已保存并重新读取 · 精确配置 ID {{saveReceipt}}</p>
    </el-card>
    <el-card v-if="editing&&canConfigure" shadow="never" class="section"><template #header><h3>编辑模型配置</h3></template>
      <el-alert v-if="conflict" title="服务器修订已重新读取，非密钥草稿保留；请核对上方当前配置后主动保存。" type="warning" :closable="false"/>
      <form @submit.prevent="save" class="model-form">
        <label>供应商<select v-model="form.provider" aria-label="供应商" :disabled="busy"><option value="openai">OpenAI 兼容协议</option><option value="anthropic">Anthropic</option></select></label>
        <label>模型名称<input v-model="form.model" aria-label="模型名称" maxlength="100" required :disabled="busy"/></label>
        <label>HTTPS 端点（可选）<input v-model="form.base_url" aria-label="HTTPS 端点" maxlength="300" placeholder="供应商标准端点可留空" :disabled="busy"/></label>
        <label>JSON 模式<select v-model="form.json_mode" aria-label="JSON 模式" :disabled="busy"><option value="json_schema">json_schema</option><option value="json_object">json_object</option></select></label>
        <label>新 API 密钥<input ref="password" type="password" aria-label="新 API 密钥" autocomplete="new-password" autocapitalize="none" spellcheck="false" maxlength="4096" :disabled="busy" @input="hasFreshKey=!!$event.target.value"/></label>
        <p class="hint">{{reusable?'留空由服务器复用当前相同供应商和端点的密钥。':'首次设置或更换供应商、端点需输入新密钥。'}} 密钥只随本次保存发送一次，并立即清除；失败后需重新输入。配置元数据不含原密钥。</p>
        <h4>供应商官方推理档位</h4><p class="hint">仅填写供应商支持的档位及非密钥请求选项，最多 8 个。</p>
        <fieldset v-for="(level,index) in form.reasoning" :key="index" :disabled="busy"><legend>推理档位 {{index+1}}</legend><label>名称<input v-model="level.name" :aria-label="`推理名称 ${index+1}`" pattern="[a-z0-9_-]{1,20}" required maxlength="20"/></label><label>显示标签<input v-model="level.label" :aria-label="`推理标签 ${index+1}`" maxlength="60"/></label><label>Anthropic effort<input v-model="level.effort" :aria-label="`推理 effort ${index+1}`" maxlength="10"/></label><label>批次字符上限<input v-model.number="level.batch_chars" :aria-label="`推理批次字符 ${index+1}`" type="number" min="1000" max="200000"/></label><label>非密钥请求选项（JSON）<textarea :value="JSON.stringify(level.request_options)" :aria-label="`推理请求选项 ${index+1}`" maxlength="4000" @input="optionsChanged($event,index)"/></label><el-button @click="removeReasoning(index)">移除档位 {{index+1}}</el-button></fieldset>
        <el-button :disabled="busy||form.reasoning.length>=8" @click="addReasoning">添加推理档位</el-button><label>默认推理档位<select v-model="form.default_reasoning" aria-label="默认推理档位" :disabled="busy"><option :value="null">未设置</option><option v-for="level in form.reasoning" :key="level.name" :value="level.name">{{level.label||level.name}}</option></select></label>
        <p>保存预期修订：{{current?.revision??'尚未设置（null）'}}</p><div class="actions"><el-button :disabled="busy" @click="cancel">放弃配置编辑</el-button><el-button native-type="submit" type="primary" :disabled="busy||!validForm" :loading="busy">保存模型配置</el-button></div>
      </form>
    </el-card>
    <el-card shadow="never" class="section"><template #header><h3>连接测试（已保存的有效配置）</h3></template><p>使用合成输入；测试通过已保存的模型配置发出一次实际调用，并记录用量。</p><p v-if="editing">请先保存或放弃未保存配置，再预检和测试。</p><label v-if="reasoning.length">测试推理档位<select v-model="testReasoning" aria-label="测试推理档位" :disabled="!canTest"><option :value="null">已保存默认档位</option><option v-for="level in reasoning" :key="level.name" :value="level.name">{{level.label??level.name}}</option></select></label>
      <div class="actions"><el-button :disabled="!canTest" @click="test(true)">预检测试范围</el-button><el-button type="primary" :disabled="!canTest||!preflight||!!preflight.blocker" @click="test(false)">明确开始连接测试</el-button></div>
      <template v-if="preflight"><p role="status">测试预检：预计调用 {{preflight.cost.calls}} 次 · {{preflight.cost.llm_tokens}} tokens</p><p v-if="preflight.blocker">预检阻止执行：{{preflight.blocker}}</p><p class="hint">预检不固定下一次测试的配置。开始后将显示实际配置 ID、模型和用量；不会自动重复测试。</p></template>
      <template v-if="testReceipt"><p role="status">实际测试状态：{{testReceipt.status}} · 作业 ID {{testReceipt.jobId}}</p><p>实际配置 ID：{{testReceipt.configId??'未知'}} · 实际模型 {{testReceipt.provider??'未知'}} / {{testReceipt.model??'未知'}}</p><p>实际用量：调用 {{testReceipt.cost.calls}} 次 · {{testReceipt.cost.llm_tokens}} tokens · 未确定调用 {{testReceipt.cost.unresolved_calls}}</p><el-alert v-if="testReceipt.changed" title="测试时配置已变化，实际配置或模型与页面显示不同；下方按回执的精确配置 ID 展示。" type="warning" :closable="false"/><ProviderMetadata v-if="testedRevision" :value="testedRevision" /></template>
    </el-card>
    <el-card shadow="never" class="section"><template #header><h3>不可变配置历史</h3></template><p>历史只读。无删除、重置或一键恢复操作；需要切换时明确保存另一有效配置。</p><p v-if="historyLoading" role="status">正在读取配置历史…</p><el-empty v-if="historyMeta&&!history.length&&!historyLoading" description="尚无配置修订"/>
      <el-table v-show="!historyLoading&&history.length" :data="history" role="region" aria-label="配置修订历史"><el-table-column prop="revision" label="修订" width="90"/><el-table-column label="供应商 / 模型"><template #default="{row}">{{row.provider}} / {{row.model}}</template></el-table-column><el-table-column label="操作"><template #default="{row}"><el-button @click="revision(row.id)">查看修订 {{row.revision}}</el-button></template></el-table-column></el-table>
      <div class="actions"><el-button @click="restart">从第一页读取历史</el-button><el-button :disabled="!historyBack.length" @click="previous">上一页历史</el-button><el-button :disabled="!historyMeta?.has_more" @click="next">下一页历史</el-button></div>
      <section v-if="selectedRevision" aria-label="精确历史修订"><h4>精确历史修订（只读）</h4><ProviderMetadata :value="selectedRevision" /><el-button @click="selectedRevision=null">关闭历史修订</el-button></section>
    </el-card>
  </template>
</template>
<style scoped>.model-form{display:grid;gap:12px}.model-form label,fieldset label{display:grid;gap:5px}.model-form input,.model-form select,.model-form textarea,select{font:inherit;border:1px solid var(--el-border-color);border-radius:4px;padding:8px;max-width:100%;box-sizing:border-box}.model-form input:disabled{background:var(--el-disabled-bg-color)}fieldset{border:1px solid var(--el-border-color);padding:12px;display:grid;gap:10px}.model-form .hint{margin:0}h4{margin:12px 0}</style>
