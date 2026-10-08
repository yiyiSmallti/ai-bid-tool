import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, extname, join, resolve } from "node:path";

const output = resolve(process.env.E2E_OUTPUT ?? "../data/work/bid-review-report/browser");
const uuid = n => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const ids = { org: uuid(1), user: uuid(2), task: uuid(3), review: uuid(4), submission: uuid(5), snapshot: uuid(6), artifact: uuid(7), job: uuid(8), tender: uuid(9), bid: uuid(10) };
const hash = "a".repeat(64), decisionHash = "b".repeat(64), newerHash = "c".repeat(64), now = "2026-10-08T00:00:00Z";
const unsafe = "合成招标逐字引用 <img src=x onerror=window.__unsafe=1>", bidQuote = "合成投标响应为 64 GB。";
const sections = [["overall", "一、总体结论"], ["basic_information", "二、基本信息"], ["compliance", "三、废标判定"], ["signatures", "签章校验"], ["risks", "四、高风险缺陷"], ["scores", "五、得分预估"], ["evidence", "六、证据核对"], ["remediation", "七、补救清单"], ["methodology", "八、检验说明"]].map(([key, title]) => ({ key, title }));
const zero = { llm_tokens: 0, ocr_pages: 0, usd: 0, basis: "zero", charge: "0", billing_currency: "USD", task_amount: "0", unpriced_calls: 0, unresolved_calls: 0 };
const result = (command, data, items = [], ok = true) => ({ ok, command, data, items, warnings: [], cost: zero, duration_ms: 0 });
// Minimal synthetic OOXML package; PostgreSQL acceptance checks the real renderer's contents.
const bytes = Buffer.from("UEsDBBQAAAAIAPBGSF10JJxTuwAAAD4BAAATAAAAW0NvbnRlbnRfVHlwZXNdLnhtbJWQuQ7CMAyGX6XKiqgRAwNquwArMPACVuq2EbkUuxxvT8o1sDHa//FZrk73SFzcnPVcq0EkrgFYD+SQyxDJZ6ULyaHkMfUQUZ+xJ1guFivQwQt5mcvUoZpqSx2OVordLa/ZBF+rRJZVsXkZJ1atMEZrNErW4eLbH8r8TShz8unhwUSeZYOCpjpcKCXTUnHEJHt0uQ6uIbXQBj26jCgn41+80HVG0zc/tcUUNDEb3ztbfhWHxn/ugOfbmgdQSwMEFAAAAAgA8EZIXWF7L0OJAAAA8gAAAAsAAABfcmVscy8ucmVsc43POw4CIRAG4KsQDrCzWlgYoLLZ1ngBAsMjLo8MGPX2UlisxsJy5p98f0accdU9ltxCrI090pqb5KH3egRoJmDSbSoV80hcoaT7GMlD1eaqPcJ+ng9AW4MrsTXZYiWnxe44uzwr/mMX56LBUzG3hLn/qPi6GLImj13yeyEL9r2eBstBCfh4Ub0AUEsDBBQAAAAIAPBGSF28zeml3gAAAPsAAAARAAAAd29yZC9kb2N1bWVudC54bWyzKbdKyU8uzU3NK1GoyM3JK7Yqt1XKKCkpsNLXL07OSM1NLNbLL0jNA8ql5RflJpYAuUXp+uX5RSkFRfnJqcXFmXnpuTn6RgYGZvq5iZl5SnY25VZJ+SmVILoARBSBiBK7pxM6nnVMeLa44eWqnmddS59O7HqyZ8GLfZOfbl/6ZPe2xw1NL9a3PFvQ/nT5lKcTZzzZM+tp2+an62Y9m9PwfHcHUOrpuoXPd09+Nm/O+z2zn81ZAzVhd+uTfbNf7Gt92rUSqODZ/KVAc2z0QdaByCIwWQAmIU7SR3jXDgBQSwECFAMUAAAACADwRkhddCScU7sAAAA+AQAAEwAAAAAAAAAAAAAAgAEAAAAAW0NvbnRlbnRfVHlwZXNdLnhtbFBLAQIUAxQAAAAIAPBGSF1hey9DiQAAAPIAAAALAAAAAAAAAAAAAACAAewAAABfcmVscy8ucmVsc1BLAQIUAxQAAAAIAPBGSF28zeml3gAAAPsAAAARAAAAAAAAAAAAAACAAZ4BAAB3b3JkL2RvY3VtZW50LnhtbFBLBQYAAAAAAwADALkAAACrAgAAAAA=", "base64");
const byteHash = createHash("sha256").update(bytes).digest("hex");
const artifact = { id: ids.artifact, org_id: ids.org, task_id: ids.task, report_id: ids.review, snapshot_id: ids.snapshot, format: "docx", sha256: byteHash, size_bytes: bytes.length, report_input_hash: hash, decisions_snapshot_sha256: decisionHash, renderer_identity: "synthetic-report-v1", created_at: now, human_only: true, advisory_only: true };
const advisory = "评标委员会决定最终评审结果；本报告仅供辅助审查。";

