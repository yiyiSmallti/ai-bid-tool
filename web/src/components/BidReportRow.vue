<script setup>
import BidPresenceLocation from "./BidPresenceLocation.vue";
import { computed } from "vue";
import { money } from "../api.js";
import { formatTime } from "../org.js";
import { reviewError } from "../bid-review.js";
const props = defineProps({ row: { type: Object, required: true } });
const finding = computed(() => props.row.finding);
const requirement = computed(() => props.row.requirement);
const validation = computed(() => props.row.validation);
const labels = { fatal: "废标风险", high: "高风险", medium: "中风险", open: "待审查", dismissed: "已驳回", confirmed: "已确认", responded: "已响应", deviation: "偏离", missing: "缺失", unknown: "未确定", rejection: "废标风险", lost_points: "扣分风险", both: "废标与扣分风险", uncertain: "影响未确定", rule: "本地规则", model: "模型", classify: "分类", dismiss: "驳回", reopen: "重新打开", confirm: "确认", commercial: "商务", technical: "技术", applies: "适用", not_applicable: "不适用", alternative: "有替代签章方式", valid: "有效", invalid: "无效", unsigned: "未发现数字签名", trusted: "链到本地信任根", untrusted: "未链到本地信任根", expired: "已过期", not_for_signing: "证书用途不含签名" };
const marks = { company_seal: "单位公章", legal_representative_signature: "法定代表人签字", authorized_agent_signature: "授权代理人签字", personal_seal: "个人印章", date: "日期", seam_seal: "骑缝章", every_page_electronic_seal: "逐页电子章", pdf_digital_signature: "PDF 数字签名" };
const text = value => value === null || value === undefined ? "未知" : typeof value === "boolean" ? value ? "是" : "否" : typeof value === "string" || typeof value === "number" ? String(value) : "请查看对应逐项记录";
const status = value => labels[value] ?? "未确定";
const pdfStatus = value => ({ ...labels, unsupported: "不支持", whole_revision: "覆盖完整签署修订", not_yet_valid: "尚未生效", absent: "未提供", unmodified: "无后续修改", none: "无后续修改", incremental_or_appended_bytes: "存在增量更新或追加字节", additional_signatures: "后续新增签名", permitted_changes: "存在允许的认证修改" })[value] ?? "未知";
const position = citation => `${citation.page_label === "rendered_docx" ? "固定转换第" : "原件第"} ${citation.page} 页${citation.location?.label ? ` · ${citation.location.label}` : citation.location?.section_path?.length ? ` · ${citation.location.section_path.join(" / ")}` : ""}`;
</script>
<template>
  <article class="report-row" data-testid="report-row">
    <template v-if="finding">
      <h4>{{ finding.title ?? finding.code }} · {{ status(finding.severity) }}</h4>
      <p v-if="row.obligation">{{ row.obligation.starred ? '★ 条款' : row.obligation.triangle ? '▲ 条款' : '符合性条款' }}</p>
      <p>机器原始结论：{{ status(finding.outcome) }} · 影响：{{ status(finding.impact) }}</p>
      <p>依据类型：{{ status(finding.basis?.kind ?? row.basis_type) }}<span v-if="finding.basis?.rule_or_prompt_version"> · {{ finding.basis.rule_or_prompt_version }}</span><span v-if="finding.basis?.model"> · {{ finding.basis.model }}</span></p>
      <p v-if="finding.explanation">{{ finding.explanation }}</p>
      <p v-if="row.summary">{{ row.summary }}</p>
      <div v-for="(citation,index) in finding.tender_support" :key="`tender-${index}`"><p>招标{{ position(citation) }} · 文件 <code>{{ citation.document_id }}</code></p><blockquote>{{ citation.quote }}</blockquote></div>
      <div v-for="(citation,index) in finding.bid_support" :key="`bid-${index}`"><p>投标{{ position(citation) }} · 文件 <code>{{ citation.document_id }}</code></p><blockquote>{{ citation.quote }}</blockquote></div>
      <template v-if="finding.absence_search"><p>投标缺失检索范围：{{ finding.absence_search.coverage === 'partial' ? '部分检索' : '固定检索清单' }}；缺失项没有原文引句。</p><p v-for="(page,index) in finding.absence_search.searched_pages" :key="index">投标第 {{ page.page }} 页 · 文件 <code>{{ page.document_id }}</code></p></template>
      <p>快照内人工决定：{{ status(finding.state) }} · 修订 {{ finding.revision }}</p>
      <p v-if="row.decision">{{ status(row.decision.action) }} · {{ formatTime(row.decision.decided_at) }} · {{ row.decision.reason }}</p>
      <p v-if="finding.remediation">补救建议：{{ finding.remediation }}</p>
      <p v-for="code in finding.limitation_codes" :key="code">未确定范围：{{ reviewError({ code }) }}</p>
    </template>
    <template v-else-if="requirement">
      <h4>签章要求 · {{ status(requirement.applicability) }}</h4>
      <p>{{ requirement.mark_types?.map(mark => marks[mark] ?? '待核对签章类型').join('、') }}<span v-if="requirement.date_required"> · 需填写日期</span></p>
      <template v-if="requirement.citation"><p>招标{{ position(requirement.citation) }}</p><blockquote>{{ requirement.citation.quote }}</blockquote></template>
      <BidPresenceLocation v-for="(location,index) in requirement.required_locations" :key="index" :location="location" />
      <p v-if="!requirement.required_locations?.length">{{ requirement.applicability === 'not_applicable' ? '无适用签章位置' : '目标位置未确定，不能判为存在' }}</p>
    </template>
    <template v-else-if="validation">
      <h4>PDF 本地数字签名校验 · {{ pdfStatus(validation.status) }}</h4><p>文件 <code>{{ validation.document_id }}</code> · {{ validation.validator_identity }} · {{ formatTime(validation.validation_time) }}</p><p>原件 SHA-256 <code>{{ validation.original_sha256 }}</code> · 固定信任快照 <code>{{ validation.trust_store_sha256 }}</code></p><p v-if="validation.final_revision">最终修订：{{ pdfStatus(validation.final_revision.status) }} · 最后签名后有修改：{{ text(validation.final_revision.modified_after_last_signature) }}</p>
      <p>离线验证不判定可见签章存在；吊销证明未知。</p>
      <p v-if="validation.signatures === undefined">详细数字签名记录仅向有原件读取权限的成员提供。</p><p v-else-if="!validation.signatures.length">{{ validation.status === 'not_applicable' ? '数字签名校验不适用' : '未发现可验证数字签名' }}</p>
      <dl v-for="signature in validation.signatures" :key="signature.signature_index" class="kv"><dt>数字签名</dt><dd>{{ signature.signature_index }}</dd><dt>字节范围覆盖</dt><dd>{{ pdfStatus(signature.coverage_status) }}</dd><dt>密码学完整性</dt><dd>{{ pdfStatus(signature.crypto_status) }}</dd><dt>内容摘要</dt><dd>{{ pdfStatus(signature.content_digest_status) }}</dd><dt>签名值</dt><dd>{{ pdfStatus(signature.signature_value_status) }}</dd><dt>签后有修改</dt><dd>{{ text(signature.modified_after_signing) }}</dd><dt>最终修订覆盖</dt><dd>{{ text(signature.final_revision_covered) }}</dd><dt>证书有效性</dt><dd>{{ pdfStatus(signature.certificate_validity_status) }}</dd><dt>本地信任链</dt><dd>{{ pdfStatus(signature.trust_status) }}</dd><dt>吊销状态</dt><dd>{{ pdfStatus(signature.revocation_status) }}</dd><dt>时间戳</dt><dd>{{ pdfStatus(signature.timestamp_status) }}</dd></dl>
    </template>
    <template v-else-if="row.kind === 'basic_information'">
      <dl class="kv"><dt>任务</dt><dd>{{ row.task_name ?? '未披露' }} · <code>{{ row.task_id }}</code></dd><dt>招标编号</dt><dd>{{ row.tender_number ?? '未提供' }}</dd><dt>评估日期</dt><dd>{{ row.assessment_date }}</dd><dt>固定提交</dt><dd><code>{{ row.submission_id }}</code></dd><dt>本地准备版本</dt><dd><code>{{ row.preparation_id }}</code></dd><dt>检验运行</dt><dd><code>{{ row.review_id }}</code></dd><dt>检验时间</dt><dd>{{ formatTime(row.review_created_at) }}</dd><dt>检验作业</dt><dd><code>{{ row.review_job_id }}</code></dd><dt>覆盖状态</dt><dd>{{ row.completion === 'partial' ? '部分完成，保留未覆盖范围' : '已完成本次范围' }}</dd></dl>
      <h4>招标文件</h4><p v-for="(document,index) in row.tender_documents" :key="document.id">{{ document.name ?? `文件 ${index + 1}` }} · <code>{{ document.id }}</code> · {{ document.size_bytes }} 字节 · <code>{{ document.sha256 }}</code></p><h4>投标文件</h4><p v-for="(document,index) in row.bid_documents" :key="document.id">{{ document.name ?? `文件 ${index + 1}` }} · <code>{{ document.id }}</code> · {{ document.size_bytes }} 字节 · <code>{{ document.sha256 }}</code></p>
    </template>
    <template v-else-if="row.kind === 'obligation'">
      <h4>{{ row.obligation.starred ? '★ 条款' : row.obligation.triangle ? '▲ 条款' : '符合性条款' }} · {{ status(row.outcome) }}</h4><p>{{ row.obligation.text }}</p><p>招标{{ position(row.obligation.citation) }}</p><blockquote>{{ row.obligation.citation.quote }}</blockquote><p>投标响应尚未确定；没有已核验的投标原文引句。</p><p>未覆盖：{{ reviewError({ code: row.reason_code }) }}</p>
    </template>
    <template v-else-if="row.kind === 'usage'"><p>模型：{{ row.usage.provider }} / {{ row.usage.model }} · 输入 {{ row.usage.input_tokens }} tokens · 输出 {{ row.usage.output_tokens }} tokens · 服务成本 {{ money(row.usage.usd, 'USD') }}</p></template>
    <template v-else-if="row.kind === 'cost'"><p>运行成本 {{ money(row.cost.usd, 'USD') }} · 平台扣费 {{ money(row.cost.charge, row.cost.billing_currency) }} · 计入任务预算 {{ money(row.cost.task_amount, row.cost.billing_currency) }} · 未确定调用 {{ row.cost.unresolved_calls }}</p></template>
    <template v-else>
      <h4 v-if="row.label">{{ row.label }}</h4><p v-if="row.text">{{ row.text }}</p><p v-if="row.value !== undefined">{{ text(row.value) }}</p><p v-for="(value,index) in row.values" :key="index">{{ text(value) }}</p>
    </template>
  </article>
</template>
<style scoped>.report-row { padding:16px 0;border-top:1px solid var(--el-border-color); } h4 { margin:0 0 12px; } blockquote { margin:8px 0;white-space:pre-wrap;overflow-wrap:anywhere;border-left:3px solid var(--el-border-color);padding-left:12px; } code,p { overflow-wrap:anywhere; }</style>
