// Failure modes: missing navigation, non-admin writes/metadata, foreign org reads,
// stale CAS overwrites, accidental activation, last-admin lockout, HTML execution,
// link persistence, wrong invitation destinations, copied links and failed reads.
import { test, expect } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const origin = process.env.E2E_BASE_URL ?? "https://members.test";
test.use({ baseURL: origin });
const work = fileURLToPath(new URL("../../data/work/", import.meta.url)), output = resolve(process.env.E2E_OUTPUT ?? join(work, "org-members/browser"));
const id = (suffix) => `00000000-0000-4000-8000-${String(suffix).padStart(12, "0")}`;
const org = id(1), orgB = id(2), admin = id(3), colleague = id(4), pending = id(5);
const now = "2026-10-08T10:00:00Z", invitationPath = "/app/setup-password#token=synthetic.invitation.signature";
const result = (command, data = {}, items = [], ok = true) => ({ ok, command, data, items, warnings: [], cost: { llm_tokens: 0, ocr_pages: 0, usd: "0" }, duration_ms: 0 });
const failure = (code) => result("org member error", { error: { code, message: "Untrusted <script>server input</script>", exit_code: 4 } }, [], false);
const member = (user_id, email, role, fields = {}) => ({ user_id, email, role, active: true, password_set: true, revision: 1, created_by: admin, created_at: now, updated_at: now, ...fields });
const visibleDialog = (page) => page.getByRole("dialog").filter({ visible: true });

