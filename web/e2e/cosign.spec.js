// Failure modes specified before implementation: partial approval counted as final,
// stale round replay, auto-selected domains/evidence, admin cross-domain approval,
// archive writes, rule changes without impact preview, and disposition signatures
// confirming Evidence. Mocked browser artifacts remain in ignored data/work.
import { expect, test } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { cosignFixture, ids } from "./cosign-fixture.js";
import { uuid } from "./assessment-fixture.js";
const output = resolve(dirname(fileURLToPath(import.meta.url)), "../../data/work/team-workflow-acceptance/cosign-browser");
const route = `/app/org/tasks/${ids.task}/review?job=${ids.extract}&requirement=${ids.requirement}`;
async function review(page, disposition = false) {
  if (!disposition) for (const input of await page.getByRole("checkbox", { name: /已逐项核对材料/ }).all()) await input.check();
  await page.getByRole("checkbox", { name: "已核对警示 source_needs_review", exact: true }).check();
  await page.getByRole("textbox", { name: "操作原因 / 警示处理理由", exact: true }).fill("逐项核对原文、材料与警示。");
  await page.getByRole("radio", { name: "本次以技术职责签署", exact: true }).check();
}
async function artifact(page, state, name) {
  state.verify(); mkdirSync(output, { recursive: true });
  await page.screenshot({ path: join(output, `${name}.png`), fullPage: true });
  writeFileSync(join(output, `${name}.json`), JSON.stringify({ passed: true, scenario: name, writes: state.writes, card_state: state.card.state, rerun: "cd web && E2E_BASE_URL=http://console.test E2E_STATIC_DIR=dist npx --no-install playwright test e2e/cosign.spec.js", scope: "Mocked API, built Vue console; no backend authorization proof" }, null, 2));
}
test("explicit partial approval leaves response pending and clears review selections", async ({ page }) => {
  const state = await cosignFixture(page); await page.goto(route);
  await expect(page.getByTestId("cosign-current")).toContainText("当前轮次 1");
  await expect(page.getByRole("radio", { name: "本次以技术职责签署", exact: true })).not.toBeChecked();
  for (const input of await page.getByRole("checkbox", { name: /已逐项核对材料/ }).all()) await expect(input).not.toBeChecked();
  await expect(page.getByRole("button", { name: "签署本轮响应", exact: true })).toBeDisabled();
  await review(page); await page.getByRole("button", { name: "签署本轮响应", exact: true }).click();
  await expect(page.getByTestId("cosign-current")).toContainText("部分已签署"); await expect(page.getByTestId("cosign-current")).toContainText("待签署：商务 / 资格");
  expect(state.card.state).toBe("pending_review"); expect(state.card.confirmed_by).toBeNull(); expect(state.writes).toHaveLength(1);
  await expect(page.getByRole("radio", { name: "本次以技术职责签署", exact: true })).toHaveCount(0); await artifact(page, state, "partial");
});
test("last domain signature is final approval and shows actor time per domain", async ({ page }) => {
  const state = await cosignFixture(page, { signed: ["commercial"], signatures: [{ domain: "commercial" }] });
  await page.goto(route); await review(page); await page.getByRole("button", { name: "签署本轮响应", exact: true }).click();
  await expect(page.getByTestId("cosign-current")).toContainText("全部已签署"); await expect(page.getByTestId("cosign-current")).toContainText(uuid(4)); expect(state.card.state).toBe("confirmed"); await artifact(page, state, "complete");
});
test("stale round clears domain evidence warnings and never automatically replays", async ({ page }) => {
  const state = await cosignFixture(page, { conflict: true }); await page.goto(route); await review(page); await page.getByRole("button", { name: "签署本轮响应", exact: true }).click();
  await expect(page.getByTestId("cosign-error")).toContainText("未重试");
  await expect(page.getByRole("radio", { name: "本次以技术职责签署", exact: true })).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: "已核对警示 source_needs_review", exact: true })).not.toBeChecked();
  await expect(page.getByRole("button", { name: "签署本轮响应", exact: true })).toBeDisabled(); expect(state.writes).toHaveLength(1); await artifact(page, state, "conflict");
});
for (const options of [{ role: "admin" }, { role: "viewer" }, { archived: true }]) test(`human domain and archive controls ${JSON.stringify(options)}`, async ({ page }) => {
  const state = await cosignFixture(page, options); await page.goto(route); await expect(page.getByTestId("cosign-current")).toBeVisible();
  await expect(page.getByRole("button", { name: "签署本轮响应", exact: true })).toHaveCount(0); expect(state.writes).toHaveLength(0); await artifact(page, state, `readonly-${options.role ?? "archived"}`);
});
test("disposition round needs reason and all domains and never reviews Evidence", async ({ page }) => {
  const state = await cosignFixture(page, { draft: true, roundRevision: 0 }); await page.goto(route);
  await page.getByRole("textbox", { name: "处置会签原因", exact: true }).fill("该项只要求履约遵守。");
  await page.getByRole("button", { name: "发起仅需遵守会签", exact: true }).click(); expect(state.card.disposition).toBe("respond");
  await expect(page.getByTestId("cosign-current")).toContainText("处置会签");
  await expect(page.getByTestId("cosign-current")).toContainText("共同处置原因：该项只要求履约遵守。");
  await review(page, true); await page.getByRole("button", { name: "签署本轮处置", exact: true }).click(); expect(state.card.disposition).toBe("respond");
  expect(state.writes[1].body.reviewed_evidence_ids).toBeUndefined(); await artifact(page, state, "disposition");
});
test("starred review rule previews affected requirements before saving", async ({ page }) => {
  const state = await cosignFixture(page, { role: "admin" }); await page.goto(`/app/org/tasks/${ids.task}/members`);
  await page.getByRole("checkbox", { name: "★ 条款要求商务与技术会签", exact: true }).check();
  await page.getByRole("textbox", { name: "审阅规则修改原因", exact: true }).fill("加强关键条款审阅。");
  await expect(page.getByRole("button", { name: "保存审阅规则", exact: true })).toBeDisabled(); await page.getByRole("button", { name: "预览规则影响", exact: true }).click();
  await expect(page.getByTestId("rule-impact")).toContainText("1 项"); await page.getByRole("button", { name: "保存审阅规则", exact: true }).click(); expect(state.previews).toBe(1); expect(state.rule).toBe(true); await artifact(page, state, "rule");
});
test("mobile keyboard sign-off never focuses live headings and has no horizontal overflow", async ({ page }) => {
  const state = await cosignFixture(page); await page.setViewportSize({ width: 390, height: 844 }); await page.goto(route); await review(page);
  const button = page.getByRole("button", { name: "签署本轮响应", exact: true }); await button.focus(); await page.keyboard.press("Enter");
  await expect(page.getByTestId("cosign-current")).toContainText("部分已签署"); expect(await page.evaluate(() => document.activeElement?.tagName === "H3")).toBe(false); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true); await artifact(page, state, "mobile-keyboard");
});

test("explicit requirement policy cannot weaken starred task rule and retires old approvals", async ({ page }) => {
  const state = await cosignFixture(page, { role: "bidder", rule: true }); await page.goto(route);
  await page.getByText("本条要求的会签策略", { exact: true }).click();
  await page.getByRole("checkbox", { name: "本条要求商务与技术会签", exact: true }).uncheck();
  await page.getByRole("textbox", { name: "会签策略修改原因", exact: true }).fill("改用任务规则确定职责。");
  await page.getByRole("button", { name: "保存本条会签策略", exact: true }).click();
  await expect(page.getByText("★ 条款匹配任务规则，关闭本条显式会签要求仍须商务与技术分别签署。", { exact: true })).toBeVisible();
  await expect(page.getByTestId("cosign-current")).toContainText("会签已失效"); expect(state.required).toBe(false); expect(state.rule).toBe(true); await artifact(page, state, "policy-rule-floor");
});
