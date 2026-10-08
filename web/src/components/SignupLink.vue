<script setup>
import { onMounted, ref } from "vue";
import { orgSignupEnabled } from "../api.js";

const enabled = ref(false), error = ref("");
onMounted(async () => {
  try { enabled.value = await orgSignupEnabled(); }
  catch { error.value = "申请入口暂不可用，请稍后刷新。"; }
});
</script>

<template>
  <p v-if="enabled" class="hint section">需要开通组织？<RouterLink to="/apply">申请开通</RouterLink></p>
  <p v-else-if="error" class="hint section" role="status">{{ error }}</p>
</template>
