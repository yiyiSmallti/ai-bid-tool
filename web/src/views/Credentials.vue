<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { Plus, Refresh } from "@element-plus/icons-vue";
import { request } from "../api.js";
import { formatTime } from "../ui.js";

const purposes = { catalog_llm: "目录模型", standalone_llm: "Standalone / Eval", vendor_search: "厂商搜索" };
const providers = { anthropic: "Anthropic", openai: "OpenAI 兼容", perplexity: "Perplexity" };
const states = { active: "已启用", disabled: "已停用", removed: "已移除" };
const reasons = { setup: "初始配置", scheduled_rotation: "定期轮换", vendor_revoked: "厂商已吊销", incident: "事件处理", retired: "停止使用", migration: "迁移" };
const outcomes = { passed: "认证通过", auth_failed: "认证失败", unsupported: "不支持认证探针", timeout: "认证超时", rate_limited: "测试次数超限", unavailable: "认证服务不可用", interrupted: "测试中断，结果未知" };
const failures = {
  revision_conflict: "凭据已被他人修改，请刷新后重新打开操作。",
  invalid_session: "平台登录已失效，请重新登录。",
  invalid_input: "凭据参数无效，请检查填写内容。",
  credential_name_conflict: "凭据名称已存在，已移除的名称也不能复用。",
  credential_purpose_conflict: "该平台服务已有凭据，请先处理现有凭据。",
  credential_removed: "凭据已移除，不能再替换、启用或测试。",
  credential_unreadable: "凭据无法解密，请检查加密根或重新提交密钥。",
  credential_backend_unavailable: "凭据数据库暂不可用，请稍后重试。",
  credential_audit_unavailable: "审计保存失败，操作未完成。",
  credential_probe_endpoint_rejected: "凭据端点不符合出站访问规则。",
  not_found: "凭据不存在。",
};
const failureText = (exc) => failures[exc.code] ?? "凭据操作失败，请刷新后重试。";
const rows = ref([]), loading = ref(false), busy = ref(false), error = ref(""), notice = ref("");
const state = ref(""), purpose = ref(""), next = ref(null), cursors = ref([null]);
const form = ref(null), detail = ref(null), probes = ref({});

async function load(reset = false) {
  if (reset) cursors.value = [null];
  loading.value = true;
  const query = new URLSearchParams({ limit: "100" });
  if (state.value) query.set("state", state.value);
  if (purpose.value) query.set("purpose", purpose.value);
  const cursor = cursors.value.at(-1);
  if (cursor) query.set("after_name", cursor);
  try {
    const response = await request("GET", `/platform/credentials?${query}`);
    rows.value = response.items;
    next.value = response.data.next_after_name;
  } catch (exc) { error.value = failureText(exc); }
  finally { loading.value = false; }
}
function pageForward() { cursors.value.push(next.value); load(); }
function pageBack() { cursors.value.pop(); load(); }
function cancel() { if (form.value) form.value.api_key = ""; form.value = null; }
function create() {
  cancel(); error.value = ""; notice.value = "";
  form.value = { action: "create", name: "", purpose: "catalog_llm", provider: "anthropic", endpoint: "https://api.anthropic.com", api_key: "", active: false, reason: "setup" };
}
function choosePurpose() {
  if (form.value.purpose === "vendor_search") { form.value.provider = "perplexity"; form.value.endpoint = "https://api.perplexity.ai"; }
  else if (form.value.provider === "perplexity") { form.value.provider = "anthropic"; form.value.endpoint = "https://api.anthropic.com"; }
}
async function show(row) {
  try { detail.value = (await request("GET", `/platform/credentials/${row.id}`)).data.credential; }
  catch (exc) { error.value = failureText(exc); }
}
async function begin(row, action) {
  cancel(); error.value = ""; notice.value = ""; busy.value = true;
  try {
    const credential = (await request("GET", `/platform/credentials/${row.id}`)).data.credential;
    detail.value = credential;
    form.value = { action, credential, expected_revision: credential.revision, api_key: "", active: credential.state !== "active", reason: action === "remove" ? "retired" : action === "replace" ? "scheduled_rotation" : "setup" };
  } catch (exc) { error.value = failureText(exc); }
  finally { busy.value = false; }
}
async function save() {
  error.value = ""; notice.value = "";
  const current = form.value;
  if (["create", "replace"].includes(current.action) && !/^[!-~]{16,4096}$/.test(current.api_key)) {
    current.api_key = "";
    error.value = "密钥须为 16 到 4096 个无空白的可打印 ASCII 字符，请重新输入。";
    return;
  }
  let path = "/platform/credentials";
  const body = { reason: current.reason };
  if (current.action === "create") Object.assign(body, { name: current.name, purpose: current.purpose, provider: current.provider, endpoint: current.endpoint, active: current.active, api_key: current.api_key });
  else {
    path += `/${current.credential.id}/${current.action === "set-active" ? "active" : current.action}`;
    body.expected_revision = current.expected_revision;
    if (current.action === "replace") body.api_key = current.api_key;
    if (current.action === "set-active") body.active = current.active;
  }
  // Clear the password input before any await; a rejected write never keeps a key around.
  current.api_key = "";
  busy.value = true;
  try {
    const result = await request("POST", path, body);
    detail.value = result.data.credential;
    cancel(); notice.value = "凭据已保存。";
    await load();
  } catch (exc) {
    error.value = failureText(exc);
    if (exc.code === "revision_conflict") await load();
  } finally { delete body.api_key; busy.value = false; }
}
async function test(row) {
  busy.value = true; error.value = "";
  try { probes.value[row.id] = (await request("POST", `/platform/credentials/${row.id}/test`, { expected_revision: row.revision, mode: "authentication" })).data.probe; }
  catch (exc) {
    const probe = exc.payload?.data?.probe;
    if (probe && Object.hasOwn(outcomes, probe.outcome)) probes.value[row.id] = probe;
    else error.value = failureText(exc);
    if (exc.code === "revision_conflict") await load();
  } finally { busy.value = false; }
}
const actionTitle = (value) => value === "replace" ? "替换密钥" : value === "remove" ? "移除凭据" : "修改启用状态";
const consumerText = (consumer) => consumer.kind === "catalog_model" ? `目录模型 ${consumer.model_id} · 第 ${consumer.model_revision} 版${consumer.default ? " · 默认" : ""}${consumer.enabled ? "" : " · 已停用"}` : `${purposes[consumer.service]}${consumer.selected ? " · 已选用" : " · 未选用"}`;
onMounted(() => load());
onBeforeUnmount(cancel);
</script>

