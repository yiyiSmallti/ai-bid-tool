<script setup>
import {onMounted,onUnmounted,ref,watch} from "vue";
import {checkProductPage,productPath,rememberProductCursor} from "../products.js";
import {errorText} from "../org.js";
const props=defineProps({modelValue:{type:String,default:""},reader:{type:Object,required:true},channel:{type:String,required:true},disabled:Boolean,optional:Boolean});
const emit=defineEmits(["update:modelValue","denied"]);
const search=ref(""),rows=ref([]),meta=ref(null),cursor=ref(null),previous=ref([]),loading=ref(false),error=ref("");
let generation=0,stopped=false,timer;
async function load(next=null){if(stopped)return;const run=++generation;props.reader.cancel(props.channel);loading.value=true;rows.value=[];meta.value=null;error.value="";cursor.value=next;
 try{const result=checkProductPage(await props.reader.read(props.channel,"POST",`${productPath}/query`,{q:search.value.trim()||null,state:props.optional?"all":"active",cursor:next,limit:25}));if(stopped||run!==generation)return;rows.value=result.items;meta.value=result.data;}
 catch(exc){if(run===generation&&exc.name!=="AbortError"){error.value=errorText(exc);if([401,403,404].includes(exc.status))emit("denied",exc);if(["management_cursor_invalid","management_cursor_expired"].includes(exc.code))previous.value=[];}}
 finally{if(run===generation)loading.value=false;}}
function reset(){previous.value=[];load();}
watch(search,()=>{generation++;props.reader.cancel(props.channel);rows.value=[];meta.value=null;loading.value=true;clearTimeout(timer);timer=setTimeout(reset,300);});
function next(){if(meta.value?.has_more){rememberProductCursor(previous.value,cursor.value);load(meta.value.next_cursor);}}
function back(){if(previous.value.length)load(previous.value.pop());}
onMounted(()=>load());onUnmounted(()=>{stopped=true;generation++;clearTimeout(timer);props.reader.cancel(props.channel);search.value="";rows.value=[];});
</script>
<template>
  <fieldset class="feature-product-picker" :disabled="disabled"><legend>{{optional?'按所属产品筛选':'所属产品'}}</legend>
    <p v-if="modelValue" class="hint">{{optional?'筛选产品 ID':'已关联产品 ID'}}：<span class="mono">{{modelValue}}</span> <RouterLink :to="`/org/products/${modelValue}`">查看产品</RouterLink></p>
    <p v-else class="hint">{{optional?'全部产品':'请选择同单位可用产品。已有的产品关联不会自动替换。'}}</p>
    <div class="actions"><el-input v-model="search" maxlength="200" :disabled="disabled" :aria-label="optional?'筛选产品搜索':'关联产品搜索'" placeholder="搜索产品名称、厂家、型号" clearable/><el-button :disabled="disabled||loading" @click="reset">重新读取产品候选</el-button><el-button v-if="optional&&modelValue" :disabled="disabled" @click="emit('update:modelValue','')">全部产品</el-button></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert"/>
    <p v-if="loading" role="status">正在读取产品候选…</p>
    <div v-else class="product-options" role="group" :aria-label="optional?'筛选产品候选':'关联产品候选'"><el-button v-for="row in rows" :key="row.ref.resource_id" :type="modelValue===row.ref.resource_id?'primary':'default'" :disabled="disabled||!optional&&row.lifecycle.state!=='active'" @click="emit('update:modelValue',row.ref.resource_id)">{{row.name}} · 修订 {{row.revision}}{{row.lifecycle.state==='inactive'?' · 已停用':''}}</el-button><p v-if="meta&&!rows.length" class="hint">{{optional?'没有符合条件的产品':'没有符合条件的可用产品'}}</p></div>
    <div class="actions"><el-button :disabled="disabled||loading||!previous.length" @click="back">上一页产品候选</el-button><el-button :disabled="disabled||loading||!meta?.has_more" @click="next">下一页产品候选</el-button><span v-if="meta" class="hint">本页 {{meta.returned}} 项</span></div>
    <input v-if="!optional" :value="modelValue" required aria-label="所属产品 ID" class="product-id" readonly tabindex="-1"/>
  </fieldset>
</template>
<style scoped>.feature-product-picker{border:1px solid var(--el-border-color);border-radius:var(--el-border-radius-base);padding:12px;margin:0 0 16px;min-width:0}.product-options{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.product-options .el-button{margin:0;max-width:100%;height:auto;min-height:32px;white-space:normal}.actions .el-input{width:min(360px,100%)}.product-id{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none}</style>
