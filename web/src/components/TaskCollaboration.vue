<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ApiError, orgSession } from "../api.js";
import { confirmAction, errorText, formatTime, orgRequest } from "../org.js";
import { useTaskLive } from "../task-live.js";

const props = defineProps({ taskId: String, jobId: String, row: Object, discussionOnly: Boolean, initialThread: String });
const emit = defineEmits(["dirty", "changed", "denied"]);
const workflow = ref(null), identity = ref(null), currentMember = ref(null), members = ref([]), ready = ref(false), error = ref(""), notice = ref(""), busy = ref(false);
const assignee = ref(props.row.owner_user_id ?? ""), reason = ref(""), assignmentRevision = ref(props.row.assignment_revision ?? 0), assignmentStale = ref(false);
const threads = ref([]), comments = ref([]), selectedThread = ref(props.initialThread ?? null), body = ref(""), mentions = ref([]), cardRevision = ref(props.row.card_revision ?? null), commentStale = ref(false), pending = ref(null), assigneeRoles = ref(new Map());
const memberCursor = ref(null), memberNext = ref(null), memberPrior = ref([]), threadCursor = ref(null), threadNext = ref(null), threadPrior = ref([]), commentCursor = ref(null), commentNext = ref(null), commentPrior = ref([]);
let readGeneration = 0, lifetime = 0, active = true;
const cardId = computed(() => props.row.card_id ?? null);
const archived = computed(() => workflow.value?.state === "archived");
const manage = computed(() => ready.value && !archived.value && (identity.value?.role === "admin" || identity.value?.role === "bidder" && currentMember.value?.active && currentMember.value.role === "owner" && workflow.value?.owner_user_id === identity.value?.user_id));
const commentAllowed = computed(() => ready.value && !archived.value && currentMember.value?.active);
const assignees = computed(() => members.value.filter(member => member.active && ["owner", "contributor"].includes(member.role) && ["admin", "bidder", "technical"].includes(assigneeRoles.value.get(member.user_id))));
const validAssignee = computed(() => !assignee.value || assignees.value.some(member => member.user_id === assignee.value));
const recipients = computed(() => members.value.filter(member => member.active));
const dirty = computed(() => Boolean(reason.value || assignee.value !== (props.row.owner_user_id ?? "") || body.value || mentions.value.length || pending.value));
const bodyLength = computed(() => Array.from(body.value).length);
const bodyBytes = computed(() => new TextEncoder().encode(body.value).length);
const validBody = computed(() => Boolean(body.value.trim()) && bodyLength.value <= 4000 && bodyBytes.value <= 16384 && mentions.value.length <= 20 && new Set(mentions.value).size === mentions.value.length);
watch(dirty, value => emit("dirty", value));
function scope(value, card = false, thread = null) {
  if (!value || value.org_id !== orgSession.get()?.orgId || value.task_id !== props.taskId || (card && value.card_id !== cardId.value) || (thread && value.thread_id !== thread)) throw new ApiError(502, "invalid_response", "讨论或成员范围不符合契约");
}
function pageScope(result, card = false, thread = null) {
  scope(result.data, card, thread);
  if (result.items.length > 50 || result.data.returned !== result.items.length || typeof result.data.has_more !== "boolean" || (result.data.has_more && !result.data.next_cursor) || (card && (result.data.thread_id ?? null) !== thread)) throw new ApiError(502, "invalid_response", "讨论分页范围不符合契约");
  for (const item of result.items) scope(item, card, thread);
}
function query(cursor) { const params = new URLSearchParams({ limit: "50" }); if (cursor) params.set("cursor", cursor); return params; }
function reset() {
  readGeneration++; lifetime++; ready.value = false; workflow.value = null; identity.value = null; currentMember.value = null; members.value = []; assigneeRoles.value = new Map(); threads.value = []; comments.value = []; selectedThread.value = null; body.value = ""; mentions.value = []; reason.value = ""; assignee.value = ""; pending.value = null;
}
function denied(exc) { reset(); error.value = `任务不可访问：${errorText(exc)}`; emit("denied", exc); }
async function readMember(cursor) { const result = await orgRequest("GET", `/tasks/${props.taskId}/members?${query(cursor)}`); pageScope(result); return result; }
async function currentAssigneeRoles(pageMembers) {
  const wanted = new Set(pageMembers.filter(member => member.active && ["owner", "contributor"].includes(member.role)).map(member => member.user_id));
  const roles = new Map(), seen = new Set(); let cursor = null;
  while (wanted.size) {
    const page = await orgRequest("GET", `/tasks/${props.taskId}/member-candidates?${query(cursor)}`);
    scope(page.data);
    if(page.items.length > 50 || page.data.returned !== page.items.length || typeof page.data.has_more !== "boolean" || page.data.has_more && !page.data.next_cursor) throw new ApiError(502, "invalid_response", "分配候选分页范围不符合契约");
    for(const candidate of page.items) {
      if(candidate.org_id !== orgSession.get()?.orgId || candidate.active !== true || !["admin", "bidder", "technical", "viewer"].includes(candidate.org_role)) throw new ApiError(502, "invalid_response", "分配候选范围不符合契约");
      // Retain roles for this task-member page, never a second org directory.
      if(wanted.delete(candidate.user_id)) roles.set(candidate.user_id,candidate.org_role);
    }
    cursor = page.data.next_cursor;
    if(!cursor) break;
    if(seen.has(cursor)) throw new ApiError(502, "invalid_response", "分配候选分页范围重复");
    seen.add(cursor);
  }
  return roles;
}
async function load() {
  const run = ++readGeneration, orgId = orgSession.get()?.orgId;
  try {
    const [detail, who, memberPage] = await Promise.all([orgRequest("GET", `/tasks/${props.taskId}/workflow`), orgRequest("GET", "/org/current"), readMember(memberCursor.value)]);
    scope(detail.data.workflow);
    if (who.data.org_id !== orgId || !["admin", "bidder", "technical", "viewer"].includes(who.data.role)) throw new ApiError(502, "invalid_response", "单位身份范围不符合契约");
    let mine = memberPage.items.find(member => member.user_id === who.data.user_id) ?? null;
    // Locate only the current grant; the picker retains one bounded member page.
    if (!mine) {
      let cursor = null; const seen = new Set();
      do {
        const page = cursor === memberCursor.value ? memberPage : await readMember(cursor);
        mine = page.items.find(member => member.user_id === who.data.user_id) ?? null;
        cursor = page.data.next_cursor;
        if (cursor && seen.has(cursor)) throw new ApiError(502, "invalid_response", "成员分页范围重复");
        if (cursor) seen.add(cursor);
      } while (!mine && cursor);
    }
    const canAssign = !props.discussionOnly && detail.data.workflow.state === "active" && (who.data.role === "admin" || who.data.role === "bidder" && mine?.active && mine.role === "owner" && detail.data.workflow.owner_user_id === who.data.user_id);
    const eligibleRoles = canAssign ? await currentAssigneeRoles(memberPage.items) : new Map();
    let threadPage = null, commentPage = null;
    if (cardId.value) {
      threadPage = await orgRequest("GET", `/cards/${cardId.value}/threads?${query(threadCursor.value)}`); pageScope(threadPage, true);
      if (selectedThread.value) { commentPage = await orgRequest("GET", `/cards/${cardId.value}/threads/${selectedThread.value}/comments?${query(commentCursor.value)}`); pageScope(commentPage, true, selectedThread.value); for(const message of commentPage.items)messageScope(message,selectedThread.value); }
    }
    if (!active || run !== readGeneration || orgId !== orgSession.get()?.orgId) return null;
    workflow.value = detail.data.workflow; identity.value = who.data; currentMember.value = mine; members.value = memberPage.items; memberNext.value = memberPage.data.next_cursor; assigneeRoles.value = eligibleRoles;
    if (threadPage) { threads.value = threadPage.items; threadNext.value = threadPage.data.next_cursor; }
    if (commentPage) { comments.value = commentPage.items; commentNext.value = commentPage.data.next_cursor; }
    if ((!mine || !mine.active) && who.data.role !== "admin") throw new ApiError(404, "not_found", "Not found");
    ready.value = true; error.value = "";
    return workflow.value.last_event_cursor;
  } catch (exc) {
    if (run === readGeneration && active) { ready.value = false; threads.value = []; comments.value = []; members.value = []; error.value = errorText(exc); }
    throw exc;
  }
}
const live = useTaskLive(props.taskId, load, denied);
async function handleFailure(exc) {
  if ([401, 403, 404].includes(exc.status)) { live.stop(); denied(exc); }
  else error.value = errorText(exc);
}
async function membersPage(next) {
  if (busy.value) return;
  if (next) { memberPrior.value.push(memberCursor.value); memberCursor.value = memberNext.value; }
  else memberCursor.value = memberPrior.value.pop() ?? null;
  await live.refresh();
}
async function threadsPage(next) {
  if (busy.value) return;
  if (next) { threadPrior.value.push(threadCursor.value); threadCursor.value = threadNext.value; }
  else threadCursor.value = threadPrior.value.pop() ?? null;
  await live.refresh();
}
async function commentsPage(next) {
  if (busy.value) return;
  if (next) { commentPrior.value.push(commentCursor.value); commentCursor.value = commentNext.value; }
  else commentCursor.value = commentPrior.value.pop() ?? null;
  await live.refresh();
}
async function firstPages() {
  memberCursor.value = null; memberPrior.value = []; threadCursor.value = null; threadPrior.value = []; commentCursor.value = null; commentPrior.value = [];
  await live.refresh();
}
async function discard() { return !dirty.value || !orgSession.get() || await confirmAction("离开会丢弃未保存的分配或评论，继续？", "放弃未保存的修改", "放弃修改", true); }
async function selectThread(id) {
  if (busy.value || pending.value || (body.value || mentions.value.length) && !await confirmAction("切换讨论会丢弃未保存的评论，继续？", "放弃未保存的评论", "放弃评论", true)) return;
  body.value = ""; mentions.value = []; commentStale.value = false; selectedThread.value = id; comments.value = []; commentCursor.value = null; commentPrior.value = []; await live.refresh();
}
async function assign() {
  if (!manage.value || busy.value || assignmentStale.value || !validAssignee.value || !reason.value.trim() || Array.from(reason.value).length > 500) return;
  const run = lifetime, orgId = orgSession.get()?.orgId;
  busy.value = true; error.value = ""; notice.value = "";
  try {
    const result = await orgRequest("PUT", `/tasks/${props.taskId}/requirements/${props.row.requirement_id}/assignment?extraction_job_id=${props.jobId}`, { expected_assignment_revision: assignmentRevision.value, assignee_user_id: assignee.value || null, reason: reason.value });
    if (!active || run !== lifetime || orgId !== orgSession.get()?.orgId) return;
    const receipt = result.data.assignment; scope(receipt);
    if (receipt.requirement_id !== props.row.requirement_id || receipt.extraction_job_id !== props.jobId || receipt.revision !== assignmentRevision.value + 1 || receipt.assignee_user_id !== (assignee.value || null)) throw new ApiError(502, "invalid_response", "分配回执范围不符合契约");
    assignmentRevision.value = receipt.revision; reason.value = ""; notice.value = "分配已保存"; emit("changed");
  } catch (exc) {
    if (active && run === lifetime) { await handleFailure(exc); if (exc.status === 409 || !exc.status || exc.status >= 500) { assignmentStale.value = true; error.value += "；分配未重试，请关闭面板后重新核对当前分配与修订。"; emit("changed"); } }
  } finally { if (active && run === lifetime) busy.value = false; }
}
function messageScope(message, thread) { scope(message, true, thread); if (typeof message.body !== "string" || !Array.isArray(message.mentioned_user_ids) || Array.from(message.body).length > 4000 || new TextEncoder().encode(message.body).length > 16384 || message.mentioned_user_ids.length > 20 || new Set(message.mentioned_user_ids).size !== message.mentioned_user_ids.length) throw new ApiError(502, "invalid_response", "评论回执范围不符合契约"); }
function requestId() {
  // getRandomValues also supports the local HTTP console; randomUUID requires HTTPS.
  const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = bytes[6] & 15 | 64; bytes[8] = bytes[8] & 63 | 128;
  const hex = Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
}
async function send(retry = false) {
  if (!commentAllowed.value || busy.value || commentStale.value || (!retry && (pending.value || !validBody.value))) return;
  if (!retry) {
    if (selectedThread.value === null && !Number.isInteger(cardRevision.value)) return;
    const input = { body: body.value, mentioned_user_ids: [...mentions.value], client_request_id: requestId() };
    if (!selectedThread.value) input.expected_card_revision = cardRevision.value;
    pending.value = { path: `/cards/${cardId.value}/threads${selectedThread.value ? `/${selectedThread.value}/comments` : ""}`, input, thread: selectedThread.value };
  }
  const request = pending.value, run = lifetime, orgId = orgSession.get()?.orgId;
  if (!request) return;
  busy.value = true; error.value = ""; notice.value = "";
  try {
    const result = await orgRequest("POST", request.path, request.input);
    if (!active || run !== lifetime || orgId !== orgSession.get()?.orgId) return;
    const message = request.thread ? result.data.comment : result.data.first_comment;
    if (!request.thread) scope(result.data.thread, true);
    messageScope(message, request.thread ?? result.data.thread.id);
    // A replay may mask a recipient who lost membership after the original commit.
    if (message.client_request_id !== request.input.client_request_id || message.author_user_id !== identity.value?.user_id || message.body !== request.input.body || message.mentioned_user_ids.some(id => !request.input.mentioned_user_ids.includes(id))) throw new ApiError(502, "invalid_response", "评论回执与请求不一致");
    selectedThread.value = message.thread_id; body.value = ""; mentions.value = []; pending.value = null; commentCursor.value = null; commentPrior.value = []; threadCursor.value = null; threadPrior.value = []; notice.value = "评论已保存"; emit("changed"); await live.refresh();
  } catch (exc) {
    if (active && run === lifetime) {
      await handleFailure(exc);
      if (exc.status === 409) { pending.value = null; commentStale.value = true; error.value += "；评论未重试，重新核对当前卡片后再明确提交。"; emit("changed"); }
      else if (!exc.status || exc.status >= 500) error.value += "；结果尚未确认，可明确重试同一请求；正文与提及已锁定。";
      else pending.value = null;
    }
  } finally { if (active && run === lifetime) busy.value = false; }
}
async function checkCard() {
  try {
    const run = lifetime, result = await orgRequest("GET", `/cards/${cardId.value}`);
    if (!active || run !== lifetime) return;
    scope(result.data);
    if (result.data.id !== cardId.value || result.data.extraction_job_id !== props.jobId || result.data.requirement_id !== props.row.requirement_id) throw new ApiError(502, "invalid_response", "卡片范围不符合契约");
    if (!Number.isInteger(result.data.revision) || result.data.revision < 1) throw new ApiError(502, "invalid_response", "卡片修订不符合契约");
    cardRevision.value = result.data.revision; commentStale.value = false; error.value = ""; notice.value = "已核对当前卡片；请确认正文与提及后重新提交。";
  } catch (exc) { await handleFailure(exc); }
}
watch(() => [props.row.assignment_revision, props.row.owner_user_id], ([revision, user]) => {
  if (reason.value && revision !== assignmentRevision.value) assignmentStale.value = true;
  else if (!reason.value && !assignmentStale.value) { assignmentRevision.value = revision ?? 0; assignee.value = user ?? ""; }
});
watch(() => props.row.card_revision, revision => {
  if (revision === cardRevision.value) return;
  if (pending.value) return;
  if (body.value && !selectedThread.value) commentStale.value = true;
  else cardRevision.value = revision;
});
function beforeUnload(event) { if (dirty.value) { event.preventDefault(); event.returnValue = ""; } }
window.addEventListener("beforeunload", beforeUnload);
onBeforeUnmount(() => { active = false; reset(); window.removeEventListener("beforeunload", beforeUnload); });
onMounted(live.start);
defineExpose({ discard, dirty });
</script>
<template>
  <section aria-label="任务分配与讨论" class="collaboration">
    <p role="status" aria-live="polite">{{ live.status.value }}</p>
    <el-button size="small" :disabled="busy" @click="firstPages">重新读取讨论与成员首页</el-button>
    <el-alert v-if="error" :title="error" type="error" :closable="false" class="section" />
    <p v-if="notice" role="status">{{ notice }}</p>
    <template v-if="ready">
      <p v-if="archived" class="hint">已归档任务只读</p>
      <section v-if="!discussionOnly" aria-label="工作分配">
        <h3>工作分配</h3><p>当前分配：{{ members.find(member => member.user_id === row.owner_user_id)?.display_label ?? row.owner_user_id ?? '未分配' }} · 修订 {{ row.assignment_revision }}</p>
        <el-form v-if="manage" label-position="top" @submit.prevent="assign">
          <el-form-item label="分配给当前任务成员"><el-select v-model="assignee" data-testid="assignment-assignee" clearable :disabled="busy || assignmentStale" placeholder="留空表示取消分配"><el-option v-for="member in assignees" :key="member.user_id" :label="member.display_label ?? member.user_id" :value="member.user_id" /></el-select></el-form-item>
          <p v-if="!validAssignee" class="hint">当前分配成员不在本页可编辑成员中；翻页核对、重新选择或留空取消分配。</p>
          <el-form-item label="分配原因" required><el-input v-model="reason" type="textarea" data-testid="assignment-reason" maxlength="500" :disabled="busy" /></el-form-item>
          <el-button native-type="submit" type="primary" :disabled="busy || assignmentStale || !validAssignee || !reason.trim()">保存分配</el-button>
        </el-form>
        <p v-else class="hint">由当前负责人或单位管理员分配准备工作。</p>
      </section>
      <section v-if="cardId" aria-label="卡片讨论">
        <h3>卡片讨论</h3><p class="hint">评论是讨论记录，不改变响应修订或人工确认。</p>
        <ul class="thread-list"><li v-for="thread in threads" :key="thread.id"><el-button :type="selectedThread === thread.id ? 'primary' : 'default'" :disabled="busy || !!pending" @click="selectThread(thread.id)">讨论 {{ formatTime(thread.created_at) }} · {{ thread.message_count }} 条</el-button></li></ul>
        <p v-if="!threads.length" class="hint">本页没有讨论。</p>
        <div class="actions"><el-button :disabled="!threadPrior.length || busy" @click="threadsPage(false)">上一页讨论</el-button><el-button :disabled="!threadNext || busy" @click="threadsPage(true)">下一页讨论</el-button><el-button v-if="selectedThread && commentAllowed" :disabled="busy || !!pending" @click="selectThread(null)">开始新讨论</el-button></div>
        <ol v-if="selectedThread" class="comment-list"><li v-for="comment in comments" :key="comment.id"><p class="hint">{{ members.find(member => member.user_id === comment.author_user_id)?.display_label ?? comment.author_user_id }} · {{ formatTime(comment.created_at) }}</p><p class="comment-body">{{ comment.body }}</p><p v-if="comment.mentioned_user_ids.length" class="hint">提及：{{ comment.mentioned_user_ids.map(id => members.find(member => member.user_id === id)?.display_label ?? id).join('、') }}</p></li></ol>
        <div v-if="selectedThread" class="actions"><el-button :disabled="!commentPrior.length || busy" @click="commentsPage(false)">上一页评论</el-button><el-button :disabled="!commentNext || busy" @click="commentsPage(true)">下一页评论</el-button></div>
        <el-form v-if="commentAllowed" label-position="top" @submit.prevent="send()">
          <el-form-item :label="selectedThread ? '回复正文' : '新讨论正文'" required><el-input v-model="body" data-testid="comment-body" type="textarea" :rows="4" :disabled="busy || !!pending" /></el-form-item>
          <p class="hint" :class="{invalid: bodyLength > 4000 || bodyBytes > 16384}">{{ bodyLength }} / 4,000 字符；{{ bodyBytes }} / 16,384 UTF-8 字节</p>
          <el-form-item label="提及当前任务成员"><el-select v-model="mentions" data-testid="comment-mentions" multiple :multiple-limit="20" :disabled="busy || !!pending"><el-option v-for="member in recipients" :key="member.user_id" :label="member.display_label ?? member.user_id" :value="member.user_id" /></el-select></el-form-item>
          <el-button v-if="pending" type="primary" :disabled="busy || commentStale" @click="send(true)">重试同一请求</el-button><el-button v-else native-type="submit" type="primary" :disabled="busy || commentStale || !validBody">{{ selectedThread ? '发送回复' : '创建讨论' }}</el-button>
          <el-button v-if="commentStale && !pending" :disabled="busy" @click="checkCard">重新核对后编辑</el-button>
        </el-form>
        <p v-else-if="!archived" class="hint">当前任务的有效成员可发表评论；管理员只读访问不授予评论权限。</p>
      </section>
      <p v-else class="hint">要求还没有响应卡片；建卡后可开展讨论。</p>
      <div v-if="manage || commentAllowed" class="actions"><el-button size="small" :disabled="!memberPrior.length || busy" @click="membersPage(false)">上一页成员</el-button><el-button size="small" :disabled="!memberNext || busy" @click="membersPage(true)">下一页成员</el-button><span class="hint">成员选项只展示当前页。</span></div>
    </template>
  </section>
</template>
<style scoped>
.collaboration{min-width:0}.collaboration section{margin:20px 0}.thread-list,.comment-list{list-style:none;padding:0}.thread-list li{margin:8px 0}.comment-list li{padding:12px 0;border-bottom:1px solid var(--border)}.comment-body{white-space:pre-wrap;overflow-wrap:anywhere}.invalid{color:var(--danger)}.el-select{width:100%}.actions{flex-wrap:wrap}
</style>
