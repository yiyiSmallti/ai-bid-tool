import { test, expect } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, extname, join, resolve } from "node:path";

const output = resolve(process.env.E2E_OUTPUT ?? "../data/work/bid-review-findings/browser");
const uuid = n => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const ids = { org: uuid(1), user: uuid(2), task: uuid(3), submission: uuid(4), tender: uuid(5), bid: uuid(6), review: uuid(7), job: uuid(8) };
const hash = "a".repeat(64), now = "2026-10-08T00:00:00Z";
const unsafe = "合成引用 <img src=x onerror=window.__unsafe=1>", cleared = "[投标单位] 已声明响应合成要求。";
const zero = { llm_tokens: 0, ocr_pages: 0, usd: 0, basis: "zero", charge: "0", billing_currency: "USD", task_amount: "0", unpriced_calls: 0, unresolved_calls: 0 };
const result = (command, data, items = [], ok = true) => ({ ok, command, data, items, warnings: [], cost: zero, duration_ms: 0 });

async function fixture(page, options = {}) {
  if (!process.env.E2E_STATIC_DIR) throw new Error("E2E_STATIC_DIR required; this suite starts no services");
  const directory = resolve(process.env.E2E_STATIC_DIR), origin = new URL(process.env.E2E_BASE_URL ?? "https://console.test").origin;
  if (!origin.startsWith("https://")) throw new Error("HTTPS fixture origin required");
  mkdirSync(output, { recursive: true });
  const state = { role: "bidder", taskRole: "reviewer", domains: ["commercial"], conflict: false, stale: false, paged: false, reads: [], writes: [], errors: [], unexpected: [], events: [], ...options };
  const citations = [{ document_id: ids.tender, page_id: uuid(20), page: 3, quote: unsafe, start_offset: 0, end_offset: unsafe.length, page_label: "original_pdf", location: null }];
  state.rows = ["fatal", "high", "medium"].map((severity, index) => ({ id: uuid(30 + index), review_id: ids.review, task_id: ids.task, obligation_id: uuid(40 + index), code: "synthetic_finding", title: ["合成强制项缺失", "合成参数偏离", "合成未确定项"][index], outcome: ["missing", "deviation", "unknown"][index], severity, impact: index === 0 ? "rejection" : index === 1 ? "lost_points" : "uncertain", basis: { kind: "rule", rule_or_prompt_version: "synthetic-v1" }, tender_support: citations, bid_support: index === 1 ? [{ document_id: ids.bid, page_id: uuid(21), page: 5, quote: cleared, start_offset: 0, end_offset: cleared.length, page_label: "original_pdf", location: null }] : [], absence_search: index === 1 ? null : { kind: "locations", searched_pages: [{ page_id: uuid(21), document_id: ids.bid, page: 5, role: "bid" }], method: "local_text", coverage: index === 2 ? "partial" : "all_bid_pages", limitation_codes: index === 2 ? ["bid_review_coverage_partial"] : [] }, explanation: "合成检验说明", remediation: "按原件人工核对", limitation_codes: index === 2 ? ["bid_review_coverage_partial"] : [], state: "open", revision: 2, review_domain: state.role === "technical" ? "technical" : "commercial", classification_id: uuid(50 + index), latest_decision_id: uuid(50 + index), advisory_only: true }));
  if (state.role === "admin") for (const row of state.rows) Object.assign(row, { revision: 1, review_domain: null, classification_id: null, latest_decision_id: null });
  const run = () => ({ id: ids.review, task_id: ids.task, submission_id: ids.submission, job_id: ids.job, status: "succeeded", completion: "partial", input_hash: hash, created_at: now, coverage: {}, uncovered_codes: [], validity: state.stale ? "stale" : "current", advisory_only: true, review_slice: "compliance" });
  const metadata = next => ({ review_id: ids.review, input_hash: hash, validity: state.stale ? "stale" : "current", next_cursor: next });
  const project = row => ["technical", "viewer"].includes(state.role) ? { ...row, tender_support: row.tender_support.map(citation => ({ ...citation, quote: "已脱敏招标引用。" })) } : row;
  page.on("pageerror", error => state.errors.push(error.message));
  await page.addInitScript(org => sessionStorage.setItem("bid.org.session", JSON.stringify({ session: "synthetic-session", orgId: org, orgName: "合成单位", email: "review@example.test" })), ids.org);
  await page.route("**/*", async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname.replace(/^\/v4(?=\/)/, ""), method = request.method();
    if (url.origin !== origin) { state.unexpected.push(`${method} external-origin`); return route.abort(); }
    if (path.startsWith("/app/")) {
      const file = path.startsWith("/app/assets/") ? join(directory, "assets", basename(path)) : join(directory, "index.html");
      return route.fulfill({ contentType: { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" }[extname(file)] ?? "application/octet-stream", body: readFileSync(file) });
    }
    if (path === "/health") return route.fulfill({ json: result("health", { org_signup_enabled: false }) });
    expect(request.headers()["x-org-id"]).toBe(ids.org);
    let response;
    if (path === "/org/current" && method === "GET") response = result("org current", { org_id: ids.org, user_id: ids.user, role: state.role });
    else if (path === `/tasks/${ids.task}/workflow` && method === "GET") response = result("task workflow", { workflow: { org_id: ids.org, task_id: ids.task, state: "active", owner_user_id: state.taskRole === "owner" ? ids.user : uuid(99) } });
    else if (path === `/tasks/${ids.task}/members` && method === "GET") response = result("task member list", { org_id: ids.org, task_id: ids.task, next_cursor: null }, [{ user_id: ids.user, role: state.taskRole, active: true, review_domains: state.domains }]);
    else if (path === `/bid-submissions/${ids.submission}` && method === "GET") response = result("review submission show", { submission: { id: ids.submission, org_id: ids.org, task_id: ids.task, revision: 1, manifest_sha256: hash, state: "prepared", created_by: ids.user, created_at: now, documents: [] }, preparation: null, inventory: [], signature_validations: [], signing_candidates: [], signing_candidate_count: 0, signing_candidates_next_cursor: null });
    // Admin/bidder owners also load the outbound-authorization history component.
    else if (path === `/bid-submissions/${ids.submission}/outbound-authorizations` && method === "GET") response = result("review outbound list", { total: 0, next_cursor: null }, []);
    else if (path === `/bid-submissions/${ids.submission}/redaction` && method === "GET") response = result("review redaction", { submission_id: ids.submission, expected_revision: 1, expected_authorization_id: null, expected_submission_manifest_sha256: hash, expected_preparation_input_hash: hash, expected_redaction_manifest_sha256: hash, provider_bindings_sha256: hash, redaction_revision: 1, redaction_rule_version: "synthetic-v1", confidential_binding_sha256: hash, derived_name_lists_sha256: hash, human_name_revision: 0, pages: [], total: 0, next_cursor: null, blockers: [] });
    else if (path === `/tasks/${ids.task}/bid-reviews` && method === "GET") response = result("review list", { task_id: ids.task, next_cursor: null }, [run()]);
    else if (path === `/bid-reviews/${ids.review}` && method === "GET") { expect(["obligations", "signing_requirements"]).toContain(url.searchParams.get("section")); response = result("review show", { run: run(), obligations: [], signing_requirements: [], next_cursor: null }, [], false); }
    else if (path === `/bid-reviews/${ids.review}/findings` && method === "GET") {
      expect(url.pathname.startsWith("/v4/")).toBe(true); expect(url.searchParams.get("limit")).toBe("50");
      if (state.stale && ["technical", "viewer"].includes(state.role)) return route.fulfill({ status: 409, json: result("review findings", { error: { code: "bid_review_input_changed" } }, [], false) });
      let rows = state.rows.filter(row => ["severity", "state", "outcome"].every(key => !url.searchParams.get(key) || row[key] === url.searchParams.get(key)));
      const isNext = url.searchParams.get("cursor") === "synthetic-next";
      if (state.paged && !isNext) rows = rows.slice(0, 1); else if (state.paged) rows = rows.slice(1);
      response = result("review findings", metadata(state.paged && !isNext ? "synthetic-next" : null), rows.map(project));
    } else if (path.startsWith(`/bid-reviews/${ids.review}/findings/`)) {
      expect(url.pathname.startsWith("/v4/")).toBe(true);
      const parts = path.split("/"), row = state.rows.find(item => item.id === parts[4]), section = parts[5];
      if (!row || !["classification", "decisions"].includes(section)) { state.unexpected.push(`${method} ${path}`); return route.abort(); }
      if (method === "GET") {
        expect(url.searchParams.get("limit")).toBe("50");
        const events = state.events.filter(event => event.finding_id === row.id && (section !== "classification" || event.action === "classify"));
        const isNext = url.searchParams.get("cursor") === "history-next";
        response = result("review history", metadata(events.length > 1 && !isNext ? "history-next" : null), events.slice(isNext ? 1 : 0, isNext ? undefined : 1));
      } else if (method === "POST") {
        const body = request.postDataJSON(); expect(body.reason.trim()).not.toBe(""); expect(body.expected_revision).toBe(row.revision); expect(body.expected_input_hash).toBe(hash); expect(body.expected_decision_id).toBe(row.latest_decision_id);
        if (state.conflict) return route.fulfill({ status: 409, json: result("review decide", { error: { code: "bid_finding_revision_conflict" } }, [], false) });
        const classify = section === "classification";
        if (classify) { expect(state.role).toBe("admin"); expect(body).not.toHaveProperty("action"); }
        else { expect(state.role).not.toBe("admin"); expect(body).not.toHaveProperty("review_domain"); }
        const event = { id: uuid(100 + state.events.length), review_id: ids.review, task_id: ids.task, finding_id: row.id, prior_decision_id: row.latest_decision_id, revision: row.revision + 1, action: classify ? "classify" : body.action, review_domain: classify ? body.review_domain : row.review_domain, state: classify ? row.state : body.action === "dismiss" ? "dismissed" : body.action === "confirm" ? "confirmed" : "open", reason: body.reason, reason_sha256: hash, decided_by: ids.user, decided_at: now, actor_kind: "session", immutable: true };
        state.events.push(event); state.writes.push(body); Object.assign(row, { state: event.state, revision: event.revision, review_domain: event.review_domain, latest_decision_id: event.id }); if (classify) row.classification_id = event.id;
        response = result(classify ? "review classify" : "review decide", event);
      } else { state.unexpected.push(`${method} ${path}`); return route.abort(); }
    } else { state.unexpected.push(`${method} ${path}`); return route.abort(); }
    state.reads.push(`${method} ${path}${url.search}`);
    return route.fulfill({ json: response });
  });
  return state;
}

async function open(page) { await page.goto(`/app/org/tasks/${ids.task}/bid-submissions/${ids.submission}`); await page.getByRole("button", { name: "查看检验结果", exact: true }).click(); await expect(page.getByRole("heading", { name: "发现项与人工审查", exact: true })).toBeVisible(); }
async function choose(page, label, option) { const wrapper = page.locator(".el-select").filter({ has: page.getByRole("combobox", { name: label, exact: true }) }).locator(".el-select__wrapper"); await expect(wrapper).toBeVisible(); await wrapper.click(); await page.getByRole("option", { name: option, exact: true }).click(); }
async function save(page, action, reason = "合成人工审查理由") { const dialog = page.getByRole("dialog").filter({ has: page.getByLabel("发现项审查理由") }); await expect(dialog.getByRole("button", { name: `保存${action}`, exact: true })).toBeDisabled(); await dialog.getByLabel("发现项审查理由").fill("   "); await expect(dialog.getByRole("button", { name: `保存${action}`, exact: true })).toBeDisabled(); await dialog.getByLabel("发现项审查理由").fill(reason); await dialog.getByRole("button", { name: `保存${action}`, exact: true }).click(); }
async function artifact(page, state, name) { await page.screenshot({ path: join(output, `${name}.png`), fullPage: true }); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]); expect(await page.evaluate(() => window.__unsafe)).toBeUndefined(); expect(await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))).not.toContain("合成人工审查理由"); writeFileSync(join(output, `${name}.json`), JSON.stringify({ passed: true, reads: state.reads, writeCount: state.writes.length, actions: state.events.map(({ action, revision }) => ({ action, revision })), errors: state.errors, unexpected: state.unexpected }, null, 2)); }

