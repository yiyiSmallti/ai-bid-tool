<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { Plus } from "@element-plus/icons-vue";
import { count, money, request } from "../api.js";

const blank = () => ({
  id: "",
  capability: "llm_extract",
  provider: "anthropic",
  model: "claude-opus-5-5",
  base_url: "",
  credential: "",
  vendor_input_usd_per_mtok: 0,
  vendor_output_usd_per_mtok: 0,
  sale_input_per_mtok: 0,
  sale_output_per_mtok: 0,
  default: false,
  enabled: true,
  levels: [],
  default_reasoning: null,
  expected_revision: null,
});
const blankLevel = () => ({ name: "", label: "", options: "{}", batch_chars: 8000, effort: "" });
const models = ref([]);
const currency = ref("");
const credentials = ref([]);
const form = ref(null);
const normalizedEndpoint = (provider, url) => {
  try { const parsed = new URL(url || (provider === "anthropic" ? "https://api.anthropic.com" : "")); if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.search || parsed.hash || (parsed.port && parsed.port !== "443")) return null; return `https://${parsed.host.toLowerCase()}${parsed.pathname.replace(/\/+$/, "")}`; } catch { return null; }
};
const matchingCredentials = computed(() => form.value ? credentials.value.filter((credential) => credential.purpose === "catalog_llm" && credential.state === "active" && credential.provider === form.value.provider && credential.endpoint === normalizedEndpoint(form.value.provider, form.value.base_url)) : []);
watch(() => [form.value?.provider, form.value?.base_url], () => { if (form.value?.credential && !matchingCredentials.value.some((credential) => credential.name === form.value.credential)) form.value.credential = ""; });
const error = ref("");
const tests = ref({});

async function load() {
  try {
    const result = await request("GET", "/platform/models");
    const available = [];
    let cursor = null;
    do {
      const query = new URLSearchParams({ purpose: "catalog_llm", limit: "100" });
      if (cursor) query.set("after_name", cursor);
      const page = await request("GET", `/platform/credentials?${query}`);
      available.push(...page.items); cursor = page.data.next_after_name;
    } while (cursor);
    credentials.value = available;
    models.value = result.items;
    currency.value = result.data.currency;
  } catch (exc) {
    error.value = exc.message;
  }
}

function edit(model) {
  form.value = model
    ? {
        ...blank(),
        ...model,
        base_url: model.base_url ?? "",
        expected_revision: model.revision,
        // Request options are edited as JSON text and parsed on save.
        levels: (model.reasoning ?? []).map((level) => ({
          name: level.name,
          label: level.label ?? "",
          options: JSON.stringify(level.request_options ?? {}),
          batch_chars: level.batch_chars,
          effort: level.effort ?? "",
        })),
      }
    : blank();
}

function addLevel() {
  form.value.levels.push(blankLevel());
  if (form.value.levels.length === 1) form.value.default_reasoning = null;
}

function removeLevel(index) {
  const [removed] = form.value.levels.splice(index, 1);
  if (removed.name === form.value.default_reasoning) form.value.default_reasoning = null;
}

// Parsed reasoning levels, or a list of problems in Chinese.
function levelsOf(levels) {
  const parsed = [];
  const issues = [];
  for (const level of levels) {
    const name = level.name.trim().toLowerCase();
    if (!/^[a-z0-9_-]{1,20}$/.test(name)) issues.push("档位名称用服务商的官方值，例如 low、high、max（小写字母、数字、- 和 _）");
    let options = {};
    try {
      options = JSON.parse(level.options || "{}");
      if (!options || typeof options !== "object" || Array.isArray(options)) throw new Error();
    } catch {
      issues.push(`档位 ${name || "（未命名）"} 的请求参数必须是 JSON 对象`);
    }
    const batch = Number(level.batch_chars);
    if (!Number.isInteger(batch) || batch < 1000 || batch > 200000) issues.push("每批字数必须是 1000 到 200000 之间的整数");
    const effort = level.effort.trim();
    if (effort && !/^[a-z]{1,10}$/.test(effort)) issues.push("Anthropic effort 只能是小写字母，例如 high");
    parsed.push({ name, label: level.label.trim() || null, request_options: options, batch_chars: batch, effort: effort || null });
  }
  const names = parsed.map((level) => level.name);
  if (new Set(names).size !== names.length) issues.push("档位名称不能重复");
  return { parsed, issues };
}

