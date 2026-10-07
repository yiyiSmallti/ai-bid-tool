<script setup>
defineProps({chapters:Array});
</script>
<template><ol class="chapter-tree"><li v-for="(chapter,index) in chapters" :key="index">{{chapter.title}}<TemplateChapterTree v-if="chapter.children.length" :chapters="chapter.children"/></li></ol></template>
<style scoped>.chapter-tree{padding-left:20px;margin:4px 0}.chapter-tree li{margin:4px 0;overflow-wrap:anywhere}</style>
