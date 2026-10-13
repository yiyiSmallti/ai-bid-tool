<script setup>
import { computed } from "vue";
import { useRoute } from "vue-router";
const route = useRoute();
const props = defineProps({ taskId: { type: String, required: true } });
// The workflow order a person actually follows; labels stay the route titles.
const steps = [
  { key: "files", to: () => `/org/tasks/${props.taskId}`, label: "文件与抽取" },
  { key: "requirements", to: () => ({ path: `/org/tasks/${props.taskId}/requirements`, query: route.query.job ? { job: route.query.job } : {} }), label: "要求确认与补录" },
  { key: "board", to: () => ({ path: `/org/tasks/${props.taskId}/board`, query: route.query.job ? { job: route.query.job } : {} }), label: "任务看板" },
  { key: "progress", to: () => `/org/tasks/${props.taskId}/progress`, label: "作业进度" },
  { key: "bid-submissions", to: () => `/org/tasks/${props.taskId}/bid-submissions`, label: "标书检验" },
  { key: "attachments", to: () => `/org/tasks/${props.taskId}/attachments`, label: "附件与隐私" },
  { key: "memory-feedback", to: () => `/org/tasks/${props.taskId}/memory-feedback`, label: "记忆反馈队列" },
];
// Child routes (a review, a draft, a card annotation, a submission, a report) keep the
// step that owns them selected, so the bar never shows "no step" inside a task.
const childSteps = {
  requirements: "requirements", review: "requirements", cards: "requirements",
  drafts: "requirements", "score-rubrics": "requirements", checks: "requirements", scores: "requirements",
  "bid-submissions": "bid-submissions", "bid-reviews": "bid-submissions",
  board: "board", progress: "progress", attachments: "attachments", "memory-feedback": "memory-feedback",
};
const currentStep = computed(() => {
  const prefix = `/org/tasks/${props.taskId}`;
  if (!route.path.startsWith(prefix)) return "";
  const segment = route.path.slice(prefix.length).replace(/^\//, "").split("/")[0];
  return segment ? childSteps[segment] ?? "" : "files";
});
</script>
<template>
  <nav class="task-navigation section" aria-label="任务导航">
    <ul class="task-steps">
      <li v-for="step in steps" :key="step.key">
        <RouterLink :to="step.to()" class="task-step" :class="{ active: currentStep === step.key }" :title="step.label">{{ step.label }}</RouterLink>
      </li>
    </ul>
  </nav>
</template>
<style scoped>
.task-navigation { display: flex; }
.task-steps {
  display: flex; flex-wrap: wrap; align-items: center; gap: 2px; margin: 0; padding: 3px;
  list-style: none; background: var(--surface-muted); border: 1px solid var(--border);
  border-radius: 11px; max-width: 100%; overflow-x: auto;
}
.task-steps li { display: flex; flex: none; }
.task-step {
  display: inline-flex; align-items: center; padding: 6px 12px; border-radius: 8px;
  color: var(--text-secondary); font-size: 13px; line-height: 1.4; white-space: nowrap;
  transition: background var(--duration) var(--ease), color var(--duration) var(--ease), box-shadow var(--duration) var(--ease);
}
.task-step:hover { background: rgba(0, 0, 0, 0.05); color: var(--text); text-decoration: none; }
.task-step.active {
  background: var(--surface); color: var(--text); font-weight: 600;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08), 0 0 0 1px rgba(0, 0, 0, 0.04);
}
.task-step:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
@media (prefers-reduced-motion: reduce) {
  .task-step { transition: none; }
}
@media (max-width: 720px) {
  .task-steps { flex-wrap: nowrap; }
}
</style>
