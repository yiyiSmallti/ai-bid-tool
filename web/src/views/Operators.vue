<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { request } from "../api.js";
import { formatTime } from "../ui.js";

const operators = ref([]), busy = ref(false), issuing = ref(""), error = ref(""), link = ref(null), copied = ref(false);
const controller = new AbortController();
let mounted = true;
const sources = { environment: "部署配置", database: "网页开通", none: "未绑定" };
const messages = {
  factor_managed_by_deployment: "此账号的身份验证器由部署配置管理，无法生成网页开通链接。",
  enrollment_unavailable: "网页开通暂不可用，请联系部署人员。",
  invalid_input: "当前账号无法生成此开通链接，请联系另一位平台管理员或部署人员。",
  not_found: "此邮箱不在平台管理员名单中，请刷新后再试。",
  invalid_session: "登录已失效，请重新登录。",
};
async function load() {
  busy.value = true; error.value = "";
  try {
    const response = await request("GET", "/platform/operators", undefined, { signal: controller.signal });
    if (mounted) operators.value = response.items;
  } catch (exc) { if (mounted) error.value = messages[exc.code] ?? "无法读取平台管理员，请稍后再试。"; }
  finally { if (mounted) busy.value = false; }
}
function clearLink() { link.value = null; copied.value = false; }
async function issue(operator) {
  if (issuing.value) return;
  clearLink(); error.value = ""; issuing.value = operator.email;
  try {
    const response = await request("POST", `/platform/operators/${encodeURIComponent(operator.email)}/enrollment-links`, { email: operator.email }, { signal: controller.signal });
    if (!mounted) return;
    const data = response.data;
    if (data.email !== operator.email || !/^\/app\/platform\/enroll#token=[A-Za-z0-9_.=-]+$/.test(data.url) || !Number.isFinite(Date.parse(data.expires_at))) throw new Error("invalid enrollment link");
    link.value = { email: data.email, url: window.location.origin + data.url, expiresAt: data.expires_at };
  } catch (exc) { if (mounted) error.value = messages[exc.code] ?? "无法生成开通链接，请稍后再试。"; }
  finally { if (mounted) issuing.value = ""; }
}
async function copy() {
  if (!link.value) return;
  try { await navigator.clipboard.writeText(link.value.url); if (mounted) copied.value = true; }
  catch { if (mounted) error.value = "无法复制，请手动选择并复制链接。"; }
}
onMounted(load);
onBeforeUnmount(() => { mounted = false; controller.abort(); clearLink(); });
</script>

<template>
  <div class="page-header">
    <div><h2>平台管理员</h2><p class="subtitle">管理员名单由部署配置维护。开通链接用于设置或确认密码并绑定身份验证器。</p></div>
    <el-button :loading="busy" @click="load">刷新</el-button>
  </div>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
  <el-alert v-if="link" type="success" show-icon :closable="false" role="note" class="section">
    把此链接交给 {{ link.email }}，有效至 {{ formatTime(link.expiresAt) }}。链接仅在此处显示一次，关闭或离开后不再保留。
    <p><code class="enrollment-link" data-testid="enrollment-link">{{ link.url }}</code></p>
    <div class="actions"><el-button size="small" @click="copy">{{ copied ? "已复制" : "复制链接" }}</el-button><el-button size="small" @click="clearLink">关闭链接</el-button></div>
  </el-alert>
  <div class="table-scroll" :aria-busy="busy">
    <table class="data-table">
      <thead><tr><th>邮箱</th><th>账号</th><th>身份验证器来源</th><th>开通时间</th><th>开通链接签发者</th><th class="num">操作</th></tr></thead>
      <tbody>
        <tr v-for="operator in operators" :key="operator.email">
          <td>{{ operator.email }}</td><td>{{ operator.has_account ? "已有账号" : "未创建" }}</td>
          <td>{{ sources[operator.factor_source] ?? "未知" }}</td>
          <td>{{ operator.enrolled_at ? formatTime(operator.enrolled_at) : "—" }}</td>
          <td>{{ operator.enrolled_by === "link" ? "部署人员" : operator.enrolled_by || "—" }}</td>
          <td class="num"><el-button size="small" :disabled="operator.factor_source === 'environment' || !!issuing" :loading="issuing === operator.email" @click="issue(operator)">生成开通链接</el-button></td>
        </tr>
        <tr v-if="!operators.length && !busy"><td colspan="6" class="empty">没有可显示的平台管理员。</td></tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.enrollment-link { overflow-wrap: anywhere; }
</style>
