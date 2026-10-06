// Failure modes are specified before implementation: false success for partial/stale
// reports, full-report preloads, org text surviving resets, fabricated score totals,
// preview writes, unbound paid requests, duplicate network retry, wrong-domain human
// decisions, stale CAS, and replacement built from incomplete/mixed snapshots.
import { expect, test } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { fixture, ids, seed, hash, uuid, zeroCost } from "./assessment-fixture.js";
import { rubricFixture, scoreFixture } from "./assessment-rubric-fixture.js";

const workRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../data/work");
const output = resolve(process.env.E2E_OUTPUT ?? join(workRoot, "console-assessments-validation/browser"));
if (relative(workRoot, output).startsWith("..")) throw new Error("E2E_OUTPUT must stay under data/work");
const workspace = `/app/org/tasks/${ids.task}/checks?job=${ids.extract}&draft=${ids.draft}`;
const report = `/app/org/tasks/${ids.task}/checks/${ids.report}`;
const observed = [];
async function checkControl(control) {
  // Element Plus overlays the native input with its visible indicator. Click the
  // associated label, while retaining the semantic input for enabled/state checks.
  await expect(control).toHaveCount(1);
  await expect(control).toBeEnabled();
  const label = control.locator("xpath=ancestor::label[1]");
  await expect(label).toBeVisible();
  if (!await control.isChecked()) await label.click();
  await expect(control).toBeChecked();
}

async function choose(target, label, option) {
  const page = typeof target.page === "function" ? target.page() : target;
  const combobox = target.getByRole("combobox", { name: label, exact: true });
  await expect(combobox).toHaveCount(1);
  await expect(combobox).toBeEnabled();
  // Both the input and its listbox have the same accessible label. Locate the
  // visible wrapper by its combobox, then follow aria-controls to the teleported popup.
  const select = target.locator(".el-select").filter({ has: page.getByRole("combobox", { name: label, exact: true }) });
  const trigger = select.locator(".el-select__wrapper");
  await expect(trigger).toBeVisible();
  if (await combobox.getAttribute("aria-expanded") !== "true") await trigger.click();
  await expect(combobox).toHaveAttribute("aria-expanded", "true");
  await expect(combobox).toHaveAttribute("aria-controls", /\S+/);
  const listbox = page.locator(`[id=${JSON.stringify(await combobox.getAttribute("aria-controls"))}]`);
  await expect(listbox).toHaveAttribute("role", "listbox");
  await expect(listbox).toBeVisible();
  // Single-select popups hide immediately after choosing; their selected option
  // stays mounted, so includeHidden permits the post-click state assertion only.
  const choice = listbox.getByRole("option", { name: option, exact: true, includeHidden: true });
  await expect(choice).toBeVisible();
  await expect(choice).toBeEnabled();
  if (await choice.getAttribute("aria-selected") !== "true") await choice.click();
  await expect(choice).toHaveAttribute("aria-selected", "true");
  if (await combobox.getAttribute("aria-expanded") === "true") await combobox.press("Escape");
  await expect(combobox).toHaveAttribute("aria-expanded", "false");
}

async function artifact(page, state, name) {
  state.verify();
  mkdirSync(output, { recursive: true });
  await page.screenshot({ path: join(output, `${name}.png`), fullPage: true });
  observed.push({ name, passed: true, requests: state.requests, writes: state.writes, previews: state.previews, submissions: state.submissions, polls: state.polls });
  writeFileSync(join(output, "result.json"), JSON.stringify({ fixture: seed, assertions: observed, rerun: { command: "cd web && npx --no-install playwright test e2e/console-assessments.spec.js", environment_names: ["E2E_BASE_URL", "E2E_STATIC_DIR", "E2E_OUTPUT"] }, scope: "stateful mocked built-app; database/API authorization tested separately" }, null, 2));
}

