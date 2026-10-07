<script setup>
import { useRoute } from "vue-router";
const route = useRoute();
defineProps({ taskId: { type: String, required: true } });
</script>
<template><nav class="task-navigation section" aria-label="任务导航"><RouterLink :to="`/org/tasks/${taskId}`">文件与抽取</RouterLink><RouterLink :to="{path:`/org/tasks/${taskId}/requirements`,query:route.query.job?{job:route.query.job}:{}}">要求确认与补录</RouterLink><RouterLink :to="{path:`/org/tasks/${taskId}/board`,query:route.query.job?{job:route.query.job}:{}}">任务看板</RouterLink><RouterLink :to="`/org/tasks/${taskId}/progress`">作业进度</RouterLink><RouterLink :to="`/org/tasks/${taskId}/members`">成员与归档</RouterLink><RouterLink :to="`/org/tasks/${taskId}/memory-feedback`">记忆反馈队列</RouterLink></nav></template>
<style scoped>.task-navigation { display:flex;gap:20px;flex-wrap:wrap; }.router-link-exact-active { font-weight:600; }</style>
