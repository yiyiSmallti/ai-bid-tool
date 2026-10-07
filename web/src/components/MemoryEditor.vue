<script setup>
import { computed } from "vue";
const props=defineProps({modelValue:{type:Object,required:true},busy:Boolean,blocked:Boolean});const emit=defineEmits(["update:modelValue","save","cancel"]);
function set(key,value){emit("update:modelValue",{...props.modelValue,content:{...props.modelValue.content,[key]:value}});}
const tags=computed({get:()=>props.modelValue.content.tags.join(", "),set:value=>set("tags",value.split(/[,，]/).map(tag=>tag.trim()).filter(Boolean))});
function expiry(value){emit("update:modelValue",{...props.modelValue,expires_at:value||null});}
</script>
<template>
  <form @submit.prevent="emit('save')" class="memory-form">
    <label for="memory-kind">记忆类型</label><select id="memory-kind" :value="modelValue.content.kind" :disabled="busy||blocked" @change="set('kind',$event.target.value)"><option value="rule">规则</option><option value="preference">偏好</option></select>
    <label for="memory-key">冲突键</label><input id="memory-key" :value="modelValue.content.conflict_key" maxlength="100" pattern="[a-z0-9][a-z0-9_.\-]*" required :disabled="busy||blocked" autocomplete="off" @input="set('conflict_key',$event.target.value)"/><p class="hint">同类记忆使用相同冲突键表示互斥规则，生效冲突必须由管理员决定。</p>
    <label for="memory-text">记忆正文</label><textarea id="memory-text" :value="modelValue.content.text" maxlength="2000" rows="6" required :disabled="busy||blocked" autocomplete="off" @input="set('text',$event.target.value)"/>
    <label for="memory-tags">标签（逗号分隔，最多 20 个）</label><input id="memory-tags" v-model="tags" :disabled="busy||blocked" autocomplete="off"/>
    <label for="memory-expiry">到期时间（可选，含时区的 ISO 时间）</label><input id="memory-expiry" :value="modelValue.expires_at??''" placeholder="2027-01-01T00:00:00Z" :disabled="busy||blocked" autocomplete="off" @input="expiry($event.target.value)"/>
    <p class="hint">保存后为候选；人工审核通过后才生效。不要录入报价、身份、银行、凭据或保密字段内容。记忆不能作为证据。</p>
    <div class="actions"><el-button native-type="submit" type="primary" :loading="busy" :disabled="busy||blocked">保存候选记忆</el-button><el-button :disabled="busy" @click="emit('cancel')">取消编辑</el-button></div>
  </form>
</template>
<style scoped>.memory-form label{display:block;margin:12px 0 5px}.memory-form input,.memory-form textarea,.memory-form select{display:block;width:100%;padding:8px 10px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:inherit;font:inherit}.memory-form select{max-width:200px}</style>
