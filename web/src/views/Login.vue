<script setup>
import { Coin, Key } from "@element-plus/icons-vue";
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { exchangeAccessKey } from "../api.js";
import { errorText } from "../org.js";

const router = useRouter(), route = useRoute();
const accessKey = ref("");
const error = ref(route.query.rejected ? "访问密钥无效，请重新输入" : route.query.unavailable ? "暂时无法核验会话，请稍后重试" : "");
const busy = ref(false);

async function submit() {
  // The field is consumed once, so the key does not stay in component state after submit.
  const key = accessKey.value;
  accessKey.value = "";
  error.value = "";
  if (!key) {
    error.value = "请输入访问密钥";
    return;
  }
  busy.value = true;
  try {
    await exchangeAccessKey(key);
    await router.push("/org/tasks");
  } catch (exc) {
    error.value = exc.status === 401 ? "访问密钥无效，请重新输入" : errorText(exc);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <el-card class="auth-card" shadow="always">
    <div class="auth-brand"><el-icon :size="20"><Coin /></el-icon>AI 标书工具</div>
    <h2>输入访问密钥</h2>
    <p class="hint">访问密钥在安装时配置，桌面应用打开本页时会自动完成登录。</p>
    <ul class="auth-facts">
      <li>本机单人使用：密钥只用于换取本机会话，不会上传到其他服务。</li>
      <li>密钥保存在安装配置中；桌面应用通过 URL 片段传入，输入后立即从地址栏移除。</li>
    </ul>
    <el-form label-position="top" @submit.prevent="submit">
      <el-form-item label="访问密钥"><el-input v-model="accessKey" type="password" autocomplete="off" name="access_key" size="large" show-password :prefix-icon="Key" /></el-form-item>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
      <el-button type="primary" native-type="submit" size="large" :loading="busy">进入</el-button>
    </el-form>
  </el-card>
</template>
