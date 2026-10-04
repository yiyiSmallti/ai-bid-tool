<script setup>
import { onMounted, ref } from "vue";
import { money, request } from "../api.js";
import { formatTime } from "../org.js";

const overview = ref(null);
const entries = ref([]);
const code = ref("");
const error = ref("");
const message = ref("");
const busy = ref(false);
const kinds = { redeem: "卡密充值", adjust: "平台调整", usage: "模型调用" };

async function load() {
  try {
    const result = await request("GET", "/billing", undefined, { org: true });
    overview.value = result.data;
    entries.value = result.items;
  } catch (exc) {
    error.value =
      exc.code === "org_inactive"
        ? "该单位已停用，请联系平台管理员"
        : exc.status === 403
          ? "只有单位管理员可以查看余额与充值"
          : exc.message;
  }
}

async function redeem() {
  error.value = "";
  message.value = "";
  if (code.value.replace(/[\s-]/g, "").length !== 16) {
    error.value = "卡密是 16 位字母和数字，如 ABCD-EFGH-JKMN-PQRS";
    return;
  }
  busy.value = true;
  try {
    const result = await request("POST", "/billing/redeem", { code: code.value }, { org: true });
    message.value = `充值成功：+${money(result.data.amount, result.data.currency)}`;
    code.value = "";
    await load();
  } catch (exc) {
    error.value =
      exc.code === "invalid_card"
        ? "卡密无效、已使用、已作废或已过期"
        : exc.status === 429
          ? "失败次数过多，请 1 小时后再试"
          : exc.message;
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="page-header"><div><h2>余额与充值</h2><p class="subtitle">平台模型的要求抽取和起草按用量从余额扣费。</p></div></div>
  <el-alert v-if="error && !overview" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
  <div v-if="overview" class="billing-top">
    <div class="stat balance-card" :class="{ danger: overview.balance <= 0 }">
      <span class="label">当前余额</span>
      <span class="value balance" data-testid="balance">{{ money(overview.balance, overview.currency) }}</span>
      <span v-if="overview.balance <= 0" class="error">余额不足，平台模型的要求抽取已暂停。充值后即可继续。</span>
    </div>
    <el-card shadow="never" class="redeem">
      <template #header><h3>卡密充值</h3></template>
      <el-form label-position="top" @submit.prevent="redeem">
        <el-form-item label="充值卡密"><el-input v-model="code" name="card-code" autocomplete="off" placeholder="ABCD-EFGH-JKMN-PQRS" /></el-form-item>
        <el-button type="primary" native-type="submit" :loading="busy">充值</el-button>
        <el-alert v-if="message" :title="message" type="success" show-icon :closable="false" role="status" class="top" />
        <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="top" />
      </el-form>
    </el-card>
  </div>
  <el-card shadow="never" class="section" body-class="flush">
    <template #header><h3>流水</h3></template>
    <div class="table-scroll flat">
      <table class="data-table">
        <thead><tr><th>时间</th><th>类型</th><th class="num">金额</th><th class="num">变动后余额</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="entry in entries" :key="entry.id">
            <td>{{ formatTime(entry.created_at) }}</td>
            <td><span class="tag" :class="entry.kind === 'redeem' ? 'success' : entry.kind === 'usage' ? 'primary' : ''">{{ kinds[entry.kind] ?? entry.kind }}</span></td>
            <td class="num" :class="entry.amount > 0 ? 'plus' : 'minus'">{{ entry.amount > 0 ? "+" : "" }}{{ money(entry.amount, entry.currency) }}</td>
            <td class="num">{{ money(entry.balance_after, entry.currency) }}</td>
            <td class="hint">{{ entry.reason ?? "—" }}</td>
          </tr>
          <tr v-if="!entries.length"><td colspan="5" class="empty">还没有流水。用充值卡密给本单位充值。</td></tr>
        </tbody>
      </table>
    </div>
  </el-card>
</template>
<style scoped>
.billing-top { display: grid; grid-template-columns: minmax(220px, 1fr) minmax(280px, 2fr); gap: 16px; margin-bottom: 16px; align-items: stretch; }
.balance-card { justify-content: center; padding: 20px 24px; }
.balance { font-size: 32px; }
.redeem h3 { margin: 0; }
.redeem .el-input { max-width: 360px; }
.top { margin-top: 12px; }
.plus { color: var(--success); }
.flat { border: none; border-radius: 0; }
:deep(.flush) { padding: 0; }
h3 { margin: 0; }
@media (max-width: 720px) { .billing-top { grid-template-columns: minmax(0, 1fr); } }
</style>
