<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { orgRequest } from "../org.js";
import { reviewError } from "../bid-review.js";
const props = defineProps({ taskId: String, submissionId: String, reviewId: String, allowed: Boolean, authorizationEpoch: Number });
const emit = defineEmits(["changed"]);
const preview = ref(null), selected = ref([]), pixels = ref({}), consent = ref(false), reason = ref(""), busy = ref(false), error = ref("");
const path = computed(() => `/bid-submissions/${encodeURIComponent(props.submissionId)}`);
const chosen = computed(() => preview.value?.images.filter(image => selected.value.includes(image.id) && pixels.value[image.id]) ?? []);
const sourceMatches = computed(() => preview.value?.source_review_id === props.reviewId);
let epoch = 0, controller;
function clearPixels() { for (const url of Object.values(pixels.value)) URL.revokeObjectURL(url); pixels.value = {}; selected.value = []; consent.value = false; }
function clear() { clearPixels(); preview.value = null; reason.value = ""; }
function validate(data) {
  if (data.submission_id !== props.submissionId || data.purpose !== "bid_review_presence" || !Array.isArray(data.images) || data.images.length > 40 || !Array.isArray(data.blockers) || !Number.isInteger(data.expected_revision)) throw new Error("presence preview parent mismatch");
  const seen = new Set();
  for (const image of data.images) {
    if (seen.has(image.id) || !/^[a-f0-9]{64}$/.test(image.sha256) || !/^[a-f0-9]{64}$/.test(image.source_sha256) || image.size_bytes <= 0 || image.size_bytes > 4 * 1024 * 1024 || image.width_px <= 0 || image.height_px <= 0 || image.width_px > 8192 || image.height_px > 8192 || image.width_px * image.height_px > 16_000_000) throw new Error("presence derivative invalid");
    seen.add(image.id);
  }
}
async function show(data, token) {
  validate(data);
  clear(); preview.value = data;
  // Fetch exact authenticated JPEG bytes, then verify the authorized hash before
  // showing any pixels. No remote URL or authorization token enters an img tag.
  for (const image of data.images) {
    const blob = await orgRequest("GET", `/bid-presence-images/${encodeURIComponent(image.id)}/content`, undefined, { signal: controller.signal, binary: true });
    if (token !== epoch) return;
    if (blob.type !== "image/jpeg" || blob.size !== image.size_bytes) throw new Error("presence derivative size mismatch");
    const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
    if (token !== epoch) return;
    if ([...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("") !== image.sha256) throw new Error("presence derivative hash mismatch");
    const url = URL.createObjectURL(blob);
    if (token !== epoch) { URL.revokeObjectURL(url); return; }
    pixels.value = { ...pixels.value, [image.id]: url };
  }
  emit("changed", data.current ? data.authorization_id : null);
}
async function load() {
  const token = ++epoch; controller?.abort(); controller = new AbortController(); clear(); error.value = ""; busy.value = true;
  try { if (props.allowed) await show((await orgRequest("GET", `${path.value}/presence-preview`, undefined, { signal: controller.signal })).data, token); }
  catch (exc) { if (token === epoch && exc.name !== "AbortError") { clearPixels(); emit("changed", null); error.value = reviewError(exc); } }
  finally { if (token === epoch) busy.value = false; }
}
async function prepare() {
  const token = epoch; if (!props.allowed || busy.value) return;
  busy.value = true; error.value = "";
  try { await show((await orgRequest("POST", `${path.value}/presence-preparations`, { review_id: props.reviewId }, { signal: controller.signal })).data, token); emit("changed", null); }
  catch (exc) { if (token === epoch && exc.name !== "AbortError") { clearPixels(); emit("changed", null); error.value = reviewError(exc); } }
  finally { if (token === epoch) busy.value = false; }
}
async function authorize(allowExternal) {
  if (!props.allowed || busy.value || !preview.value || !reason.value.trim()) return;
  if (allowExternal && (!sourceMatches.value || !consent.value || !chosen.value.length || preview.value.blockers.length || Object.keys(pixels.value).length !== preview.value.images.length)) return;
  if (!allowExternal && !preview.value.revocable_authorization) return;
  const token = epoch, fixed = preview.value;
  const receipt = allowExternal ? fixed : fixed.revocable_authorization;
  const body = { request_id: crypto.randomUUID(), expected_revision: fixed.expected_revision, expected_authorization_id: fixed.expected_authorization_id, manifest_sha256: receipt.manifest_sha256, image_ids: allowExternal ? chosen.value.map(image => image.id) : receipt.image_ids, privacy_reviewed: true, allow_external: allowExternal, reason: reason.value.trim() };
  busy.value = true; error.value = "";
  try {
    const result = await orgRequest("POST", `${path.value}/presence-authorizations`, body, { signal: controller.signal });
    if (token !== epoch) return;
    if (result.data.purpose !== "bid_review_presence" || result.data.manifest_sha256 !== receipt.manifest_sha256 || result.data.allow_external !== allowExternal) throw new Error("presence authorization mismatch");
    emit("changed", allowExternal ? result.data.id : null); await load();
  } catch (exc) { if (token === epoch && exc.name !== "AbortError") { consent.value = false; error.value = reviewError(exc); } }
  finally { if (token === epoch) busy.value = false; }
}
watch(() => [props.submissionId, props.reviewId, props.allowed, props.authorizationEpoch], load, { immediate: true });
watch(selected, () => { consent.value = false; }, { deep: true });
onBeforeUnmount(() => { epoch++; controller?.abort(); clear(); });
</script>
<template>
  <el-card class="section" shadow="never"><template #header><h3>签章初筛图片授权</h3></template>
    <p>只从本次检验已确认的签章目标页生成全页模糊 JPEG。请逐页核对下方实际外发像素，确认文字、印章名称和身份信息均不可读。报价页及未确认目标页禁止外发。</p>
    <p>此授权仅用于签章存在性初筛（bid_review_presence），与文本授权分开。准备、脱敏、服务配置或授权人职责变化后需要重新核对。</p>
    <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" />
    <el-button :loading="busy" :disabled="!allowed" @click="prepare">生成本次目标页模糊图片</el-button>
    <el-button :disabled="busy" @click="load">刷新图片授权</el-button>
    <template v-if="preview">
      <p role="status">{{ preview.current ? '图片授权有效' : '图片尚未授权或授权已失效' }}</p>
      <p v-if="!sourceMatches">尚无本次检验的目标页图片；请先生成。</p>
      <p v-for="code in preview.blockers" :key="code">{{ reviewError({ code }) }}</p>
      <el-checkbox-group v-model="selected" aria-label="选择初筛图片">
      <div v-for="image in preview.images" :key="image.id" class="presence-image section" data-testid="presence-image">
        <el-checkbox :value="image.id" :disabled="busy || !sourceMatches || !pixels[image.id]">投标第 {{ image.page }} 页初筛图片</el-checkbox>
        <img v-if="pixels[image.id]" :src="pixels[image.id]" :alt="`投标第 ${image.page} 页实际外发模糊图片`" />
        <p>外发 JPEG SHA-256 <code>{{ image.sha256 }}</code></p><p>源页 SHA-256 <code>{{ image.source_sha256 }}</code></p>
      </div>
      </el-checkbox-group>
      <el-form label-position="top"><el-form-item label="图片授权或撤销理由"><el-input v-model="reason" aria-label="图片授权或撤销理由" maxlength="2000" :disabled="busy" /></el-form-item></el-form>
      <el-checkbox v-model="consent" :disabled="busy || !chosen.length">已逐页核对所选模糊图片，同意仅用于签章存在性初筛</el-checkbox>
      <div class="actions section"><el-button type="primary" :loading="busy" :disabled="!consent || !chosen.length || !reason.trim() || !sourceMatches || preview.blockers.length > 0" @click="authorize(true)">授权所选初筛图片</el-button><el-button v-if="preview.revocable_authorization" type="danger" plain :disabled="busy || !reason.trim()" @click="authorize(false)">撤销图片外发授权</el-button></div>
      <p>授权完成后重新预检并运行检验，初筛结果仍需人工确认。</p>
    </template>
  </el-card>
</template>
<style scoped>h3 { margin:0; } .el-checkbox { display:block; } .presence-image img { display:block;max-width:100%;max-height:600px;border:1px solid var(--el-border-color);margin:12px 0; } code { overflow-wrap:anywhere; }</style>
