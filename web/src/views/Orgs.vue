<script setup>
import { computed, onMounted, ref } from "vue";
import { count, money, request } from "../api.js";

const orgs = ref([]);
const usage = ref({});
const totals = ref({ calls: 0, tokens: 0, charge: 0 });
const currency = ref("");
const adjusting = ref(null);
const adjustment = ref({ mode: "add", amount: "", reason: "" });
const error = ref("");
const creating = ref(false);
const form = ref({ name: "", admin_email: "" });
const created = ref(null);

const activeCount = computed(() => orgs.value.filter((org) => org.active).length);

async function load() {
  error.value = "";
  try {
    const [list, month] = await Promise.all([
      request("GET", "/platform/orgs"),
      request("GET", "/platform/usage"),
    ]);
    orgs.value = list.items;
    totals.value = month.data.totals;
    currency.value = month.data.currency;
    const byOrg = {};
    for (const row of month.items) {
      const entry = (byOrg[row.org_id] ??= { tokens: 0, charge: 0 });
      entry.tokens += row.tokens;
      entry.charge += row.charge;
    }
    usage.value = byOrg;
  } catch (exc) {
    error.value = exc.message;
  }
}

async function create() {
  error.value = "";
  if (!form.value.name.trim() || !form.value.admin_email.includes("@")) {
    error.value = "填写单位名称和管理员邮箱";
    return;
  }
  try {
    const result = await request("POST", "/platform/orgs", form.value);
    created.value = {
      ...result.data,
      link: result.data.setup_url ? window.location.origin + result.data.setup_url : null,
    };
    form.value = { name: "", admin_email: "" };
    creating.value = false;
    await load();
  } catch (exc) {
    error.value = exc.message;
  }
}

function startAdjust(org) {
  adjusting.value = org;
  adjustment.value = { mode: "add", amount: "", reason: "" };
}

async function adjust() {
  error.value = "";
  const amount = Number(adjustment.value.amount);
  if (adjustment.value.amount === "" || Number.isNaN(amount) || !adjustment.value.reason.trim()) {
    error.value = "填写金额和原因";
    return;
  }
  try {
    await request("POST", `/platform/orgs/${adjusting.value.id}/balance`, { ...adjustment.value, amount });
    adjusting.value = null;
    await load();
  } catch (exc) {
    error.value = exc.code === "currency_mismatch" ? "该单位余额使用其他币种，无法调整" : exc.message;
  }
}

async function setActive(org, active) {
  const verb = active ? "启用" : "停用";
  if (!window.confirm(`${verb}「${org.name}」？停用后该单位所有登录和令牌立即失效。`)) return;
  try {
    await request("POST", `/platform/orgs/${org.id}/active`, { active });
    await load();
  } catch (exc) {
    error.value = exc.message;
  }
}

onMounted(load);
</script>

<template>
  <div class="toolbar">
    <h2>单位</h2>
    <button class="primary" @click="creating = !creating">开通单位</button>
  </div>
  <div class="cards">
    <div class="card"><div class="label">启用中单位</div><div class="value">{{ activeCount }}</div></div>
    <div class="card"><div class="label">本月 token</div><div class="value">{{ count(totals.tokens) }}</div></div>
    <div class="card"><div class="label">本月应收</div><div class="value">{{ money(totals.charge, currency) }}</div></div>
  </div>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <form v-if="creating" class="panel" @submit.prevent="create">
    <div class="grid">
      <label>单位名称<input v-model="form.name" name="org-name" /></label>
      <label>管理员邮箱<input v-model="form.admin_email" type="email" name="admin-email" /></label>
    </div>
    <p><button class="primary" type="submit">开通</button></p>
  </form>
  <div v-if="created" class="notice" data-testid="created-notice">
    <template v-if="created.link">
      已开通。把下面的一次性链接交给 {{ created.admin_email }}，用于设置密码，24 小时内有效，只显示这一次：
      <p><code data-testid="setup-link">{{ created.link }}</code></p>
    </template>
    <template v-else>已开通，{{ created.admin_email }} 用现有密码即可登录。</template>
  </div>
  <form v-if="adjusting" class="panel" @submit.prevent="adjust" data-testid="adjust-panel">
    <p>调整「{{ adjusting.name }}」的余额，当前 {{ money(adjusting.balance, adjusting.currency ?? currency) }}</p>
    <div class="grid">
      <label>方式
        <select v-model="adjustment.mode" name="adjust-mode"><option value="add">增减（负数为扣减）</option><option value="set">设为</option></select>
      </label>
      <label>金额（{{ currency }}）<input v-model="adjustment.amount" type="number" step="0.01" name="adjust-amount" /></label>
      <label>原因<input v-model="adjustment.reason" maxlength="500" name="adjust-reason" /></label>
    </div>
    <p><button class="primary" type="submit">确认调整</button> <button type="button" @click="adjusting = null">取消</button></p>
  </form>
  <table>
    <thead>
      <tr><th>名称</th><th>状态</th><th class="num">成员</th><th>管理员</th><th class="num">余额</th><th class="num">本月 token</th><th class="num">本月应收</th><th></th></tr>
    </thead>
    <tbody>
      <tr v-for="org in orgs" :key="org.id">
        <td>{{ org.name }}</td>
        <td><span :class="['badge', org.active ? 'ok' : '']">{{ org.active ? "启用" : "已停用" }}</span></td>
        <td class="num">{{ org.member_count }}</td>
        <td>{{ org.admin_emails.join("，") || "—" }}</td>
        <td class="num">{{ money(org.balance, org.currency ?? currency) }}</td>
        <td class="num">{{ count(usage[org.id]?.tokens) }}</td>
        <td class="num">{{ money(usage[org.id]?.charge ?? 0, currency) }}</td>
        <td class="num">
          <button @click="startAdjust(org)">调整余额</button>
          <button v-if="org.active" @click="setActive(org, false)">停用</button>
          <button v-else @click="setActive(org, true)">启用</button>
        </td>
      </tr>
      <tr v-if="!orgs.length"><td colspan="8" class="hint">还没有单位。点「开通单位」创建第一个。</td></tr>
    </tbody>
  </table>
  <p class="hint">这里只显示账号与汇总数据，不显示任何单位的任务、文件或要求。</p>
</template>
