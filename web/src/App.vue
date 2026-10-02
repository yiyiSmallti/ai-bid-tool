<script setup>
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { orgSession, session } from "./api.js";
import { orgAccess } from "./org.js";
const route = useRoute(), router = useRouter();
const area = computed(() => route.meta.area);
const org = computed(() => (area.value === "org" ? orgSession.get() : null));
async function signOut() {
  if (area.value === "org") {
    // Let the editor's leave guard run before discarding the session.
    const failure = await router.push("/org/login");
    if (!failure) orgSession.clear();
  } else { session.clear(); router.push("/platform/login"); }
}
</script>
<template>
  <a class="skip-link" href="#main-content">跳到主内容</a>
  <div v-if="area === 'platform'" class="shell">
    <nav class="nav"><h1>平台后台</h1><RouterLink to="/platform/orgs">单位</RouterLink><RouterLink to="/platform/models">模型</RouterLink><RouterLink to="/platform/cards">卡密</RouterLink><RouterLink to="/platform/usage">用量与账单</RouterLink><RouterLink to="/platform/audit">审计</RouterLink><div class="who"><button @click="signOut">退出登录</button></div></nav>
    <main id="main-content" tabindex="-1"><RouterView /></main>
  </div>
  <div v-else-if="area === 'org'">
    <header class="topbar"><strong>{{ org?.orgName }}</strong><span class="hint">{{ org?.email }} · {{ orgAccess.role }}</span><nav aria-label="单位导航"><RouterLink to="/org/tasks">招标任务</RouterLink> <RouterLink v-if="orgAccess.role === 'admin'" to="/org/billing">余额与充值</RouterLink></nav><button @click="signOut">退出 / 切换单位</button></header>
    <main id="main-content" class="org-workspace" tabindex="-1"><p v-if="orgAccess.error" class="error" role="alert">{{ orgAccess.error }}</p><RouterView v-else-if="orgAccess.role" :key="`${route.path}:${route.query.job ?? ''}:${org?.orgId}`" /></main>
  </div>
  <main v-else id="main-content" class="public-main"><RouterView /></main>
</template>
