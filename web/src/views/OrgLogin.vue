<script setup>
import { Coin, OfficeBuilding } from "@element-plus/icons-vue";
import { computed, onBeforeUnmount, ref } from "vue";
import { useRouter } from "vue-router";
import { orgSession, request } from "../api.js";

const router = useRouter();
const email = ref("");
const password = ref("");
const orgs = ref(null);
const error = ref("");
const busy = ref(false);
const noOrg = ref(false);
const retryAt = ref(0), now = ref(Date.now());
const retrySeconds = computed(() => Math.max(0, Math.ceil((retryAt.value - now.value) / 1000)));
const clock = setInterval(() => { now.value = Date.now(); }, 1000);
onBeforeUnmount(() => clearInterval(clock));
function waitForRetry(exc) { if (exc.retryAfter) { now.value = Date.now(); retryAt.value = now.value + exc.retryAfter; } }

async function lookup() {
  if (retrySeconds.value) return;
  error.value = "";
  noOrg.value = false;
  if (!email.value || !password.value) {
    error.value = "填写邮箱和密码";
    return;
  }
  busy.value = true;
  try {
    const found = (await request("POST", "/auth/orgs", { email: email.value, password: password.value })).items;
    const usable = found.filter((org) => org.active);
    if (!found.length) noOrg.value = true;
    else if (usable.length === 1 && found.length === 1) await enter(usable[0]);
    else orgs.value = found;
  } catch (exc) {
    waitForRetry(exc);
    error.value = exc.status === 401 ? "邮箱或密码不正确" : exc.message;
  } finally {
    busy.value = false;
  }
}

async function enter(org) {
  if (retrySeconds.value) return;
  busy.value = true;
  error.value = "";
  try {
    const result = await request("POST", "/auth/login", {
      email: email.value,
      password: password.value,
      org_id: org.org_id,
    });
    orgSession.set({ session: result.data.session, orgId: org.org_id, orgName: org.name, email: email.value });
    password.value = "";
    await router.push("/org/tasks");
  } catch (exc) {
    waitForRetry(exc);
    error.value = exc.code === "org_inactive" ? "该单位已停用，请联系平台管理员" : exc.message;
  } finally { busy.value = false; }
}
</script>

<template>
  <el-card class="auth-card" shadow="always">
    <div class="auth-brand"><el-icon :size="20"><Coin /></el-icon>AI 标书工具</div>
    <h2>单位登录</h2>
    <p class="hint">平台管理员请使用<RouterLink to="/platform/login">平台后台登录</RouterLink>。</p>
    <el-alert v-if="retrySeconds" :title="`请求受限，请在 ${retrySeconds} 秒后重试。`" type="warning" :closable="false" role="status" />
    <el-form v-if="!orgs" label-position="top" @submit.prevent="lookup">
      <el-form-item label="邮箱"><el-input v-model="email" type="email" autocomplete="username" name="email" size="large" /></el-form-item>
      <el-form-item label="密码"><el-input v-model="password" type="password" autocomplete="current-password" name="password" size="large" show-password /></el-form-item>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
      <el-alert v-if="noOrg" type="info" :closable="false" role="alert" class="section">这个账号还没有加入任何单位。平台管理员请从<RouterLink to="/platform/login">平台后台</RouterLink>登录。</el-alert>
      <el-button type="primary" native-type="submit" size="large" :loading="busy" :disabled="retrySeconds > 0">登录</el-button>
    </el-form>
    <div v-else class="stack">
      <p class="hint">选择要进入的单位</p>
      <el-button v-for="org in orgs" :key="org.org_id" class="org-choice" size="large" :icon="OfficeBuilding" :disabled="!org.active || busy || retrySeconds > 0" @click="enter(org)">
        {{ org.name }}<span class="hint">{{ org.active ? "" : "（已停用）" }}</span>
      </el-button>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" />
    </div>
  </el-card>
</template>

<style scoped>
.org-choice { width: 100%; justify-content: flex-start; margin-left: 0 !important; }
</style>
