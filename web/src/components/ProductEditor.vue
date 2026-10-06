<script setup>
const props=defineProps({modelValue:{type:Object,required:true},busy:Boolean});const emit=defineEmits(["update:modelValue","save","cancel"]);
function set(key,value){emit("update:modelValue",{...props.modelValue,[key]:value||null});}
const fields=[{key:"name",label:"产品名称",max:200,required:true},{key:"vendor",label:"厂家",max:200,required:true},{key:"model",label:"精确型号",max:200,required:true},{key:"model_version",label:"型号版本",max:100},{key:"official_url",label:"官网规格页 URL",type:"url"},{key:"whitepaper_url",label:"白皮书 URL",type:"url"}];
</script>
<template>
  <form class="product-form" @submit.prevent="emit('save')">
    <div class="grid">
      <div v-for="field in fields" :key="field.key" class="section">
        <label :for="`product-${field.key}`">{{field.label}}<span v-if="field.required" aria-hidden="true"> *</span></label>
        <input :id="`product-${field.key}`" :value="modelValue[field.key]??''" :type="field.type??'text'" :maxlength="field.max" :required="field.required" :disabled="busy" class="product-input" :aria-label="field.label" autocomplete="off" @input="set(field.key,$event.target.value)" />
      </div>
    </div>
    <p class="hint">厂家、精确型号和版本需要与实际拟投产品一致。来源地址是声明，尚未完成证据抓取与人工核验。</p>
    <div class="actions"><el-button type="primary" native-type="submit" :loading="busy" :disabled="busy">保存产品</el-button><el-button :disabled="busy" @click="emit('cancel')">取消编辑</el-button></div>
  </form>
</template>
<style scoped>
.product-input{display:block;width:100%;height:34px;border:1px solid var(--el-border-color);border-radius:var(--el-border-radius-base);padding:0 11px;margin-top:6px;font:inherit;color:inherit;background:var(--surface)}
.product-input:disabled{background:var(--surface-muted)}
@media(max-width:600px){.grid{grid-template-columns:1fr}}
</style>
