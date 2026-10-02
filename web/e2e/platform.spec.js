// End-to-end check of the platform console. Requires E2E_BASE_URL, E2E_EMAIL,
// E2E_PASSWORD, E2E_TOTP_SECRET and E2E_OUTPUT (a new artifact directory).
import { createHmac } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";

const env = (name) => {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
};
const output = env("E2E_OUTPUT");
mkdirSync(output, { recursive: true });

function totp(secret, offset = 0) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const char of secret.replace(/=+$/, "").toUpperCase()) bits += alphabet.indexOf(char).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g).map((byte) => parseInt(byte, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000) + offset));
  const digest = createHmac("sha1", key).update(counter).digest();
  const start = digest[digest.length - 1] & 0x0f;
  return String((digest.readUInt32BE(start) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}

test("operator manages orgs, models, usage and audit", async ({ page, browser }) => {
  const steps = [];
  const consoleErrors = [];
  page.on("console", (message) => message.type() === "error" && consoleErrors.push(message.text()));
  page.on("dialog", (dialog) => dialog.accept());
  const shot = async (name) => {
    await page.screenshot({ path: join(output, `${String(steps.length + 1).padStart(2, "0")}-${name}.png`), fullPage: true });
    steps.push(name);
  };

  const first = await page.goto("/app/platform/orgs");
  expect(first.headers()["content-security-policy"]).toContain("default-src 'self'");
  await expect(page).toHaveURL(/\/app\/platform\/login$/);

  await page.fill("input[name=email]", env("E2E_EMAIL"));
  await page.fill("input[name=password]", env("E2E_PASSWORD"));
  await page.fill("input[name=totp]", totp(env("E2E_TOTP_SECRET"), 10));
  await page.click("button[type=submit]");
  await expect(page.getByRole("alert")).toHaveText("邮箱、密码或验证码不正确");
  await shot("login-rejected");

  await page.fill("input[name=password]", env("E2E_PASSWORD"));
  await page.fill("input[name=totp]", totp(env("E2E_TOTP_SECRET")));
  await page.click("button[type=submit]");
  await expect(page).toHaveURL(/\/app\/platform\/orgs$/);
  await expect(page.getByText("计费演示单位")).toBeVisible();
  await shot("orgs");

  await page.click("text=开通单位");
  await page.fill("input[name=org-name]", "端到端测试单位");
  await page.fill("input[name=admin-email]", "e2e-admin@example.test");
  await page.click("form.panel button[type=submit]");
  const link = (await page.getByTestId("setup-link").textContent()).trim();
  expect(link).toMatch(/\/app\/setup-password#token=/);
  await expect(page.getByRole("row", { name: /端到端测试单位/ })).toContainText("e2e-admin@example.test");
  await shot("org-created");

  await page.click("nav >> text=模型");
  await page.click("text=添加模型");
  await page.fill("input[name=model-id]", "e2e-model");
  await page.fill("input[name=credential]", "missing_key");
  for (const [name, value] of [["vendor-input", "2"], ["vendor-output", "10"], ["sale-input", "3"], ["sale-output", "15"]]) {
    await page.fill(`input[name=${name}]`, value);
  }
  await page.check("input[name=default]");
  await page.click("form.panel button[type=submit]");
  const row = page.getByRole("row", { name: /e2e-model/ });
  await expect(row).toContainText("未配置");
  await expect(row).toContainText("默认");
  await row.getByRole("button", { name: "测试" }).click();
  await expect(row.getByTestId("test-result")).toContainText("未通过：provider_unavailable");
  await shot("models");

  // Register the vendor's official reasoning levels and test each one.
  await row.getByRole("button", { name: "编辑" }).click();
  await page.click("text=添加档位");
  await page.click("text=添加档位");
  await page.fill("input[name=level-name-0]", "low");
  await page.fill("textarea[name=level-options-0]", '{"thinking": {"type": "enabled"}, "reasoning_effort": "low"}');
  await page.fill("input[name=level-name-1]", "max");
  await page.fill("input[name=level-label-1]", "深度推理");
  await page.fill("textarea[name=level-options-1]", "not json");
  await page.fill("input[name=level-batch-1]", "4000");
  await page.click("form.panel button[type=submit]");
  await expect(page.getByRole("alert")).toContainText("档位 max 的请求参数必须是 JSON 对象");
  await expect(page.getByRole("alert")).toContainText("请选择一个默认档位");
  await page.fill("textarea[name=level-options-1]", '{"thinking": {"type": "enabled"}, "reasoning_effort": "max"}');
  await page.getByLabel("默认档位 2").check();
  await page.click("form.panel button[type=submit]");
  await expect(row.getByTestId("levels-summary")).toHaveText("推理强度：low / max（默认）");
  await row.getByRole("button", { name: "测试" }).click();
  await expect(row.getByTestId("level-result-low")).toContainText("low：未通过：provider_unavailable");
  await expect(row.getByTestId("level-result-max")).toContainText("max：未通过：provider_unavailable");
  await shot("model-levels");

  await page.click("nav >> text=卡密");
  await page.fill("input[name=card-count]", "2");
  await page.fill("input[name=card-face]", "30");
  await page.fill("input[name=card-note]", "端到端批次");
  await page.click("form.panel button[type=submit]");
  await expect(page.getByTestId("card-code")).toHaveCount(2);
  const codes = (await page.getByTestId("card-code").allTextContents()).map((code) => code.trim());
  const [cardDownload] = await Promise.all([page.waitForEvent("download"), page.click("text=下载 CSV")]);
  const cardsPath = join(output, "issued-cards.csv");
  await cardDownload.saveAs(cardsPath);
  const cardsCsv = readFileSync(cardsPath, "utf8");
  expect(codes.every((code) => cardsCsv.includes(code))).toBe(true);
  const voidRow = page.getByRole("row", { name: new RegExp(`…${codes[1].slice(-4)}`) });
  await voidRow.getByRole("button", { name: "作废" }).click();
  await expect(voidRow).toContainText("已作废");
  await shot("cards");

  await page.click("nav >> text=单位");
  const demo = page.getByRole("row", { name: /计费演示单位/ });
  await demo.getByRole("button", { name: "调整余额" }).click();
  await page.fill("input[name=adjust-amount]", "5");
  await page.fill("input[name=adjust-reason]", "端到端充值");
  await page.click("[data-testid=adjust-panel] button[type=submit]");
  await expect(demo).toContainText("5.00 USD");
  await shot("balance-adjusted");

  await page.click("nav >> text=用量与账单");
  const usageRow = page.getByRole("row", { name: /计费演示单位/ });
  await expect(usageRow).toContainText("平台计费");
  await expect(usageRow).toContainText("0.0162 USD");
  const [download] = await Promise.all([page.waitForEvent("download"), page.click("text=导出 CSV")]);
  const csvPath = join(output, download.suggestedFilename());
  await download.saveAs(csvPath);
  const csv = readFileSync(csvPath, "utf8");
  expect(csv.split("\n")[0]).toContain("month,org_name,org_id,billing");
  expect(csv).toContain("计费演示单位");
  await page.reload();
  await expect(page.getByRole("heading", { name: "用量与账单" })).toBeVisible();
  await shot("usage");


  await page.click("nav >> text=审计");
  for (const label of ["登录", "开通单位", "修改模型", "测试模型", "生成卡密", "作废卡密", "调整余额"]) {
    await expect(page.getByRole("cell", { name: label, exact: true }).first()).toBeVisible();
  }
  await shot("audit");

  const anonymous = await browser.newPage();
  await anonymous.goto(link);
  await expect(anonymous).toHaveURL(/\/app\/setup-password$/);
  await anonymous.fill("input[name=new-password]", "e2e-admin-password-1");
  await anonymous.fill("input[name=confirm-password]", "e2e-admin-password-1");
  await anonymous.click("button[type=submit]");
  await expect(anonymous.getByRole("status")).toContainText("密码已设置");
  await anonymous.screenshot({ path: join(output, "org-1-setup-password.png") });

  // The new org admin signs in and recharges with an issued card.
  await anonymous.getByRole("link", { name: "登录单位" }).click();
  await anonymous.fill("input[name=email]", "e2e-admin@example.test");
  await anonymous.fill("input[name=password]", "e2e-admin-password-1");
  await anonymous.click("button[type=submit]");
  await expect(anonymous).toHaveURL(/\/app\/org\/billing$/);
  await expect(anonymous.getByTestId("balance")).toHaveText("0.00 USD");
  await anonymous.fill("input[name=card-code]", codes[1]);
  await anonymous.click("form.panel button[type=submit]");
  await expect(anonymous.getByRole("alert")).toContainText("卡密无效、已使用、已作废或已过期");
  await anonymous.fill("input[name=card-code]", codes[0].toLowerCase());
  await anonymous.click("form.panel button[type=submit]");
  await expect(anonymous.getByRole("status")).toContainText("充值成功");
  await expect(anonymous.getByTestId("balance")).toHaveText("30.00 USD");
  await expect(anonymous.getByRole("row", { name: /卡密充值/ })).toBeVisible();
  await anonymous.screenshot({ path: join(output, "org-2-billing.png"), fullPage: true });
  // Opening the same link again in a fresh tab must not allow a second password change.
  const reuse = await browser.newPage();
  await reuse.goto(link);
  await reuse.fill("input[name=new-password]", "e2e-admin-password-2");
  await reuse.fill("input[name=confirm-password]", "e2e-admin-password-2");
  await reuse.click("button[type=submit]");
  await expect(reuse.getByRole("alert")).toContainText("链接已失效或已使用");
  await reuse.screenshot({ path: join(output, "org-3-setup-link-reused.png") });
  await reuse.close();

  await page.click("nav >> text=单位");
  const created = page.getByRole("row", { name: /端到端测试单位/ });
  await expect(created).toContainText("30.00 USD");
  await created.getByRole("button", { name: "停用" }).click();
  await expect(created).toContainText("已停用");
  await shot("org-disabled");
  await anonymous.reload();
  await expect(anonymous.getByRole("alert")).toHaveText("该单位已停用，请联系平台管理员");
  await anonymous.close();

  await page.click("text=退出登录");
  await expect(page).toHaveURL(/\/app\/platform\/login$/);
  await page.goto("/app/platform/orgs");
  await expect(page).toHaveURL(/\/app\/platform\/login$/);

  expect(consoleErrors.filter((text) => !text.includes("401"))).toEqual([]);
  writeFileSync(
    join(output, "result.json"),
    JSON.stringify({ passed: true, steps: [...steps, "setup-password", "org-redeem", "link-reuse-rejected", "disabled-org-blocked", "signed-out"], csv: download.suggestedFilename() }, null, 2),
  );
});
