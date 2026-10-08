import { test, expect } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, extname, join, resolve } from "node:path";

const output = resolve(process.env.E2E_OUTPUT ?? "../data/work/operator-enrollment/browser");
const result = (command, data = {}, items = [], ok = true) => ({ ok, command, data, items, warnings: [], cost: { llm_tokens: 0, ocr_pages: 0, usd: "0" }, duration_ms: 0 });
const token = "synthetic.enrollment-token", pending = "synthetic.encrypted-pending";
const secret = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP", password = "synthetic-enrollment-password";
const email = "operator@example.test", expiresAt = "2036-10-08T18:30:00Z";
const uri = `otpauth://totp/AI%20Bid%20Tool:${email}?secret=${secret}&issuer=AI%20Bid%20Tool`;
const failure = (command, code) => result(command, { error: { code, message: `${password} ${token} ${secret}`, exit_code: 4 } }, [], false);

test.beforeEach(async ({ page }) => {
  mkdirSync(output, { recursive: true });
  if (!process.env.E2E_STATIC_DIR) return;
  const directory = resolve(process.env.E2E_STATIC_DIR);
  await page.route((url) => url.pathname.startsWith("/app/"), async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    const file = pathname.startsWith("/app/assets/") ? join(directory, "assets", basename(pathname)) : join(directory, "index.html");
    const contentType = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" }[extname(file)] ?? "application/octet-stream";
    await route.fulfill({ contentType, body: readFileSync(file) });
  });
});

async function start(page, step = "set") {
  await page.route((url) => url.pathname === "/platform/enrollment/start", async (route) => {
    expect(route.request().headers().authorization).toBeUndefined();
    expect(route.request().postDataJSON()).toEqual({ token });
    await route.fulfill({ json: result("platform enrollment start", { email, password: step, totp_secret: secret, otpauth_uri: uri, pending, expires_at: expiresAt }) });
  });
}
async function fill(page, step = "set") {
  await page.fill("input[name=password]", password);
  if (step === "set") await page.fill("input[name=confirm-password]", password);
  await page.fill("input[name=code]", "123456");
}
async function assertNoStoredSecrets(page) {
  const stored = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
  for (const value of [token, pending, password, secret, uri]) expect(stored).not.toContain(value);
}

test("public enrollment sets password, renders QR locally and clears secrets on completion", async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem("bid.platform.session", "synthetic-session"));
  await start(page);
  const requests = [], logs = [];
  page.on("console", message => logs.push(message.text()));
  await page.route((url) => url.pathname === "/platform/enrollment/complete", async (route) => {
    expect(route.request().headers().authorization).toBeUndefined();
    requests.push(route.request().postDataJSON());
    await route.fulfill({ json: result("platform enrollment complete", { email, enrolled: true, account_created: true, password_set: true }) });
  });
  await page.goto(`/app/platform/enroll#token=${token}`);
  await expect(page.getByTestId("totp-secret")).toHaveText(secret);
  await expect(page).toHaveURL(/\/app\/platform\/enroll$/);
  const qr = page.getByRole("img", { name: "身份验证器绑定二维码" });
  await expect(qr).toBeVisible();
  expect(await qr.evaluate(canvas => canvas.width)).toBeGreaterThan(100);
  await expect(page.locator("input[name=password]")).toHaveAttribute("autocomplete", "new-password");
  await fill(page);
  await page.fill("input[name=password]", "short");
  await page.getByRole("button", { name: "完成开通" }).click();
  await expect(page.getByRole("alert")).toContainText("密码须为 12 至 1,024 个字符");
  await page.fill("input[name=password]", password);
  await page.fill("input[name=confirm-password]", "different-password");
  await page.getByRole("button", { name: "完成开通" }).click();
  await expect(page.getByRole("alert")).toContainText("两次输入的密码不一致");
  await page.fill("input[name=confirm-password]", password);
  await page.fill("input[name=code]", "123");
  await page.getByRole("button", { name: "完成开通" }).click();
  await expect(page.getByRole("alert")).toContainText("6 位验证码");
  expect(requests).toHaveLength(0);
  await page.fill("input[name=code]", "123456");
  await page.screenshot({ path: join(output, "enrollment-form.png"), fullPage: true });
  await page.getByRole("button", { name: "完成开通" }).click();
  await expect(page.getByRole("status")).toContainText("已完成开通");
  await expect(page.getByRole("link", { name: "登录平台后台" })).toHaveAttribute("href", "/app/platform/login");
  expect(requests).toEqual([{ token, pending, password, code: "123456" }]);
  await expect(page.locator("canvas, input[type=password], input[name=code]")).toHaveCount(0);
  for (const value of [token, pending, password, secret, uri]) expect(await page.locator("body").innerText()).not.toContain(value);
  await assertNoStoredSecrets(page);
  expect(logs.join("\n")).not.toContain(secret);
  expect(logs.join("\n")).not.toContain(password);
  await page.screenshot({ path: join(output, "enrollment-complete.png"), fullPage: true });
  writeFileSync(join(output, "enrollment-result.json"), JSON.stringify({ passed: true, checks: ["public-no-auth", "fragment-removed", "local-qr", "password-validation", "completion-clears-secrets", "no-storage"] }, null, 2));
});