test.describe("rules check acceptance", () => {
  test("malformed Result 4 cost cannot become a successful assessment read", async ({ page }) => {
    const state = await fixture(page);
    await page.route("**/v4/tasks/*/assessment-inputs?*", route => route.fulfill({ json: {
      ok: true, command: "assessment inputs", data: {}, items: [], warnings: [],
      cost: { ...zeroCost(), basis: null }, duration_ms: 0,
    } }));
    await page.goto(workspace);
    await expect(page.getByRole("alert")).toContainText("invalid_response");
    await expect(page.getByRole("button", { name: "提交检查", exact: true })).toHaveCount(0);
    expect(state.writes).toBe(0);
    await artifact(page, state, "rules-malformed-result-cost");
  });

  test("rules task entry carries the selected successful extraction to both assessment workspaces", async ({ page }) => {
    const state = await fixture(page, { role: "viewer" });
    await page.goto(`/app/org/tasks/${ids.task}`);
    const checks = page.getByRole("link", { name: "检查风险", exact: true });
    await expect(checks).toHaveAttribute("href", `/app/org/tasks/${ids.task}/checks?job=${ids.extract}`);
    await expect(page.getByRole("link", { name: "评分预估", exact: true })).toHaveAttribute("href", `/app/org/tasks/${ids.task}/scores?job=${ids.extract}`);
    await checks.focus(); await page.keyboard.press("Enter");
    await expect(page).toHaveURL(new RegExp(`/checks\\?job=${ids.extract}$`));
    await expect(page.getByRole("button", { name: "预览检查", exact: true })).toBeDisabled();
    expect(state.writes).toBe(0);
    await artifact(page, state, "rules-task-entry-keyboard");
  });

  test("rules preview is read-only; exact hash submission polls and refresh recovers", async ({ page }) => {
    const state = await fixture(page, { published: false });
    await page.goto(workspace);
    await expect(page.getByRole("heading", { name: "检查风险", exact: true })).toBeVisible();
    await expect(page.getByText(/仅评估已保存初稿/).first()).toBeVisible();
    await expect(page.getByText("尚未运行检查", { exact: true })).toBeVisible();
    await page.getByLabel("评估日期", { exact: true }).fill("2026-10-05");
    await page.getByRole("button", { name: "预览检查", exact: true }).click();
    await expect(page.getByText("本次检查范围", { exact: true })).toBeVisible();
    await expect(page.getByText(/不调用模型，费用为 0/)).toBeVisible();
    expect(state.previews).toBe(1); expect(state.writes).toBe(0); expect(state.submissions).toBe(0);
    await page.getByRole("button", { name: "提交检查", exact: true }).click();
    await expect(page.getByTestId("job-status")).toContainText(/已完成|已成功/, { timeout: 15000 });
    expect(state.submissions).toBe(1); expect(state.writes).toBe(1); expect(state.polls).toBeGreaterThanOrEqual(2);
    const paidBody = state.requests.find((row) => row.method === "POST" && row.body?.dry_run === false).body;
    expect(paidBody.expected_input_hash).toBe(hash); expect(paidBody.mode).toBe("rules");
    expect(paidBody.reasoning == null).toBe(true); expect(paidBody.max_charge == null).toBe(true);
    await page.reload();
    await expect(page.getByRole("link", { name: /查看报告/ }).first()).toBeVisible();
    expect(state.submissions).toBe(1);
    expect(state.requests.filter((row) => row.path === `/checks/${ids.report}` && row.query.part !== "summary")).toHaveLength(0);
    await artifact(page, state, "rules-workspace-recovery");
  });

  test("rules report bounded pages, citation windows, text safety and unknown dates", async ({ page }) => {
    const state = await fixture(page);
    await page.goto(report);
    await expect(page.getByText("合成风险 1：响应存在负偏离。", { exact: true })).toBeVisible();
    await expect(page.getByText(/废标风险/).first()).toBeVisible();
    await expect(page.getByText(/扣分风险/).first()).toBeVisible();
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    await expect(page.getByText("合成风险 2：响应存在负偏离。", { exact: true })).toBeVisible();
    expect(state.requests.some((row) => row.query.cursor === "second-page")).toBe(true);
    await page.getByRole("button", { name: "查看引用原文", exact: true }).first().click();
    await expect(page.getByRole("dialog", { name: "引用原文" })).toContainText("第二章 / 技术要求 / 第 17 段");
    await page.keyboard.press("Escape");
    await page.reload();
    await page.getByRole("button", { name: "查看引用原文", exact: true }).first().click();
    const dialog = page.getByRole("dialog", { name: "引用原文" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/还有原文未展开/)).toBeVisible();
    await dialog.getByRole("button", { name: "继续展开原文", exact: true }).click();
    await expect(dialog).toContainText("onerror=window.__unsafe=1");
    expect(await page.evaluate(() => window.__unsafe)).toBeUndefined();
    expect(await dialog.locator("img").count()).toBe(0);
    await page.keyboard.press("Escape");
    await page.getByRole("tab", { name: "覆盖情况", exact: true }).click();
    await expect(page.getByText(/未请求语义检查/).first()).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: /匹配 1200.*本页 50 条/ })).toBeVisible();
    await expect(page.getByRole("table").locator("tbody tr")).toHaveCount(50);
    await page.getByRole("tab", { name: "证照日期", exact: true }).click();
    await expect(page.getByRole("table").getByText(/未知.*评估日期/)).toBeVisible();
    expect(state.requests.filter((row) => row.path === `/checks/${ids.report}`).every((row) => row.query.view === "console")).toBe(true);
    expect(state.requests.some((row) => /preview|download-link/.test(row.path))).toBe(false);
    await page.setViewportSize({ width: 720, height: 900 });
    await page.evaluate(() => { document.documentElement.style.zoom = "2"; });
    await expect(page.getByRole("tab", { name: "证照日期", exact: true })).toBeVisible();
    await artifact(page, state, "rules-report-bounded-citations");
  });

  test("rules responsible human requires reason, dismiss history reopen and CAS", async ({ page }) => {
    const state = await fixture(page);
    await page.goto(report);
    const dismiss = page.getByRole("button", { name: "忽略此风险", exact: true });
    await dismiss.click();
    const dialog = page.getByRole("dialog", { name: "风险处理" });
    await dialog.getByRole("button", { name: "提交处理", exact: true }).click();
    await expect(dialog.getByRole("alert")).toContainText("请填写处理理由");
    expect(state.writes).toBe(0);
    await dialog.getByLabel("处理理由", { exact: true }).fill("已复核，当前负偏离为录入错误。");
    await dialog.getByRole("button", { name: "提交处理", exact: true }).click();
    await expect(page.getByRole("button", { name: "重新打开", exact: true })).toBeVisible();
    expect(state.decisions).toHaveLength(1); expect(state.findings[0].revision).toBe(2);
    await page.getByRole("button", { name: "查看处理记录", exact: true }).first().click();
    await expect(page.getByText(/修订 2.*已复核，当前负偏离为录入错误。/)).toBeVisible();
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "重新打开", exact: true }).click();
    await dialog.getByLabel("处理理由", { exact: true }).fill("重新复核后仍需要补充真实证据。");
    await dialog.getByRole("button", { name: "提交处理", exact: true }).click();
    await expect(dismiss).toBeVisible();
    expect(state.decisions.map((row) => row.action)).toEqual(["dismiss", "reopen"]);
    state.conflict = true;
    await dismiss.click();
    await dialog.getByLabel("处理理由", { exact: true }).fill("冲突时仍保留本地理由。");
    await dialog.getByRole("button", { name: "提交处理", exact: true }).click();
    await expect(page.getByRole("alert").last()).toContainText(/修订|更新/);
    expect(state.writes).toBe(2); expect(state.decisions).toHaveLength(2);
    await artifact(page, state, "rules-decision-cas");
  });

  for (const role of ["admin", "viewer", "bidder"]) {
    test(`rules stored technical domain denies ${role} human decision`, async ({ page }) => {
      const state = await fixture(page, { role });
      await page.goto(report);
      await expect(page.getByText("合成风险 1：响应存在负偏离。", { exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "忽略此风险", exact: true })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "重新打开", exact: true })).toHaveCount(0);
      expect(state.writes).toBe(0);
      if (role === "viewer") {
        await page.goto(workspace);
        await expect(page.getByRole("button", { name: "预览检查", exact: true })).toBeDisabled();
        await expect(page.getByRole("button", { name: "提交检查", exact: true })).toHaveCount(0);
      }
      await artifact(page, state, `rules-role-${role}`);
    });
  }

  test("rules no draft, partial empty report, stale read and explicit byte failure", async ({ page }) => {
    const state = await fixture(page, { noDraft: true, published: false });
    await page.goto(`/app/org/tasks/${ids.task}/checks?job=${ids.extract}`);
    await expect(page.getByText(/尚未生成初稿/)).toBeVisible();
    await expect(page.getByRole("button", { name: "预览检查", exact: true })).toBeDisabled();
    state.noDraft = false; state.partial = true; state.findings = [];
    await page.goto(report);
    await expect(page.getByText(/部分内容未完成检查/)).toBeVisible();
    await expect(page.getByText("全部通过", { exact: true })).toHaveCount(0);
    state.stale = true;
    await page.reload();
    await expect(page.getByRole("alert").first()).toContainText(/初稿|过期|变化/);
    await expect(page.getByRole("button", { name: "忽略此风险", exact: true })).toHaveCount(0);
    state.error = "assessment_entry_too_large";
    await page.reload();
    await expect(page.getByRole("alert").first()).toContainText(/大小|过大|超过|assessment_entry_too_large/);
    expect(state.writes).toBe(0);
    await artifact(page, state, "rules-partial-stale-size-limit");
  });

  test("rules date edit invalidates preview and delayed source is discarded on org exit", async ({ page }) => {
    const state = await fixture(page);
    await page.goto(workspace);
    // A fixed starting date makes the later edit a real change on every run day.
    await page.getByLabel("评估日期", { exact: true }).fill("2026-10-05");
    await page.getByRole("button", { name: "预览检查", exact: true }).click();
    await expect(page.getByRole("button", { name: "提交检查", exact: true })).toBeEnabled();
    const preview = state.requests.find((row) => row.method === "POST" && row.body?.dry_run === true).body;
    expect(preview.assessment_date).toBe("2026-10-05");
    await page.getByLabel("评估日期", { exact: true }).fill("2026-10-06");
    await expect(page.getByLabel("评估日期", { exact: true })).toHaveValue("2026-10-06");
    await expect(page.getByRole("button", { name: "提交检查", exact: true })).toHaveCount(0);
    expect(state.submissions).toBe(0);
    let release;
    state.delayedCitation = new Promise((resolve) => { release = resolve; });
    await page.goto(report);
    await page.getByRole("button", { name: "查看引用原文", exact: true }).first().click();
    await expect.poll(() => state.requests.filter((row) => row.path.endsWith("/assessment-citation")).length).toBe(1);
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "退出 / 切换单位", exact: true }).click();
    await expect(page).toHaveURL(/\/app\/org\/login$/);
    release();
    await expect(page.getByText(/必须提供真实材料/)).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem("bid.org.session"))).toBeNull();
    const saved = await page.evaluate(() => JSON.stringify({ ...sessionStorage }));
    expect(saved).not.toContain("必须提供真实材料");
    expect(saved).not.toContain("处理理由");
    await artifact(page, state, "rules-reset-aborts-source");
  });

  test("rules filter reset drops cursor and distinguishes filtered empty from full-report counts", async ({ page }) => {
    const state = await fixture(page);
    await page.goto(report);
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    await choose(page, "处理状态", "已忽略");
    await expect(page.getByRole("status").filter({ hasText: /匹配 0.*全部 3/ })).toBeVisible();
    await expect(page.getByText("当前筛选下没有记录", { exact: true })).toBeVisible();
    const selected = state.requests.filter(row => row.query.status === "dismissed");
    expect(selected).toHaveLength(1); expect(selected[0].query.cursor).toBeUndefined();
    await page.getByRole("button", { name: "重置筛选", exact: true }).click();
    await expect(page.getByText("合成风险 1：响应存在负偏离。", { exact: true })).toBeVisible();
    expect(state.writes).toBe(0);
    await artifact(page, state, "rules-filter-cursor-reset");
  });

  test("rules missing extraction has no inferred selection and foreign-org report stays inaccessible", async ({ page }) => {
    const state = await fixture(page, { org: ids.orgB, role: "viewer" });
    await page.goto(`/app/org/tasks/${ids.task}/checks`);
    await expect(page.getByRole("alert").first()).toContainText("请先在任务中明确选择成功的提取结果");
    expect(state.requests.some(row => row.path.includes("assessment-inputs"))).toBe(false);
    await page.goto(report);
    await expect(page.getByRole("alert").first()).toContainText(/不存在|无权|不可访问/);
    await expect(page.getByText("机器发现风险（含已忽略）", { exact: true })).toHaveCount(0);
    await expect(page.getByText("合成风险 1：响应存在负偏离。", { exact: true })).toHaveCount(0);
    expect(state.writes).toBe(0);
    await artifact(page, state, "rules-foreign-org-no-extraction");
  });

  test("rules running job cancellation is explicit and terminal null error remains readable", async ({ page }) => {
    const state = await fixture(page, { holdJob: true });
    await page.goto(workspace);
    await page.getByRole("button", { name: "预览检查", exact: true }).click();
    await page.getByRole("button", { name: "提交检查", exact: true }).click();
    await expect(page.getByTestId("job-status")).toContainText("运行中");
    await page.getByRole("button", { name: "取消作业", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "取消作业" })).toContainText("已获准发出的调用仍可能产生费用");
    expect(state.cancelled).toBeUndefined();
    await page.getByRole("button", { name: "确认取消", exact: true }).click();
    await expect(page.getByTestId("job-status")).toContainText("已取消");
    await expect(page.getByRole("button", { name: "取消作业", exact: true })).toHaveCount(0);
    expect(state.requests.filter(row => row.path.endsWith("/cancel"))).toHaveLength(1);
    expect(state.submissions).toBe(1);
    await artifact(page, state, "rules-explicit-cancel");
  });
});