<template>
  <div class="page-header"><div><h2>服务凭据</h2><p class="subtitle">管理平台出站服务凭据，密钥只在提交时传送，保存后仅显示指纹与末四位。</p></div><div class="actions"><el-button :icon="Refresh" :disabled="loading || busy" @click="load()">刷新</el-button><el-button type="primary" :icon="Plus" :disabled="busy" @click="create">添加凭据</el-button></div></div>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
  <el-alert v-if="notice" :title="notice" type="success" :closable="false" role="status" class="section" />
  <el-card v-if="form" shadow="never" class="section">
    <template #header><h3 class="card-title">{{ form.action === 'create' ? '添加凭据' : `${actionTitle(form.action)} ${form.credential.name}` }}</h3></template>
    <form class="panel el-form el-form--label-top" @submit.prevent="save">
      <div v-if="form.action === 'create'" class="grid">
        <el-form-item label="凭据名称"><el-input v-model="form.name" name="credential-name" maxlength="40" required placeholder="main" /></el-form-item>
        <el-form-item label="用途"><el-select v-model="form.purpose" name="credential-purpose" @change="choosePurpose"><el-option v-for="(text, value) in purposes" :key="value" :value="value" :label="text" /></el-select></el-form-item>
        <el-form-item label="服务商"><el-select v-model="form.provider" name="credential-provider" :disabled="form.purpose === 'vendor_search'"><el-option v-for="(text, value) in providers" v-show="form.purpose === 'vendor_search' ? value === 'perplexity' : value !== 'perplexity'" :key="value" :value="value" :label="text" /></el-select></el-form-item>
        <el-form-item label="HTTPS Base URL"><el-input v-model="form.endpoint" name="credential-endpoint" :disabled="form.purpose === 'vendor_search'" required /></el-form-item>
        <div class="hint">名称、用途、服务商和端点保存后不可更改。需要更换端点时须创建新凭据。</div>
        <label class="check"><input v-model="form.active" type="checkbox" name="credential-active" />创建时启用（默认停用）</label>
      </div>
      <el-form-item v-if="['create', 'replace'].includes(form.action)" label="API 密钥（只写）"><el-input v-model="form.api_key" type="password" name="credential-key" autocomplete="new-password" maxlength="4096" required :disabled="busy" /></el-form-item>
      <p v-if="form.action === 'replace'" class="hint">替换后下一次外发请求使用新密钥；已开始的请求仍可能使用旧值。原密钥无法恢复。</p>
      <div v-if="form.action === 'set-active' || form.action === 'remove'" data-testid="credential-impact">
        <p class="notice">{{ form.action === 'remove' ? '移除会清空在线密钥，名称不能复用；引用它的后续调用将失败。' : form.active ? '启用后，引用它的消费对象可发起新请求。' : '停用后，引用它的下一次请求将失败。' }}</p>
        <p>影响的消费对象：</p><ul v-if="form.credential.consumers.length"><li v-for="consumer in form.credential.consumers" :key="consumer.model_id ?? consumer.service">{{ consumerText(consumer) }}</li></ul><p v-else class="hint">当前没有消费对象。</p>
      </div>
      <el-form-item label="原因"><el-select v-model="form.reason" name="credential-reason"><el-option v-for="(text, value) in reasons" v-show="form.action !== 'remove' || ['vendor_revoked', 'incident', 'retired'].includes(value)" :key="value" :value="value" :label="text" /></el-select></el-form-item>
      <div class="actions"><el-button :type="form.action === 'remove' ? 'danger' : 'primary'" native-type="submit" :loading="busy">{{ form.action === 'create' ? '保存凭据' : form.action === 'remove' ? '确认移除' : '保存' }}</el-button><el-button :disabled="busy" @click="cancel">取消</el-button></div>
    </form>
  </el-card>
  <div class="actions section"><el-select v-model="state" placeholder="全部状态" clearable aria-label="状态筛选" :disabled="loading || busy" @change="load(true)"><el-option v-for="(text, value) in states" :key="value" :value="value" :label="text" /></el-select><el-select v-model="purpose" placeholder="全部用途" clearable aria-label="用途筛选" :disabled="loading || busy" @change="load(true)"><el-option v-for="(text, value) in purposes" :key="value" :value="value" :label="text" /></el-select></div>
  <div v-loading="loading" class="table-scroll">
    <table class="data-table"><thead><tr><th>名称 / 用途</th><th>服务商 / 端点</th><th>指纹 / 末四位</th><th>状态 / 修订</th><th>操作</th></tr></thead><tbody>
      <tr v-for="row in rows" :key="row.id"><td><strong>{{ row.name }}</strong><div class="hint">{{ purposes[row.purpose] }}</div></td><td>{{ providers[row.provider] }}<div class="hint">{{ row.endpoint }}</div></td><td class="mono">{{ row.fingerprint }}<div class="hint">…{{ row.last_four }}</div></td><td><span class="tag" :class="row.state === 'active' ? 'success' : ''">{{ states[row.state] }}</span><div class="hint">第 {{ row.revision }} 版 · 密钥第 {{ row.secret_version }} 版</div></td><td><div class="actions"><el-button size="small" :disabled="busy" @click="show(row)">详情</el-button><template v-if="row.state !== 'removed'"><el-button size="small" :disabled="busy" @click="begin(row, 'replace')">替换密钥</el-button><el-button size="small" :disabled="busy" @click="begin(row, 'set-active')">{{ row.state === 'active' ? '停用' : '启用' }}</el-button><el-button size="small" :disabled="busy" @click="test(row)">测试认证</el-button><el-button size="small" type="danger" plain :disabled="busy" @click="begin(row, 'remove')">移除</el-button></template></div><div v-if="probes[row.id]" class="hint" data-testid="credential-probe">{{ outcomes[probes[row.id].outcome] }} · 测试第 {{ probes[row.id].tested_revision }} 版 · {{ probes[row.id].duration_ms }} ms<span v-if="probes[row.id].retry_after_seconds"> · {{ probes[row.id].retry_after_seconds }} 秒后可重试</span><div>仅验证认证元数据，不证明模型授权、额度或推理能力。</div></div></td></tr>
      <tr v-if="!rows.length"><td colspan="5" class="empty">没有符合筛选条件的凭据。</td></tr>
    </tbody></table>
  </div>
  <div class="actions section"><el-button :disabled="cursors.length <= 1 || loading || busy" @click="pageBack">上一页</el-button><span class="hint">第 {{ cursors.length }} 页</span><el-button :disabled="!next || loading || busy" @click="pageForward">下一页</el-button></div>
  <el-card v-if="detail" shadow="never" class="section" data-testid="credential-detail"><template #header><h3 class="card-title">{{ detail.name }} · 凭据详情</h3></template><p class="hint">{{ detail.id }} · {{ detail.updated_by }} · {{ formatTime(detail.updated_at) }}</p><p>第 {{ detail.revision }} 版 · 密钥第 {{ detail.secret_version }} 版 · {{ states[detail.state] }}</p><div data-testid="credential-consumers"><h4>消费对象</h4><ul v-if="detail.consumers.length"><li v-for="consumer in detail.consumers" :key="consumer.model_id ?? consumer.service">{{ consumerText(consumer) }}</li></ul><p v-else class="hint">当前没有消费对象。</p></div><el-button @click="detail = null">关闭详情</el-button></el-card>
  <p class="hint">一次性环境迁移使用 bid platform credential import-env 的受保护文件入口。</p>
</template>
<style scoped>
.card-title { margin: 0; }
.actions { flex-wrap: wrap; }
.actions .el-button + .el-button { margin-left: 0; }
.el-select { min-width: 180px; }
</style>
