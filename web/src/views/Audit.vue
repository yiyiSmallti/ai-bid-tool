<script setup>
import { onMounted, ref } from "vue";
import { request } from "../api.js";
import { formatTime } from "../ui.js";

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
  <div class="page-header"><div><h2>审计</h2><p class="subtitle">最近 200 条平台操作记录。</p></div></div>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
  <div class="table-scroll">
    <table class="data-table">
      <thead><tr><th>时间</th><th>管理员</th><th>操作</th><th>对象</th><th>结果</th></tr></thead>
      <tbody>
        <tr v-for="entry in entries" :key="entry.id">
          <td>{{ formatTime(entry.created_at) }}</td>
          <td>{{ entry.actor_email }}</td>
          <td>{{ labels[entry.action] ?? entry.action }}</td>
          <td class="hint mono">{{ entry.object_id ?? "—" }}</td>
          <td><span class="tag" :class="entry.outcome === 'success' ? 'success' : 'danger'">{{ entry.outcome === "success" ? "成功" : entry.outcome }}</span></td>
        </tr>
        <tr v-if="!entries.length"><td colspan="5" class="empty">暂无审计记录</td></tr>
      </tbody>
    </table>
  </div>
</template>
