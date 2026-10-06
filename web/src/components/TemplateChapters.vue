<script setup>
const props=defineProps({modelValue:Array,depth:{type:Number,default:1},busy:Boolean});
const emit=defineEmits(["update:modelValue"]);
function change(index,field,value){emit("update:modelValue",props.modelValue.map((chapter,i)=>i===index?{...chapter,[field]:value}:chapter));}
function add(){emit("update:modelValue",[...props.modelValue,{title:"",children:[]}]);}
function remove(index){emit("update:modelValue",props.modelValue.filter((_,i)=>i!==index));}
</script>
<template>
 <div class="chapters"><div v-for="(chapter,index) in modelValue" :key="index" class="chapter"><div class="actions"><el-input :model-value="chapter.title" :aria-label="`第 ${depth} 层章节 ${index+1} 标题`" maxlength="200" :disabled="busy" placeholder="声明章节标题" @update:model-value="change(index,'title',$event)"/><el-button :disabled="busy" @click="remove(index)">移除章节</el-button></div><TemplateChapters v-if="depth<6" :model-value="chapter.children" :depth="depth+1" :busy="busy" @update:model-value="change(index,'children',$event)"/></div><el-button :disabled="busy||modelValue.length>=100" @click="add">{{depth===1?'添加声明章节':'添加子章节'}}</el-button></div>
</template>
<style scoped>.chapters{padding-left:14px;border-left:2px solid var(--el-border-color);margin:10px 0}.chapter .el-input{max-width:420px}</style>
