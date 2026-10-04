<script setup>
import { computed, nextTick, ref } from "vue";
import { errorText, orgRequest } from "../org.js";
// A Word original shown from its parsed blocks: paragraphs in order, table cells regrouped
// into tables. It is the structure citations point to, not Word's page layout.
const props = defineProps({ modelValue: Boolean, title: { type: String, required: true }, documentId: { type: String, required: true }, highlight: { type: String, default: "" } });
const emit = defineEmits(["update:modelValue"]);
const chunks = ref(null), error = ref(""), body = ref(null);
const sections = computed(() => (chunks.value ?? []).map((chunk) => {
  const items = [];
  for (const block of chunk.blocks ?? []) {
    if (block.kind !== "cell") { items.push({ type: "paragraph", block }); continue; }
    let table = items.at(-1);
    if (table?.type !== "table" || table.table !== block.table) { table = { type: "table", table: block.table, rows: [] }; items.push(table); }
    let row = table.rows.at(-1);
    if (!row || row.row !== block.row) { row = { row: block.row, cells: [] }; table.rows.push(row); }
    row.cells.push(block);
  }
  return { id: chunk.id, path: chunk.blocks?.[0]?.section_path?.join(" / ") ?? "", items };
}));
async function opened() {
  error.value = "";
  try {
    if (!chunks.value) chunks.value = (await orgRequest("GET", `/documents/${props.documentId}/chunks`)).items;
    await nextTick();
    body.value?.querySelector(".is-target")?.scrollIntoView({ block: "center" });
  } catch (exc) { error.value = errorText(exc); }
}
</script>
<template>
  <el-dialog :model-value="modelValue" :title="title" width="min(1000px, 96vw)" top="3vh" append-to-body @update:model-value="emit('update:modelValue', $event)" @opened="opened">
    <p class="hint">按解析出的段落与表格展示 Word 原件{{ highlight ? "，引文所在位置已高亮" : "" }}。分页与字体以下载的原件为准。</p>
    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" role="alert" />
    <el-skeleton v-else-if="!chunks" :rows="8" animated />
    <div v-else ref="body" class="docx-body" tabindex="0" aria-label="Word 原件内容">
      <section v-for="section in sections" :key="section.id">
        <h4 v-if="section.path" class="section-path">{{ section.path }}</h4>
        <template v-for="(item, index) in section.items" :key="index">
          <p v-if="item.type === 'paragraph'" :class="{ 'is-target': item.block.block_id === highlight }">{{ item.block.text }}</p>
          <table v-else class="docx-table"><tbody>
            <tr v-for="row in item.rows" :key="row.row"><td v-for="cell in row.cells" :key="cell.block_id" :class="{ 'is-target': cell.block_id === highlight }">{{ cell.text }}</td></tr>
          </tbody></table>
        </template>
      </section>
    </div>
  </el-dialog>
</template>
<style scoped>
.docx-body { height: 76vh; overflow: auto; padding: 8px 24px; background: #fff; border: 1px solid var(--border); border-radius: 6px; line-height: 1.8; }
.section-path { color: var(--muted); font-weight: 500; font-size: 13px; border-bottom: 1px solid var(--border); padding-bottom: 4px; }
.docx-body p { margin: 6px 0; white-space: pre-wrap; }
.docx-table { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 13px; }
.docx-table td { border: 1px solid #c8ccd4; padding: 6px 8px; vertical-align: top; white-space: pre-wrap; }
.is-target { background: #fff3c4; outline: 2px solid #f0b400; outline-offset: 1px; }
</style>
