<script setup>
import FeatureProductPicker from "./FeatureProductPicker.vue";
import {implementationLabels} from "../features.js";
const props=defineProps({modelValue:{type:Object,required:true},busy:Boolean,reader:{type:Object,required:true}});
const emit=defineEmits(["update:modelValue","save","cancel","denied"]);
function set(key,value){emit("update:modelValue",{...props.modelValue,[key]:value});}
</script>
<template>
  <form class="feature-form" @submit.prevent="emit('save')">
    <FeatureProductPicker :model-value="modelValue.product_id" :reader="reader" channel="edit-products" :disabled="busy" @update:model-value="set('product_id',$event)" @denied="emit('denied',$event)"/>
    <div class="section"><label for="feature-name">功能名称 *</label><input id="feature-name" :value="modelValue.name" maxlength="200" required :disabled="busy" class="feature-input" aria-label="功能名称" autocomplete="off" @input="set('name',$event.target.value)"/></div>
    <div class="section"><label for="feature-description">功能描述 *</label><textarea id="feature-description" :value="modelValue.description" maxlength="10000" required :disabled="busy" class="feature-input" rows="6" aria-label="功能描述" @input="set('description',$event.target.value)"/></div>
    <div class="section"><label for="feature-status">实现状态 *</label><select id="feature-status" :value="modelValue.status" required :disabled="busy" class="feature-input" aria-label="实现状态" @change="set('status',$event.target.value)"><option v-for="(label,value) in implementationLabels" :key="value" :value="value">{{label}}</option></select></div>
    <p class="hint">实现状态是单位声明。标为已实现后仍需真实截图等材料；保存功能不会替换截图、确认原型或确认任何证据。</p>
    <div class="actions"><el-button type="primary" native-type="submit" :loading="busy" :disabled="busy||!modelValue.product_id">保存功能</el-button><el-button :disabled="busy" @click="emit('cancel')">取消编辑</el-button></div>
  </form>
</template>
<style scoped>.feature-input{display:block;width:100%;min-height:34px;border:1px solid var(--el-border-color);border-radius:var(--el-border-radius-base);padding:7px 11px;margin-top:6px;font:inherit;color:inherit;background:var(--surface)}.feature-input:disabled{background:var(--surface-muted)}textarea.feature-input{resize:vertical}</style>
