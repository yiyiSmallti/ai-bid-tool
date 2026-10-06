import { onUnmounted, ref } from "vue";
import { ApiError, orgEventStream, orgSession } from "./api.js";
import { orgRequest } from "./org.js";

// Events invalidate projections; only an authenticated snapshot supplies business state.
export function useTaskLive(taskId, snapshot, denied) {
  const status = ref("尚未连接");
  let stopped = true, cursor = null, snapshotReady = false, lastSnapshotError = null, controller = null, timer = null, refreshTimer = null, validityTimer = null, generation = 0, failures = 0, polling = false, refreshFlight = null, refreshAgain = false;
  const seen = new Set();
  function clearTimers() { clearTimeout(timer); clearTimeout(refreshTimer); clearInterval(validityTimer); }
  function stop() { refreshAgain = false; stopped = true; snapshotReady = false; lastSnapshotError = null; cursor = null; generation++; clearTimers(); controller?.abort(); controller = null; seen.clear(); }
  function revoke(exc) { stop(); status.value = "访问已停止"; denied(exc); }
  function terminal(exc) { return [401,403,404].includes(exc.status); }
  async function refresh() {
    if (stopped || document.hidden) return false;
    const run = generation;
    if (refreshFlight?.run === run) { refreshAgain = true; return refreshFlight.promise; }
    const flight = { run, promise: null }; refreshFlight = flight;
    flight.promise = (async () => {
      try {
        const next = await snapshot();
        if (run !== generation || stopped) return false;
        snapshotReady = typeof next === "string" && next.length > 0 && next.length <= 1024;
        if (snapshotReady) { cursor = next; lastSnapshotError = null; }
        else status.value = "读取暂不可用，等待重试";
        return snapshotReady;
      }
      catch (exc) {
        if (run === generation) { snapshotReady = false; lastSnapshotError = exc; }
        if (run === generation && terminal(exc)) revoke(exc);
        else if (run === generation && exc.name !== "AbortError") status.value = "读取暂不可用，等待重试";
        return false;
      }
      finally { if (refreshFlight === flight) refreshFlight = null; if (refreshAgain && run === generation) { refreshAgain = false; scheduleRefresh(); } }
    })();
    return flight.promise;
  }
  function scheduleRefresh() { clearTimeout(refreshTimer); const run = generation; if (!stopped) refreshTimer = setTimeout(() => { if (!stopped && run === generation) refresh(); }, 150); }
  function scheduleConnect(delay) { clearTimeout(timer); const run = generation; if (!stopped) timer = setTimeout(() => { if (!stopped && run === generation) connect(); }, delay); }
  async function receive(event) {
    if (stopped) return;
    failures = 0;
    if (event.type === "heartbeat") { if (typeof event.cursor === "string") cursor = event.cursor; return; }
    if (event.type === "reset_required") { snapshotReady = false; cursor = null; await refresh(); return; }
    if (event.org_id !== orgSession.get()?.orgId || event.task_id !== taskId || typeof event.event_id !== "string" || typeof event.cursor !== "string") throw new ApiError(502, "invalid_event_stream", "进度事件范围不符合契约");
    cursor = event.cursor;
    if (seen.has(event.event_id)) return;
    seen.add(event.event_id); if (seen.size > 2000) seen.delete(seen.values().next().value);
    scheduleRefresh();
  }
  function retry(exc) {
    if (stopped || document.hidden) return;
    failures++; polling = failures >= 3;
    status.value = !snapshotReady ? "正在重新读取快照" : polling ? "轮询更新" : "正在重新连接";
    const delay = Math.max(exc?.retryAfter ?? 0, 1000, Math.min(30000, (polling ? 5000 : 1000) * 2 ** Math.min(failures - (polling ? 3 : 1), 5) * (polling ? 1 : 0.8 + Math.random() * 0.4)));
    scheduleConnect(delay);
  }
  async function connect() {
    if (stopped || document.hidden) return;
    const run = generation;
    // A failed snapshot must recover before stream/poll admission, including retries.
    if (!snapshotReady) {
      const fresh = await refresh();
      if (run !== generation || stopped || document.hidden) return;
      if (!fresh) { retry(lastSnapshotError); return; }
    }
    controller = new AbortController();
    try {
      if (polling) {
        status.value = "轮询更新";
        const params = new URLSearchParams({ limit: "100", wait_seconds: "0" }); if (cursor) params.set("cursor", cursor);
        const result = await orgRequest("GET", `/tasks/${taskId}/events/poll?${params}`, undefined, { signal: controller.signal });
        if (run !== generation || stopped) return;
        if (result.data.reset_required) {
          snapshotReady = false; cursor = null;
          const fresh = await refresh();
          if (run !== generation || stopped) return;
          if (!fresh) { retry(lastSnapshotError); return; }
        }
        else {
          for (const event of result.items) { if (run !== generation || stopped) return; await receive(event); }
          if (run !== generation || stopped) return;
          cursor = result.data.next_cursor ?? result.data.head_cursor ?? cursor;
        }
        if (run !== generation || stopped) return;
        failures = 3; scheduleConnect(result.data.has_more ? 1000 : 5000);
      } else {
        status.value = "实时更新";
        await orgEventStream(`/tasks/${taskId}/events`, cursor, controller.signal, receive);
        if (run === generation && !stopped) retry(snapshotReady ? undefined : lastSnapshotError);
      }
    } catch (exc) {
      if (run !== generation || stopped || exc.name === "AbortError") return;
      if (terminal(exc)) revoke(exc);
      else if (["event_cursor_expired", "invalid_event_cursor"].includes(exc.code) || exc.status === 409) { snapshotReady = false; cursor = null; await refresh(); if (!stopped && run === generation) retry(lastSnapshotError ?? exc); }
      else retry(exc);
    }
  }
  async function start() { stop(); stopped = false; failures = 0; polling = false; const run = generation; const fresh = await refresh(); if (!stopped && run === generation) { validityTimer = setInterval(refresh, 25000); if (fresh) connect(); else retry(lastSnapshotError); } }
  function visibility() { if (stopped) return; const run = generation; clearTimeout(timer); controller?.abort(); if (document.hidden) status.value = "后台已暂停"; else { refresh().then(fresh => { if (!stopped && run === generation) { if (fresh) connect(); else retry(lastSnapshotError); } }); } }
  const reset = () => revoke(new ApiError(401, "session_changed", "单位会话已改变，请重新打开任务"));
  document.addEventListener("visibilitychange", visibility); window.addEventListener("bid:org-reset", reset);
  onUnmounted(() => { stop(); document.removeEventListener("visibilitychange", visibility); window.removeEventListener("bid:org-reset", reset); });
  return { status, start, stop, refresh };
}
