import { test, expect } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, extname, join, resolve } from "node:path";

const output = resolve(process.env.E2E_OUTPUT ?? "../data/work/org-signup/browser");
const result = (command, data = {}, items = [], ok = true) => ({ ok, command, data, items, warnings: [], cost: { llm_tokens: 0, ocr_pages: 0, usd: "0" }, duration_ms: 0 });
const receipt = result("auth org-application submit", { submitted: true, review: "platform_manual", sign_in_after_approval: true });
const failure = (code, message = "Operation failed") => result("auth org-application submit", { error: { code, message, exit_code: 3 } }, [], false);
const password = "synthetic-signup-password";
const id = (suffix) => `00000000-0000-0000-0000-${String(suffix).padStart(12, "0")}`;

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

async function config(page, enabled = true) {
  await page.route((url) => url.pathname === "/health", (route) => route.fulfill({ json: result("health", { org_signup_enabled: enabled }) }));
}
async function fillApplication(page) {
  await page.fill("input[name=org-name]", "浏览器测试组织");
  await page.fill("input[name=contact-name]", "测试联系人");
  await page.fill("input[name=email]", "applicant@example.test");
  await page.fill("input[name=phone]", "+86 138 0000 0000");
  await page.fill("textarea[name=note]", "整理招标文件和要求。");
  await page.fill("input[name=password]", password);
  await page.fill("input[name=confirm-password]", password);
}

test("enabled sign-in links, password confirmation and uniform application receipt", async ({ page }) => {
  await config(page);
  const requests = [];
  await page.route((url) => url.pathname === "/auth/org-applications", async (route) => {
    expect(route.request().headers().authorization).toBeUndefined();
    requests.push(route.request().postDataJSON());
    await route.fulfill({ json: receipt });
  });
  await page.goto("/app/platform/login");
  await expect(page.getByRole("link", { name: "申请开通" })).toBeVisible();
  await page.goto("/app/org/login");
  await page.getByRole("link", { name: "申请开通" }).click();
  await expect(page).toHaveURL(/\/app\/apply$/);
  await expect(page.getByRole("heading", { name: "组织申请开通" })).toBeVisible();
  await fillApplication(page);
  await expect(page.locator("input[name=password]")).toHaveAttribute("type", "password");
  await page.fill("input[name=password]", "short");
  await page.getByRole("button", { name: "提交申请" }).click();
  await expect(page.getByRole("alert")).toContainText("密码须为 12 至 1,024 个字符");
  await page.fill("input[name=password]", password);
  await page.fill("input[name=confirm-password]", "different-password");
  await page.getByRole("button", { name: "提交申请" }).click();
  await expect(page.getByRole("alert")).toContainText("两次输入的密码不一致");
  expect(requests).toHaveLength(0);
  await page.fill("input[name=confirm-password]", password);
  await page.getByRole("button", { name: "提交申请" }).click();
  await expect(page.getByRole("status")).toContainText("审核通过后使用该邮箱和密码登录");
  expect(requests).toEqual([{ org_name: "浏览器测试组织", contact_name: "测试联系人", email: "applicant@example.test", phone: "+86 138 0000 0000", note: "整理招标文件和要求。", password }]);
  expect(await page.locator("body").innerText()).not.toContain(password);
  expect(await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))).not.toContain(password);
  await expect(page.locator("input[type=password]")).toHaveCount(0);
  await page.screenshot({ path: join(output, "application-receipt.png"), fullPage: true });
  writeFileSync(join(output, "application-result.json"), JSON.stringify({ passed: true, checks: ["both-links", "confirmation", "password-minimum", "public-request", "uniform-receipt", "no-password-storage"] }, null, 2));
});

test("disabled signup hides both links and the direct application form", async ({ page }) => {
  await config(page, false);
  let writes = 0;
  await page.route((url) => url.pathname === "/auth/org-applications", (route) => { writes++; return route.fulfill({ json: receipt }); });
  for (const path of ["/app/platform/login", "/app/org/login"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: path.includes("platform") ? "平台后台登录" : "单位登录" })).toBeVisible();
    await expect(page.getByRole("link", { name: "申请开通" })).toHaveCount(0);
  }
  await page.goto("/app/apply");
  await expect(page.getByRole("status")).toContainText("组织申请开通暂未开放");
  await expect(page.getByRole("button", { name: "提交申请" })).toHaveCount(0);
  expect(writes).toBe(0);
  await page.screenshot({ path: join(output, "signup-disabled.png"), fullPage: true });
});

