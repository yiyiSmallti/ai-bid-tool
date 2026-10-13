import { randomUUID } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { openConsole } from "./access-key.js";

process.env.PLAYWRIGHT_NO_COPY_PROMPT = "1";
test.use({ trace: "off", screenshot: "off", video: "off" });

const env = (name) => {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
};
const output = env("E2E_OUTPUT");
mkdirSync(output, { recursive: true });

test("service credential configuration and sign-out", async ({ page }) => {
  const credentialName = `e2e_${randomUUID().replaceAll("-", "")}`;
  const steps = [];
  const consoleErrors = [];
  page.on("console", (message) => message.type() === "error" && consoleErrors.push(message.text()));
  page.on("dialog", (dialog) => dialog.accept());
  const recordStep = (name) => steps.push(name);

  await openConsole(page, "/platform/credentials");
  await expect(page.getByRole("heading", { name: "服务凭据", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "添加凭据" }).click();
  await page.fill("input[name=credential-name]", credentialName);
  await page.fill("input[name=credential-key]", "synthetic-e2e-service-key");
  await page.check("input[name=credential-active]");
  await page.locator("form.panel button[type=submit]").click();
  const row = page.getByRole("row").filter({ has: page.getByText(credentialName, { exact: true }) });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("已启用");
  await expect(page.locator("input[name=credential-key]")).toHaveCount(0);
  recordStep("credential-created");

  await row.getByRole("button", { name: "移除", exact: true }).click();
  await page.locator("form.panel button[type=submit]").click();
  await expect(row).toContainText("已移除");
  await expect(row.getByRole("button", { name: "替换密钥", exact: true })).toHaveCount(0);
  recordStep("credential-removed");

  await page.getByRole("button", { name: "退出", exact: true }).click();
  await expect(page).toHaveURL(/\/app\/login$/);
  await page.goto("/app/platform/credentials");
  await expect(page).toHaveURL(/\/app\/login$/);

  expect(consoleErrors.filter((text) => !text.includes("401"))).toEqual([]);
  writeFileSync(
    join(output, "result.json"),
    JSON.stringify({ passed: true, steps: [...steps, "signed-out"] }, null, 2),
  );
});

test("console settings pages are reachable under one access-key session", async ({ page }) => {
  await openConsole(page, "/platform/credentials");
  await expect(page.getByRole("heading", { name: "服务凭据", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Clef 初筛配置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Clef 初筛配置", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "信任根证书", exact: true }).click();
  await expect(page.getByRole("heading", { name: "信任根证书", exact: true })).toBeVisible();
});