async function fixture(page, options = {}) {
  if (!process.env.E2E_STATIC_DIR) throw new Error("E2E_STATIC_DIR required; this suite starts no services");
  const directory = resolve(process.env.E2E_STATIC_DIR), origin = new URL(process.env.E2E_BASE_URL ?? "https://console.test").origin;
  if (!origin.startsWith("https://")) throw new Error("HTTPS fixture origin required");
  mkdirSync(output, { recursive: true });
  const state = { role: "bidder", renderFailed: false, changed: false, conflict: false, expiredLink: false, wrongTask: false, reads: [], writes: [], errors: [], unexpected: [], published: false, ...options };
  state.historyStatus ??= state.published ? "ready" : null;
  state.attempts = 0;
  const citation = (document, quote) => ({ document_id: document, page_id: uuid(20), page: document === ids.tender ? 3 : 5, quote, page_label: "original_pdf", location: null });
  const finding = () => ({ id: uuid(30), review_id: ids.review, task_id: ids.task, title: "合成参数偏离", code: "synthetic_finding", outcome: "deviation", severity: "high", impact: "rejection", basis: { kind: "model", rule_or_prompt_version: "synthetic-v1", model: "synthetic-model" }, tender_support: [citation(ids.tender, ["admin", "bidder"].includes(state.role) ? unsafe : "已脱敏招标要求。")], bid_support: [citation(ids.bid, bidQuote)], state: "confirmed", revision: 3, explanation: "合成参数不足。", remediation: "人工核对并补充真实响应。", limitation_codes: [] });
  const decision = { id: uuid(40), finding_id: uuid(30), action: "confirm", reason: "合成人工决定：确认偏离", decided_at: now, decided_by: ids.user };
  const notice = (label, text) => ({ kind: "notice", label, text });
  const clearedRow = row => {
    if (row.kind === "finding") { const safe = Object.fromEntries(["id", "review_id", "task_id", "code", "outcome", "severity", "impact", "state", "revision"].map(key => [key, row.finding[key]])); return { kind: "finding", finding: safe, decision: null, summary: "已脱敏偏离说明。", basis_type: "model" }; }
    if (row.kind === "signing_requirement") { const { citation: omitted, ...requirement } = row.requirement; return { kind: row.kind, requirement }; }
    if (row.kind === "pdf_validation") { const { signatures: omitted, ...validation } = row.validation; return { kind: row.kind, validation }; }
    if (row.kind === "basic_information") { const { task_name: omittedName, tender_number: omittedTender, tender_documents: omittedTenders, bid_documents: omittedBids, ...safe } = row; return safe; }
    return row;
  };
  const rows = key => ({
    overall: [notice("总体结论", "已发现废标风险，最大风险为合成参数偏离；补救需逐项人工核对。未评分。")],
    basic_information: [{ kind: "basic_information", task_name: "合成任务", tender_number: "SYNTHETIC", task_id: ids.task, submission_id: ids.submission, assessment_date: "2026-10-08", review_id: ids.review, review_job_id: ids.job, tender_documents: [{ id: ids.tender, size_bytes: 200, sha256: hash }], bid_documents: [{ id: ids.bid, size_bytes: 300, sha256: hash }], completion: "partial" }],
    compliance: [{ kind: "finding", finding: finding(), decision }, notice("报价瑕疵", "未评估；报价页保留在本地。")],
    signatures: [{ kind: "signing_requirement", requirement: { id: uuid(50), applicability: "applies", mark_types: ["company_seal"], date_required: true, citation: citation(ids.tender, "合成签章条款：每页需盖章。"), required_locations: [{ document_id: ids.bid, page: 5, status: "unresolved", reason_code: "presence_not_checked" }] } }, { kind: "pdf_validation", validation: { document_id: ids.bid, validator_identity: "synthetic-local-v1", validation_time: now, status: "unsigned", signatures: [] } }],
    risks: [{ kind: "finding", finding: finding(), decision }],
    scores: [notice("得分预估", "未评分；第一阶段评分不可用。")],
    evidence: [notice("证据核对", "第一阶段未执行证据核对。")],
    remediation: [notice("发现项补救", "人工核对并补充真实响应；不得编造材料。")],
    methodology: [notice("检验范围", "部分页面未覆盖；签章存在性未核验；本地规则与模型判断分别列明。"), { kind: "usage", usage: { provider: "synthetic", model: "synthetic-model", version: "v1", input_tokens: 100, output_tokens: 20, usd: 0.01 } }, { kind: "cost", cost: { ...zero, usd: 0.01, charge: "0.01", task_amount: "0.01" } }, notice("最终认定", advisory)],
  })[key];
  page.on("pageerror", error => state.errors.push(error.message));
  await page.addInitScript(org => sessionStorage.setItem("bid.org.session", JSON.stringify({ session: "synthetic-session", orgId: org, orgName: "合成单位", email: "report@example.test" })), ids.org);
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
    else if (path === `/bid-reviews/${ids.review}/report` && method === "GET") {
      expect(url.pathname.startsWith("/v4/")).toBe(true); expect(url.searchParams.get("limit")).toBe("50");
      const key = url.searchParams.get("section"), snapshot = url.searchParams.get("snapshot_id"), cursor = url.searchParams.get("cursor"); expect(sections.map(item => item.key)).toContain(key); expect([null, ids.snapshot]).toContain(snapshot); expect([null, "synthetic-next"]).toContain(cursor);
      if (snapshot && state.historyStatus !== "ready") return route.fulfill({ status: 409, json: result("review report show", { error: { code: "bid_review_input_changed" } }, [], false) });
      const all = rows(key), paged = key === "compliance", selectedRows = paged ? cursor ? all.slice(1) : all.slice(0, 1) : all;
      response = result("review report show", { review_id: ids.review, task_id: state.wrongTask ? uuid(99) : ids.task, snapshot_id: snapshot, input_hash: hash, report_input_hash: hash, decisions_snapshot_sha256: snapshot ? decisionHash : state.changed ? newerHash : decisionHash, current_decisions_snapshot_sha256: state.changed ? newerHash : decisionHash, renderer_identity: "synthetic-report-v1", completion: "partial", advisory_statement: advisory, sections, section: key, projection: ["admin", "bidder"].includes(state.role) ? "protected" : "cleared", next_cursor: paged && !cursor ? "synthetic-next" : null, artifacts: snapshot && state.published && ["admin", "bidder"].includes(state.role) ? [artifact] : [] }, ["admin", "bidder"].includes(state.role) ? selectedRows : selectedRows.map(clearedRow), false);
    } else if (path === `/bid-reviews/${ids.review}/reports` && method === "GET") {
      expect(url.pathname.startsWith("/v4/")).toBe(true); expect(url.searchParams.get("limit")).toBe("50");
      response = result("review report list", { review_id: ids.review, task_id: ids.task, next_cursor: null }, state.historyStatus && ["admin", "bidder"].includes(state.role) ? [{ snapshot_id: ids.snapshot, input_hash: hash, can_retry: ["failed", "cancelled"].includes(state.historyStatus), created_at: now, decisions_snapshot_sha256: decisionHash, report_input_hash: hash, artifact_status: state.historyStatus, artifacts: state.published ? [artifact] : [] }] : []);
    } else if (path === `/bid-reviews/${ids.review}/artifacts` && method === "POST") {
      expect(url.pathname.startsWith("/v4/")).toBe(true); expect(["admin", "bidder"]).toContain(state.role);
      const body = request.postDataJSON(); state.writes.push(body); expect(body.report_id).toBe(ids.review); expect(body.expected_decisions_snapshot_sha256).toBe(state.changed ? newerHash : decisionHash);
      if (body.dry_run) { expect(body).not.toHaveProperty("preflight_token"); expect(body).not.toHaveProperty("expected_input_hash"); response = result("review report", { report_id: ids.review, input_hash: hash, report_input_hash: hash, decisions_snapshot_sha256: state.changed ? newerHash : decisionHash, renderer_identity: "synthetic-report-v1", expires_at: new Date(Date.now() + 900000).toISOString(), preflight_token: "synthetic-signed-receipt", formats: ["docx", "console"], budget: { input_hash: hash, planned_calls: 0, estimate: zero }, admission_blockers: [] }); }
      else {
        expect(body.expected_input_hash).toBe(hash); expect(body.preflight_token).toBe("synthetic-signed-receipt");
        if (state.conflict) return route.fulfill({ status: 409, json: result("review report", { error: { code: "bid_review_decisions_changed" } }, [], false) });
        if (body.retry) { expect(["failed", "cancelled"]).toContain(state.historyStatus); expect(body.request_id).not.toBe(state.failedRequestId); state.renderFailed = false; state.historyStatus = "pending"; state.attempts++; }
        else if (!state.historyStatus) { state.historyStatus = "pending"; state.attempts++; }
        state.lastSubmittedId = body.request_id; response = result("review report", { job_id: ids.job });
      }
    } else if (path === `/jobs/${ids.job}` && method === "GET") {
      expect(url.pathname.startsWith("/v4/")).toBe(true); state.published = !state.renderFailed;
      state.historyStatus = state.renderFailed ? "failed" : "ready"; if (state.renderFailed) state.failedRequestId = state.lastSubmittedId;
      response = result("job status", { id: ids.job, task_id: ids.task, kind: "bid_review_report", status: state.renderFailed ? "failed" : "succeeded", attempts: state.attempts, reasoning: null, error: state.renderFailed ? { code: "bid_review_report_render_failed", exit_code: 4 } : null, result: state.renderFailed ? null : { snapshot_id: ids.snapshot, review_id: ids.review, artifacts: [artifact] } }, [], !state.renderFailed);
    } else if (path === `/bid-review-artifacts/${ids.artifact}/download-link` && method === "GET") {
      expect(["admin", "bidder"]).toContain(state.role); expect(state.published).toBe(true); response = result("review report download link", { url: `/bid-review-artifacts/${ids.artifact}/download?signature=synthetic-human-signature`, expires_in: 300, artifact });
    } else if (path === `/bid-review-artifacts/${ids.artifact}/download` && method === "GET") {
      expect(["admin", "bidder"]).toContain(state.role); expect(url.searchParams.get("signature")).toBe("synthetic-human-signature"); state.reads.push(`${method} ${path}`);
      if (state.expiredLink) return route.fulfill({ status: 404, json: result("review report download", { error: { code: "not_found" } }, [], false) });
      return route.fulfill({ headers: { "Cache-Control": "no-store", "Content-Disposition": 'attachment; filename="bid-review-report.docx"' }, contentType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", body: bytes });
    } else { state.unexpected.push(`${method} ${path}`); return route.abort(); }
    state.reads.push(`${method} ${path}${url.search}`); return route.fulfill({ json: response });
  });
  return state;
}
async function open(page) { await page.goto(`/app/org/tasks/${ids.task}/bid-reviews/${ids.review}/report`); await expect(page.getByRole("heading", { name: "标书检验报告", exact: true })).toBeVisible(); await expect(page.getByTestId("report-section-overall")).toContainText("未评分"); }
async function readSection(page, key) { await page.getByTestId(`report-section-${key}`).getByRole("button", { name: "查看本节", exact: true }).click(); }
async function render(page) { await page.getByRole("button", { name: "预检 Word 报告", exact: true }).click(); await expect(page.getByRole("button", { name: "提交 Word 渲染", exact: true })).toBeDisabled(); await page.getByText("确认此决定快照，提交 Word 渲染", { exact: true }).click(); await page.getByRole("button", { name: "提交 Word 渲染", exact: true }).click(); }
async function evidence(page, state, name) { await page.screenshot({ path: join(output, `${name}.png`), fullPage: true }); expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]); expect(await page.evaluate(() => window.__unsafe)).toBeUndefined(); const stored = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } })); expect(stored).not.toContain(unsafe); expect(stored).not.toContain(bidQuote); expect(stored).not.toContain("合成人工决定"); writeFileSync(join(output, `${name}.json`), JSON.stringify({ passed: true, reads: state.reads, previewCount: state.writes.filter(item => item.dry_run).length, submitCount: state.writes.filter(item => !item.dry_run).length, errors: state.errors, unexpected: state.unexpected }, null, 2)); }