test.describe("combined check acceptance", () => {
  test("combined partial coverage retains no-risk citations and unassessed reasons", async ({ page }) => {
    const state = await fixture(page, { mode: "combined", partial: true });
    await page.goto(report);
    await expect(page.getByText("部分内容未完成检查", { exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "覆盖情况", exact: true }).click();
    await expect(page.getByText(/在已评估范围未发现风险/).first()).toBeVisible();
    const first = page.getByRole("table").locator("tbody tr").first();
    await expect(first.getByRole("button", { name: "查看引用原文", exact: true })).toHaveCount(3);
    await first.getByRole("button", { name: "查看引用原文", exact: true }).nth(2).click();
    await expect(page.getByRole("dialog", { name: "引用原文" })).toContainText(`响应修订 ${ids.revision}`);
    expect(state.writes).toBe(0);
    await artifact(page, state, "combined-partial-coverage-citations");
  });

  test("combined explicit cap and consent bind the preview; date and cap changes revoke consent", async ({ page }) => {
    const state = await fixture(page, { published: false });
    await page.goto(workspace);
    await checkControl(page.getByRole("radio", { name: "规则与语义检查", exact: true }));
    await page.getByLabel("评估日期", { exact: true }).fill("2026-10-05");
    await page.getByLabel("本次作业平台扣费上限", { exact: true }).fill("1");
    await page.getByRole("button", { name: "预览检查", exact: true }).click();
    const consent = page.getByRole("checkbox", { name: "我已核对外发范围与费用上限", exact: true });
    await expect(consent).not.toBeChecked();
    await expect(page.getByRole("button", { name: "提交检查", exact: true })).toBeDisabled();
    await checkControl(consent);
    await expect(page.getByRole("button", { name: "提交检查", exact: true })).toBeEnabled();
    await page.getByLabel("本次作业平台扣费上限", { exact: true }).fill("2");
    await expect(page.getByRole("button", { name: "提交检查", exact: true })).toHaveCount(0);
    await page.getByRole("button", { name: "预览检查", exact: true }).click();
    await expect(consent).not.toBeChecked();
    await checkControl(consent);
    await page.getByRole("button", { name: "提交检查", exact: true }).click();
    await expect(page.getByTestId("job-status")).toContainText(/已完成|已成功/, { timeout: 15000 });
    const submitted = state.requests.find((row) => row.method === "POST" && row.body?.dry_run === false).body;
    expect(submitted).toMatchObject({ mode: "combined", expected_input_hash: hash, assessment_date: "2026-10-05", max_charge: "2" });
    expect(state.previews).toBe(2); expect(state.submissions).toBe(1); expect(state.writes).toBe(1);
    await artifact(page, state, "combined-cap-consent");
  });

  for (const blocker of ["redaction_required", "insufficient_balance", "task_budget_exceeded", "task_budget_unpriced", "task_budget_currency_review_required"]) {
    test(`combined ${blocker} remains a successful but blocked preview`, async ({ page }) => {
      const state = await fixture(page, { blocker, unknownCost: blocker === "task_budget_unpriced" });
      await page.goto(workspace);
      await checkControl(page.getByRole("radio", { name: "规则与语义检查", exact: true }));
      await page.getByLabel("本次作业平台扣费上限", { exact: true }).fill("1");
      await page.getByRole("button", { name: "预览检查", exact: true }).click();
      await expect(page.getByText("本次检查范围", { exact: true })).toBeVisible();
      const text = { redaction_required: /尚未开启外发遮挡/, insufficient_balance: /余额不足/, task_budget_exceeded: /任务预算不足/, task_budget_unpriced: /未定价/, task_budget_currency_review_required: /币种/ }[blocker];
      await expect(page.getByRole("alert").filter({ hasText: text }).first()).toBeVisible();
      await checkControl(page.getByRole("checkbox", { name: "我已核对外发范围与费用上限", exact: true }));
      await expect(page.getByRole("button", { name: "提交检查", exact: true })).toBeDisabled();
      expect(state.previews).toBe(1); expect(state.submissions).toBe(0); expect(state.writes).toBe(0);
      await artifact(page, state, `combined-blocker-${blocker}`);
    });
  }

  test("combined queue receipt has explicit idempotent recovery; network failure never auto-submits", async ({ page }) => {
    const state = await fixture(page, { queueError: true });
    await page.goto(workspace);
    await checkControl(page.getByRole("radio", { name: "规则与语义检查", exact: true }));
    await page.getByLabel("本次作业平台扣费上限", { exact: true }).fill("1");
    await page.getByRole("button", { name: "预览检查", exact: true }).click();
    await checkControl(page.getByRole("checkbox", { name: "我已核对外发范围与费用上限", exact: true }));
    await page.getByRole("button", { name: "提交检查", exact: true }).click();
    await expect(page.getByRole("alert").first()).toContainText("任务已保存，等待重新调度");
    expect(state.submissions).toBe(1);
    const original = state.requests.find((row) => row.method === "POST" && row.body?.dry_run === false).body;
    state.queueError = false;
    await page.getByRole("button", { name: "重新调度已保存作业", exact: true }).click();
    await expect(page.getByText("已复用相同输入的结果", { exact: true })).toBeVisible();
    const writes = state.requests.filter((row) => row.method === "POST" && row.body?.dry_run === false);
    expect(writes).toHaveLength(2); expect(writes[1].body).toEqual(original); expect(state.writes).toBe(1);
    await page.goto(workspace);
    state.networkError = true;
    await checkControl(page.getByRole("radio", { name: "规则与语义检查", exact: true }));
    await page.getByLabel("本次作业平台扣费上限", { exact: true }).fill("1");
    await page.getByRole("button", { name: "预览检查", exact: true }).click();
    await checkControl(page.getByRole("checkbox", { name: "我已核对外发范围与费用上限", exact: true }));
    await page.getByRole("button", { name: "提交检查", exact: true }).click();
    await expect(page.getByRole("alert").first()).toBeVisible();
    await expect(page.getByRole("checkbox", { name: "我已核对外发范围与费用上限", exact: true })).not.toBeChecked();
    expect(state.submissions).toBe(3);
    await artifact(page, state, "combined-explicit-recovery");
  });
});

test.describe("rubric acceptance", () => {
  const rubricWorkspace = `/app/org/tasks/${ids.task}/score-rubrics?job=${ids.extract}`;
  const rubricReview = `/app/org/tasks/${ids.task}/score-rubrics/${ids.rubric}`;
  const strategies = [
    { name: "legacy", progress: [null], labels: ["正在生成评分规则"] },
    { name: "single-pass", progress: [{ scheme: "single_pass", strategy_version: "synthetic-v1", stage: "whole_table", completed_batches: null, total_batches: null, sections_sha256: null }], labels: ["正在分析整张评分表"] },
    { name: "two-stage", progress: [
      { scheme: "two_stage", strategy_version: "synthetic-v2", stage: "sections", completed_batches: null, total_batches: null, sections_sha256: null },
      { scheme: "two_stage", strategy_version: "synthetic-v2", stage: "items", completed_batches: 1, total_batches: 3, sections_sha256: hash },
      { scheme: "two_stage", strategy_version: "synthetic-v2", stage: "validation", completed_batches: null, total_batches: null, sections_sha256: hash },
      { scheme: "two_stage", strategy_version: "synthetic-v2", stage: "publication", completed_batches: null, total_batches: null, sections_sha256: hash },
    ], labels: ["第 1 步：确定分节和总分规则", "第 2 步：生成评分条目", "正在核对引用与完整性", "正在保存待审核规则"] },
  ];
  for (const strategy of strategies) {
    test(`rubric ${strategy.name} generation displays only observed progress`, async ({ page }) => {
      const state = await rubricFixture(page, { progress: strategy.progress, progressStep: 0, partial: strategy.name === "two-stage" });
      await page.goto(rubricWorkspace);
      await expect(page.getByRole("heading", { name: "评分规则", exact: true })).toBeVisible();
      await page.getByLabel("本次作业平台扣费上限", { exact: true }).fill("1");
      await page.getByRole("button", { name: "预览生成评分规则", exact: true }).click();
      expect(state.writes).toBe(0); expect(state.submissions).toBe(0);
      await expect(page.getByRole("button", { name: "提交生成评分规则", exact: true })).toBeDisabled();
      await checkControl(page.getByRole("checkbox", { name: "我已核对外发范围与费用上限", exact: true }));
      await page.getByRole("button", { name: "提交生成评分规则", exact: true }).click();
      for (let index = 0; index < strategy.labels.length; index++) {
        const progress = page.getByRole("progressbar", { name: strategy.labels[index], exact: true });
        await expect(progress).toBeVisible();
        if (strategy.progress[index]?.stage === "items") { await expect(progress).toHaveAttribute("aria-valuenow", "1"); await expect(progress).toHaveAttribute("aria-valuemax", "3"); await expect(progress).toContainText("已完成 1 / 3 批"); }
        else await expect(progress).not.toHaveAttribute("aria-valuenow");
        state.progressStep = index + 1;
        await page.getByRole("button", { name: "刷新作业", exact: true }).click();
      }
      await expect(page.getByTestId("job-status")).toContainText(/已完成|已成功|部分完成/);
      expect(state.submissions).toBe(1); expect(state.writes).toBe(1);
      if (state.partial) await expect(page.getByText(/部分完成/).first()).toBeVisible();
      await artifact(page, state, `rubric-generation-${strategy.name}`);
    });
  }

  test("rubric server checklist prevents whole-set confirmation until coverage and domain reviews finish", async ({ page }) => {
    const state = await rubricFixture(page);
    await page.goto(rubricReview);
    await expect(page.getByRole("heading", { name: "评分规则审阅", exact: true })).toBeVisible();
    const confirmSet = page.getByRole("button", { name: "确认整套评分规则", exact: true });
    await expect(confirmSet).toBeDisabled();
    for (const tab of ["审核分节", "审核条目"]) {
      await page.getByRole("tab", { name: tab, exact: true }).click();
      for (let index = 0; index < 2; index++) {
        await page.getByRole("button", { name: tab === "审核分节" ? "确认分节" : "确认条目", exact: true }).click();
        const dialog = page.getByRole("dialog", { name: "评分规则处理" });
        await dialog.getByLabel("处理理由", { exact: true }).fill("已逐字核对招标原文和审核职责。");
        await dialog.getByRole("button", { name: "提交决定", exact: true }).click();
        if (index === 0) await page.getByRole("button", { name: "下一页", exact: true }).click();
      }
    }
    await expect(confirmSet).toBeDisabled();
    // Coverage is a separate human operation: confirming every visible child alone is insufficient.
    state.rubricCoverage.forEach((row, index) => { row.disposition = "mapped"; row.rubric_item_ids = [uuid(800 + index)]; row.decided_by = uuid(3); row.decided_at = "2026-10-05T00:00:00Z"; row.revision++; });
    state.rubricRevision++;
    await page.reload();
    await expect(confirmSet).toBeEnabled();
    await confirmSet.click();
    const dialog = page.getByRole("dialog", { name: "评分规则处理" });
    await dialog.getByLabel("处理理由", { exact: true }).fill("覆盖清单和各职责审核均已完成，确认整套规则。");
    await dialog.getByRole("button", { name: "提交决定", exact: true }).click();
    await expect(page.getByRole("button", { name: "重新打开整套规则", exact: true })).toBeVisible();
    expect(state.rubricState).toBe("confirmed"); expect(state.writes).toBe(5);
    await artifact(page, state, "rubric-whole-set-gates");
  });

  for (const role of ["admin", "technical", "viewer"]) {
    test(`rubric ${role} cannot confirm commercial domain work`, async ({ page }) => {
      const state = await rubricFixture(page, { role });
      await page.goto(rubricReview);
      await page.getByRole("tab", { name: "审核分节", exact: true }).click();
      await expect(page.getByRole("button", { name: "确认分节", exact: true })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "确认整套评分规则", exact: true })).toBeDisabled();
      expect(state.writes).toBe(0);
      await artifact(page, state, `rubric-role-${role}`);
    });
  }

  test("rubric coverage mapping, exclusion, reopening and duplicate canonical source are distinct writes", async ({ page }) => {
    const state = await rubricFixture(page);
    await page.goto(rubricReview);
    await page.getByRole("tab", { name: "核对评分要求覆盖", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "评分规则处理" });
    await page.getByRole("button", { name: "核对覆盖", exact: true }).click();
    await checkControl(dialog.getByRole("checkbox", { name: /合成评分条目 1/ }));
    await dialog.getByLabel("处理理由", { exact: true }).fill("核对第一条要求与对应评分条目。");
    await dialog.getByRole("button", { name: "提交决定", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect(page.getByText(/已对应评分项/)).toBeVisible();
    expect(state.rubricCoverage[0].rubric_item_ids).toEqual([uuid(800)]);
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    await page.getByRole("button", { name: "核对覆盖", exact: true }).click();
    await choose(dialog, "覆盖决定", "有理由地排除");
    await dialog.getByLabel("处理理由", { exact: true }).fill("第二条属于重复附加说明，先明确排除。");
    await dialog.getByRole("button", { name: "提交决定", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect.poll(() => state.rubricCoverage[1].disposition).toBe("excluded");
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    await page.getByRole("button", { name: "核对覆盖", exact: true }).click();
    await choose(dialog, "覆盖决定", "重新打开覆盖");
    await dialog.getByLabel("处理理由", { exact: true }).fill("排除理由有误，重新核对规范要求。");
    await dialog.getByRole("button", { name: "提交决定", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect.poll(() => state.rubricCoverage[1].disposition).toBe("pending");
    await page.getByRole("button", { name: "下一页", exact: true }).click();
    await page.getByRole("button", { name: "核对覆盖", exact: true }).click();
    await choose(dialog, "覆盖决定", "重复要求");
    await choose(dialog, "规范要求", "必须提供真实材料，响应应无负偏离。");
    await expect(dialog.getByText(/规范要求目标项.*合成评分条目 1/)).toBeVisible();
    await dialog.getByLabel("处理理由", { exact: true }).fill("逐字核对规范要求原文，确认第二条重复。");
    await dialog.getByRole("button", { name: "提交决定", exact: true }).click();
    await expect(dialog).not.toBeVisible();
    await expect.poll(() => state.rubricCoverage[1].disposition).toBe("duplicate");
    expect(state.rubricCoverage[1].canonical_requirement_id).toBe(uuid(700));
    expect(state.writes).toBe(4);
    await artifact(page, state, "rubric-coverage-decisions");
  });

  test("rubric section reviews every source and replacement removes one verified selection", async ({ page }) => {
    const state = await rubricFixture(page, { multipleSectionSources: true });
    await page.goto(rubricReview);
    const section = page.locator('.el-card').filter({ has: page.getByRole('heading', { name: '合成分节 1', exact: true }) });
    await expect(section.getByRole('button', { name: '查看引用原文', exact: true })).toHaveCount(2);
    await section.getByRole('button', { name: '查看引用原文', exact: true }).nth(1).click();
    await expect(page.getByRole('dialog', { name: '引用原文' })).toContainText(state.sections[0].sources[1].quote);
    expect(state.requests.at(-1).query).toMatchObject({ origin: 'sources', citation_index: '1' });
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: '完整修订评分规则', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '完整修订评分规则' });
    await expect(dialog.getByRole('button', { name: '移除此原文', exact: true })).toHaveCount(2);
    await dialog.getByRole('button', { name: '移除此原文', exact: true }).nth(1).click();
    await expect(dialog.getByRole('button', { name: '移除此原文', exact: true })).toBeDisabled();
    await dialog.getByLabel('完整修订理由', { exact: true }).fill('核对两段固定原文后移除不属于此分节的一段。');
    await dialog.getByRole('button', { name: '保存完整修订', exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/score-rubrics/${uuid(23)}$`));
    expect(state.replacements[0].sections[0].sources).toEqual([{ requirement_id: uuid(700), quote: state.rubricCoverage[0].source.quote }]);
    expect(state.replacements[0].sections[0]).not.toHaveProperty('source');
    expect(state.replacements[0].sections[0]).not.toHaveProperty('requirement_id');
    await artifact(page, state, 'rubric-multiple-section-sources');
  });

  test("rubric full replacement reads every page, preserves graph and resets approvals with new IDs", async ({ page }) => {
    const state = await rubricFixture(page);
    await page.goto(rubricReview);
    await page.getByRole("button", { name: "完整修订评分规则", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "完整修订评分规则" });
    await expect(dialog.getByRole("status")).toContainText("分节 2、条目 2、评分要求 2");
    for (const part of ["sections", "items", "coverage"]) expect(state.requests.some(row => row.query.part === part && row.query.cursor === "second-page")).toBe(true);
    await dialog.getByLabel("标题", { exact: true }).fill("完整修订后的第一分节");
    await dialog.getByRole("button", { name: "下一条修订", exact: true }).click();
    await expect(dialog.getByLabel("标题", { exact: true })).toHaveValue("合成分节 2");
    await dialog.getByRole("tab", { name: "修订覆盖提议", exact: true }).click();
    await choose(dialog, "覆盖提议", "对应评分项");
    await choose(dialog, "对应条目", "合成评分条目 1");
    await dialog.getByLabel("覆盖理由", { exact: true }).fill("拟将第一条要求对应第一评分项，等待人工复核。");
    await dialog.getByLabel("完整修订理由", { exact: true }).fill("保留全部分节、条目和覆盖图，仅明确第一分节标题。");
    await dialog.getByRole("button", { name: "保存完整修订", exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`/score-rubrics/${uuid(23)}$`));
    await expect(page.getByText(/第 2 版.*待审核/)).toBeVisible();
    expect(state.replacements).toHaveLength(1); expect(state.replacements[0].sections[1].title).toBe("合成分节 2");
    expect(state.sections.every(row => row.review_domain === null && row.state === "candidate" && row.confirmed_by === null)).toBe(true);
    expect(state.rubricCoverage.every(row => row.disposition === "pending" && row.decided_by === null)).toBe(true);
    expect(state.rubricItems.every(row => ![uuid(800), uuid(801)].includes(row.id))).toBe(true);
    await page.getByRole("button", { name: "恢复已保存的覆盖提议", exact: true }).click();
    await page.getByRole("tab", { name: "核对评分要求覆盖", exact: true }).click();
    await page.getByRole("button", { name: "核对已保存提议", exact: true }).click();
    const recovered = page.getByRole("dialog", { name: "评分规则处理" });
    await expect(recovered.getByRole("checkbox", { name: /合成评分条目 1/ })).toBeChecked();
    await expect(recovered.getByLabel("处理理由", { exact: true })).toHaveValue("拟将第一条要求对应第一评分项，等待人工复核。");
    expect(state.writes).toBe(1);
    await recovered.getByRole("button", { name: "提交决定", exact: true }).click();
    await expect(recovered).not.toBeVisible();
    await expect.poll(() => state.rubricCoverage[0].rubric_item_ids).toEqual([state.rubricItems[0].id]);
    await expect(page.getByText(/已对应评分项/)).toBeVisible();
    expect(state.writes).toBe(2);
    await artifact(page, state, "rubric-complete-replacement");
  });

  for (const role of ["bidder", "technical"]) {
    test(`rubric ${role} repairs own entries while preserving another domain's invalid declarations`, async ({ page }) => {
      const state = await rubricFixture(page, { role });
      const ownDomain = role === "bidder" ? "commercial" : "technical", otherDomain = role === "bidder" ? "technical" : "commercial";
      for (const rows of [state.sections, state.rubricItems]) { rows[0].review_domain = ownDomain; rows[1].review_domain = otherDomain; }
      Object.assign(state.sections[0], { weight: "35", score_range: { minimum: "12", maximum: "10" }, normalization_errors: ["invalid_score_bounds", "invalid_weight"] });
      Object.assign(state.rubricItems[0], { score_range: null, normalization_errors: ["missing_score_bounds"] });
      Object.assign(state.sections[1], { weight: "35", cap: "-2.12345678", score_range: { minimum: "12.12345678", maximum: "10.12345678" }, normalization_errors: ["invalid_cap", "invalid_score_bounds", "invalid_weight", "unexpected_cap"] });
      Object.assign(state.rubricItems[1], { assessment_mode: "model_assessable", score_range: null, weight: "35.12345678", ambiguity_reason: null, normalization_errors: ["invalid_weight", "missing_score_bounds"] });
      const lockedSection = structuredClone(state.sections[1]), lockedItem = structuredClone(state.rubricItems[1]);
      state.nextNormalizationErrors = { sections: [[], lockedSection.normalization_errors], items: [[], lockedItem.normalization_errors] };
      await page.goto(rubricReview);
      await expect(page.getByTestId("rubric-normalization-errors")).toContainText("invalid_weight");
      await page.getByRole("button", { name: "完整修订评分规则", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "完整修订评分规则" });
      await expect(dialog.getByRole("status")).toContainText("分节 2、条目 2、评分要求 2");
      for (const part of ["sections", "items", "coverage"]) expect(state.requests.some(row => row.query.part === part && row.query.cursor === "second-page")).toBe(true);
      await expect(dialog.getByTestId("replacement-normalization-errors")).toContainText("invalid_score_bounds");
      await expect(dialog.getByLabel("权重", { exact: true })).toHaveValue("35");
      await dialog.getByLabel("权重", { exact: true }).fill("");
      await dialog.getByLabel("最低分", { exact: true }).fill("0");
      await dialog.getByRole("button", { name: "下一条修订", exact: true }).click();
      await expect(dialog.getByLabel("标题", { exact: true })).toBeDisabled();
      await expect(dialog.getByRole("button", { name: "移除当前项", exact: true })).toBeDisabled();
      await expect(dialog.getByLabel("权重", { exact: true })).toBeDisabled();
      await expect(dialog.getByLabel("权重", { exact: true })).toHaveValue("35");
      await expect(dialog.getByLabel("最高分", { exact: true })).toHaveValue(lockedSection.score_range.maximum);
      await expect(dialog.getByTestId("replacement-normalization-errors")).toContainText("unexpected_cap");
      await dialog.getByRole("tab", { name: "修订条目", exact: true }).click();
      await expect(dialog.getByTestId("replacement-normalization-errors")).toContainText("missing_score_bounds");
      await dialog.getByRole("button", { name: "设置分值范围", exact: true }).click();
      await dialog.getByLabel("最高分", { exact: true }).fill("10");
      await dialog.getByRole("button", { name: "下一条修订", exact: true }).click();
      await expect(dialog.getByLabel("权重", { exact: true })).toBeDisabled();
      await expect(dialog.getByLabel("权重", { exact: true })).toHaveValue(lockedItem.weight);
      await expect(dialog.getByRole("button", { name: "设置分值范围", exact: true })).toBeDisabled();
      await expect(dialog.getByTestId("replacement-normalization-errors")).toContainText("missing_score_bounds");
      if (role === "technical") {
        await dialog.getByRole("tab", { name: "修订总分规则", exact: true }).click();
        await expect(dialog.getByRole("combobox", { name: "总分合计规则", exact: true })).toBeDisabled();
      }
      await dialog.getByLabel("完整修订理由", { exact: true }).fill("仅修复当前职责的权重和分值范围，保留其他职责原始声明供后续审核。详细错误由服务器保存后重新核验。");
      await dialog.getByRole("button", { name: "保存完整修订", exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`/score-rubrics/${uuid(23)}$`));
      const replacement = state.replacements[0];
      expect(replacement.sections[0]).toMatchObject({ weight: null, score_range: { minimum: "0", maximum: "10" } });
      expect(replacement.items[0].score_range).toEqual({ minimum: "0", maximum: "10" });
      expect(replacement.sections[1]).toMatchObject({ weight: lockedSection.weight, cap: lockedSection.cap, score_range: lockedSection.score_range });
      expect(replacement.items[1]).toMatchObject({ weight: lockedItem.weight, score_range: null, ambiguity_reason: null });
      for (const field of ["key", "title", "order", "aggregation", "aggregation_rule_text", "score_range", "weight", "cap", "included_in_overall_total", "ambiguity_reason"]) expect(replacement.sections[1][field]).toEqual(lockedSection[field]);
      expect(replacement.sections[1].sources).toEqual(lockedSection.sources.map(({ requirement_id, quote }) => ({ requirement_id, quote })));
      expect(replacement.sections[1].source_section_id).toBe(lockedSection.id);
      for (const field of ["requirement_id", "key", "title", "rule_text", "order", "assessment_mode", "score_range", "weight", "ambiguity_reason"]) expect(replacement.items[1][field]).toEqual(lockedItem[field]);
      expect(replacement.items[1].source_item_id).toBe(lockedItem.id); expect(replacement.items[1].section_key).toBe(lockedSection.key);
      if (role === "technical") expect(replacement).toMatchObject({ overall_aggregation: "sum", overall_rule_text: null, overall_score_range: { minimum: "0", maximum: "10" }, overall_cap: null });
      for (const part of ["sections", "items"]) for (const row of replacement[part]) { expect(row).not.toHaveProperty("normalization_errors"); expect(row).not.toHaveProperty("review_domain"); }
      expect(state.sections[0].normalization_errors).toEqual([]); expect(state.rubricItems[0].normalization_errors).toEqual([]);
      expect(state.rubricSummary().completeness.normalization_errors).toBeGreaterThan(0);
      await expect(page.getByTestId("rubric-normalization-errors")).toHaveCount(0);
      await page.getByRole("button", { name: "下一页", exact: true }).click();
      await expect(page.getByTestId("rubric-normalization-errors")).toContainText("invalid_score_bounds");
      await page.getByRole("tab", { name: "审核条目", exact: true }).click();
      await page.getByRole("button", { name: "下一页", exact: true }).click();
      await expect(page.getByTestId("rubric-normalization-errors")).toContainText("missing_score_bounds");
      await expect(page.getByRole("button", { name: "确认整套评分规则", exact: true })).toBeDisabled();
      expect(state.writes).toBe(1);
      await artifact(page, state, `rubric-domain-repair-${role}`);
    });
  }

  for (const problem of ["mixedSnapshot", "missingPage"]) {
    test(`rubric ${problem} disables complete replacement save`, async ({ page }) => {
      const state = await rubricFixture(page, { [problem]: true });
      await page.goto(rubricReview);
      await page.getByRole("button", { name: "完整修订评分规则", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: "完整修订评分规则" });
      await expect(dialog.getByRole("alert").first()).toContainText(/快照|变化|assessment_view_changed/);
      await expect(dialog.getByRole("button", { name: "保存完整修订", exact: true })).toBeDisabled();
      expect(state.replacements).toHaveLength(0); expect(state.writes).toBe(0);
      await artifact(page, state, `rubric-replacement-${problem}`);
    });
  }

  test("rubric oversized complete replacement cannot submit", async ({ page }) => {
    const state = await rubricFixture(page);
    await page.goto(rubricReview);
    await page.getByRole("button", { name: "完整修订评分规则", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "完整修订评分规则" });
    await expect(dialog.getByRole("status")).toContainText("完整快照");
    await dialog.getByRole("tab", { name: "修订条目", exact: true }).click();
    await dialog.getByLabel("条目评分规则", { exact: true }).fill("x".repeat(530000));
    await dialog.getByLabel("完整修订理由", { exact: true }).fill("模拟超出完整编辑上限。");
    await expect(dialog.getByRole("alert").last()).toContainText("超出当前编辑上限");
    await expect(dialog.getByRole("button", { name: "保存完整修订", exact: true })).toBeDisabled();
    expect(state.replacements).toHaveLength(0);
    await artifact(page, state, "rubric-replacement-oversize");
  });
});

test.describe("score acceptance", () => {
  const scoreWorkspace = `/app/org/tasks/${ids.task}/scores?job=${ids.extract}&draft=${ids.draft}&rubric=${ids.rubric}`;
  const scoreReport = `/app/org/tasks/${ids.task}/scores/${ids.score}`;
  test("score preview submits exact confirmed rubric, draft, date, hash and positive cap", async ({ page }) => {
    const state = await scoreFixture(page);
    await page.goto(scoreWorkspace);
    await expect(page.getByRole("heading", { name: "评分预估", exact: true })).toBeVisible();
    await page.getByLabel("评估日期", { exact: true }).fill("2026-10-05");
    await page.getByLabel("本次作业平台扣费上限", { exact: true }).fill("1");
    await page.getByRole("button", { name: "预览评分", exact: true }).click();
    await expect(page.getByText("本次评分范围", { exact: true })).toBeVisible();
    expect(state.previews).toBe(1); expect(state.writes).toBe(0);
    await expect(page.getByRole("button", { name: "提交评分", exact: true })).toBeDisabled();
    await checkControl(page.getByRole("checkbox", { name: "我已核对外发范围与费用上限", exact: true }));
    await page.getByRole("button", { name: "提交评分", exact: true }).click();
    await expect(page.getByTestId("job-status")).toContainText(/已完成|已成功/);
    await expect(page.getByText(/部分完成；请查看已保存报告/)).toBeVisible();
    const submitted = state.requests.find(row => row.method === "POST" && row.path.endsWith("/scores") && row.body.dry_run === false);
    expect(submitted.body).toMatchObject({ rubric_id: ids.rubric, draft_id: ids.draft, assessment_date: "2026-10-05", expected_input_hash: hash, max_charge: "1" });
    expect(state.submissions).toBe(1); expect(state.writes).toBe(1);
    await artifact(page, state, "score-bound-submit");
  });

  for (const totalStatus of ["estimated", "range_only", "unavailable"]) {
    test(`score ${totalStatus} preserves zero, null and server-only aggregate semantics`, async ({ page }) => {
      const state = await scoreFixture(page, { totalStatus, stale: totalStatus === "unavailable" });
      await page.goto(scoreReport);
      await expect(page.getByRole("heading", { name: "评分报告", exact: true })).toBeVisible();
      if (totalStatus === "estimated") await expect(page.getByTestId("score-total")).toHaveText("0");
      else {
        await expect(page.getByText(totalStatus === "unavailable" ? "总分暂不可用" : /可能范围|可能区间/).first()).toBeVisible();
        await expect(page.getByTestId("score-total")).toHaveCount(0);
        await expect(page.getByText(/已评估.*小计/).first()).toBeVisible();
      }
      await page.getByRole("tab", { name: "逐项评分", exact: true }).click();
      await expect(page.getByText(/已评估的真实零分，缺少条款规定的真实证明。/)).toBeVisible();
      await expect(page.getByText(/条目预估：0/)).toBeVisible();
      await expect(page.getByText("缺少可核验真实材料", { exact: true })).toBeVisible();
      await expect(page.getByText("补充真实材料并由职责负责人确认", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: "下一页", exact: true }).click();
      if (totalStatus !== "estimated") {
        await expect(page.getByText(/无法评估/).first()).toBeVisible();
        await expect(page.getByText(/该分节不进入总分，但其外部排名仍无法评估。/)).toBeVisible();
        await expect(page.getByText(/条目分数：未知/)).toBeVisible();
        await expect(page.getByText(/条目预估：0/)).toHaveCount(0);
      }
      await page.getByRole("tab", { name: "分节合计", exact: true }).click();
      await page.getByRole("button", { name: "下一页", exact: true }).click();
      if (totalStatus === "unavailable") await expect(page.getByRole("table").getByText(/暂不可用/)).toBeVisible();
      if (totalStatus === "range_only") await expect(page.getByText(/可能范围|可能区间/).last()).toBeVisible();
      if (state.stale) await expect(page.getByRole("alert").first()).toContainText(/初稿|规则|变化|过期/);
      expect(state.requests.filter(row => row.path.endsWith(`/scores/${ids.score}`)).every(row => row.query.view === "console")).toBe(true);
      expect(state.writes).toBe(0);
      await artifact(page, state, `score-report-${totalStatus}`);
    });
  }

  test("score unconfirmed rubric blocks preview and viewer cannot submit", async ({ page }) => {
    const state = await scoreFixture(page, { unconfirmedRubric: true });
    await page.goto(scoreWorkspace);
    await expect(page.getByRole("button", { name: "预览评分", exact: true })).toBeDisabled();
    expect(state.previews).toBe(0); expect(state.submissions).toBe(0);
    state.role = "viewer"; state.unconfirmedRubric = false;
    await page.reload();
    await expect(page.getByRole("button", { name: "预览评分", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "提交评分", exact: true })).toHaveCount(0);
    await artifact(page, state, "score-role-rubric-gates");
  });

  test("score zero planned calls saves an explicit unavailable report and reads reasons on demand", async ({ page }) => {
    const state = await scoreFixture(page, { noEligible: true });
    await page.goto(scoreWorkspace);
    await page.getByLabel("本次作业平台扣费上限", { exact: true }).fill("1");
    await page.getByRole("button", { name: "预览评分", exact: true }).click();
    await expect(page.getByText("没有可由模型评估的评分项", { exact: true })).toBeVisible();
    expect(state.writes).toBe(0);
    expect(state.requests.filter(row => row.query.entry_id)).toHaveLength(0);
    await page.getByText("查看预检无法评估的评分项", { exact: true }).click();
    await page.getByRole("button", { name: `核对评分项 ${uuid(800)}`, exact: true }).click();
    await expect(page.getByText("需要人工核对外部排名，模型不能评估", { exact: true })).toBeVisible();
    expect(state.requests.filter(row => row.query.entry_id === uuid(800))).toHaveLength(1);
    await checkControl(page.getByRole("checkbox", { name: "我已核对外发范围与费用上限", exact: true }));
    await page.getByRole("button", { name: "生成无法评估报告", exact: true }).click();
    await expect(page.getByText(/部分完成；请查看已保存报告/)).toBeVisible();
    expect(state.submissions).toBe(1);
    await artifact(page, state, "score-no-eligible-explicit-report");
  });

  test("score verified citations use pinned draft revision and current card deep link", async ({ page }) => {
    const state = await scoreFixture(page);
    await page.goto(scoreReport);
    await page.getByRole("tab", { name: "逐项评分", exact: true }).click();
    await page.getByText("查看保存的原文、响应和证据引用", { exact: true }).click();
    const citations = page.getByRole("button", { name: "查看引用原文", exact: true });
    await citations.nth(1).click();
    const dialog = page.getByRole("dialog", { name: "引用原文" });
    await expect(dialog).toContainText(`响应修订 ${ids.revision}`);
    await expect(dialog.getByRole("link", { name: "前往响应卡修改", exact: true })).toHaveAttribute("href", new RegExp(`job=${ids.extract}.*requirement=${uuid(700)}.*card=${ids.card}`));
    await page.keyboard.press("Escape");
    await expect(citations.nth(1)).toBeFocused();
    expect(state.requests.filter(row => row.path.endsWith("/assessment-citation")).map(row => row.query.origin)).toEqual(["citations"]);
    await page.setViewportSize({ width: 720, height: 900 });
    await page.evaluate(() => { document.documentElement.style.zoom = "2"; });
    await expect(page.getByRole("tab", { name: "逐项评分", exact: true })).toBeVisible();
    await artifact(page, state, "score-pinned-citation");
  });
});
