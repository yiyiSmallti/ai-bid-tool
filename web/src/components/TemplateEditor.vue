<script setup>
import {computed} from "vue";
import TemplateChapters from "./TemplateChapters.vue";
import {validTemplateData} from "../templates.js";
const props=defineProps({modelValue:Object,busy:Boolean,file:File});
const emit=defineEmits(["update:modelValue","file","save","cancel"]);
const projects=computed(()=>(props.modelValue.project_types??[]).join("\n"));
function change(key,value){emit("update:modelValue",{...props.modelValue,[key]:value});}
const valid=computed(()=>validTemplateData(props.modelValue)&&props.file&&props.file.name.toLowerCase().endsWith(".docx")&&props.file.size>0&&props.file.size<=40*1024*1024);
</script>
<template>
 <el-form label-position="top" @submit.prevent="emit('save')"><el-form-item label="模板名称"><el-input :model-value="modelValue.name" aria-label="模板名称" maxlength="200" :disabled="busy" @update:model-value="change('name',$event)"/></el-form-item><el-form-item label="适用项目类型（每行一项，可选）"><el-input :model-value="projects" type="textarea" :rows="3" aria-label="适用项目类型" :disabled="busy" @update:model-value="change('project_types',$event.trim()?$event.split('\n'):null)"/></el-form-item><el-form-item label="声明章节（最多 200 个节点、六层）"><TemplateChapters :model-value="modelValue.chapters??[]" :busy="busy" @update:model-value="change('chapters',$event.length?$event:null)"/></el-form-item><el-form-item label="本次修订的 DOCX 原件"><input type="file" accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document" aria-label="模板 DOCX" :disabled="busy" required @change="emit('file',$event.target.files[0]??null)"/></el-form-item><p class="hint">每次保存必须上传 DOCX，最大 40 MiB。声明章节不编辑 Word 原件；文件有效性由服务器校验，校验通过不代表导出获批。</p><div class="actions"><el-button type="primary" native-type="submit" :disabled="busy||!valid" :loading="busy">保存模板</el-button><el-button :disabled="busy" @click="emit('cancel')">取消编辑</el-button></div></el-form>
</template>
