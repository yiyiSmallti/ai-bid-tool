<script setup>
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { session } from "./api.js";

const route = useRoute();
const router = useRouter();
const framed = computed(() => !route.meta.public);

function signOut() {
  session.clear();
  router.push("/platform/login");
}
</script>

<template>
  <div v-if="framed" class="shell">
    <nav class="nav">
      <h1>平台后台</h1>
      <RouterLink to="/platform/orgs">单位</RouterLink>
      <RouterLink to="/platform/models">模型</RouterLink>
      <RouterLink to="/platform/usage">用量与账单</RouterLink>
      <RouterLink to="/platform/audit">审计</RouterLink>
      <div class="who"><button @click="signOut">退出登录</button></div>
    </nav>
    <main><RouterView /></main>
  </div>
  <RouterView v-else />
</template>
