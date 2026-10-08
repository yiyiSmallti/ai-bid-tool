<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { formatTime, orgRequest } from "../org.js";
import { reviewError } from "../bid-review.js";
const props = defineProps({ taskId: String, submissionId: String, owner: Boolean, prepared: Boolean });
const emit = defineEmits(["changed"]);
const preview = ref(null), grants = ref([]), grantsCursor = ref(null), selected = ref([]), reason = ref(""), bidderNames = ref(""), staffNames = ref(""), consent = ref(false), busy = ref(false), error = ref("");
const path = computed(() => `/bid-submissions/${encodeURIComponent(props.submissionId)}`);
const current = computed(() => grants.value[0] ?? null);
const allowed = computed(() => props.owner && props.prepared);
const chosen = computed(() => preview.value?.pages.filter(page => selected.value.includes(page.page_id) && page.outbound_eligible) ?? []);
let serial = 0, controller;
const noteLabels = { price_page: "报价页禁止外发", price_page_excluded: "报价页禁止外发", uncertain_price_classification: "报价分类未确定，禁止外发", uncertain_price_page_excluded: "报价分类未确定，禁止外发", image_page_not_transcribed: "图片页未转录", unparsed_image_regions: "含未解析图片区域", redaction_changed: "脱敏绑定已变化", no_text: "没有可外发文本", text_unavailable: "页面文本不可用", text_unavailable_human_only: "缺少可靠文本，仅可按原件人工核对", short_name_requires_human_review: "短姓名可能遮挡不完整，需要逐页人工核对", identity_area_requires_human_review: "包含人员或单位身份区域，需要逐页人工核对", short_confidential_value_requires_human_review: "短保密值可能过度遮挡，需要逐页人工核对" };
const note = code => noteLabels[code] ?? "页面存在脱敏或解析限制，请核对";
async function read() {
  const token = serial;
  if (!allowed.value) return;
  await readGrants();
  if (token !== serial) return;
  let cursor = 0, fixed = null, pages = [], seen = new Set();
  do {
    const result = await orgRequest("GET", `${path.value}/redaction?cursor=${cursor}&limit=25`, undefined, { signal: controller?.signal });
    if (token !== serial) return;
    const data = result.data;
    if (data.submission_id !== props.submissionId || (fixed && (fixed.expected_redaction_manifest_sha256 !== data.expected_redaction_manifest_sha256 || fixed.expected_revision !== data.expected_revision || fixed.provider_bindings_sha256 !== data.provider_bindings_sha256))) throw new Error("redaction changed during read");
    fixed = data;
    for (const page of data.pages) { if (seen.has(page.page_id)) throw new Error("duplicate page"); seen.add(page.page_id); pages.push(page); }
    const next = data.next_cursor; if (next !== null && (!Number.isInteger(next) || next <= cursor)) throw new Error("invalid redaction cursor"); cursor = next;
  } while (cursor !== null);
  preview.value = { ...fixed, pages };
}
async function readGrants(more = false) {
  const token = serial, cursor = more ? grantsCursor.value : 0;
  const result = await orgRequest("GET", `${path.value}/outbound-authorizations?cursor=${cursor}&limit=100`, undefined, { signal: controller?.signal });
  if (token !== serial) return;
  if (result.items.length > 100 || result.items.some(row => row.task_id !== props.taskId || row.submission_id !== props.submissionId) || (result.data.next_cursor !== null && (!Number.isInteger(result.data.next_cursor) || result.data.next_cursor <= cursor))) throw new Error("authorization parent mismatch");
  grants.value = more ? [...grants.value, ...result.items] : result.items; grantsCursor.value = result.data.next_cursor;
}
async function moreGrants() {
  const token = serial; busy.value = true;
  try { await readGrants(true); } catch (exc) { if (token === serial && exc.name !== "AbortError") error.value = reviewError(exc); }
  finally { if (token === serial) busy.value = false; }
}
async function load() {
  const token = ++serial; controller?.abort(); controller = new AbortController(); preview.value = null; grants.value = []; grantsCursor.value = null; selected.value = []; consent.value = false; reason.value = ""; bidderNames.value = ""; staffNames.value = ""; error.value = ""; busy.value = true;
  try { if (props.prepared) await read(); } catch (exc) { if (token === serial && exc.name !== "AbortError") error.value = reviewError(exc); }
  finally { if (token === serial) busy.value = false; }
}
async function scopeHash(pages) {
  const sorted = pages.slice().sort((a, b) => a.page_id.localeCompare(b.page_id)).map(page => ({ page_id: page.page_id, sanitized_text_sha256: page.sanitized_text_sha256 }));
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(sorted)));
  return [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("");
}
async function authorize() {
  if (!allowed.value || !consent.value || !chosen.value.length || !reason.value.trim() || preview.value.blockers.length) return;
  const token = serial; busy.value = true; error.value = "";
  try {
    const fixed = preview.value, pages = chosen.value.map(({ page_id, sanitized_text_sha256 }) => ({ page_id, sanitized_text_sha256 }));
    const body = { request_id: crypto.randomUUID(), expected_revision: fixed.expected_revision, expected_authorization_id: fixed.expected_authorization_id, expected_submission_manifest_sha256: fixed.expected_submission_manifest_sha256, expected_preparation_input_hash: fixed.expected_preparation_input_hash, expected_redaction_manifest_sha256: fixed.expected_redaction_manifest_sha256, provider_bindings_sha256: fixed.provider_bindings_sha256, authorized_sanitized_context_sha256: await scopeHash(pages), pages, purposes: ["bid_review_text"], allowed_capabilities: ["llm"], privacy_reviewed: true, allow_external: true, reason: reason.value.trim() };
    await orgRequest("POST", `${path.value}/outbound-authorizations`, body, { signal: controller?.signal });
    if (token !== serial) return; emit("changed"); await load();
  } catch (exc) { if (token === serial && exc.name !== "AbortError") { error.value = reviewError(exc); if (exc.status === 409) { preview.value = null; consent.value = false; } } }
  finally { if (token === serial) busy.value = false; }
}
async function revoke() {
  if (!allowed.value || !current.value || !reason.value.trim()) return;
  const token = serial; busy.value = true; error.value = "";
  try { await orgRequest("POST", `${path.value}/outbound-authorizations/revoke`, { request_id: crypto.randomUUID(), expected_revision: current.value.revision, expected_authorization_id: current.value.id, reason: reason.value.trim() }, { signal: controller?.signal }); if (token !== serial) return; emit("changed"); await load(); }
  catch (exc) { if (token === serial && exc.name !== "AbortError") error.value = reviewError(exc); }
  finally { if (token === serial) busy.value = false; }
}
async function saveNames() {
  if (!allowed.value || !preview.value) return;
  const split = text => [...new Set(text.split(/\r?\n/).map(value => value.trim()).filter(Boolean))];
  const bidder = split(bidderNames.value), staff = split(staffNames.value);
  if (bidder.length > 500 || staff.length > 500 || [...bidder, ...staff].some(name => name.length > 20000)) { error.value = "补充名单每类最多 500 项，请缩小名单"; return; }
  const token = serial; busy.value = true; error.value = "";
  try {
    await orgRequest("POST", `${path.value}/redaction/names`, { request_id: crypto.randomUUID(), expected_revision: preview.value.human_name_revision, bidder_names: bidder, staff_names: staff }, { signal: controller?.signal });
    if (token !== serial) return; emit("changed"); await load();
  } catch (exc) { if (token === serial && exc.name !== "AbortError") error.value = reviewError(exc); }
  finally { if (token === serial) busy.value = false; }
}
watch(() => [props.taskId, props.submissionId, props.owner, props.prepared], load, { immediate: true });
watch([selected, reason], () => { consent.value = false; }, { deep: true });
onBeforeUnmount(() => { serial++; controller?.abort(); preview.value = null; reason.value = ""; bidderNames.value = ""; staffNames.value = ""; });
</script>
<template>
  <el-card class="section" shadow="never"><template #header><h3>外发授权</h3></template>
    <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" />
    <p>仅授权逐页核对后的脱敏文本，固定到本次准备、脱敏规则和模型配置。报价页、分类未确定页及图片不在此授权范围。</p>
    <p v-if="!allowed" class="hint">逐页文本核对与授权需要任务负责人以单位管理员或商务成员身份登录，并完成本地准备。</p>
    <template v-if="preview && allowed">
      <el-alert v-for="code in preview.blockers" :key="code" :title="reviewError({ code })" type="warning" :closable="false" />
      <details class="section"><summary>补充单位与人员脱敏名单</summary>
        <p>每行填写一项。保存会替换本次提交的完整人工补充名单；未填写项会从人工补充名单移除，本地自动发现的名单继续生效。保存后须重新核对页面并授权。当前人工补充修订 {{ preview.human_name_revision }}。</p>
        <el-form label-position="top"><el-form-item label="投标单位补充名单"><el-input v-model="bidderNames" aria-label="投标单位补充名单" type="textarea" :rows="3" maxlength="100000" :disabled="busy" /></el-form-item><el-form-item label="人员补充名单"><el-input v-model="staffNames" aria-label="人员补充名单" type="textarea" :rows="3" maxlength="100000" :disabled="busy" /></el-form-item></el-form>
        <el-button :loading="busy" :disabled="!bidderNames.trim() && !staffNames.trim() && !preview.human_name_revision" @click="saveNames">保存完整补充名单</el-button>
      </details>
      <el-checkbox-group v-model="selected" aria-label="选择外发页面">
        <div v-for="page in preview.pages" :key="page.page_id" class="sanitized-page" data-testid="sanitized-page">
          <el-checkbox :value="page.page_id" :disabled="busy || !page.outbound_eligible">{{ page.role === 'tender' ? '招标' : '投标' }}第 {{ page.page }} 页</el-checkbox>
          <p>{{ page.price_classification === 'uncertain' ? '报价分类未确定：禁止外发' : page.price_page || page.price_classification === 'price' ? '报价页：禁止外发' : '非报价页' }} · {{ page.outbound_eligible ? '可选择脱敏文本' : '不可外发' }}</p>
          <p v-for="(code, index) in page.notes" :key="index" class="hint">{{ note(code) }}</p>
          <pre>{{ page.sanitized_text }}</pre><p class="hint mono">脱敏文本哈希 {{ page.sanitized_text_sha256 }}</p>
        </div>
      </el-checkbox-group>
      <el-form label-position="top"><el-form-item label="授权或撤销理由"><el-input v-model="reason" aria-label="授权或撤销理由" type="textarea" maxlength="20000" :disabled="busy" /></el-form-item></el-form>
      <el-checkbox v-model="consent" :disabled="busy">已逐页核对所选脱敏文本，同意用于标书文本检验</el-checkbox>
      <div class="actions"><el-button type="primary" :loading="busy" :disabled="!consent || !chosen.length || !reason.trim() || preview.blockers.length > 0" @click="authorize">授权所选页面</el-button><el-button v-if="current?.allow_external" type="danger" plain :loading="busy" :disabled="!reason.trim()" @click="revoke">撤销外发授权</el-button></div>
    </template>
    <el-table :data="grants" role="table" aria-label="外发授权记录"><el-table-column label="授权修订"><template #default="{ row }">{{ row.revision }}</template></el-table-column><el-table-column label="状态"><template #default="{ row }">{{ !row.allow_external ? '已撤销' : row.current ? '当前授权' : '历史授权或已失效' }}</template></el-table-column><el-table-column label="页数"><template #default="{ row }">{{ row.pages.length }}</template></el-table-column><el-table-column label="时间"><template #default="{ row }">{{ formatTime(row.authorized_at) }}</template></el-table-column></el-table>
    <el-button v-if="grantsCursor !== null" :disabled="busy" @click="moreGrants">更多外发授权记录</el-button>
    <el-button :disabled="busy" @click="load">刷新外发授权</el-button>
  </el-card>
</template>
<style scoped>.sanitized-page { border-bottom:1px solid var(--border); padding:12px 0; } pre { white-space:pre-wrap; overflow-wrap:anywhere; font:inherit; background:var(--surface-muted); padding:12px; } .mono { overflow-wrap:anywhere; } h3 { margin:0; }</style>
