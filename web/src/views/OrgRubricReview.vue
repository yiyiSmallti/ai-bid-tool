<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import AssessmentCitation from "../components/AssessmentCitation.vue";
import RubricReplacement from "../components/RubricReplacement.vue";
import { assessmentError, domainLabels, enc, queryPath, stateLabels, useAssessmentPage, useUnsaved } from "../assessments.js";
import { formatTime, orgRequest } from "../org.js";
const route = useRoute(), router = useRouter();
const taskId = computed(() => String(route.params.taskId)), rubricId = computed(() => String(route.params.rubricId)), base = computed(() => `/tasks/${enc(taskId.value)}/score-rubrics/${enc(rubricId.value)}`);
const summary = ref(null), taskName = ref(''), error = ref(''), loading = ref(false), part = ref('sections'), domain = ref(''), state = ref(''), groupId = ref('');
const filters = computed(() => ({ part: part.value, ...(['sections', 'items'].includes(part.value) ? { domain: domain.value, state: state.value } : {}), ...(part.value === 'blockers' ? { group_id: groupId.value } : {}) }));
const listing = useAssessmentPage(base, filters, refreshSummary), { rows, meta, error: pageError, loading: pageLoading, position, next } = listing;
const dialog = ref(false), action = ref(null), reason = ref(''), reasonInput = ref(null), decisionError = ref(''), busy = ref(false), selectedDomain = ref('commercial');
const coverageAction = ref('mapped'), itemIds = ref([]), candidateItems = ref([]), itemCursor = ref(null), canonicalId = ref(''), canonical = ref(null), canonicalItems = ref([]), canonicalItemCursor = ref(null), coverageCandidates = ref([]), coverageCursor = ref(null), selectionsReady = ref(false);
const historyOpen = ref(false), historyItems = ref([]), historyCursor = ref(null), replacementOpen = ref(false), replacementDirty = ref(false), proposal = ref(null);
let sequence = 0;
function clearCoverage() { candidateItems.value = []; canonical.value = null; canonicalItems.value = []; coverageCandidates.value = []; itemIds.value = []; canonicalId.value = ""; itemCursor.value = null; coverageCursor.value = null; canonicalItemCursor.value = null; selectionsReady.value = false; }
const dirty = computed(() => !!reason.value.trim() || replacementDirty.value);
useUnsaved(dirty, () => { sequence++; summary.value = null; dialog.value = false; reason.value = ''; replacementOpen.value = false; historyItems.value = []; proposal.value = null; clearCoverage(); });
const readonly = computed(() => summary.value?.state === 'superseded' || summary.value?.validity !== 'current');
const can = (name) => !readonly.value && summary.value?.actions?.some(a => a.action === name && a.allowed);
const canRow = (row, name) => !readonly.value && meta.value?.subject_actions?.find(s => s.subject_id === row.id)?.actions.some(a => a.action === name && a.allowed);
const aggregationLabels = { sum: '加总', weighted_sum: '加权合计', capped_sum: '封顶合计', formula: '公式（不能自动合计）', non_additive: '非加总规则（不能自动合计）' };
const modeLabels = { model_assessable: '可由模型评估', ambiguous: '表述存在歧义', price_comparison: '价格比较', external_comparison: '外部比较', manual_only: '需人工评估', unsupported_formula: '不支持的公式' };
const coverageLabels = { pending: '待核对', mapped: '已对应评分项', duplicate: '已标记重复要求', excluded: '已明确排除' };
async function refreshSummary() {
  const current = sequence;
  const result = await orgRequest('GET', queryPath(base.value, { view: 'console', part: 'summary' }));
  if (current !== sequence) return;
  if (result.data.task_id !== taskId.value || result.data.id !== rubricId.value) throw { status: 404 };
  summary.value = result.data;
}
async function load() { const current = ++sequence; loading.value = true; error.value = ''; summary.value = null; proposal.value = null; dialog.value = false; reason.value = ''; clearCoverage(); try { await refreshSummary(); if (current !== sequence) return; taskName.value = (await orgRequest('GET', `/tasks/${enc(taskId.value)}`)).data.name; await listing.load(); } catch (exc) { if (exc.name !== 'AbortError' && current === sequence) error.value = assessmentError(exc); } finally { if (current === sequence) loading.value = false; } }
async function loadItems(cursor = null, canonicalRequirement = null) {
  const current = sequence, subject = action.value?.row?.id;
  const result = await orgRequest('GET', queryPath(base.value, { view: 'console', part: 'items', requirement_id: canonicalRequirement ?? action.value.row.requirement_id, limit: 50, cursor }));
  if (current !== sequence || subject !== action.value?.row?.id) return;
  if (result.data.parent_revision !== summary.value.revision) throw { code: 'assessment_view_changed' };
  if (canonicalRequirement) { canonicalItems.value = result.items; canonicalItemCursor.value = result.data.next_cursor; }
  else { candidateItems.value = cursor ? [...candidateItems.value, ...result.items] : result.items; itemCursor.value = result.data.next_cursor; }
}
async function loadCoverageCandidates(cursor = null) {
  const current = sequence, subject = action.value?.row?.id;
  const result = await orgRequest('GET', queryPath(base.value, { view: 'console', part: 'coverage', limit: 50, cursor }));
  if (current !== sequence || subject !== action.value?.row?.id) return;
  if (result.data.parent_revision !== summary.value.revision) throw { code: 'assessment_view_changed' };
  coverageCandidates.value = result.items.filter(r => r.requirement_id !== action.value.row.requirement_id); coverageCursor.value = result.data.next_cursor;
}
async function chooseCanonical() {
  const current = sequence, selected = canonicalId.value, subject = action.value?.row?.id;
  canonical.value = null; canonicalItems.value = []; selectionsReady.value = false;
  if (!selected) return;
  try {
    const result = await orgRequest('GET', queryPath(base.value, { view: 'console', part: 'coverage', requirement_id: selected, limit: 1 }));
    if (current !== sequence || selected !== canonicalId.value || subject !== action.value?.row?.id) return;
    if (result.data.parent_revision !== summary.value.revision || !result.items[0]) throw { code: 'assessment_view_changed' };
    canonical.value = result.items[0]; await loadItems(null, selected);
    if (current === sequence && selected === canonicalId.value) selectionsReady.value = true;
  } catch (exc) { if (exc.name !== 'AbortError' && current === sequence) decisionError.value = assessmentError(exc); }
}
async function safeMore(callback) { try { await callback(); } catch (exc) { if (exc.name !== 'AbortError') { decisionError.value = assessmentError(exc); selectionsReady.value = false; } } }
async function open(row, operation, decisionAction) {
  clearCoverage(); action.value = { row, operation, decisionAction, part: part.value }; reason.value = ''; decisionError.value = ''; dialog.value = true; selectedDomain.value = row?.review_domain ?? 'commercial';
  coverageAction.value = 'mapped'; itemIds.value = []; canonicalId.value = ''; canonical.value = null; selectionsReady.value = false;
  const request = sequence;
  if (operation === 'coverage') {
    busy.value = true;
    try { await Promise.all([loadItems(), loadCoverageCandidates()]); if (request === sequence && action.value?.row?.id === row.id) selectionsReady.value = true; }
    catch (exc) { decisionError.value = assessmentError(exc); } finally { busy.value = false; }
  }
  await nextTick(); reasonInput.value?.focus();
}
async function submit() {
  if (!reason.value.trim()) { decisionError.value = '请填写处理理由'; reasonInput.value?.focus(); return; }
  if (!action.value || readonly.value) return;
  const current = action.value, row = current.row;
  const body = { expected_revision: row?.revision ?? summary.value.revision, expected_input_hash: summary.value.input_hash, reason: reason.value.trim() };
  let path = base.value;
  if (current.operation === 'classify') { path += `/${current.part}/${enc(row.id)}/classification`; body.review_domain = selectedDomain.value; }
  else if (current.operation === 'coverage') {
    path += `/coverage/${enc(row.requirement_id)}/decisions`; body.action = coverageAction.value; body.rubric_item_ids = coverageAction.value === 'mapped' ? itemIds.value : []; body.canonical_requirement_id = coverageAction.value === 'duplicate' ? canonicalId.value : null;
    if ((body.action === 'mapped' && !body.rubric_item_ids.length) || (body.action === 'duplicate' && (!canonical.value || !selectionsReady.value))) { decisionError.value = '请先核对目标评分项或规范要求原文'; return; }
  } else { path += current.operation === 'set' ? '?view=console' : `/${current.part}/${enc(row.id)}`; path = current.operation === 'set' ? `${base.value}/decisions?view=console` : `${path}/decisions`; body.action = current.decisionAction; }
  busy.value = true; decisionError.value = '';
  try { await orgRequest('POST', path, body); dialog.value = false; reason.value = ''; await refreshSummary(); await listing.load(); if (historyOpen.value) await loadHistory(); }
  catch (exc) {
    if (exc.name === 'AbortError') return; decisionError.value = assessmentError(exc);
    if (['revision_conflict', 'assessment_view_changed', 'invalid_transition', 'rubric_superseded'].includes(exc.code)) {
      try { await refreshSummary(); await listing.load(); if (row) { const result = await orgRequest('GET', queryPath(base.value, { view: 'console', part: current.part, entry_id: row.id })); if (result.items[0]) action.value = { ...current, row: result.items[0] }; } } catch (reload) { decisionError.value += `；${assessmentError(reload)}`; }
    }
  } finally { busy.value = false; }
}
async function loadHistory(cursor = null) {
  const current = sequence; historyOpen.value = true;
  try { const result = await orgRequest('GET', queryPath(`${base.value}/history`, { limit: 50, cursor })); if (current !== sequence) return; historyItems.value = result.items; historyCursor.value = result.data.next_cursor; }
  catch (exc) { if (exc.name !== 'AbortError' && current === sequence) error.value = assessmentError(exc); }
}
async function loadProposal() {
  const current = sequence;
  try { const result = await orgRequest('GET', queryPath(base.value, { view: 'console', part: 'replacement' })); if (current === sequence) proposal.value = result.data; }
  catch (exc) { if (exc.name !== 'AbortError' && current === sequence) error.value = exc.status === 404 ? '此评分规则没有已保存的修订提议' : assessmentError(exc); }
}
function useProposal(row) {
  const request = sequence;
  const entry = proposal.value?.replacement.coverage.find(c => c.requirement_id === row.requirement_id);
  if (!entry || row.disposition !== 'pending') return;
  open(row, 'coverage').then(() => {
    if (request !== sequence || !dialog.value || action.value?.row?.id !== row.id) return;
    coverageAction.value = entry.disposition === 'pending' ? 'mapped' : entry.disposition;
    itemIds.value = candidateItems.value.filter(i => entry.rubric_item_keys.includes(i.key)).map(i => i.id);
    canonicalId.value = entry.canonical_requirement_id ?? ''; reason.value = entry.reason ?? '';
    if (coverageAction.value === 'duplicate') chooseCanonical();
  });
}
function replacementSaved(value) { replacementDirty.value = false; replacementOpen.value = false; router.push(`/org/tasks/${taskId.value}/score-rubrics/${value.id}`); }
watch(filters, () => listing.load()); watch(() => [taskId.value, rubricId.value], load, { immediate: true });
</script>
<template>
  <nav class="breadcrumb" aria-label="位置"><RouterLink :to="`/org/tasks/${taskId}`">{{ taskName || '任务' }}</RouterLink><span>/</span><RouterLink :to="`/org/tasks/${taskId}/score-rubrics?job=${summary?.extraction_job_id ?? ''}`">评分规则</RouterLink></nav>
  <header class="page-header"><div><h2>评分规则审阅</h2><p>已核对评分规则与总分是否可计算是两个独立状态。评分仅评估已保存初稿。</p></div><el-button @click="load">重新读取规则</el-button></header>
  <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" /><el-skeleton v-if="loading" :rows="4" animated />
  <template v-if="summary">
    <p>第 {{ summary.version }} 版 · 修订 {{ summary.revision }} · {{ stateLabels[summary.state] }} · {{ stateLabels[summary.validity] }} · 提取 {{ summary.extraction_job_id }}</p>
    <p v-if="summary.prior_rubric_id"><RouterLink :to="`/org/tasks/${taskId}/score-rubrics/${summary.prior_rubric_id}`">查看前一版 {{ summary.prior_rubric_id }}</RouterLink></p>
    <el-alert v-if="readonly" :title="summary.state === 'superseded' ? '此评分规则已被新版本替代，仅供追溯' : '评分要求已变化，请重新生成并审核规则'" type="warning" :closable="false" show-icon role="alert"><RouterLink :to="`/org/tasks/${taskId}/score-rubrics?job=${summary.extraction_job_id}`">查看评分规则版本</RouterLink></el-alert>
    <el-card class="section" shadow="never"><h3>审核清单与下一步</h3><ol><li>分配审核职责 — 管理员；分节分类不会自动分类条目。</li><li>核对评分要求覆盖 — 商务负责人（投标专员）；覆盖 {{ summary.completeness.covered_requirement_count }} / {{ summary.completeness.scoring_requirement_count }}，待核对 {{ summary.completeness.pending_requirements }}。</li><li>审核分节和条目 — 商务负责人（投标专员）或技术负责人；待确认分节 {{ summary.completeness.unconfirmed_sections }}，条目 {{ summary.completeness.unconfirmed_items }}。</li><li>确认整套评分规则 — 商务负责人（投标专员）；重复组 {{ summary.completeness.duplicate_groups }}，规范化问题 {{ summary.completeness.normalization_errors }}。</li></ol><p>分节合计规则：{{ summary.completeness.section_aggregation_rules_confirmed ? '已核对' : '待核对' }} · 总分规则：{{ summary.completeness.overall_aggregation_rule_confirmed ? '已核对' : '待核对' }}</p>
      <p>总分规则：{{ aggregationLabels[summary.overall_aggregation] }} · {{ summary.overall_rule_text }}</p><p v-if="summary.overall_score_range">总分约定范围 {{ summary.overall_score_range.minimum }}–{{ summary.overall_score_range.maximum }}</p><p v-if="summary.overall_cap != null">总分封顶 {{ summary.overall_cap }}</p><p v-if="!summary.overall_aggregation_assessable">规则已核对后，系统仍无法自动合计。</p>
      <div class="actions"><el-button v-if="summary.state === 'candidate'" type="primary" :disabled="!can('rubric_confirm') || !summary.completeness.complete" @click="open(null, 'set', 'confirm')">确认整套评分规则</el-button><el-button v-if="summary.state === 'confirmed'" :disabled="!can('rubric_reopen')" @click="open(null, 'set', 'reopen')">重新打开整套规则</el-button><el-button :disabled="!can('rubric_revise')" @click="replacementOpen = true">完整修订评分规则</el-button><el-button @click="loadHistory()">查看审核历史</el-button><el-button v-if="summary.prior_rubric_id" @click="loadProposal">恢复已保存的覆盖提议</el-button><RouterLink v-if="summary.state === 'confirmed'" :to="`/org/tasks/${taskId}/scores?job=${summary.extraction_job_id}&rubric=${rubricId}`">预览评分</RouterLink></div>
      <p v-if="summary.state === 'confirmed'">修改子项前，须由商务负责人重新打开整套规则。</p>
    </el-card>
    <el-tabs v-model="part" aria-label="评分规则审阅分区"><el-tab-pane label="审核分节" name="sections" /><el-tab-pane label="审核条目" name="items" /><el-tab-pane label="核对评分要求覆盖" name="coverage" /><el-tab-pane label="待处理问题" name="blockers" /></el-tabs>
    <div v-if="['sections', 'items'].includes(part)" class="actions"><el-select v-model="domain" aria-label="负责职责" clearable placeholder="全部职责"><el-option v-for="(label, value) in domainLabels" :key="value" :label="label" :value="value" /></el-select><el-select v-model="state" aria-label="审核状态" clearable placeholder="全部状态"><el-option v-for="value in ['candidate','confirmed','rejected']" :key="value" :value="value" :label="stateLabels[value]" /></el-select></div>
    <el-button v-if="groupId && part === 'blockers'" @click="groupId = ''">返回全部问题组</el-button>
    <p v-if="meta" role="status" aria-live="polite">匹配 {{ meta.filtered_total }} / 全部 {{ meta.total }} 条；第 {{ position + 1 }} 页，本页 {{ meta.returned }} 条</p><el-alert v-if="pageError" :title="pageError" type="error" :closable="false" show-icon role="alert" /><el-skeleton v-if="pageLoading" :rows="3" animated /><el-empty v-else-if="!rows.length && !pageError" description="当前筛选下没有记录" />
    <el-card v-for="row in rows" :key="row.id ?? `${row.code}-${row.subject_id}`" class="section" shadow="never">
      <template v-if="part === 'blockers'"><p>{{ row.message }}（{{ row.code }}）</p><p v-if="row.group_id">重复组 {{ row.group_id }} · 共 {{ row.group_member_count }} 个成员 <el-button @click="groupId = row.group_id">查看该组全部成员</el-button></p><RouterLink v-if="row.requirement_id" :to="`/org/tasks/${taskId}/review?job=${summary.extraction_job_id}&requirement=${row.requirement_id}`">前往响应卡修改</RouterLink></template>
      <template v-else>
        <h3>{{ row.title ?? `评分要求 ${row.requirement_id}` }}</h3><p v-if="part !== 'coverage'">{{ domainLabels[row.review_domain ?? 'unclassified'] }} · {{ stateLabels[row.state] }} · 修订 {{ row.revision }}</p><p v-else>{{ coverageLabels[row.disposition] }} · 修订 {{ row.revision }} · {{ row.reason }}</p>
        <blockquote class="quote">{{ row.source.quote }}</blockquote><AssessmentCitation :task-id="taskId" parent-kind="rubric" :parent-id="rubricId" :part="part === 'sections' ? 'rubric_section' : part === 'items' ? 'rubric_item' : 'coverage'" :entry-id="row.id" />
        <template v-if="part === 'sections'"><p>分节规则：{{ aggregationLabels[row.aggregation] }} · {{ row.aggregation_rule_text }}</p><p>进入总分：{{ row.included_in_overall_total ? '是' : '否' }} · 权重 {{ row.weight ?? '未设置' }} · 封顶 {{ row.cap ?? '未设置' }}</p><p v-if="!row.aggregation_assessable">规则已核对后，系统仍无法自动合计。</p></template>
        <template v-if="part === 'items'"><p>{{ row.rule_text }}</p><p>评估方式：{{ modeLabels[row.assessment_mode] }} · 权重 {{ row.weight ?? '未设置' }}</p></template>
        <p v-if="row.score_range">约定范围 {{ row.score_range.minimum }}–{{ row.score_range.maximum }}</p><p v-if="row.ambiguity_reason">无法评估或歧义原因：{{ row.ambiguity_reason }}</p>
        <template v-if="part === 'coverage'"><p v-if="row.rubric_item_ids.length">对应评分项 {{ row.rubric_item_ids.join('、') }}</p><p v-if="row.canonical_requirement_id">规范要求 {{ row.canonical_requirement_id }}</p><el-button :disabled="!canRow(row, 'rubric_coverage_decide')" @click="open(row, 'coverage')">核对覆盖</el-button><el-button v-if="proposal && row.disposition === 'pending'" :disabled="!canRow(row, 'rubric_coverage_decide')" @click="useProposal(row)">核对已保存提议</el-button></template>
        <div v-else class="actions"><el-button v-if="canRow(row, 'rubric_classify')" @click="open(row, 'classify')">分配职责</el-button><template v-if="canRow(row, part === 'sections' ? 'rubric_section_decide' : 'rubric_item_decide')"><el-button v-if="row.state !== 'confirmed'" @click="open(row, 'decision', 'confirm')">{{ part === 'sections' ? '确认分节' : '确认条目' }}</el-button><el-button v-if="row.state !== 'rejected'" @click="open(row, 'decision', 'reject')">驳回</el-button><el-button v-if="row.state !== 'candidate'" @click="open(row, 'decision', 'reopen')">重新打开</el-button></template></div>
      </template>
    </el-card>
    <div class="actions"><el-button :disabled="position === 0 || pageLoading" @click="listing.back">上一页</el-button><el-button :disabled="!next || pageLoading" @click="listing.forward">下一页</el-button></div>
    <el-card v-if="historyOpen" class="section" shadow="never"><h3>评分规则审核历史</h3><p v-if="!historyItems.length">尚无审核历史</p><ol><li v-for="event in historyItems" :key="event.id">{{ event.kind }} · {{ event.action ?? event.review_domain ?? '完整修订' }} · 修订 {{ event.revision ?? event.version }} · {{ event.reason }} · {{ event.decided_by ?? event.revised_by }} · {{ formatTime(event.decided_at ?? event.revised_at) }}</li></ol><el-button v-if="historyCursor" @click="loadHistory(historyCursor)">更多审核历史</el-button></el-card>
    <RubricReplacement v-if="replacementOpen" :task-id="taskId" :rubric-id="rubricId" :summary="summary" @dirty="replacementDirty = $event" @saved="replacementSaved" @close="replacementOpen = false; replacementDirty = false" />
  </template>
  <el-dialog v-model="dialog" title="评分规则处理" width="min(720px, 94vw)" :close-on-click-modal="false" @opened="reasonInput?.focus()">
    <p>{{ action?.row?.title ?? action?.row?.source.quote ?? '整套评分规则' }} · 修订 {{ action?.row?.revision ?? summary?.revision }}</p><el-alert v-if="decisionError" :title="decisionError" type="error" :closable="false" role="alert" id="rubric-decision-error" />
    <el-form label-position="top" @submit.prevent="submit">
      <el-form-item v-if="action?.operation === 'classify'" label="审核职责"><el-select v-model="selectedDomain" aria-label="审核职责"><el-option value="commercial" label="商务负责人（投标专员）" /><el-option value="technical" label="技术负责人" /></el-select></el-form-item>
      <template v-if="action?.operation === 'coverage'">
        <el-form-item label="覆盖决定"><el-select v-model="coverageAction" aria-label="覆盖决定"><el-option value="mapped" label="对应评分项" /><el-option value="duplicate" label="重复要求" /><el-option value="excluded" label="有理由地排除" /><el-option value="reopen" label="重新打开覆盖" /></el-select></el-form-item>
        <template v-if="coverageAction === 'mapped'"><el-checkbox-group v-model="itemIds" aria-label="选择对应评分项"><div v-for="item in candidateItems" :key="item.id"><el-checkbox :value="item.id">{{ item.title }} · {{ item.rule_text }}</el-checkbox></div></el-checkbox-group><el-button v-if="itemCursor" @click="safeMore(() => loadItems(itemCursor))">更多目标评分项</el-button><p v-if="!candidateItems.length">该要求没有评分项，请先完整修订规则。</p></template>
        <template v-if="coverageAction === 'duplicate'"><el-select v-model="canonicalId" aria-label="规范要求" @change="chooseCanonical"><el-option v-for="entry in coverageCandidates" :key="entry.requirement_id" :value="entry.requirement_id" :label="entry.source.quote" /></el-select><el-button v-if="coverageCursor" @click="safeMore(() => loadCoverageCandidates(coverageCursor))">更多规范要求</el-button><blockquote v-if="canonical" class="quote">{{ canonical.source.quote }}</blockquote><p v-for="item in canonicalItems" :key="item.id">规范要求目标项：{{ item.title }} · {{ item.rule_text }}</p><el-button v-if="canonicalItemCursor" @click="safeMore(() => loadItems(canonicalItemCursor, canonicalId))">更多规范要求目标项</el-button></template>
      </template>
      <el-form-item label="处理理由" required><el-input ref="reasonInput" v-model="reason" type="textarea" :rows="4" aria-label="处理理由" aria-describedby="rubric-decision-error" /></el-form-item><el-button native-type="submit" type="primary" :loading="busy" :disabled="readonly">提交决定</el-button><el-button @click="dialog = false; reason = ''">取消</el-button>
    </el-form>
  </el-dialog>
</template>
<style scoped>.quote { white-space:pre-wrap; overflow-wrap:anywhere; } .actions .el-select { width:240px; }</style>
