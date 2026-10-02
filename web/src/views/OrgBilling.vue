<script setup>
import { onMounted, ref } from "vue";
import { money, request } from "../api.js";

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
  <h2>余额与充值</h2>
  <div v-if="overview" class="cards" style="grid-template-columns: 1fr">
    <div class="card">
      <div class="label">当前余额</div>
      <p class="balance" data-testid="balance">{{ money(overview.balance, overview.currency) }}</p>
      <p v-if="overview.balance <= 0" class="error">余额不足，平台模型的要求抽取已暂停。充值后即可继续。</p>
    </div>
  </div>
  <p v-if="error && !overview" class="error" role="alert">{{ error }}</p>
  <form v-if="overview" class="panel" @submit.prevent="redeem">
    <label>充值卡密<input v-model="code" name="card-code" autocomplete="off" placeholder="ABCD-EFGH-JKMN-PQRS" /></label>
    <p><button class="primary" type="submit" :disabled="busy">充值</button></p>
    <p v-if="message" class="notice" role="status">{{ message }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </form>
  <h2>流水</h2>
  <table>
    <thead><tr><th>时间</th><th>类型</th><th class="num">金额</th><th class="num">变动后余额</th><th>说明</th></tr></thead>
    <tbody>
      <tr v-for="entry in entries" :key="entry.id">
        <td>{{ new Date(entry.created_at).toLocaleString("zh-CN") }}</td>
        <td>{{ kinds[entry.kind] ?? entry.kind }}</td>
        <td class="num">{{ entry.amount > 0 ? "+" : "" }}{{ money(entry.amount, entry.currency) }}</td>
        <td class="num">{{ money(entry.balance_after, entry.currency) }}</td>
        <td class="hint">{{ entry.reason ?? "—" }}</td>
      </tr>
      <tr v-if="!entries.length"><td colspan="5" class="hint">还没有流水。用充值卡密给本单位充值。</td></tr>
    </tbody>
  </table>
</template>
