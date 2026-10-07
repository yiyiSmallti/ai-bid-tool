<script setup>
import { computed } from 'vue';
import SecretTextEditor from './SecretTextEditor.vue';
import { profileTexts, qualificationKinds } from '../qualifications.js';
const props=defineProps({kind:{type:String,required:true},modelValue:{type:Object,required:true},fields:{type:Array,default:()=>[]},busy:Boolean});
const emit=defineEmits(['update:modelValue','save','cancel']);
const form=computed({get:()=>props.modelValue,set:value=>emit('update:modelValue',value)});
const valid=computed(()=>form.value.name.trim() && (props.kind==='profiles' || form.value.number.trim() && (!form.value.valid_from || !form.value.valid_until || form.value.valid_from<=form.value.valid_until)));
</script>
<template>
 <el-form label-position="top" @submit.prevent="emit('save')">
  <el-form-item :label="kind==='profiles'?'单位名称':'证照名称'"><el-input v-model="form.name" :aria-label="kind==='profiles'?'单位名称':'证照名称'" maxlength="200" :disabled="busy"/></el-form-item>
  <template v-if="kind==='profiles'"><el-form-item v-for="text in profileTexts" :key="text.key" :label="text.label"><SecretTextEditor v-model="form[text.key]" :fields="fields" :disabled="busy" :label="text.label" :rows="text.rows" :maxlength="text.max"/></el-form-item><p class="hint">只插入保密字段标签，真实值在导出时填入。</p></template>
  <template v-else><el-form-item label="类别"><el-select v-model="form.kind" aria-label="证照类别" :disabled="busy"><el-option label="单位证照" value="qualification"/><el-option label="人员证书" value="personnel"/></el-select></el-form-item><el-form-item label="证号"><el-input v-model="form.number" aria-label="证号" maxlength="200" :disabled="busy"/></el-form-item><div class="actions"><el-form-item label="有效期起"><el-input v-model="form.valid_from" type="date" aria-label="有效期起" :disabled="busy"/></el-form-item><el-form-item label="有效期止"><el-input v-model="form.valid_until" type="date" aria-label="有效期止" :disabled="busy"/></el-form-item></div><p class="hint">有效期止留空表示日期未声明；保存元数据会生成没有原件的新修订，可随后上传原件。</p></template>
  <div class="actions"><el-button type="primary" native-type="submit" :loading="busy" :disabled="!valid">保存{{qualificationKinds[kind].label}}</el-button><el-button :disabled="busy" @click="emit('cancel')">取消编辑</el-button></div>
 </el-form>
</template>
