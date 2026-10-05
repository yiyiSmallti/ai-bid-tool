<script setup>
import ConfidentialPanel from "../components/ConfidentialPanel.vue";
</script>
<template>
  <div class="page-header">
    <div>
      <h2>保密字段</h2>
      <p class="subtitle">报价、证件号、银行账号、联系人和电话登记在这里。起草时这些值换成占位符，模型看不到；导出时自动填回。</p>
    </div>
  </div>
  <ConfidentialPanel />
</template>