// Mirrors PlatformModelSet so mistakes are explained before the request is sent.
function problems(body) {
  const issues = [];
  if (!/^[a-z0-9][a-z0-9_-]{0,39}$/.test(body.id)) issues.push("标识只能用小写字母、数字、- 和 _，以字母或数字开头，最多 40 个字符");
  if (!body.model) issues.push("请填写模型名称");
  if (!matchingCredentials.value.some((credential) => credential.name === body.credential)) issues.push("请选择用途、服务商和端点匹配的目录模型凭据");
  if (body.base_url && !/^https:\/\/[^\s/]+(\/\S*)?$/.test(body.base_url)) issues.push("Base URL 必须以 https:// 开头");
  if (body.provider === "openai" && !body.base_url) issues.push("OpenAI 兼容服务需要填写 Base URL");
  for (const key of ["vendor_input_usd_per_mtok", "vendor_output_usd_per_mtok", "sale_input_per_mtok", "sale_output_per_mtok"]) {
    if (!Number.isFinite(body[key]) || body[key] < 0) issues.push("价格必须是不小于 0 的数字");
  }
  if (body.default && !body.enabled) issues.push("默认模型必须启用");
  if (body.reasoning.length && !body.reasoning.some((level) => level.name === body.default_reasoning))
    issues.push("请选择一个默认档位（服务商的官方默认值）");
  return [...new Set(issues)];
}

async function save() {
  error.value = "";
  const body = {
    ...form.value,
    id: form.value.id.trim().toLowerCase(),
    model: form.value.model.trim(),
    credential: form.value.credential.trim().toLowerCase(),
    base_url: form.value.base_url.trim() || null,
  };
  for (const key of Object.keys(body)) if (key.endsWith("_per_mtok")) body[key] = Number(body[key]);
  for (const key of ["revision", "updated_by", "updated_at", "credential_configured", "levels"]) delete body[key];
  const levels = levelsOf(form.value.levels);
  body.reasoning = levels.parsed;
  body.default_reasoning = levels.parsed.length ? form.value.default_reasoning : null;
  const issues = [...levels.issues, ...problems(body)];
  if (issues.length) {
    error.value = issues.join("；");
    return;
  }
  try {
    await request("POST", "/platform/models", body);
    form.value = null;
    await load();
  } catch (exc) {
    error.value =
      exc.code === "revision_conflict" ? "模型已被他人修改，请刷新后重试" : `保存失败：${exc.message}`;
  }
}

async function disableModel(model) {
  error.value = "";
  // Keep the catalog fields byte-for-byte: this path may only disable the existing
  // consumer, including one whose credential was removed. It cannot change a binding.
  const body = { ...model, expected_revision: model.revision, enabled: false, default: false };
  for (const key of ["revision", "updated_by", "updated_at", "credential_configured"]) delete body[key];
  try {
    await request("POST", "/platform/models", body);
    form.value = null;
    await load();
  } catch (exc) {
    error.value = exc.code === "revision_conflict" ? "模型已被他人修改，请刷新后重试" : "停用失败，请刷新后重试";
  }
}

async function test(model) {
  tests.value[model.id] = { running: true };
  try {
    tests.value[model.id] = (await request("POST", `/platform/models/${model.id}/test`)).data;
  } catch (exc) {
    tests.value[model.id] = { passed: false, error: { code: exc.code, message: exc.message } };
  }
}

onMounted(load);
</script>

