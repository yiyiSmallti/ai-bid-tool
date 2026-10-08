import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, extname, join, resolve } from "node:path";

const output = resolve(process.env.E2E_OUTPUT ?? "../data/work/bid-review-clef/browser");
const uuid = n => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const ids = { org: uuid(1), user: uuid(2), task: uuid(3), submission: uuid(4), tender: uuid(5), bid: uuid(6), grant: uuid(7), job: uuid(8), review: uuid(9), image: uuid(10), page: uuid(11), workers: uuid(12), gateway: uuid(13), triaged: uuid(14) };
const hash = "a".repeat(64), now = "2026-10-08T00:00:00Z";
// Metadata-free JPEG generated locally by PyMuPDF. All fixture content is synthetic.
const jpeg = Buffer.from("/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAIBAQEBAQIBAQECAgICAgQDAgICAgUEBAMEBgUGBgYFBgYGBwkIBgcJBwYGCAsICQoKCgoKBggLDAsKDAkKCgr/2wBDAQICAgICAgUDAwUKBwYHCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgr/wgARCAAgABgDAREAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAj/xAAUAQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAG/gAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAw/9oACAEBAAEFAk//xAAUEQEAAAAAAAAAAAAAAAAAAAAw/9oACAEDAQE/AU//xAAUEQEAAAAAAAAAAAAAAAAAAAAw/9oACAECAQE/AU//xAAUEAEAAAAAAAAAAAAAAAAAAAAw/9oACAEBAAY/Ak//xAAUEAEAAAAAAAAAAAAAAAAAAAAw/9oACAEBAAE/IU//2gAMAwEAAgADAAAAEAAAAAAP/8QAFBEBAAAAAAAAAAAAAAAAAAAAMP/aAAgBAwEBPxBP/8QAFBEBAAAAAAAAAAAAAAAAAAAAMP/aAAgBAgEBPxBP/8QAFBABAAAAAAAAAAAAAAAAAAAAMP/aAAgBAQABPxBP/9k=", "base64");
const jpegHash = createHash("sha256").update(jpeg).digest("hex");
const zero = { llm_tokens: 0, ocr_pages: 0, usd: 0, basis: "zero", charge: "0", billing_currency: "USD", task_amount: "0", unpriced_calls: 0, unresolved_calls: 0 };
const result = (command, data = {}, items = [], ok = true) => ({ ok, command, data, items, warnings: [], cost: zero, duration_ms: 0 });
const configured = () => ({ expected_revision: null, revision: 1, account_id: "b".repeat(32), gateway_id: "synthetic-clef", enabled: true, price_revision: 1, fixed_sale_price: "0.05", currency: "USD", platform_model_id: "bid-review-clef", workers_credential_id: ids.workers, gateway_credential_id: ids.gateway, workers_credential_revision: 1, gateway_credential_revision: 1, gateway_check: null, updated_at: now });
const hardened = () => ({ gateway_id: "synthetic-clef", authentication: true, collect_logs: false, logpush: false, cache_ttl: 0, gateway_retries: false, rate_limit_requests: 200, rate_limit_seconds: 60, workers_ai_billing_mode: "unified", checked_at: now });
const reportSections = [["overall", "一、总体结论"], ["basic_information", "二、基本信息"], ["compliance", "三、废标判定"], ["signatures", "签章校验"], ["risks", "四、高风险缺陷"], ["scores", "五、得分预估"], ["evidence", "六、证据核对"], ["remediation", "七、补救清单"], ["methodology", "八、检验说明"]].map(([key, title]) => ({ key, title }));

