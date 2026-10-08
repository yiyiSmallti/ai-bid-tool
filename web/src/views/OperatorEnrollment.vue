<script setup>
import { Coin } from "@element-plus/icons-vue";
import qrcode from "qrcode-generator";
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import { request } from "../api.js";

// Fragments stay off HTTP requests; remove the bearer link before any API read.
let token = new URLSearchParams(window.location.hash.slice(1)).get("token") ?? "";
history.replaceState(history.state, "", window.location.pathname);
const controller = new AbortController();
let mounted = true;
const checking = ref(true), busy = ref(false), ready = ref(false), done = ref(false), error = ref("");
const email = ref(""), passwordStep = ref("set"), expiresAt = ref("");
const secret = ref(""), uri = ref(""), pending = ref("");
const password = ref(""), confirm = ref(""), code = ref(""), canvas = ref(null);
const messages = {
  invalid_enrollment_link: "链接已失效或已使用，请索取新的开通链接。",
  invalid_enrollment: "密码或验证码不正确，请重新输入。",
  factor_managed_by_deployment: "此账号的身份验证器由部署配置管理，请联系部署人员。",
  enrollment_unavailable: "网页开通暂不可用，请联系部署人员。",
  weak_password: "密码须为 12 至 1,024 个字符。",
  invalid_input: "填写内容不符合要求，请检查后重新输入。",
  too_many_attempts: "失败次数过多，请 15 分钟后再试。",
  auth_busy: "服务繁忙，请稍后再试。",
};
const terminal = new Set(["invalid_enrollment_link", "factor_managed_by_deployment", "enrollment_unavailable"]);
function clearPasswords() { password.value = ""; confirm.value = ""; code.value = ""; }
function clearSecrets() {
  clearPasswords(); token = ""; pending.value = ""; secret.value = ""; uri.value = "";
  if (canvas.value) {
    canvas.value.getContext("2d")?.clearRect(0, 0, canvas.value.width, canvas.value.height);
    canvas.value.width = 0; canvas.value.height = 0;
  }
  ready.value = false;
}
function drawQr() {
  const qr = qrcode(0, "M"); qr.addData(uri.value); qr.make();
  const cells = qr.getModuleCount(), margin = 4, size = Math.max(3, Math.floor(240 / (cells + margin * 2)));
  const edge = (cells + margin * 2) * size, context = canvas.value.getContext("2d");
  canvas.value.width = edge; canvas.value.height = edge;
  context.fillStyle = "#fff"; context.fillRect(0, 0, edge, edge); context.fillStyle = "#000";
  for (let row = 0; row < cells; row++) for (let column = 0; column < cells; column++) {
    if (qr.isDark(row, column)) context.fillRect((column + margin) * size, (row + margin) * size, size, size);
  }
}
onMounted(async () => {
  if (!token) { error.value = messages.invalid_enrollment_link; checking.value = false; return; }
  try {
    const response = await request("POST", "/platform/enrollment/start", { token }, { signal: controller.signal });
    if (!mounted) return;
    const data = response.data;
    if (typeof data.email !== "string" || !["set", "confirm"].includes(data.password) || !/^[A-Z2-7]{32}$/.test(data.totp_secret) || !/^otpauth:\/\/totp\//.test(data.otpauth_uri) || typeof data.pending !== "string" || !data.pending || !Number.isFinite(Date.parse(data.expires_at))) throw new Error("invalid enrollment response");
    email.value = data.email; passwordStep.value = data.password; expiresAt.value = data.expires_at;
    secret.value = data.totp_secret; uri.value = data.otpauth_uri; pending.value = data.pending; ready.value = true; checking.value = false;
    await nextTick();
    if (mounted) drawQr();
  } catch (exc) {
    if (mounted) { clearSecrets(); error.value = messages[exc.code] ?? "无法读取开通信息，请索取新的链接后再试。"; }
  } finally { if (mounted) checking.value = false; }
});
onBeforeUnmount(() => { mounted = false; controller.abort(); clearSecrets(); });

async function submit() {
  if (busy.value || !ready.value) return;
  error.value = "";
  if (passwordStep.value === "set" && (password.value.length < 12 || password.value.length > 1024)) { error.value = messages.weak_password; return; }
  if (!password.value || password.value.length > 1024) { error.value = "请输入现有密码。"; return; }
  if (passwordStep.value === "set" && password.value !== confirm.value) { error.value = "两次输入的密码不一致。"; return; }
  if (!/^\d{6}$/.test(code.value)) { error.value = "填写身份验证器中的 6 位验证码。"; return; }
  const body = { token, pending: pending.value, password: password.value, code: code.value };
  clearPasswords(); busy.value = true;
  try {
    // request serializes the wire body before yielding to fetch; release it immediately.
    const submission = request("POST", "/platform/enrollment/complete", body, { signal: controller.signal });
    body.token = ""; body.pending = ""; body.password = ""; body.code = "";
    const response = await submission;
    if (!mounted) return;
    if (response.data.enrolled !== true || response.data.email !== email.value || typeof response.data.account_created !== "boolean" || typeof response.data.password_set !== "boolean") throw new Error("invalid enrollment receipt");
    clearSecrets(); done.value = true;
  } catch (exc) {
    if (mounted) {
      if (terminal.has(exc.code)) clearSecrets();
      error.value = messages[exc.code] ?? "开通结果无法确认，请稍后再试。";
    }
  } finally {
    body.token = ""; body.pending = ""; body.password = ""; body.code = "";
    if (mounted) busy.value = false;
  }
}
</script>

<template>
  <el-card class="auth-card enrollment-card" shadow="always">
    <div class="auth-brand"><el-icon :size="20"><Coin /></el-icon>AI 标书工具</div>
    <h2>平台管理员开通</h2>
    <el-alert v-if="done" type="success" :closable="false" show-icon role="status">
      {{ email }} 已完成开通。请等待身份验证器显示下一组验证码后登录。
      <p><RouterLink to="/platform/login">登录平台后台</RouterLink></p>
    </el-alert>
    <p v-else-if="checking" class="hint" role="status">正在核验开通链接…</p>
    <template v-else>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
      <el-form v-if="ready" label-position="top" @submit.prevent="submit">
        <p><strong>{{ email }}</strong></p>
        <p class="hint">链接有效至 {{ new Date(expiresAt).toLocaleString("zh-CN", { hour12: false }) }}。使用身份验证器扫描二维码，或手动输入下方密钥。</p>
        <div class="factor-setup">
          <canvas ref="canvas" role="img" aria-label="身份验证器绑定二维码" />
          <p class="hint">手动输入密钥</p><code data-testid="totp-secret">{{ secret }}</code>
        </div>
        <p v-if="passwordStep === 'confirm'" class="hint" role="note">此邮箱已有账号，请确认现有密码。开通不会更改密码。</p>
        <p v-else class="hint">设置 12 至 1,024 个字符的密码。</p>
        <el-form-item :label="passwordStep === 'set' ? '新密码' : '现有密码'" required><el-input v-model="password" name="password" type="password" maxlength="1024" :autocomplete="passwordStep === 'set' ? 'new-password' : 'current-password'" :disabled="busy" /></el-form-item>
        <el-form-item v-if="passwordStep === 'set'" label="确认密码" required><el-input v-model="confirm" name="confirm-password" type="password" maxlength="1024" autocomplete="new-password" :disabled="busy" /></el-form-item>
        <el-form-item label="验证码" required><el-input v-model="code" name="code" inputmode="numeric" maxlength="6" autocomplete="one-time-code" placeholder="身份验证器中的 6 位数字" :disabled="busy" /></el-form-item>
        <el-button type="primary" native-type="submit" :loading="busy">完成开通</el-button>
      </el-form>
    </template>
  </el-card>
</template>

<style scoped>
.enrollment-card { width: min(520px, 100%); }
.factor-setup { margin: 16px 0; text-align: center; }
.factor-setup canvas { display: block; max-width: 100%; margin: 0 auto; }
.factor-setup code { display: block; overflow-wrap: anywhere; letter-spacing: 1px; }
</style>
