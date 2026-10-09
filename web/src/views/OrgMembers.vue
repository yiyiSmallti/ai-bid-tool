<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { orgSession } from "../api.js";
import { formatTime, label, orgAccess, orgRequest, roles } from "../org.js";

const members = ref([]), selectedRoles = reactive({}), loading = ref(false), busy = ref(false);
const error = ref(""), formError = ref(""), status = ref(""), stale = ref(false);
const dialog = ref(""), target = ref(null), invitation = ref(null), copied = ref(false);
const form = reactive({ email: "", role: "viewer" });
const manage = computed(() => orgAccess.role === "admin");
const controller = new AbortController();
let mounted = true, generation = 0;
const messages = {
  member_exists: "此邮箱已是本单位成员，请在成员表中修改角色或启用。",
  revision_conflict: "成员修订已变化，修改未提交。请关闭对话框并重新读取，核对后再提交。",
  last_admin_required: "单位必须保留至少一位启用的管理员，不能降级或停用最后一位管理员。",
  password_already_set: "该成员已经设置密码，请直接进入单位登录页。",
  invalid_input: "邮箱或角色不符合要求，请核对后再提交。",
  forbidden: "当前账号无权管理单位成员，请重新读取或切换单位。",
  not_found: "该成员不可访问，请重新读取成员列表。",
  invalid_session: "登录已失效，请重新登录单位。",
};
const failureText = (exc) => messages[exc.code] ?? "操作结果无法核验，请重新读取成员列表。";
function memberView(row) {
  if (!row || typeof row.user_id !== "string" || typeof row.email !== "string" || !Object.hasOwn(roles, row.role) || typeof row.active !== "boolean" || (manage.value && (typeof row.password_set !== "boolean" || !Number.isInteger(row.revision) || row.revision < 1))) throw new Error("Invalid member response");
  return row;
}
function clearInvitation() { invitation.value = null; copied.value = false; }
function clear() {
  generation++; members.value = []; Object.keys(selectedRoles).forEach(key => delete selectedRoles[key]);
  dialog.value = ""; target.value = null; form.email = ""; formError.value = ""; status.value = ""; clearInvitation();
}
function memberResult(data) {
  const row = memberView(data.member);
  if (!/^\/app\/(setup-password#token=[A-Za-z0-9_.=-]+|org\/login)$/.test(data.invitation_url) || (data.expires_in !== null && data.expires_in !== undefined && (!Number.isInteger(data.expires_in) || data.expires_in < 1))) throw new Error("Invalid invitation response");
  return { member: row, url: window.location.origin + data.invitation_url, expiresIn: data.expires_in };
}
async function load() {
  if (busy.value || loading.value) return;
  const run = ++generation; loading.value = true; error.value = ""; status.value = ""; clearInvitation();
  try {
    const response = await orgRequest("GET", "/org/members", undefined, { signal: controller.signal });
    if (!mounted || run !== generation) return;
    const rows = response.items.map(memberView);
    if (new Set(rows.map(row => row.user_id)).size !== rows.length || (!manage.value && rows.some(row => !row.active))) throw new Error("Invalid member list");
    members.value = rows; Object.keys(selectedRoles).forEach(key => delete selectedRoles[key]);
    rows.forEach(row => { selectedRoles[row.user_id] = row.role; }); stale.value = false;
  } catch (exc) { if (mounted && run === generation) { members.value = []; error.value = failureText(exc); } }
  finally { if (mounted) loading.value = false; }
}
function openAdd() {
  if (!manage.value || busy.value || stale.value) return;
  clearInvitation(); formError.value = ""; form.email = ""; form.role = "viewer"; dialog.value = "add";
}
function openActive(row) {
  if (!manage.value || busy.value || stale.value) return;
  clearInvitation(); formError.value = ""; target.value = { ...row }; dialog.value = "active";
}
function closeDialog(mode = null) { if (mode && dialog.value !== mode) return; if (!busy.value) { dialog.value = ""; target.value = null; form.email = ""; formError.value = ""; } }
function updateMember(row) {
  members.value = members.value.map(item => item.user_id === row.user_id ? row : item);
  selectedRoles[row.user_id] = row.role;
  if (row.user_id === orgAccess.userId) {
    if (!row.active) { orgSession.clear(); window.dispatchEvent(new CustomEvent("bid:signed-out", { detail: "org" })); }
    else {
      orgAccess.role = row.role;
      if (row.role !== "admin") members.value = members.value.filter(item => item.active).map(({ user_id, email, role, active }) => ({ user_id, email, role, active }));
    }
  }
}
async function mutate(path, body, kind, row = null) {
  if (!manage.value || busy.value || loading.value || stale.value) return;
  const run = generation;
  busy.value = true; error.value = ""; formError.value = ""; status.value = ""; clearInvitation();
  try {
    const response = await orgRequest("POST", path, body, { signal: controller.signal });
    if (!mounted || run !== generation) return;
    if (kind === "add" || kind === "invite") {
      const data = memberResult(response.data);
      if (row && data.member.user_id !== row.user_id) throw new Error("Invalid invitation target");
      if (kind === "add") { members.value.push(data.member); selectedRoles[data.member.user_id] = data.member.role; }
      else updateMember(data.member);
      invitation.value = data; copied.value = false; dialog.value = "invitation"; form.email = "";
      status.value = kind === "add" ? "成员已添加，请把邀请链接交给本人。" : "邀请链接已生成，请把链接交给本人。";
    } else {
      const changed = memberView(response.data);
      if (changed.user_id !== row.user_id) throw new Error("Invalid member target");
      updateMember(changed); dialog.value = ""; target.value = null;
      status.value = kind === "role" ? "成员角色已保存。" : changed.active ? "成员已启用，已撤销的令牌不会恢复。" : "成员已停用，本单位的 API 令牌已撤销，任务历史保留。";
    }
  } catch (exc) {
    if (!mounted || run !== generation) return;
    if (dialog.value === "add" || dialog.value === "active") formError.value = failureText(exc);
    else error.value = failureText(exc);
    if (exc.code === "revision_conflict" || !Object.hasOwn(messages, exc.code)) stale.value = true;
    if (exc.status === 403 || exc.status === 404) { stale.value = true; members.value = []; }
  } finally { if (mounted) busy.value = false; }
}
function add() {
  const email = form.email.trim().toLowerCase();
  if (email.length > 254 || !/^[^@\s]+@[^@\s]+$/.test(email)) { formError.value = messages.invalid_input; return; }
  return mutate("/org/members", { email, role: form.role }, "add");
}
function saveRole(row) { return mutate(`/org/members/${encodeURIComponent(row.user_id)}/role`, { role: selectedRoles[row.user_id], expected_revision: row.revision }, "role", row); }
function setActive() { const row = target.value; if (row) return mutate(`/org/members/${encodeURIComponent(row.user_id)}/active`, { active: !row.active, expected_revision: row.revision }, "active", row); }
async function copy() {
  if (!invitation.value) return;
  try { await navigator.clipboard.writeText(invitation.value.url); if (mounted && invitation.value) copied.value = true; }
  catch { if (mounted) formError.value = "无法复制，请手动选择并复制链接。"; }
}
function closeInvitation() { clearInvitation(); dialog.value = ""; formError.value = ""; }
const addedBy = (id) => id ? members.value.find(row => row.user_id === id)?.email ?? id : "历史记录未标注";
window.addEventListener("bid:org-reset", clear);
onMounted(load);
onBeforeUnmount(() => { mounted = false; controller.abort(); window.removeEventListener("bid:org-reset", clear); clear(); });
</script>

<template>
  <div class="page-header">
    <div><h2>成员</h2><p class="subtitle">单位成员角色决定在本单位的权限；任务参与范围在任务内设置。</p></div>
    <div class="actions"><el-button :loading="loading" :disabled="busy || !!dialog" @click="load">重新读取</el-button><el-button v-if="manage" type="primary" :disabled="busy || loading || stale" @click="openAdd">添加成员</el-button></div>
  </div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" class="section" />
  <p v-if="status" role="status" aria-live="polite">{{ status }}</p>
  <p v-if="manage" class="hint" role="note">单位必须保留至少一位启用的管理员，包括本人在内，最后一位管理员不能降级或停用。停用会撤销该成员在本单位的 API 令牌，保留任务历史；再次启用不会恢复已撤销的令牌。</p>
  <p v-else class="hint" role="note">只有单位管理员能管理成员。当前仅显示启用成员的邮箱和角色。</p>
  <p v-if="stale" class="hint" role="alert">当前列表不能继续修改。请关闭对话框并重新读取成员列表，核对后再提交。</p>
  <div class="table-scroll" :aria-busy="loading">
    <table class="data-table">
      <thead><tr><th>邮箱</th><th>角色</th><template v-if="manage"><th>状态</th><th>密码</th><th>添加人</th><th>添加时间</th><th>更新时间</th><th>修订</th><th>操作</th></template></tr></thead>
      <tbody>
        <tr v-for="member in members" :key="member.user_id">
          <td>{{ member.email }}</td>
          <td><template v-if="manage"><el-select v-model="selectedRoles[member.user_id]" :aria-label="`${member.email} 角色`" :disabled="busy || loading || stale" class="member-role"><el-option v-for="(name, value) in roles" :key="value" :label="name" :value="value" /></el-select><el-button size="small" :disabled="busy || loading || stale || selectedRoles[member.user_id] === member.role" @click="saveRole(member)">保存角色</el-button></template><template v-else>{{ label(roles, member.role) }}</template></td>
          <template v-if="manage"><td><el-tag :type="member.active ? 'success' : 'info'">{{ member.active ? "启用" : "停用" }}</el-tag></td><td>{{ member.password_set ? "已设置" : "未设置" }}</td><td>{{ addedBy(member.created_by) }}</td><td>{{ member.created_at ? formatTime(member.created_at) : "—" }}</td><td>{{ member.updated_at ? formatTime(member.updated_at) : "—" }}</td><td>{{ member.revision }}</td><td><div class="actions"><el-button size="small" :type="member.active ? 'danger' : 'primary'" :disabled="busy || loading || stale" @click="openActive(member)">{{ member.active ? "停用" : "启用" }}</el-button><el-button v-if="!member.password_set" size="small" :disabled="busy || loading || stale" @click="mutate(`/org/members/${encodeURIComponent(member.user_id)}/invitation`, undefined, 'invite', member)">生成邀请链接</el-button></div></td></template>
        </tr>
        <tr v-if="!members.length && !loading"><td :colspan="manage ? 9 : 2" class="empty">没有可显示的成员。</td></tr>
      </tbody>
    </table>
  </div>
  <el-dialog :model-value="dialog === 'add'" title="添加成员" width="min(520px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @close="closeDialog('add')">
    <el-form label-position="top" @submit.prevent="add"><p class="hint">按邮箱添加成员，不改变已有账号的密码。邀请链接由管理员交给本人，不会自动发送邮件。</p><el-form-item label="邮箱" required><el-input v-model="form.email" name="member-email" type="email" maxlength="254" autocomplete="off" :disabled="busy" /></el-form-item><el-form-item label="角色" required><el-select v-model="form.role" aria-label="新成员角色" :disabled="busy"><el-option v-for="(name, value) in roles" :key="value" :label="name" :value="value" /></el-select></el-form-item><el-alert v-if="formError" :title="formError" type="error" :closable="false" role="alert" class="section" /><div class="actions"><el-button :disabled="busy" @click="closeDialog()">取消</el-button><el-button native-type="submit" type="primary" :loading="busy" :disabled="!manage || stale || !form.email.trim()">确认添加</el-button></div></el-form>
  </el-dialog>
  <el-dialog :model-value="dialog === 'active'" :title="target?.active ? '停用成员' : '启用成员'" width="min(520px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @close="closeDialog('active')">
    <p>{{ target?.email }}</p><p v-if="target?.active">停用后该成员下一次请求将失去本单位访问权限，其在本单位的 API 令牌会撤销。任务历史、决定和审计归属保留。</p><p v-else>启用后恢复本单位成员权限，已经撤销的 API 令牌不会恢复。</p><el-alert v-if="formError" :title="formError" type="error" :closable="false" role="alert" class="section" /><div class="actions"><el-button :disabled="busy" @click="closeDialog()">取消</el-button><el-button :type="target?.active ? 'danger' : 'primary'" :loading="busy" :disabled="!manage || stale" @click="setActive">{{ target?.active ? "确认停用" : "确认启用" }}</el-button></div>
  </el-dialog>
  <el-dialog :model-value="dialog === 'invitation'" title="成员邀请链接" width="min(620px, 94vw)" :close-on-click-modal="false" @close="closeInvitation">
    <template v-if="invitation"><p role="note">把此链接交给 {{ invitation.member.email }}。链接仅在此处显示一次，关闭或离开页面后不再保留。{{ invitation.expiresIn ? `设置密码链接有效期为 ${Math.floor(invitation.expiresIn / 3600)} 小时，设置密码后失效。` : "成员已有密码，打开链接后直接登录单位。" }}</p><p><code class="invitation-link" data-testid="invitation-link">{{ invitation.url }}</code></p><el-alert v-if="formError" :title="formError" type="error" :closable="false" role="alert" /><div class="actions"><el-button type="primary" @click="copy">{{ copied ? "已复制" : "复制链接" }}</el-button><el-button @click="closeInvitation">关闭链接</el-button></div></template>
  </el-dialog>
</template>

<style scoped>
.member-role { width: 145px; margin-right: 8px; }
.invitation-link { overflow-wrap: anywhere; user-select: all; }
</style>