async function fixture(page, options = {}) {
  if (!process.env.E2E_STATIC_DIR) throw new Error("E2E_STATIC_DIR required; no service is started");
  const directory = resolve(process.env.E2E_STATIC_DIR), origin = new URL(process.env.E2E_BASE_URL ?? "https://console.test").origin;
  if (!origin.startsWith("https://")) throw new Error("HTTPS origin required");
  mkdirSync(output, { recursive: true });
  const state = { role: "admin", taskRole: "owner", platform: false, config: configured(), prepared: false, authorized: false, revision: 0, corruptImage: false, unavailable: false, submitted: false, runEnabled: true, previews: [], writes: [], reads: [], errors: [], unexpected: [], ...options };
  page.on("pageerror", error => state.errors.push(error.message));
  await page.addInitScript(({ org, platform }) => {
    if (platform) sessionStorage.setItem("bid.platform.session", "synthetic-platform-session");
    else sessionStorage.setItem("bid.org.session", JSON.stringify({ session: "synthetic-review-session", orgId: org, orgName: "合成单位", email: "admin@example.test" }));
  }, { org: ids.org, platform: state.platform });
  const image = () => ({ id: ids.image, page_id: ids.page, page: 1, document_id: ids.bid, sha256: jpegHash, source_sha256: hash, width_px: 24, height_px: 32, size_bytes: jpeg.length, blur: { method: "gaussian", radius: 32 }, privacy_receipt_sha256: hash });
  const presence = () => ({ submission_id: ids.submission, source_review_id: state.prepared ? (state.newPreparation ? ids.triaged : ids.review) : null, manifest_sha256: state.prepared ? hash : null, expected_revision: state.revision + 1, expected_authorization_id: state.revision ? ids.grant : null, images: state.prepared ? [image()] : [], blockers: [], authorization_id: state.authorized ? ids.grant : null, current: state.authorized && !state.newPreparation, revocable_authorization: state.authorized ? { id: ids.grant, revision: state.revision, manifest_sha256: hash, image_ids: [ids.image], allow_external: true, purpose: "bid_review_presence" } : null, purpose: "bid_review_presence" });
  const coverage = triaged => ({ clef: !triaged ? "unavailable" : !state.runEnabled ? "disabled" : state.unavailable ? "unavailable" : "triaged", clef_calls: triaged && state.runEnabled && !state.unavailable ? 1 : 0, clef_cost: triaged && state.runEnabled && !state.unavailable ? "0.05" : "0", clef_unvalidated_cases: ["faint", "greyscale", "partial", "wrong_company", "required_position", "seam"] });
  const run = id => ({ id, task_id: ids.task, submission_id: ids.submission, job_id: ids.job, status: "succeeded", completion: "partial", input_hash: hash, created_at: now, coverage: coverage(id === ids.triaged), uncovered_codes: ["signing_presence_unchecked"], advisory_only: true, validity: "current", review_slice: "compliance" });
  await page.route("**/*", async route => {
    const req = route.request(), url = new URL(req.url()), path = url.pathname.replace(/^\/v4(?=\/)/, ""), method = req.method();
    if (url.origin !== origin) { state.unexpected.push(`${method} external-origin`); return route.abort(); }
    if (path.startsWith("/app/")) {
      const file = path.startsWith("/app/assets/") ? join(directory, "assets", basename(path)) : join(directory, "index.html");
      return route.fulfill({ contentType: { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" }[extname(file)] ?? "application/octet-stream", body: readFileSync(file) });
    }
    if (path === "/health" && method === "GET") return route.fulfill({ json: result("health", { org_signup_enabled: false }) });
    let response;
    if (state.platform) {
      expect(req.headers().authorization).toBe("Bearer synthetic-platform-session"); expect(req.headers()["x-org-id"]).toBeUndefined();
      if (path === "/platform/credentials" && method === "GET") {
        const purpose = url.searchParams.get("purpose"); expect(["clef_workers_ai", "clef_gateway"]).toContain(purpose); expect(url.searchParams.get("state")).toBe("active"); expect(url.searchParams.get("limit")).toBe("100");
        response = result("platform credential list", { next_after_name: null }, [{ id: purpose === "clef_workers_ai" ? ids.workers : ids.gateway, name: purpose === "clef_workers_ai" ? "synthetic-workers" : "synthetic-gateway", purpose, provider: "cloudflare", state: "active", revision: 1 }]);
      } else if (path === "/platform/clef" && method === "GET") response = result("platform clef show", { config: state.config, blockers: state.config.gateway_check ? [] : ["clef_gateway_not_checked"] });
      else if (path === "/platform/clef" && method === "PUT") {
        const body = req.postDataJSON(); expect(body).toEqual({ expected_revision: 1, account_id: "b".repeat(32), gateway_id: "synthetic-clef", enabled: true, price_revision: 2, fixed_sale_price: "0.07", currency: "USD", workers_credential_id: ids.workers, gateway_credential_id: ids.gateway });
        state.writes.push({ path, body }); state.config = { ...state.config, ...body, revision: 2, gateway_check: null }; response = result("platform clef set", { config: state.config, blockers: ["clef_gateway_not_checked"] });
      } else if (path === "/platform/clef/check" && method === "POST") {
        const body = req.postDataJSON(); expect(body).toEqual({ expected_revision: 2 }); state.writes.push({ path, body }); state.config.gateway_check = hardened(); response = result("platform clef check", { config: state.config, blockers: [] });
      }
    } else {
      expect(req.headers().authorization).toBe("Bearer synthetic-review-session"); expect(req.headers()["x-org-id"]).toBe(ids.org);
      if (path === "/org/current" && method === "GET") response = result("org current", { org_id: ids.org, user_id: ids.user, role: state.role });
      else if (path === `/tasks/${ids.task}/workflow` && method === "GET") response = result("task workflow", { workflow: { org_id: ids.org, task_id: ids.task, state: "active", owner_user_id: state.taskRole === "owner" ? ids.user : uuid(99) } });
      else if (path === `/tasks/${ids.task}/members` && method === "GET") response = result("task member list", { org_id: ids.org, task_id: ids.task, next_cursor: null }, [{ user_id: ids.user, role: state.role === "viewer" ? "observer" : state.taskRole, active: true }]);
      else if (path === `/bid-submissions/${ids.submission}/outbound-authorizations` && method === "GET") { expect(state.taskRole).toBe("owner"); response = result("review outbound list", { total: 0, next_cursor: null }, []); }
      else if (path === `/bid-submissions/${ids.submission}/redaction` && method === "GET") { expect(state.taskRole).toBe("owner"); response = result("review redaction", { submission_id: ids.submission, expected_revision: 1, expected_authorization_id: null, expected_submission_manifest_sha256: hash, expected_preparation_input_hash: hash, expected_redaction_manifest_sha256: hash, provider_bindings_sha256: hash, redaction_revision: 1, redaction_rule_version: "synthetic", confidential_binding_sha256: hash, derived_name_lists_sha256: hash, human_name_revision: 0, pages: [], total: 0, next_cursor: null, blockers: [] }); }
      else if (path === `/bid-submissions/${ids.submission}` && method === "GET") response = result("review submission show", { submission: { id: ids.submission, org_id: ids.org, task_id: ids.task, revision: 1, manifest_sha256: hash, state: "prepared", created_by: ids.user, created_at: now, documents: [{ id: ids.tender, role: "tender", kind: "tender", media_type: "application/pdf", sha256: hash, size_bytes: 1000 }, { id: ids.bid, role: "bid", kind: "commercial_technical", media_type: "application/pdf", sha256: hash, size_bytes: 1000 }] }, preparation: null, inventory: [], signature_validations: [], signing_candidates: [], signing_candidate_count: 0, signing_candidates_next_cursor: null });
      else if (path === `/tasks/${ids.task}/bid-reviews` && method === "GET") { expect(url.searchParams.get("limit")).toBe("50"); response = result("review list", { task_id: ids.task, next_cursor: null }, state.submitted ? [run(ids.triaged), run(ids.review)] : [run(ids.review)]); }
      else if (path === `/tasks/${ids.task}/bid-reviews` && method === "POST") {
        const body = req.postDataJSON(); expect(body.submission_id).toBe(ids.submission); expect(body.scope).toBe("uploaded_bid"); expect(body.review_slice).toBe("compliance"); expect(typeof body.clef_enabled).toBe("boolean");
        if (body.dry_run) {
          state.previews.push(body); const asOf = new Date(); const available = body.clef_enabled && state.authorized && !state.unavailable;
          response = result("review run", { dry_run: true, input: { task_id: ids.task, submission_id: ids.submission, input_hash: hash, clef: { enabled: body.clef_enabled, available, blockers: body.clef_enabled && !available ? ["clef_presence_unavailable"] : [], planned_calls: available ? 1 : 0, fixed_sale_price: "0.05", currency: "USD", price_revision: "1", binding_sha256: hash, authorization_id: state.authorized ? ids.grant : null } }, expires_at: new Date(asOf.getTime() + 900000).toISOString(), preflight_token: "synthetic-clef-preflight", admission_blockers: [], uncovered_codes: available ? [] : ["signing_presence_unchecked"], budget: { planned_calls: available ? 2 : 1, input_hash: hash, estimate: { ...zero, charge: available ? "0.05" : "0" } } });
        } else {
          expect(body.clef_enabled).toBe(state.previews.at(-1).clef_enabled); expect(body.preflight_token).toBe("synthetic-clef-preflight"); expect(body.expected_input_hash).toBe(hash); expect(body.request_id).toBe(state.previews.at(-1).request_id);
          state.writes.push({ path, body }); state.submitted = true; state.runEnabled = body.clef_enabled; response = result("review run", { job_id: ids.job, status: "queued", cached: false });
        }
      } else if (path === `/jobs/${ids.job}` && method === "GET") response = result("job status", { id: ids.job, kind: "bid_review", status: "succeeded", attempts: 1, result: { review_id: ids.triaged, completion: "partial", coverage: coverage(true), uncovered_codes: ["signing_presence_unchecked"], usage_record_ids: [] } });
      else if (path === `/bid-reviews/${ids.triaged}/reports` && method === "GET") response = result("review report list", { review_id: ids.triaged, task_id: ids.task, next_cursor: null }, []);
      else if (path === `/bid-reviews/${ids.triaged}/report` && method === "GET") {
        const section = url.searchParams.get("section"); expect(reportSections.map(row => row.key)).toContain(section); expect(url.searchParams.get("limit")).toBe("50");
        const rows = section === "signatures" ? [{ kind: "signing_requirement", requirement: { applicability: "applies", mark_types: ["company_seal"], required_locations: [{ page: 1, status: "triage_absent", probability_yes: 0.03, presence_probabilities: { company_seal: 0.03, signature: 0.01 }, needs_human_confirmation: true }] } }, { kind: "notice", label: "Clef 初筛", text: "完成 1 次调用；固定调用费用 0.05 USD；需人工确认。" }] : section === "methodology" ? [{ kind: "notice", label: "Clef 验证范围", text: "浅色、灰度、部分印章、错误单位、指定位置及骑缝章尚未验证。返回 tokens 仅作遥测，使用固定每次调用售价。" }] : [{ kind: "notice", label: "辅助审查", text: "签章初筛结果须由人工确认。" }];
        response = result("review report", { task_id: ids.task, review_id: ids.triaged, section, snapshot_id: null, input_hash: hash, report_input_hash: hash, decisions_snapshot_sha256: hash, current_decisions_snapshot_sha256: hash, projection: "protected", completion: "partial", sections: reportSections, advisory_statement: "评标委员会决定最终评审结果；本报告仅供辅助审查。", next_cursor: null, artifacts: [] }, rows);
      }
      else if ([`/bid-reviews/${ids.review}/findings`, `/bid-reviews/${ids.triaged}/findings`].includes(path) && method === "GET") response = result("review findings", { review_id: path.split("/")[2], input_hash: hash, validity: "current", next_cursor: null }, []);
      else if ([`/bid-reviews/${ids.review}`, `/bid-reviews/${ids.triaged}`].includes(path) && method === "GET") {
        const id = path.split("/")[2], section = url.searchParams.get("section"); expect(["obligations", "signing_requirements"]).toContain(section);
        response = result("review show", { run: run(id), obligations: [], signing_requirements: section === "signing_requirements" ? [{ id: uuid(30), applicability: "applies", mark_types: ["company_seal"], date_required: true, citation: null, required_locations: [{ page_id: ids.page, document_id: ids.bid, page: 1, status: id === ids.triaged && state.runEnabled && !state.unavailable ? "triage_absent" : "unresolved", probability_yes: id === ids.triaged && state.runEnabled && !state.unavailable ? 0.03 : null, presence_probabilities: id === ids.triaged && state.runEnabled && !state.unavailable ? { company_seal: 0.03, signature: 0.01 } : {}, needs_human_confirmation: true, owner_status: "unresolved", date_status: "unresolved", reason_code: "presence_not_checked" }] }] : [], next_cursor: null });
      } else if (path === `/bid-submissions/${ids.submission}/presence-preview` && method === "GET") { expect(state.role).toBe("admin"); response = result("review presence preview", presence()); }
      else if (path === `/bid-submissions/${ids.submission}/presence-preparations` && method === "POST") { const body = req.postDataJSON(); expect(body).toEqual({ review_id: ids.review }); state.writes.push({ path, body }); state.prepared = true; response = result("review presence prepare", presence()); }
      else if (path === `/bid-presence-images/${ids.image}/content` && method === "GET") { expect(state.prepared).toBe(true); state.reads.push(`${method} ${path}`); return route.fulfill({ contentType: "image/jpeg", headers: { "Cache-Control": "no-store" }, body: state.corruptImage ? Buffer.alloc(jpeg.length) : jpeg }); }
      else if (path === `/bid-submissions/${ids.submission}/presence-authorizations` && method === "POST") {
        const body = req.postDataJSON(); expect(body.expected_revision).toBe(state.revision + 1); expect(body.expected_authorization_id).toBe(state.revision ? ids.grant : null); expect(body.manifest_sha256).toBe(hash); expect(body.image_ids).toEqual([ids.image]); expect(body.privacy_reviewed).toBe(true); expect(body.reason).toBeTruthy(); expect(body).not.toHaveProperty("images");
        state.writes.push({ path, body }); state.authorized = body.allow_external; state.revision++; response = result("review presence authorize", { id: ids.grant, revision: state.revision, manifest_sha256: hash, image_ids: [ids.image], allow_external: body.allow_external, purpose: "bid_review_presence" });
      }
      if (path.includes("bid-submissions") || path.includes("bid-reviews") || path.includes("bid-presence-images") || path.includes("/jobs/")) expect(url.pathname.startsWith("/v4/")).toBe(true);
    }
    if (!response) { state.unexpected.push(`${method} ${path}`); return route.abort(); }
    state.reads.push(`${method} ${path}`); return route.fulfill({ json: response });
  });
  state.verify = () => { expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]); };
  return state;
}
async function artifact(page, state, name) {
  await page.screenshot({ path: join(output, `${name}.png`), fullPage: true }); state.verify();
  writeFileSync(join(output, `${name}.json`), JSON.stringify({ passed: true, reads: state.reads, writes: state.writes.map(row => ({ path: row.path, clef_enabled: row.body.clef_enabled, allow_external: row.body.allow_external })), previews: state.previews.map(row => ({ clef_enabled: row.clef_enabled })), errors: state.errors, unexpected: state.unexpected }, null, 2));
}
async function openImages(page) {
  await page.goto(`/app/org/tasks/${ids.task}/bid-submissions/${ids.submission}`);
  await page.getByRole("button", { name: "查看检验结果", exact: true }).click();
  await page.getByRole("button", { name: "签章图片外发授权", exact: true }).click();
  await page.getByRole("button", { name: "生成本次目标页模糊图片", exact: true }).click();
}
async function authorize(page) {
  const image = page.getByTestId("presence-image"); await expect(image).toHaveCount(1); await expect(image.locator("img")).toHaveAttribute("src", /^blob:/);
  await image.getByText("投标第 1 页初筛图片", { exact: true }).click();
  await page.getByLabel("图片授权或撤销理由", { exact: true }).fill("逐页确认文字不可读");
  await page.getByText("已逐页核对所选模糊图片，同意仅用于签章存在性初筛", { exact: true }).click();
  await page.getByRole("button", { name: "授权所选初筛图片", exact: true }).click();
  await expect(page.getByText("图片授权有效", { exact: true })).toBeVisible();
}

test("platform config pins fixed call price and independently checks gateway hardening", async ({ page }) => {
  const state = await fixture(page, { platform: true }); await page.goto("/app/platform/clef");
  await expect(page.getByRole("heading", { name: "Clef 初筛配置", exact: true })).toBeVisible();
  await page.getByLabel("固定每次调用售价（USD）", { exact: true }).fill("0.07"); await page.getByRole("spinbutton", { name: "价格版本", exact: true }).fill("2");
  await page.getByRole("button", { name: "保存 Clef 配置", exact: true }).click(); await expect(page.getByText("Clef 配置已保存，请运行网关安全检查。", { exact: true })).toBeVisible();
  await expect(page.getByTestId("clef-gateway-check")).toHaveCount(0); await page.getByRole("button", { name: "检查 Clef 网关", exact: true }).click();
  await expect(page.getByTestId("clef-gateway-check")).toContainText("200 次 / 60 秒"); await expect(page.getByTestId("clef-gateway-check")).toContainText("0.07 USD"); expect(state.writes).toHaveLength(2); await artifact(page, state, "01-platform-hardening");
});

test("human reviews exact derivative, default-on preview includes fixed quote, triage miss stays human", async ({ page }) => {
  const state = await fixture(page); await openImages(page); await authorize(page);
  await expect(page.getByRole("checkbox", { name: "使用 Clef 初筛（默认开启）", exact: true })).toBeChecked();
  await page.getByRole("button", { name: "预检检验运行", exact: true }).click(); await expect(page.getByTestId("clef-preview")).toContainText("计划 1 次图片调用"); await expect(page.getByTestId("clef-preview")).toContainText("0.05 USD"); expect(state.previews[0].clef_enabled).toBe(true);
  await page.getByText("确认费用与已授权文本，提交检验运行", { exact: true }).click(); await page.getByRole("button", { name: "提交检验运行", exact: true }).click();
  await expect(page.getByTestId("signing-location")).toContainText("初筛未发现"); await expect(page.getByTestId("signing-location")).toContainText("存在概率 3.0%"); await expect(page.getByTestId("signing-location")).toContainText("需人工确认"); await expect(page.getByTestId("signing-location")).toContainText("须升级人工核对");
  await expect(page.getByTestId("clef-coverage")).toContainText("已完成初筛"); expect(state.writes.find(row => row.body.dry_run === false).body.clef_enabled).toBe(true);
  const saved = await page.evaluate(() => JSON.stringify({ ...sessionStorage, ...localStorage })); expect(saved).not.toContain("synthetic-clef-preflight"); expect(saved).not.toContain(jpegHash); await artifact(page, state, "02-authorized-triage");
});

test("opt-out invalidates preflight and stays disabled through submission", async ({ page }) => {
  const state = await fixture(page, { authorized: true, prepared: true, revision: 1 }); await page.goto(`/app/org/tasks/${ids.task}/bid-submissions/${ids.submission}`);
  await page.getByRole("button", { name: "预检检验运行", exact: true }).click(); await expect(page.getByTestId("review-preview")).toBeVisible();
  await page.getByRole("checkbox", { name: "使用 Clef 初筛（默认开启）", exact: true }).uncheck(); await expect(page.getByTestId("review-preview")).toHaveCount(0);
  await page.getByRole("button", { name: "预检检验运行", exact: true }).click(); await expect(page.getByTestId("clef-preview")).toContainText("本次已关闭"); await expect(page.getByTestId("clef-preview")).toContainText("计划 0 次图片调用");
  await page.getByText("确认费用与已授权文本，提交检验运行", { exact: true }).click(); await page.getByRole("button", { name: "提交检验运行", exact: true }).click();
  await expect(page.getByTestId("clef-coverage")).toContainText("本次已关闭"); expect(state.runEnabled).toBe(false); await artifact(page, state, "03-opt-out");
});

test("unavailable Clef preserves visible coverage and allows text review", async ({ page }) => {
  const state = await fixture(page, { unavailable: true }); await page.goto(`/app/org/tasks/${ids.task}/bid-submissions/${ids.submission}`);
  await page.getByRole("button", { name: "预检检验运行", exact: true }).click(); await expect(page.getByTestId("clef-preview")).toContainText("不可用，继续文本检验");
  await page.getByText("确认费用与已授权文本，提交检验运行", { exact: true }).click(); await expect(page.getByRole("button", { name: "提交检验运行", exact: true })).toBeEnabled(); await page.getByRole("button", { name: "提交检验运行", exact: true }).click(); await expect(page.getByTestId("clef-coverage")).toContainText("不可用"); await artifact(page, state, "04-unavailable-coverage");
});

test("image authorization revocation invalidates run preflight and changed bytes never render", async ({ page }) => {
  const state = await fixture(page); await openImages(page); await authorize(page); await page.getByRole("button", { name: "预检检验运行", exact: true }).click(); await expect(page.getByTestId("review-preview")).toBeVisible();
  await page.getByLabel("图片授权或撤销理由", { exact: true }).fill("撤销图片范围"); await page.getByRole("button", { name: "撤销图片外发授权", exact: true }).click(); await expect(page.getByTestId("review-preview")).toHaveCount(0); expect(state.authorized).toBe(false);
  state.corruptImage = true; await page.getByRole("button", { name: "刷新图片授权", exact: true }).click(); await expect(page.getByTestId("presence-image").locator("img")).toHaveCount(0); await expect(page.getByRole("button", { name: "授权所选初筛图片", exact: true })).toBeDisabled(); await artifact(page, state, "05-revoke-and-integrity");
});

test("old image grant remains revocable after source changes and pixel loading fails", async ({ page }) => {
  const state = await fixture(page); await openImages(page); await authorize(page);
  state.newPreparation = true; state.corruptImage = true;
  await page.getByRole("button", { name: "刷新图片授权", exact: true }).click();
  await expect(page.getByTestId("presence-image").locator("img")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "撤销图片外发授权", exact: true })).toBeVisible();
  await page.getByLabel("图片授权或撤销理由", { exact: true }).fill("撤销旧检验的图片授权");
  await page.getByRole("button", { name: "撤销图片外发授权", exact: true }).click();
  await expect.poll(() => state.authorized).toBe(false);
  await artifact(page, state, "08-stale-grant-revocation");
});

