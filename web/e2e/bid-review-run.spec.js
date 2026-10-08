import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, extname, join, resolve } from "node:path";

const output = resolve(process.env.E2E_OUTPUT ?? "../data/work/bid-review-run/browser");
const uuid = n => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const ids = { org: uuid(1), user: uuid(2), task: uuid(3), submission: uuid(4), document: uuid(5), bid: uuid(6), grant: uuid(7), job: uuid(8), review: uuid(9) };
const hash = "a".repeat(64), now = "2026-10-08T00:00:00Z", quote = "投标文件须逐页加盖公章。<img src=x onerror=window.__unsafe=1>";
const zero = { llm_tokens: 0, ocr_pages: 0, usd: 0, basis: "zero", charge: "0", billing_currency: "USD", task_amount: "0", unpriced_calls: 0, unresolved_calls: 0 };
const result = (command, data = {}, items = [], ok = true) => ({ ok, command, data, items, warnings: [], cost: zero, duration_ms: 0 });
const scopeHash = pages => createHash("sha256").update(JSON.stringify(pages.slice().sort((a, b) => a.page_id.localeCompare(b.page_id)).map(page => ({ page_id: page.page_id, sanitized_text_sha256: page.sanitized_text_sha256 })))).digest("hex");

async function fixture(page, options = {}) {
  if (!process.env.E2E_STATIC_DIR) throw new Error("E2E_STATIC_DIR required; this suite starts no services");
  mkdirSync(output, { recursive: true });
  const directory = resolve(process.env.E2E_STATIC_DIR), origin = new URL(process.env.E2E_BASE_URL ?? "https://console.test").origin;
  if (!origin.startsWith("https://")) throw new Error("HTTPS origin required for exact page scope hashing");
  const state = { role: "bidder", owner: true, partial: true, staleSubmit: false, blockers: [], grants: [], authorizations: 0, revocations: 0, namesWrites: 0, namesRevision: 0, previews: 0, submissions: 0, reads: [], errors: [], unexpected: [], submitted: false, ...options };
  page.on("pageerror", error => state.errors.push(error.message));
  await page.addInitScript(({ org }) => sessionStorage.setItem("bid.org.session", JSON.stringify({ session: "synthetic-review-session", orgId: org, orgName: "合成单位", email: "bidder@example.test" })), { org: ids.org });
  const pages = [
    { page_id: uuid(20), document_id: ids.document, page: 1, role: "tender", sanitized_text_sha256: hash, sanitized_text: quote, price_page: false, price_classification: "non_price", notes: [], redaction_counts: {}, outbound_eligible: true },
    { page_id: uuid(21), document_id: ids.bid, page: 1, role: "bid", sanitized_text_sha256: hash, sanitized_text: "[投标单位] 承诺逐页核对。", price_page: false, price_classification: "non_price", notes: [], redaction_counts: { derived_names: 1 }, outbound_eligible: true },
    { page_id: uuid(22), document_id: ids.bid, page: 2, role: "bid", sanitized_text_sha256: hash, sanitized_text: "", price_page: true, price_classification: "price", notes: ["price_page_excluded"], redaction_counts: {}, outbound_eligible: false },
    { page_id: uuid(23), document_id: ids.bid, page: 3, role: "bid", sanitized_text_sha256: hash, sanitized_text: "", price_page: false, price_classification: "uncertain", notes: ["uncertain_price_classification"], redaction_counts: {}, outbound_eligible: false },
  ];
  const redaction = () => ({ submission_id: ids.submission, expected_revision: (state.grants[0]?.revision ?? 0) + 1, expected_authorization_id: state.grants[0]?.id ?? null, expected_submission_manifest_sha256: hash, expected_preparation_input_hash: hash, expected_redaction_manifest_sha256: hash, provider_bindings_sha256: hash, redaction_revision: 1, redaction_rule_version: "synthetic-v1", confidential_binding_sha256: hash, derived_name_lists_sha256: hash, human_name_revision: state.namesRevision, pages, total: pages.length, next_cursor: null, blockers: [] });
  const run = () => ({ id: ids.review, task_id: ids.task, submission_id: ids.submission, job_id: ids.job, status: state.submitted ? "succeeded" : "queued", completion: state.submitted ? state.partial ? "partial" : "complete" : null, input_hash: hash, created_at: now, coverage: { tender_pages_total: 1, tender_pages_authorized: 1, tender_pages_assessed: 1 }, uncovered_codes: state.partial ? ["signing_presence_unchecked"] : [], advisory_only: true, validity: "current", review_slice: "compliance" });
  const citation = { document_id: ids.document, page_id: uuid(20), page: 1, quote, location: null, page_label: "original_pdf" };
  const detail = { submission: { id: ids.submission, org_id: ids.org, task_id: ids.task, revision: 1, manifest_sha256: hash, state: "prepared", created_by: ids.user, created_at: now, preparation_job_id: uuid(30), preparation_input_hash: hash, documents: [
    { id: ids.document, role: "tender", kind: "tender", media_type: "application/pdf", sha256: hash, size_bytes: 1000 },
    { id: ids.bid, role: "bid", kind: "commercial_technical", media_type: "application/pdf", sha256: hash, size_bytes: 1000 },
  ] }, preparation: null, inventory: [], signature_validations: [], signing_candidates: [], signing_candidate_count: 0, signing_candidates_next_cursor: null };
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
    else if (path === `/tasks/${ids.task}/workflow` && method === "GET") response = result("task workflow", { workflow: { org_id: ids.org, task_id: ids.task, state: "active", owner_user_id: state.owner ? ids.user : uuid(99) } });
    else if (path === `/tasks/${ids.task}/members` && method === "GET") response = result("task member list", { org_id: ids.org, task_id: ids.task, next_cursor: null }, [{ user_id: ids.user, role: state.role === "viewer" ? "observer" : state.owner ? "owner" : "contributor", active: true }]);
    else if (path === `/bid-submissions/${ids.submission}` && method === "GET") response = result("review submission show", detail);
    else if (path === `/bid-submissions/${ids.submission}/redaction` && method === "GET") { expect(state.owner).toBe(true); expect(url.searchParams.get("cursor")).toBe("0"); expect(url.searchParams.get("limit")).toBe("25"); response = result("review redaction", redaction()); }
    else if (path === `/bid-submissions/${ids.submission}/outbound-authorizations` && method === "GET") { expect(state.owner).toBe(true); expect(url.searchParams.get("limit")).toBe("100"); const cursor = Number(url.searchParams.get("cursor")), width = state.grantPageSize ?? 100; response = result("review outbound list", { total: state.grants.length, next_cursor: cursor + width < state.grants.length ? cursor + width : null }, state.grants.slice(cursor, cursor + width)); }
    else if (path === `/bid-submissions/${ids.submission}/outbound-authorizations` && method === "POST") {
      const body = request.postDataJSON(), fixed = redaction();
      for (const key of ["expected_revision", "expected_authorization_id", "expected_submission_manifest_sha256", "expected_preparation_input_hash", "expected_redaction_manifest_sha256", "provider_bindings_sha256"]) expect(body[key]).toEqual(fixed[key]);
      expect(body.pages).toEqual(pages.slice(0, 2).map(({ page_id, sanitized_text_sha256 }) => ({ page_id, sanitized_text_sha256 })));
      expect(body.authorized_sanitized_context_sha256).toBe(scopeHash(body.pages)); expect(body.privacy_reviewed).toBe(true); expect(body.allow_external).toBe(true); expect(body.purposes).toEqual(["bid_review_text"]); expect(body.allowed_capabilities).toEqual(["llm"]);
      expect(body).not.toHaveProperty("sanitized_text");
      state.authorizations++;
      state.grants = [{ id: ids.grant, org_id: ids.org, task_id: ids.task, submission_id: ids.submission, submission_manifest_sha256: hash, preparation_input_hash: hash, redaction_manifest_sha256: hash, authorized_sanitized_context_sha256: body.authorized_sanitized_context_sha256, provider_bindings_sha256: hash, revision: body.expected_revision, prior_authorization_id: body.expected_authorization_id, allow_external: true, purposes: body.purposes, allowed_capabilities: body.allowed_capabilities, pages: body.pages, authorized_by: ids.user, authorized_at: now, reason_sha256: hash, actor_kind: "session", immutable: true, price_pages_included: false, current: true }];
      response = result("review outbound authorize", state.grants[0]);
    } else if (path === `/bid-submissions/${ids.submission}/outbound-authorizations/revoke` && method === "POST") {
      const body = request.postDataJSON(); expect(body.expected_revision).toBe(state.grants[0].revision); expect(body.expected_authorization_id).toBe(ids.grant);
      state.revocations++; state.grants = [{ ...state.grants[0], revision: state.grants[0].revision + 1, allow_external: false }]; response = result("review outbound revoke", state.grants[0]);
    } else if (path === `/bid-submissions/${ids.submission}/redaction/names` && method === "POST") {
      const body = request.postDataJSON(); expect(body.expected_revision).toBe(state.namesRevision); expect(body.bidder_names).toEqual(["Synthetic supplemental bidder"]); expect(body.staff_names).toEqual(["Synthetic supplemental staff"]);
      state.namesRevision++; state.namesWrites++; state.grants = state.grants.map(row => ({ ...row, current: false }));
      response = result("review redaction names", { id: uuid(55), revision: state.namesRevision, names_sha256: hash, bidder_name_count: 1, staff_name_count: 1 });
    } else if (path === `/tasks/${ids.task}/bid-reviews` && method === "GET") { expect(url.searchParams.get("limit")).toBe("50"); response = result("review list", { task_id: ids.task, next_cursor: null }, state.submitted ? [run()] : []); }
    else if (path === `/tasks/${ids.task}/bid-reviews` && method === "POST") {
      const body = request.postDataJSON(); expect(body.submission_id).toBe(ids.submission); expect(body.review_slice).toBe("compliance"); expect(body.scope).toBe("uploaded_bid");
      if (body.dry_run) {
        expect(body).not.toHaveProperty("preflight_token"); expect(body).not.toHaveProperty("expected_input_hash"); state.previews++; state.previewRequest = body.request_id;
        const asOf = new Date(), expiresAt = new Date(asOf.getTime() + 900000).toISOString();
        response = result("review run", { dry_run: true, input: { task_id: ids.task, submission_id: ids.submission, input_hash: hash }, expires_at: expiresAt, preflight_token: "synthetic-review-receipt", preflight_ttl_seconds: 900, admission_blockers: state.blockers, uncovered_codes: [], external_price_pages: false, budget: { dry_run: true, command: "review run", task_id: ids.task, input_hash: hash, as_of: asOf.toISOString(), task_budget: null, planned_calls: 1, maximum_calls: 1, estimate: zero, next_call: null, admission_blocker: null, first_pass_fits: true, full_run_guaranteed: false } });
      } else {
        expect(body.request_id).toBe(state.previewRequest); expect(body.expected_input_hash).toBe(hash); expect(body.preflight_token).toBe("synthetic-review-receipt");
        if (state.staleSubmit) return route.fulfill({ status: 409, json: result("review run", { error: { code: "bid_input_changed" } }, [], false) });
        state.submissions++; state.submitted = true; response = result("review run", { job_id: ids.job, status: "queued", cached: false });
      }
    } else if (path === `/jobs/${ids.job}` && method === "GET") response = result("job status", { id: ids.job, kind: "bid_review", status: "succeeded", attempts: 1, reasoning: null, error: null, result: { review_id: ids.review, completion: state.partial ? "partial" : "complete", coverage: { tender_pages_total: 1, tender_pages_authorized: 1, tender_pages_assessed: 1 }, uncovered_codes: state.partial ? ["signing_presence_unchecked"] : [], stop_reason: null, usage_record_ids: [] } }, [], !state.partial);
    else if (path === `/bid-reviews/${ids.review}/findings` && method === "GET") { expect(url.searchParams.get("limit")).toBe("50"); response = result("review findings", { review_id: ids.review, input_hash: hash, validity: "current", next_cursor: null }, []); }
    else if (path === `/bid-reviews/${ids.review}` && method === "GET") {
      const section = url.searchParams.get("section"); expect(["obligations", "signing_requirements"]).toContain(section); expect(url.searchParams.get("limit")).toBe("50");
      response = result("review show", { run: run(), obligations: section === "obligations" ? [{ id: uuid(40), text: quote, category: "substantive", starred: true, rejection_trigger: true, citation }] : [], signing_requirements: section === "signing_requirements" ? [{ id: uuid(41), candidate_id: null, applicability: "applies", mark_types: ["company_seal"], owner_roles: ["company"], date_required: false, location_rule: "every_page", citation, required_locations: [{ page_id: uuid(21), document_id: ids.bid, page: 1, status: "unresolved", group_id: null, reason_code: "presence_not_checked" }], reason_code: "presence_not_checked" }] : [], next_cursor: null }, [], !state.partial);
    } else { state.unexpected.push(`${method} ${path}`); return route.abort(); }
    if (path.includes("bid-submissions") || path.includes("bid-reviews") || path.includes("/jobs/")) expect(url.pathname.startsWith("/v4/")).toBe(true);
    state.reads.push(`${method} ${path}`);
    return route.fulfill({ json: response });
  });
  state.verify = () => { expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]); };
  return state;
}

