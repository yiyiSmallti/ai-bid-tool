<script setup>
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
  <div class="center">
    <h2>单位登录</h2>
    <p class="hint">平台管理员请使用<RouterLink to="/platform/login">平台后台登录</RouterLink>。</p>
    <p v-if="retrySeconds" class="notice" role="status">请求受限，请在 {{ retrySeconds }} 秒后重试。</p>
    <form v-if="!orgs" @submit.prevent="lookup">
      <label>邮箱<input v-model="email" type="email" autocomplete="username" name="email" /></label>
      <label>密码<input v-model="password" type="password" autocomplete="current-password" name="password" /></label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <p v-if="noOrg" class="notice" role="alert">
        这个账号还没有加入任何单位。平台管理员请从<RouterLink to="/platform/login">平台后台</RouterLink>登录。
      </p>
      <button class="primary" type="submit" :disabled="busy || retrySeconds > 0">登录</button>
    </form>
    <div v-else class="choices">
      <p class="hint">选择要进入的单位</p>
      <button v-for="org in orgs" :key="org.org_id" :disabled="!org.active || busy || retrySeconds > 0" @click="enter(org)">
        {{ org.name }}<span class="hint">{{ org.active ? "" : "（已停用）" }}</span>
      </button>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
    </div>
  </div>
</template>
