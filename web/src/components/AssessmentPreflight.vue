<script setup>
import { computed } from "vue";
import { codeText } from "../assessments.js";
const props = defineProps({ preview: Object, rules: Boolean });
const preflight = computed(() => props.preview?.preflight);
</script>
<template>
  <section v-if="preview" aria-label="预检与预计用量" class="assessment-preflight">
    <p v-if="rules">本地规则检查不调用模型。</p>
    <p v-if="preflight">预计调用 {{ preflight.planned_calls ?? '未知' }} 次 · 预计 tokens {{ preflight.estimate?.llm_tokens ?? '未知' }}</p>
    <p v-if="preview.estimated_duration_ms != null">预计时长 {{ preview.estimated_duration_ms }} ms</p>
    <p v-if="preflight?.admission_blocker || preview.admission_blocker" role="alert">{{ codeText(preflight?.admission_blocker ?? preview.admission_blocker) }}</p>
    <p class="hint">预检不写入、不调用服务；提交时重新核对输入、配置和权限。</p>
  </section>
</template>
<style scoped>.assessment-preflight { background:var(--surface-muted); padding:14px; border-radius:6px; margin:14px 0; }</style>
