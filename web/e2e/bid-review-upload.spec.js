import { test, expect } from "@playwright/test";
import { openConsole } from "./access-key.js";
import { mkdirSync, readFileSync } from "node:fs";
import { basename, extname, join, resolve } from "node:path";

const output = resolve(process.env.E2E_OUTPUT ?? "../data/work/bid-review-upload/browser");
const uuid = n => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
const ids = { task: uuid(3), submission: uuid(4), job: uuid(5) };
const hash = "a".repeat(64), now = "2026-10-08T00:00:00Z";
const zero = { calls: 0, llm_tokens: 0, ocr_pages: 0, search_requests: 0, browser_units: 0, unresolved_calls: 0 };
const result = (command, data = {}, items = [], ok = true) => ({ ok, command, data, items, warnings: [], cost: zero, duration_ms: 0 });
const pdf = name => ({ name, mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\nSynthetic browser file " + name) });

async function fixture(page, options = {}) {
  if (!process.env.E2E_STATIC_DIR) throw new Error("E2E_STATIC_DIR is required; this suite starts no services");
  mkdirSync(output, { recursive: true });
  const directory = resolve(process.env.E2E_STATIC_DIR);
  const origin = new URL(process.env.E2E_BASE_URL ?? "https://console.test").origin;
  if (!origin.startsWith("https://")) throw new Error("Use an HTTPS E2E_BASE_URL for Web Crypto upload hashing");
  const state = { uploads: 0, uploadPreviews: 0, prepares: 0, preparePreviews: 0, prepared: false, submitted: false, hold: false, errors: [], unexpected: [], files: [], ...options };
  page.on("pageerror", error => state.errors.push(error.message));
  const submission = () => ({ id: ids.submission, task_id: ids.task, revision: 1, manifest_sha256: hash, created_at: now,
    ...(state.prepared ? { state: "prepared", preparation_job_id: ids.job, preparation_input_hash: hash, documents: state.files.map(file => ({ ...file, page_count: 2, rendered_pdf_sha256: file.sha256, render_profile: "local-v1", renderer_identity: "synthetic-v1", citation_mode: "page", parsing_warnings: [] })) } : { state: "uploaded", files: state.files }) });
  const detail = () => ({ submission: submission(), preparation: state.submitted ? { job_id: ids.job, status: state.prepared ? "succeeded" : "running", input_hash: hash } : null, inventory: state.prepared ? state.files.map(file => ({ document_id: file.id, page_count: 2, text_pages: 1, image_pages: 1, signature_fields: file.role === "bid" ? 1 : 0, parsing_warnings: file.role === "bid" ? ["pdf_signature_fields_not_validated", "<img src=x onerror=window.__unsafe=1>"] : [] })) : [] });
  await page.route("**/*", async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    if (url.origin !== origin) { state.unexpected.push(`${method} external-origin`); return route.abort(); }
    if (path.startsWith("/app/")) {
      const file = path.startsWith("/app/assets/") ? join(directory, "assets", basename(path)) : join(directory, "index.html");
      return route.fulfill({ contentType: { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" }[extname(file)] ?? "application/octet-stream", body: readFileSync(file) });
    }
    if (path === "/health") return route.fulfill({ json: result("health", { status: "ok" }) });
    if (method === "POST" && path === "/auth/session") return route.fulfill({ json: result("auth session create", { session: "synthetic-session", expires_in: 86400 }) });
    if (method === "GET" && path === "/auth/session") return route.fulfill({ json: result("auth session show", { actor_kind: "session", scopes: [], expires_at: null }) });
    if (!["/health", "/auth/session"].includes(path)) expect(request.headers().authorization).toBe("Bearer synthetic-session");
    let response;
    if (path === `/tasks/${ids.task}/progress`) response = result("task progress", { task_id: ids.task, event_cursor: "opaque-snapshot", as_of: now });
    else if (method === "GET" && path === `/tasks/${ids.task}/bid-submissions`) {
      expect(url.pathname.startsWith("/")).toBe(true); expect(url.searchParams.get("limit")).toBe("50");
      response = result("review submission list", { task_id: ids.task, total: state.uploads ? 1 : 0, next_cursor: null }, state.uploads ? [submission()] : []);
    } else if (method === "POST" && path === `/tasks/${ids.task}/bid-submissions`) {
      expect(url.pathname.startsWith("/")).toBe(true);
      const wire = request.postDataBuffer().toString("utf8"), match = wire.match(/name="metadata"\r\n\r\n([^\r]+)/);
      expect(match).not.toBeNull(); const body = JSON.parse(match[1]);
      expect(body.files.map(file => file.role)).toEqual(["tender", "bid"]);
      expect(body.files[1].kind).toBe("qualification");
      if (body.dry_run) { state.uploadPreviews++; state.uploadRequest = body.request_id; response = result("review upload", { dry_run: true, files: body.files, payload_sha256: hash, limits: { files: 20, file_bytes: 40 * 1024 * 1024, submission_bytes: 40 * 1024 * 1024 }, cost: zero }); }
      else { expect(body.request_id).toBe(state.uploadRequest); state.uploads++; state.files = body.files.map((file, index) => ({ ...file, id: uuid(10 + index), file_id: uuid(20 + index), task_id: ids.task, submission_id: ids.submission })); response = result("review upload", submission()); }
    } else if (method === "GET" && path === `/tasks/${ids.task}/bid-reviews`) { expect(url.pathname.startsWith("/")).toBe(true); response = result("review list", { task_id: ids.task, next_cursor: null }, []); }
    else if (method === "GET" && path === `/bid-submissions/${ids.submission}`) response = result("review submission show", detail());
    else if (method === "GET" && path === `/bid-submissions/${ids.submission}/outbound-authorizations`) response = result("review outbound list", { total: 0, next_cursor: null }, []);
    else if (method === "GET" && path === `/bid-submissions/${ids.submission}/redaction`) response = result("review redaction", { submission_id: ids.submission, expected_revision: 1, expected_authorization_id: null, expected_submission_manifest_sha256: hash, expected_preparation_input_hash: hash, expected_redaction_manifest_sha256: hash, provider_bindings_sha256: hash, redaction_revision: 1, redaction_rule_version: "synthetic-v1", confidential_binding_sha256: hash, derived_name_lists_sha256: hash, human_name_revision: 0, pages: [], total: 0, next_cursor: null, blockers: [] });
    else if (method === "POST" && path === `/tasks/${ids.task}/bid-submissions/${ids.submission}/prepare`) {
      const body = request.postDataJSON(); expect(body.submission_id).toBe(ids.submission); expect(url.pathname.startsWith("/")).toBe(true);
      if (body.dry_run) {
        state.preparePreviews++; state.prepareRequest = body.request_id;
        const asOf = new Date(), expiresAt = new Date(asOf.getTime() + 900000).toISOString();
        response = result("review prepare", { dry_run: true, submission_id: ids.submission, input_hash: hash, local_only: true, admission_blockers: [], expires_at: expiresAt, preflight_token: "synthetic-receipt", preflight: { dry_run: true, command: "review prepare", task_id: ids.task, input_hash: hash, as_of: asOf.toISOString(), planned_calls: 0, maximum_calls: 0, estimate: zero, next_call: null, admission_blocker: null, full_run_guaranteed: false } });
      } else {
        expect(body.request_id).toBe(state.prepareRequest); expect(body.expected_input_hash).toBe(hash); expect(body.preflight_token).toBe("synthetic-receipt"); state.prepares++; state.submitted = true; response = result("review prepare", { job_id: ids.job, status: "queued", cached: false });
      }
    } else if (method === "GET" && path === `/jobs/${ids.job}`) {
      if (!state.hold) state.prepared = true;
      response = result("job status", { id: ids.job, kind: "bid_review_prepare", status: state.prepared ? "succeeded" : "running", attempts: 1, reasoning: null, error: null, result: state.prepared ? { submission_id: ids.submission, page_count: 4 } : null });
    } else if (method === "POST" && path === `/jobs/${ids.job}/cancel`) response = result("job cancel", { id: ids.job, status: "cancelled" });
    else { state.unexpected.push(`${method} ${path}`); return route.abort(); }
    return route.fulfill({ json: response });
  });
  state.verify = () => { expect(state.errors).toEqual([]); expect(state.unexpected).toEqual([]); };
  return state;
}

async function pick(page) {
  await page.getByLabel("选择招标文件", { exact: true }).setInputFiles(pdf("synthetic-tender.pdf"));
  await page.getByLabel("选择投标文件", { exact: true }).setInputFiles(pdf("synthetic-bid.pdf"));
  await page.locator(".el-select").click();
  await page.getByRole("option", { name: "资格材料", exact: true }).click();
}

test("wizard previews without uploading, explicitly uploads and prepares fixed page inventory", async ({ page }) => {
  const state = await fixture(page);
  await openConsole(page, `/org/tasks/${ids.task}/bid-submissions`);
  await expect(page.getByRole("heading", { name: "标书检验", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "标书检验", exact: true })).toBeVisible();
  await pick(page);
  await page.getByRole("button", { name: "预览上传限制", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("每份 40 MiB");
  expect(state.uploads).toBe(0); expect(state.uploadPreviews).toBe(1);
  await page.screenshot({ path: join(output, "01-upload-preview.png"), fullPage: true });
  await page.getByText("确认文件角色与材料类型，上传此固定版本", { exact: true }).click();
  await page.getByRole("button", { name: "上传固定版本", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/bid-submissions/${ids.submission}$`));
  await page.getByRole("button", { name: "预检本地准备", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("预检未创建作业");
  expect(state.prepares).toBe(0); expect(state.preparePreviews).toBe(1);
  await page.screenshot({ path: join(output, "02-prepare-preview.png"), fullPage: true });
  await page.getByText("确认此固定版本，开始本地准备", { exact: true }).click();
  await page.getByRole("button", { name: "提交本地准备", exact: true }).click();
  await expect(page.getByRole("table", { name: "页面清单", exact: true })).toContainText("资格材料");
  await expect(page.getByText("准备状态：准备完成", { exact: true })).toBeVisible();
  expect(state.prepares).toBe(1); expect(state.uploads).toBe(1);
  await expect(page.getByText("仅发现 PDF 签名字段，尚未验证", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => window.__unsafe)).toBeUndefined();
  expect(await page.evaluate(() => Object.values(sessionStorage).join(" "))).not.toContain("synthetic-bid.pdf");
  await page.screenshot({ path: join(output, "03-inventory.png"), fullPage: true });
  state.verify();
});

test("changing selected files clears preview and explicit upload consent", async ({ page }) => {
  const state = await fixture(page);
  await openConsole(page, `/org/tasks/${ids.task}/bid-submissions`); await pick(page);
  await page.getByRole("button", { name: "预览上传限制", exact: true }).click();
  await page.getByText("确认文件角色与材料类型，上传此固定版本", { exact: true }).click();
  await page.getByRole("button", { name: "移除", exact: true }).last().click();
  await expect(page.getByRole("button", { name: "上传固定版本", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "预览上传限制", exact: true })).toBeDisabled();
  expect(state.uploads).toBe(0); state.verify();
});
