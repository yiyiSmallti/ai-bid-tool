<script setup>
import { formatTime } from "../org.js";
defineProps({ validations: { type: Array, default: () => [] }, candidates: { type: Array, default: () => [] }, candidateCount: { type: Number, default: 0 }, nextCursor: { type: Number, default: null }, busy: Boolean });
defineEmits(["more"]);
const labels = { valid: "有效", invalid: "无效", unsupported: "不支持", unknown: "未知", trusted: "链到本地信任根", not_for_signing: "证书用途不含签名", whole_revision: "覆盖完整签署修订", not_applicable: "不适用", unsigned: "未发现数字签名", modified_after_signing: "签后发生修改", expired: "已过期", not_yet_valid: "尚未生效", absent: "未提供", unmodified: "无后续修改", none: "无后续修改", incremental_or_appended_bytes: "存在增量更新或追加字节", additional_signatures: "后续新增签名", permitted_changes: "存在允许的认证修改" };
const status = value => labels[value] ?? "未知";
const changed = value => value === true ? "是" : value === false ? "否" : "未知";
const marks = { company_seal: "公章", legal_representative_signature: "法定代表人签名", legal_representative_seal: "法定代表人私章", personal_seal: "个人印章", authorized_agent_signature: "授权代理人签名", seam_seal: "骑缝章", every_page_electronic_seal: "每页电子签章", pdf_digital_signature: "PDF 数字签名", electronic_seal: "电子印章", signature: "签名", seal: "印章", date: "日期" };
const mark = value => marks[value] ?? "待人工核对";
const owners = { company: "投标单位", bidder: "投标人", legal_representative: "法定代表人", authorized_agent: "授权代理人" };
const owner = value => owners[value] ?? "待人工核对";
const variants = { signed_content: "签署内容", signed_attributes_der_set: "DER SET 签署属性", signed_content_sm2_default_za: "签署内容与默认 SM2 ZA", signed_attributes_der_set_sm2_default_za: "DER SET 签署属性与默认 SM2 ZA", direct_sm3_content_digest: "内容 SM3 摘要" };
const reasons = { offline_revocation_unknown: "离线材料不能确定吊销状态", claimed_time_not_trusted: "声明签署时间未经可信时间戳证明", unsupported_pdf_signature_profile: "不支持此 PDF 签名格式", unsupported_timestamp_proof: "不支持提供的时间戳证明", malformed_or_unsupported_signature: "签名结构无效或不支持" };
</script>
<template>
  <el-card class="section" shadow="never" data-testid="pdf-signature-results"><template #header><h3>PDF 数字签名本地验证</h3></template>
    <p class="hint">使用原件字节和固定本地信任快照，不联网、不调用模型。密码学验证、签后修改、证书信任和吊销证明分别记录；有效签名不能代替每处可见签章要求。</p>
    <p v-if="!validations.length">本地准备完成后显示数字签名验证结果。</p>
    <section v-for="document in validations" :key="document.document_id" class="document-result" :aria-label="`文档 ${document.document_id} 签名结果`">
      <h4>文档 <span class="mono">{{ document.document_id }}</span> · {{ status(document.status) }}</h4>
      <dl class="kv"><dt>原件 SHA-256</dt><dd class="mono">{{ document.original_sha256 }}</dd><dt>信任快照 SHA-256</dt><dd class="mono">{{ document.trust_store_sha256 }}</dd><dt>验证器</dt><dd>{{ document.validator_identity }}</dd><dt>本地验证时间</dt><dd>{{ formatTime(document.validation_time) }}</dd><dt>最终修订结果</dt><dd>{{ status(document.final_revision.status) }}</dd><dt>最后签名后有修改</dt><dd>{{ changed(document.final_revision.modified_after_last_signature) }}</dd><dt>覆盖最终修订的签名</dt><dd>{{ document.final_revision.covered_by_signature_indices.length ? document.final_revision.covered_by_signature_indices.join('、') : '无' }}</dd></dl>
      <p v-if="!document.signatures.length">{{ document.status === 'not_applicable' ? '此格式或未签名文件不适用数字签名验证；可见印章仍需人工核对。' : '未发现可验证的数字签名。' }}</p>
      <article v-for="signature in document.signatures" :key="signature.signature_index" class="signature-result" :aria-label="`数字签名 ${signature.signature_index}`">
        <h4>数字签名 {{ signature.signature_index }}<span v-if="signature.signed_revision"> · 签署修订 {{ signature.signed_revision }}</span></h4>
        <dl class="kv">
          <dt>签名字段哈希</dt><dd class="mono">{{ signature.field_name_sha256 ?? '未取得' }}</dd>
          <dt>ByteRange</dt><dd>{{ signature.byte_range.length ? signature.byte_range.join('、') : '未取得' }}</dd>
          <dt>字节范围覆盖</dt><dd>{{ status(signature.coverage_status) }}</dd>
          <dt>密码学完整性</dt><dd>{{ status(signature.crypto_status) }}</dd>
          <dt>内容摘要</dt><dd>{{ status(signature.content_digest_status) }}</dd>
          <dt>签名值</dt><dd>{{ status(signature.signature_value_status) }}</dd>
          <dt>签后有修改</dt><dd>{{ changed(signature.modified_after_signing) }}</dd>
          <dt>后续修改类型</dt><dd>{{ status(signature.post_signing_changes) }}</dd>
          <dt>覆盖最终修订</dt><dd>{{ signature.final_revision_covered ? '是' : '否' }}</dd>
          <dt>证书有效性</dt><dd>{{ status(signature.certificate_validity_status) }}</dd>
          <dt>本地信任链</dt><dd>{{ status(signature.trust_status) }}</dd>
          <dt>吊销证明</dt><dd>{{ status(signature.revocation_status) }}</dd>
          <dt>可信时间戳</dt><dd>{{ status(signature.timestamp_status) }}<span v-if="signature.trusted_timestamp_time"> · {{ formatTime(signature.trusted_timestamp_time) }}</span></dd>
          <dt>声明签署时间</dt><dd>{{ signature.claimed_signing_time ? formatTime(signature.claimed_signing_time) : '未提供；不能推定' }}</dd>
          <dt>摘要 / 签名算法</dt><dd>{{ signature.digest_algorithm ?? '未知' }} / {{ signature.signature_algorithm ?? '未知' }}</dd>
          <dt>匹配的验证方式</dt><dd>{{ variants[signature.verification_variant] ?? '未取得' }}</dd>
          <template v-if="signature.reason_codes?.length"><dt>验证限制</dt><dd><p v-for="(reason, index) in signature.reason_codes" :key="index">{{ reasons[reason] ?? '存在未解决的本地验证限制' }}</p></dd></template>
          <template v-if="signature.certificate"><dt>证书指纹</dt><dd class="mono">{{ signature.certificate.fingerprint_sha256 ?? '未取得' }}</dd><dt>证书有效期</dt><dd>{{ signature.certificate.not_before ? formatTime(signature.certificate.not_before) : '未知' }} 至 {{ signature.certificate.not_after ? formatTime(signature.certificate.not_after) : '未知' }}</dd><dt>证书主体</dt><dd>{{ signature.certificate.subject ?? '受限或未取得' }}</dd><dt>颁发者</dt><dd>{{ signature.certificate.issuer ?? '受限或未取得' }}</dd></template>
        </dl>
      </article>
    </section>
  </el-card>
  <el-card class="section" shadow="never" data-testid="signing-candidates"><template #header><h3>签章要求候选</h3></template>
    <p class="hint">本地文本扫描共发现 {{ candidateCount }} 条候选。每条适用性均未知，尚未确认为要求；图片页未做 OCR，不代表条款完整覆盖。</p>
    <p v-if="!candidates.length">尚无可显示的候选。</p>
    <article v-for="candidate in candidates" :key="candidate.id" class="signature-result" :aria-label="`签章候选 ${candidate.ordinal}`">
      <h4>候选 {{ candidate.ordinal }} · 适用性未知</h4>
      <p class="mono">文档 {{ candidate.document_id }} · 第 {{ candidate.page }} 页 · 页面 {{ candidate.page_id }}</p>
      <template v-if="candidate.quote !== null && candidate.quote !== undefined"><blockquote class="exact-quote">{{ candidate.quote }}</blockquote><p>原文字符位置 {{ candidate.start_offset }}–{{ candidate.end_offset }}<span v-if="candidate.location_hint"> · {{ candidate.location_hint }}</span></p><p v-if="candidate.mark_types?.length">标记候选：{{ candidate.mark_types.map(mark).join('、') }}</p><p v-if="candidate.owner_roles?.length">主体候选：{{ candidate.owner_roles.map(owner).join('、') }}</p><p v-if="candidate.date_required !== null && candidate.date_required !== undefined">日期候选：{{ candidate.date_required ? '可能要求填写日期' : '未识别日期要求' }}</p></template>
      <p v-else class="hint">原文与候选详情仅向有原件读取权限的人工身份显示。</p>
    </article>
    <el-button v-if="nextCursor !== null" :loading="busy" @click="$emit('more')">加载更多候选</el-button>
  </el-card>
</template>
<style scoped>
h3 { margin:0; }
h4 { margin:0 0 12px; }
.document-result { border-top:1px solid var(--el-border-color);padding-top:20px;margin-top:20px; }
.signature-result { background:var(--surface-muted);border-radius:8px;padding:16px;margin-top:16px; }
.mono { overflow-wrap:anywhere; }
.exact-quote { white-space:pre-wrap;overflow-wrap:anywhere;border-left:3px solid var(--el-border-color);padding-left:12px;margin-left:0; }
</style>
