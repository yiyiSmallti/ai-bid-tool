import { test, expect } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, extname, join, resolve } from "node:path";

const output = resolve(process.env.E2E_OUTPUT ?? "../data/work/bid-review-sig/browser");
const uuid = n => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const org = uuid(1), user = uuid(2), task = uuid(3), submission = uuid(4), document = uuid(10), anchorId = uuid(20);
const hash = "a".repeat(64), now = "2026-10-08T00:00:00Z";
const zero = { llm_tokens: 0, ocr_pages: 0, usd: 0, basis: "zero", charge: "0", billing_currency: "USD", task_amount: "0", unpriced_calls: 0, unresolved_calls: 0 };
const result = (command, data = {}, items = []) => ({ ok: true, command, data, items, warnings: [], cost: zero, duration_ms: 0 });
const preview = { label: "合成 CA", fingerprint_sha256: hash, subject: "CN=Synthetic root", issuer: "CN=Synthetic root", not_before: "2026-01-01T00:00:00Z", not_after: "2027-01-01T00:00:00Z", is_ca: true };
const anchor = () => ({ ...preview, id: anchorId, enabled: true, revision: 1, created_by: "operator@example.test", created_at: now, disabled_by: null, disabled_at: null });

async function fixture(page, { platform = false, restricted = false } = {}) {
  if (!process.env.E2E_STATIC_DIR) throw new Error("E2E_STATIC_DIR required; this suite starts no services");
  mkdirSync(output, { recursive: true });
  const directory = resolve(process.env.E2E_STATIC_DIR), origin = new URL(process.env.E2E_BASE_URL ?? "https://console.test").origin;
  const state = { errors: [], unexpected: [], previews: 0, writes: 0, candidateReads: 0, stored: null };
  page.on("pageerror", error => state.errors.push(error.message));
  await page.addInitScript(({ platform, org }) => platform ? sessionStorage.setItem("bid.platform.session", "synthetic-platform-session") : sessionStorage.setItem("bid.org.session", JSON.stringify({ session: "synthetic-org-session", orgId: org, orgName: "合成单位", email: "bidder@example.test" })), { platform, org });
  const certificate = { fingerprint_sha256: hash, not_before: preview.not_before, not_after: preview.not_after, ...(!restricted ? { subject: "CN=Protected synthetic signer", issuer: "CN=Protected synthetic issuer" } : {}) };
  const signatures = [
    { signature_index: 1, signed_revision: 1, field_name_sha256: hash, byte_range: [0, 100, 200, 300], crypto_status: "valid", content_digest_status: "valid", signature_value_status: "valid", coverage_status: "whole_revision", modified_after_signing: true, final_revision_covered: false, post_signing_changes: "incremental_or_appended_bytes", trust_status: "trusted", certificate_validity_status: "valid", revocation_status: "unknown", timestamp_status: "absent", claimed_signing_time: null, certificate },
    { signature_index: 2, signed_revision: 2, field_name_sha256: hash, byte_range: [0, 100, 200, 400], crypto_status: "invalid", content_digest_status: "valid", signature_value_status: "invalid", coverage_status: "whole_revision", modified_after_signing: false, final_revision_covered: true, post_signing_changes: "none", trust_status: "unknown", certificate_validity_status: "expired", revocation_status: "unknown", timestamp_status: "unsupported", claimed_signing_time: now, certificate },
  ];
  const candidate = ordinal => { const quote = ordinal === 1 ? "投标文件须逐页加盖公章。<img src=x onerror=window.__unsafe=1>" : "授权代理人应签字并填写日期。"; return ({ id: uuid(30 + ordinal), document_id: document, page_id: uuid(40 + ordinal), ordinal, page: ordinal, candidate_kind: "signing_clause", applicability: "unknown", ...(!restricted ? { quote, start_offset: 0, end_offset: [...quote].length, mark_types: ["company_seal", "personal_seal", "every_page_electronic_seal", "pdf_digital_signature"], owner_roles: ["company"], date_required: false, location_hint: "每页" } : {}) }); };
  const detail = { submission: { id: submission, org_id: org, task_id: task, revision: 1, manifest_sha256: hash, state: "prepared", created_at: now, documents: [{ id: document, role: "bid", kind: "qualification", media_type: "application/pdf", sha256: hash, size_bytes: 1000 }] }, preparation: null, inventory: [], signature_validations: [{ document_id: document, original_sha256: hash, trust_store_sha256: hash, validator_identity: "synthetic-local-v1", validation_network: "disabled", validation_time: now, status: "invalid", signatures, final_revision: { status: "invalid", covered_by_signature_indices: [2], modified_after_last_signature: false } }, { document_id: uuid(11), original_sha256: hash, trust_store_sha256: hash, validator_identity: "synthetic-local-v1", validation_time: now, status: "not_applicable", signatures: [], final_revision: { status: "unsigned", covered_by_signature_indices: [], modified_after_last_signature: false } }], signing_candidates: [candidate(1)], signing_candidate_count: 2, signing_candidates_next_cursor: 2 };
  await page.route("**/*", async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname.replace(/^\/v4(?=\/)/, ""), method = request.method();
    if (url.origin !== origin) { state.unexpected.push(`${method} external-origin`); return route.abort(); }
    if (path.startsWith("/app/")) {
      const file = path.startsWith("/app/assets/") ? join(directory, "assets", basename(path)) : join(directory, "index.html");
      return route.fulfill({ contentType: { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" }[extname(file)] ?? "application/octet-stream", body: readFileSync(file) });
    }
    if (path === "/health") return route.fulfill({ json: result("health", { org_signup_enabled: false }) });
    let response;
    if (platform) {
      expect(request.headers().authorization).toBe("Bearer synthetic-platform-session"); expect(request.headers()["x-org-id"]).toBeUndefined();
      if (path === "/platform/trust-anchors" && method === "GET") response = result("platform trust-anchor list", {}, state.stored ? [state.stored] : []);
      else if (["/platform/trust-anchors", "/platform/trust-anchors/preview"].includes(path) && method === "POST") {
        const wire = request.postDataBuffer().toString(); expect(wire).toContain('name="certificate"'); expect(wire).toContain("合成 CA");
        if (path.endsWith("/preview")) { state.previews++; response = result("platform trust-anchor preview", preview); }
        else { const created = !state.stored; state.stored ??= anchor(); state.writes++; response = result("platform trust-anchor add", { ...state.stored, created }); }
      } else if (path === `/platform/trust-anchors/${anchorId}/disable` && method === "POST") { state.writes++; state.stored = { ...state.stored, enabled: false, revision: 2, disabled_by: "operator@example.test", disabled_at: now }; response = result("platform trust-anchor disable", state.stored); }
    } else {
      expect(request.headers()["x-org-id"]).toBe(org);
      if (path === "/org/current") response = result("org current", { org_id: org, user_id: user, role: restricted ? "viewer" : "bidder" });
      else if (path === `/tasks/${task}/workflow`) response = result("task workflow", { workflow: { org_id: org, task_id: task, state: "active" } });
      else if (path === `/tasks/${task}/members`) response = result("task member list", { org_id: org, task_id: task, next_cursor: null }, [{ user_id: user, role: restricted ? "observer" : "contributor", active: true }]);
      else if (path === `/bid-submissions/${submission}` && method === "GET") response = result("review submission show", detail);
      else if (path === `/bid-submissions/${submission}/signing-candidates` && method === "GET") { expect(url.pathname.startsWith("/v4/")).toBe(true); expect(url.searchParams.get("cursor")).toBe("2"); expect(url.searchParams.get("limit")).toBe("20"); state.candidateReads++; response = result("review signing-candidates", { items: [candidate(2)], total: 2, next_cursor: null }); }
    }
    if (!response) { state.unexpected.push(`${method} ${path}`); return route.abort(); }
    return route.fulfill({ json: response });
  });
  state.verify = () => { expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]); };
  return state;
}

