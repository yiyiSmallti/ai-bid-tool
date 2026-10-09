<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { request } from "../api.js";
import { formatTime } from "../ui.js";
const config = ref(null), blockers = ref([]), credentials = ref([]), busy = ref(false), error = ref(""), notice = ref("");
const form = reactive({ account_id: "", gateway_id: "", enabled: true, price_revision: 1, fixed_sale_price: "", currency: "USD", workers_credential_id: "", gateway_credential_id: "" });
const workers = computed(() => credentials.value.filter(row => row.purpose === "clef_workers_ai" && row.state === "active"));
const gateways = computed(() => credentials.value.filter(row => row.purpose === "clef_gateway" && row.state === "active"));
const ready = computed(() => /^[a-f0-9]{32}$/.test(form.account_id) && /^[a-z0-9][a-z0-9_-]{0,63}$/.test(form.gateway_id) && String(form.fixed_sale_price).trim() !== "" && Number.isFinite(Number(form.fixed_sale_price)) && Number(form.fixed_sale_price) >= 0 && /^[A-Z]{3}$/.test(form.currency) && Number.isInteger(form.price_revision) && form.price_revision > 0 && workers.value.some(row => row.id === form.workers_credential_id) && gateways.value.some(row => row.id === form.gateway_credential_id));
const labels = { clef_gateway_unchecked: "尚未完成网关安全检查", clef_credential_unavailable: "平台 Clef 凭据不可用", clef_credential_changed: "平台 Clef 凭据已变化，请保存配置并重新检查", clef_configuration_unavailable: "Clef 配置暂不可用", clef_unconfigured: "尚未配置 Clef", clef_disabled: "平台已停用 Clef", clef_gateway_not_checked: "尚未完成网关安全检查", clef_gateway_check_failed: "网关安全检查未通过", clef_gateway_unsafe: "网关未满足安全设置", clef_credentials_missing: "缺少有效的模型令牌或网关令牌", clef_price_missing: "缺少固定调用售价", clef_revision_conflict: "配置修订已变化，请刷新后重试", revision_conflict: "配置修订已变化，请刷新后重试", invalid_input: "配置参数无效，请核对填写内容", forbidden: "需要平台管理员本人登录", invalid_session: "平台登录已失效，请重新登录" };
const message = code => labels[code] ?? `配置未就绪（${code}）`;
let mounted = true;
const controller = new AbortController();
function apply(data) {
  if (!Array.isArray(data.blockers) || data.config && (!Number.isInteger(data.config.revision) || data.config.revision < 1)) throw new Error("invalid Clef configuration");
  config.value = data.config; blockers.value = data.blockers;
  if (data.config) for (const key of Object.keys(form)) form[key] = data.config[key];
}
async function load() {
  busy.value = true; error.value = "";
  try {
    const [settings, modelCredentials, gatewayCredentials] = await Promise.all([
      request("GET", "/platform/clef", undefined, { signal: controller.signal }),
      request("GET", "/platform/credentials?limit=100&purpose=clef_workers_ai&state=active", undefined, { signal: controller.signal }),
      request("GET", "/platform/credentials?limit=100&purpose=clef_gateway&state=active", undefined, { signal: controller.signal }),
    ]);
    if (!mounted) return;
    const rows = [...modelCredentials.items, ...gatewayCredentials.items];
    if (rows.some(row => !["clef_workers_ai", "clef_gateway"].includes(row.purpose) || row.provider !== "cloudflare" || row.state !== "active") || modelCredentials.data.next_after_name || gatewayCredentials.data.next_after_name) throw new Error("invalid credential metadata");
    credentials.value = rows; apply(settings.data);
  } catch (exc) { if (mounted && exc.name !== "AbortError") error.value = message(exc.code ?? "clef_configuration_unavailable"); }
  finally { if (mounted) busy.value = false; }
}
async function save() {
  if (!ready.value || busy.value) return;
  busy.value = true; error.value = ""; notice.value = "";
  try {
    const result = await request("PUT", "/platform/clef", { ...form, expected_revision: config.value?.revision ?? null }, { signal: controller.signal });
    if (!mounted) return;
    apply(result.data); notice.value = "Clef 配置已保存，请运行网关安全检查。";
  } catch (exc) { if (mounted && exc.name !== "AbortError") error.value = message(exc.code ?? "clef_configuration_unavailable"); }
  finally { if (mounted) busy.value = false; }
}
async function check() {
  if (!config.value || busy.value) return;
  busy.value = true; error.value = ""; notice.value = "";
  try {
    const result = await request("POST", "/platform/clef/check", { expected_revision: config.value.revision }, { signal: controller.signal });
    if (!mounted) return;
    apply(result.data); notice.value = result.data.blockers.length ? "网关检查未通过，Clef 调用被阻止。" : "网关安全检查通过。";
  } catch (exc) { if (mounted && exc.name !== "AbortError") { if (exc.payload?.data?.config) apply(exc.payload.data); error.value = message(exc.code ?? "clef_gateway_check_failed"); } }
  finally { if (mounted) busy.value = false; }
}
onMounted(load);
onBeforeUnmount(() => { mounted = false; controller.abort(); });
</script>
<template>
  <div class="page-header"><div><h2>Clef 初筛配置</h2><p class="subtitle">平台统一配置签章存在性初筛；每次调用按固定售价计费，返回 tokens 仅作遥测。</p></div><el-button :loading="busy" @click="load">刷新 Clef 配置</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" class="section" />
  <el-alert v-if="notice" :title="notice" :type="blockers.length ? 'warning' : 'success'" :closable="false" role="status" class="section" />
  <p>需要独立的 Workers AI 模型令牌与网关令牌；请在 <RouterLink to="/platform/credentials">服务凭据</RouterLink> 中创建并启用。单位成员无法读取平台凭据。</p>
  <el-card class="section" shadow="never"><template #header><h3>服务与固定售价</h3></template>
    <el-form label-position="top" @submit.prevent="save">
      <el-form-item label="Cloudflare Account ID"><el-input v-model="form.account_id" aria-label="Cloudflare Account ID" maxlength="32" :disabled="busy" /></el-form-item>
      <el-form-item label="AI Gateway ID"><el-input v-model="form.gateway_id" aria-label="AI Gateway ID" maxlength="64" :disabled="busy" /></el-form-item>
      <el-form-item label="Workers AI 模型凭据"><el-select v-model="form.workers_credential_id" aria-label="Workers AI 模型凭据" :disabled="busy"><el-option v-for="row in workers" :key="row.id" :value="row.id" :label="`${row.name} · 第 ${row.revision} 版`" /></el-select></el-form-item>
      <el-form-item label="网关凭据"><el-select v-model="form.gateway_credential_id" aria-label="网关凭据" :disabled="busy"><el-option v-for="row in gateways" :key="row.id" :value="row.id" :label="`${row.name} · 第 ${row.revision} 版`" /></el-select></el-form-item>
      <el-form-item label="记账币种"><el-input v-model="form.currency" aria-label="记账币种" maxlength="3" :disabled="busy" /></el-form-item>
      <el-form-item :label="`固定每次调用售价（${form.currency}）`"><el-input v-model="form.fixed_sale_price" :aria-label="`固定每次调用售价（${form.currency}）`" inputmode="decimal" :disabled="busy" /></el-form-item>
      <el-form-item label="价格版本"><el-input-number v-model="form.price_revision" aria-label="价格版本" :min="1" :precision="0" :disabled="busy" /></el-form-item>
      <el-checkbox v-model="form.enabled" :disabled="busy">启用平台 Clef 初筛</el-checkbox>
      <p>每个目标页一次调用，同时询问公章与签字存在性。每次检验默认开启，提交人可在预检中关闭。</p>
      <el-button type="primary" native-type="submit" :loading="busy" :disabled="!ready">保存 Clef 配置</el-button>
    </el-form>
  </el-card>
  <el-card class="section" shadow="never"><template #header><h3>网关安全检查</h3></template>
    <p>检查认证开启、日志与 Logpush 关闭、缓存 TTL 为 0、网关自动重试关闭且已设置限流。任一不满足或凭据、价格缺失均阻止初筛调用。</p>
    <el-button :loading="busy" :disabled="!config" @click="check">检查 Clef 网关</el-button>
    <p v-for="code in blockers" :key="code">阻止调用：{{ message(code) }}</p>
    <dl v-if="config?.gateway_check" class="kv" data-testid="clef-gateway-check"><dt>检查时间</dt><dd>{{ formatTime(config.gateway_check.checked_at) }}</dd><dt>认证</dt><dd>开启</dd><dt>日志与 Logpush</dt><dd>关闭</dd><dt>缓存 TTL</dt><dd>{{ config.gateway_check.cache_ttl }}</dd><dt>网关重试</dt><dd>关闭</dd><dt>限流</dt><dd>{{ config.gateway_check.rate_limit_requests }} 次 / {{ config.gateway_check.rate_limit_seconds }} 秒</dd><dt>计费</dt><dd>固定每次 {{ config.fixed_sale_price }} {{ config.currency }} · 价格第 {{ config.price_revision }} 版</dd></dl>
  </el-card>
</template>
<style scoped>h3 { margin:0; } .el-select { width:100%; } .el-checkbox { display:block;margin-bottom:12px; }</style>