test("commercial reviewer reads anchors and dismisses, reopens, confirms with required reasons and paged immutable history", async ({ page }) => {
  const state = await fixture(page); await open(page);
  const first = page.getByTestId("bid-finding").first(); await expect(first).toContainText("招标原件第 3 页"); await expect(first).toContainText("投标第 5 页"); await expect(first).toContainText(unsafe); await expect(first.locator("img")).toHaveCount(0);
  await first.getByRole("button", { name: "驳回发现项", exact: true }).click(); await save(page, "驳回"); await expect(first).toContainText("当前决定：已驳回 · 修订 3"); await expect(first).toContainText("机器原始结论：缺失");
  await first.getByRole("button", { name: "重新打开发现项", exact: true }).click(); await save(page, "重新打开"); await expect(first).toContainText("当前决定：待审查 · 修订 4");
  await first.getByRole("button", { name: "确认发现项", exact: true }).click(); await save(page, "确认"); await expect(first).toContainText("当前决定：已确认 · 修订 5");
  await first.getByRole("button", { name: "决定历史", exact: true }).click(); await expect(page.getByRole("table", { name: "发现项历史", exact: true })).toContainText("合成人工审查理由"); await page.getByRole("button", { name: "更多历史记录", exact: true }).click(); await expect(page.getByRole("table", { name: "发现项历史", exact: true })).toContainText("重新打开"); expect(state.events.map(row => row.action)).toEqual(["dismiss", "reopen", "confirm"]); await artifact(page, state, "01-review-history");
});

