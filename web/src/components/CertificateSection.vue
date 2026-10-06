<script setup>
import QualificationList from './QualificationList.vue';
defineProps({reader:{type:Object,required:true}});
</script>
<template><section data-testid="certificate-section"><QualificationList kind="certificates" :reader="reader"/></section></template>
