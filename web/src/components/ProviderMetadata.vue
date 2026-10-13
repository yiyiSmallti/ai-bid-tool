<script setup>
import { formatTime } from "../ui.js";
defineProps({ value: Object });
</script>
<template>
  <dl v-if="value" class="provider-details">
    <dt>配置修订</dt><dd>{{value.revision}} · <span class="mono">{{value.id}}</span></dd>
    <dt>供应商 / 模型</dt><dd>{{value.provider}} / {{value.model}}</dd>
    <dt>HTTPS 端点</dt><dd>{{value.configuration.base_url??'供应商标准端点'}}</dd>
    <dt>凭据状态</dt><dd>已配置；不代表连接成功</dd>
    <dt>推理档位</dt><dd>{{value.reasoning.map(level=>`${level.label??level.name} (${level.name})`).join('、')||'未登记'}} · 默认 {{value.default_reasoning??'未登记'}}</dd>
    <dt>精确修订时间</dt><dd>{{formatTime(value.revised_at)}}</dd>
  </dl>
</template>
<style scoped>.provider-details{display:grid;grid-template-columns:minmax(130px,max-content) minmax(0,1fr);gap:8px 16px}.provider-details dt{color:var(--muted)}.provider-details dd{margin:0;overflow-wrap:anywhere}@media(max-width:600px){.provider-details{grid-template-columns:1fr;gap:4px}.provider-details dd{margin-bottom:8px}}</style>