test("nine report sections retain advisory, quotes, human decision, unknown locations and bounded pagination", async ({ page }) => {
  const state = await fixture(page); await open(page);
  for (const section of sections) await expect(page.getByRole("heading", { name: section.title, exact: true })).toBeVisible();
  for (const section of sections.slice(1)) await readSection(page, section.key);
  const compliance = page.getByTestId("report-section-compliance"); await expect(compliance).toContainText(unsafe); await expect(compliance).toContainText(bidQuote); await expect(compliance).toContainText("合成人工决定：确认偏离"); await expect(compliance.locator("img")).toHaveCount(0);
  await compliance.getByRole("button", { name: "下一页 三、废标判定", exact: true }).click(); await expect(compliance).toContainText("报价页保留在本地"); await expect(compliance).not.toContainText(unsafe);
  await compliance.getByRole("button", { name: "上一页 三、废标判定", exact: true }).click(); await expect(compliance).toContainText(unsafe);
  await expect(page.getByTestId("signing-location")).toContainText("位置未解决"); await expect(page.getByTestId("report-section-scores")).toContainText("未评分"); await expect(page.getByTestId("report-section-evidence")).toContainText("未执行"); expect(state.writes).toHaveLength(0); await evidence(page, state, "01-sections-pagination");
});
test("write-free preview requires explicit submit, freezes snapshot and verifies signed Word bytes", async ({ page }) => {
  const state = await fixture(page); await open(page); await render(page); await expect(page.getByRole("button", { name: "下载 Word 报告", exact: true })).toBeVisible();
  expect(state.writes).toHaveLength(2); expect(state.writes[0].dry_run).toBe(true); expect(state.writes[1].dry_run).toBe(false); expect(state.writes[1].request_id).toBe(state.writes[0].request_id);
  const downloaded = page.waitForEvent("download"); await page.getByRole("button", { name: "下载 Word 报告", exact: true }).click(); const receipt = await downloaded; expect(receipt.suggestedFilename()).toBe("bid-review-report.docx"); await receipt.saveAs(join(output, "02-signed-download.docx")); await expect(page.getByText("已核验文件长度与 SHA-256，开始下载 bid-review-report.docx。", { exact: true })).toBeVisible(); await evidence(page, state, "02-render-download");
});
for (const role of ["technical", "viewer"]) test(`${role} cleared reader has no render or download controls and receives no protected quotes`, async ({ page }) => {
  const state = await fixture(page, { role, published: true }); await open(page); await readSection(page, "risks"); await expect(page.locator("body")).not.toContainText(unsafe); await expect(page.locator("body")).not.toContainText(bidQuote); await expect(page.getByTestId("report-section-risks")).toContainText("已脱敏偏离说明。"); await expect(page.getByRole("button", { name: "预检 Word 报告", exact: true })).toHaveCount(0); await expect(page.getByRole("button", { name: "下载 Word 报告", exact: true })).toHaveCount(0); expect(state.writes).toHaveLength(0); await evidence(page, state, `03-cleared-${role}`);
});
test("render failure retains console report without partial Word artifact", async ({ page }) => {
  const state = await fixture(page, { renderFailed: true }); await open(page); await render(page); await expect(page.getByTestId("job-status")).toContainText("失败"); await expect(page.getByRole("button", { name: "下载 Word 报告", exact: true })).toHaveCount(0); await expect(page.getByTestId("report-section-overall")).toContainText("未评分"); expect(state.published).toBe(false); await evidence(page, state, "04-render-failure");
});
test("failed render requires an explicit retry under a fresh preview request and polls the same job again", async ({ page }) => {
  const state = await fixture(page, { renderFailed: true }); await open(page); await render(page); await expect(page.getByTestId("job-status")).toContainText("失败");
  const failedSubmit = state.writes.find(row => !row.dry_run); await page.getByRole("button", { name: "预检 Word 报告", exact: true }).click();
  const preview = page.getByTestId("report-render-preview"); await expect(preview).toContainText("需明确选择重试"); await preview.getByText("确认此决定快照，提交 Word 渲染", { exact: true }).click(); await expect(preview.getByRole("button", { name: "提交 Word 渲染", exact: true })).toBeDisabled();
  await preview.getByText("明确重试此决定快照的失败或取消作业", { exact: true }).click(); await preview.getByRole("button", { name: "提交 Word 渲染", exact: true }).click();
  await expect(page.getByRole("button", { name: "下载 Word 报告", exact: true })).toBeVisible(); expect(state.attempts).toBe(2);
  const submits = state.writes.filter(row => !row.dry_run); expect(submits).toHaveLength(2); expect(submits[1].retry).toBe(true); expect(submits[1].request_id).not.toBe(failedSubmit.request_id); expect(submits[1].request_id).toBe(state.writes.filter(row => row.dry_run)[1].request_id); await evidence(page, state, "09-explicit-render-retry");
});
for (const historyStatus of ["pending", "failed"]) test(`${historyStatus} history cannot be selected while the current console report stays readable`, async ({ page }) => {
  const state = await fixture(page, { historyStatus }); await open(page);
  const wrapper = page.locator(".el-select").filter({ has: page.getByRole("combobox", { name: "报告快照选择", exact: true }) }).locator(".el-select__wrapper"); await wrapper.click(); const option = page.getByRole("option").filter({ hasText: ids.snapshot }); await expect(option).toHaveClass(/is-disabled/); await option.dispatchEvent("click");
  expect(state.reads.some(path => path.includes("snapshot_id="))).toBe(false); await expect(page.getByTestId("report-section-overall")).toContainText("未评分"); await page.getByRole("option", { name: "当前决定（待渲染）", exact: true }).click(); await expect(page.getByTestId("report-section-overall")).toContainText("未评分"); await evidence(page, state, `10-history-${historyStatus}`);
});
test("later decisions mark fixed history stale without rewriting its finding decision", async ({ page }) => {
  const state = await fixture(page, { published: true, changed: true }); await open(page);
  const wrapper = page.locator(".el-select").filter({ has: page.getByRole("combobox", { name: "报告快照选择", exact: true }) }).locator(".el-select__wrapper"); await expect(wrapper).toBeVisible(); await wrapper.click(); await page.getByRole("option").filter({ hasText: ids.snapshot }).click();
  await expect(page.getByTestId("snapshot-stale")).toBeVisible(); await readSection(page, "risks"); await expect(page.getByTestId("report-section-risks")).toContainText("合成人工决定：确认偏离"); expect(state.reads.some(path => path.includes(`snapshot_id=${ids.snapshot}`))).toBe(true); await evidence(page, state, "05-immutable-history");
});
test("expired signed byte delivery fails without local download", async ({ page }) => {
  const state = await fixture(page, { published: true, expiredLink: true }); await open(page);
  await page.locator(".el-select").filter({ has: page.getByRole("combobox", { name: "报告快照选择", exact: true }) }).locator(".el-select__wrapper").click();
  await page.getByRole("option").filter({ hasText: ids.snapshot }).click(); let downloads = 0; page.on("download", () => downloads++); await page.getByRole("button", { name: "下载 Word 报告", exact: true }).click(); await expect(page.getByRole("alert").filter({ hasText: "不可访问" })).toBeVisible(); expect(downloads).toBe(0); await evidence(page, state, "06-expired-link");
});
test("decision conflict discards preview and requires a fresh preview", async ({ page }) => {
  const state = await fixture(page, { conflict: true }); await open(page); await render(page); await expect(page.getByRole("alert").filter({ hasText: "人工决定已变化" })).toBeVisible(); await expect(page.getByTestId("report-render-preview")).toHaveCount(0); expect(state.published).toBe(false); await evidence(page, state, "07-decision-conflict");
});
test("wrong-task response is rejected before any report row or download control", async ({ page }) => {
  const state = await fixture(page, { wrongTask: true }); await page.goto(`/app/org/tasks/${ids.task}/bid-reviews/${ids.review}/report`); await expect(page.getByRole("alert")).toBeVisible(); await expect(page.getByTestId("report-row")).toHaveCount(0); await expect(page.getByRole("button", { name: "预检 Word 报告", exact: true })).toHaveCount(0); await evidence(page, state, "08-task-boundary");
});
