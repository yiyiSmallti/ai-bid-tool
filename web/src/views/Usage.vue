<script setup>
import { onMounted, ref } from "vue";
import { count, money, request } from "../api.js";

const thisMonth = new Date().toISOString().slice(0, 7);
const from = ref(thisMonth);
const to = ref(thisMonth);
const rows = ref([]);
const totals = ref(null);
const error = ref("");

async function load() {
  error.value = "";
  try {
    const result = await request("GET", `/platform/usage?from=${from.value}&to=${to.value}`);
    rows.value = result.items;
    totals.value = result.data.totals;
  } catch (exc) {
    error.value = exc.message;
  }
}

const columns = [
  "month", "org_name", "org_id", "billing", "provider", "model", "calls", "tokens",
  "input_tokens", "output_tokens", "ocr_pages", "vendor_usd", "unpriced_calls", "charge_usd",
];

function exportCsv() {
  const escape = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
  const lines = [columns.join(","), ...rows.value.map((row) => columns.map((c) => escape(row[c])).join(","))];
  const blob = new Blob(["﻿" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `usage-${from.value}-${to.value}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

onMounted(load);
</script>

<template>
  <div class="toolbar">
    <h2>用量与账单</h2>
    <div>
      <label class="inline">从<input v-model="from" type="month" name="from" /></label>
      <label class="inline">到<input v-model="to" type="month" name="to" /></label>
      <button @click="load">查询</button>
      <button :disabled="!rows.length" @click="exportCsv">导出 CSV</button>
    </div>
  </div>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <div v-if="totals" class="cards">
    <div class="card"><div class="label">调用次数</div><div class="value">{{ count(totals.calls) }}</div></div>
    <div class="card"><div class="label">服务商成本</div><div class="value">{{ money(totals.vendor_usd) }}</div></div>
    <div class="card"><div class="label">应收</div><div class="value">{{ money(totals.charge_usd) }}</div></div>
  </div>
  <table>
    <thead>
      <tr><th>月份</th><th>单位</th><th>计费</th><th>服务商 / 模型</th><th class="num">调用</th><th class="num">token</th><th class="num">成本</th><th class="num">应收</th></tr>
    </thead>
    <tbody>
      <tr v-for="row in rows" :key="row.month + row.org_id + row.billing + row.model">
        <td>{{ row.month.slice(0, 7) }}</td>
        <td>{{ row.org_name }}</td>
        <td><span :class="['badge', row.billing === 'platform' ? 'ok' : '']">{{ row.billing === "platform" ? "平台计费" : "不计费" }}</span></td>
        <td>{{ row.provider }}<div class="hint">{{ row.model }}</div></td>
        <td class="num">{{ count(row.calls) }}</td>
        <td class="num">{{ count(row.tokens) }}</td>
        <td class="num">{{ money(row.vendor_usd) }}<div v-if="row.unpriced_calls" class="hint">{{ row.unpriced_calls }} 次未定价</div></td>
        <td class="num">{{ money(row.charge_usd) }}</td>
      </tr>
      <tr v-if="!rows.length"><td colspan="8" class="hint">所选月份没有用量。</td></tr>
    </tbody>
  </table>
  <p class="hint">「不计费」是未使用平台目录模型的调用（BID_LLM_* 配置），只统计成本。结算方式未定，这里只展示金额。</p>
</template>