test("viewer cannot prepare or authorize derivatives and no protected image request occurs", async ({ page }) => {
  const state = await fixture(page, { role: "viewer" }); await page.goto(`/app/org/tasks/${ids.task}/bid-submissions/${ids.submission}`); await page.getByRole("button", { name: "查看检验结果", exact: true }).click();
  await expect(page.getByRole("button", { name: "签章图片外发授权", exact: true })).toHaveCount(0); await expect(page.getByRole("checkbox", { name: "使用 Clef 初筛（默认开启）", exact: true })).toHaveCount(0); expect(state.reads.some(path => path.includes("/presence-") || path.includes("bid-presence-images"))).toBe(false); expect(state.writes).toHaveLength(0); await artifact(page, state, "06-viewer-gate");
});

test("admin contributor can run review but cannot authorize presence images", async ({ page }) => {
  const state = await fixture(page, { taskRole: "contributor" }); await page.goto(`/app/org/tasks/${ids.task}/bid-submissions/${ids.submission}`);
  await page.getByRole("button", { name: "查看检验结果", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "使用 Clef 初筛（默认开启）", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "签章图片外发授权", exact: true })).toHaveCount(0);
  expect(state.reads.some(path => path.includes("presence-"))).toBe(false);
  await artifact(page, state, "09-contributor-gate");
});

test("report preserves triage probabilities, manual escalation, fixed charge and unvalidated cases", async ({ page }) => {
  const state = await fixture(page, { submitted: true, authorized: true, prepared: true }); await page.goto(`/app/org/tasks/${ids.task}/bid-reviews/${ids.triaged}/report`);
  const signatures = page.getByTestId("report-section-signatures"); await signatures.getByRole("button", { name: "查看本节", exact: true }).click();
  await expect(signatures).toContainText("初筛未发现"); await expect(signatures).toContainText("存在概率 3.0%"); await expect(signatures).toContainText("需人工确认"); await expect(signatures).toContainText("固定调用费用 0.05 USD");
  const methodology = page.getByTestId("report-section-methodology"); await methodology.getByRole("button", { name: "查看本节", exact: true }).click(); await expect(methodology).toContainText("浅色、灰度、部分印章、错误单位、指定位置及骑缝章尚未验证"); await expect(methodology).toContainText("返回 tokens 仅作遥测"); await artifact(page, state, "07-report-triage");
});
