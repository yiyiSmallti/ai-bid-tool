<script setup>
import { onMounted, ref } from "vue";
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
  expected_revision: null,
});
const models = ref([]);
const currency = ref("");
const form = ref(null);
const error = ref("");
const tests = ref({});

async function load() {
  try {
    const result = await request("GET", "/platform/models");
    models.value = result.items;
    currency.value = result.data.currency;
  } catch (exc) {
    error.value = exc.message;
  }
}

function edit(model) {
  form.value = model
    ? { ...blank(), ...model, base_url: model.base_url ?? "", expected_revision: model.revision }
    : blank();
}

// Mirrors PlatformModelSet so mistakes are explained before the request is sent.
function problems(body) {
  const issues = [];
  if (!/^[a-z0-9][a-z0-9_-]{0,39}$/.test(body.id)) issues.push("标识只能用小写字母、数字、- 和 _，以字母或数字开头，最多 40 个字符");
  if (!body.model) issues.push("请填写模型名称");
  if (!/^[a-z0-9_]{1,40}$/.test(body.credential)) issues.push("凭据名只能用小写字母、数字和 _，例如 main");
  if (body.base_url && !/^https:\/\/[^\s/]+(\/\S*)?$/.test(body.base_url)) issues.push("Base URL 必须以 https:// 开头");
  if (body.provider === "openai" && !body.base_url) issues.push("OpenAI 兼容服务需要填写 Base URL");
  for (const key of ["vendor_input_usd_per_mtok", "vendor_output_usd_per_mtok", "sale_input_per_mtok", "sale_output_per_mtok"]) {
    if (!Number.isFinite(body[key]) || body[key] < 0) issues.push("价格必须是不小于 0 的数字");
  }
  if (body.default && !body.enabled) issues.push("默认模型必须启用");
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
  for (const key of ["revision", "updated_by", "updated_at", "credential_configured"]) delete body[key];
  const issues = problems(body);
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
  <div class="toolbar">
    <h2>模型</h2>
    <button class="primary" @click="edit(null)">添加模型</button>
  </div>
  <p class="hint">
    服务商密钥只保存在部署环境变量 BID_PLATFORM_CREDENTIAL_&lt;凭据名&gt; 中，页面只显示是否已配置。设为默认的模型用于所有单位的要求抽取，并按售价计入应收。
  </p>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <form v-if="form" class="panel" @submit.prevent="save">
    <div class="grid">
      <label>标识<input v-model="form.id" :disabled="form.expected_revision !== null" name="model-id" placeholder="opus-standard" /></label>
      <label>服务商
        <select v-model="form.provider" name="provider"><option value="anthropic">Anthropic</option><option value="openai">OpenAI 兼容</option></select>
      </label>
      <label>模型<input v-model="form.model" name="model" /></label>
      <label>Base URL（可选，https）<input v-model="form.base_url" name="base-url" /></label>
      <label>凭据名<input v-model="form.credential" name="credential" placeholder="main" /></label>
      <span class="hint">凭据名 main 对应环境变量 BID_PLATFORM_CREDENTIAL_MAIN；标识和凭据名只用小写字母、数字、_（标识还可用 -）。</span>
      <label>成本价 输入（USD/百万 token）<input v-model="form.vendor_input_usd_per_mtok" type="number" min="0" step="0.01" name="vendor-input" /></label>
      <label>成本价 输出<input v-model="form.vendor_output_usd_per_mtok" type="number" min="0" step="0.01" name="vendor-output" /></label>
      <label>售价 输入（{{ currency }}/百万 token）<input v-model="form.sale_input_per_mtok" type="number" min="0" step="0.01" name="sale-input" /></label>
      <label>售价 输出（{{ currency }}）<input v-model="form.sale_output_per_mtok" type="number" min="0" step="0.01" name="sale-output" /></label>
      <label class="inline"><input v-model="form.enabled" type="checkbox" name="enabled" />启用</label>
      <label class="inline"><input v-model="form.default" type="checkbox" name="default" />设为默认</label>
    </div>
    <p><button class="primary" type="submit">保存</button> <button type="button" @click="form = null">取消</button></p>
  </form>
  <table>
    <thead>
      <tr><th>标识</th><th>服务商 / 模型</th><th>凭据</th><th class="num">成本价 入/出（USD）</th><th class="num">售价 入/出（{{ currency }}）</th><th>状态</th><th></th></tr>
    </thead>
    <tbody>
      <tr v-for="model in models" :key="model.id">
        <td>{{ model.id }}<div class="hint">第 {{ model.revision }} 版 · {{ model.updated_by }}</div></td>
        <td>{{ model.provider }}<div class="hint">{{ model.model }}</div></td>
        <td>
          {{ model.credential }}
          <span :class="['badge', model.credential_configured ? 'ok' : 'bad']">{{ model.credential_configured ? "已配置" : "未配置" }}</span>
        </td>
        <td class="num">{{ model.vendor_input_usd_per_mtok }} / {{ model.vendor_output_usd_per_mtok }}</td>
        <td class="num">{{ model.sale_input_per_mtok }} / {{ model.sale_output_per_mtok }}</td>
        <td>
          <span v-if="model.default" class="badge ok">默认</span>
          <span v-if="!model.enabled" class="badge">已停用</span>
        </td>
        <td class="num">
          <button @click="edit(model)">编辑</button>
          <button @click="test(model)">测试</button>
          <div v-if="tests[model.id]" class="hint" data-testid="test-result">
            <template v-if="tests[model.id].running">测试中…</template>
            <template v-else-if="tests[model.id].passed">
              通过 · {{ count(tests[model.id].usage.tokens) }} token · {{ money(tests[model.id].usage.charge, currency) }}
            </template>
            <template v-else-if="tests[model.id].error.code === 'provider_quota_exhausted'">
              未通过：服务商额度已用完或套餐不可用（{{ tests[model.id].error.message }}）
            </template>
            <template v-else>未通过：{{ tests[model.id].error.code }}（{{ tests[model.id].error.message }}）</template>
          </div>
        </td>
      </tr>
      <tr v-if="!models.length"><td colspan="7" class="hint">还没有模型。添加一个并设为默认后，单位的要求抽取会使用它。</td></tr>
    </tbody>
  </table>
</template>