async function fixture(page, options = {}) {
  if (!process.env.E2E_STATIC_DIR) throw new Error("E2E_STATIC_DIR is required; this suite starts no services");
  if (new URL(origin).protocol !== "https:") throw new Error("Org member browser tests require an HTTPS test origin for clipboard verification");
  if (relative(work, output).startsWith("..")) throw new Error("E2E_OUTPUT must stay under data/work");
  mkdirSync(output, { recursive: true });
  const directory = resolve(process.env.E2E_STATIC_DIR);
  const state = {
    org, role: "admin", user: admin, writes: [], reads: [], unexpected: [], errors: [], conflict: false,
    members: [member(admin, "admin@example.test", "admin"), member(colleague, "colleague@example.test", "technical"), member(pending, "pending@example.test", "viewer", { password_set: false, active: false })],
    ...options,
  };
  page.on("pageerror", exc => state.errors.push(exc.message));
  page.on("console", message => { if (message.type() === "error" && !/Failed to load resource: the server responded with a status of (403|404|409|400)/.test(message.text())) state.errors.push(message.text()); });
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"], { origin });
  await page.addInitScript(({ org, role }) => { if (!sessionStorage.getItem("bid.org.session")) sessionStorage.setItem("bid.org.session", JSON.stringify({ orgId: org, orgName: "合成成员单位", email: `${role}@example.test`, session: "synthetic-session" })); }, state);
  await page.route("**/*", async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    if (url.origin !== new URL(origin).origin) { state.unexpected.push(`${method} external-origin`); return route.abort(); }
    if (method === "GET" && path.startsWith("/app/")) {
      const file = path.startsWith("/app/assets/") ? join(directory, "assets", basename(path)) : join(directory, "index.html");
      return route.fulfill({ contentType: { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" }[extname(file)] ?? "application/octet-stream", body: readFileSync(file) });
    }
    if (method === "GET" && path === "/health") return route.fulfill({ json: result("health", { org_signup_enabled: false }) });
    try {
      if (path.startsWith("/auth/")) {
        expect(request.headers().authorization).toBeUndefined();
        const body = request.postDataJSON();
        if (method === "POST" && path === "/auth/setup-password") {
          expect(body.token).toBe(invitationPath.split("#token=")[1]); expect(body.password).toBe("synthetic-member-password");
          const row = state.members.find(row => row.email === "new@example.test"); row.password_set = true;
          return route.fulfill({ json: result("auth setup-password", { password_set: true }) });
        }
        if (method === "POST" && path === "/auth/orgs") {
          expect(body).toEqual({ email: "new@example.test", password: "synthetic-member-password" });
          return route.fulfill({ json: result("auth orgs", {}, [{ org_id: org, name: "合成成员单位", active: true }]) });
        }
        if (method === "POST" && path === "/auth/login") {
          expect(body.org_id).toBe(org); state.user = state.members.find(row => row.email === body.email).user_id; state.role = "technical";
          return route.fulfill({ json: result("auth login", { session: "synthetic-session" }) });
        }
      }
      expect(request.headers().authorization).toBe("Bearer synthetic-session");
      expect(request.headers()["x-org-id"]).toBe(state.org);
      if (method === "GET") state.reads.push(path);
      if (method === "GET" && path === "/org/current") return route.fulfill({ json: result("org current", { org_id: state.org, user_id: state.user, role: state.role }) });
      if (method === "GET" && path === "/tasks") return route.fulfill({ json: result("task list") });
      if (method === "GET" && path === "/org/members") {
        if (state.listError) return route.fulfill({ status: 403, json: failure("forbidden") });
        const rows = state.org === orgB ? [member(id(20), "foreign@example.test", state.role)] : state.members;
        return route.fulfill({ json: result("org member list", {}, state.role === "admin" ? rows : rows.filter(row => row.active).map(({ user_id, email, role, active }) => ({ user_id, email, role, active }))) });
      }
      if (method === "POST" && /^\/org\/members(?:\/[^/]+\/(?:role|active|invitation))?$/.test(path)) {
        const body = request.postData() ? request.postDataJSON() : undefined;
        state.writes.push({ path, body });
        if (state.role !== "admin") return route.fulfill({ status: 403, json: failure("forbidden") });
        if (state.nextError) { const code = state.nextError; state.nextError = null; return route.fulfill({ status: code === "forbidden" ? 403 : 409, json: failure(code) }); }
        if (path === "/org/members") {
          expect(Object.keys(body).sort()).toEqual(["email", "role"]);
          if (state.members.some(row => row.email === body.email)) return route.fulfill({ status: 409, json: failure("member_exists") });
          const row = member(id(10 + state.members.length), body.email, body.role, { password_set: body.email === "existing@example.test" });
          state.members.push(row);
          return route.fulfill({ json: result("org member add", { member: row, invitation_url: row.password_set ? "/app/org/login" : invitationPath, expires_in: row.password_set ? null : 86400 }) });
        }
        const target = path.split("/")[3], kind = path.split("/")[4], row = state.members.find(row => row.user_id === target);
        if (!row) return route.fulfill({ status: 404, json: failure("not_found") });
        if (kind === "invitation") {
          expect(body).toBeUndefined();
          if (row.password_set) return route.fulfill({ status: 409, json: failure("password_already_set") });
          return route.fulfill({ json: result("org member invite", { member: row, invitation_url: invitationPath, expires_in: 86400 }) });
        }
        expect(Object.keys(body).sort()).toEqual([kind === "role" ? "role" : "active", "expected_revision"].sort());
        if (state.conflict) { state.conflict = false; row.revision++; return route.fulfill({ status: 409, json: failure("revision_conflict") }); }
        expect(body.expected_revision).toBe(row.revision);
        if (row.active && row.role === "admin" && state.members.filter(row => row.active && row.role === "admin").length === 1 && (kind === "role" && body.role !== "admin" || kind === "active" && !body.active)) return route.fulfill({ status: 409, json: failure("last_admin_required") });
        row[kind === "role" ? "role" : "active"] = body[kind === "role" ? "role" : "active"]; row.revision++;
        if (row.user_id === state.user) state.role = row.role;
        return route.fulfill({ json: result(kind === "role" ? "org member role" : "org member set-active", row) });
      }
      state.unexpected.push(`${method} ${path}`); return route.abort();
    } catch (exc) { state.unexpected.push(String(exc)); return route.abort(); }
  });
  state.verify = () => { expect(state.unexpected).toEqual([]); expect(state.errors).toEqual([]); };
  return state;
}

async function choose(page, label, name) {
  const control = page.getByRole("combobox", { name: label, exact: true });
  await page.locator(".el-select").filter({ has: control }).locator(".el-select__wrapper").click();
  await expect(control).toHaveAttribute("aria-expanded", "true");
  const popup = page.locator(`[id=${JSON.stringify(await control.getAttribute("aria-controls"))}]`);
  await popup.getByRole("option", { name, exact: true, includeHidden: true }).click();
}
async function add(page, email, role = "技术审核") {
  await page.getByRole("button", { name: "添加成员", exact: true }).click();
  await visibleDialog(page).locator("input[name=member-email]").fill(email);
  await choose(page, "新成员角色", role);
  await visibleDialog(page).getByRole("button", { name: "确认添加" }).click();
}
async function artifact(page, state, name) {
  state.verify();
  expect(await page.locator("body").innerText()).not.toContain("synthetic.invitation.signature");
  const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
  expect(storage).not.toContain("synthetic.invitation.signature"); expect(storage).not.toContain("synthetic-member-password");
  await page.screenshot({ path: join(output, `${name}.png`), fullPage: true });
  writeFileSync(join(output, `${name}.json`), JSON.stringify({ passed: true, reads: state.reads, writes: state.writes.map(({ path }) => path), unexpected: state.unexpected, errors: state.errors }, null, 2));
}

test("navigation, new member invitation, copy, password setup and assigned-role login", async ({ page }) => {
  const state = await fixture(page);
  await page.goto("/app/org/tasks"); await page.getByRole("link", { name: "成员", exact: true }).click();
  await expect(page.getByRole("heading", { name: "成员", exact: true })).toBeVisible();
  await expect(page.getByRole("row", { name: /pending@example.test/ })).toContainText("未设置");
  await add(page, "NEW@example.test");
  const link = visibleDialog(page).getByTestId("invitation-link"); await expect(link).toHaveText(origin + invitationPath);
  expect(state.writes[0].body).toEqual({ email: "new@example.test", role: "technical" });
  await visibleDialog(page).getByRole("button", { name: "复制链接" }).click();
  await expect(visibleDialog(page).getByRole("button", { name: "已复制" })).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(origin + invitationPath);
  await visibleDialog(page).getByRole("button", { name: "关闭链接" }).click();
  await artifact(page, state, "member-added");
  await page.goto(invitationPath);
  await expect(page).toHaveURL(/\/app\/setup-password$/);
  await page.locator("input[name=new-password]").fill("synthetic-member-password");
  await page.locator("input[name=confirm-password]").fill("synthetic-member-password");
  await page.getByRole("button", { name: "设置密码", exact: true }).click();
  await page.getByRole("link", { name: "登录单位" }).click();
  await page.locator("input[name=email]").fill("new@example.test"); await page.locator("input[name=password]").fill("synthetic-member-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).toHaveURL(/\/app\/org\/tasks$/); await page.getByRole("link", { name: "成员", exact: true }).click();
  await expect(page.getByRole("button", { name: "添加成员", exact: true })).toHaveCount(0);
  await expect(page.locator("thead")).not.toContainText("密码");
  await artifact(page, state, "assigned-role-signin");
});

test("existing account addition uses sign-in link and duplicate membership remains a conflict", async ({ page }) => {
  const state = await fixture(page); await page.goto("/app/org/members");
  await add(page, "existing@example.test", "商务审核");
  await expect(visibleDialog(page).getByTestId("invitation-link")).toHaveText(origin + "/app/org/login");
  await expect(visibleDialog(page).getByRole("note")).toContainText("已有密码");
  await visibleDialog(page).getByRole("button", { name: "关闭链接" }).click();
  await add(page, "existing@example.test");
  await expect(visibleDialog(page).getByRole("alert")).toContainText("已是本单位成员");
  await visibleDialog(page).getByRole("button", { name: "取消", exact: true }).click();
  expect(state.members.filter(row => row.email === "existing@example.test")).toHaveLength(1);
  await artifact(page, state, "existing-account");
});

test("CAS conflict blocks repeat writes until reload; activation requires confirmation", async ({ page }) => {
  const state = await fixture(page, { conflict: true }); await page.goto("/app/org/members");
  const row = page.getByRole("row", { name: /colleague@example.test/ });
  await choose(page, "colleague@example.test 角色", "商务审核"); await row.getByRole("button", { name: "保存角色" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "成员修订已变化" })).toBeVisible();
  await expect(row.getByRole("button", { name: "保存角色" })).toBeDisabled(); expect(state.writes).toHaveLength(1);
  await page.getByRole("button", { name: "重新读取" }).click();
  await choose(page, "colleague@example.test 角色", "商务审核"); await row.getByRole("button", { name: "保存角色" }).click();
  await expect(page.getByRole("status")).toContainText("角色已保存");
  expect(state.writes[1].body).toEqual({ role: "bidder", expected_revision: 2 });
  await row.getByRole("button", { name: "停用", exact: true }).click();
  await expect(visibleDialog(page)).toContainText("任务历史");
  await visibleDialog(page).getByRole("button", { name: "取消", exact: true }).click(); expect(state.writes).toHaveLength(2);
  await row.getByRole("button", { name: "停用", exact: true }).click(); await visibleDialog(page).getByRole("button", { name: "确认停用" }).click();
  await expect(row).toContainText("停用"); expect(state.writes[2].body).toEqual({ active: false, expected_revision: 3 });
  await row.getByRole("button", { name: "启用", exact: true }).click();
  await expect(visibleDialog(page)).toContainText("不会恢复"); await visibleDialog(page).getByRole("button", { name: "确认启用" }).click();
  expect(state.writes[3].body).toEqual({ active: true, expected_revision: 4 });
  await expect(page.getByRole("status")).toContainText("令牌不会恢复"); await artifact(page, state, "cas-and-activation");
});

test("last active admin cannot be demoted or deactivated", async ({ page }) => {
  const state = await fixture(page); await page.goto("/app/org/members");
  await expect(page.getByRole("note")).toContainText("最后一位管理员不能降级或停用");
  const row = page.locator("tbody tr").filter({ has: page.locator("td:first-child", { hasText: /^admin@example\.test$/ }) });
  await choose(page, "admin@example.test 角色", "只读成员"); await row.getByRole("button", { name: "保存角色" }).click();
  await expect(page.getByRole("alert")).toContainText("至少一位启用的管理员");
  await row.getByRole("button", { name: "停用", exact: true }).click(); await visibleDialog(page).getByRole("button", { name: "确认停用" }).click();
  await expect(visibleDialog(page).getByRole("alert")).toContainText("至少一位启用的管理员");
  expect(state.members.find(row => row.user_id === admin)).toMatchObject({ role: "admin", active: true, revision: 1 });
  await visibleDialog(page).getByRole("button", { name: "取消", exact: true }).click(); await artifact(page, state, "last-admin");
});

test("self-demotion immediately removes inactive members and management metadata", async ({ page }) => {
  const state = await fixture(page, { members: [member(admin, "admin@example.test", "admin"), member(colleague, "second-admin@example.test", "admin"), member(pending, "inactive@example.test", "viewer", { active: false, password_set: false })] });
  await page.goto("/app/org/members");
  await expect(page.getByRole("row", { name: /inactive@example.test/ })).toBeVisible();
  const row = page.locator("tbody tr").filter({ has: page.locator("td:first-child", { hasText: /^admin@example\.test$/ }) });
  await choose(page, "admin@example.test 角色", "只读成员"); await row.getByRole("button", { name: "保存角色" }).click();
  await expect(page.getByRole("status")).toContainText("角色已保存");
  await expect(page.getByRole("row", { name: /inactive@example.test/ })).toHaveCount(0);
  await expect(page.locator("thead th")).toHaveCount(2);
  await expect(page.getByRole("combobox")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /添加成员|保存角色|停用|启用|生成邀请链接/ })).toHaveCount(0);
  expect(state.writes).toHaveLength(1); expect(state.writes[0].body).toEqual({ role: "viewer", expected_revision: 1 });
  await artifact(page, state, "self-demotion");
});

