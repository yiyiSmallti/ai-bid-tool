<script setup>
import { jobStatuses, label, formatTime } from "../org.js";
defineProps({jobs:{type:Array,default:()=>[]}});
</script>
<template><ul class="job-list"><li v-for="job in jobs" :key="job.job_id"><strong>{{ label(jobStatuses,job.state) }}</strong> · 尝试 {{ job.attempts }} 次<div class="hint mono">{{ job.job_id }}</div><template v-if="job.progress"><p v-if="job.progress.total === null || job.progress.total === undefined">已完成 {{ job.progress.completed }} 项；总量未知</p><template v-else><p>已完成 {{ job.progress.completed }} / {{ job.progress.total }} 项</p><el-progress v-if="job.progress.total > 0" :percentage="Math.floor(job.progress.completed / job.progress.total * 100)" /></template></template><p v-else class="hint">尚无已提交的工作量记录</p><time v-if="job.updated_at" class="hint">{{ formatTime(job.updated_at) }}</time></li><li v-if="!jobs.length" class="empty">没有可见的作业。</li></ul></template>
<style scoped>.job-list{list-style:none;padding:0;margin:0}.job-list li{padding:12px 0;border-bottom:1px solid var(--border)}.job-list p{margin:6px 0}</style>
