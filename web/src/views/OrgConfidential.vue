<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute } from "vue-router";
import { orgSession } from "../api.js";
import { confirmAction, errorText, formatTime, orgAccess, orgRequest } from "../org.js";
import { validId, rememberProductCursor } from "../products.js";
import { blankConfidentialField, checkConfidentialField, checkConfidentialPage, checkConfidentialReveal, checkConfidentialValue, confidentialAuthority, confidentialKinds as kinds, confidentialMaintainer, confidentialReader, confidentialScopes as scopes, submitConfidentialValue } from "../confidential-management.js";

const route = useRoute(), reader = confidentialReader();
const mode = ref("values"), search = ref(""), archived = ref(false), rows = ref([]), page = ref(null), cursor = ref(null), previous = ref([]);
const loading = ref(false), busy = ref(false), error = ref(""), notice = ref(""), access = ref(null), revoked = ref(false);
const creating = ref(false), form = ref(blankConfidentialField()), metadata = ref(null), labelDraft = ref(""), editing = ref(null), secret = ref(""), shown = ref(null), past = ref(null);
const taskId = computed(() => validId(route.query.task) ? route.query.task : null);
const writable = computed(() => !revoked.value && confidentialMaintainer());
const dirty = computed(() => creating.value && JSON.stringify(form.value) !== JSON.stringify(blankConfidentialField()) || metadata.value && labelDraft.value !== metadata.value.label || !!secret.value);
let generation = 0, detailGeneration = 0, timer = null, revealTimer = null, stopped = false;
function canSet(field) { return writable.value && !field.archived && (field.scope === "org" || !!taskId.value && access.value?.canPin); }
function canReveal(value) { return writable.value && value.status === "filled" && (value.scope === "org" || !!taskId.value && (!!access.value?.member?.active || orgAccess.role === "admin" && !!access.value?.workflow)); }
function clearReveal() { detailGeneration++; shown.value = null; clearTimeout(revealTimer); }
function closeSecret() { editing.value = null; secret.value = ""; }
function clearForms() { closeSecret(); clearReveal(); metadata.value = null; labelDraft.value = ""; creating.value = false; form.value = blankConfidentialField(); past.value = null; }
function deny(exc) {
  if ([401, 403, 404].includes(exc.status)) { revoked.value = true; generation++; reader.cancel("list"); reader.cancel("detail"); reader.cancel("history"); clearForms(); rows.value = []; page.value = null; access.value = null; loading.value = false; }
}
function safeFailure(exc) {
  const messages = { confidential_value_conflict: "当前值已变化，请核对最新版本并重新输入", revision_conflict: "字段修订已变化，请核对最新状态", confidential_field_archived: "字段已归档，不能填写", confidential_task_required: "需要明确的任务上下文", confidential_task_not_allowed: "全单位共用值不能附带任务", invalid_input: "填写内容不符合要求，请修正后重新输入", invalid_response: "保密数据范围或响应边界不符合契约" };
  // Never render a server message that could contain echoed secret input.
  return messages[exc.code] ?? (exc.status === 404 ? "不可访问" : exc.status === 403 ? "当前角色无权执行此操作" : exc.status === 401 ? "请重新登录单位" : "保存或查看结果未确定，请重新读取当前状态后主动操作");
}
async function authority() { const run = generation, value = await confidentialAuthority(reader, taskId.value); if (!stopped && run === generation) access.value = value; }
async function load(next = null) {
  if (stopped || revoked.value) return;
  const run = ++generation; reader.cancel("list"); loading.value = true; rows.value = []; page.value = null; cursor.value = next; error.value = "";
  try {
    if (route.query.task && !taskId.value) throw new Error("任务 ID 无效");
    await authority(); if (stopped || run !== generation) return;
    const result = checkConfidentialPage(await reader.read("list", "POST", `/management/confidential-${mode.value === "fields" ? "fields" : "values"}/query`, { limit: 25, cursor: next, q: search.value.trim() || null, task_id: taskId.value, archived: archived.value }), mode.value, taskId.value);
    if (stopped || run !== generation) return; rows.value = result.items; page.value = result.data;
  } catch (exc) { if (run === generation && exc.name !== "AbortError") { deny(exc); error.value = errorText(exc); if (["management_cursor_invalid", "management_cursor_expired"].includes(exc.code)) { cursor.value = null; previous.value = []; } } }
  finally { if (run === generation) loading.value = false; }
}
function resetList() { previous.value = []; return load(); }
watch([search, archived, mode], () => {
  if (stopped) return; generation++; reader.cancel("list"); rows.value = []; page.value = null; loading.value = true; previous.value = []; clearTimeout(timer); timer = setTimeout(resetList, 300);
});
function next() { if (page.value?.has_more) { rememberProductCursor(previous.value, cursor.value); load(page.value.next_cursor); } }
function back() { if (previous.value.length) load(previous.value.pop()); }
async function exactField(fieldId, key) {
  const result = checkConfidentialPage(await reader.read("detail", "POST", "/management/confidential-fields/query", { limit: 25, cursor: null, q: null, field_id: fieldId, task_id: taskId.value, archived: true }), "fields", taskId.value);
  const field = result.items.find(item => item.id === fieldId && item.key === key); if (!field) throw Object.assign(new Error("不可访问"), { status: 404 }); return field;
}
async function exactValue(field) {
  if (field.scope === "task" && !taskId.value) return null;
  const result = checkConfidentialPage(await reader.read("detail", "POST", "/management/confidential-values/query", { limit: 25, cursor: null, q: null, field_id: field.id, task_id: taskId.value, archived: true }), "values", taskId.value);
  const value = result.items.find(item => item.field_id === field.id); if (!value) throw Object.assign(new Error("不可访问"), { status: 404 }); return checkConfidentialValue(value, taskId.value, field.id, field);
}
async function openSet(row) {
  if (busy.value) return; busy.value = true; error.value = ""; notice.value = ""; clearReveal(); const run = detailGeneration;
  try { await authority(); const field = mode.value === "fields" ? await exactField(row.id, row.key) : await exactField(row.field_id, row.key); const value = await exactValue(field);
    if (run !== detailGeneration || stopped) return;
    if (!canSet(field)) { error.value = "填写需要单位管理员或商务审核；任务值还需要活跃任务负责人或协作者身份，且字段必须未归档。"; return; }
    editing.value = { field, value }; secret.value = "";
  } catch (exc) { if (exc.name !== "AbortError") { deny(exc); error.value = safeFailure(exc); } } finally { busy.value = false; }
}
async function saveValue() {
  if (busy.value || !editing.value || !secret.value.trim() || !canSet(editing.value.field)) return;
  busy.value = true; error.value = ""; const target = editing.value;
  try {
    await authority(); if (editing.value !== target || !canSet(target.field)) { closeSecret(); error.value = "权限已变化，请重新打开填写窗口"; return; }
    const request = submitConfidentialValue(target.field.id, secret.value, target.field.scope === "task" ? taskId.value : null, target.field.revision, target.value?.value_id ?? null);
    secret.value = "";
    const result = await request; checkConfidentialValue(result.data, taskId.value, target.field.id, target.field);
    if (result.data.status !== "filled" || result.data.value_id === target.value?.value_id || result.data.version !== (target.value?.version ?? 0) + 1) throw new Error("保密写入回执不符合契约");
    if (editing.value !== target || stopped) return;
    closeSecret(); notice.value = `已保存保密值版本 ${result.data.version}；回执仅包含脱敏元数据`; await resetList();
  } catch (exc) {
    secret.value = "";
    if (exc.name !== "AbortError") {
      deny(exc);
      if (["revision_conflict", "confidential_value_conflict"].includes(exc.code) && !revoked.value) {
        try { const field = await exactField(target.field.id, target.field.key), value = await exactValue(field); if (editing.value === target) editing.value = { field, value }; await resetList(); } catch (readError) { deny(readError); closeSecret(); }
        error.value = "字段或当前值已变化；输入已清空。已重新读取，请核对后重新输入并主动保存。";
      } else { closeSecret(); if (!revoked.value) await resetList(); error.value = safeFailure(exc); }
    }
  } finally { secret.value = ""; busy.value = false; }
}
async function reveal(value) {
  if (busy.value || !canReveal(value)) return; clearReveal(); const run = detailGeneration; busy.value = true; error.value = "";
  try { await authority(); if (run !== detailGeneration || !canReveal(value)) return;
    const result = await orgRequest("POST", `/confidential-values/${value.value_id}/reveal`, undefined, { contractVersion: 4 });
    if (run !== detailGeneration || stopped || document.hidden) return; shown.value = checkConfidentialReveal(result.data, value); revealTimer = setTimeout(clearReveal, 30000);
  } catch (exc) { if (exc.name !== "AbortError") { deny(exc); error.value = safeFailure(exc); } } finally { busy.value = false; }
}
async function history(row, next = null) {
  clearReveal(); const fieldId = row.field_id ?? row.id;
  if (row.scope === "task" && !taskId.value) return;
  let selected = past.value?.field.field_id === fieldId || past.value?.field.id === fieldId ? past.value : { field: row, items: [], page: null, cursor: null, previous: [] };
  past.value = selected; selected = past.value; selected.items = []; selected.page = null; selected.cursor = next; const run = detailGeneration;
  try { const result = checkConfidentialPage(await reader.read("history", "POST", `/management/confidential-fields/${fieldId}/values/history/query`, { limit: 25, cursor: next, task_id: row.scope === "task" ? taskId.value : null }), "history", row.scope === "task" ? taskId.value : null, fieldId);
    if (run !== detailGeneration || past.value !== selected || stopped) return; past.value = { ...selected, items: result.items, page: result.data };
  } catch (exc) { if (exc.name !== "AbortError") { deny(exc); error.value = errorText(exc); } }
}
function closeHistory() { clearReveal(); reader.cancel("history"); past.value = null; }
function nextHistory() { const current = past.value; rememberProductCursor(current.previous, current.cursor); history(current.field, current.page.next_cursor); }
function backHistory() { const current = past.value; history(current.field, current.previous.pop()); }
async function openMetadata(row) { if (busy.value || !writable.value) return; busy.value = true; error.value = ""; clearReveal(); try { await authority(); if (writable.value) { metadata.value = await exactField(row.id, row.key); labelDraft.value = metadata.value.label; } } catch (exc) { deny(exc); error.value = errorText(exc); } finally { busy.value = false; } }
async function saveMetadata(archive = null) {
  if (!metadata.value || !writable.value || busy.value) return;
  const target = metadata.value;
  if (archive === true && !await confirmAction("归档后，该字段不能用于新的占位符；引用它的卡片确认、起草和导出可能被阻止。确定归档？", "归档保密字段", "确认归档")) return;
  busy.value = true; error.value = "";
  try { await authority(); if (!writable.value || metadata.value !== target) return;
    const body = { expected_revision: target.revision, ...(archive === null ? { label: labelDraft.value.trim() } : { archived: archive }) };
    const result = await orgRequest("POST", `/confidential-fields/${target.id}/revisions`, body, { contractVersion: 4 }); checkConfidentialField(result.data, target.id);
    metadata.value = null; labelDraft.value = ""; notice.value = `字段元数据已保存 · 并发修订 ${result.data.revision}`; await resetList();
  } catch (exc) { deny(exc); if (exc.code === "revision_conflict" && !revoked.value) { try { metadata.value = await exactField(target.id, target.key); await resetList(); } catch (readError) { deny(readError); } error.value = "字段已变化；未保存名称已保留，请核对最新状态后主动保存。"; } else error.value = errorText(exc); }
  finally { busy.value = false; }
}
async function create() {
  if (!writable.value || busy.value) return; busy.value = true; error.value = "";
  try { await authority(); if (!writable.value || !creating.value) return;
    const result = await orgRequest("POST", "/confidential-fields", { ...form.value, label: form.value.label.trim() }, { contractVersion: 4 }); checkConfidentialField(result.data);
    creating.value = false; form.value = blankConfidentialField(); mode.value = "fields"; notice.value = "已登记保密字段；键名、类别和范围固定"; await resetList();
  } catch (exc) { deny(exc); error.value = errorText(exc); } finally { busy.value = false; }
}
function clear() { stopped = true; generation++; detailGeneration++; clearTimeout(timer); reader.stop(); clearForms(); rows.value = []; page.value = null; previous.value = []; search.value = ""; notice.value = ""; loading.value = false; error.value = "单位会话已改变，请重新打开保密字段页"; }
function blur() { clearReveal(); }
function visibility() { if (document.hidden) clearReveal(); }
const canLeave = async () => { clearReveal(); return !dirty.value || await confirmAction("放弃未保存的字段内容或保密值？", "放弃未保存编辑", "放弃编辑"); };
onBeforeRouteLeave(canLeave);
onBeforeRouteUpdate(async () => { if (!await canLeave()) return false; generation++; clearForms(); return true; });
watch(() => route.query.task, () => { access.value = null; resetList(); });
watch(() => orgAccess.role, role => { if (role && !["admin", "bidder"].includes(role)) clearForms(); });
function unload(event) { clearReveal(); if (dirty.value) { event.preventDefault(); event.returnValue = ""; } }
onMounted(() => { load(); window.addEventListener("bid:org-reset", clear); window.addEventListener("blur", blur); window.addEventListener("beforeunload", unload); document.addEventListener("visibilitychange", visibility); });
onUnmounted(() => { clear(); window.removeEventListener("bid:org-reset", clear); window.removeEventListener("blur", blur); window.removeEventListener("beforeunload", unload); document.removeEventListener("visibilitychange", visibility); });
</script>
<template>
  <div class="page-header"><div><h2>保密字段</h2><p class="subtitle">单位管理员或商务审核维护。模型只接收占位符，导出时填入登记的值。</p></div><el-button v-if="writable" :disabled="busy" @click="creating = true; clearReveal()">登记字段</el-button></div>
  <p class="section" data-testid="confidential-scope">{{ taskId ? `当前任务：${taskId}；显示全单位共用值与此任务的值。字段定义仍属于全单位。` : '当前范围：全单位共用值；任务值需从明确的任务上下文打开。' }}</p>
  <p v-if="taskId && !access?.canPin" class="hint">任务归档、非成员、审阅者或观察者不能填写任务值；需要活跃任务负责人或协作者。</p>
  <p v-if="!writable" class="hint">当前角色只能读取脱敏信息；维护和查看明文需要单位管理员或商务审核本人登录。</p>
  <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" class="section" />
  <el-alert v-if="notice" :title="notice" type="success" :closable="false" role="status" class="section" />
  <el-card shadow="never" class="section">
    <div class="actions"><el-button :type="mode === 'values' ? 'primary' : 'default'" @click="mode = 'values'">当前脱敏值</el-button><el-button :type="mode === 'fields' ? 'primary' : 'default'" @click="mode = 'fields'">字段定义</el-button><div class="search"><label for="confidential-search">搜索保密字段</label><el-input id="confidential-search" v-model="search" maxlength="200" aria-label="搜索保密字段" placeholder="键名、名称的前缀词" clearable /></div><label><input v-model="archived" type="checkbox" aria-label="包含归档字段" /> 包含归档字段</label><el-button :disabled="loading || revoked" @click="resetList">重新读取保密字段</el-button></div>
    <p v-if="loading" role="status">正在读取保密字段…</p>
    <div v-show="!loading && rows.length" class="table-scroll"><table class="data-table" aria-label="保密字段列表"><thead><tr><th>名称 / 占位符</th><th>类别 / 范围</th><th>{{ mode === 'fields' ? '字段状态' : '当前值' }}</th><th>下一步</th></tr></thead><tbody><tr v-for="row in rows" :key="row.id ?? row.field_id"><td><strong>{{ row.label }}</strong><p><code>{{ row.placeholder }}</code></p></td><td><span>{{ kinds[row.kind] }}</span><p class="hint">{{ scopes[row.scope] }}</p></td><td v-if="mode === 'fields'"><span>{{ row.archived ? '已归档' : '可用' }}</span><p class="hint">元数据并发修订 {{ row.revision }}；没有字段元数据历史</p></td><td v-else><span>{{ row.status === 'filled' ? `已填 · 值版本 ${row.version}` : '未填' }}</span><p v-if="row.tail" class="hint">末 4 位 {{ row.tail }}</p><p v-if="row.set_at" class="hint">{{ formatTime(row.set_at) }} · 填写者 {{ row.set_by ?? '未知' }}</p></td><td><div class="row-actions"><el-button v-if="writable && (mode === 'fields' ? canSet(row) : row.scope === 'org' || access?.canPin)" link :disabled="busy" @click="openSet(row)">填写新值</el-button><el-button v-if="mode === 'values' && canReveal(row)" link :disabled="busy" @click="reveal(row)">查看明文</el-button><el-button v-if="row.scope === 'org' || taskId" link :disabled="busy" @click="history(row)">值历史</el-button><el-button v-if="mode === 'fields' && writable" link :disabled="busy" @click="openMetadata(row)">修改字段</el-button></div></td></tr></tbody></table></div>
    <el-empty v-if="page && !loading && !error && !rows.length" description="没有符合条件的保密字段" />
    <div class="actions"><el-button :disabled="loading || !previous.length" @click="back">上一页保密字段</el-button><el-button :disabled="loading || !page?.has_more" @click="next">下一页保密字段</el-button><span v-if="page" class="hint">本页 {{ page.returned }} 项 · {{ formatTime(page.as_of) }}；每页反映读取时的状态。</span></div>
  </el-card>
  <el-dialog :model-value="creating" title="登记保密字段" width="min(520px, 94vw)" destroy-on-close @close="creating = false; form = blankConfidentialField()"><form class="editor" @submit.prevent="create"><label>字段名称<input v-model="form.label" aria-label="字段名称" maxlength="100" required /></label><label>字段键名<input v-model="form.key" aria-label="字段键名" pattern="[a-z][a-z0-9_]{1,47}" maxlength="48" required /></label><label>字段类别<select v-model="form.kind" aria-label="字段类别"><option v-for="(name, key) in kinds" :key="key" :value="key">{{ name }}</option></select></label><label>字段范围<select v-model="form.scope" aria-label="字段范围"><option v-for="(name, key) in scopes" :key="key" :value="key">{{ name }}</option></select></label><p class="hint">键名、类别和范围登记后固定，值通过独立的填写窗口输入。</p><div class="actions"><el-button @click="creating = false; form = blankConfidentialField()">取消登记</el-button><el-button native-type="submit" type="primary" :loading="busy" :disabled="!writable">保存字段定义</el-button></div></form></el-dialog>
  <el-dialog :model-value="!!metadata" title="修改保密字段" width="min(520px, 94vw)" destroy-on-close @close="metadata = null; labelDraft = ''"><template v-if="metadata"><p>{{ metadata.placeholder }} · {{ kinds[metadata.kind] }} · {{ scopes[metadata.scope] }}</p><p class="hint">元数据并发修订 {{ metadata.revision }}；该计数器不是可恢复的历史版本。</p><label class="editor">修改字段名称<input v-model="labelDraft" aria-label="修改字段名称" maxlength="100" /></label><p class="hint">归档会阻止新占位符使用，并可能阻止引用该字段的卡片确认、起草和导出。</p><div class="actions"><el-button :disabled="busy || !writable || !labelDraft.trim()" @click="saveMetadata()">保存字段名称</el-button><el-button :disabled="busy || !writable" @click="saveMetadata(!metadata.archived)">{{ metadata.archived ? '取消归档字段' : '归档字段' }}</el-button><el-button @click="metadata = null; labelDraft = ''">关闭字段编辑</el-button></div></template></el-dialog>
  <el-dialog :model-value="!!editing" title="填写新的保密值" width="min(520px, 94vw)" destroy-on-close @close="closeSecret"><template v-if="editing"><p>{{ editing.field.label }} · {{ scopes[editing.field.scope] }}</p><p class="hint">字段修订 {{ editing.field.revision }} · {{ editing.value?.value_id ? `当前值版本 ${editing.value.version}` : '该范围尚无值，本次明确校验空值' }}。每次填写保留历史；输入不会预填现有明文。</p><label class="editor">新的保密值<input v-model="secret" aria-label="新的保密值" type="password" autocomplete="off" maxlength="2000" /></label><p class="hint">提交后立即清空输入；冲突需要核对最新版本并重新填写。</p></template><template #footer><el-button @click="closeSecret">取消填写</el-button><el-button type="primary" :loading="busy" :disabled="!secret.trim() || !editing || !canSet(editing.field)" @click="saveValue">保存新值</el-button></template></el-dialog>
  <el-dialog :model-value="!!shown" title="查看保密值" width="min(520px, 94vw)" destroy-on-close @close="clearReveal"><p class="hint">此次查看已记入审计，30 秒后自动隐藏。关闭、窗口失焦、页面隐藏或导航也会清空。</p><p data-testid="revealed-confidential-value" class="revealed">{{ shown?.value }}</p><template #footer><el-button @click="clearReveal">关闭明文</el-button></template></el-dialog>
  <el-dialog :model-value="!!past" title="保密值历史" width="min(640px, 94vw)" destroy-on-close @close="closeHistory"><template v-if="past"><p>{{ past.field.label }} · {{ scopes[past.field.scope] }}{{ past.field.scope === 'task' ? ` · ${taskId}` : '' }}</p><p class="hint">只显示值版本和脱敏信息；字段名称不是历史标签。</p><div class="table-scroll"><table class="data-table" aria-label="脱敏值历史"><thead><tr><th>值版本</th><th>脱敏尾号</th><th>时间 / 填写者</th></tr></thead><tbody><tr v-for="item in past.items" :key="item.value_id"><td>{{ item.version }}</td><td>{{ item.tail ?? '无尾号' }}</td><td>{{ formatTime(item.set_at) }} · {{ item.set_by ?? '未知' }}</td></tr></tbody></table></div><p v-if="past.page && !past.items.length">暂无保密值历史</p><div class="actions"><el-button :disabled="!past.page || !past.previous.length" @click="backHistory">上一页值历史</el-button><el-button :disabled="!past.page?.has_more" @click="nextHistory">下一页值历史</el-button><el-button @click="closeHistory">关闭值历史</el-button></div></template></el-dialog>
</template>
<style scoped>
.search{width:min(320px,100%)}.search label{display:block;margin-bottom:4px}.editor{display:grid;gap:14px}.editor label{display:grid;gap:6px}.editor input,.editor select{box-sizing:border-box;width:100%;padding:9px;border:1px solid var(--el-border-color);border-radius:4px;background:var(--el-bg-color);color:var(--el-text-color-primary)}.editor input:focus,.editor select:focus{outline:2px solid var(--el-color-primary)}.revealed{white-space:pre-wrap;overflow-wrap:anywhere;font-family:monospace;font-size:18px}.row-actions{flex-wrap:wrap}code{overflow-wrap:anywhere}td{vertical-align:top}td p{margin:5px 0}.actions{flex-wrap:wrap}
</style>
