<script setup>
import { onMounted, ref } from "vue";
import { request } from "../api.js";
import { formatTime } from "../ui.js";

const emit = defineEmits(["decided"]);
const statuses = { pending: "待审核", approved: "已通过", rejected: "已拒绝", expired: "已过期" };
const rows = ref([]), status = ref("pending"), loading = ref(false), busy = ref(false), error = ref(""), notice = ref("");
const cursors = ref([null]), hasNext = ref(false), selected = ref(null), action = ref(null), decisionError = ref("");
const orgName = ref(""), attach = ref(false), reason = ref("");
const failures = {
  existing_user_requires_attach: "该邮箱已有账号。核实申请人拥有该账号后，明确确认挂到已有账号。",
  application_not_pending: "申请已处理或已过期，请刷新列表。",
  not_found: "申请不存在，请刷新列表。",
  invalid_input: "填写内容不符合要求，请检查后再提交。",
};

async function load(reset = false) {
  if (reset) cursors.value = [null];
  loading.value = true; error.value = "";
  const query = new URLSearchParams({ status: status.value, limit: "50" });
  const before = cursors.value.at(-1);
  if (before) query.set("before", before);
  try {
    const response = await request("GET", `/platform/org-applications?${query}`);
    rows.value = response.items; hasNext.value = rows.value.length === 50;
  } catch (exc) { rows.value = []; hasNext.value = false; error.value = failures[exc.code] ?? "申请列表读取失败，请刷新后重试。"; }
  finally { loading.value = false; }
}
function next() { cursors.value.push(rows.value.at(-1).created_at); load(); }
function previous() { cursors.value.pop(); load(); }
function begin(row, mode) {
  selected.value = { ...row }; action.value = mode; decisionError.value = ""; notice.value = "";
  orgName.value = row.org_name; attach.value = false; reason.value = "";
}
function close() { if (!busy.value) { selected.value = null; action.value = null; } }
async function decide() {
  if (!selected.value || busy.value) return;
  decisionError.value = "";
  if (action.value === "approve" && !orgName.value.trim()) { decisionError.value = "填写组织名称。"; return; }
  if (action.value === "approve" && selected.value.existing_user && !attach.value) { decisionError.value = failures.existing_user_requires_attach; return; }
  if (action.value === "reject" && !reason.value.trim()) { decisionError.value = "填写拒绝原因。"; return; }
  const mode = action.value;
  const body = mode === "approve"
    ? { org_name: orgName.value.trim() === selected.value.org_name ? null : orgName.value.trim(), attach_existing_user: attach.value }
    : { reason: reason.value.trim() };
  busy.value = true;
  try {
    await request("POST", `/platform/org-applications/${selected.value.id}/${mode}`, body);
    selected.value = null; action.value = null;
    notice.value = mode === "approve" ? "已审核通过并开通单位。" : "已拒绝申请，原因已记录。";
    await load(true); emit("decided");
  } catch (exc) {
    decisionError.value = failures[exc.code] ?? "审核操作失败，请刷新核对申请后重试。";
    if (exc.code === "existing_user_requires_attach") { selected.value.existing_user = true; attach.value = false; }
  } finally { busy.value = false; }
}
onMounted(() => load());
</script>