<template>
  <div class="page-header">
    <div><h2>模型</h2><p class="subtitle">设为默认的模型用于所有单位的要求抽取，并按售价计入应收。</p></div>
    <el-button type="primary" :icon="Plus" @click="edit(null)">添加模型</el-button>
  </div>
  <p class="notice">服务商密钥通过服务凭据页面维护。模型选择用途、服务商和端点匹配的已启用凭据。</p>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
  <el-card v-if="form" shadow="never" class="section">
    <template #header><h3 class="card-title">{{ form.expected_revision !== null ? `编辑模型 ${form.id}` : "添加模型" }}</h3></template>
    <form class="panel el-form el-form--label-top" @submit.prevent="save">
      <div class="grid three">
        <el-form-item label="标识"><el-input v-model="form.id" :disabled="form.expected_revision !== null" name="model-id" placeholder="opus-standard" /></el-form-item>
        <el-form-item label="服务商"><el-select v-model="form.provider" name="provider"><el-option value="anthropic" label="Anthropic" /><el-option value="openai" label="OpenAI 兼容" /></el-select></el-form-item>
        <el-form-item label="模型"><el-input v-model="form.model" name="model" /></el-form-item>
        <el-form-item label="Base URL（可选，https）"><el-input v-model="form.base_url" name="base-url" /></el-form-item>
        <el-form-item label="服务凭据"><el-select v-model="form.credential" name="credential" data-testid="model-credential-select" placeholder="选择匹配的凭据"><el-option v-for="credential in matchingCredentials" :key="credential.id" :value="credential.name" :label="`${credential.name} · …${credential.last_four}`" /></el-select></el-form-item>
        <div class="hint cred-hint"><RouterLink to="/platform/credentials">管理服务凭据</RouterLink>。新绑定只显示匹配的已启用凭据；已有失效引用可使用列表中的“停用”停止该模型。“已配置”不代表厂商认证或额度已验证。</div>
      </div>
      <div class="grid four">
        <el-form-item label="成本价 输入（USD/百万 token）"><el-input v-model="form.vendor_input_usd_per_mtok" type="number" min="0" step="0.01" name="vendor-input" /></el-form-item>
        <el-form-item label="成本价 输出"><el-input v-model="form.vendor_output_usd_per_mtok" type="number" min="0" step="0.01" name="vendor-output" /></el-form-item>
        <el-form-item :label="`售价 输入（${currency}/百万 token）`"><el-input v-model="form.sale_input_per_mtok" type="number" min="0" step="0.01" name="sale-input" /></el-form-item>
        <el-form-item :label="`售价 输出（${currency}）`"><el-input v-model="form.sale_output_per_mtok" type="number" min="0" step="0.01" name="sale-output" /></el-form-item>
      </div>
      <div class="actions"><label class="check"><input v-model="form.enabled" type="checkbox" name="enabled" />启用</label><label class="check"><input v-model="form.default" type="checkbox" name="default" />设为默认</label></div>
      <h4>推理强度</h4>
      <p class="hint">
        按服务商文档登记该模型的官方档位，例如智谱 GLM-5.3 的 low、high、max，请求参数写
        {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}。用户抽取时可选择档位，不选时用默认档位。不登记则不分档。
      </p>
      <div v-if="form.levels.length" class="table-scroll">
        <table class="data-table" data-testid="levels">
          <thead><tr><th>默认</th><th>名称</th><th>说明</th><th>请求参数（JSON）</th><th class="num">每批字数</th><th v-if="form.provider === 'anthropic'">effort</th><th></th></tr></thead>
          <tbody>
            <tr v-for="(level, index) in form.levels" :key="index">
              <td><input v-model="form.default_reasoning" type="radio" :value="level.name.trim().toLowerCase()" name="default-level" :aria-label="`默认档位 ${index + 1}`" /></td>
              <td><el-input v-model="level.name" :name="`level-name-${index}`" placeholder="high" class="narrow" /></td>
              <td><el-input v-model="level.label" :name="`level-label-${index}`" placeholder="增强推理" class="narrow" /></td>
              <td><el-input v-model="level.options" type="textarea" :name="`level-options-${index}`" :rows="2" class="wide" /></td>
              <td class="num"><el-input v-model="level.batch_chars" type="number" min="1000" step="1000" :name="`level-batch-${index}`" class="narrow" /></td>
              <td v-if="form.provider === 'anthropic'"><el-input v-model="level.effort" :name="`level-effort-${index}`" placeholder="high" class="narrow" /></td>
              <td><el-button size="small" type="danger" link @click="removeLevel(index)">删除</el-button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="actions"><el-button :icon="Plus" @click="addLevel">添加档位</el-button></div>
      <div class="actions"><el-button type="primary" native-type="submit">保存</el-button><el-button @click="form = null">取消</el-button></div>
    </form>
  </el-card>
  <div class="table-scroll">
    <table class="data-table">
      <thead><tr><th>标识</th><th>服务商 / 模型</th><th>凭据</th><th class="num">成本价 入/出（USD）</th><th class="num">售价 入/出（{{ currency }}）</th><th>状态</th><th class="num">操作</th></tr></thead>
      <tbody>
        <tr v-for="model in models" :key="model.id">
          <td><strong>{{ model.id }}</strong><div class="hint">第 {{ model.revision }} 版 · {{ model.updated_by }}</div></td>
          <td>
            {{ model.provider }}<div class="hint">{{ model.model }}</div>
            <div v-if="model.reasoning?.length" class="hint" data-testid="levels-summary">推理强度：<span v-for="(level, index) in model.reasoning" :key="level.name">{{ index ? " / " : "" }}{{ level.name }}{{ level.name === model.default_reasoning ? "（默认）" : "" }}</span></div>
          </td>
          <td>{{ model.credential }} <span class="tag" :class="model.credential_configured ? 'success' : 'danger'">{{ model.credential_configured ? "已配置" : "未配置" }}</span></td>
          <td class="num">{{ model.vendor_input_usd_per_mtok }} / {{ model.vendor_output_usd_per_mtok }}</td>
          <td class="num">{{ model.sale_input_per_mtok }} / {{ model.sale_output_per_mtok }}</td>
          <td><div class="tags"><span v-if="model.default" class="tag success">默认</span><span v-if="!model.enabled" class="tag">已停用</span></div></td>
          <td class="num">
            <div class="row-actions"><el-button size="small" @click="edit(model)">编辑</el-button><el-button v-if="model.enabled" size="small" @click="disableModel(model)">停用</el-button><el-button size="small" @click="test(model)">测试</el-button></div>
            <div v-if="tests[model.id]" class="hint test-result" data-testid="test-result">
              <template v-if="tests[model.id].running">测试中…</template>
              <template v-else-if="tests[model.id].levels">
                <div v-for="level in tests[model.id].levels" :key="level.reasoning" :data-testid="`level-result-${level.reasoning}`">
                  {{ level.reasoning }}：<template v-if="level.passed">通过 · {{ count(level.usage.tokens) }} token · {{ money(level.usage.charge, currency) }}</template
                  ><template v-else-if="level.error.code === 'provider_quota_exhausted'">未通过：服务商额度已用完或套餐不可用（{{ level.error.message }}）</template
                  ><template v-else>未通过：{{ level.error.code }}（{{ level.error.message }}）</template>
                </div>
              </template>
              <template v-else-if="tests[model.id].passed">通过 · {{ count(tests[model.id].usage.tokens) }} token · {{ money(tests[model.id].usage.charge, currency) }}</template>
              <template v-else-if="tests[model.id].error.code === 'provider_quota_exhausted'">未通过：服务商额度已用完或套餐不可用（{{ tests[model.id].error.message }}）</template>
              <template v-else>未通过：{{ tests[model.id].error.code }}（{{ tests[model.id].error.message }}）</template>
            </div>
          </td>
        </tr>
        <tr v-if="!models.length"><td colspan="7" class="empty">还没有模型。添加一个并设为默认后，单位的要求抽取会使用它。</td></tr>
      </tbody>
    </table>
  </div>
</template>
<style scoped>
.card-title { margin: 0; }
.grid.three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.grid.four { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.cred-hint { align-self: center; }
.narrow { width: 120px; }
.wide { min-width: 260px; }
.row-actions { display: inline-flex; gap: 6px; }
.row-actions .el-button + .el-button { margin-left: 0; }
.test-result { margin-top: 6px; text-align: left; }
@media (max-width: 900px) { .grid.three, .grid.four { grid-template-columns: minmax(0, 1fr); } }
</style>
