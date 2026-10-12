import { computed, inject, onMounted, provide, ref } from "vue";
import { ApiError, orgSession } from "./api.js";
import { orgAccess, orgRequest } from "./org.js";
import { useTaskLive } from "./task-live.js";

const taskAuthority = Symbol("task-authority");
export function useProvidedTaskAuthority() { return inject(taskAuthority, ref(null)); }
export function canEditTask(access) { return access?.workflow.state === "active" && access.member?.active && ["owner", "contributor"].includes(access.member.role); }
export function canReviewTask(access, domain) { return access?.workflow.state === "active" && access.member?.active && ["owner", "contributor", "reviewer"].includes(access.member.role) && access.member.review_domains.includes(domain); }

// Progress exposes only the current actor's active grant, without a member directory.
export function taskAccessGrant(value) {
  if (value === null) return null;
  if (!value || !["owner", "contributor", "reviewer", "observer"].includes(value.role) || !Array.isArray(value.review_domains) || value.review_domains.some(domain => !["commercial", "technical"].includes(domain)) || new Set(value.review_domains).size !== value.review_domains.length) throw new ApiError(502, "invalid_response", "任务访问授权不符合契约");
  return { role: value.role, review_domains: value.review_domains, active: true };
}
export function useTaskAuthority(taskId, denied) {
  const access = ref(null); let generation = 0;
  async function snapshot() {
    const run = ++generation, current = orgSession.get()?.orgId;
    try {
      const [result, identity] = await Promise.all([orgRequest("GET", `/tasks/${taskId}/progress`), orgRequest("GET", "/org/current")]);
      if (identity.data.org_id !== current || !["admin", "bidder", "technical", "viewer"].includes(identity.data.role)) throw new ApiError(502, "invalid_response", "单位身份范围不符合契约");
      const userId = identity.data.user_id;
      const workflow = result.data.workflow;
      if (workflow.org_id !== current || workflow.task_id !== taskId) throw new ApiError(502, "invalid_response", "任务身份范围不符合契约");
      const member = taskAccessGrant(result.data.access);
      if (run === generation) { orgAccess.role = identity.data.role; orgAccess.userId = userId; access.value = { workflow, member }; }
      return result.data.event_cursor;
    } catch (exc) { if (run === generation) access.value = null; throw exc; }
  }
  const live = useTaskLive(taskId, snapshot, exc => { generation++; access.value = null; denied?.(exc); });
  provide(taskAuthority, access);
  onMounted(live.start);
  return { access, canWrite: computed(() => canEditTask(access.value)), canReview: domain => canReviewTask(access.value, domain), refresh: live.refresh };
}
