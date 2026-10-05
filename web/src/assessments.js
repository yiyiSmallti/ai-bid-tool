import { computed, onBeforeUnmount, ref } from "vue";
import { onBeforeRouteLeave, onBeforeRouteUpdate } from "vue-router";
import { confirmAction, orgRequest } from "./org.js";

export const severityLabels = { disqualification_risk: "废标风险", deduction_risk: "扣分风险", info: "提示" };
export const domainLabels = { commercial: "商务负责人（投标专员）", technical: "技术负责人", unclassified: "待管理员分配职责" };
export const stateLabels = { current: "当前有效", stale: "已过期", complete: "已完成", partial: "部分完成", candidate: "待审核", confirmed: "已确认", rejected: "已驳回", superseded: "已被新版本替代", open: "待处理", dismissed: "已忽略", response: "响应", comply_only: "须遵守", gap: "缺口", assessed: "已评估", unassessed: "尚未评估", not_requested: "未请求语义检查", no_risk_found: "在已评估范围未发现风险", risk: "发现风险", unknown: "未知", valid: "有效", expired: "已过期", not_yet_valid: "尚未生效" };
const errors = {
  redaction_required: "尚未开启外发遮挡，请由管理员或投标专员处理后重新预览",
  insufficient_balance: "余额不足，暂不能提交；请联系管理员补充余额",
  task_budget_exceeded: "任务预算不足，请联系管理员或投标专员调整预算后重新预览",
  task_budget_unpriced: "任务存在未定价用量，请联系管理员核对计价后重新预览",
  task_budget_currency_review_required: "任务预算币种需要管理员核对",
  job_max_charge_exceeded: "本次作业费用上限不足，请核对后重新预览",
  check_stale_draft: "初稿已过期，请重新组表并预览", score_stale_draft: "初稿已过期，请重新组表并预览",
  check_input_changed: "初稿内容或设置已变化，请重新组表并预览", score_input_changed: "初稿或评分规则已变化，请重新预览评分",
  rubric_input_changed: "评分要求或设置已变化，请重新预览", score_rubric_unconfirmed: "请先完成所选评分规则的审核清单",
  rubric_incomplete: "评分规则审核尚未完成，请检查覆盖、职责和逐项确认", unclassified: "请管理员先分配审核职责",
  revision_conflict: "内容已被他人修改，已刷新当前修订；请核对后再次明确提交",
  assessment_view_changed: "审核快照已变化，请重新读取当前分组", rubric_superseded: "此评分规则已有新版本，请打开新版本",
  invalid_transition: "当前状态不能执行此操作，请核对当前修订", queue_unavailable: "任务已保存，等待重新调度",
  assessment_entry_too_large: "此条记录超过当前显示上限，详情暂不可用", invalid_cursor: "分页已失效，请返回第一页",
  forbidden: "当前角色无权执行此操作", human_required: "此操作需要职责负责人本人登录",
  spend_cap_below_first_call: "费用上限不足以覆盖首次调用，请核对后提高上限并重新预览",
  job_charge_limit_exceeded: "本次作业超过平台单作业上限，请联系管理员核对",
  billing_currency_mismatch: "计费币种不一致，请联系管理员核对",
  rubric_context_limit: "完整评分表超出模型上下文上限，请联系管理员选择支持该范围的模型",
  sensitive_scoring_source: "评分原文包含需要保护的内容，请联系职责负责人核对外发范围",
  empty_scoring_requirements: "所选提取结果没有评分要求，请核对提取范围",
  unknown_price: "模型价格未知，请联系管理员配置后重新预览", unsupported_reasoning: "模型不支持所选推理档位，请核对配置",
  provider_unavailable: "模型服务不可用，请联系管理员核对配置", context_limit_exceeded: "输入超过模型上下文上限，请核对输入或模型配置",
  no_scoring_requirements: "所选提取结果没有评分要求，请核对提取范围",
};
export const assessmentError = (exc) => exc?.status === 404 ? "记录不存在或无权查看" : `${errors[exc?.code] ?? "操作未完成，请核对输入或重新读取"}${exc?.code ? `（${exc.code}）` : ""}`;
export const codeText = (code) => errors[code] ?? `请核对服务状态（${code}）`;
export const enc = encodeURIComponent;
export function suggestedCap(value) {
  if (value == null || !/^\d+(?:\.\d+)?$/.test(String(value))) return "";
  const [whole, fraction = ""] = String(value).split(".");
  let cents = BigInt(whole) * 100n + BigInt((fraction + "00").slice(0, 2));
  if (/[1-9]/.test(fraction.slice(2))) cents++;
  if (cents === 0n) cents = 1n;
  return `${cents / 100n}.${String(cents % 100n).padStart(2, "0")}`;
}
export function queryPath(path, values) {
  const query = new URLSearchParams(Object.entries(values).filter(([, value]) => value !== "" && value != null));
  return `${path}?${query}`;
}
export function useUnsaved(dirty, clear) {
  const beforeUnload = (event) => { if (dirty.value) { event.preventDefault(); event.returnValue = ""; } };
  const reset = () => clear();
  const guard = async () => !dirty.value || await confirmAction("还有未保存的理由或修订内容，离开会丢弃这些内容。", "离开当前页面", "丢弃并离开");
  onBeforeRouteLeave(guard);
  onBeforeRouteUpdate(guard);
  window.addEventListener("beforeunload", beforeUnload);
  window.addEventListener("bid:org-reset", reset);
  onBeforeUnmount(() => { window.removeEventListener("beforeunload", beforeUnload); window.removeEventListener("bid:org-reset", reset); });
}
export function usePreviewInvalidation(invalidate, taskId) {
  const changed = (event) => { const id = event.detail?.taskId ?? event.detail?.task_id; if (!id || id === taskId.value) invalidate(); };
  const hidden = () => { if (document.hidden) invalidate(); };
  window.addEventListener("bid:task-materials-changed", changed);
  window.addEventListener("bid:task-cards-changed", changed);
  document.addEventListener("visibilitychange", hidden);
  onBeforeUnmount(() => { window.removeEventListener("bid:task-materials-changed", changed); window.removeEventListener("bid:task-cards-changed", changed); document.removeEventListener("visibilitychange", hidden); });
}
export function useAssessmentPage(path, filters, changed) {
  const rows = ref([]), meta = ref(null), error = ref(""), loading = ref(false), cursors = ref([null]), position = ref(0);
  let serial = 0;
  const next = computed(() => meta.value?.next_cursor);
  async function load(cursor = null, reset = true) {
    const request = ++serial; loading.value = true; error.value = "";
    // A filter change can switch the row shape (e.g. items to coverage); drop the old page
    // before the request so templates never render rows from the previous part.
    if (reset) { rows.value = []; meta.value = null; }
    try {
      const result = await orgRequest("GET", queryPath(path.value, { view: "console", ...filters.value, limit: 50, cursor }));
      if (request !== serial) return;
      if (reset) { cursors.value = [null]; position.value = 0; }
      rows.value = result.items; meta.value = result.data;
    } catch (exc) {
      if (exc.name === "AbortError" || request !== serial) return;
      error.value = assessmentError(exc);
      if (exc.code === "assessment_view_changed") { rows.value = []; meta.value = null; cursors.value = [null]; position.value = 0; await changed?.(); }
    } finally { if (request === serial) loading.value = false; }
  }
  async function forward() { if (!next.value) return; const cursor = next.value; await load(cursor, false); if (!error.value) { position.value++; cursors.value[position.value] = cursor; } }
  async function back() { if (!position.value) return; const previous = position.value - 1; await load(cursors.value[previous], false); if (!error.value) position.value = previous; }
  const clear = () => { serial++; rows.value = []; meta.value = null; error.value = ""; };
  window.addEventListener("bid:org-reset", clear);
  onBeforeUnmount(() => { clear(); window.removeEventListener("bid:org-reset", clear); });
  return { rows, meta, error, loading, position, next, load, forward, back };
}
