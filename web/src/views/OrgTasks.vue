<script setup>
import { Plus } from "@element-plus/icons-vue";
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { request } from "../api.js";
import { errorText, formatTime } from "../org.js";
const router = useRouter();
const tasks = ref(null), error = ref(""), createError = ref(""), busy = ref(false), creating = ref(false);
const name = ref(""), number = ref(""), deadline = ref("");
const SOON_MS = 7 * 24 * 60 * 60 * 1000;
// Deadline state is derived on the client; the server keeps the fixed timestamp only.
function deadlineState(value) {
  if (!value) return { tag: "", label: "未设置" };
  const at = new Date(value).getTime();
  if (Number.isNaN(at)) return { tag: "", label: "未设置" };
  const remaining = at - Date.now();
  if (remaining < 0) return { tag: "danger", label: "已过期" };
  if (remaining <= SOON_MS) return { tag: "warning", label: "临近截止" };
  return { tag: "success", label: "进行中" };
}
const rows = computed(() => (tasks.value ?? []).map((task) => ({ ...task, deadlineState: deadlineState(task.deadline) })));
async function load() { try { tasks.value = (await request("GET", "/tasks")).items; } catch (exc) { error.value = errorText(exc); } }
async function create() {
  busy.value = true; createError.value = "";
  try {
    const body = { name: name.value, tender_number: number.value || null, deadline: deadline.value ? new Date(deadline.value).toISOString() : null };
    const result = await request("POST", "/tasks", body);
    creating.value = false;
    await router.push(`/org/tasks/${result.data.id}`);
  } catch (exc) { createError.value = errorText(exc); } finally { busy.value = false; }
}
onMounted(load);
</script>
<template>
  <div class="page-header">
    <div><h2>招标任务</h2><p class="subtitle">从招标原文到逐条人工审阅。初稿三表只覆盖所选抽取集合。</p></div>
    <el-button type="primary" size="large" :icon="Plus" @click="creating = true">新建任务</el-button>
  </div>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="section" />
  <el-skeleton v-if="tasks === null && !error" :rows="4" animated role="status" aria-label="正在读取任务" />
  <template v-else-if="tasks">
    <div v-if="rows.length" class="table-scroll">
      <table class="data-table tasks-table"><caption>任务列表（{{ tasks.length }}）</caption>
        <thead><tr><th>任务名称</th><th>招标编号</th><th>截止时间</th><th>模型外发遮挡</th><th class="num">操作</th></tr></thead>
        <tbody>
          <tr v-for="task in rows" :key="task.id">
            <td>
              <RouterLink :to="`/org/tasks/${task.id}`" class="task-name">{{ task.name }}</RouterLink>
              <div class="hint mono">{{ task.id }}</div>
            </td>
            <td>{{ task.tender_number || "未填写" }}</td>
            <td class="deadline">
              <span>{{ task.deadline ? formatTime(task.deadline) : "未设置" }}</span>
              <el-tag :type="task.deadlineState.tag || 'info'" size="small" effect="plain">{{ task.deadlineState.label }}</el-tag>
            </td>
            <td><el-tag :type="task.model_redaction_enabled ? 'success' : 'warning'" size="small">{{ task.model_redaction_enabled ? "开启" : "关闭" }}</el-tag></td>
            <td class="num"><RouterLink :to="`/org/tasks/${task.id}`" class="open-task">打开任务</RouterLink></td>
          </tr>
        </tbody>
      </table>
    </div>
    <el-card v-else class="section empty-card" shadow="never">
      <el-empty description="暂无任务，点右上角「新建任务」开始">
        <el-button type="primary" :icon="Plus" @click="creating = true">新建任务</el-button>
      </el-empty>
    </el-card>
  </template>
  <el-dialog v-model="creating" width="560px" :close-on-click-modal="false" aria-label="创建任务">
    <template #header><h3 class="dialog-title">创建任务</h3></template>
    <el-form label-position="top" @submit.prevent="create">
      <el-form-item label="任务名称" required><el-input v-model="name" required maxlength="200" show-word-limit /></el-form-item>
      <div class="grid">
        <el-form-item label="招标编号"><el-input v-model="number" maxlength="100" /></el-form-item>
      </div>
      <el-form-item label="截止时间"><el-input v-model="deadline" type="datetime-local" /></el-form-item>
      <p class="hint">截止时间按本机时区提交。</p>
      <el-alert v-if="createError" :title="createError" type="error" show-icon :closable="false" class="section" />
      <div class="actions dialog-actions"><el-button @click="creating = false">取消</el-button><el-button type="primary" native-type="submit" :loading="busy" :disabled="!name.trim()">创建任务</el-button></div>
    </el-form>
  </el-dialog>

</template>
<style scoped>
.tasks-table { min-width: 720px; }
.task-name { font-weight: 600; }
.tasks-table .hint { font-size: 12px; }
.deadline { white-space: nowrap; }
.deadline > span { margin-right: 8px; font-variant-numeric: tabular-nums; }
.open-task { font-weight: 500; }
.empty-card { border-style: dashed; }
.empty-card :deep(.el-card__body) { padding: 12px 20px 24px; }
.dialog-title { margin: 0; }
.dialog-actions { justify-content: flex-end; margin-bottom: 0; }
</style>