async function artifact(page, state, name) {
  await page.screenshot({ path: join(output, `${name}.png`), fullPage: true });
  state.verify(); writeFileSync(join(output, `${name}.json`), JSON.stringify({ passed: true, errors: state.errors, unexpected: state.unexpected, previews: state.previews, writes: state.writes, candidateReads: state.candidateReads }, null, 2));
}

test("independent signatures keep later invalid result and exact unknown candidates", async ({ page }) => {
  const state = await fixture(page);
  await page.goto(`/app/org/tasks/${task}/bid-submissions/${submission}`);
  const section = page.getByTestId("pdf-signature-results");
  await expect(section.getByRole("heading", { name: /数字签名 1/ })).toBeVisible();
  await expect(section.getByRole("heading", { name: /数字签名 2/ })).toBeVisible();
  await expect(section).toContainText("最终修订结果"); await expect(section).toContainText("无效"); await expect(section).toContainText("已过期");
  await expect(section).toContainText("CN=Protected synthetic issuer"); await expect(section).toContainText("不适用");
  const candidates = page.getByTestId("signing-candidates");
  await expect(candidates.getByRole("heading", { name: "签章要求候选", exact: true })).toBeVisible();
  await expect(candidates).toContainText("适用性未知"); await expect(candidates).toContainText("主体候选：投标单位"); await expect(candidates).toContainText("每页电子签章");
  await expect(section).toContainText("存在增量更新或追加字节"); await expect(section).toContainText("无后续修改"); await expect(candidates.locator("blockquote")).toHaveText("投标文件须逐页加盖公章。<img src=x onerror=window.__unsafe=1>");
  await expect(candidates.locator("img")).toHaveCount(0);
  await candidates.getByRole("button", { name: "加载更多候选" }).click();
  await expect(candidates).toContainText("授权代理人应签字并填写日期。"); expect(state.candidateReads).toBe(1);
  expect(await page.evaluate(() => window.__unsafe)).toBeUndefined();
  await artifact(page, state, "01-signatures-and-candidates");
});

