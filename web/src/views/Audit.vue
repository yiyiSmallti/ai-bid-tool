<script setup>
import { onMounted, ref } from "vue";
import { request } from "../api.js";
import { formatTime } from "../ui.js";

const entries = ref([]);
const error = ref("");
const outcomes = { success: "成功", failed: "失败", denied: "拒绝", interrupted: "测试中断，结果未知", in_progress: "测试进行中" };
const probeOutcomes = { passed: "认证通过", auth_failed: "认证失败", unsupported: "不支持认证探针", timeout: "认证超时", rate_limited: "测试次数超限", unavailable: "认证服务不可用", interrupted: "测试中断，结果未知" };
function outcomeText(entry) {
  if (entry.outcome !== "success") return outcomes[entry.outcome] ?? entry.outcome;
  if (entry.action === "credential.probe_start") return "测试已开始";
  if (entry.action === "credential.probe_finish") return probeOutcomes[entry.details?.outcome] ?? "测试已完成";
  return "成功";
}
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
  "credential.create": "创建凭据",
  "credential.replace": "替换凭据密钥",
  "credential.set_active": "启用/停用凭据",
  "credential.remove": "移除凭据",
  "credential.import": "导入凭据",
  "credential.read": "读取凭据元数据",
  "credential.probe_start": "开始认证测试",
  "credential.probe_finish": "完成认证测试",
  "credential.rewrap": "凭据根密钥轮换",
  "platform.credential_denied": "拒绝凭据访问",
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
          <td><span class="tag" :class="entry.outcome === 'success' ? 'success' : 'danger'">{{ outcomeText(entry) }}</span></td>
        </tr>
        <tr v-if="!entries.length"><td colspan="5" class="empty">暂无审计记录</td></tr>
      </tbody>
    </table>
  </div>
</template>