test("self-deactivation with another admin signs out the org session", async ({ page }) => {
  const state = await fixture(page, { members: [member(admin, "admin@example.test", "admin"), member(colleague, "second-admin@example.test", "admin")] });
  await page.goto("/app/org/members");
  const row = page.locator("tbody tr").filter({ has: page.locator("td:first-child", { hasText: /^admin@example\.test$/ }) });
  await row.getByRole("button", { name: "停用", exact: true }).click();
  await visibleDialog(page).getByRole("button", { name: "确认停用" }).click();
  await expect(page).toHaveURL(/\/app\/org\/login$/);
  await expect(page.getByRole("heading", { name: "单位登录", exact: true })).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem("bid.org.session"))).toBeNull();
  expect(state.writes).toHaveLength(1);
  expect(state.writes[0].body).toEqual({ active: false, expected_revision: 1 });
  expect(state.members.find(item => item.user_id === admin).active).toBe(false);
  await artifact(page, state, "self-deactivation");
});

test("reissued link is ephemeral; already-set password receives Chinese failure", async ({ page }) => {
  const state = await fixture(page); await page.goto("/app/org/members");
  const row = page.getByRole("row", { name: /pending@example.test/ });
  await row.getByRole("button", { name: "生成邀请链接" }).click(); await expect(visibleDialog(page).getByTestId("invitation-link")).toBeVisible();
  await visibleDialog(page).getByRole("button", { name: "关闭链接" }).click();
  state.members.find(row => row.user_id === pending).password_set = true;
  await row.getByRole("button", { name: "生成邀请链接" }).click();
  await expect(page.getByRole("alert")).toContainText("已经设置密码"); await expect(page.getByTestId("invitation-link")).toHaveCount(0);
  await artifact(page, state, "invitation-reissue");
});

