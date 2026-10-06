<script setup>
import {computed,onMounted,onUnmounted,ref,watch} from "vue";
import {onBeforeRouteLeave,onBeforeRouteUpdate,useRoute} from "vue-router";
import {ApiError} from "../api.js";
import {confirmAction,errorText,formatTime,orgAccess,orgRequest} from "../org.js";
import {downloadTemplateOriginal,blankSections,checkBinding,checkBindingPage,checkBindingPreview,checkTemplateDetail,columnLabels,rememberTemplateCursor,sectionLabels,templatePath,templateReader,validId,validSections} from "../templates.js";
const route=useRoute(),id=route.params.templateId,revisionId=route.params.revisionId,reader=templateReader();
const template=ref(null),rows=ref([]),meta=ref(null),cursor=ref(null),previous=ref([]),selected=ref(null),sections=ref(blankSections()),preview=ref(null),reviewed=ref(false),inspected=ref(false),error=ref(""),loading=ref(false),busy=ref(false),receipt=ref(null);
let stopped=false,generation=0;
const canRead=computed(()=>!stopped&&['admin','bidder'].includes(orgAccess.role)),canWrite=computed(()=>canRead.value&&orgAccess.role==='admin'),dirty=computed(()=>canWrite.value&&JSON.stringify(sections.value)!==JSON.stringify(blankSections()));
const revision=computed(()=>template.value?.detail.revision);
const backLink=computed(()=>({path:`/org/templates/${id}`,query:{...(revision.value?{revision:String(revision.value.revision)}:{}),...(validId(route.query.task)?{task:route.query.task}:{})}}));
function invalidate(){generation++;reader.cancel('preview');preview.value=null;reviewed.value=false;inspected.value=false;receipt.value=null;}
watch(sections,invalidate,{deep:true,flush:'sync'});
function clear(){stopped=true;generation++;reader.stop();template.value=null;rows.value=[];meta.value=null;selected.value=null;sections.value=blankSections();preview.value=null;reviewed.value=false;inspected.value=false;receipt.value=null;loading.value=false;}
function deny(exc){clear();error.value=errorText(exc);}
async function resolveRevision(){
 if(!validId(id)||!validId(revisionId))throw new ApiError(400,'invalid_input','模板或修订 ID 无效');
 const number=route.query.revision===undefined?null:Number(route.query.revision);
 if(number!==null&&(!Number.isInteger(number)||number<1))throw new ApiError(400,'invalid_input','模板修订无效');
 const detail=checkTemplateDetail((await reader.read('template','GET',`${templatePath}/${id}?revision_id=${revisionId}`)).data,id,number);
 if(detail.detail.revision.id!==revisionId)throw new ApiError(502,'invalid_response','模板修订范围不符合契约');
 return detail;
}
async function load(next=null){if(!canRead.value||!template.value)return;loading.value=true;rows.value=[];meta.value=null;selected.value=null;error.value='';cursor.value=next;
 try{const result=checkBindingPage(await reader.read('list','POST','/management/export-bindings/query',{template_revision_id:revisionId,cursor:next,limit:25}),revisionId,revision.value.file.sha256);if(stopped)return;rows.value=result.items;meta.value=result.data;}
 catch(exc){if(exc.name!=='AbortError'){error.value=errorText(exc);if([401,403,404].includes(exc.status))deny(exc);if(['management_cursor_invalid','management_cursor_expired'].includes(exc.code)){cursor.value=null;previous.value=[];}}}finally{loading.value=false;}}
async function show(row){selected.value=null;try{const result=await reader.read('binding','GET',`/management/export-bindings/${row.id}?template_revision_id=${revisionId}`);const checked=checkBinding(result.data,revisionId,revision.value.file.sha256,row.id);if(!stopped)selected.value=checked;}catch(exc){if(exc.name!=='AbortError'){error.value=errorText(exc);if([401,403,404].includes(exc.status))deny(exc);}}}
function nextPage(){if(!meta.value?.has_more)return;rememberTemplateCursor(previous.value,cursor.value);load(meta.value.next_cursor);}
function back(){if(previous.value.length)load(previous.value.pop());}
async function original(){if(!revision.value)return;const run=generation;inspected.value=false;reviewed.value=false;
 try{await downloadTemplateOriginal(reader,revisionId,revision.value.file);if(!stopped&&run===generation)inspected.value=true;}catch(exc){if(exc.name!=='AbortError'){error.value=errorText(exc);if([401,403,404].includes(exc.status))deny(exc);}}}
