import { ElMessageBox } from "element-plus/es/components/message-box/index";

export const formatTime = (value) => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "未知";

// Resolves true only when the person explicitly confirms; closing the box counts as cancel.
export async function confirmAction(message, title = "请确认", confirmText = "确定", danger = false) {
  try {
    await ElMessageBox.confirm(message, title, { confirmButtonText: confirmText, cancelButtonText: "取消", type: danger ? "warning" : "info", confirmButtonClass: danger ? "el-button--danger" : "" });
    return true;
  } catch { return false; }
}
