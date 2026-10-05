import { test, expect } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve, join, extname, basename } from "node:path";

const id = "00000000-0000-0000-0000-000000000001";
const secret = "synthetic-only-browser-key";
const replacement = "synthetic-only-replacement-key";
const output = resolve(process.env.E2E_OUTPUT ?? "../data/work/platform-credentials-validation/browser");
const result = (command, data = {}, items = [], ok = true) => ({ ok, command, data, items, warnings: [], cost: { llm_tokens: 0, ocr_pages: 0, usd: "0" }, duration_ms: 0 });
const view = () => ({ id, name: "main", purpose: "catalog_llm", provider: "anthropic", endpoint: "https://api.anthropic.com", revision: 1, secret_version: 1, state: "disabled", fingerprint: "sha256:aaaaaaaaaaaaaaaa", last_four: "-key", created_at: "2026-10-05T00:00:00Z", updated_at: "2026-10-05T00:00:00Z", updated_by: "ops@example.test", consumers: [{ kind: "catalog_model", model_id: "synthetic-model", model_revision: 1, enabled: true, default: true }] });

// Optional built-app fixture keeps this suite executable without an API or preview process.
test.beforeEach(async ({ page }) => {
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
  await page.addInitScript(() => sessionStorage.setItem("bid.platform.session", "synthetic-session"));
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
  await page.goto("/app/platform/credentials");
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
  await expect(page.getByTestId("credential-consumers")).toContainText("synthetic-model");
  await row.getByRole("button", { name: "替换密钥" }).click();
  await page.fill("input[name=credential-key]", replacement);
  await page.locator("form.panel button[type=submit]").click();
  await expect(page.getByRole("alert")).toContainText("已被他人修改");
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
  await expect(page.getByTestId("credential-impact")).toContainText("synthetic-model");
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
  await page.screenshot({ path: join(output, "credential-removed.png"), fullPage: true });
  writeFileSync(join(output, "result.json"), JSON.stringify({ passed: true, checks: ["write-only", "default-disabled", "consumer-impact", "revision-conflict", "authentication-only", "tombstone"] }, null, 2));
});

test("model selection lists matching active credentials", async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem("bid.platform.session", "synthetic-session"));
  const active = { ...view(), state: "active" };
  const disabled = { ...active, id: "00000000-0000-0000-0000-000000000002", name: "disabled", state: "disabled" };
  const mismatched = { ...active, id: "00000000-0000-0000-0000-000000000003", name: "other_vendor", provider: "openai", endpoint: "https://other.example.test" };
  await page.route((url) => url.pathname.startsWith("/platform/credentials"), (route) => route.fulfill({ json: result("platform credential list", { next_after_name: null }, [active, disabled, mismatched]) }));
  await page.route((url) => url.pathname === "/platform/models", (route) => route.fulfill({ json: result("platform model list", { currency: "USD" }, []) }));
  await page.goto("/app/platform/models");
  await page.getByRole("button", { name: "添加模型" }).click();
  await page.locator("[data-testid=model-credential-select]").click();
  await expect(page.getByRole("option", { name: /main/ })).toBeVisible();
  await expect(page.getByRole("option", { name: /disabled/ })).toHaveCount(0);
  await expect(page.getByRole("option", { name: /other_vendor/ })).toHaveCount(0);
});

test("removed credential permits only disabling the unchanged model", async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem("bid.platform.session", "synthetic-session"));
  const removed = { ...view(), state: "removed" };
  let model = { id: "synthetic-model", capability: "llm_extract", provider: "anthropic", model: "synthetic-model", base_url: null, credential: "main", vendor_input_usd_per_mtok: 1, vendor_output_usd_per_mtok: 2, sale_input_per_mtok: 3, sale_output_per_mtok: 4, default: true, enabled: true, reasoning: [], default_reasoning: null, revision: 1, updated_by: "ops@example.test", updated_at: "2026-10-05T00:00:00Z", credential_configured: false };
  let writes = 0;
  await page.route((url) => url.pathname.startsWith("/platform/credentials"), (route) => route.fulfill({ json: result("platform credential list", { next_after_name: null }, [removed]) }));
  await page.route((url) => url.pathname === "/platform/models", async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: result("platform model list", { currency: "USD" }, [model]) });
    const body = route.request().postDataJSON();
    const { revision, updated_by, updated_at, credential_configured, ...original } = model;
    expect(body).toEqual({ ...original, enabled: false, default: false, expected_revision: revision });
    writes++;
    model = { ...model, enabled: false, default: false, revision: revision + 1 };
    await route.fulfill({ json: result("platform model set", model) });
  });
  await page.goto("/app/platform/models");
  const row = page.getByRole("row", { name: /synthetic-model/ });
  await row.getByRole("button", { name: "停用", exact: true }).click();
  await expect(row).toContainText("已停用");
  expect(writes).toBe(1);
  await page.getByRole("button", { name: "添加模型" }).click();
  await page.getByTestId("model-credential-select").click();
  await expect(page.getByRole("option", { name: /main/ })).toHaveCount(0);
});