test("restricted reader sees dimensions without certificate names or tender quotes", async ({ page }) => {
  const state = await fixture(page, { restricted: true });
  await page.goto(`/app/org/tasks/${task}/bid-submissions/${submission}`);
  await expect(page.getByTestId("pdf-signature-results")).toContainText("受限或未取得");
  await expect(page.getByTestId("signing-candidates")).toContainText("原文与候选详情仅向有原件读取权限的人工身份显示");
  const text = await page.locator("body").innerText(); expect(text).not.toContain("Protected synthetic"); expect(text).not.toContain("投标文件须逐页");
  expect(await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))).not.toContain("synthetic signer");
  await artifact(page, state, "02-restricted-reader");
});

test("platform root preview writes nothing, add preserves duplicates and disable pins prior results", async ({ page }) => {
  const state = await fixture(page, { platform: true });
  await page.goto("/app/platform/trust-anchors");
  await expect(page.getByRole("link", { name: "信任根证书", exact: true })).toBeVisible();
  await page.getByLabel("信任根标签", { exact: true }).fill("合成 CA");
  await page.getByLabel("选择 PEM 或 DER 证书", { exact: true }).setInputFiles({ name: "root.der", mimeType: "application/octet-stream", buffer: Buffer.from("SYNTHETIC ROOT TRANSPORT") });
  await page.getByRole("button", { name: "解析证书预览", exact: true }).click();
  await expect(page.getByTestId("trust-anchor-preview")).toContainText("CN=Synthetic root"); expect(state.writes).toBe(0);
  await page.getByRole("button", { name: "添加此信任根", exact: true }).click();
  const table = page.getByRole("table", { name: "信任根证书列表", exact: true }); await expect(table).toContainText("已启用");
  await table.getByRole("button", { name: "停用", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "确认停用", exact: true }).click();
  await expect(table).toContainText("已停用"); await expect(table.getByRole("button", { name: "停用", exact: true })).toHaveCount(0);
  await page.getByLabel("信任根标签", { exact: true }).fill("合成 CA");
  await page.getByLabel("选择 PEM 或 DER 证书", { exact: true }).setInputFiles({ name: "root.pem", mimeType: "application/x-pem-file", buffer: Buffer.from("SYNTHETIC ROOT TRANSPORT") });
  await page.getByRole("button", { name: "解析证书预览", exact: true }).click(); await page.getByRole("button", { name: "添加此信任根", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("此证书已存在，保留原有启停状态"); await expect(table).toContainText("已停用");
  expect(state.writes).toBe(3); expect(state.previews).toBe(2);
  await artifact(page, state, "03-platform-trust-root");
});
