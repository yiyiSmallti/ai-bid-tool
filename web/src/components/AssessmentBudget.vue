<script setup>
import { computed } from "vue";
import { codeText } from "../assessments.js";
import { money } from "../api.js";
const props = defineProps({ preview: Object, budget: Object, rules: Boolean });
const preflight = computed(() => props.preview?.budget_preflight);
const current = computed(() => preflight.value?.task_budget ?? props.budget);
const blockers = computed(() => [...new Set([props.preview?.admission_blocker, preflight.value?.admission_blocker].filter(Boolean))]);
</script>
<template>
  <section aria-label="费用与任务预算" class="assessment-budget">
    <h4>任务预算（实际执行）</h4>
    <dl v-if="current" class="budget-values">
      <dt>预算上限</dt><dd>{{ current.limit == null ? '未设置' : money(current.limit, current.currency) }}</dd>
      <dt>已花费</dt><dd>{{ money(current.spent, current.currency) }}</dd>
      <dt>已预留</dt><dd>{{ money(current.reserved, current.currency) }}</dd>
      <dt>可用额度</dt><dd>{{ money(current.available, current.currency) }}</dd>
      <dt>预算修订</dt><dd>{{ current.revision }} · {{ current.state === 'active' ? '执行中' : '需要核对币种' }}</dd>
    </dl>
    <p v-else>任务预算暂不可用</p>
    <template v-if="preview">
      <p v-if="rules">不调用模型，费用为 0</p>
      <p v-else>预计服务用量成本（美元）：{{ preview.estimated_cost?.usd == null ? '暂无法估算' : money(preview.estimated_cost.usd, 'USD') }}</p>
      <p>预计平台扣费上限（首轮）：{{ money(preview.estimated_charge, preview.billing_currency) }}</p>
      <p v-if="preflight">预计计入任务预算：{{ money(preflight.estimate?.task_amount, preflight.estimate?.billing_currency) }} · 预计调用 {{ preflight.planned_calls ?? '未知' }} 次</p>
      <p class="hint">预检不预留额度；重试、拆分和并发用量可能影响实际支出，不保证整次任务都能完成。自有密钥的平台扣费为零时，供应商仍可能计费。</p>
      <p>计价依据：{{ preview.cost_basis === 'unknown' ? '暂不可估' : '已知' }}（{{ preview.cost_basis_reason }}）</p>
      <p>使用的模型：{{ preview.model ?? '不调用模型或尚未选定' }} · 模型修订 {{ preview.model_revision ?? '未知' }} · 推理档位 {{ preview.reasoning ?? '默认' }}</p>
      <p>{{ blockers.includes('redaction_required') ? '尚未开启外发遮挡' : '外发内容已遮挡' }}：规则修订 {{ preview.redaction_revision }} · {{ Object.entries(preview.redacted_counts ?? {}).map(([key, value]) => `${key}: ${value}`).join('，') || '无替换内容' }}</p>
      <p>{{ preview.estimated_duration_ms == null ? '耗时暂无法估算' : `预计耗时 ${preview.estimated_duration_ms} ms` }}</p>
      <el-alert v-for="blocker in blockers" :key="blocker" :title="codeText(blocker)" type="error" :closable="false" show-icon role="alert" />
    </template>
  </section>
</template>
<style scoped>.budget-values { display:grid; grid-template-columns: max-content 1fr; gap:6px 16px; } dd { margin:0; } .assessment-budget { background:var(--surface-muted); padding:14px; border-radius:6px; margin:14px 0; }</style>
