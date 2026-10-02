<script setup>
import { onMounted, ref } from "vue";
import { request } from "../api.js";

const entries = ref([]);
const error = ref("");
const labels = {
  "platform.login": "登录",
  "platform.org.create": "开通单位",
  "platform.org.active": "启用/停用单位",
  "platform.model.set": "修改模型",
  "platform.model.test": "测试模型",
  "platform.card.create": "生成卡密",
  "platform.card.void": "作废卡密",
  "platform.org.balance": "调整余额",
  "billing.redeem": "卡密兑换",
};

onMounted(async () => {
  try {
    entries.value = (await request("GET", "/platform/audit?limit=200")).items;
  } catch (exc) {
    error.value = exc.message;
  }
});
</script>

<template>
  <h2>审计</h2>
  <p v-if="error" class="error" role="alert">{{ error }}</p>
  <table>
    <thead><tr><th>时间</th><th>管理员</th><th>操作</th><th>对象</th><th>结果</th></tr></thead>
    <tbody>
      <tr v-for="entry in entries" :key="entry.id">
        <td>{{ new Date(entry.created_at).toLocaleString("zh-CN") }}</td>
        <td>{{ entry.actor_email }}</td>
        <td>{{ labels[entry.action] ?? entry.action }}</td>
        <td class="hint">{{ entry.object_id ?? "—" }}</td>
        <td><span :class="['badge', entry.outcome === 'success' ? 'ok' : 'bad']">{{ entry.outcome }}</span></td>
      </tr>
    </tbody>
  </table>
</template>
