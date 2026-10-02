<script setup>
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { orgSession, session } from "./api.js";

const route = useRoute();
const router = useRouter();
const area = computed(() => route.meta.area);
const org = computed(() => (area.value === "org" ? orgSession.get() : null));

function signOut() {
  if (area.value === "org") {
    orgSession.clear();
    router.push("/org/login");
  } else {
    session.clear();
    router.push("/platform/login");
  }
}
</script>

<template>
  <div v-if="area === 'platform'" class="shell">
    <nav class="nav">
      <h1>平台后台</h1>
      <RouterLink to="/platform/orgs">单位</RouterLink>
      <RouterLink to="/platform/models">模型</RouterLink>
      <RouterLink to="/platform/cards">卡密</RouterLink>
      <RouterLink to="/platform/usage">用量与账单</RouterLink>
      <RouterLink to="/platform/audit">审计</RouterLink>
      <div class="who"><button @click="signOut">退出登录</button></div>
    </nav>
    <main><RouterView /></main>
  </div>
  <div v-else-if="area === 'org'">
    <header class="topbar">
      <strong>{{ org?.orgName }}</strong>
      <span class="hint">{{ org?.email }}</span>
      <button @click="signOut">退出登录</button>
    </header>
    <main class="narrow"><RouterView /></main>
  </div>
  <RouterView v-else />
</template>