test("server filters and bounded findings pagination retain groups", async ({ page }) => {
  const state = await fixture(page, { paged: true }); await open(page); await expect(page.getByTestId("bid-finding")).toHaveCount(1); await page.getByRole("button", { name: "更多发现项", exact: true }).click(); await expect(page.getByTestId("bid-finding")).toHaveCount(3);
  state.paged = false; await choose(page, "风险筛选", "高风险缺陷"); await expect(page.getByTestId("bid-finding")).toHaveCount(1); await expect(page.getByTestId("bid-finding")).toContainText(cleared);
  expect(state.reads.some(path => path.includes("severity=high"))).toBe(true); await artifact(page, state, "02-filter-pagination");
});

test("administrator classifies without gaining a deciding control", async ({ page }) => {
  const state = await fixture(page, { role: "admin", taskRole: "owner", domains: [] }); await open(page); const first = page.getByTestId("bid-finding").first();
  await expect(page.getByRole("button", { name: "驳回发现项", exact: true })).toHaveCount(0); await first.getByRole("button", { name: "分类发现项", exact: true }).click(); await choose(page, "发现项专业职责", "技术"); await save(page, "分类"); await expect(first).toContainText("技术"); await expect(first).toContainText("修订 2"); await expect(page.getByRole("button", { name: "确认发现项", exact: true })).toHaveCount(0); expect(state.events[0].action).toBe("classify"); await artifact(page, state, "03-admin-classification");
});