for (const role of ["bidder", "technical", "viewer"]) {
  test(`${role} sees only active member emails and roles`, async ({ page }) => {
    const state = await fixture(page, { role, user: colleague }); await page.goto("/app/org/members");
    await expect(page.getByRole("link", { name: "成员", exact: true })).toBeVisible();
    await expect(page.getByRole("row", { name: /colleague@example.test/ })).toContainText("技术审核");
    await expect(page.getByRole("row", { name: /pending@example.test/ })).toHaveCount(0);
    await expect(page.locator("thead th")).toHaveCount(2); await expect(page.getByRole("combobox")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /添加成员|保存角色|停用|启用|生成邀请链接/ })).toHaveCount(0);
    expect(state.writes).toEqual([]); await artifact(page, state, `readonly-${role}`);
  });
}

test("escaped member text, denied reads and org switch discard old member state", async ({ page }) => {
  const state = await fixture(page, { members: [member(admin, "admin@example.test", "admin"), member(colleague, "<script>unsafe</script>@example.test", "technical")] });
  await page.goto("/app/org/members"); await expect(page.getByRole("row", { name: /unsafe/ })).toContainText("<script>unsafe</script>");
  await expect(page.locator("tbody script, tbody img")).toHaveCount(0);
  state.listError = true; await page.getByRole("button", { name: "重新读取" }).click(); await expect(page.getByRole("alert")).toContainText("无权管理");
  await expect(page.getByRole("row", { name: /unsafe/ })).toHaveCount(0);
  state.listError = false; state.org = orgB;
  await page.evaluate(orgId => sessionStorage.setItem("bid.org.session", JSON.stringify({ orgId, orgName: "合成单位 B", session: "synthetic-session" })), orgB);
  await page.goto("/app/org/members"); await expect(page.getByRole("row", { name: /foreign@example.test/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /admin@example.test|unsafe/ })).toHaveCount(0);
  await artifact(page, state, "escaped-and-isolated");
});
