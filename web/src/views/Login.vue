<script setup>
import { Coin } from "@element-plus/icons-vue";
import { ref } from "vue";
import { useRouter } from "vue-router";
import { request, session } from "../api.js";

const router = useRouter();
const email = ref("");
const password = ref("");
const totp = ref("");
const error = ref("");
const busy = ref(false);

async function submit() {
  error.value = "";
  if (!email.value || !password.value || !/^\d{6}$/.test(totp.value)) {
    error.value = "填写邮箱、密码和 6 位验证码";
    return;
  }
  busy.value = true;
  try {
    const result = await request("POST", "/platform/auth/login", {
      email: email.value,
      password: password.value,
      totp: totp.value,
    });
    session.set(result.data.session);
    router.push("/platform/orgs");
  } catch (exc) {
    error.value = exc.status === 429 ? "失败次数过多，请 15 分钟后再试" : "邮箱、密码或验证码不正确";
    totp.value = "";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <el-card class="auth-card" shadow="always">
    <div class="auth-brand"><el-icon :size="20"><Coin /></el-icon>AI 标书工具</div>
    <h2>平台后台登录</h2>
    <p class="hint">平台运营人员使用，需要身份验证器中的 6 位验证码。</p>
    <el-form label-position="top" @submit.prevent="submit">
      <el-form-item label="邮箱"><el-input v-model="email" type="email" autocomplete="username" name="email" size="large" /></el-form-item>
      <el-form-item label="密码"><el-input v-model="password" type="password" autocomplete="current-password" name="password" size="large" show-password /></el-form-item>
      <el-form-item label="验证码"><el-input v-model="totp" inputmode="numeric" maxlength="6" autocomplete="one-time-code" name="totp" placeholder="身份验证器中的 6 位数字" size="large" /></el-form-item>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
      <el-button type="primary" native-type="submit" size="large" :loading="busy">登录</el-button>
    </el-form>
  </el-card>
</template>