async function previewBinding(){if(!canWrite.value||!validSections(sections.value)||busy.value)return;invalidate();const run=generation;busy.value=true;error.value='';const input=JSON.parse(JSON.stringify(sections.value));
 try{const result=await reader.read('preview','POST','/export-template-bindings',{template_revision_id:revisionId,expected_template_sha256:revision.value.file.sha256,sections:input,dry_run:true});const value=checkBindingPreview(result.data,revisionId,revision.value.file.sha256);if(!stopped&&run===generation)preview.value=value;}
 catch(exc){if(exc.name!=='AbortError'){error.value=errorText(exc);if([401,403,404].includes(exc.status))deny(exc);}}finally{busy.value=false;}}
const canCreate=computed(()=>canWrite.value&&preview.value&&inspected.value&&reviewed.value&&preview.value.issues.every(issue=>issue.severity!=='block')&&preview.value.anchors.length===6&&validSections(sections.value));
async function create(){if(!canCreate.value||busy.value)return;const run=generation,review=preview.value;busy.value=true;error.value='';
 try{const result=await orgRequest('POST','/export-template-bindings',{template_revision_id:revisionId,expected_template_sha256:review.template_sha256,expected_static_content_hash:review.static_content_hash,sections:JSON.parse(JSON.stringify(sections.value)),dry_run:false},{contractVersion:4});const value=checkBinding(result.data,revisionId,review.template_sha256);if(value.static_content_hash!==review.static_content_hash)throw new ApiError(502,'invalid_response','创建回执静态内容哈希不匹配');if(stopped||run!==generation)return;preview.value=null;reviewed.value=false;inspected.value=false;sections.value=blankSections();receipt.value=value;previous.value=[];await load();}
 catch(exc){if(exc.name!=='AbortError'){if([401,403,404].includes(exc.status))deny(exc);else{preview.value=null;reviewed.value=false;inspected.value=false;}error.value=['export_static_hash_conflict','export_template_hash_conflict'].includes(exc.code)?'模板或静态内容哈希已变化，请重新预览并核对原件后再创建绑定。':!exc.status?'创建结果尚未确定，请主动重新读取绑定记录后核对；不会自动重试。':errorText(exc);}}finally{busy.value=false;}}