for (const [code, status, text] of [
  ["too_many_attempts", 429, "提交次数过多"],
  ["signup_busy", 503, "申请队列已满"],
  ["auth_busy", 503, "服务繁忙"],
  ["weak_password", 400, "密码至少 12 个字符"],
  ["invalid_input", 400, "填写内容不符合要求"],
  ["signup_disabled", 404, "组织申请开通暂未开放"],
]) {
  test(`application ${code} clears passwords and does not render server input`, async ({ page }) => {
    await config(page);
    await page.route((url) => url.pathname === "/auth/org-applications", (route) => route.fulfill({ status, json: failure(code, password) }));
    await page.goto("/app/apply");
    await fillApplication(page);
    await page.getByRole("button", { name: "提交申请" }).click();
    await expect(page.getByRole(code === "signup_disabled" ? "status" : "alert")).toContainText(text);
    if (code !== "signup_disabled") {
      await expect(page.locator("input[name=password]")).toHaveValue("");
      await expect(page.locator("input[name=confirm-password]")).toHaveValue("");
    }
    expect(await page.locator("body").innerText()).not.toContain(password);
  });
}

function application(suffix, fields = {}) {
  return { id: id(suffix), status: "pending", org_name: "申请组织 " + suffix, contact_name: "联系人 " + suffix, email: `person${suffix}@example.test`, phone: null, note: null, created_at: "2026-10-07T10:00:00Z", expires_at: "2026-11-06T10:00:00Z", existing_user: false, source_submissions_24h: 1, decided_at: null, decided_by: null, decision_reason: null, org_id: null, admin_user_id: null, user_created: null, attached_existing_user: null, ...fields };
}

test("platform badge, escaped applications, corrected approval, explicit attachment and rejection", async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem("bid.platform.session", "synthetic-session"));
  let applications = [
    application(1, { org_name: '<img src=x onerror="window.signupXss=true">', contact_name: "<script>unsafe</script>", note: "<b>用途文本</b>\n第二行", source_submissions_24h: 3 }),
    application(2, { existing_user: true }),
    application(3),
    application(4, { status: "expired" }),
  ];
  const decisions = [], orgs = [];
  await page.route((url) => url.pathname === "/platform/orgs", (route) => route.fulfill({ json: result("platform org list", { pending_applications: applications.filter(row => row.status === "pending").length }, orgs) }));
  await page.route((url) => url.pathname === "/platform/usage", (route) => route.fulfill({ json: result("platform usage", { totals: { calls: 0, tokens: 0, charge: 0 }, currency: "USD" }) }));
  await page.route((url) => url.pathname.startsWith("/platform/org-applications"), async (route) => {
    expect(route.request().headers().authorization).toBe("Bearer synthetic-session");
    const url = new URL(route.request().url());
    if (route.request().method() === "GET") return route.fulfill({ json: result("platform org application list", {}, applications.filter(row => row.status === url.searchParams.get("status"))) });
    const body = route.request().postDataJSON(), mode = url.pathname.split("/").at(-1), target = url.pathname.split("/").at(-2);
    decisions.push({ id: target, mode, body });
    const row = applications.find(row => row.id === target);
    if (mode === "approve") {
      expect(body.attach_existing_user).toBe(row.existing_user);
      const decision = { application_id: target, status: "approved", org_id: id(100 + decisions.length), admin_user_id: id(200 + decisions.length), user_created: !row.existing_user, attached_existing_user: row.existing_user };
      orgs.push({ id: decision.org_id, name: body.org_name ?? row.org_name, active: true, member_count: 1, admin_emails: [row.email], balance: 0, currency: "USD" });
      applications = applications.map(item => item.id === target ? { ...item, ...decision, decided_at: "2026-10-07T11:00:00Z", decided_by: "ops@example.test" } : item);
      return route.fulfill({ json: result("platform org application approve", decision) });
    }
    applications = applications.map(item => item.id === target ? { ...item, status: "rejected", decided_at: "2026-10-07T11:00:00Z", decided_by: "ops@example.test", decision_reason: body.reason } : item);
    return route.fulfill({ json: result("platform org application reject", { application_id: target, status: "rejected", org_id: null, admin_user_id: null, user_created: null, attached_existing_user: null }) });
  });
  await page.goto("/app/platform/orgs");
  await expect(page.getByTestId("pending-applications")).toHaveText("待审核 3");
  await page.getByRole("tab", { name: /申请/ }).click();
  const first = page.getByRole("row", { name: /用途文本/ });
  await expect(first).toContainText('<img src=x onerror="window.signupXss=true">');
  await expect(first).toContainText("<script>unsafe</script>");
  await expect(first.locator("img, script, b")).toHaveCount(0);
  expect(await page.evaluate(() => window.signupXss)).toBeUndefined();
  await page.screenshot({ path: join(output, "application-queue.png"), fullPage: true });
  await first.getByRole("button", { name: "通过", exact: true }).click();
  const dialog = page.getByRole("dialog").filter({ visible: true });
  await dialog.locator("input[name=application-org-name]").fill("更正后的组织名称");
  await dialog.getByRole("button", { name: "确认通过" }).click();
  await expect(page.getByRole("status")).toContainText("已审核通过并开通单位");
  await expect(page.getByTestId("pending-applications")).toHaveText("待审核 2");
  expect(decisions[0]).toEqual({ id: id(1), mode: "approve", body: { org_name: "更正后的组织名称", attach_existing_user: false } });
  const existing = page.getByRole("row", { name: /person2@example.test/ });
  await existing.getByRole("button", { name: "通过", exact: true }).click();
  await expect(dialog.getByRole("note")).toContainText("已有密码不会改变");
  await expect(dialog.getByRole("button", { name: "确认通过" })).toBeDisabled();
  await page.screenshot({ path: join(output, "existing-account-confirmation.png"), fullPage: true });
  await dialog.locator(".el-checkbox").filter({ hasText: "挂到已有账号" }).click(); await expect(dialog.getByRole("checkbox", { name: /挂到已有账号/ })).toBeChecked();
  await dialog.getByRole("button", { name: "确认通过" }).click();
  await expect(page.getByTestId("pending-applications")).toHaveText("待审核 1");
  expect(decisions[1].body).toEqual({ org_name: null, attach_existing_user: true });
  const rejected = page.getByRole("row", { name: /person3@example.test/ });
  await rejected.getByRole("button", { name: "拒绝", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "确认拒绝" })).toBeDisabled();
  await dialog.locator("textarea[name=application-reason]").fill("未能核实申请人身份");
  await dialog.getByRole("button", { name: "确认拒绝" }).click();
  await expect(page.getByTestId("pending-applications")).toHaveText("待审核 0");
  expect(decisions[2].body).toEqual({ reason: "未能核实申请人身份" });
  // The select placeholder overlays the semantic input; open it through the visible trigger.
  const chooseStatus = async (name) => {
    const control = page.getByRole("combobox", { name: "申请状态" });
    await page.locator(".el-select").filter({ has: control }).locator(".el-select__wrapper").click();
    await expect(control).toHaveAttribute("aria-expanded", "true");
    const popup = page.locator(`[id=${JSON.stringify(await control.getAttribute("aria-controls"))}]`);
    await popup.getByRole("option", { name, exact: true, includeHidden: true }).click();
  };
  await chooseStatus("已拒绝");
  await expect(page.getByRole("row", { name: /person3@example.test/ })).toContainText("未能核实申请人身份");
  await page.screenshot({ path: join(output, "application-rejected.png"), fullPage: true });
  await chooseStatus("已过期");
  const expired = page.getByRole("row", { name: /person4@example.test/ });
  await expect(expired).toContainText("已过期");
  await expect(expired.getByRole("button")).toHaveCount(0);
  await page.getByRole("tab", { name: "单位", exact: true }).click();
  await expect(page.getByRole("row", { name: /更正后的组织名称/ })).toContainText("0.00 USD");
  writeFileSync(join(output, "review-result.json"), JSON.stringify({ passed: true, checks: ["pending-badge", "escaped-fields", "name-correction", "explicit-attach", "required-reason", "expiry-no-approval", "zero-balance"] }, null, 2));
});