test("existing password is confirmed, retries clear credentials, and unmount discards factor", async ({ page }) => {
  await start(page, "confirm");
  let attempts = 0;
  await page.route((url) => url.pathname === "/platform/enrollment/complete", async (route) => {
    expect(route.request().postDataJSON()).toEqual({ token, pending, password, code: "123456" });
    attempts++;
    await route.fulfill({ status: 401, json: failure("platform enrollment complete", "invalid_enrollment") });
  });
  await page.goto(`/app/platform/enroll#token=${token}`);
  await expect(page.getByRole("note")).toContainText("开通不会更改密码");
  await expect(page.locator("input[name=password]")).toHaveAttribute("autocomplete", "current-password");
  await expect(page.locator("input[name=confirm-password]")).toHaveCount(0);
  await fill(page, "confirm");
  await page.getByRole("button", { name: "完成开通" }).click();
  await expect(page.getByRole("alert")).toContainText("密码或验证码不正确");
  await expect(page.locator("input[name=password]")).toHaveValue("");
  await expect(page.locator("input[name=code]")).toHaveValue("");
  expect(await page.locator("body").innerText()).not.toContain(password);
  expect(await page.locator("body").innerText()).not.toContain(token);
  await assertNoStoredSecrets(page);
  expect(attempts).toBe(1);
  await page.screenshot({ path: join(output, "enrollment-confirm-password.png"), fullPage: true });
  // Route away within the Vue app, so the component's unmount cleanup executes.
  await page.evaluate(() => {
    history.pushState(history.state, "", "/app/setup-password");
    window.dispatchEvent(new PopStateEvent("popstate", { state: history.state }));
  });
  await expect(page.getByRole("heading", { name: "设置密码", exact: true })).toBeVisible();
  await page.evaluate(() => {
    history.pushState(history.state, "", "/app/platform/enroll");
    window.dispatchEvent(new PopStateEvent("popstate", { state: history.state }));
  });
  await expect(page.getByRole("alert")).toContainText("链接已失效或已使用");
  await expect(page.locator("canvas, input[type=password]")).toHaveCount(0);
  expect(await page.locator("body").innerText()).not.toContain(secret);
});

for (const [code, status, text] of [
  ["invalid_enrollment_link", 400, "链接已失效或已使用"],
  ["factor_managed_by_deployment", 409, "由部署配置管理"],
  ["enrollment_unavailable", 503, "网页开通暂不可用"],
]) {
  test(`start ${code} never exposes server error values`, async ({ page }) => {
    await page.route((url) => url.pathname === "/platform/enrollment/start", route => route.fulfill({ status, json: failure("platform enrollment start", code) }));
    await page.goto(`/app/platform/enroll#token=${token}`);
    await expect(page.getByRole("alert")).toContainText(text);
    await expect(page.locator("canvas, input[type=password]")).toHaveCount(0);
    const body = await page.locator("body").innerText();
    for (const value of [token, password, secret]) expect(body).not.toContain(value);
    await assertNoStoredSecrets(page);
  });
}

