import { computed, inject, onMounted, provide, ref } from "vue";
import { ApiError, orgSession } from "./api.js";
import { orgAccess, orgRequest } from "./org.js";
import { useTaskLive } from "./task-live.js";

const taskAuthority = Symbol("task-authority");
export function useProvidedTaskAuthority() { return inject(taskAuthority, ref(null)); }
export function canEditTask(access) { return access?.workflow.state === "active" && access.member?.active && ["owner", "contributor"].includes(access.member.role); }
export function canReviewTask(access, domain) { return access?.workflow.state === "active" && access.member?.active && ["owner", "contributor", "reviewer"].includes(access.member.role) && access.member.review_domains.includes(domain); }

// Member pages are bounded; retain only the current member, never a second directory.
export function useTaskAuthority(taskId, denied) {
  const access = ref(null); let generation = 0;
  async function snapshot() {
    const run = ++generation, current = orgSession.get()?.orgId;
    try {
      const [result, identity] = await Promise.all([orgRequest("GET", `/tasks/${taskId}/workflow`), orgRequest("GET", "/org/current")]);
      if (identity.data.org_id !== current || !["admin", "bidder", "technical", "viewer"].includes(identity.data.role)) throw new ApiError(502, "invalid_response", "单位身份范围不符合契约");
      const userId = identity.data.user_id;
      const workflow = result.data.workflow;
      if (workflow.org_id !== current || workflow.task_id !== taskId) throw new ApiError(502, "invalid_response", "任务身份范围不符合契约");
      let cursor = null, member = null;
      const cursors = new Set();
      do {
        const params = new URLSearchParams({ limit: "100" }); if (cursor) params.set("cursor", cursor);
        const page = await orgRequest("GET", `/tasks/${taskId}/members?${params}`);
        if (page.data.org_id !== current || page.data.task_id !== taskId || page.items.length > 100) throw new ApiError(502, "invalid_response", "任务成员范围不符合契约");
        member = page.items.find(item => item.user_id === userId) ?? null;
        cursor = page.data.next_cursor;
        if (cursor && cursors.has(cursor)) throw new ApiError(502, "invalid_response", "任务成员分页重复");
        if (cursor) cursors.add(cursor);
      } while (!member && cursor);
      if (run === generation) { orgAccess.role = identity.data.role; orgAccess.userId = userId; access.value = { workflow, member }; }
      return workflow.last_event_cursor;
    } catch (exc) { if (run === generation) access.value = null; throw exc; }
  }
  const live = useTaskLive(taskId, snapshot, exc => { generation++; access.value = null; denied?.(exc); });
  provide(taskAuthority, access);
  onMounted(live.start);
  return { access, canWrite: computed(() => canEditTask(access.value)), canReview: domain => canReviewTask(access.value, domain), refresh: live.refresh };
}
