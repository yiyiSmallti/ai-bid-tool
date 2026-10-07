<script setup>
import { onUnmounted } from 'vue';
import CertificateSection from '../components/CertificateSection.vue';
import QualificationList from '../components/QualificationList.vue';
import { qualificationReader } from '../qualifications.js';
// Both bounded lists share two request slots; originals and declaration bodies load
// only after opening an exact detail, rather than preloading the entire library.
const reader=qualificationReader();
onUnmounted(()=>reader.stop());
</script>
<template>
 <div class="page-header"><div><h2>单位资料</h2><p class="subtitle">由商务审核或单位管理员维护。资料与证照均为自行声明，真实性、业绩和资质未经核验；任务固定所选修订，资料更新不会自动升级任务。</p></div></div>
 <QualificationList kind="profiles" :reader="reader"/>
 <CertificateSection :reader="reader"/>
</template>