<template>
  <div class="applications">
    <div class="actions section">
      <el-select v-model="status" aria-label="申请状态" :disabled="loading" @change="load(true)"><el-option v-for="(text, key) in statuses" :key="key" :label="text" :value="key" /></el-select>
      <el-button :loading="loading" @click="load(true)">刷新申请</el-button>
    </div>
    <p class="hint">审核通过后开通空单位，申请人成为管理员。请先核实组织信息和申请人身份；审核结果由运营人员联系申请人。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" class="section" />
    <el-alert v-if="notice" :title="notice" type="success" :closable="false" role="status" class="section" />
    <div class="table-scroll" :aria-busy="loading">
      <table class="data-table">
        <thead><tr><th>组织与用途</th><th>联系人</th><th>邮箱与账号</th><th>手机号</th><th>提交与到期</th><th>同来源 24 小时申请</th><th>状态与决定</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="row in rows" :key="row.id">
            <td><strong>{{ row.org_name }}</strong><p v-if="row.note" class="submitted-text hint">{{ row.note }}</p></td>
            <td>{{ row.contact_name }}</td>
            <td>{{ row.email }}<p class="hint">{{ row.existing_user ? '已有账号' : '新账号' }}</p></td>
            <td>{{ row.phone || '—' }}</td>
            <td>{{ formatTime(row.created_at) }}<p class="hint">到期 {{ formatTime(row.expires_at) }}</p></td>
            <td>{{ row.source_submissions_24h }}</td>
            <td><el-tag :type="row.status === 'pending' ? 'warning' : row.status === 'approved' ? 'success' : 'info'">{{ statuses[row.status] }}</el-tag><p v-if="row.decided_by" class="hint">{{ row.decided_by }} · {{ formatTime(row.decided_at) }}</p><p v-if="row.decision_reason" class="submitted-text hint">{{ row.decision_reason }}</p></td>
            <td><div v-if="row.status === 'pending'" class="row-actions"><el-button size="small" type="primary" @click="begin(row, 'approve')">通过</el-button><el-button size="small" type="danger" plain @click="begin(row, 'reject')">拒绝</el-button></div><span v-else class="hint">—</span></td>
          </tr>
          <tr v-if="!rows.length"><td colspan="8" class="empty">{{ loading ? '正在读取申请…' : '没有符合状态的申请。' }}</td></tr>
        </tbody>
      </table>
    </div>
    <div class="actions section"><el-button :disabled="loading || cursors.length === 1" @click="previous">上一页</el-button><el-button :disabled="loading || !hasNext" @click="next">下一页</el-button></div>
    <el-dialog :model-value="Boolean(selected)" :title="action === 'approve' ? '审核通过' : '拒绝申请'" width="min(580px, calc(100vw - 24px))" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" destroy-on-close @close="close">
      <el-form v-if="selected" label-position="top" @submit.prevent="decide">
        <p>{{ selected.org_name }} · {{ selected.contact_name }} · {{ selected.email }}</p>
        <template v-if="action === 'approve'">
          <el-form-item label="组织名称（可更正）" required><el-input v-model="orgName" name="application-org-name" maxlength="200" :disabled="busy" /></el-form-item>
          <template v-if="selected.existing_user">
            <el-alert title="该邮箱已有账号。请核实申请人拥有该账号，开通后该账号将成为新单位管理员，已有密码不会改变。" type="warning" :closable="false" show-icon role="note" class="section" />
            <el-checkbox v-model="attach" :disabled="busy" name="attach-existing-user">挂到已有账号（已核实申请人拥有该账号）</el-checkbox>
          </template>
          <p v-else class="hint">申请人使用提交的邮箱和密码登录。单位初始余额为零。</p>
        </template>
        <el-form-item v-else label="拒绝原因（仅内部记录）" required><el-input v-model="reason" name="application-reason" type="textarea" maxlength="500" :autosize="{ minRows: 3, maxRows: 6 }" :disabled="busy" /></el-form-item>
        <el-alert v-if="decisionError" :title="decisionError" type="error" :closable="false" role="alert" class="section" />
        <div class="actions section"><el-button :disabled="busy" @click="close">取消</el-button><el-button :type="action === 'approve' ? 'primary' : 'danger'" native-type="submit" :loading="busy" :disabled="action === 'approve' ? !orgName.trim() || (selected.existing_user && !attach) : !reason.trim()">{{ action === 'approve' ? '确认通过' : '确认拒绝' }}</el-button></div>
      </el-form>
    </el-dialog>
  </div>
</template>

<style scoped>
.applications > .actions .el-select { width: 150px; }
.row-actions { display: flex; gap: 6px; }
.row-actions .el-button + .el-button { margin-left: 0; }
.submitted-text { white-space: pre-wrap; overflow-wrap: anywhere; max-width: 300px; }
</style>