test("account appearing at approval requires renewed explicit attachment", async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem("bid.platform.session", "synthetic-session"));
  const row = application(8); let writes = 0;
  await page.route((url) => url.pathname === "/platform/orgs", (route) => route.fulfill({ json: result("platform org list", { pending_applications: 1 }) }));
  await page.route((url) => url.pathname === "/platform/usage", (route) => route.fulfill({ json: result("platform usage", { totals: { calls: 0, tokens: 0, charge: 0 }, currency: "USD" }) }));
  await page.route((url) => url.pathname.startsWith("/platform/org-applications"), async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: result("platform org application list", {}, writes > 1 ? [] : [row]) });
    writes++;
    if (writes === 1) return route.fulfill({ status: 409, json: result("platform org application approve", { error: { code: "existing_user_requires_attach", message: "Conflict", exit_code: 4 } }, [], false) });
    expect(route.request().postDataJSON().attach_existing_user).toBe(true);
    return route.fulfill({ json: result("platform org application approve", { application_id: row.id, status: "approved", org_id: id(98), admin_user_id: id(99), user_created: false, attached_existing_user: true }) });
  });
  await page.goto("/app/platform/orgs");
  await page.getByRole("tab", { name: /申请/ }).click();
  await page.getByRole("button", { name: "通过", exact: true }).click();
  const dialog = page.getByRole("dialog").filter({ visible: true });
  await dialog.getByRole("button", { name: "确认通过" }).click();
  await expect(dialog.getByRole("alert")).toContainText("该邮箱已有账号");
  await expect(dialog.getByRole("button", { name: "确认通过" })).toBeDisabled();
  expect(writes).toBe(1);
  await dialog.locator(".el-checkbox").filter({ hasText: "挂到已有账号" }).click(); await expect(dialog.getByRole("checkbox", { name: /挂到已有账号/ })).toBeChecked();
  await dialog.getByRole("button", { name: "确认通过" }).click();
  await expect(page.getByRole("status")).toContainText("已审核通过并开通单位");
  expect(writes).toBe(2);
});