for (const [code, status, text] of [
  ["invalid_enrollment_link", 400, "链接已失效或已使用"],
  ["too_many_attempts", 429, "失败次数过多"],
  ["auth_busy", 503, "服务繁忙"],
  ["weak_password", 400, "密码须为 12 至 1,024 个字符"],
]) {
  test(`completion ${code} clears passwords and uses safe error wording`, async ({ page }) => {
    await start(page);
    await page.route((url) => url.pathname === "/platform/enrollment/complete", route => route.fulfill({ status, json: failure("platform enrollment complete", code) }));
    await page.goto(`/app/platform/enroll#token=${token}`);
    await fill(page);
    await page.getByRole("button", { name: "完成开通" }).click();
    await expect(page.getByRole("alert")).toContainText(text);
    if (code === "invalid_enrollment_link") {
      await expect(page.locator("canvas, input[type=password]")).toHaveCount(0);
      expect(await page.locator("body").innerText()).not.toContain(secret);
    } else {
      await expect(page.locator("input[name=password]")).toHaveValue("");
      await expect(page.locator("input[name=confirm-password]")).toHaveValue("");
    }
    expect(await page.locator("body").innerText()).not.toContain(password);
    await assertNoStoredSecrets(page);
  });
}

test("platform administrators list sources and issue a full link only once", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.addInitScript(() => sessionStorage.setItem("bid.platform.session", "synthetic-session"));
  const issued = [];
  const operators = [
    { email, has_account: false, factor_source: "none", enrolled_at: null, enrolled_by: null },
    { email: "database@example.test", has_account: true, factor_source: "database", enrolled_at: "2026-10-08T18:00:00Z", enrolled_by: "issuer@example.test" },
    { email: "environment@example.test", has_account: true, factor_source: "environment", enrolled_at: null, enrolled_by: null },
    { email: "<b>escaped</b>@example.test", has_account: true, factor_source: "database", enrolled_at: "2026-10-08T18:00:00Z", enrolled_by: "<script>issuer</script>" },
  ];
  await page.route((url) => url.pathname === "/platform/operators", async route => {
    expect(route.request().headers().authorization).toBe("Bearer synthetic-session");
    await route.fulfill({ json: result("platform operator list", {}, operators) });
  });
  await page.route((url) => url.pathname.startsWith("/platform/operators/") && url.pathname.endsWith("/enrollment-links"), async route => {
    expect(route.request().headers().authorization).toBe("Bearer synthetic-session");
    expect(decodeURIComponent(new URL(route.request().url()).pathname.split("/")[3])).toBe(email);
    issued.push(route.request().postDataJSON());
    await route.fulfill({ json: result("platform operator enrollment-link", { email, url: `/app/platform/enroll#token=${token}`, expires_at: expiresAt }) });
  });
  await page.goto("/app/platform/operators");
  await expect(page.getByRole("navigation", { name: "平台导航" }).getByRole("link", { name: "平台管理员" })).toHaveClass(/active/);
  await expect(page.getByRole("row", { name: /database@example.test/ })).toContainText("网页开通");
  const environment = page.getByRole("row", { name: /environment@example.test/ });
  await expect(environment).toContainText("部署配置");
  await expect(environment.getByRole("button", { name: "生成开通链接" })).toBeDisabled();
  const escaped = page.getByRole("row", { name: /escaped/ });
  await expect(escaped).toContainText("<b>escaped</b>@example.test");
  await expect(escaped.locator("b, script")).toHaveCount(0);
  await page.getByRole("row", { name: /operator@example.test/ }).getByRole("button", { name: "生成开通链接" }).click();
  const fullLink = new URL(`/app/platform/enroll#token=${token}`, page.url()).href;
  await expect(page.getByTestId("enrollment-link")).toHaveText(fullLink);
  await page.getByRole("button", { name: "复制链接", exact: true }).click();
  await expect(page.getByRole("button", { name: "已复制", exact: true })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(fullLink);
  expect(issued).toEqual([{ email }]);
  await assertNoStoredSecrets(page);
  await page.screenshot({ path: join(output, "operators-issued-link.png"), fullPage: true });
  await page.getByRole("button", { name: "关闭链接" }).click();
  await expect(page.getByTestId("enrollment-link")).toHaveCount(0);
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.getByTestId("enrollment-link")).toHaveCount(0);
  await page.screenshot({ path: join(output, "operators-list.png"), fullPage: true });
  writeFileSync(join(output, "operators-result.json"), JSON.stringify({ passed: true, checks: ["platform-navigation", "allowlisted-status", "environment-disabled", "escaped-metadata", "authenticated-issue", "full-link-copy", "one-time-display"] }, null, 2));
});
