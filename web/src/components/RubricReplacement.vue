<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { assessmentError, domainLabels, enc, queryPath } from "../assessments.js";
import { confirmAction, orgAccess, orgRequest } from "../org.js";
import AssessmentCitation from "./AssessmentCitation.vue";
const props = defineProps({ taskId: String, rubricId: String, summary: Object });
const emit = defineEmits(['dirty', 'saved', 'close']);
const baseline = ref(null), form = ref(null), graph = ref(null), error = ref(''), loading = ref(false), saving = ref(false), sourceRequirement = ref(''), validSnapshot = ref(false), active = ref('sections'), index = ref(0);
let generation = 0;
const base = computed(() => `/tasks/${enc(props.taskId)}/score-rubrics/${enc(props.rubricId)}`);
const sectionFields = ['key','title','order','aggregation','aggregation_rule_text','score_range','weight','cap','included_in_overall_total','ambiguity_reason'];
const itemFields = ['requirement_id','key','title','rule_text','order','assessment_mode','score_range','weight','ambiguity_reason'];
const overallFields = ['overall_aggregation','overall_rule_text','overall_score_range','overall_cap'];
const pick = (row, fields) => Object.fromEntries(fields.map(key => [key, row[key] ?? null]));
const clone = (value) => JSON.parse(JSON.stringify(value));
const ownDomain = computed(() => ({ bidder: 'commercial', technical: 'technical' })[orgAccess.role]);
const current = computed(() => form.value?.[active.value]?.[index.value]);
const currentSource = computed(() => graph.value?.coverage.find(row => row.requirement_id === current.value?.requirement_id)?.source);
const entries = computed(() => form.value?.[active.value] ?? []);
const dirty = computed(() => !!form.value && JSON.stringify(form.value) !== JSON.stringify(baseline.value));
watch(dirty, value => emit('dirty', value));
watch(active, () => index.value = 0);
watch([active, index], () => sourceRequirement.value = '');
const editable = computed(() => {
  const row = current.value; if (!row || !ownDomain.value) return false;
  if (active.value === 'coverage') return orgAccess.role === 'bidder' || graph.value.items.filter(i => i.requirement_id === row.requirement_id).every(i => i.review_domain === ownDomain.value);
  const original = graph.value?.[active.value].find(item => item.id === (row.source_section_id ?? row.source_item_id));
  return !original || original.review_domain === ownDomain.value;
});
const originalDomain = computed(() => { const row = current.value; return graph.value?.[active.value]?.find(item => item.id === (row?.source_section_id ?? row?.source_item_id))?.review_domain; });
const changes = computed(() => {
  if (!form.value || !baseline.value) return [];
  const output = [];
  for (const part of ['sections','items','coverage']) {
    const key = part === 'coverage' ? 'requirement_id' : 'key';
    const before = new Map(baseline.value[part].map(row => [row[key], row]));
    for (const row of form.value[part]) { const prior = before.get(row[key]); if (!prior || JSON.stringify(row) !== JSON.stringify(prior)) output.push({ part, key: row[key], action: prior ? '修改' : '新增', fields: Object.keys(row).filter(field => JSON.stringify(row[field]) !== JSON.stringify(prior?.[field])).join('、') }); before.delete(row[key]); }
    for (const [value] of before) output.push({ part, key: value, action: '移除', fields: '' });
  }
  for (const field of overallFields) if (JSON.stringify(form.value[field]) !== JSON.stringify(baseline.value[field])) output.push({ part: 'overall', key: field, action: '修改', fields: '总分规则' });
  return output;
});
const bytes = computed(() => form.value ? new TextEncoder().encode(JSON.stringify(form.value)).length : 0);
const aggregationOptions = { sum: '加总', weighted_sum: '加权合计', capped_sum: '封顶合计', formula: '公式（无法自动合计）', non_additive: '非加总规则' };
const modeOptions = { model_assessable: '可由模型评估', ambiguous: '歧义', price_comparison: '价格比较', external_comparison: '外部比较', manual_only: '仅人工评估', unsupported_formula: '不支持的公式' };
async function load() {
  const request = ++generation; loading.value = true; error.value = ''; validSnapshot.value = false;
  try {
    const header = (await orgRequest('GET', queryPath(base.value, { view: 'console', part: 'summary' }))).data;
    const loaded = {}, snapshots = new Set();
    for (const part of ['sections','items','coverage']) {
      const list = [], seenCursors = new Set(); let cursor = null, expectedTotal = null;
      do {
        const result = await orgRequest('GET', queryPath(base.value, { view: 'console', part, limit: 100, cursor }));
        if (request !== generation) return;
        if (result.data.parent_revision !== header.revision || result.data.validity !== 'current' || result.data.parent_id !== props.rubricId || result.data.task_id !== props.taskId || result.data.returned !== result.items.length) throw { code: 'assessment_view_changed' };
        if (expectedTotal !== null && expectedTotal !== result.data.total) throw { code: 'assessment_view_changed' };
        snapshots.add(result.data.snapshot); expectedTotal = result.data.total; list.push(...result.items); cursor = result.data.next_cursor;
        if (cursor && seenCursors.has(cursor)) throw { code: 'invalid_cursor' }; seenCursors.add(cursor);
      } while (cursor);
      if (list.length !== expectedTotal || new Set(list.map(r => r.id)).size !== list.length) throw { code: 'assessment_view_changed' };
      loaded[part] = list;
    }
    const latest = (await orgRequest('GET', queryPath(base.value, { view: 'console', part: 'summary' }))).data;
    if (snapshots.size !== 1 || latest.revision !== header.revision || latest.input_hash !== header.input_hash || latest.validity !== 'current' || latest.state === 'superseded') throw { code: 'assessment_view_changed' };
    if (request !== generation) return;
    graph.value = loaded;
    const sectionKeys = new Map(loaded.sections.map(row => [row.id, row.key])), itemKeys = new Map(loaded.items.map(row => [row.id, row.key]));
    const replacement = { expected_revision: header.revision, expected_input_hash: header.input_hash, ...pick(header, overallFields), reason: '', sections: loaded.sections.map(row => ({ source_section_id: row.id, sources: row.sources.map(entry => ({ requirement_id: entry.requirement_id, quote: entry.quote })), ...pick(row, sectionFields) })), items: loaded.items.map(row => ({ source_item_id: row.id, section_key: sectionKeys.get(row.section_id), ...pick(row, itemFields) })), coverage: loaded.coverage.map(row => ({ requirement_id: row.requirement_id, disposition: row.disposition, rubric_item_keys: row.rubric_item_ids.map(id => itemKeys.get(id)), canonical_requirement_id: row.canonical_requirement_id, reason: row.reason })) };
    baseline.value = clone(replacement); form.value = clone(replacement); validSnapshot.value = true;
  } catch (exc) { if (exc.name !== 'AbortError' && request === generation) error.value = assessmentError(exc); }
  finally { if (request === generation) loading.value = false; }
}
function changeAggregation(target, prefix = '') {
  if (target[`${prefix}aggregation`] !== 'capped_sum') target[`${prefix}cap`] = null;
  if (!['formula','non_additive'].includes(target[`${prefix}aggregation`])) target[prefix ? 'overall_rule_text' : 'aggregation_rule_text'] = null;
}
function toggleRange(row, field = 'score_range') { row[field] = row[field] ? null : { minimum: '0', maximum: '0' }; }
function add() {
  if (!ownDomain.value || !graph.value.coverage.length) return;
  const requirementId = graph.value.coverage[0].requirement_id;
  if (active.value === 'sections') form.value.sections.push({ source_section_id: null, sources: [{ requirement_id: requirementId, quote: graph.value.coverage[0].source.quote }], key: '', title: '', order: form.value.sections.length + 1, aggregation: 'sum', aggregation_rule_text: null, score_range: null, weight: null, cap: null, included_in_overall_total: true, ambiguity_reason: null });
  else if (active.value === 'items') form.value.items.push({ source_item_id: null, requirement_id: requirementId, section_key: form.value.sections[0]?.key ?? '', key: '', title: '', rule_text: '', order: form.value.items.length + 1, assessment_mode: 'model_assessable', score_range: { minimum: '0', maximum: '0' }, weight: null, ambiguity_reason: null });
  index.value = entries.value.length - 1;
}
function remove() {
  if (!editable.value || active.value === 'coverage') return;
  if (active.value === 'sections' && form.value.items.some(i => i.section_key === current.value.key)) { error.value = '此分节仍有关联条目，请先调整当前职责的条目；其他职责内容必须保留。'; return; }
  entries.value.splice(index.value, 1); index.value = Math.max(0, index.value - 1);
}
function sourceTarget(entry) {
  for (const section of graph.value?.sections ?? []) {
    const citationIndex = section.sources.findIndex(saved => saved.requirement_id === entry.requirement_id && saved.quote === entry.quote);
    if (citationIndex >= 0) return { part: 'rubric_section', entryId: section.id, origin: 'sources', citationIndex };
  }
  const coverage = graph.value?.coverage.find(saved => saved.requirement_id === entry.requirement_id && saved.source.quote === entry.quote);
  return coverage ? { part: 'coverage', entryId: coverage.id, origin: 'source', citationIndex: 0 } : null;
}
function addSource() {
  if (!editable.value || active.value !== 'sections') return;
  const entry = graph.value.coverage.find(row => row.requirement_id === sourceRequirement.value);
  if (entry && !current.value.sources.some(saved => saved.requirement_id === entry.requirement_id && saved.quote === entry.source.quote)) current.value.sources.push({ requirement_id: entry.requirement_id, quote: entry.source.quote });
  sourceRequirement.value = '';
}
function removeSource(sourceIndex) {
  if (editable.value && current.value.sources.length > 1) current.value.sources.splice(sourceIndex, 1);
}
function coverageChanged(row) { if (row.disposition !== 'mapped') row.rubric_item_keys = []; if (row.disposition !== 'duplicate') row.canonical_requirement_id = null; }
async function save() {
  if (!validSnapshot.value || !form.value || saving.value) return;
  if (!form.value.reason.trim()) { error.value = '请填写完整修订理由'; return; }
  if (form.value.sections.some(row => !row.sources.length)) { error.value = '每个分节必须至少保留一段固定原文'; return; }
  if (bytes.value > 512 * 1024) { error.value = '完整修订内容超出当前编辑上限'; return; }
  if (!form.value.sections.length || !form.value.items.length || form.value.coverage.length !== baseline.value.coverage.length) { error.value = '完整修订必须保留分节、条目和全部固定评分要求'; return; }
  for (const kind of ['sections', 'items']) if (form.value[kind].some(r => !r.key.trim() || !r.title.trim()) || new Set(form.value[kind].map(r => r.key)).size !== form.value[kind].length) { error.value = '分节和条目键必须唯一，标题不能为空'; return; }
  saving.value = true; error.value = '';
  try {
    const latest = (await orgRequest('GET', queryPath(base.value, { view: 'console', part: 'summary' }))).data;
    if (latest.revision !== form.value.expected_revision || latest.input_hash !== form.value.expected_input_hash) { validSnapshot.value = false; throw { code: 'revision_conflict' }; }
    const result = await orgRequest('POST', `${base.value}/revisions?view=console`, form.value); emit('dirty', false); emit('saved', result.data);
  } catch (exc) { if (exc.name !== 'AbortError') { error.value = assessmentError(exc); if (['revision_conflict','assessment_view_changed','rubric_superseded'].includes(exc.code)) validSnapshot.value = false; } } finally { saving.value = false; }
}
async function close() { if (!dirty.value || await confirmAction('未保存的完整修订将被丢弃。', '关闭修订', '丢弃修订')) emit('close'); }
async function reload() { if (!dirty.value || await confirmAction('将重新读取完整快照并丢弃当前未保存修改。', '重新载入完整规则', '重新载入')) await load(); }
function clear() { generation++; form.value = null; baseline.value = null; graph.value = null; sourceRequirement.value = '';  emit('dirty', false); }
window.addEventListener('bid:org-reset', clear); onBeforeUnmount(() => { clear(); window.removeEventListener('bid:org-reset', clear); });
load();
</script>
<template>
  <el-dialog :model-value="true" title="完整修订评分规则" width="min(980px, 96vw)" :close-on-click-modal="false" :before-close="close">
    <p>将创建新版本；所有职责分类、条目确认和覆盖决定需要重新完成。招标原文来自服务器固定要求，不可编辑。</p><el-alert v-if="error" :title="error" type="error" :closable="false" show-icon role="alert" /><el-skeleton v-if="loading" :rows="5" animated />
    <el-button v-if="!validSnapshot && !loading" @click="reload">重新载入完整规则</el-button>
    <template v-if="form">
      <p role="status">完整快照：分节 {{ form.sections.length }}、条目 {{ form.items.length }}、评分要求 {{ form.coverage.length }}；基于修订 {{ form.expected_revision }}。序列化大小 {{ bytes }} / 524288 字节。</p>
      <el-alert v-if="!validSnapshot" title="快照已变化或读取未完成，保存已禁用；当前修改保留供核对" type="warning" :closable="false" />
      <el-tabs v-model="active" aria-label="完整修订分区"><el-tab-pane label="修订分节" name="sections" /><el-tab-pane label="修订条目" name="items" /><el-tab-pane label="修订覆盖提议" name="coverage" /><el-tab-pane label="修订总分规则" name="overall" /></el-tabs>
      <template v-if="active !== 'overall'">
        <div class="actions"><el-button :disabled="index === 0" @click="index--">上一条修订</el-button><span>{{ index + 1 }} / {{ entries.length }}</span><el-button :disabled="index >= entries.length - 1" @click="index++">下一条修订</el-button><el-button v-if="active !== 'coverage'" @click="add">{{ active === 'sections' ? '新增分节' : '新增条目' }}</el-button><el-button v-if="active !== 'coverage'" :disabled="!editable" @click="remove">移除当前项</el-button></div>
        <template v-if="current"><p v-if="!editable">此内容属于{{ domainLabels[originalDomain ?? 'unclassified'] }}，必须完整保留。</p><blockquote v-if="active !== 'sections'" class="quote">{{ currentSource?.quote }}</blockquote>
          <el-form label-position="top" :disabled="!editable">
            <template v-if="active === 'sections'"><p>分节可引用多段已固定的评分要求原文，至少保留一段。</p><article v-for="(entry, sourceIndex) in current.sources" :key="`${entry.requirement_id}-${sourceIndex}`"><p>评分要求 {{ entry.requirement_id }}</p><blockquote class="quote">{{ entry.quote }}</blockquote><AssessmentCitation v-if="sourceTarget(entry)" :task-id="taskId" parent-kind="rubric" :parent-id="rubricId" v-bind="sourceTarget(entry)" /><el-button :disabled="!editable || current.sources.length === 1" @click="removeSource(sourceIndex)">移除此原文</el-button></article><el-form-item label="添加固定分节原文"><el-select v-model="sourceRequirement" aria-label="添加固定分节原文"><el-option v-for="entry in graph.coverage" :key="entry.requirement_id" :value="entry.requirement_id" :label="entry.source.quote" /></el-select><el-button :disabled="!sourceRequirement" @click="addSource">添加此原文</el-button></el-form-item></template>
            <el-form-item v-else label="固定评分要求"><el-select v-model="current.requirement_id" aria-label="固定评分要求" :disabled="!!current.source_item_id || active === 'coverage'"><el-option v-for="entry in graph.coverage" :key="entry.requirement_id" :value="entry.requirement_id" :label="entry.source.quote" /></el-select></el-form-item>
            <template v-if="active !== 'coverage'">
              <el-form-item label="稳定键"><el-input v-model="current.key" aria-label="稳定键" :disabled="!!(current.source_section_id || current.source_item_id)" /></el-form-item><el-form-item label="标题"><el-input v-model="current.title" aria-label="标题" /></el-form-item><el-form-item label="顺序"><el-input-number v-model="current.order" :min="1" :precision="0" aria-label="顺序" /></el-form-item>
              <template v-if="active === 'sections'"><el-form-item label="分节合计规则"><el-select v-model="current.aggregation" aria-label="分节合计规则" @change="changeAggregation(current)"><el-option v-for="(label,value) in aggregationOptions" :key="value" :value="value" :label="label" /></el-select></el-form-item><el-form-item v-if="['formula','non_additive'].includes(current.aggregation)" label="固定规则原文"><el-input v-model="current.aggregation_rule_text" type="textarea" aria-label="固定规则原文" /></el-form-item><el-form-item v-if="current.aggregation === 'capped_sum'" label="分节封顶"><el-input v-model="current.cap" inputmode="decimal" aria-label="分节封顶" /></el-form-item><el-checkbox v-model="current.included_in_overall_total">此分节进入总分</el-checkbox></template>
              <template v-else><el-form-item label="所属分节"><el-select v-model="current.section_key" aria-label="所属分节"><el-option v-for="section in form.sections" :key="section.key" :value="section.key" :label="section.title || section.key" /></el-select></el-form-item><el-form-item label="条目评分规则"><el-input v-model="current.rule_text" type="textarea" :rows="4" aria-label="条目评分规则" /></el-form-item><el-form-item label="评估方式"><el-select v-model="current.assessment_mode" aria-label="评估方式"><el-option v-for="(label,value) in modeOptions" :key="value" :value="value" :label="label" /></el-select></el-form-item></template>
              <el-form-item label="分值范围"><el-button @click="toggleRange(current)">{{ current.score_range ? '取消分值范围' : '设置分值范围' }}</el-button><div v-if="current.score_range" class="actions"><el-input v-model="current.score_range.minimum" aria-label="最低分" inputmode="decimal" /><el-input v-model="current.score_range.maximum" aria-label="最高分" inputmode="decimal" /></div></el-form-item>
              <el-form-item label="权重（空为不设置）"><el-input :model-value="current.weight ?? ''" aria-label="权重" inputmode="decimal" @update:model-value="current.weight = $event || null" /></el-form-item><el-form-item label="歧义或无法评估原因"><el-input :model-value="current.ambiguity_reason ?? ''" type="textarea" aria-label="歧义或无法评估原因" @update:model-value="current.ambiguity_reason = $event || null" /></el-form-item>
            </template>
            <template v-else><el-form-item label="覆盖提议"><el-select v-model="current.disposition" aria-label="覆盖提议" @change="coverageChanged(current)"><el-option value="pending" label="待人工核对" /><el-option value="mapped" label="对应评分项" /><el-option value="duplicate" label="重复要求" /><el-option value="excluded" label="有理由地排除" /></el-select></el-form-item><el-form-item v-if="current.disposition === 'mapped'" label="对应条目"><el-select v-model="current.rubric_item_keys" multiple aria-label="对应条目"><el-option v-for="item in form.items.filter(row => row.requirement_id === current.requirement_id)" :key="item.key" :value="item.key" :label="item.title" /></el-select></el-form-item><el-form-item v-if="current.disposition === 'duplicate'" label="规范要求"><el-select v-model="current.canonical_requirement_id" aria-label="规范要求"><el-option v-for="entry in graph.coverage.filter(row => row.requirement_id !== current.requirement_id)" :key="entry.requirement_id" :value="entry.requirement_id" :label="entry.source.quote" /></el-select></el-form-item><el-form-item label="覆盖理由"><el-input :model-value="current.reason ?? ''" type="textarea" aria-label="覆盖理由" @update:model-value="current.reason = $event || null" /></el-form-item></template>
          </el-form>
        </template>
      </template>
      <el-form v-else label-position="top" :disabled="orgAccess.role !== 'bidder'"><p v-if="orgAccess.role !== 'bidder'">只有商务负责人（投标专员）可修改总分规则，当前内容将完整保留。</p><el-form-item label="总分合计规则"><el-select v-model="form.overall_aggregation" aria-label="总分合计规则" @change="changeAggregation(form, 'overall_')"><el-option v-for="(label,value) in aggregationOptions" :key="value" :value="value" :label="label" /></el-select></el-form-item><el-form-item label="总分规则原文"><el-input v-model="form.overall_rule_text" type="textarea" aria-label="总分规则原文" /></el-form-item><el-form-item v-if="form.overall_aggregation === 'capped_sum'" label="总分封顶"><el-input v-model="form.overall_cap" aria-label="总分封顶" inputmode="decimal" /></el-form-item><el-button @click="toggleRange(form, 'overall_score_range')">{{ form.overall_score_range ? '取消总分范围' : '设置总分范围' }}</el-button><div v-if="form.overall_score_range" class="actions"><el-input v-model="form.overall_score_range.minimum" aria-label="总分最低值" /><el-input v-model="form.overall_score_range.maximum" aria-label="总分最高值" /></div></el-form>
      <details><summary>比较新增、移除和修改（{{ changes.length }} 项）</summary><ul><li v-for="change in changes" :key="`${change.part}-${change.key}`">{{ change.action }} · {{ change.key }} · {{ change.fields }}</li></ul></details>
      <el-form-item label="完整修订理由" required><el-input v-model="form.reason" type="textarea" aria-label="完整修订理由" /></el-form-item><el-alert v-if="bytes > 524288" title="完整修订内容超出当前编辑上限" type="error" :closable="false" role="alert" />
    </template>
    <template #footer><el-button @click="close">取消修订</el-button><el-button type="primary" :disabled="!validSnapshot || !dirty || bytes > 524288 || !form?.reason.trim()" :loading="saving" @click="save">保存完整修订</el-button></template>
  </el-dialog>
</template>
<style scoped>.quote { white-space:pre-wrap; overflow-wrap:anywhere; } .actions .el-input { max-width:200px; } </style>
