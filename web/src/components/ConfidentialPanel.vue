<script setup>
import { computed, onMounted, ref } from "vue";
import { confirmAction, errorText, formatTime, label, orgAccess, orgRequest } from "../org.js";
// Without a task this manages the org's fields and org-wide values; with one it fills the
// task's own values. Values are only ever typed in or revealed on request, never listed.
const props = defineProps({ taskId: { type: String, default: null } });
const kinds = { amount: "金额", contact: "联系人 / 电话", identity: "证件号", bank_account: "银行账号", other: "其他" };
const scopes = { org: "全单位共用", task: "每个任务单独填写" };
const fields = ref([]), values = ref([]), error = ref(""), busy = ref(false);
const editing = ref(null), draft = ref(""), shown = ref(null), past = ref(null);
const creating = ref({ key: "", label: "", kind: "amount", scope: "task" });
const writable = computed(() => ["admin", "bidder"].includes(orgAccess.role));
const rows = computed(() => {
  const byField = new Map(values.value.map((item) => [item.field_id, item]));
  return fields.value.map((field) => ({ field, value: byField.get(field.id) ?? null }));
});
const missing = computed(() => values.value.filter((item) => item.status === "missing").length);
async function load() {
  error.value = "";
  try {
    const query = props.taskId ? `?task_id=${props.taskId}` : "";
    const [listed, state] = await Promise.all([orgRequest("GET", "/confidential-fields"), orgRequest("GET", `/confidential-values${query}`)]);
    fields.value = listed.items; values.value = state.items;
  } catch (exc) { error.value = errorText(exc); }
}
function canSet(field) { return writable.value && (props.taskId ? true : field.scope === "org"); }
function openSet(field) { editing.value = field; draft.value = ""; }
async function save() {
  busy.value = true; error.value = "";
  try {
    const body = { value: draft.value, ...(editing.value.scope === "task" ? { task_id: props.taskId } : {}) };
    await orgRequest("POST", `/confidential-fields/${editing.value.id}/values`, body);
    editing.value = null; draft.value = ""; await load();
  } catch (exc) { error.value = errorText(exc); } finally { busy.value = false; }
}
async function reveal(value) {
  error.value = "";
  try {
    shown.value = (await orgRequest("POST", `/confidential-values/${value.value_id}/reveal`)).data;
    // The plain value is kept only briefly in memory.
    setTimeout(() => { if (shown.value?.value_id === value.value_id) shown.value = null; }, 30000);
  } catch (exc) { error.value = errorText(exc); }
}
async function history(field) {
  error.value = "";
  try {
    const query = field.scope === "task" && props.taskId ? `?task_id=${props.taskId}` : "";
    past.value = { field, items: (await orgRequest("GET", `/confidential-fields/${field.id}/values${query}`)).items };
  } catch (exc) { error.value = errorText(exc); }
}
async function create() {
  busy.value = true; error.value = "";
  try {
    await orgRequest("POST", "/confidential-fields", creating.value);
    creating.value = { key: "", label: "", kind: "amount", scope: "task" }; await load();
  } catch (exc) { error.value = errorText(exc); } finally { busy.value = false; }
}
async function archive(field) {
  if (!(await confirmAction(`归档后，引用 {{secret.${field.key}}} 的卡片无法确认，导出也会被阻止。确定归档“${field.label}”？`))) return;
  try {
    await orgRequest("POST", `/confidential-fields/${field.id}/revisions`, { expected_revision: field.revision, archived: true });
    await load();
  } catch (exc) { error.value = errorText(exc); }
}
onMounted(load);
</script>
<template>
  <el-card class="section" shadow="never" body-class="flush" data-testid="confidential-panel">
    <template #header>
      <div class="section-title">
        <h3>{{ taskId ? "报价与保密信息" : "保密字段" }}</h3>
        <span class="hint">模型只看到 <code v-pre>{{secret.键名}}</code> 占位符，导出时填入这里登记的值</span>
        <el-tag v-if="taskId && missing" type="warning" size="small">{{ missing }} 项未填</el-tag>
      </div>
    </template>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" class="inset" />
    <div class="table-scroll flat">
      <table class="data-table">
        <thead><tr><th>名称</th><th>占位符</th><th>类别</th><th>范围</th><th>当前值</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="{ field, value } in rows" :key="field.id">
            <td>{{ field.label }}</td>
            <td><code>{{ field.placeholder }}</code></td>
            <td>{{ label(kinds, field.kind) }}</td>
            <td>{{ label(scopes, field.scope) }}</td>
            <td>
              <template v-if="value?.status === 'filled'">
                <span class="tag success">已填</span>
                <span v-if="value.tail" class="hint"> 末 4 位 {{ value.tail }}</span>
                <div class="hint">{{ formatTime(value.set_at) }}</div>
              </template>
              <span v-else-if="value" class="tag warning">未填</span>
              <span v-else class="hint">按任务填写</span>
            </td>
            <td>
              <div class="row-actions">
                <el-button v-if="canSet(field)" link type="primary" @click="openSet(field)">{{ value?.status === "filled" ? "更新" : "填写" }}</el-button>
                <el-button v-if="writable && value?.status === 'filled'" link @click="reveal(value)">查看</el-button>
                <el-button v-if="value" link @click="history(field)">历史</el-button>
                <el-button v-if="writable && !taskId" link type="danger" @click="archive(field)">归档</el-button>
              </div>
            </td>
          </tr>
          <tr v-if="!rows.length"><td colspan="6" class="empty">还没有保密字段。{{ taskId ? "到“保密字段”页登记投标总价、联系人等字段。" : "在下方登记投标总价、联系人、银行账号等字段。" }}</td></tr>
        </tbody>
      </table>
    </div>
    <el-form v-if="writable && !taskId" class="create inset" inline @submit.prevent="create">
      <el-form-item label="名称"><el-input v-model="creating.label" placeholder="投标总价" /></el-form-item>
      <el-form-item label="键名"><el-input v-model="creating.key" placeholder="bid_total" /></el-form-item>
      <el-form-item label="类别"><el-select v-model="creating.kind" style="width: 140px"><el-option v-for="(text, key) in kinds" :key="key" :label="text" :value="key" /></el-select></el-form-item>
      <el-form-item label="范围"><el-select v-model="creating.scope" style="width: 170px"><el-option v-for="(text, key) in scopes" :key="key" :label="text" :value="key" /></el-select></el-form-item>
      <el-button type="primary" native-type="submit" :loading="busy" :disabled="!creating.label || !creating.key">登记字段</el-button>
    </el-form>
  </el-card>
  <el-dialog :model-value="!!editing" :title="`填写：${editing?.label ?? ''}`" width="440px" @close="editing = null">
    <p class="hint">值加密保存，不发给模型，只在导出文件中出现。每次填写都保留历史版本。</p>
    <el-input v-model="draft" type="password" show-password autocomplete="off" name="confidential-value" />
    <template #footer><el-button @click="editing = null">取消</el-button><el-button type="primary" :loading="busy" :disabled="!draft.trim()" @click="save">保存</el-button></template>
  </el-dialog>
  <el-dialog :model-value="!!shown" title="查看保密值" width="440px" @close="shown = null">
    <p class="hint">此次查看已记入审计，30 秒后自动隐藏。</p>
    <p class="revealed">{{ shown?.value }}</p>
  </el-dialog>
  <el-dialog :model-value="!!past" :title="`历史：${past?.field.label ?? ''}`" width="520px" @close="past = null">
    <table class="data-table">
      <thead><tr><th>版本</th><th>末 4 位</th><th>时间</th></tr></thead>
      <tbody><tr v-for="item in past?.items ?? []" :key="item.value_id"><td>{{ item.version }}</td><td>{{ item.tail ?? "—" }}</td><td>{{ formatTime(item.set_at) }}</td></tr></tbody>
    </table>
  </el-dialog>
</template>
<style scoped>
.inset { margin: 12px 16px; }
.create { padding-top: 4px; }
.flat { border: none; border-radius: 0; }
:deep(.flush) { padding: 0; }
.revealed { font-family: var(--el-font-family-mono, monospace); font-size: 18px; word-break: break-all; }
code { font-size: 12px; white-space: nowrap; }
td:first-child, .row-actions { white-space: nowrap; }
h3 { margin: 0; }
</style>
