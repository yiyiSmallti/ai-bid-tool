<script setup>
import { Coin } from "@element-plus/icons-vue";
import { ref } from "vue";
import { request } from "../api.js";

// The token travels in the URL fragment, which browsers never send to the server.
const token = new URLSearchParams(window.location.hash.slice(1)).get("token") ?? "";
history.replaceState(null, "", window.location.pathname);
const password = ref("");
const confirm = ref("");
const error = ref(token ? "" : "链接不完整，请向平台管理员索取新的设置链接");
const done = ref(false);

async function submit() {
  error.value = "";
  if (password.value.length < 12) {
    error.value = "密码至少 12 个字符";
    return;
  }
  if (password.value !== confirm.value) {
    error.value = "两次输入的密码不一致";
    return;
  }
  try {
    await request("POST", "/auth/setup-password", { token, password: password.value });
    done.value = true;
  } catch (exc) {
    error.value =
      exc.code === "invalid_setup_link" ? "链接已失效或已使用，请向平台管理员索取新的设置链接" : exc.message;
  }
}
</script>

<template>
  <el-card class="auth-card" shadow="always">
    <div class="auth-brand"><el-icon :size="20"><Coin /></el-icon>AI 标书工具</div>
    <h2>设置密码</h2>
    <el-alert v-if="done" type="success" :closable="false" show-icon role="status">
      密码已设置。单位用户请<RouterLink to="/org/login">登录单位</RouterLink>；平台管理员请进入<RouterLink to="/platform/login">平台后台</RouterLink>。
    </el-alert>
    <el-form v-else label-position="top" @submit.prevent="submit">
      <p class="hint">密码至少 12 个字符。</p>
      <el-form-item label="新密码"><el-input v-model="password" type="password" autocomplete="new-password" name="new-password" size="large" show-password /></el-form-item>
      <el-form-item label="再次输入"><el-input v-model="confirm" type="password" autocomplete="new-password" name="confirm-password" size="large" show-password /></el-form-item>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
      <el-button type="primary" native-type="submit" size="large" :disabled="!token">设置密码</el-button>
    </el-form>
  </el-card>
</template>