async function artifact(page, state, name) {
  await page.screenshot({ path: join(output, `${name}.png`), fullPage: true });
  state.verify(); writeFileSync(join(output, `${name}.json`), JSON.stringify({ passed: true, authorizations: state.authorizations, revocations: state.revocations, namesWrites: state.namesWrites, previews: state.previews, submissions: state.submissions, reads: state.reads, errors: state.errors, unexpected: state.unexpected }, null, 2));
}
async function authorize(page) {
  const cards = page.getByTestId("sanitized-page");
  await cards.nth(0).getByText("招标第 1 页", { exact: true }).click();
  await cards.nth(1).getByText("投标第 1 页", { exact: true }).click();
  await page.getByLabel("授权或撤销理由", { exact: true }).fill("已核对脱敏范围");
  await page.getByText("已逐页核对所选脱敏文本，同意用于标书文本检验", { exact: true }).click();
  await page.getByRole("button", { name: "授权所选页面", exact: true }).click();
  await expect(page.getByRole("table", { name: "外发授权记录", exact: true })).toContainText("当前授权");
}

test("owner authorizes exact safe pages, previews call-free then explicitly submits partial signing checklist", async ({ page }) => {
  const state = await fixture(page); await page.goto(`/app/org/tasks/${ids.task}/bid-submissions/${ids.submission}`);
  const cards = page.getByTestId("sanitized-page"); await expect(cards).toHaveCount(4);
  await expect(cards.nth(2)).toContainText("报价页：禁止外发"); await expect(cards.nth(2).locator(".el-checkbox")).toHaveClass(/is-disabled/);
  await expect(cards.nth(3)).toContainText("报价分类未确定"); await expect(cards.nth(3).locator(".el-checkbox")).toHaveClass(/is-disabled/);
  await expect(cards.nth(0).locator("pre")).toHaveText(quote); await expect(cards.locator("img")).toHaveCount(0);
  await authorize(page); expect(state.authorizations).toBe(1);
  await page.getByRole("button", { name: "预检检验运行", exact: true }).click();
  await expect(page.getByTestId("review-preview")).toContainText("预检未创建作业或调用模型"); expect(state.submissions).toBe(0);
  await expect(page.getByRole("button", { name: "提交检验运行", exact: true })).toBeDisabled();
  await artifact(page, state, "01-authorized-preview");
  await page.getByText("确认费用与已授权文本，提交检验运行", { exact: true }).click(); await page.getByRole("button", { name: "提交检验运行", exact: true }).click();
  await expect(page.getByRole("table", { name: "招标义务与引用", exact: true })).toContainText(quote);
  await expect(page.getByRole("table", { name: "签章检查清单", exact: true })).toContainText("投标第 1 页 · 位置未解决");
  await expect(page.getByText("检验结果：部分完成 · 仅供辅助审查", { exact: true })).toBeVisible();
  await expect(page.getByText("招标页面覆盖：已检验 1 / 共 1 页；已授权 1 页", { exact: true })).toBeVisible();
  expect(state.submissions).toBe(1); expect(state.previews).toBe(1);
  expect(await page.evaluate(() => window.__unsafe)).toBeUndefined();
  const saved = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } })); expect(saved).not.toContain("投标文件须"); expect(saved).not.toContain("synthetic-review-receipt");
  await artifact(page, state, "02-partial-signing-checklist");
});

