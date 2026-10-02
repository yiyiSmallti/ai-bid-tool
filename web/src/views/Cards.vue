<script setup>
import { onMounted, ref } from "vue";
import { money, request } from "../api.js";

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
  if (!window.confirm(`作废卡密 …${card.last4}？作废后无法兑换，也不能恢复。`)) return;
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
  <div class="toolbar">
    <h2>卡密</h2>
    <div>
      <select v-model="status" name="status-filter" @change="load">
        <option value="">全部</option><option value="active">未使用</option>
        <option value="redeemed">已兑换</option><option value="void">已作废</option>
      </select>
    </div>
  </div>
  <form class="panel" @submit.prevent="issue">
    <div class="grid">
      <label>数量（1–500）<input v-model="form.count" type="number" min="1" max="500" name="card-count" /></label>
      <label>面值（{{ currency }}）<input v-model="form.face_value" type="number" min="0.01" step="0.01" name="card-face" /></label>
      <label>有效期至（可选）<input v-model="form.expires_at" type="datetime-local" name="card-expires" /></label>
      <label>备注（可选）<input v-model="form.note" maxlength="200" name="card-note" /></label>
    </div>
    <p><button class="primary" type="submit">生成卡密</button></p>
  </form>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <div v-if="issued" class="notice" data-testid="issued-cards">
    已生成 {{ issued.count }} 张，每张 {{ money(issued.face_value, issued.currency) }}。卡密只显示这一次，离开页面后无法找回：
    <p><button type="button" @click="download">下载 CSV</button></p>
    <div v-for="card in issued.cards" :key="card.id"><code class="card-code" data-testid="card-code">{{ card.code }}</code></div>
  </div>
  <table>
    <thead><tr><th>卡密</th><th class="num">面值</th><th>状态</th><th>有效期</th><th>备注</th><th>生成</th><th></th></tr></thead>
    <tbody>
      <tr v-for="card in cards" :key="card.id">
        <td>…{{ card.last4 }}</td>
        <td class="num">{{ money(card.face_value, card.currency) }}</td>
        <td>
          <span :class="['badge', card.status === 'active' && !card.expired ? 'ok' : card.status === 'void' ? 'bad' : '']">
            {{ card.status === "active" && card.expired ? "已过期" : labels[card.status] }}
          </span>
        </td>
        <td>{{ card.expires_at ? new Date(card.expires_at).toLocaleDateString("zh-CN") : "长期" }}</td>
        <td class="hint">{{ card.note ?? "—" }}</td>
        <td class="hint">{{ card.created_by }}</td>
        <td class="num"><button v-if="card.status === 'active'" @click="voidCard(card)">作废</button></td>
      </tr>
      <tr v-if="!cards.length"><td colspan="7" class="hint">还没有卡密。填写数量和面值后生成。</td></tr>
    </tbody>
  </table>
</template>
