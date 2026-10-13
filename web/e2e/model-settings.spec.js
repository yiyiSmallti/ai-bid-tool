// Failure scenarios specified before implementation: secret hydration/echo/storage;
// concurrent saves; implicit repeated calls; preflight/save races;
import { test, expect } from "@playwright/test";
import { openConsole } from "./access-key.js";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { basename, dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../.."), work = join(root, "data/work"), base = resolve(process.env.E2E_OUTPUT ?? join(work, "management-pages-validation")), output = join(base, "models");
function inside(parent, path) {
  const rel = relative(parent, path);
  return rel !== ".." && !rel.startsWith("../") && !rel.startsWith("/");
}
if (!inside(join(work, "management-pages-validation"), base)) throw new Error("E2E_OUTPUT must be inside data/work/management-pages-validation");
let ancestor = output;
while (!existsSync(ancestor)) ancestor = dirname(ancestor);
if (!inside(realpathSync(root), realpathSync(ancestor))) throw new Error("Artifact output escapes worktree");
const staticDir = process.env.E2E_STATIC_DIR ? resolve(process.env.E2E_STATIC_DIR) : null;
if (staticDir && (!inside(root, staticDir) || !existsSync(staticDir) || !inside(realpathSync(root), realpathSync(staticDir)))) throw new Error("E2E_STATIC_DIR escapes worktree");
const J = "00000000-0000-0000-0000-000000000007", date = "2026-10-06T00:00:00Z";
const rid = (n) => `00000000-0000-0000-0000-${String(100 + n).padStart(12, "0")}`;
const zero = { calls: 0, llm_tokens: 0, ocr_pages: 0, search_requests: 0, browser_units: 0, unresolved_calls: 0 };
const actual = { ...zero, calls: 1, llm_tokens: 32 };
const result = (command, data = {}, items = [], ok = true, cost = zero) => ({ ok, command, data, items, warnings: [], cost, duration_ms: 0 });
const secret = "synthetic-one-time-management-key";
function metadata(n = 1) {
  return { id: rid(n), revision: n, configuration: { capability: "llm_extract", provider: "openai", model: `byok-model-${n}`, base_url: "https://byok.example/v1", json_mode: "json_schema", reasoning: [], default_reasoning: null, expected_revision: null }, provider: "openai", model: `byok-model-${n}`, credential_state: "configured", updated_at: date, revised_at: date, reasoning: [], default_reasoning: null };
}
// Match confidential-management: secret scenarios never retain DOM or image captures.
process.env.PLAYWRIGHT_NO_COPY_PROMPT = "1";
const records = [], health = new WeakMap();
test.use({ trace: "off", video: "off", screenshot: "off" });
test.beforeEach(async ({ page }) => {
  const h = { console: 0, runtime: 0 };
  health.set(page, h);
  page.on("pageerror", () => h.runtime++);
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type()) && !message.text().startsWith("Failed to load resource")) h.console++;
  });
  if (staticDir) await page.route((url) => url.pathname.startsWith("/app/"), (route) => {
    const path = new URL(route.request().url()).pathname, file = path.startsWith("/app/assets/") ? join(staticDir, "assets", basename(path)) : join(staticDir, "index.html");
    return route.fulfill({ contentType: { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" }[extname(file)] ?? "application/octet-stream", body: readFileSync(file) });
  });
});
test.afterEach(async ({ page }, info) => {
  mkdirSync(output, { recursive: true });
  if (!inside(realpathSync(work), realpathSync(output))) throw new Error("Artifact symlink escapes worktree");
  await page.locator('input[type="password"]').evaluateAll((nodes) => nodes.forEach((node) => {
    node.value = "";
    node.dispatchEvent(new Event("input", { bubbles: true }));
  }));
  const h = health.get(page), overlay = await page.locator("vite-error-overlay").count();
  records.push({ scenario: info.title, status: info.status, console_errors: h.console, runtime_errors: h.runtime, framework_overlay: overlay, measurements: info.annotations.filter((item) => item.type === "measurement").map((item) => JSON.parse(item.description)) });
  const index = staticDir ? join(staticDir, "index.html") : join(root, "web/dist/index.html");
  writeFileSync(join(output, "result.json"), JSON.stringify({ mode: "mocked_api", fixture_version: "model-settings-v1", schema_version: "5.0", browser: "chromium", browser_version: page.context().browser()?.version() ?? null, build_sha256: existsSync(index) ? createHash("sha256").update(readFileSync(index)).digest("hex") : null, secret_capture: "disabled", reproduction: "E2E_BASE_URL=<local-api> E2E_OUTPUT=../data/work/management-pages-validation node_modules/.bin/playwright test e2e/model-settings.spec.js", scenarios: records, expected_scenarios: 19, complete: records.length === 19, passed: records.length === 19 && records.every((r) => r.status === "passed" && !r.console_errors && !r.runtime_errors && !r.framework_overlay) }, null, 2));
  expect(h.console).toBe(0);
  expect(h.runtime).toBe(0);
  expect(overlay).toBe(0);
});
async function fixture(page, options = {}) {
  const state = { head: 1, ...options }, requests = [], writes = [], queries = [], tests = [], reads = { active: 0, max: 0 };
  let releaseSave = null, releaseRead = null;
  const pageData = (items, next = null) => ({ as_of: date, returned: items.length, next_cursor: next, has_more: next !== null });
  const current = () => state.initial ? null : metadata(state.head);
  await page.route((url) => !url.pathname.startsWith("/app/"), async (route) => {
    const req = route.request(), path = new URL(req.url()).pathname;
    requests.push({ path, method: req.method(), body: req.postData() ?? "" });
    const counted = path.startsWith("/management/providers");
    if (counted) {
      reads.active++;
      reads.max = Math.max(reads.max, reads.active);
    }
    try {
      if (counted && state.readDelay) await new Promise((resolve2) => setTimeout(resolve2, state.readDelay));
      const send = (command, data = {}, items = [], cost = zero) => route.fulfill({ json: result(command, data, items, true, cost) });
      const failure = (status, code, message = "Synthetic failure") => route.fulfill({ status, json: result("provider set", { error: { code, message, exit_code: 4 } }, [], false) });
      if (path === "/auth/session") return req.method() === "POST" ? send("auth session create", { session: "synthetic-models-session", expires_in: 3600 }) : send("auth session show", { actor_kind: "session", scopes: [], expires_at: null });
      if (path === "/management/providers") {
        if (state.delayRead) await new Promise((resolve2) => releaseRead = resolve2);
        const value = current();
        if (state.leak && value) value.key_last4 = "leaked-suffix";
        return send("provider show", { capability: "llm_extract", configured: value !== null, current: value, reasoning: value?.reasoning ?? [], actions: [{ action: "configure", allowed: !state.token, reason: state.token ? "human_required" : null }, { action: "test", allowed: !state.token && value !== null, reason: state.token ? "human_required" : value === null ? "provider_unavailable" : null }], connection_status: "not_checked" });
      }
      if (path === "/management/providers/history/query") {
        const body = req.postDataJSON();
        queries.push({ kind: "history", body });
        const index = body.cursor ? Number(body.cursor.slice(1)) : 0;
        const items = state.manyPages ? Array.from({ length: 25 }, (_, i) => metadata(1e4 - index * 25 - i)) : state.initial ? [] : [metadata(state.head)];
        return send("provider history-page", pageData(items, state.manyPages && index < state.manyPages - 1 ? `p${index + 1}` : null), items);
      }
      if (path.startsWith("/management/providers/revisions/")) {
        const n = Number(path.slice(-12)) - 100;
        return send("provider revision show", metadata(n));
      }
      if (path === "/providers" && req.method() === "POST") {
        const body = req.postDataJSON();
        writes.push(body);
        if (state.delaySave) await new Promise((resolve2) => releaseSave = resolve2);
        if (state.denied) return failure(403, "forbidden", secret);
        if (state.conflict) {
          state.head++;
          state.conflict = false;
          return failure(409, "revision_conflict", secret);
        }
        if (state.validation) return failure(422, "invalid_input", secret);
        state.head++;
        state.initial = false;
        if (state.unknownResponse) return send("provider set", { unverified: true });
        return send("provider set", { id: rid(state.head), revision: state.head, key_last4: "key-suffix-never-render", ignored: "discard-this-receipt" });
      }
      if (path === "/providers/test") {
        const body = req.postDataJSON();
        tests.push(body);
        if (!body.dry_run && state.timeout) return route.abort("timedout");
        if (body.dry_run) return send("provider test", { dry_run: true, preflight: { estimate: zero, admission_blocker: null } }, [], zero);
        const id = rid(state.race ? state.head + 1 : state.head);
        if (state.failedTest) return route.fulfill({ status: 503, json: result("provider test", { job_id: J, status: "failed", provider_config_id: id, usage: { model: "actually-tested-model", provider: "openai" }, error: { code: "provider_unavailable", message: secret, exit_code: 4 } }, [], false, actual) });
        return send("provider test", { job_id: J, status: "succeeded", provider_config_id: id, reasoning: null, usage: { model: "actually-tested-model", provider: "openai" }, result: {} }, [], actual);
      }
      if(req.method()==="GET"&&path==="/health")return route.fulfill({json:{ok:true,command:"health",data:{status:"ok",version:"5.0",real_llm_configured:false},items:[],warnings:[],cost:{ calls: 0, llm_tokens: 0, ocr_pages: 0, search_requests: 0, browser_units: 0, unresolved_calls: 0 },duration_ms:0}});
      throw new Error(`Unexpected models request ${req.method()} ${path}`);
    } finally {
      if (counted) reads.active--;
    }
  });
  return { state, requests, writes, queries, tests, reads, releaseSave: () => releaseSave?.(), releaseRead: () => releaseRead?.() };
}
async function open(page) {
  await openConsole(page, "/org/settings/models");
  await expect(page.getByRole("heading", { name: "模型与服务配置", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "已保存的有效配置", exact: true })).toBeVisible();
}
async function edit(page) {
  await page.getByRole("button", { name: "修改模型配置", exact: true }).click();
}
async function freshByok(page) {
  await edit(page);
  await page.getByLabel("模型名称", { exact: true }).fill("fresh-model");
  await page.getByLabel("HTTPS 端点", { exact: true }).fill("https://byok.example/v1");
}
async function privateState(page, text = secret) {
  const value = await page.evaluate(async () => ({ url: location.href, storage: JSON.stringify({ ...localStorage, ...sessionStorage }), databases: (await indexedDB.databases()).map((item) => item.name) }));
  expect(value.url.includes(text)).toBe(false);
  expect(value.storage.includes(text)).toBe(false);
  expect(value.databases).toEqual([]);
}
test("metadata opening has no vendor request and history bounded", async ({ page }) => {
  const f = await fixture(page);
  await open(page);
  // Element Plus renders header/body tables inside one named region.
  const history = page.getByRole("region", { name: "配置修订历史", exact: true });
  await expect(history).toBeVisible();
  await expect(history.getByRole("columnheader", { name: "修订", exact: true })).toBeVisible();
  await expect(history.getByRole("button", { name: "查看修订 1", exact: true })).toBeVisible();
  expect(f.requests.some((req) => req.path === "/providers" && req.method === "GET")).toBe(false);
  expect(f.tests).toEqual([]);
  expect(f.queries[0].body).toEqual({ cursor: null, limit: 25 });
});
test("fresh password blank consumed once cleared before response and never persisted", async ({ page }) => {
  const f = await fixture(page, { delaySave: true });
  await open(page);
  await edit(page);
  const key = page.getByLabel("新 API 密钥", { exact: true });
  await expect.poll(() => key.evaluate(node => node.value === "")).toBe(true);
  await key.fill(secret);
  await page.getByRole("button", { name: "保存模型配置", exact: true }).click();
  await expect.poll(() => key.evaluate(node => node.value === "")).toBe(true);
  await expect.poll(() => f.writes.length).toBe(1);
  await privateState(page);
  expect(f.requests.filter((req) => req.body.includes(secret)).length).toBe(1);
  expect(f.writes[0].api_key === secret).toBe(true);
  expect(f.writes[0].expected_revision).toBe(1);
  f.releaseSave();
  await expect(page.getByText(/配置已保存并重新读取/)).toBeVisible();
  await expect(page.getByText("key-suffix-never-render")).toHaveCount(0);
  await edit(page);
  await expect.poll(() => page.getByLabel("新 API 密钥", { exact: true }).evaluate(node => node.value === "")).toBe(true);
});
test("server key reuse omits api key when provider and endpoint unchanged", async ({ page }) => {
  const f = await fixture(page);
  await open(page);
  await edit(page);
  await page.getByLabel("模型名称", { exact: true }).fill("changed-model");
  await page.getByRole("button", { name: "保存模型配置", exact: true }).click();
  await expect.poll(() => f.writes.length).toBe(1);
  expect(Object.hasOwn(f.writes[0], "api_key")).toBe(false);
  expect(f.writes[0].expected_revision).toBe(1);
});
test("initial byok requires fresh key and expected revision null", async ({ page }) => {
  const f = await fixture(page, { initial: true });
  await open(page);
  await freshByok(page);
  await expect(page.getByRole("button", { name: "保存模型配置", exact: true })).toBeDisabled();
  await page.getByLabel("新 API 密钥", { exact: true }).fill(secret);
  await page.getByRole("button", { name: "保存模型配置", exact: true }).click();
  await expect.poll(() => f.writes.length).toBe(1);
  expect(f.writes[0].expected_revision).toBeNull();
});
test("endpoint change requires key and retains no browser credential", async ({ page }) => {
  await fixture(page);
  await open(page);
  await edit(page);
  await page.getByLabel("HTTPS 端点", { exact: true }).fill("https://changed.example/v1");
  await expect(page.getByRole("button", { name: "保存模型配置", exact: true })).toBeDisabled();
  await privateState(page, "changed.example");
});
test("unexpected secret field rejects entire safe projection", async ({ page }) => {
  await fixture(page, { leak: true });
  await openConsole(page, "/org/settings/models");
  await expect(page.getByRole("alert").filter({ hasText: /元数据范围/ })).toBeVisible();
  await expect(page.getByText("leaked-suffix")).toHaveCount(0);
});
test("concurrency conflict clears key retains draft and rereads revision", async ({ page }) => {
  const f = await fixture(page, { conflict: true });
  await open(page);
  await edit(page);
  await page.getByLabel("模型名称", { exact: true }).fill("nonsecret-draft");
  await page.getByLabel("新 API 密钥", { exact: true }).fill(secret);
  await page.getByRole("button", { name: "保存模型配置", exact: true }).click();
  await expect.poll(() => page.getByLabel("新 API 密钥", { exact: true }).evaluate(node => node.value === "")).toBe(true);
  await expect(page.getByLabel("模型名称", { exact: true })).toHaveValue("nonsecret-draft");
  await expect(page.getByText("保存预期修订：2")).toBeVisible();
  expect(f.writes.length).toBe(1);
  expect((await page.locator("body").innerText()).includes(secret)).toBe(false);
});
test("validation response never echoes key and leaves password blank", async ({ page }) => {
  const f = await fixture(page, { validation: true });
  await open(page);
  await edit(page);
  await page.getByLabel("新 API 密钥", { exact: true }).fill(secret);
  await page.getByRole("button", { name: "保存模型配置", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("配置参数无效");
  await expect.poll(() => page.getByLabel("新 API 密钥", { exact: true }).evaluate(node => node.value === "")).toBe(true);
  expect((await page.locator("body").innerText()).includes(secret)).toBe(false);
  expect(f.writes.length).toBe(1);
});
test("test explicit preflight then start shows actual identity and consumes preflight", async ({ page }) => {
  const f = await fixture(page, { race: true });
  await open(page);
  await expect(page.getByRole("button", { name: "明确开始连接测试", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "预检测试范围", exact: true }).click();
  await expect.poll(() => f.tests.length).toBe(1);
  expect(f.tests[0].dry_run).toBe(true);
  await page.getByRole("button", { name: "明确开始连接测试", exact: true }).click();
  await expect(page.getByText(/测试时配置已变化/)).toBeVisible();
  await expect(page.getByText(/actually-tested-model/)).toBeVisible();
  await expect(page.getByRole("button", { name: "明确开始连接测试", exact: true })).toBeDisabled();
  expect(f.tests).toHaveLength(2);
  expect(f.requests.some((req) => req.path === `/management/providers/revisions/${rid(2)}`)).toBe(true);
});
test("model timeout never automatically repeats test", async ({ page }) => {
  const f = await fixture(page, { timeout: true });
  await open(page);
  await page.getByRole("button", { name: "预检测试范围", exact: true }).click();
  await expect(page.getByRole("button", { name: "明确开始连接测试", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "明确开始连接测试", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("可能已发出调用");
  await expect(page.getByRole("button", { name: "明确开始连接测试", exact: true })).toBeDisabled();
  await page.waitForTimeout(100);
  expect(f.tests.filter((body) => !body.dry_run)).toHaveLength(1);
});
test("unsaved navigation warns and session teardown discards late metadata", async ({ page }) => {
  const f = await fixture(page);
  await open(page);
  await edit(page);
  await page.getByLabel("新 API 密钥", { exact: true }).fill(secret);
  await page.getByRole("link", { name: "产品库", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("放弃未保存的配置");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  expect(await page.getByLabel("新 API 密钥", { exact: true }).evaluate((node) => node.value === "synthetic-one-time-management-key")).toBe(true);
  await page.evaluate(() => window.dispatchEvent(new Event("bid:session-reset")));
  await expect(page.getByLabel("新 API 密钥", { exact: true })).toHaveCount(0);
  expect(f.writes.length).toBe(0);
  await privateState(page);
});
test("many bounded history pages release old page objects", async ({ page }, info) => {
  test.setTimeout(12e4);
  await page.addInitScript(() => {
    window.__modelRetention = { refs: [], bytes: 0, pages: 0 };
    const json = Response.prototype.json;
    Response.prototype.json = async function(...args) {
      const payload = await json.apply(this, args);
      if (payload.command === "provider history-page") {
        const tracker = window.__modelRetention, bytes = (value) => new TextEncoder().encode(JSON.stringify(value)).length;
        tracker.bytes += bytes(payload);
        tracker.pages++;
        tracker.refs.push({ ref: new WeakRef(payload.items), bytes: bytes(payload.items) }, { ref: new WeakRef(payload.data), bytes: bytes(payload.data) });
      }
      return payload;
    };
  });
  const pages = 60;
  await fixture(page, { manyPages: pages });
  await open(page);
  await expect(page.getByRole("button", { name: "查看修订 10000", exact: true })).toBeVisible();
  for (let n = 1; n < pages; n++) {
    await page.getByRole("button", { name: "下一页历史", exact: true }).click();
    await expect(page.getByRole("button", { name: `查看修订 ${1e4 - n * 25}`, exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: `查看修订 ${1e4 - (n - 1) * 25}`, exact: true })).toHaveCount(0);
  }
  const cdp = await page.context().newCDPSession(page);
  try {
    await cdp.send("HeapProfiler.collectGarbage");
  } finally {
    await cdp.detach();
  }
  const measured = await page.evaluate(() => {
    const t = window.__modelRetention, live = t.refs.filter((item) => item.ref.deref());
    return { pages: t.pages, transferred_bytes: t.bytes, retained_bytes: live.reduce((sum, item) => sum + item.bytes, 0), retained_arrays: live.filter((item) => Array.isArray(item.ref.deref())).length };
  });
  expect(measured.pages).toBe(pages);
  expect(measured.retained_bytes).toBeLessThanOrEqual(2 * 1024 * 1024);
  expect(measured.retained_arrays).toBeLessThanOrEqual(1);
  info.annotations.push({ type: "measurement", description: JSON.stringify(measured) });
});
test("failed model test retains safe actual resource receipt without vendor error echo", async ({ page }) => {
  const f = await fixture(page, { failedTest: true });
  await open(page);
  await page.getByRole("button", { name: "预检测试范围", exact: true }).click();
  await expect(page.getByRole("button", { name: "明确开始连接测试", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "明确开始连接测试", exact: true }).click();
  await expect(page.getByText(/实际测试状态：failed/)).toBeVisible();
  await expect(page.getByText("实际用量：调用 1 次 · 32 tokens · 未确定调用 0", { exact: true })).toBeVisible();
  expect((await page.locator("body").innerText()).includes(secret)).toBe(false);
  expect(f.tests.filter((body) => !body.dry_run)).toHaveLength(1);
});
test("unverified save rereads safe metadata retains nonsecret draft and does not resend key", async ({ page }) => {
  const f = await fixture(page, { unknownResponse: true });
  await open(page);
  await edit(page);
  await page.getByLabel("模型名称", { exact: true }).fill("retained-after-uncertain-save");
  await page.getByLabel("新 API 密钥", { exact: true }).fill(secret);
  await page.getByRole("button", { name: "保存模型配置", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: /保存结果尚未确定/ })).toBeVisible();
  await expect(page.getByLabel("模型名称", { exact: true })).toHaveValue("retained-after-uncertain-save");
  await expect.poll(() => page.getByLabel("新 API 密钥", { exact: true }).evaluate(node => node.value === "")).toBe(true);
  await expect(page.getByText("保存预期修订：2")).toBeVisible();
  expect(f.writes.length).toBe(1);
  expect(f.requests.filter((req) => req.body.includes(secret)).length).toBe(1);
});
test("session reset cancels pending metadata and ignores late response", async ({ page }) => {
  const f = await fixture(page, { delayRead: true });
  await openConsole(page, "/org/settings/models");
  await expect.poll(() => f.requests.filter((req) => req.path === "/management/providers").length).toBe(1);
  await page.evaluate(() => window.dispatchEvent(new Event("bid:session-reset")));
  f.releaseRead();
  await expect(page.getByText(/byok-model-1/)).toHaveCount(0);
  expect(f.requests.some((req) => req.path.endsWith("/history/query"))).toBe(false);
  await privateState(page);
});
test("unsaved edit blocks preflight and server denial clears fresh key", async ({ page }) => {
  const f = await fixture(page, { denied: true });
  await open(page);
  await edit(page);
  await expect(page.getByRole("button", { name: "预检测试范围", exact: true })).toBeDisabled();
  await page.getByLabel("新 API 密钥", { exact: true }).fill(secret);
  await page.getByRole("button", { name: "保存模型配置", exact: true }).click();
  await expect(page.getByLabel("新 API 密钥", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("alert")).toContainText("未保存内容已清除");
  expect(f.writes.length).toBe(1);
  expect(f.tests).toHaveLength(0);
  await privateState(page);
});
test("built mocked first actionable and bounded row read performance", async ({ page }, info) => {
  const f = await fixture(page, { readDelay: 25, manyPages: 2 });
  const started = Date.now();
  await open(page);
  await expect(page.getByRole("button", { name: "修改模型配置", exact: true })).toBeEnabled();
  const firstActionable = Date.now() - started;
  await page.getByRole("button", { name: "重新读取配置", exact: true }).click();
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => f.reads.active).toBe(0);
  await edit(page);
  await expect(page.getByLabel("新 API 密钥", { exact: true })).toBeVisible();
  await expect.poll(() => f.reads.active).toBe(0);
  const rows = await page.locator(".el-table__body tbody tr").count();
  expect(rows).toBeLessThanOrEqual(100);
  expect(f.reads.max).toBeLessThanOrEqual(2);
  expect(f.queries.every((item) => item.body.limit === 25)).toBe(true);
  expect(firstActionable).toBeLessThanOrEqual(1500);
  info.annotations.push({ type: "measurement", description: JSON.stringify({ first_actionable_ms: firstActionable, target_ms: 1500, rendered_rows: rows, rows_limit: 100, max_in_flight_reads: f.reads.max, reads_limit: 2, mode: "built_mocked_api" }) });
});
test("narrow screen keyboard edits and saves with blank reused credential", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 800 });
  const f = await fixture(page);
  await open(page);
  await edit(page);
  const model = page.getByLabel("模型名称", { exact: true });
  await model.focus();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type("keyboard-model");
  await expect.poll(() => page.getByLabel("新 API 密钥", { exact: true }).evaluate(node => node.value === "")).toBe(true);
  await page.getByRole("button", { name: "保存模型配置", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText(/配置已保存并重新读取/)).toBeVisible();
  expect(f.writes.length).toBe(1);
  expect(Object.hasOwn(f.writes[0], "api_key")).toBe(false);
  expect(f.writes[0].model).toBe("keyboard-model");
  const nav = page.getByRole("navigation", { name: "导航", exact: true });
  const navigation = await nav.evaluate(node => ({
    right: node.getBoundingClientRect().right,
    viewport: innerWidth,
    overflow: getComputedStyle(node).overflowX,
    content: node.scrollWidth,
    container: node.clientWidth,
  }));
  expect(navigation.overflow).toBe("auto");
  expect(navigation.content).toBeGreaterThan(navigation.container);
  expect(navigation.right).toBeLessThanOrEqual(navigation.viewport + 1);
  // The last entry remains keyboard reachable; only its own container scrolls.
  await nav.getByRole("link", { name: "保密字段", exact: true }).focus();
  await expect.poll(() => nav.evaluate(node => node.scrollLeft)).toBeGreaterThan(0);
  const width = await page.evaluate(() => ({ document: document.documentElement.scrollWidth, viewport: innerWidth }));
  expect(width.document).toBeLessThanOrEqual(width.viewport + 1);
});
test("server human-only configuration and test hints are respected", async ({ page }) => {
  const f = await fixture(page, { token: true });
  await open(page);
  await expect(page.getByRole("button", { name: "修改模型配置", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "预检测试范围", exact: true })).toBeDisabled();
  expect(f.writes.length).toBe(0);
  expect(f.tests).toHaveLength(0);
});
