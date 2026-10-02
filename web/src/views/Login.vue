<script setup>
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
  <div class="center">
    <h2>平台后台登录</h2>
    <form @submit.prevent="submit">
      <label>邮箱<input v-model="email" type="email" autocomplete="username" name="email" /></label>
      <label>密码<input v-model="password" type="password" autocomplete="current-password" name="password" /></label>
      <label>验证码<input v-model="totp" inputmode="numeric" maxlength="6" autocomplete="one-time-code" name="totp" placeholder="身份验证器中的 6 位数字" /></label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button class="primary" type="submit" :disabled="busy">登录</button>
    </form>
  </div>
</template>
