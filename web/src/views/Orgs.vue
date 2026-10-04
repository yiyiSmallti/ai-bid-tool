<script setup>
import { computed, onMounted, ref } from "vue";
import { Plus } from "@element-plus/icons-vue";
import { count, money, request } from "../api.js";
import { confirmAction } from "../ui.js";

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
  if (!(await confirmAction(`${verb}「${org.name}」？停用后该单位所有登录和令牌立即失效。`, `${verb}单位`, "确定", !active))) return;
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
  <div class="page-header">
    <div><h2>单位</h2><p class="subtitle">这里只显示账号与汇总数据，不显示任何单位的任务、文件或要求。</p></div>
    <el-button type="primary" :icon="Plus" @click="creating = !creating">开通单位</el-button>
  </div>
  <div class="stat-grid">
    <div class="stat"><span class="label">启用中单位</span><span class="value">{{ activeCount }}</span></div>
    <div class="stat"><span class="label">本月 token</span><span class="value">{{ count(totals.tokens) }}</span></div>
    <div class="stat"><span class="label">本月应收</span><span class="value">{{ money(totals.charge, currency) }}</span></div>
  </div>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
  <el-card v-if="creating" shadow="never" class="section">
    <template #header><h3 class="card-title">开通单位</h3></template>
    <form class="panel el-form el-form--label-top" @submit.prevent="create">
      <div class="grid">
        <el-form-item label="单位名称"><el-input v-model="form.name" name="org-name" /></el-form-item>
        <el-form-item label="管理员邮箱"><el-input v-model="form.admin_email" type="email" name="admin-email" /></el-form-item>
      </div>
      <el-button type="primary" native-type="submit">开通</el-button>
    </form>
  </el-card>
  <el-alert v-if="created" type="success" :closable="false" show-icon role="note" class="section" data-testid="created-notice">
    <template v-if="created.link">
      已开通。把下面的一次性链接交给 {{ created.admin_email }}，用于设置密码，24 小时内有效，只显示这一次：
      <p><code data-testid="setup-link">{{ created.link }}</code></p>
    </template>
    <template v-else>已开通，{{ created.admin_email }} 用现有密码即可登录。</template>
  </el-alert>
  <el-card v-if="adjusting" shadow="never" class="section">
    <template #header><h3 class="card-title">调整「{{ adjusting.name }}」的余额</h3></template>
    <form class="panel el-form el-form--label-top" data-testid="adjust-panel" @submit.prevent="adjust">
      <p class="hint">当前余额 {{ money(adjusting.balance, adjusting.currency ?? currency) }}</p>
      <div class="grid three">
        <el-form-item label="方式"><el-select v-model="adjustment.mode" name="adjust-mode"><el-option value="add" label="增减（负数为扣减）" /><el-option value="set" label="设为" /></el-select></el-form-item>
        <el-form-item :label="`金额（${currency}）`"><el-input v-model="adjustment.amount" type="number" step="0.01" name="adjust-amount" /></el-form-item>
        <el-form-item label="原因"><el-input v-model="adjustment.reason" maxlength="500" name="adjust-reason" /></el-form-item>
      </div>
      <div class="actions"><el-button type="primary" native-type="submit">确认调整</el-button><el-button @click="adjusting = null">取消</el-button></div>
    </form>
  </el-card>
  <div class="table-scroll">
    <table class="data-table">
      <thead><tr><th>名称</th><th>状态</th><th class="num">成员</th><th>管理员</th><th class="num">余额</th><th class="num">本月 token</th><th class="num">本月应收</th><th class="num">操作</th></tr></thead>
      <tbody>
        <tr v-for="org in orgs" :key="org.id">
          <td><strong>{{ org.name }}</strong></td>
          <td><span class="tag" :class="org.active ? 'success' : ''">{{ org.active ? "启用" : "已停用" }}</span></td>
          <td class="num">{{ org.member_count }}</td>
          <td>{{ org.admin_emails.join("，") || "—" }}</td>
          <td class="num">{{ money(org.balance, org.currency ?? currency) }}</td>
          <td class="num">{{ count(usage[org.id]?.tokens) }}</td>
          <td class="num">{{ money(usage[org.id]?.charge ?? 0, currency) }}</td>
          <td class="num"><div class="row-actions">
            <el-button size="small" @click="startAdjust(org)">调整余额</el-button>
            <el-button v-if="org.active" size="small" type="danger" plain @click="setActive(org, false)">停用</el-button>
            <el-button v-else size="small" type="success" plain @click="setActive(org, true)">启用</el-button>
          </div></td>
        </tr>
        <tr v-if="!orgs.length"><td colspan="8" class="empty">还没有单位。点「开通单位」创建第一个。</td></tr>
      </tbody>
    </table>
  </div>
</template>
<style scoped>
.card-title { margin: 0; }
.grid.three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.row-actions { display: inline-flex; gap: 6px; }
.row-actions .el-button + .el-button { margin-left: 0; }
@media (max-width: 720px) { .grid.three { grid-template-columns: minmax(0, 1fr); } }
</style>