test("technical reviewer receives cleared evidence and acts only in assigned domain", async ({ page }) => {
  const state = await fixture(page, { role: "technical", domains: ["technical"] }); await open(page); const second = page.getByTestId("bid-finding").nth(1); await expect(second).toContainText(cleared); await second.getByRole("button", { name: "确认发现项", exact: true }).click(); await save(page, "确认"); expect(state.events[0].review_domain).toBe("technical"); await artifact(page, state, "04-technical-review");
});

test("viewer projections show cleared evidence without deciding controls", async ({ page }) => {
  const state = await fixture(page, { role: "viewer", taskRole: "observer", domains: [] }); await open(page); await expect(page.getByTestId("bid-finding")).toHaveCount(3); await expect(page.getByRole("button", { name: "决定历史", exact: true })).toHaveCount(3); await expect(page.getByRole("button", { name: "确认发现项", exact: true })).toHaveCount(0); expect(await page.locator("body").innerText()).not.toContain(unsafe); expect(await page.locator("body").innerText()).toContain(cleared); expect(state.writes).toHaveLength(0); await artifact(page, state, "05-safe-reader");
});

test("a commercial reviewer without the finding domain cannot decide", async ({ page }) => {
  const state = await fixture(page, { role: "bidder", domains: ["technical"] }); await open(page); await expect(page.getByTestId("bid-finding")).toHaveCount(3); await expect(page.getByRole("button", { name: "驳回发现项", exact: true })).toHaveCount(0); await expect(page.getByRole("button", { name: "确认发现项", exact: true })).toHaveCount(0); await expect(page.getByRole("button", { name: "分类发现项", exact: true })).toHaveCount(0); expect(state.writes).toHaveLength(0); await artifact(page, state, "08-domain-gate");
});

test("concurrent revision conflict clears dialog and prevents blind retry", async ({ page }) => {
  const state = await fixture(page, { conflict: true }); await open(page); await page.getByTestId("bid-finding").first().getByRole("button", { name: "驳回发现项", exact: true }).click(); await save(page, "驳回"); await expect(page.getByRole("alert")).toContainText("此次操作未写入"); await expect(page.getByLabel("发现项审查理由")).not.toBeVisible(); await expect(page.getByRole("button", { name: "驳回发现项", exact: true })).toHaveCount(0); expect(state.writes).toHaveLength(0); await artifact(page, state, "06-conflict");
});

test("stale protected report remains readable and fences decisions", async ({ page }) => {
  const state = await fixture(page, { stale: true }); await open(page); await expect(page.getByTestId("bid-finding")).toHaveCount(3); await expect(page.getByRole("button", { name: "确认发现项", exact: true })).toHaveCount(0); expect(await page.locator("body").innerText()).toContain(unsafe); expect(state.writes).toHaveLength(0); await artifact(page, state, "07-stale");
});


test("stale technical clearance blocks quotations", async ({ page }) => {
  const state = await fixture(page, { role: "technical", domains: ["technical"], stale: true }); await open(page); await expect(page.getByRole("alert").filter({ hasText: "已变化" }).first()).toBeVisible(); await expect(page.getByTestId("bid-finding")).toHaveCount(0); expect(await page.locator("body").innerText()).not.toContain(cleared); expect(state.writes).toHaveLength(0); await artifact(page, state, "09-stale-clearance");
});
