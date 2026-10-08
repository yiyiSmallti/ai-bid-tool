<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { ApiError } from "../api.js";
import { formatTime, orgRequest } from "../org.js";
import { reviewError } from "../bid-review.js";
const props = defineProps({ taskId: String, run: Object, authority: Object, authorizationEpoch: Number });
const findings = ref([]), meta = ref(null), cursor = ref(null), severity = ref(""), state = ref(""), outcome = ref(""), busy = ref(false), error = ref("");
const dialog = ref(null), reason = ref(""), domain = ref("commercial"), history = ref(null), historyRows = ref([]), historyCursor = ref(null);
const labels = { fatal: "废标风险", high: "高风险缺陷", medium: "其他", open: "待审查", dismissed: "已驳回", confirmed: "已确认", responded: "已响应", deviation: "偏离", missing: "缺失", unknown: "未确定", commercial: "商务", technical: "技术", dismiss: "驳回", reopen: "重新打开", confirm: "确认", classify: "分类", rejection: "废标风险", lost_points: "扣分风险", both: "废标与扣分风险", none: "未确定影响" };
const groups = computed(() => ["fatal", "high", "medium"].map(key => ({ key, label: labels[key], rows: findings.value.filter(row => row.severity === key) })));
const current = computed(() => props.run?.validity === "current" && meta.value?.validity === "current");
const path = computed(() => `/bid-reviews/${encodeURIComponent(props.run.id)}/findings`);
const activeMember = computed(() => props.authority?.taskState === "active" && props.authority.member?.active);
const canClassify = computed(() => props.authority?.taskState === "active" && props.authority.role === "admin");
function canDecide(row) { return current.value && activeMember.value && ["owner", "contributor", "reviewer"].includes(props.authority.member.role) && props.authority.member.review_domains?.includes(row.review_domain) && ((props.authority.role === "bidder" && row.review_domain === "commercial") || (props.authority.role === "technical" && row.review_domain === "technical")); }
const canReadHistory = computed(() => ["admin", "bidder", "technical", "viewer"].includes(props.authority?.role) && (current.value || ["admin", "bidder"].includes(props.authority?.role)));
let epoch = 0, historyEpoch = 0, controller;
const seenCursors = new Set(), seenHistoryCursors = new Set();
function invalid() { return new ApiError(502, "invalid_response", "发现项响应范围无法核验"); }
function validatePage(response, maximum = 50) {
  if (response.data.review_id !== props.run.id || response.data.input_hash !== props.run.input_hash || !["current", "stale"].includes(response.data.validity) || response.items.length > maximum || !(response.data.next_cursor === null || typeof response.data.next_cursor === "string")) throw invalid();
  return response;
}
async function read(more = false) {
  const token = epoch, query = new URLSearchParams({ limit: "50" });
  for (const [key, value] of Object.entries({ severity: severity.value, state: state.value, outcome: outcome.value })) if (value) query.set(key, value);
  if (more && cursor.value) query.set("cursor", cursor.value);
  const response = await orgRequest("GET", `${path.value}?${query}`, undefined, { signal: controller?.signal });
  if (token !== epoch) return;
  validatePage(response);
  const known = new Set(more ? findings.value.map(row => row.id) : []);
  for (const row of response.items) { if (row.review_id !== props.run.id || row.task_id !== props.taskId || known.has(row.id) || !["fatal", "high", "medium"].includes(row.severity) || !["open", "dismissed", "confirmed"].includes(row.state) || !Number.isInteger(row.revision) || row.revision < 1) throw invalid(); known.add(row.id); }
  if (response.data.next_cursor && seenCursors.has(response.data.next_cursor)) throw invalid();
  if (response.data.next_cursor) seenCursors.add(response.data.next_cursor);
  findings.value = more ? [...findings.value, ...response.items] : response.items; meta.value = response.data; cursor.value = response.data.next_cursor;
}
function closeDialog() { dialog.value = null; reason.value = ""; domain.value = "commercial"; }
function closeHistory() { historyEpoch++; history.value = null; historyRows.value = []; historyCursor.value = null; seenHistoryCursors.clear(); }
async function load() {
  const token = ++epoch; controller?.abort(); controller = new AbortController(); closeDialog(); closeHistory(); seenCursors.clear(); findings.value = []; meta.value = null; cursor.value = null; error.value = ""; busy.value = true;
  try { await read(); } catch (exc) { if (token === epoch && exc.name !== "AbortError") error.value = reviewError(exc); }
  finally { if (token === epoch) busy.value = false; }
}
async function more() { const token = epoch; busy.value = true; try { await read(true); } catch (exc) { if (token === epoch && exc.name !== "AbortError") error.value = reviewError(exc); } finally { if (token === epoch) busy.value = false; } }
function openDialog(row, action) { closeHistory(); reason.value = ""; domain.value = row.review_domain ?? "commercial"; dialog.value = { row, action }; }
async function mutate() {
  const fixed = dialog.value, token = epoch;
  if (!fixed || !reason.value.trim() || !current.value || (fixed.action === "classify" ? !canClassify.value : !canDecide(fixed.row))) return;
  const body = { request_id: crypto.randomUUID(), reason: reason.value.trim(), expected_revision: fixed.row.revision, expected_input_hash: meta.value.input_hash, expected_decision_id: fixed.row.latest_decision_id };
  if (fixed.action === "classify") body.review_domain = domain.value; else body.action = fixed.action;
  busy.value = true; error.value = "";
  try {
    await orgRequest("POST", `${path.value}/${encodeURIComponent(fixed.row.id)}/${fixed.action === "classify" ? "classification" : "decisions"}`, body, { signal: controller?.signal });
    if (token === epoch) await load();
  } catch (exc) {
    if (token === epoch && exc.name !== "AbortError") {
      error.value = exc.status === 409 ? "发现项修订或输入已变化，请刷新发现项后重新审查；此次操作未写入。" : reviewError(exc);
      if (exc.status === 409) { closeDialog(); meta.value = null; }
    }
  } finally { if (token === epoch) busy.value = false; }
}
async function readHistory(more = false) {
  const token = epoch, historyToken = historyEpoch, fixed = history.value, query = new URLSearchParams({ limit: "50" });
  if (!fixed) return; if (more && historyCursor.value) query.set("cursor", historyCursor.value);
  const response = await orgRequest("GET", `${path.value}/${encodeURIComponent(fixed.row.id)}/${fixed.section}?${query}`, undefined, { signal: controller?.signal });
  if (token !== epoch || historyToken !== historyEpoch) return;
  validatePage(response);
  const known = new Set(more ? historyRows.value.map(row => row.id) : []);
  for (const row of response.items) { if (row.finding_id !== fixed.row.id || row.review_id !== props.run.id || known.has(row.id)) throw invalid(); known.add(row.id); }
  if (response.data.next_cursor && seenHistoryCursors.has(response.data.next_cursor)) throw invalid();
  if (response.data.next_cursor) seenHistoryCursors.add(response.data.next_cursor);
  historyRows.value = more ? [...historyRows.value, ...response.items] : response.items; historyCursor.value = response.data.next_cursor;
}
async function showHistory(row, section) { closeDialog(); closeHistory(); history.value = { row, section }; await moreHistory(false); }
async function moreHistory(more = true) { const token = epoch; busy.value = true; try { await readHistory(more); } catch (exc) { if (token === epoch && exc.name !== "AbortError") { closeHistory(); error.value = reviewError(exc); } } finally { if (token === epoch) busy.value = false; } }
const citationPosition = citation => `${citation.page_label === "rendered_docx" ? "固定转换第" : "原件第"} ${citation.page} 页${citation.location?.section_path?.length ? ` · ${citation.location.section_path.join(" / ")}` : ""}${citation.location?.block_id ? ` · 结构块 ${citation.location.block_id}` : ""}`;
function basisLabel(basis) { if (!basis) return "依据未披露"; return `${({ rule: "规则", model: "模型" })[basis.kind] ?? "辅助检验"} · ${basis.rule_or_prompt_version}${basis.model ? ` · ${basis.model}` : ""}${basis.confidence !== null && basis.confidence !== undefined ? ` · 模型置信度 ${basis.confidence}（不代表准确率）` : ""}`; }
watch(() => [props.run.id, props.run.input_hash, props.run.validity, props.authorizationEpoch, props.authority], load, { immediate: true });
watch([severity, state, outcome], load);
window.addEventListener("bid:org-reset", load);
onBeforeUnmount(() => { epoch++; controller?.abort(); closeDialog(); closeHistory(); findings.value = []; window.removeEventListener("bid:org-reset", load); });
</script>
<template>
  <section class="section" aria-label="标书发现项">
    <div class="page-heading"><h4>发现项与人工审查</h4><el-button :disabled="busy" @click="load">刷新发现项</el-button></div>
    <p>评标委员会决定最终评审结果；本报告仅供辅助审查。人工决定保留机器原始结论，不确认材料证据，也不改变评分。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" />
    <el-alert v-if="meta?.validity === 'stale' || run.validity === 'stale'" title="输入或授权已变化，当前结果仅供历史查阅，不能作出人工决定。" type="warning" :closable="false" />
    <el-form inline class="section">
      <el-form-item label="风险"><el-select v-model="severity" aria-label="风险筛选" :disabled="busy" style="width:160px"><el-option label="全部风险" value="" /><el-option v-for="key in ['fatal','high','medium']" :key="key" :label="labels[key]" :value="key" /></el-select></el-form-item>
      <el-form-item label="状态"><el-select v-model="state" aria-label="发现项状态筛选" :disabled="busy" style="width:160px"><el-option label="全部状态" value="" /><el-option v-for="key in ['open','dismissed','confirmed']" :key="key" :label="labels[key]" :value="key" /></el-select></el-form-item>
      <el-form-item label="响应"><el-select v-model="outcome" aria-label="响应筛选" :disabled="busy" style="width:160px"><el-option label="全部响应" value="" /><el-option v-for="key in ['responded','deviation','missing','unknown']" :key="key" :label="labels[key]" :value="key" /></el-select></el-form-item>
    </el-form>
    <template v-for="group in groups" :key="group.key">
      <h4>{{ group.label }}</h4><p v-if="!group.rows.length" class="hint">本页无此类发现项</p>
      <el-card v-for="row in group.rows" :key="row.id" shadow="never" class="section" data-testid="bid-finding">
        <template #header><strong>{{ row.title ?? row.code }}</strong> · {{ labels[row.state] }} · {{ labels[row.outcome] }} · {{ labels[row.review_domain] ?? '未分类' }}</template>
        <p>机器原始结论：{{ labels[row.outcome] }} · {{ labels[row.impact] ?? '影响未确定' }} · {{ basisLabel(row.basis) }}</p>
        <p v-if="row.explanation">{{ row.explanation }}</p>
        <template v-if="row.tender_support?.length"><h5>招标依据</h5><div v-for="(citation,index) in row.tender_support" :key="index"><p>招标{{ citationPosition(citation) }}</p><blockquote>{{ citation.quote }}</blockquote></div></template>
        <template v-if="row.bid_support?.length"><h5>投标响应</h5><div v-for="(citation,index) in row.bid_support" :key="index"><p>投标{{ citationPosition(citation) }}</p><blockquote>{{ citation.quote }}</blockquote></div></template>
        <template v-if="row.absence_search"><h5>缺失检索范围</h5><p>{{ ({ required_locations: '已检索指定位置', all_bid_pages: '已检索全部投标页', complete_inventory: '已核对固定提交文件清单', partial: '部分检索，结论未确定' })[row.absence_search.coverage] }}</p><p v-for="(page,index) in row.absence_search.searched_pages" :key="index">投标第 {{ page.page }} 页 · <code>{{ page.document_id }}</code></p><template v-if="row.absence_search.kind === 'submission_inventory'"><p>需提交材料：{{ row.absence_search.required_document_description }}</p><p>提交清单哈希 <code>{{ row.absence_search.submission_manifest_sha256 }}</code></p><p v-for="id in row.absence_search.inspected_bid_document_ids" :key="id">已核对文件 <code>{{ id }}</code></p></template><p v-for="code in row.absence_search.limitation_codes" :key="code">{{ reviewError({code}) }}</p></template>
        <template v-if="row.rule_evidence?.kind === 'pdf_signature_validation'"><h5>本地签名校验依据</h5><p v-for="(id,index) in row.rule_evidence.document_ids" :key="id">投标文件 <code>{{ id }}</code> · 校验记录 <code>{{ row.rule_evidence.validation_ids[index] }}</code></p></template>
        <p v-if="row.remediation">补救建议：{{ row.remediation }}</p><p v-for="code in row.limitation_codes" :key="code">检验限制：{{ reviewError({ code }) }}</p>
        <p class="hint">当前决定：{{ labels[row.state] }} · 修订 {{ row.revision }}<span v-if="row.latest_decision_id"> · <code>{{ row.latest_decision_id }}</code></span></p><p v-if="row.reviewed_basis?.kind === 'human_reviewed'">人工核对已确认 · 决定 <code>{{ row.reviewed_basis.human_decision_id }}</code></p>
        <div class="actions">
          <template v-if="canDecide(row)"><el-button v-if="row.state === 'open'" :disabled="busy" @click="openDialog(row,'dismiss')">驳回发现项</el-button><el-button v-if="row.state === 'open'" type="primary" :disabled="busy" @click="openDialog(row,'confirm')">确认发现项</el-button><el-button v-if="row.state !== 'open'" :disabled="busy" @click="openDialog(row,'reopen')">重新打开发现项</el-button></template>
          <el-button v-if="current && canClassify && row.state === 'open'" :disabled="busy" @click="openDialog(row,'classify')">分类发现项</el-button>
          <el-button v-if="canReadHistory" :disabled="busy" @click="showHistory(row,'decisions')">决定历史</el-button><el-button v-if="canReadHistory && canClassify" :disabled="busy" @click="showHistory(row,'classification')">分类历史</el-button>
        </div><p v-if="!row.review_domain" class="hint">需管理员先分类，再由对应职责的人工审查人决定。</p>
      </el-card>
    </template>
    <el-button v-if="cursor" :loading="busy" @click="more">更多发现项</el-button>
    <el-dialog :model-value="Boolean(dialog)" :title="`${labels[dialog?.action] ?? ''}发现项`" width="min(560px, 94vw)" :close-on-click-modal="false" @update:model-value="closeDialog">
      <template v-if="dialog"><p>{{ dialog.row.title ?? dialog.row.code }} · 当前修订 {{ dialog.row.revision }}</p><p v-if="dialog.action === 'classify'">分类只记录专业职责，不授予管理员作出审查决定的权限。</p>
        <el-form label-position="top"><el-form-item v-if="dialog.action === 'classify'" label="专业职责"><el-select v-model="domain" aria-label="发现项专业职责" :disabled="busy"><el-option label="商务" value="commercial" /><el-option label="技术" value="technical" /></el-select></el-form-item><el-form-item label="审查理由（必填）"><el-input v-model="reason" aria-label="发现项审查理由" type="textarea" :rows="4" maxlength="20000" :disabled="busy" /></el-form-item></el-form>
      </template><template #footer><el-button :disabled="busy" @click="closeDialog">取消</el-button><el-button type="primary" :loading="busy" :disabled="!reason.trim()" @click="mutate">保存{{ labels[dialog?.action] }}</el-button></template>
    </el-dialog>
    <el-dialog :model-value="Boolean(history)" :title="history?.section === 'classification' ? '分类历史' : '决定历史'" width="min(760px, 94vw)" @update:model-value="closeHistory">
      <el-table :data="historyRows" aria-label="发现项历史"><el-table-column label="动作"><template #default="{row}">{{ labels[row.action] ?? '分类' }} · {{ labels[row.review_domain] }}</template></el-table-column><el-table-column label="审查理由" prop="reason" /><el-table-column label="时间"><template #default="{row}">{{ formatTime(row.decided_at) }}</template></el-table-column><el-table-column label="审查人"><template #default="{row}"><code>{{ row.decided_by }}</code></template></el-table-column><el-table-column label="修订" prop="revision" /></el-table><el-button v-if="historyCursor" :loading="busy" @click="moreHistory">更多历史记录</el-button>
    </el-dialog>
  </section>
</template>
<style scoped>blockquote { margin:8px 0; white-space:pre-wrap; overflow-wrap:anywhere; } code { overflow-wrap:anywhere; } h4 { margin:16px 0; } h5 { margin:12px 0 4px; } .actions { flex-wrap:wrap; }</style>
