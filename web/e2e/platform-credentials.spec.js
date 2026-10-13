import { test, expect } from "@playwright/test";
import { openConsole } from "./access-key.js";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve, join, extname, basename } from "node:path";

process.env.PLAYWRIGHT_NO_COPY_PROMPT = "1";
test.use({ trace: "off", screenshot: "off", video: "off" });

const id = "00000000-0000-0000-0000-000000000001";
const secret = "synthetic-only-browser-key";
const replacement = "synthetic-only-replacement-key";
const output = resolve(process.env.E2E_OUTPUT ?? "../data/work/platform-credentials-validation/browser");
const result = (command, data = {}, items = [], ok = true) => ({ ok, command, data, items, warnings: [], cost: { calls: 0, llm_tokens: 0, ocr_pages: 0, search_requests: 0, browser_units: 0, unresolved_calls: 0 }, duration_ms: 0 });
const view = () => ({ id, name: "main", purpose: "standalone_llm", provider: "anthropic", endpoint: "https://api.anthropic.com", revision: 1, secret_version: 1, state: "disabled", fingerprint: "sha256:aaaaaaaaaaaaaaaa", last_four: "-key", created_at: "2026-10-05T00:00:00Z", updated_at: "2026-10-05T00:00:00Z", consumers: [{ kind: "service", service: "standalone_llm", selected: true }] });

// Optional built-app fixture keeps this suite executable without an API or preview process.
test.beforeEach(async ({ page }) => {
  await page.route("**/auth/session", route => route.fulfill({ json: route.request().method() === "POST" ? result("auth session create", { session: "synthetic-session", expires_in: 3600 }) : result("auth session show", { actor_kind: "session", scopes: [], expires_at: null }) }));
  if (!process.env.E2E_STATIC_DIR) return;
  const directory = resolve(process.env.E2E_STATIC_DIR);
  await page.route((url) => url.pathname.startsWith("/app/"), async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const file = pathname.startsWith("/app/assets/") ? join(directory, "assets", basename(pathname)) : join(directory, "index.html");
    const contentType = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" }[extname(file)] ?? "application/octet-stream";
    await route.fulfill({ contentType, body: readFileSync(file) });
  });
});

test("credential create, consumers, rotation conflict, probe, disable and removal", async ({ page }) => {
  mkdirSync(output, { recursive: true });
  let credential = null;
  let conflict = true;
  let writes = 0;
  await page.route((url) => url.pathname.startsWith("/platform/credentials"), async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const body = request.postDataJSON();
    let action = path.split("/").at(-1);
    if (path === "/platform/credentials") {
      if (request.method() === "GET") return route.fulfill({ json: result("platform credential list", { next_after_name: null }, credential ? [credential] : []) });
      expect(body.api_key).toBe(secret);
      expect(body.active).toBe(false);
      credential = view(); writes++;
      action = "create";
    } else if (request.method() === "GET") action = "show";
    else if (action === "test") return route.fulfill({ status: 422, json: result("platform credential test", { error: { code: "credential_probe_unsupported", message: "Authentication metadata unsupported", exit_code: 4 }, probe: { probe_id: id, credential_id: id, tested_revision: credential.revision, secret_version: credential.secret_version, outcome: "unsupported", duration_ms: 0, checked_at: credential.updated_at, proves: "authentication_only" } }, [], false) });
    else if (action === "replace") {
      expect(body.api_key).toBe(replacement);
      if (conflict) {
        conflict = false;
        credential.revision++;
        return route.fulfill({ status: 409, json: result("platform credential replace", { error: { code: "revision_conflict", message: "Conflict", exit_code: 4 } }, [], false) });
      }
      credential.secret_version++; credential.revision++; writes++;
    } else if (action === "active") { credential.state = body.active ? "active" : "disabled"; credential.revision++; writes++; action = "set-active"; }
    else if (action === "remove") { credential.state = "removed"; credential.revision++; writes++; }
    await route.fulfill({ json: result("platform credential " + action, { credential }) });
  });
  await openConsole(page, "/platform/credentials");
  await expect(page.getByRole("heading", { name: "服务凭据", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "添加凭据" }).click();
  await page.fill("input[name=credential-name]", "main");
  await page.fill("input[name=credential-endpoint]", "https://api.anthropic.com");
  await page.fill("input[name=credential-key]", secret);
  await expect(page.locator("input[name=credential-key]")).toHaveAttribute("type", "password");
  await page.locator("form.panel button[type=submit]").click();
  const row = page.getByRole("row", { name: /main.*Anthropic/ });
  await expect(row).toContainText("已停用");
  await expect(page.locator("input[name=credential-key]")).toHaveCount(0);
  await row.getByRole("button", { name: "详情" }).click();
  await expect(page.getByTestId("credential-consumers")).toContainText("Standalone / Eval");
  await row.getByRole("button", { name: "替换密钥" }).click();
  await page.fill("input[name=credential-key]", replacement);
  await page.locator("form.panel button[type=submit]").click();
  await expect(page.getByRole("alert")).toHaveText("凭据修订已变化，请刷新后重新打开操作。");
  await expect(page.locator("input[name=credential-key]")).toHaveValue("");
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await row.getByRole("button", { name: "替换密钥" }).click();
  await page.fill("input[name=credential-key]", replacement);
  await page.locator("form.panel button[type=submit]").click();
  await expect(row).toContainText("密钥第 2 版");
  await row.getByRole("button", { name: "启用", exact: true }).click();
  await page.locator("form.panel button[type=submit]").click();
  await expect(row).toContainText("已启用");
  await row.getByRole("button", { name: "测试认证" }).click();
  await expect(row.getByTestId("credential-probe")).toContainText("不支持认证探针");
  await expect(row.getByTestId("credential-probe")).toContainText("第 4 版");
  await row.getByRole("button", { name: "停用", exact: true }).click();
  await expect(page.getByTestId("credential-impact")).toContainText("Standalone / Eval");
  await page.locator("form.panel button[type=submit]").click();
  await expect(row).toContainText("已停用");
  await row.getByRole("button", { name: "移除", exact: true }).click();
  await page.locator("form.panel button[type=submit]").click();
  await expect(row).toContainText("已移除");
  await expect(row.getByRole("button", { name: "替换密钥" })).toHaveCount(0);
  expect(writes).toBe(5);
  const rendered = await page.locator("body").innerText();
  expect(rendered).not.toContain(secret);
  expect(rendered).not.toContain(replacement);
  expect(await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))).not.toContain(secret);
  writeFileSync(join(output, "result.json"), JSON.stringify({ passed: true, checks: ["write-only", "default-disabled", "consumer-impact", "revision-conflict", "authentication-only", "tombstone"] }, null, 2));
});
