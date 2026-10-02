<script setup>
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { errorText, orgAccess, orgRequest } from "../org.js";
const router = useRouter();
const tasks = ref(null), error = ref(""), busy = ref(false);
const name = ref(""), number = ref(""), deadline = ref(""), budget = ref("");
const createAllowed = computed(() => ["admin", "bidder"].includes(orgAccess.role));
async function load() { try { tasks.value = (await orgRequest("GET", "/tasks")).items; } catch (exc) { error.value = errorText(exc); } }
async function create() {
  busy.value = true; error.value = "";
  try {
    const body = { name: name.value, tender_number: number.value || null, deadline: deadline.value ? new Date(deadline.value).toISOString() : null, budget_usd: budget.value === "" ? null : Number(budget.value) };
    const result = await orgRequest("POST", "/tasks", body);
    await router.push(`/org/tasks/${result.data.id}`);
  } catch (exc) { error.value = errorText(exc); } finally { busy.value = false; }
}
onMounted(load);
</script>
<template>
  <h2>招标任务</h2>
  <p class="hint">从招标原文到逐条人工审阅。初稿三表只覆盖所选抽取集合。</p>
  <form v-if="createAllowed" class="panel" @submit.prevent="create">
    <h3>创建任务</h3><div class="grid">
      <label>任务名称<input v-model="name" required maxlength="200" /></label>
      <label>招标编号<input v-model="number" maxlength="100" /></label>
      <label>截止时间<input v-model="deadline" type="datetime-local" /></label>
      <label>预算记录（USD）<input v-model="budget" type="number" min="0" step="any" /></label>
    </div><p class="hint">截止时间按本机时区提交；预算仅作记录，不是强制费用上限。</p>
    <button class="primary" :disabled="busy">创建任务</button>
  </form>
  <p v-if="error" role="alert" class="error">{{ error }}</p>
  <p v-if="tasks === null" role="status">正在读取任务…</p>
  <table v-else><caption>本单位任务</caption><thead><tr><th>名称</th><th>模型外发遮挡</th><th>入口</th></tr></thead>
    <tbody><tr v-for="task in tasks" :key="task.id"><td>{{ task.name }}</td><td>{{ task.model_redaction_enabled ? "开启" : "关闭" }}</td><td><RouterLink :to="`/org/tasks/${task.id}`">打开任务</RouterLink></td></tr>
    <tr v-if="!tasks.length"><td colspan="3">暂无任务</td></tr></tbody>
  </table>
</template>
