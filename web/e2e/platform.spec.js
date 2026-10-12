// End-to-end check of the platform console. Requires E2E_BASE_URL, E2E_EMAIL,
// E2E_PASSWORD, E2E_TOTP_SECRET and E2E_OUTPUT (a new artifact directory).
import { createHmac } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
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

test("operator manages models and credentials", async ({ page }) => {
  const steps = [];
  const consoleErrors = [];
  page.on("console", (message) => message.type() === "error" && consoleErrors.push(message.text()));
  page.on("dialog", (dialog) => dialog.accept());
  const shot = async (name) => {
    await page.screenshot({ path: join(output, `${String(steps.length + 1).padStart(2, "0")}-${name}.png`), fullPage: true });
    steps.push(name);
  };

  const first = await page.goto("/app/platform/models");
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
  await expect(page).toHaveURL(/\/app\/platform\/models$/);
  await expect(page.getByRole("heading", { name: "模型" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "平台导航" }).getByRole("link", { name: /单位|用量|审计|平台管理员/ })).toHaveCount(0);
  await shot("models-entry");

  await page.click("nav >> text=服务凭据");
  await page.getByRole("button", { name: "添加凭据" }).click();
  await page.fill("input[name=credential-name]", "e2e_catalog");
  await page.fill("input[name=credential-key]", "synthetic-e2e-catalog-key");
  await page.check("input[name=credential-active]");
  await page.locator("form.panel button[type=submit]").click();
  await expect(page.getByRole("row", { name: /e2e_catalog/ })).toContainText("已启用");
  await expect(page.locator("input[name=credential-key]")).toHaveCount(0);
  await shot("credential-created");

  // Model content tests use a deterministic failure, never a real vendor request.
  let modelTests = 0;
  await page.route("**/platform/models/e2e-model/test", async (route) => {
    const names = modelTests++ ? ["low", "max"] : [null];
    const levels = names.map((reasoning) => ({ reasoning, passed: false, error: { code: "provider_unavailable", message: "Synthetic vendor unavailable" }, usage: null }));
    await route.fulfill({ json: { ok: true, command: "platform model test", data: { passed: false, levels, error: levels[0].error }, items: [], warnings: [], cost: { calls: 0, llm_tokens: 0, ocr_pages: 0, search_requests: 0, browser_units: 0, unresolved_calls: 0 }, duration_ms: 0 } });
  });
  await page.click("nav >> text=模型");
  await page.click("text=添加模型");
  await page.fill("input[name=model-id]", "e2e-model");
  await page.getByTestId("model-credential-select").click();
  await page.getByRole("option", { name: /e2e_catalog/ }).click();

  await page.check("input[name=default]");
  await page.click("form.panel button[type=submit]");
  const row = page.getByRole("row", { name: /e2e-model/ });
  await expect(row).toContainText("已配置");
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

  await page.click("text=退出登录");
  await expect(page).toHaveURL(/\/app\/platform\/login$/);
  await page.goto("/app/platform/models");
  await expect(page).toHaveURL(/\/app\/platform\/login$/);

  expect(consoleErrors.filter((text) => !text.includes("401"))).toEqual([]);
  writeFileSync(
    join(output, "result.json"),
    JSON.stringify({ passed: true, steps: [...steps, "signed-out"] }, null, 2),
  );
});