test("revoking authorization clears the preview and explicit submit consent", async ({ page }) => {
  const state = await fixture(page); await page.goto(`/app/org/tasks/${ids.task}/bid-submissions/${ids.submission}`); await authorize(page);
  await page.getByRole("button", { name: "预检检验运行", exact: true }).click(); await expect(page.getByTestId("review-preview")).toBeVisible();
  await page.getByText("确认费用与已授权文本，提交检验运行", { exact: true }).click();
  await page.getByLabel("授权或撤销理由", { exact: true }).fill("撤销当前外发范围"); await page.getByRole("button", { name: "撤销外发授权", exact: true }).click();
  await expect(page.getByRole("table", { name: "外发授权记录", exact: true })).toContainText("已撤销"); await expect(page.getByTestId("review-preview")).toHaveCount(0);
  expect(state.revocations).toBe(1); expect(state.submissions).toBe(0); await artifact(page, state, "03-revoked-preview");
});

test("stale submit invalidates receipt and blocked preview cannot be submitted", async ({ page }) => {
  const state = await fixture(page, { staleSubmit: true }); await page.goto(`/app/org/tasks/${ids.task}/bid-submissions/${ids.submission}`);
  await page.getByRole("button", { name: "预检检验运行", exact: true }).click(); await page.getByText("确认费用与已授权文本，提交检验运行", { exact: true }).click(); await page.getByRole("button", { name: "提交检验运行", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("输入已变化"); await expect(page.getByTestId("review-preview")).toHaveCount(0);
  state.blockers = ["outbound_authorization_required"]; await page.getByRole("button", { name: "预检检验运行", exact: true }).click();
  await expect(page.getByTestId("review-preview")).toBeVisible(); await page.getByText("确认费用与已授权文本，提交检验运行", { exact: true }).click(); await expect(page.getByRole("button", { name: "提交检验运行", exact: true })).toBeDisabled();
  expect(state.submissions).toBe(0); await artifact(page, state, "04-stale-and-blocked");
});

test("viewer receives safe run metadata without protected page text or controls", async ({ page }) => {
  const state = await fixture(page, { owner: false, role: "viewer" }); await page.goto(`/app/org/tasks/${ids.task}/bid-submissions/${ids.submission}`);
  await expect(page.getByRole("table", { name: "检验运行列表", exact: true })).toBeVisible();
  await expect(page.getByTestId("sanitized-page")).toHaveCount(0); await expect(page.getByRole("button", { name: "预检检验运行", exact: true })).toHaveCount(0); await expect(page.getByRole("button", { name: "授权所选页面", exact: true })).toHaveCount(0);
  expect(state.reads.some(path => path.includes("/redaction"))).toBe(false); expect(state.reads.some(path => path.includes("outbound-authorizations"))).toBe(false);
  expect(await page.locator("body").innerText()).not.toContain(quote); await artifact(page, state, "05-reader-gate");
});


test("supplemental name replacement clears previous authorization consent and preview", async ({ page }) => {
  const state = await fixture(page); await page.goto(`/app/org/tasks/${ids.task}/bid-submissions/${ids.submission}`); await authorize(page);
  await page.getByRole("button", { name: "预检检验运行", exact: true }).click(); await expect(page.getByTestId("review-preview")).toBeVisible();
  await page.getByText("补充单位与人员脱敏名单", { exact: true }).click();
  await page.getByLabel("投标单位补充名单", { exact: true }).fill("Synthetic supplemental bidder"); await page.getByLabel("人员补充名单", { exact: true }).fill("Synthetic supplemental staff");
  await page.getByRole("button", { name: "保存完整补充名单", exact: true }).click();
  await expect(page.getByTestId("review-preview")).toHaveCount(0); await expect(page.getByRole("table", { name: "外发授权记录", exact: true })).toContainText("历史授权或已失效");
  await expect(page.getByRole("button", { name: "授权所选页面", exact: true })).toBeDisabled(); expect(state.namesWrites).toBe(1); expect(state.submissions).toBe(0);
  expect(await page.evaluate(() => Object.values(sessionStorage).join(" "))).not.toContain("Synthetic supplemental"); await artifact(page, state, "06-supplemental-names");
});

test("technical contributor can preview an authorized run without originating grants", async ({ page }) => {
  const state = await fixture(page, { owner: false, role: "technical" }); await page.goto(`/app/org/tasks/${ids.task}/bid-submissions/${ids.submission}`);
  await expect(page.getByRole("button", { name: "预检检验运行", exact: true })).toBeVisible(); await expect(page.getByTestId("sanitized-page")).toHaveCount(0);
  await page.getByRole("button", { name: "预检检验运行", exact: true }).click(); await expect(page.getByTestId("review-preview")).toBeVisible();
  expect(state.reads.some(path => path.includes("/redaction"))).toBe(false); expect(state.authorizations).toBe(0); await artifact(page, state, "07-technical-run-gate");
});

test("authorization history follows the bounded next cursor", async ({ page }) => {
  const state = await fixture(page, { grantPageSize: 1 }); await page.goto(`/app/org/tasks/${ids.task}/bid-submissions/${ids.submission}`); await authorize(page);
  state.grants.push({ ...state.grants[0], id: uuid(56), revision: 1, current: false });
  await page.getByRole("button", { name: "刷新外发授权", exact: true }).click();
  await page.getByRole("button", { name: "更多外发授权记录", exact: true }).click(); await expect(page.getByRole("table", { name: "外发授权记录", exact: true })).toContainText("历史授权或已失效");
  await expect(page.getByRole("button", { name: "更多外发授权记录", exact: true })).toHaveCount(0); await artifact(page, state, "08-history-pagination");
});
