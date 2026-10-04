<script setup>
import { onMounted, ref } from "vue";
import { Download } from "@element-plus/icons-vue";
import { money, request } from "../api.js";
import { confirmAction } from "../ui.js";

const cards = ref([]);
const currency = ref("");
const status = ref("");
const form = ref({ count: 10, face_value: 100, expires_at: "", note: "" });
const issued = ref(null);
const error = ref("");
const labels = { active: "未使用", redeemed: "已兑换", void: "已作废" };

async function load() {
  try {
    const query = status.value ? `?status=${status.value}` : "";
    const result = await request("GET", `/platform/cards${query}`);
    cards.value = result.items;
    currency.value = result.data.currency;
  } catch (exc) {
    error.value = exc.message;
  }
}

async function issue() {
  error.value = "";
  const count = Number(form.value.count);
  const face = Number(form.value.face_value);
  if (!(count >= 1 && count <= 500) || !(face > 0)) {
    error.value = "数量 1 到 500，面值必须大于 0";
    return;
  }
  const body = { count, face_value: face, note: form.value.note || null };
  if (form.value.expires_at) body.expires_at = new Date(form.value.expires_at).toISOString();
  try {
    issued.value = (await request("POST", "/platform/cards", body)).data;
    await load();
  } catch (exc) {
    error.value = exc.message;
  }
}

function download() {
  const batch = issued.value;
  const rows = ["code,last4,face_value,currency,batch_id,expires_at"].concat(
    batch.cards.map((c) => [c.code, c.last4, batch.face_value, batch.currency, batch.batch_id, batch.expires_at ?? ""].join(",")),
  );
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([rows.join("\n") + "\n"], { type: "text/csv" }));
  link.download = `cards-${batch.batch_id}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function voidCard(card) {
  if (!(await confirmAction(`作废卡密 …${card.last4}？作废后无法兑换，也不能恢复。`, "作废卡密", "确定", true))) return;
  try {
    await request("POST", `/platform/cards/${card.id}/void`);
    await load();
  } catch (exc) {
    error.value = exc.message;
  }
}

onMounted(load);
</script>

<template>
  <div class="page-header">
    <div><h2>卡密</h2><p class="subtitle">生成充值卡密交给单位管理员兑换。卡密只在生成时显示一次。</p></div>
    <el-select v-model="status" name="status-filter" aria-label="按状态筛选" class="status-filter" @change="load">
      <el-option value="" label="全部" /><el-option value="active" label="未使用" /><el-option value="redeemed" label="已兑换" /><el-option value="void" label="已作废" />
    </el-select>
  </div>
  <el-card shadow="never" class="section">
    <template #header><h3 class="card-title">生成卡密</h3></template>
    <form class="panel el-form el-form--label-top" @submit.prevent="issue">
      <div class="grid four">
        <el-form-item label="数量（1–500）"><el-input v-model="form.count" type="number" min="1" max="500" name="card-count" /></el-form-item>
        <el-form-item :label="`面值（${currency}）`"><el-input v-model="form.face_value" type="number" min="0.01" step="0.01" name="card-face" /></el-form-item>
        <el-form-item label="有效期至（可选）"><el-input v-model="form.expires_at" type="datetime-local" name="card-expires" /></el-form-item>
        <el-form-item label="备注（可选）"><el-input v-model="form.note" maxlength="200" name="card-note" /></el-form-item>
      </div>
      <el-button type="primary" native-type="submit">生成卡密</el-button>
    </form>
  </el-card>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
  <el-card v-if="issued" shadow="never" class="section issued" data-testid="issued-cards">
    <template #header><div class="section-title"><h3 class="card-title">已生成 {{ issued.count }} 张，每张 {{ money(issued.face_value, issued.currency) }}</h3><el-button :icon="Download" @click="download">下载 CSV</el-button></div></template>
    <p class="hint">卡密只显示这一次，离开页面后无法找回。</p>
    <div class="codes"><code v-for="card in issued.cards" :key="card.id" class="card-code" data-testid="card-code">{{ card.code }}</code></div>
  </el-card>
  <div class="table-scroll">
    <table class="data-table">
      <thead><tr><th>卡密</th><th class="num">面值</th><th>状态</th><th>有效期</th><th>备注</th><th>生成</th><th class="num">操作</th></tr></thead>
      <tbody>
        <tr v-for="card in cards" :key="card.id">
          <td class="mono">…{{ card.last4 }}</td>
          <td class="num">{{ money(card.face_value, card.currency) }}</td>
          <td><span class="tag" :class="card.status === 'active' && !card.expired ? 'success' : card.status === 'void' ? 'danger' : ''">{{ card.status === "active" && card.expired ? "已过期" : labels[card.status] }}</span></td>
          <td>{{ card.expires_at ? new Date(card.expires_at).toLocaleDateString("zh-CN") : "长期" }}</td>
          <td class="hint">{{ card.note ?? "—" }}</td>
          <td class="hint">{{ card.created_by }}</td>
          <td class="num"><el-button v-if="card.status === 'active'" size="small" type="danger" plain @click="voidCard(card)">作废</el-button></td>
        </tr>
        <tr v-if="!cards.length"><td colspan="7" class="empty">还没有卡密。填写数量和面值后生成。</td></tr>
      </tbody>
    </table>
  </div>
</template>
<style scoped>
.card-title { margin: 0; }
.status-filter { width: 140px; }
.grid.four { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.codes { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; }
.card-code { font-size: 15px; letter-spacing: 1px; background: var(--surface-muted); border-radius: 6px; padding: 8px 10px; }
@media (max-width: 900px) { .grid.four { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
