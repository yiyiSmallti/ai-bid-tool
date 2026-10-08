<script setup>
import { Coin } from "@element-plus/icons-vue";
import { onBeforeUnmount, onMounted, ref } from "vue";
import { orgSignupEnabled, request } from "../api.js";

const enabled = ref(false), checking = ref(true), busy = ref(false), done = ref(false), error = ref("");
const form = ref({ org_name: "", contact_name: "", email: "", phone: "", note: "" });
const password = ref(""), confirm = ref("");
const failures = {
  weak_password: "密码至少 12 个字符。",
  invalid_input: "填写内容不符合要求，请检查后重新输入密码。",
  signup_disabled: "组织申请开通暂未开放。",
  too_many_attempts: "提交次数过多，请在 24 小时后再试。",
  signup_busy: "申请队列已满，请稍后再试。",
  auth_busy: "服务繁忙，请稍后再试。",
};
onMounted(async () => {
  try { enabled.value = await orgSignupEnabled(); }
  catch { error.value = "无法读取申请入口配置，请刷新后再试。"; }
  finally { checking.value = false; }
});
function clearPasswords() { password.value = ""; confirm.value = ""; }
onBeforeUnmount(clearPasswords);

async function submit() {
  if (busy.value || !enabled.value) return;
  error.value = "";
  if (!form.value.org_name.trim() || !form.value.contact_name.trim() || !/^[^@\s]+@[^@\s]+$/.test(form.value.email.trim())) {
    error.value = "填写组织名称、联系人和有效邮箱。"; return;
  }
  if (password.value.length < 12 || password.value.length > 1024) { error.value = "密码须为 12 至 1,024 个字符。"; return; }
  if (password.value !== confirm.value) { error.value = "两次输入的密码不一致。"; return; }
  const body = {
    org_name: form.value.org_name.trim(), contact_name: form.value.contact_name.trim(), email: form.value.email.trim(),
    phone: form.value.phone.trim() || null, note: form.value.note.trim() || null, password: password.value,
  };
  clearPasswords(); busy.value = true;
  try {
    const response = await request("POST", "/auth/org-applications", body);
    if (response.data.submitted !== true || response.data.review !== "platform_manual" || response.data.sign_in_after_approval !== true) throw new Error("invalid receipt");
    done.value = true;
  } catch (exc) {
    error.value = failures[exc.code] ?? "提交结果无法确认，请稍后重新提交。";
    if (exc.code === "signup_disabled") enabled.value = false;
  } finally { delete body.password; busy.value = false; }
}
</script>

<template>
  <el-card class="auth-card application-card" shadow="always">
    <div class="auth-brand"><el-icon :size="20"><Coin /></el-icon>AI 标书工具</div>
    <h2>组织申请开通</h2>
    <el-alert v-if="done" type="success" :closable="false" show-icon role="status">
      申请已提交。审核通过后使用该邮箱和密码登录。平台运营人员会根据填写的联系方式联系您。
      <p><RouterLink to="/org/login">登录单位</RouterLink></p>
    </el-alert>
    <p v-else-if="checking" class="hint" role="status">正在读取申请入口配置…</p>
    <el-alert v-else-if="!enabled" :title="error || '组织申请开通暂未开放。'" :type="error ? 'error' : 'info'" :closable="false" role="status" />
    <el-form v-else label-position="top" @submit.prevent="submit">
      <p class="hint">申请由平台运营人员审核，密码须为 12 至 1,024 个字符。已有账号的邮箱在审核通过后仍使用原密码。</p>
      <el-form-item label="组织名称" required><el-input v-model="form.org_name" name="org-name" maxlength="200" :disabled="busy" /></el-form-item>
      <el-form-item label="联系人" required><el-input v-model="form.contact_name" name="contact-name" maxlength="100" autocomplete="name" :disabled="busy" /></el-form-item>
      <el-form-item label="邮箱" required><el-input v-model="form.email" name="email" type="email" maxlength="254" autocomplete="username" :disabled="busy" /></el-form-item>
      <el-form-item label="手机号（选填）"><el-input v-model="form.phone" name="phone" type="tel" maxlength="40" autocomplete="tel" :disabled="busy" /></el-form-item>
      <el-form-item label="用途说明（选填）"><el-input v-model="form.note" name="note" type="textarea" maxlength="500" :autosize="{ minRows: 2, maxRows: 5 }" :disabled="busy" /></el-form-item>
      <el-form-item label="密码" required><el-input v-model="password" name="password" type="password" maxlength="1024" autocomplete="new-password" :disabled="busy" /></el-form-item>
      <el-form-item label="确认密码" required><el-input v-model="confirm" name="confirm-password" type="password" maxlength="1024" autocomplete="new-password" :disabled="busy" /></el-form-item>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" class="section" />
      <el-button type="primary" native-type="submit" :loading="busy">提交申请</el-button>
    </el-form>
    <p v-if="!done" class="hint section"><RouterLink to="/org/login">返回单位登录</RouterLink></p>
  </el-card>
</template>

<style scoped>
.application-card { width: min(520px, 100%); }
</style>
