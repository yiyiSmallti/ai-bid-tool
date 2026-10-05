<script setup>
import { computed, onMounted, ref } from "vue";
import SecretTextEditor from "../components/SecretTextEditor.vue";
import { errorText, orgAccess, orgRequest, warningText } from "../org.js";
// Organization declarations. Long text fields take confidential fields as blocks, so a
// bank account or contact is never typed into text that drafting sends to the model.
const TEXTS = [
  { key: "registration_details", label: "注册信息", rows: 3, max: 10000 },
  { key: "performance_summary", label: "业绩概述", rows: 4, max: 20000 },
  { key: "standard_wording", label: "标准表述", rows: 6, max: 20000 },
];
const profiles = ref([]), fields = ref([]), selected = ref(null), form = ref(blank());
const error = ref(""), notice = ref(""), warnings = ref([]), busy = ref(false);
const writable = computed(() => ["admin", "bidder"].includes(orgAccess.role));
function blank() { return { name: "", registration_details: "", performance_summary: "", standard_wording: "" }; }
function open(entry) {
  selected.value = entry; notice.value = "";
  form.value = entry ? { ...blank(), ...Object.fromEntries(Object.entries(entry.data).map(([k, v]) => [k, v ?? ""])) } : blank();
}
async function load() {
  error.value = "";
  try {
    const [listed, secret] = await Promise.all([orgRequest("GET", "/resources/profiles"), orgRequest("GET", "/confidential-fields")]);
    profiles.value = listed.items; fields.value = secret.items; warnings.value = listed.warnings;
    open(profiles.value.find((item) => item.profile_id === selected.value?.profile_id) ?? profiles.value[0] ?? null);
  } catch (exc) { error.value = errorText(exc); }
}
async function save() {
  busy.value = true; error.value = ""; notice.value = "";
  // Unknown text stays null rather than an empty declaration.
  const data = Object.fromEntries(Object.entries(form.value).map(([k, v]) => [k, v.trim() ? v : null]));
  try {
    const result = selected.value
      ? await orgRequest("POST", `/resources/profiles/${selected.value.profile_id}/revisions`, { expected_revision: selected.value.revision, data })
      : await orgRequest("POST", "/resources/profiles", { data });
    selected.value = result.data; notice.value = `已保存为第 ${result.data.revision} 版；已选用旧版本的任务不受影响。`;
    await load();
  } catch (exc) { error.value = errorText(exc); } finally { busy.value = false; }
}
onMounted(load);
</script>
<template>
  <div class="page-header">
    <div>
      <h2>单位资料</h2>
      <p class="subtitle">单位名称、注册信息、业绩和标准表述。账号、联系人等从上方字段条拖入，起草时模型只看到字段名，导出时填入真实值。</p>
    </div>
    <el-button v-if="writable" @click="open(null)">新建资料</el-button>
  </div>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="section" />
  <el-alert v-for="item in warnings" :key="item" :title="warningText(item)" type="info" :closable="false" class="section" />
  <div class="layout">
    <el-card shadow="never" body-class="flush">
      <template #header><h3>资料</h3></template>
      <ul class="list">
        <li v-for="entry in profiles" :key="entry.profile_id"><button type="button" :class="{ active: entry.profile_id === selected?.profile_id }" @click="open(entry)">{{ entry.data.name }}<span class="hint"> · 第 {{ entry.revision }} 版</span></button></li>
        <li v-if="!profiles.length" class="empty">还没有单位资料。</li>
      </ul>
    </el-card>
    <el-card shadow="never">
      <template #header><h3>{{ selected ? `编辑：${selected.data.name}` : "新建资料" }}</h3></template>
      <el-form label-position="top" @submit.prevent="save">
        <el-form-item label="单位名称"><el-input v-model="form.name" maxlength="200" :disabled="!writable" /></el-form-item>
        <el-form-item v-for="text in TEXTS" :key="text.key" :label="text.label">
          <SecretTextEditor v-model="form[text.key]" :fields="fields" :disabled="!writable" :label="text.label" :rows="text.rows" :maxlength="text.max" />
        </el-form-item>
        <el-button v-if="writable" type="primary" native-type="submit" :loading="busy" :disabled="!form.name.trim()">{{ selected ? "保存为新版本" : "创建" }}</el-button>
        <span v-if="!fields.length" class="hint"> 还没有保密字段，可先到“保密字段”页登记。</span>
        <el-alert v-if="notice" :title="notice" type="success" show-icon :closable="false" role="status" class="top" />
      </el-form>
    </el-card>
  </div>
</template>
<style scoped>
.layout { display: grid; grid-template-columns: minmax(180px, 1fr) minmax(0, 3fr); gap: 16px; align-items: start; }
.list { list-style: none; margin: 0; padding: 4px 0; }
.list button { width: 100%; text-align: left; padding: 8px 16px; border: none; background: none; cursor: pointer; font: inherit; color: inherit; }
.list button.active { background: var(--el-color-primary-light-9); color: var(--el-color-primary); }
.empty { padding: 12px 16px; color: var(--el-text-color-secondary); }
.top { margin-top: 12px; }
:deep(.flush) { padding: 0; }
h3 { margin: 0; }
@media (max-width: 720px) { .layout { grid-template-columns: minmax(0, 1fr); } }
</style>
