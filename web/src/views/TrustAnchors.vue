<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { request } from "../api.js";
import { formatTime } from "../ui.js";
const anchors = ref([]), label = ref(""), selected = ref(null), preview = ref(null), busy = ref(false), error = ref(""), notice = ref(""), disabling = ref(null), input = ref(null);
const controller = new AbortController();
const disableOpen = computed({ get: () => !!disabling.value, set: value => { if (!value) disabling.value = null; } });
let mounted = true, selection = 0;
const messages = { invalid_input: "证书或标签无效，请选择 64 KiB 以内的 PEM/DER CA 证书", invalid_trust_anchor: "证书不是有效的 PEM/DER CA 证书，请核对原件", trust_store_unavailable: "信任库暂时无法访问，请稍后重试", trust_anchor_capacity: "信任根保留数量已达到上限", trust_anchor_invalid: "无法解析 CA 证书", trust_anchor_not_ca: "此证书没有 CA 属性，不能作为信任根", trust_anchor_limit: "证书超过 64 KiB 上限", not_found: "信任根不可访问，请刷新", forbidden: "当前身份无权维护信任根", invalid_session: "平台登录已失效，请重新登录" };
function fail(exc) { error.value = messages[exc.code] ?? "操作未完成，请刷新后再试"; }
async function load() {
  busy.value = true; error.value = "";
  try { const result = await request("GET", "/platform/trust-anchors", undefined, { signal: controller.signal }); if (mounted) anchors.value = result.items; }
  catch (exc) { if (mounted && exc.name !== "AbortError") fail(exc); }
  finally { if (mounted) busy.value = false; }
}
function invalidate() { selection++; preview.value = null; notice.value = ""; }
function select(event) {
  invalidate(); error.value = ""; selected.value = event.target.files?.[0] ?? null;
  if (selected.value && (!selected.value.size || selected.value.size > 65536)) { selected.value = null; event.target.value = ""; error.value = messages.trust_anchor_limit; }
}
function multipart() { const body = new FormData(); body.append("label", label.value.trim()); body.append("certificate", selected.value, "certificate"); return body; }
async function inspect() {
  const current = selection;
  busy.value = true; error.value = ""; preview.value = null;
  try { const result = await request("POST", "/platform/trust-anchors/preview", multipart(), { signal: controller.signal }); if (mounted && current === selection) preview.value = result.data; }
  catch (exc) { if (mounted && exc.name !== "AbortError") fail(exc); }
  finally { if (mounted) busy.value = false; }
}
async function save() {
  if (!preview.value || busy.value) return;
  busy.value = true; error.value = "";
  try {
    const result = await request("POST", "/platform/trust-anchors", multipart(), { signal: controller.signal });
    if (!mounted) return;
    notice.value = result.data.created ? "信任根已添加，后续准备将使用新的信任快照" : "此证书已存在，保留原有启停状态";
    selected.value = null; preview.value = null; label.value = ""; if (input.value) input.value.value = "";
    await load();
  } catch (exc) { if (mounted && exc.name !== "AbortError") fail(exc); }
  finally { if (mounted) busy.value = false; }
}
async function disable() {
  const anchor = disabling.value;
  if (!anchor || busy.value) return;
  busy.value = true; error.value = "";
  try {
    await request("POST", `/platform/trust-anchors/${encodeURIComponent(anchor.id)}/disable`, {}, { signal: controller.signal });
    if (!mounted) return;
    disabling.value = null; notice.value = "信任根已停用；已有准备结果仍引用原有固定快照"; await load();
  } catch (exc) { if (mounted && exc.name !== "AbortError") fail(exc); }
  finally { if (mounted) busy.value = false; }
}
onMounted(load);
onBeforeUnmount(() => { mounted = false; controller.abort(); selected.value = null; preview.value = null; disabling.value = null; });
</script>
<template>
  <div class="page-header"><div><h2>信任根证书</h2><p class="subtitle">本地 PDF 签名验证使用启用的 CA 证书；每次准备固定信任快照。</p></div><el-button :loading="busy" @click="load">刷新</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" role="alert" class="section" />
  <el-alert v-if="notice" :title="notice" type="success" :closable="false" role="status" class="section" />
  <el-card class="section" shadow="never"><template #header><h3>添加信任根</h3></template>
    <el-form label-position="top" @submit.prevent="inspect">
      <el-form-item label="标签"><el-input v-model="label" aria-label="信任根标签" maxlength="100" :disabled="busy" @input="invalidate" /></el-form-item>
      <el-form-item label="CA 证书"><input ref="input" type="file" aria-label="选择 PEM 或 DER 证书" accept=".pem,.der,.crt,.cer" :disabled="busy" @change="select" /></el-form-item>
      <p class="hint">支持 PEM/DER，最多 64 KiB。解析预览不保存证书；添加前请与 CA 官方公布的 SHA-256 指纹比对。</p>
      <el-button :loading="busy" :disabled="!selected || !label.trim()" @click="inspect">解析证书预览</el-button>
    </el-form>
    <div v-if="preview" class="section" data-testid="trust-anchor-preview">
      <dl class="kv"><dt>标签</dt><dd>{{ preview.label }}</dd><dt>SHA-256 指纹</dt><dd class="mono">{{ preview.fingerprint_sha256 }}</dd><dt>主体</dt><dd>{{ preview.subject }}</dd><dt>颁发者</dt><dd>{{ preview.issuer }}</dd><dt>有效期</dt><dd>{{ formatTime(preview.not_before) }} 至 {{ formatTime(preview.not_after) }}</dd><dt>CA 属性</dt><dd>{{ preview.is_ca ? '是' : '否' }}</dd></dl>
      <el-button type="primary" :loading="busy" @click="save">添加此信任根</el-button>
    </div>
  </el-card>
  <el-table role="table" :data="anchors" aria-label="信任根证书列表" empty-text="尚未添加信任根证书">
    <el-table-column label="证书" min-width="280"><template #default="{ row }"><strong>{{ row.label }}</strong><div class="mono hint">{{ row.fingerprint_sha256 }}</div><p>主体：{{ row.subject }}</p><p>颁发者：{{ row.issuer }}</p></template></el-table-column>
    <el-table-column label="有效期" min-width="220"><template #default="{ row }">{{ formatTime(row.not_before) }} 至 {{ formatTime(row.not_after) }}</template></el-table-column>
    <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '已启用' : '已停用' }}</el-tag></template></el-table-column>
    <el-table-column label="管理记录" min-width="220"><template #default="{ row }">第 {{ row.revision }} 版 · {{ row.created_by }}<div>{{ formatTime(row.created_at) }}</div><div v-if="row.disabled_at">{{ row.disabled_by }} · {{ formatTime(row.disabled_at) }} 停用</div></template></el-table-column>
    <el-table-column label="操作" width="110"><template #default="{ row }"><el-button v-if="row.enabled" size="small" :disabled="busy" @click="disabling = row">停用</el-button></template></el-table-column>
  </el-table>
  <el-dialog v-model="disableOpen" title="停用信任根证书" width="480" :close-on-click-modal="false">
    <p>停用 {{ disabling?.label }} 后，后续准备不会使用此证书。已有准备结果保留原有固定信任快照。</p>
    <template #footer><el-button :disabled="busy" @click="disabling = null">取消</el-button><el-button type="danger" :loading="busy" @click="disable">确认停用</el-button></template>
  </el-dialog>
</template>
<style scoped>
h3 { margin:0; }
.mono { overflow-wrap:anywhere; }
</style>
