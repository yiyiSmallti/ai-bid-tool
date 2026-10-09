<script setup>
import { computed } from "vue";
const props = defineProps({ location: { type: Object, required: true } });
const triage = computed(() => ({ triage_present: "初筛存在", triage_absent: "初筛未发现", triage_uncertain: "初筛未确定" })[props.location.status]);
const percent = value => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1 ? `${(value * 100).toFixed(1)}%` : "未确定";
</script>
<template>
  <div data-testid="signing-location">
    <p>{{ location.page ? `投标第 ${location.page} 页` : '投标页码未确定' }} · {{ triage ?? '位置未解决' }}<span v-if="location.group_id"> · 骑缝组 {{ location.group_id }}</span></p>
    <template v-if="triage">
      <p v-if="location.probability_yes !== null && location.probability_yes !== undefined">存在概率 {{ percent(location.probability_yes) }}</p>
      <p v-for="(value, mark) in location.presence_probabilities" :key="mark">{{ mark === 'company_seal' ? '单位公章' : mark === 'signature' ? '签字' : mark }}存在概率 {{ percent(value) }}</p>
      <p>需人工确认 · 单位归属、日期与指定位置仍未解决</p>
      <el-alert v-if="location.status === 'triage_absent'" title="初筛未发现所需签章：涉及废标条件时须升级人工核对，不可直接通过。" type="warning" :closable="false" />
    </template>
  </div>
</template>
