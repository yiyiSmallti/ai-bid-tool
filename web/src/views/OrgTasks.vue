<script setup>
import { Plus } from "@element-plus/icons-vue";
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { errorText, orgAccess, orgRequest } from "../org.js";
const router = useRouter();
const tasks = ref(null), error = ref(""), createError = ref(""), busy = ref(false), creating = ref(false);
const name = ref(""), number = ref(""), deadline = ref(""), budget = ref("");
const createAllowed = computed(() => ["admin", "bidder"].includes(orgAccess.role));
async function load() { try { tasks.value = (await orgRequest("GET", "/tasks")).items; } catch (exc) { error.value = errorText(exc); } }
async function create() {
  busy.value = true; createError.value = "";
  try {
    const body = { name: name.value, tender_number: number.value || null, deadline: deadline.value ? new Date(deadline.value).toISOString() : null, budget_usd: budget.value === "" ? null : Number(budget.value) };
    const result = await orgRequest("POST", "/tasks", body);
    creating.value = false;
    await router.push(`/org/tasks/${result.data.id}`);
  } catch (exc) { createError.value = errorText(exc); } finally { busy.value = false; }
}
onMounted(load);
</script>
<template>
  <div class="page-header">
    <div><h2>招标任务</h2><p class="subtitle">从招标原文到逐条人工审阅。初稿三表只覆盖所选抽取集合。</p></div>
    <el-button v-if="createAllowed" type="primary" :icon="Plus" @click="creating = true">新建任务</el-button>
  </div>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="section" />
  <el-skeleton v-if="tasks === null && !error" :rows="4" animated role="status" aria-label="正在读取任务" />
  <div v-else-if="tasks" class="table-scroll">
    <table class="data-table"><caption>本单位任务（{{ tasks.length }}）</caption>
      <thead><tr><th>任务名称</th><th>模型外发遮挡</th><th class="num">操作</th></tr></thead>
      <tbody>
        <tr v-for="task in tasks" :key="task.id">
          <td><RouterLink :to="`/org/tasks/${task.id}`" class="task-name">{{ task.name }}</RouterLink></td>
          <td><el-tag :type="task.model_redaction_enabled ? 'success' : 'warning'" size="small">{{ task.model_redaction_enabled ? "开启" : "关闭" }}</el-tag></td>
          <td class="num"><RouterLink :to="`/org/tasks/${task.id}`">打开任务</RouterLink></td>
        </tr>
        <tr v-if="!tasks.length"><td colspan="3" class="empty">暂无任务{{ createAllowed ? "，点右上角「新建任务」开始" : "" }}</td></tr>
      </tbody>
    </table>
  </div>
  <el-dialog v-if="createAllowed" v-model="creating" width="560px" :close-on-click-modal="false" aria-label="创建任务">
    <template #header><h3 class="dialog-title">创建任务</h3></template>
    <el-form label-position="top" @submit.prevent="create">
      <el-form-item label="任务名称" required><el-input v-model="name" required maxlength="200" show-word-limit /></el-form-item>
      <div class="grid">
        <el-form-item label="招标编号"><el-input v-model="number" maxlength="100" /></el-form-item>
        <el-form-item label="预算记录（USD）"><el-input v-model="budget" type="number" min="0" step="any" /></el-form-item>
      </div>
      <el-form-item label="截止时间"><el-input v-model="deadline" type="datetime-local" /></el-form-item>
      <p class="hint">截止时间按本机时区提交；预算仅作记录，不是强制费用上限。</p>
      <el-alert v-if="createError" :title="createError" type="error" show-icon :closable="false" class="section" />
      <div class="actions dialog-actions"><el-button @click="creating = false">取消</el-button><el-button type="primary" native-type="submit" :loading="busy" :disabled="!name.trim()">创建任务</el-button></div>
    </el-form>
  </el-dialog>
</template>
<style scoped>
.task-name { font-weight: 500; }
.dialog-title { margin: 0; }
.dialog-actions { justify-content: flex-end; margin-bottom: 0; }
</style>