watch(()=>orgAccess.role,(role,old)=>{if(old&&old!==role){clear();error.value='权限已改变，绑定配置与审阅已清除，请重新打开页面';}});
const canLeave=async()=>!dirty.value||await confirmAction('放弃未保存的绑定配置和静态内容审阅？','放弃未保存编辑','放弃编辑');
onBeforeRouteLeave(canLeave);onBeforeRouteUpdate(canLeave);
function reset(){clear();error.value='单位会话已改变，请重新打开模板绑定';}
function unload(event){if(dirty.value){event.preventDefault();event.returnValue='';}}
onMounted(async()=>{window.addEventListener('bid:org-reset',reset);window.addEventListener('beforeunload',unload);if(!canRead.value){error.value='只有单位管理员和标书专员可查看绑定';return;}loading.value=true;try{template.value=await resolveRevision();if(!stopped)await load();}catch(exc){if(exc.name!=='AbortError')deny(exc);}finally{loading.value=false;}});
onUnmounted(()=>{clear();window.removeEventListener('bid:org-reset',reset);window.removeEventListener('beforeunload',unload);});
</script>
<template>
 <nav class="breadcrumb" aria-label="位置"><RouterLink :to="backLink">模板详情</RouterLink><span>/</span><span>精确修订绑定</span></nav>
 <div class="page-header"><div><h2>模板绑定</h2><p class="subtitle">单位管理员创建不可变绑定；标书专员查看已有绑定。新修订需要独立审阅；绑定不确认材料或批准任务导出。</p></div></div>
 <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" class="section"/>
 <p v-if="loading" role="status">正在读取模板绑定…</p>
 <template v-if="template&&canRead">
  <p>{{revision.data.name}} · 内容修订 {{revision.revision}} · 原件 {{revision.file.name}}</p><p class="mono">修订 ID：{{revisionId}}</p>
  <el-alert v-if="receipt" :title="`已创建绑定 · 绑定 ID ${receipt.id} · 静态内容哈希 ${receipt.static_content_hash}`" type="success" :closable="false" role="status" class="section"/>
  <el-card shadow="never" class="section"><template #header><h3>已有绑定与审阅记录</h3></template><el-empty v-if="meta&&!rows.length" description="本修订尚无绑定，管理员可先预览与核对原件"/><el-table :data="rows" aria-label="绑定列表"><el-table-column label="绑定 / 适用性" min-width="220"><template #default="{row}"><el-button link type="primary" @click="show(row)">{{row.id}}</el-button><p>{{row.current?'当前四列表格绑定':'旧版绑定不能用于导出'}}</p></template></el-table-column><el-table-column label="审阅人 / 时间" min-width="220"><template #default="{row}">{{row.reviewed_by}} · {{formatTime(row.reviewed_at)}}</template></el-table-column></el-table><div class="actions"><el-button :disabled="loading" @click="previous=[];load()">重新读取绑定</el-button><el-button :disabled="loading||!previous.length" @click="back">上一页绑定</el-button><el-button :disabled="loading||!meta?.has_more" @click="nextPage">下一页绑定</el-button></div><template v-if="selected"><p class="mono">模板 SHA-256：{{selected.template_sha256}}</p><p class="mono">静态内容哈希：{{selected.static_content_hash}}</p><p class="mono">绑定哈希：{{selected.binding_hash}}</p><pre>{{JSON.stringify(selected.sections,null,2)}}</pre></template></el-card>
  <el-card v-if="canWrite" shadow="never" class="section"><template #header><h3>新建六节绑定</h3></template><p class="hint">原件需包含按以下顺序排列的六个独立锚点段落，声明章节须对应原件标题。使用原件中真实存在的标题和表格样式 ID；前三节固定四列且宽度合计 100%，其余节不配置列。</p><el-form label-position="top" @submit.prevent="previewBinding"><fieldset v-for="(section,index) in sections" :key="section.section" class="section"><legend>{{sectionLabels[section.section]}}（{{section.section}}）</legend><p class="hint mono">原件独立段落锚点：<code v-text="'{{bid.'+section.section+'}}'" /></p><div class="grid"><el-form-item label="标题样式 ID"><el-input v-model="section.heading_style_id" :aria-label="`${section.section} 标题样式 ID`" maxlength="200" :disabled="busy"/></el-form-item><el-form-item label="表格样式 ID"><el-input v-model="section.table_style_id" :aria-label="`${section.section} 表格样式 ID`" maxlength="200" :disabled="busy"/></el-form-item></div><div v-if="index<3" class="binding-columns"><el-form-item v-for="column in section.columns" :key="column.key" :label="`${columnLabels[column.key]}宽度（%）`"><el-input v-model="column.width_percent" :aria-label="`${section.section} ${column.key} 宽度`" type="number" min="0.01" max="100" step="0.01" :disabled="busy"/></el-form-item></div></fieldset><el-button type="primary" native-type="submit" :disabled="busy||!validSections(sections)" :loading="busy">预览绑定</el-button></el-form>
   <template v-if="preview"><h3>核对预览与原件静态内容</h3><dl><dt>模板 SHA-256</dt><dd class="mono">{{preview.template_sha256}}</dd><dt>静态内容哈希</dt><dd class="mono">{{preview.static_content_hash}}</dd><dt>适配器版本</dt><dd>{{preview.adapter_version}}</dd></dl><ul><li v-for="anchor in preview.anchors" :key="anchor.section">{{sectionLabels[anchor.section]}} · 原件段落 {{anchor.paragraph}} · 文档节 {{anchor.section_index}}</li></ul><el-alert v-for="issue in preview.issues" :key="issue.issue_id" :title="issue.code" :type="issue.severity==='block'?'error':'warning'" :closable="false"/><p class="hint">下载并打开本修订原件，逐项核对正文、页眉页脚、已有表格及其他静态内容。编辑配置后本次预览和勾选均失效。</p><el-button :disabled="busy" @click="original">下载原件核对静态内容</el-button><div class="section"><label class="check"><input v-model="reviewed" type="checkbox" :disabled="busy||!inspected"/>我已核对本修订原件中的全部静态内容</label></div><el-button type="primary" :disabled="busy||!canCreate" :loading="busy" @click="create">确认创建绑定</el-button></template>
  </el-card>
 </template>
</template>
<style scoped>fieldset{border:1px solid var(--el-border-color);border-radius:8px;padding:16px}legend{padding:0 6px}.binding-columns{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}pre{white-space:pre-wrap;overflow-wrap:anywhere}dd{margin:4px 0 16px;overflow-wrap:anywhere}@media(max-width:600px){.binding-columns{grid-template-columns:repeat(2,minmax(0,1fr))}}.check{display:flex;gap:8px;align-items:center}
</style>
