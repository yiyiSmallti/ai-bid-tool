<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { orgSession } from "../api.js";
import { dispositions, domains, errorText, formatTime, label, mine, orgAccess, orgRequest } from "../org.js";
import { canReviewTask, useProvidedTaskAuthority } from "../task-authority.js";
const props = defineProps({ taskId: String, jobId: String, requirementId: String, card: Object, reviewed: Array, warnings: Array, reason: String, confirmBlocker: String, dirty: Boolean, conflict: Boolean });
const emit = defineEmits(["updated", "policy", "review", "clear-review", "dirty", "denied", "refresh-card"]);
const authority = useProvidedTaskAuthority();
const policy = ref(null), review = ref(null), signatures = ref([]), currentSignatures = ref([]), cursor = ref(null), prior = ref([]);
const selectedDomain = ref(""), policyRequired = ref(false), policyReason = ref(""), dispositionReason = ref(""), error = ref(""), notice = ref(""), busy = ref(false), loaded = ref(false);
let active = true, generation = 0, fingerprint = "";
const activeTask = computed(() => authority.value?.workflow.state === "active");
const manage = computed(() => activeTask.value && (orgAccess.role === "admin" || authority.value?.member?.active && authority.value?.member.role === "owner"));
const policyDirty = computed(() => !!policy.value && (policyRequired.value !== policy.value.co_sign_required || !!policyReason.value));
watch([policyDirty, dispositionReason], () => emit("dirty", policyDirty.value || !!dispositionReason.value));
const round = computed(() => review.value?.round);
const requiredDomains = computed(() => review.value?.summary.required_domains ?? policy.value?.required_domains ?? []);
const multi = computed(() => (policy.value?.required_domains.length ?? 0) > 1);
const openRound = computed(() => round.value?.state === "open" && ["pending", "partial"].includes(review.value?.summary.status));
const canSignDomain = domain => canReviewTask(authority.value, domain) && mine(domain);
const signableDomains = computed(() => openRound.value ? review.value.summary.pending_domains.filter(canSignDomain) : []);
const editableDisposition = computed(() => multi.value && props.card && ["draft", "rejected", "needs_material"].includes(props.card.state) && !openRound.value && canSignDomain(policy.value?.primary_domain));
const dispositionBlocker = computed(() => {
  if (!round.value?.disposition_reason?.trim()) return "本轮共同处置原因尚未读取";
  if (props.dirty) return "有未保存响应，请先保存";
  if (props.conflict) return "卡片有修订冲突，请先重新核对";
  if ((props.warnings?.length ?? 0) !== (props.card?.warning_codes.length ?? 0)) return "请逐项勾选本轮全部警示";
  if (!props.reason?.trim()) return "处置签署需要填写本人的核对理由";
  return "";
});
const signBlocker = computed(() => {
  if (!loaded.value) return "会签状态尚未读取";
  if (policyDirty.value || dispositionReason.value.trim()) return "有未保存的会签策略或处置原因，请先处理这些编辑";
  if (!signableDomains.value.includes(selectedDomain.value)) return "请明确选择本人负责且尚未签署的职责";
  return round.value?.purpose === "disposition" ? dispositionBlocker.value : props.confirmBlocker;
});
const statusLabels = computed(() => ({ not_required: "尚无审阅轮次", pending: multi.value ? "等待签署" : "等待确认", partial: "部分已签署", complete: multi.value ? "全部已签署" : "审阅已完成", invalidated: multi.value ? "会签已失效" : "审阅已失效" }));
const scope = value => value.org_id === orgSession.get()?.orgId && value.task_id === props.taskId;
function clear() { selectedDomain.value = ""; emit("clear-review"); }
async function load(resetPage = true) {
  const run = ++generation;
  if (resetPage) { cursor.value = null; prior.value = []; }
  try {
    const params = new URLSearchParams({ limit: "50" }); if (cursor.value) params.set("cursor", cursor.value);
    const [p, s] = await Promise.all([
      orgRequest("GET", `/tasks/${props.taskId}/requirements/${props.requirementId}/review-policy?extraction_job_id=${props.jobId}`),
      props.card ? orgRequest("GET", `/cards/${props.card.id}/signoffs?${params}`) : Promise.resolve(null),
    ]);
    if (!active || run !== generation) return;
    if (!scope(p.data.policy) || p.data.policy.requirement_id !== props.requirementId || p.data.policy.extraction_job_id !== props.jobId || !Array.isArray(p.data.policy.required_domains)) throw new Error("会签策略范围不符合契约");
    if (s && (!scope(s.data) || s.data.card_id !== props.card.id || s.items.length > 50 || s.data.returned !== s.items.length || s.data.round && (!scope(s.data.round) || s.data.round.card_id !== props.card.id || s.data.round.requirement_id !== props.requirementId || s.data.round.extraction_job_id !== props.jobId) || s.items.some(item => !scope(item) || item.card_id !== props.card.id))) throw new Error("会签轮次或签署范围不符合契约");
    const next = JSON.stringify([p.data.policy, s?.data.round, s?.data.summary]);
    if (fingerprint && next !== fingerprint) { clear(); emit("refresh-card"); }
    fingerprint = next;
    const hadPolicyEdits = policyDirty.value;
    policy.value = p.data.policy;
    if (!hadPolicyEdits) policyRequired.value = policy.value.co_sign_required;
    review.value = s?.data ?? null; signatures.value = s?.items ?? [];
    if (resetPage) currentSignatures.value = signatures.value.filter(item => item.round_id === s?.data.round?.id);
    loaded.value = true; emit("policy", policy.value); emit("review", review.value);
  } catch (exc) {
    if (!active || run !== generation) return;
    loaded.value = false; emit("policy", null); clear(); error.value = errorText(exc);
    if ([401,403,404].includes(exc.status)) { policy.value = null; review.value = null; signatures.value = []; currentSignatures.value = []; emit("denied", exc); }
  }
}
async function mutate(path, body, method = "POST") {
  busy.value = true; error.value = ""; notice.value = "";
  try {
    await orgRequest(method, path, body);
    if (!active) return;
    clear();
    if (path.includes("/review-policy?")) policyReason.value = "";
    if (path.endsWith("/review-rounds")) dispositionReason.value = "";
    if (props.card) { const fresh = (await orgRequest("GET", `/cards/${props.card.id}`)).data; if (!scope(fresh) || fresh.requirement_id !== props.requirementId || fresh.extraction_job_id !== props.jobId) throw new Error("响应卡范围不符合契约"); emit("updated", fresh); }
    await load(); notice.value = "操作已保存；会签结果以当前轮次为准。";
    window.dispatchEvent(new CustomEvent("bid:task-cards-changed", { detail: { taskId: props.taskId } }));
  } catch (exc) {
    if (!active) return;
    clear(); error.value = errorText(exc);
    if (exc.status === 409) { await load(); emit("refresh-card"); error.value = `${errorText(exc)}；本次签署或修改未重试。旧职责和审阅勾选已清除，请重新核对当前轮次后明确提交。`; }
    else if ([401,403,404].includes(exc.status)) emit("denied", exc);
  } finally { busy.value = false; }
}
function savePolicy() {
  if (!manage.value || !policy.value || !policyReason.value.trim() || props.dirty || props.conflict) return;
  mutate(`/tasks/${props.taskId}/requirements/${props.requirementId}/review-policy?extraction_job_id=${props.jobId}`, { expected_policy_revision: policy.value.revision, co_sign_required: policyRequired.value, reason: policyReason.value.trim() }, "PUT");
}
function openDisposition(intended) {
  if (!editableDisposition.value || props.dirty || props.conflict || !dispositionReason.value.trim()) return;
  mutate(`/cards/${props.card.id}/review-rounds`, { expected_revision: props.card.revision, reason: dispositionReason.value.trim(), purpose: "disposition", intended_disposition: intended, client_request_id: crypto.randomUUID() });
}
function sign() {
  if (busy.value || signBlocker.value || !openRound.value) return;
  const body = { expected_revision: props.card.revision, expected_round: round.value.round_revision, selected_domain: selectedDomain.value, purpose: round.value.purpose, reviewed_warning_codes: [...props.warnings], reason: props.reason.trim() || null, client_request_id: crypto.randomUUID() };
  if (round.value.purpose === "response") { body.action = "confirm"; body.reviewed_evidence_ids = [...props.reviewed]; }
  mutate(`/cards/${props.card.id}/signoffs`, body);
}
function signedBy(domain) { return currentSignatures.value.find(item => item.domain === domain); }
async function next() { if (!review.value?.next_cursor) return; prior.value.push(cursor.value); cursor.value = review.value.next_cursor; await load(false); }
async function previous() { cursor.value = prior.value.pop() ?? null; await load(false); }
watch(() => props.card?.revision, () => { clear(); load(); });
watch(() => [authority.value?.workflow.revision, authority.value?.workflow.last_event_cursor], () => { load(); });
watch(() => authority.value?.workflow.access_epoch, clear);
onMounted(load);
onBeforeUnmount(() => { active = false; generation++; });
defineExpose({ refresh: () => { clear(); return load(); } });
</script>
<template>
  <section class="cosign section" aria-label="会签策略与签署记录">
    <h4>会签策略与签署记录</h4>
    <p v-if="!loaded" class="hint">正在读取会签策略与当前轮次；读取完成前不能签署。</p>
    <template v-if="policy">
      <p>有效职责：{{policy.required_domains.map(domain => label(domains,domain)).join('、') || '待分类'}} · 策略修订 {{policy.revision}} · 任务规则修订 {{policy.task_rule_revision}}</p>
      <p v-if="policy.starred && policy.co_sign_starred" class="notice warning">★ 条款匹配任务规则，关闭本条显式会签要求仍须商务与技术分别签署。</p>
      <details><summary>本条要求的会签策略</summary><label class="check"><input v-model="policyRequired" type="checkbox" :disabled="!manage || busy" />本条要求商务与技术会签</label>
        <el-form v-if="manage" label-position="top" @submit.prevent="savePolicy"><el-form-item label="会签策略修改原因" required><el-input v-model="policyReason" type="textarea" aria-label="会签策略修改原因" aria-required="true" maxlength="500" :disabled="busy" /></el-form-item><p class="hint">修改会退役受影响轮次；历史签署不能批准新策略。</p><el-button native-type="submit" :disabled="busy || !policyReason.trim() || dirty || conflict">保存本条会签策略</el-button></el-form>
        <p v-else class="hint">{{activeTask ? '只有任务负责人或单位管理员可以修改策略。' : '任务已归档，策略只读。'}}</p>
      </details>
    </template>
    <div v-if="review" data-testid="cosign-current" class="status-box" role="status" aria-live="polite">
      <p><strong>{{label(statusLabels,review.summary.status)}}</strong> · 当前轮次 {{review.summary.round_revision || '尚未建立'}}</p>
      <p v-if="round">{{round.purpose === 'disposition' ? (multi ? '处置会签' : '处置审阅') : (multi ? '响应会签' : '响应审阅')}} · {{label(dispositions,round.intended_disposition)}} · 快照卡片修订 {{round.card_revision}}</p>
      <p v-if="round?.purpose === 'disposition'" class="quote">共同处置原因：{{round.disposition_reason ?? '处置原因尚未读取，不能签署'}}</p>
      <p>待签署：{{review.summary.pending_domains.map(domain => label(domains,domain)).join('、') || '无'}}</p>
      <ul class="domain-signatures"><li v-for="domain in requiredDomains" :key="domain"><strong>{{label(domains,domain)}}</strong>：{{review.summary.signed_domains.includes(domain) ? '已签署' : '待签署'}}<span v-if="review.summary.signed_domains.includes(domain) && signedBy(domain)"> · 签署人 {{signedBy(domain).signer_user_id}} · {{formatTime(signedBy(domain).created_at)}} · 轮次 {{round.round_revision}}</span></li></ul>
      <p v-if="['pending','partial'].includes(review.summary.status)" class="hint">当前审阅完成前，响应不可进入初稿；处置审阅完成前保留当前处置。</p>
      <p v-if="review.summary.status === 'invalidated'" class="notice warning">该轮次已失效，历史签署不能算作当前审批。</p>
    </div>
    <template v-if="editableDisposition">
      <el-form label-position="top" @submit.prevent><el-form-item label="处置会签原因" required><el-input v-model="dispositionReason" type="textarea" aria-label="处置会签原因" aria-required="true" maxlength="500" :disabled="busy" /></el-form-item></el-form>
      <div class="actions"><el-button :disabled="busy || dirty || conflict || !dispositionReason.trim()" @click="openDisposition(card.disposition === 'comply_only' ? 'respond' : 'comply_only')">{{card.disposition === 'comply_only' ? '发起逐项响应会签' : '发起仅需遵守会签'}}</el-button></div>
    </template>
    <template v-if="signableDomains.length && (multi || round?.purpose === 'disposition')">
      <fieldset class="sign-domains"><legend>本次签署职责（请明确选择）</legend><label v-for="domain in requiredDomains" :key="domain" class="check"><input v-model="selectedDomain" type="radio" name="cosign-domain" :value="domain" :disabled="busy || !signableDomains.includes(domain) || dirty || conflict" />本次以{{domain === 'technical' ? '技术' : '商务 / 资格'}}职责签署</label></fieldset>
      <p class="hint">签署仅代表本人职责。每位签署人都要独立核对全部材料和警示；处置签署只核对警示和理由，不确认证据。</p>
      <el-button type="success" :disabled="busy || !!signBlocker" aria-describedby="cosign-blocker" @click="sign">{{round.purpose === 'disposition' ? '签署本轮处置' : '签署本轮响应'}}</el-button>
      <p v-if="signBlocker" id="cosign-blocker" class="hint">暂不能签署：{{signBlocker}}</p>
    </template>
    <p v-else-if="openRound && multi" class="hint">{{activeTask ? '当前账号没有尚待签署的职责；单位管理员不能代替专业审核人签署。' : '任务已归档，签署只读。'}}</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" data-testid="cosign-error" class="section" />
    <p v-if="notice" role="status">{{notice}}</p>
    <details v-if="signatures.length"><summary>签署历史（包含已退役轮次）</summary><ul class="domain-signatures"><li v-for="item in signatures" :key="item.id">{{label(domains,item.domain)}} · {{item.purpose === 'disposition' ? '处置' : '响应'}} · 签署人 {{item.signer_user_id}} · {{formatTime(item.created_at)}} · {{item.round_id === round?.id ? '当前轮次' : '历史轮次'}} <span class="mono">{{item.round_id}}</span></li></ul><div class="actions"><el-button size="small" :disabled="!prior.length" @click="previous">上一页签署记录</el-button><el-button size="small" :disabled="!review.next_cursor" @click="next">下一页签署记录</el-button></div></details>
  </section>
</template>
<style scoped>
.cosign { border-top: 1px solid var(--border); padding-top: 8px; }
.sign-domains { border: 1px solid var(--border); border-radius: 8px; margin: 12px 0; min-width: 0; }
.domain-signatures { padding-left: 20px; overflow-wrap: anywhere; }
.domain-signatures li { margin: 8px 0; }
</style>
