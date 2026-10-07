<script setup>
import { money } from "../api.js";
import { formatTime } from "../ui.js";
defineProps({ value: Object, currency: String });
</script>
<template>
  <dl v-if="value" class="provider-details">
    <dt>来源</dt><dd>{{value.configuration.source==='org'?'自有密钥（BYOK）':'平台目录'}}</dd>
    <dt>配置修订</dt><dd>{{value.revision}} · <span class="mono">{{value.id}}</span></dd>
    <dt>供应商 / 模型</dt><dd>{{value.provider}} / {{value.model}}</dd>
    <dt v-if="value.configuration.source==='platform'">已保存目录标识</dt><dd v-if="value.configuration.source==='platform'">{{value.configuration.platform_model_id}} · {{value.catalog_state==='enabled'?'可用':'不可用（保留已保存的模型身份）'}} · 保存时目录修订 {{value.catalog_revision??'未知'}}</dd>
    <dt v-if="value.configuration.source==='org'">HTTPS 端点</dt><dd v-if="value.configuration.source==='org'">{{value.configuration.base_url??'供应商标准端点'}}</dd>
    <dt>凭据状态</dt><dd>{{value.credential_state==='configured'?'已配置；不代表连接成功':'由平台管理'}}</dd>
    <dt>推理档位</dt><dd>{{value.reasoning.map(level=>`${level.label??level.name} (${level.name})`).join('、')||'未登记'}} · 默认 {{value.default_reasoning??'未登记'}}</dd>
    <dt>{{value.configuration.source==='org'?'供应商声明用量价格':'保存时平台公布售价'}} / 百万 token</dt><dd>输入 {{money(value.configuration.source==='org'?value.configuration.input_usd_per_mtok:value.sale_input_per_mtok,value.configuration.source==='org'?'USD':currency)}} · 输出 {{money(value.configuration.source==='org'?value.configuration.output_usd_per_mtok:value.sale_output_per_mtok,value.configuration.source==='org'?'USD':currency)}}</dd>
    <dt>精确修订者 / 时间</dt><dd>{{value.revised_by??'未知'}} · {{formatTime(value.revised_at)}}</dd>
  </dl>
</template>
<style scoped>.provider-details{display:grid;grid-template-columns:minmax(130px,max-content) minmax(0,1fr);gap:8px 16px}.provider-details dt{color:var(--muted)}.provider-details dd{margin:0;overflow-wrap:anywhere}@media(max-width:600px){.provider-details{grid-template-columns:1fr;gap:4px}.provider-details dd{margin-bottom:8px}}</style>
