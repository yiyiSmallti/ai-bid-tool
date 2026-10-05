<script setup>
import { onMounted, ref, watch } from "vue";
// Plain text in which `{{secret.key}}` shows as a labelled block. Fields are dragged or
// clicked in from the strip above, so nobody types a placeholder; the model value stays text.
const props = defineProps({
  modelValue: { type: String, default: "" },
  fields: { type: Array, default: () => [] },
  disabled: Boolean,
  label: { type: String, default: "" },
  maxlength: { type: Number, default: 20000 },
  rows: { type: Number, default: 4 },
});
const emit = defineEmits(["update:modelValue", "change"]);
const SECRET = /\{\{secret\.([a-z][a-z0-9_]{1,47})\}\}/g;
const MIME = "application/x-bid-secret";
const editor = ref(null);
let emitted = null, composing = false, saved = null;

function chip(key) {
  const field = props.fields.find((item) => item.key === key);
  const node = document.createElement("span");
  node.className = field ? "secret-chip" : "secret-chip unknown";
  node.contentEditable = "false";
  node.dataset.key = key;
  node.textContent = field ? field.label : `未登记：${key}`;
  node.title = `导出时填入“${field?.label ?? key}”的值`;
  return node;
}
function render(text) {
  const root = editor.value;
  if (!root) return;
  root.replaceChildren();
  let cursor = 0;
  for (const match of (text ?? "").matchAll(SECRET)) {
    if (match.index > cursor) root.append(document.createTextNode(text.slice(cursor, match.index)));
    root.append(chip(match[1]));
    cursor = match.index + match[0].length;
  }
  if (cursor < (text ?? "").length) root.append(document.createTextNode(text.slice(cursor)));
}
function serialize(node = editor.value) {
  let out = "";
  for (const child of node.childNodes) {
    if (child.nodeType === Node.TEXT_NODE) out += child.nodeValue.replace(/ /g, " ");
    else if (child.dataset?.key) out += `{{secret.${child.dataset.key}}}`;
    else if (child.nodeName === "BR") out += "\n";
    else {
      // Browsers may wrap a new line in a block element; it starts a line of its own.
      if (["DIV", "P"].includes(child.nodeName) && out && !out.endsWith("\n")) out += "\n";
      out += serialize(child);
    }
  }
  return out;
}
function sync() {
  if (composing || !editor.value) return;
  const value = serialize();
  remember();
  emitted = value;
  emit("update:modelValue", value);
}
function caret() {
  const selection = window.getSelection();
  return selection?.rangeCount && editor.value?.contains(selection.getRangeAt(0).startContainer) ? selection.getRangeAt(0).cloneRange() : null;
}
function remember() { saved = caret() ?? saved; }
function place(key, range) {
  if (props.disabled || !editor.value) return;
  if (!range || !editor.value.contains(range.startContainer)) {
    range = document.createRange();
    range.selectNodeContents(editor.value);
    range.collapse(false);
  }
  range.deleteContents();
  const node = chip(key);
  range.insertNode(node);
  range.setStartAfter(node);
  range.collapse(true);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  saved = range.cloneRange();
  sync();
  emit("change");
}
// The strip keeps focus in the editor (mousedown is prevented), so the live caret is
// where the user is typing; the remembered one covers a click after leaving the editor.
function insert(key) { const range = caret() ?? saved; editor.value?.focus(); place(key, range); }
function startDrag(event, key) {
  event.dataTransfer.setData(MIME, key);
  event.dataTransfer.setData("text/plain", `{{secret.${key}}}`);
  event.dataTransfer.effectAllowed = "copy";
}
function pointRange(x, y) {
  if (document.caretRangeFromPoint) return document.caretRangeFromPoint(x, y);
  const position = document.caretPositionFromPoint?.(x, y);
  if (!position) return null;
  const range = document.createRange();
  range.setStart(position.offsetNode, position.offset);
  return range;
}
function drop(event) {
  const key = event.dataTransfer.getData(MIME);
  if (!key) return;
  event.preventDefault();
  const range = pointRange(event.clientX, event.clientY);
  editor.value?.focus();
  place(key, range);
}
function keydown(event) {
  if (event.key === "Enter" && !event.isComposing) {
    event.preventDefault();
    document.execCommand("insertText", false, "\n");
  }
}
function paste(event) {
  event.preventDefault();
  const text = event.clipboardData.getData("text/plain");
  const room = props.maxlength - serialize().length;
  document.execCommand("insertText", false, text.slice(0, Math.max(0, room)));
}
watch(() => props.modelValue, (value) => { if (value !== emitted) { emitted = value; render(value); } });
// Labels arrive after the text when fields load later.
watch(() => props.fields, () => render(props.modelValue ?? ""));
onMounted(() => { emitted = props.modelValue; render(props.modelValue ?? ""); });
defineExpose({ insert });
</script>
<template>
  <div class="secret-text">
    <div v-if="!disabled && fields.length" class="palette" aria-label="保密字段，拖入正文或点击插入">
      <span class="hint">拖入或点击插入：</span>
      <button v-for="field in fields" :key="field.key" type="button" class="secret-chip" draggable="true" :title="`插入“${field.label}”，导出时填入其值`" @mousedown.prevent @click="insert(field.key)" @dragstart="startDrag($event, field.key)">{{ field.label }}</button>
    </div>
    <div
      ref="editor"
      class="editor"
      :class="{ disabled }"
      role="textbox"
      aria-multiline="true"
      :aria-label="label"
      :aria-disabled="disabled"
      :contenteditable="disabled ? 'false' : 'true'"
      :style="{ minHeight: `${rows * 1.6 + 1}em` }"
      @input="sync"
      @keydown="keydown"
      @paste="paste"
      @keyup="remember"
      @mouseup="remember"
      @blur="remember(); render(modelValue ?? '')"
      @compositionstart="composing = true"
      @compositionend="composing = false; sync()"
      @dragover.prevent
      @drop="drop"
    />
  </div>
</template>
<style scoped>
.secret-text { width: 100%; }
.palette { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 6px; }
.editor {
  width: 100%; box-sizing: border-box; padding: 5px 11px; line-height: 1.6; white-space: pre-wrap; word-break: break-word;
  border: 1px solid var(--el-border-color); border-radius: var(--el-border-radius-base); background: var(--el-fill-color-blank);
  color: var(--el-text-color-regular); font-size: var(--el-font-size-base); outline: none; max-height: 24em; overflow-y: auto;
}
.editor:focus { border-color: var(--el-color-primary); }
.editor.disabled { background: var(--el-disabled-bg-color); color: var(--el-disabled-text-color); cursor: not-allowed; }
:deep(.secret-chip) {
  display: inline-block; margin: 0 2px; padding: 0 8px; border-radius: 10px; line-height: 1.6; font-size: 12px;
  background: var(--el-color-primary-light-9); color: var(--el-color-primary); border: 1px solid var(--el-color-primary-light-7);
  white-space: nowrap; user-select: none; cursor: default;
}
button.secret-chip { cursor: grab; font-family: inherit; }
:deep(.secret-chip.unknown) { background: var(--el-color-danger-light-9); color: var(--el-color-danger); border-color: var(--el-color-danger-light-7); }
</style>
