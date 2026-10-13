// Failure modes identified before writing these browser gates:
// - Discovery: uploaded/unparsed documents and parse jobs must survive a fresh tab;
//   the G1 job reader must not expose extraction/generation jobs or queue internals.
// - Citations: PDF quotes must be found in the sorted extracted page text and Word
//   quotes in the cited structural block; tests must not assume PDF space runs.
// - Cost and jobs: a dry-run must not create a job or vendor call; queued is not
//   completed, partial is not failure, and the model generation control must stay
//   disabled until a human authorizes the exact preview hash.
// - Review gates: evidence and warnings need explicit human review.
// - Concurrency: stale revisions must retain local edits, clear old review checks and
//   fail visibly; an atomic batch with one stale item must write nothing.
// - Scale: all 1,200 requirements and CardViews must load without truncation, only
//   one page plus one detail may mount, and filtering/paging/keyboard review must
//   stay under the approved p95 and first-load limits without preloading images.
// - Drafts: rows, comply-only entries and gaps must partition one fixed extraction;
//   partial and stale drafts must remain visible without being called deliverable.
// - Browser safety: source text must stay text, no auth/signature may enter URLs or
//   artifacts, no unexpected console errors may occur, and narrow/200% layouts must
//   keep source and actions reachable by keyboard.

import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { cpus, platform, release } from "node:os";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test } from "@playwright/test";
import { openConsole } from "./access-key.js";
import { SESSION_KEY } from "../src/session-storage.js";

const env = (name) => {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
};

const workRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../data/work");
let output;
let fixture;
let taskA;
let taskId;
let extractionJobId;
let reviewUrl;
const hash = (value) => createHash("sha256").update(String(value)).digest("hex").slice(0, 16);

function loadRuntime() {
  output = resolve(env("E2E_OUTPUT"));
  const outputRelative = relative(workRoot, output);
  if (outputRelative.startsWith("..") || resolve(workRoot, outputRelative) !== output) {
    throw new Error(`E2E_OUTPUT must stay under ${workRoot}`);
  }
  mkdirSync(output, { recursive: true });

  const manifestPath = resolve(env("E2E_ORG_FIXTURE"));
  const manifestRelative = relative(workRoot, manifestPath);
  if (manifestRelative.startsWith("..") || resolve(workRoot, manifestRelative) !== manifestPath) {
    throw new Error(`E2E_ORG_FIXTURE must stay under ${workRoot}`);
  }
  fixture = JSON.parse(readFileSync(manifestPath, "utf8"));
  if (fixture.schema !== "single-user-console-e2e-v2") throw new Error("Unsupported fixture manifest");
  taskA = fixture.tasks.a;
  taskId = taskA.task.id;
  extractionJobId = taskA.extractions.low.id;
  reviewUrl = `/app/org/tasks/${taskId}/review?job=${extractionJobId}`;
}

function assertResult(payload) {
  expect(Object.keys(payload).sort()).toEqual(
    ["command", "cost", "data", "duration_ms", "items", "ok", "warnings"].sort(),
  );
  expect(Array.isArray(payload.items)).toBe(true);
  expect(Array.isArray(payload.warnings)).toBe(true);
}

async function login(page) {
  await openConsole(page);
  await expect(page).toHaveURL(/\/app\/org\/tasks$/);
  await expect(page.getByRole("heading", { name: "招标任务" })).toBeVisible();
}

// Element Plus selects open a listbox; options are chosen by their visible text.
// Use the combobox keyboard controls to open and close Element Plus listboxes.
// Options are scoped to the listbox this combobox controls, never another select closing.
async function openSelect(page, label) {
  const input = page.getByLabel(label, { exact: true });
  // The page disables its selects while an in-flight dry run resolves; a native
  // disabled input swallows the key press, so wait for the control before opening.
  await expect(input).toBeEnabled();
  // Element Plus sometimes ignores the first ArrowDown while the control settles,
  // leaving aria-controls pointing at a listbox that never became visible. A pointer
  // click is not an option: the selected-value overlay and the sticky header both
  // intercept it. Keep to the keyboard and retry until this combobox's listbox opens,
  // re-reading aria-controls each time in case the dropdown element is re-created.
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await input.press("ArrowDown");
    const listbox = page.locator(`[id="${await input.getAttribute("aria-controls")}"]`);
    if (await listbox.isVisible()) return listbox;
    await page.waitForTimeout(100);
  }
  const listbox = page.locator(`[id="${await input.getAttribute("aria-controls")}"]`);
  await expect(listbox).toBeVisible();
  return listbox;
}

async function choose(page, label, option) {
  const listbox = await openSelect(page, label);
  await listbox.getByRole("option", { name: option, exact: typeof option === "string" }).click();
  await expect(listbox).toBeHidden();
}

async function optionTexts(page, label) {
  const listbox = await openSelect(page, label);
  const texts = await listbox.getByRole("option").allTextContents();
  await page.getByLabel(label, { exact: true }).press("Escape");
  await expect(listbox).toBeHidden();
  return texts.join("\n");
}

// Confirms the open Element Plus message box.
async function accept(page) {
  await page.getByRole("dialog").filter({ visible: true }).getByRole("button", { name: "确定" }).click();
}

async function browserApi(page, method, path, body) {
  return page.evaluate(
    async ({ requestMethod, requestPath, requestBody, sessionKey }) => {
      const current = sessionStorage.getItem(sessionKey);
      if (!current) throw new Error("Missing console session");
      const headers = { Authorization: `Bearer ${current}` };
      if (requestBody !== undefined) headers["Content-Type"] = "application/json";
      const response = await fetch(requestPath, {
        method: requestMethod,
        headers,
        credentials: "omit",
        cache: "no-store",
        body: requestBody === undefined ? undefined : JSON.stringify(requestBody),
      });
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        // The tested endpoints are JSON. Returning null preserves the status for a useful assertion.
      }
      return { status: response.status, payload };
    },
    { requestMethod: method, requestPath: path, requestBody: body, sessionKey: SESSION_KEY },
  );
}

function sourceBlock(chunks, requirement) {
  const chunk = chunks.find((item) => item.id === requirement.source.chunk_id);
  expect(chunk).toBeTruthy();
  if (requirement.source.page != null) return chunk.text;
  const block = chunk.blocks.find(
    (item) => item.block_id === requirement.source.location.block_id,
  );
  expect(block).toBeTruthy();
  return block.text;
}

function p95(samples) {
  const sorted = [...samples].sort((left, right) => left - right);
  return sorted[Math.max(0, Math.ceil(sorted.length * 0.95) - 1)] ?? 0;
}

function sanitized(value) {
  if (Array.isArray(value)) return value.map(sanitized);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, sanitized(item)]));
  }
  if (typeof value === "string" && /^[0-9a-f-]{36}$/i.test(value)) return `uuid-sha256:${hash(value)}`;
  return value;
}

test.describe("访问密钥登录", () => {
  test("an unauthenticated visit shows the access-key form", async ({ page }) => {
    await page.goto("/app/org/tasks");
    await expect(page).toHaveURL(/\/app\/login$/);
    await expect(page.getByRole("heading", { name: "输入访问密钥" })).toBeVisible();
    await expect(page.getByLabel("访问密钥")).toBeVisible();
    await expect(page.getByRole("button", { name: "进入" })).toBeVisible();
  });

  test("a wrong access key is rejected without a session", async ({ page }) => {
    await page.goto("/app/login");
    await page.getByLabel("访问密钥").fill(`wrong-${"x".repeat(40)}`);
    await page.getByRole("button", { name: "进入" }).click();
    await expect(page.getByRole("alert")).toContainText("访问密钥无效");
    expect(await page.evaluate((key) => sessionStorage.getItem(key), SESSION_KEY)).toBeNull();
  });

  test("the URL fragment key is exchanged and removed before the tasks page renders", async ({ page }) => {
    await login(page);
    expect(new URL(page.url()).hash).toBe("");
    expect(page.url()).not.toContain("#key");
    await expect(page.getByRole("heading", { name: "招标任务" })).toBeVisible();
  });
});

test.describe.serial("招标、审阅与初稿控制台", () => {
  test.beforeAll(() => loadRuntime());

  test("G1 discovery and exact PDF/Word citations", async ({ page, request }) => {
    await login(page);

    const ownTask = await browserApi(page, "GET", `/tasks/${taskId}`);
    expect(ownTask.status).toBe(200);
    assertResult(ownTask.payload);
    expect(ownTask.payload.data.name).toBe(taskA.task.name);

    const documents = await browserApi(page, "GET", `/tasks/${taskId}/documents`);
    expect(documents.status).toBe(200);
    assertResult(documents.payload);
    expect(documents.payload.items).toHaveLength(2);
    expect(new Set(documents.payload.items.map((item) => item.status))).toEqual(
      new Set(["parsed"]),
    );
    expect(documents.payload.items.every((item) => !("storage_key" in item))).toBe(true);

    const parseJobs = await browserApi(page, "GET", `/tasks/${taskId}/jobs?kind=parse`);
    expect(parseJobs.status).toBe(200);
    assertResult(parseJobs.payload);
    expect(parseJobs.payload.data.kind).toBe("parse");
    expect(parseJobs.payload.items).toHaveLength(2);
    expect(parseJobs.payload.items.every((item) => item.kind === "parse")).toBe(true);
    for (const item of parseJobs.payload.items) {
      expect(item).not.toHaveProperty("queue_id");
      expect(item).not.toHaveProperty("lease_until");
      expect(item).not.toHaveProperty("run_id");
    }

    const noKind = await browserApi(page, "GET", `/tasks/${taskId}/jobs`);
    expect(noKind.status).toBe(422);
    const wrongKind = await browserApi(page, "GET", `/tasks/${taskId}/jobs?kind=extract`);
    expect(wrongKind.status).toBe(422);

    const wordRequirements = await browserApi(
      page,
      "GET",
      `/tasks/${taskId}/requirements?job=${taskA.extractions.low.id}`,
    );
    expect(wordRequirements.status).toBe(200);
    expect(wordRequirements.payload.items).toHaveLength(fixture.expectations.large_requirement_count);
    const wordChunks = await browserApi(
      page,
      "GET",
      `/documents/${taskA.documents.word.id}/chunks`,
    );
    expect(wordChunks.status).toBe(200);
    for (const requirement of [wordRequirements.payload.items[0], wordRequirements.payload.items.at(-1)]) {
      expect(requirement.source.page).toBeNull();
      expect(requirement.source.location.label).toBeTruthy();
      expect(sourceBlock(wordChunks.payload.items, requirement)).toContain(requirement.source.quote);
    }
    expect(fixture.review.multi_evidence_cards).toHaveLength(80);
    expect(fixture.review.multi_evidence_cards.every((card) => card.evidence_count === 2)).toBe(
      true,
    );
    const cardSlots = await browserApi(
      page,
      "GET",
      `/tasks/${taskId}/cards?job=${taskA.extractions.low.id}`,
    );
    expect(cardSlots.status).toBe(200);
    for (const seeded of [
      fixture.review.multi_evidence_cards[0],
      fixture.review.multi_evidence_cards.at(-1),
    ]) {
      const slot = cardSlots.payload.items.find(
        (item) => item.requirement_id === seeded.requirement_id,
      );
      expect(slot.card.evidence).toHaveLength(2);
      expect(slot.card.evidence.map((item) => item.input.field_path).sort()).toEqual([
        "model",
        "model_version",
      ]);
    }

    const pdfRequirements = await browserApi(
      page,
      "GET",
      `/tasks/${taskId}/requirements?job=${taskA.extractions.pdf.id}`,
    );
    expect(pdfRequirements.status).toBe(200);
    expect(pdfRequirements.payload.items).toHaveLength(3);
    const pdfChunks = await browserApi(
      page,
      "GET",
      `/documents/${taskA.documents.pdf.id}/chunks`,
    );
    expect(pdfChunks.status).toBe(200);
    expect(new Set(pdfRequirements.payload.items.map((item) => item.source.quote)).size).toBe(3);
    for (const requirement of pdfRequirements.payload.items) {
      expect(requirement.source.location).toBeNull();
      expect(requirement.source.page).toBeGreaterThan(0);
      // Compare against actual sorted extraction. Deliberately no expectation about space runs.
      expect(sourceBlock(pdfChunks.payload.items, requirement)).toContain(requirement.source.quote);
    }

    const anonymous = await request.get(`/tasks/${taskId}`);
    expect(anonymous.status()).toBe(401);
    expect((await anonymous.json()).ok).toBe(false);
  });

  test("task entry and official reasoning history", async ({ browser }) => {
    const admin = await browser.newPage();
    await login(admin);
    await admin.getByRole("button", { name: "新建任务" }).click();
    await expect(admin.getByRole("heading", { name: "创建任务" })).toBeVisible();
    for (const label of ["任务名称", "招标编号", "截止时间"]) {
      await expect(admin.getByLabel(label)).toBeVisible();
    }
    await admin.getByLabel("任务名称").fill("浏览器创建与上传恢复任务");
    await admin.getByLabel("招标编号").fill("BROWSER-UPLOAD-2026");
    await admin.getByLabel("截止时间").fill("2027-04-03T10:30");
    await admin.getByRole("button", { name: "创建任务" }).click();
    await expect(admin.getByRole("heading", { name: "浏览器创建与上传恢复任务" })).toBeVisible();
    const createdTaskId = new URL(admin.url()).pathname.split("/").at(-1);
    const upload = admin.getByLabel("招标文件（PDF / DOCX）");
    await upload.setInputFiles(fixture.browser_materials.pdf);
    await admin.getByRole("button", { name: "上传文件" }).click();
    await expect(admin.getByRole("status")).toContainText("上传成功");
    await upload.setInputFiles(fixture.browser_materials.pdf);
    await admin.getByRole("button", { name: "上传文件" }).click();
    await expect(admin.getByRole("status")).toContainText("同内容文件已存在");

    const recovered = await browser.newPage();
    await login(recovered);
    await recovered.goto(`/app/org/tasks/${createdTaskId}`);
    // Documents load after the page; read the options only once the selected document is shown.
    await expect(recovered.getByText(/状态：已上传/)).toBeVisible();
    await choose(recovered, "文档", /browser-upload-synthetic\.pdf/);
    await recovered.close();

    await admin.getByRole("button", { name: "开始解析" }).click();
    await expect(admin.getByTestId("job-status")).toContainText("作业状态：已成功", {
      timeout: 30_000,
    });
    await expect(admin.getByText(/状态：已解析/)).toBeVisible();
    const beforePreview = await browserApi(
      admin,
      "GET",
      `/tasks/${createdTaskId}/extractions`,
    );
    await admin.getByRole("button", { name: "抽取预检" }).click();
    const levels = await optionTexts(admin, "官方推理档位");
    expect(levels).toContain("轻度推理");
    expect(levels).toContain("深度推理");
    await choose(admin, "官方推理档位", /· high(（默认）)?$/);
    await admin.getByRole("button", { name: "抽取预检" }).click();
    await expect(admin.getByText(/预检结果：可抽取 · 档位 high/)).toBeVisible();
    const afterPreview = await browserApi(
      admin,
      "GET",
      `/tasks/${createdTaskId}/extractions`,
    );
    expect(afterPreview.payload.items.map((item) => item.job_id)).toEqual(
      beforePreview.payload.items.map((item) => item.job_id),
    );
    await admin.getByRole("button", { name: "开始抽取" }).click();
    await expect(admin.getByTestId("job-status")).toContainText("作业状态：已成功", {
      timeout: 30_000,
    });
    await expect(admin.getByRole("table", { name: /抽取历史/ })).toContainText("high");
    await expect(admin.getByRole("link", { name: "确认要求" })).toBeVisible();
    await expect(admin.getByRole("link", { name: "审阅响应" })).toBeVisible();

    await admin.goto("/app/org/tasks");
    // Several synthetic tasks may share the fixture name; open the exact task ID.
    await admin
      .getByRole("row")
      .filter({ has: admin.locator(`a[href="/app/org/tasks/${taskId}"]`) })
      .getByRole("link", { name: "打开任务" })
      .click();
    await expect(admin.getByRole("heading", { name: taskA.task.name })).toBeVisible();
    await expect(admin.getByText(/状态：已解析/)).toBeVisible();
    const documentOptions = await optionTexts(admin, "文档");
    expect(documentOptions).toContain(taskA.documents.word.name);
    expect(documentOptions).toContain(taskA.documents.pdf.name);
    await expect(admin.getByRole("table", { name: /抽取历史/ })).toContainText("low");
    await expect(admin.getByRole("table", { name: /抽取历史/ })).toContainText("high");
    await expect(admin.getByText("最新", { exact: true }).first()).toBeVisible();
    await admin.getByRole("button", { name: /查看作业/ }).first().click();
    await expect(admin.getByTestId("job-status")).toContainText("作业状态：已成功");
    await admin.close();

  });

  test("1,200-row filtering, pagination and keyboard review meet the gate", async ({ page }) => {
    const consoleErrors = [];
    const previewRequests = [];
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    page.on("request", (request) => {
      if (request.url().includes("/preview/download")) previewRequests.push(request.url());
    });
    await login(page);

    const started = Date.now();
    await page.goto(reviewUrl);
    await expect(page.getByTestId("requirement-count")).toHaveText("匹配 1200 / 全集 1200");
    await expect(page.getByTestId("requirement-row")).toHaveCount(50);
    const firstLoadMs = Date.now() - started;
    expect(firstLoadMs).toBeLessThanOrEqual(fixture.expectations.first_load_ms_max);
    expect(previewRequests).toEqual([]);

    const samples = [];
    const search = page.getByLabel("搜索要求或引文");
    for (const marker of [
      "SYN-A-0001",
      "SYN-A-0107",
      "SYN-A-0213",
      "SYN-A-0319",
      "SYN-A-0425",
      "SYN-A-0531",
      "SYN-A-0637",
      "SYN-A-0743",
      "SYN-A-0849",
      "SYN-A-0955",
      "SYN-A-1061",
      "SYN-A-1167",
    ]) {
      const before = await page.evaluate(() => performance.now());
      await search.fill(marker);
      await expect(page.getByTestId("requirement-row")).toHaveCount(1);
      samples.push((await page.evaluate(() => performance.now())) - before);
    }
    await search.fill("");
    await expect(page.getByTestId("requirement-row")).toHaveCount(50);

    for (let index = 0; index < 4; index += 1) {
      const before = await page.evaluate(() => performance.now());
      await page.getByRole("button", { name: "下一页" }).click();
      await expect(page.getByTestId("requirement-row")).toHaveCount(50);
      samples.push((await page.evaluate(() => performance.now())) - before);
    }
    const previous = page.getByRole("button", { name: "上一页" });
    await previous.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByTestId("requirement-row")).toHaveCount(50);

    await choose(page, "类别", "技术");
    await expect(page.getByTestId("requirement-count")).toContainText("全集 1200");
    await choose(page, "状态", "缺卡片");
    await page.getByLabel("只看缺口").check();
    await page.getByLabel("只看星标").check();
    await expect(page.getByTestId("requirement-count")).toBeVisible();

    await page.getByLabel("只看星标").uncheck();
    await page.getByLabel("只看缺口").uncheck();
    await choose(page, "状态", "全部");
    await choose(page, "类别", "全部");
    await expect(page.getByTestId("requirement-row")).toHaveCount(50);

    const open = page.getByRole("button", { name: "打开审阅" }).first();
    await open.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("region", { name: "要求审阅详情" })).toBeVisible();
    await expect(page.getByText("招标原文", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /下一条待审阅/ })).toBeVisible();

    const interactionP95 = p95(samples);
    expect(interactionP95).toBeLessThanOrEqual(fixture.expectations.interaction_p95_ms_max);
    expect(consoleErrors).toEqual([]);

    await page.setViewportSize({ width: 720, height: 900 });
    await page.evaluate(() => {
      document.documentElement.style.zoom = "2";
    });
    await expect(page.getByText("招标原文", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: /保存草稿|提交审阅/ }).first()).toBeVisible();
    await page.screenshot({ path: join(output, "org-console-review-narrow.png"), fullPage: true });

    writeFileSync(
      join(output, "org-console-performance.json"),
      JSON.stringify(
        {
          fixture: fixture.fixture,
          requirement_count: fixture.expectations.large_requirement_count,
          first_load_ms: firstLoadMs,
          interaction_samples_ms: samples,
          interaction_p95_ms: interactionP95,
          thresholds: {
            first_load_ms: fixture.expectations.first_load_ms_max,
            interaction_p95_ms: fixture.expectations.interaction_p95_ms_max,
          },
          machine: {
            platform: platform(),
            release: release(),
            cpu: cpus()[0]?.model ?? "unknown",
            cpu_count: cpus().length,
            browser: await page.context().browser().version(),
          },
          material: {
            documents: 2,
            provision_provider_calls: fixture.provider.calls.length,
            transport: fixture.provider.transport,
          },
        },
        null,
        2,
      ),
    );
  });

  // The 1,200-requirement fixture makes this serial review chain cost ~47s
  // (measured 34.5s of human review work plus a 12.6s draft job), which exceeds
  // Playwright's 60s default before the model-generation assertions run.
  test("review gates, conflict, atomic comply-only, drafts and the bound model generation run", async ({ browser }) => {
    test.setTimeout(180_000);
    const technical = await browser.newPage();
    technical.on("dialog", (dialog) => dialog.accept());
    await login(technical);
    const modelRequests = [];
    technical.on("request", (request) => {
      if (request.method() !== "POST" || !request.url().endsWith("/cards/generations")) return;
      const body = request.postDataJSON();
      if (body && body.dry_run !== true) modelRequests.push(body);
    });

    const pending = fixture.review.cards.pending_evidence;
    await technical.goto(`${reviewUrl}&requirement=${pending.requirement_id}`);
    await expect(technical.getByText("SYN-MODEL-64", { exact: true }).first()).toBeVisible();
    const evidenceChecks = technical.getByRole("checkbox", { name: /核对材料/ });
    expect(await evidenceChecks.count()).toBeGreaterThan(0);
    for (const checkbox of await evidenceChecks.all()) await expect(checkbox).not.toBeChecked();
    await expect(technical.getByRole("button", { name: "确认响应" })).toBeDisabled();
    for (const checkbox of await evidenceChecks.all()) await checkbox.check();
    await technical.getByRole("button", { name: "确认响应" }).click();
    await accept(technical);
    await expect(technical.getByText(/状态：已确认/)).toBeVisible();

    const bidder = await browser.newPage();
    bidder.on("dialog", (dialog) => dialog.accept());
    await login(bidder);
    await bidder.goto(
      `${reviewUrl}&requirement=${fixture.review.cards.pending_reject.requirement_id}`,
    );
    await expect(bidder.getByRole("radio", { name: "承诺" })).toBeChecked();
    await expect(bidder.getByText("本修订没有 Evidence。承诺不构成证明材料。")).toBeVisible();
    await bidder.getByLabel("操作原因 / 警示处理理由").fill("浏览器商务审阅：承诺文字需重写。");
    await bidder.getByRole("button", { name: "驳回" }).click();
    await accept(bidder);
    await expect(bidder.getByText(/状态：已驳回/)).toBeVisible();
    await bidder.close();

    await technical.goto(
      `${reviewUrl}&requirement=${fixture.review.cards.pending_needs_material.requirement_id}`,
    );
    await technical
      .getByLabel("操作原因 / 警示处理理由")
      .fill("浏览器技术审阅：须补充真实交付材料。");
    await technical.getByRole("button", { name: "需补材料" }).click();
    await accept(technical);
    await expect(technical.getByText(/状态：需补材料/)).toBeVisible();

    await technical.goto(
      `${reviewUrl}&requirement=${fixture.review.cards.needs_material.requirement_id}`,
    );
    await expect(technical.getByText(/状态：需补材料/)).toBeVisible();
    await expect(technical.getByText(/需要补充真实材料/)).toBeVisible();

    const conflict = fixture.review.cards.conflict_target;
    const second = await browser.newPage();
    await login(second);
    await technical.goto(`${reviewUrl}&requirement=${conflict.requirement_id}`);
    // The label is shared by the form item and the editor; address the editor itself.
    const localText = technical.getByRole("textbox", { name: "响应正文" });
    await localText.fill("浏览器一保留的未保存并发响应。");
    const competing = await browserApi(second, "PUT", `/cards/${conflict.id}`, {
      expected_revision: conflict.revision,
      content: {
        ...conflict.content,
        response_text: "浏览器二先保存的合成并发响应。",
      },
    });
    expect(competing.status).toBe(200);
    await technical.getByRole("button", { name: "保存草稿" }).click();
    await expect(technical.getByRole("dialog", { name: "修订冲突" })).toBeVisible();
    await expect(technical.getByRole("alert")).toContainText(/修订|改变/);
    // The editor is a contenteditable element; retained text is its text content.
    await expect(localText).toHaveText("浏览器一保留的未保存并发响应。");
    await second.close();

    const candidates = fixture.review.batch_candidates;
    const beforeCards = await browserApi(
      technical,
      "GET",
      `/tasks/${taskId}/cards?job=${extractionJobId}`,
    );
    const byRequirement = new Map(
      beforeCards.payload.items.map((slot) => [slot.requirement_id, slot]),
    );
    const failedBatch = await browserApi(
      technical,
      "POST",
      `/tasks/${taskId}/cards/dispositions`,
      {
        extraction_job_id: extractionJobId,
        items: [
          {
            requirement_id: candidates[0],
            expected_revision: null,
            disposition: "comply_only",
            reason: "合成浏览器原子批次第一项。",
          },
          {
            requirement_id: conflict.requirement_id,
            expected_revision: conflict.revision,
            disposition: "comply_only",
            reason: "故意使用过期修订使整批失败。",
          },
        ],
      },
    );
    expect(failedBatch.status).toBe(409);
    const afterFailure = await browserApi(
      technical,
      "GET",
      `/tasks/${taskId}/cards?job=${extractionJobId}`,
    );
    const untouched = afterFailure.payload.items.find(
      (slot) => slot.requirement_id === candidates[0],
    );
    expect(untouched.status).toBe(byRequirement.get(candidates[0]).status);

    await technical.goto(reviewUrl);
    await choose(technical, "类别", "技术");
    await choose(technical, "状态", "缺卡片");
    const selectable = technical.getByTestId("requirement-row").getByRole("checkbox");
    expect(await selectable.count()).toBeGreaterThanOrEqual(3);
    for (let index = 0; index < 3; index += 1) await selectable.nth(index).check();
    await technical.getByRole("button", { name: "准备批量处置" }).click();
    const batchDialog = technical.getByRole("dialog", { name: "批量处置预检" });
    await expect(batchDialog).toBeVisible();
    for (let index = 1; index <= 3; index += 1) {
      await batchDialog
        .getByLabel(`逐项理由 ${index}`)
        .fill(`合成浏览器批量第 ${index} 项：该技术条款只需遵守。`);
    }
    await batchDialog.getByRole("button", { name: "提交整批处置" }).click();
    await expect(technical.getByRole("status")).toContainText("整批处置已保存");

    await technical.getByText("模型起草预览", { exact: true }).click();
    await technical.getByRole("button", { name: "预检外发范围与配置" }).click();
    await expect(technical.getByRole("heading", { name: "本次预检" })).toBeVisible();
    // The run stays disabled until a human authorizes this exact preview.
    const model = technical.getByRole("button", { name: "确认并运行" });
    await expect(model).toBeDisabled();

    expect(modelRequests).toEqual([]);

    await technical.goto(`/app/org/tasks/${taskId}/drafts?job=${extractionJobId}`);
    await expect(technical.getByRole("heading", { name: "响应表初稿" })).toBeVisible();
    await technical.getByRole("button", { name: "预检组表" }).click();
    await expect(technical.getByText("响应行", { exact: true })).toBeVisible();
    await expect(technical.getByText("须遵守", { exact: true })).toBeVisible();
    await expect(technical.getByText("缺口", { exact: true })).toBeVisible();
    await expect(technical.locator(".draft-preview").getByText("本次预检为确定性组表，不调用模型。", { exact: true })).toBeVisible();
    await technical.getByRole("button", { name: "确认生成初稿" }).click();
    await expect(technical.getByTestId("job-status")).toContainText("作业状态：已成功", {
      timeout: 60_000,
    });
    await expect(technical.getByText(/份$/).first()).toBeVisible();
    await expect(technical.getByText("有缺口", { exact: true }).first()).toBeVisible();
    const staleHistory = technical.getByRole("button").filter({ hasText: "已失效" }).first();
    await expect(staleHistory).toBeVisible();
    await staleHistory.click();
    await expect(technical.getByRole("alert")).toContainText("历史快照已经失效");
    await expect(technical.getByText(/负偏离/).first()).toBeVisible();
    await technical.getByRole("tab", { name: /技术响应/ }).click();
    await expect(technical.getByRole("link", { name: "回到审阅" }).first()).toBeVisible();

    const draft = await browserApi(
      technical,
      "GET",
      `/drafts/${fixture.review.drafts.current.id}`,
    );
    expect(draft.status).toBe(200);
    assertResult(draft.payload);
    expect(draft.payload.data.completion).toBe("partial");
    expect(draft.payload.data.validity).toBe("stale");
    const partition = [
      ...Object.values(draft.payload.data.tables).flat().map((row) => row.requirement_id),
      ...draft.payload.data.comply_only.map((row) => row.requirement_id),
      ...draft.payload.data.gaps.map((row) => row.requirement_id),
    ];
    expect(partition).toHaveLength(fixture.expectations.large_requirement_count);
    expect(new Set(partition).size).toBe(fixture.expectations.large_requirement_count);

    // A human-authorized run carries the exact preview hash.
    await technical.goto(reviewUrl);
    await choose(technical, "类别", "技术");
    await choose(technical, "状态", "缺卡片");
    await technical.getByTestId("requirement-row").getByRole("checkbox").first().check();
    await technical.getByText("模型起草预览", { exact: true }).click();
    await technical.getByRole("button", { name: "预检外发范围与配置" }).click();
    await expect(technical.getByRole("heading", { name: "本次预检" })).toBeVisible();
    const tokenEstimate = technical.locator(".stat").filter({
      has: technical.getByText("估算 tokens", { exact: true }),
    }).locator("strong");
    await expect(tokenEstimate).toBeVisible();
    await expect(tokenEstimate).toHaveText(/^[1-9]\d*$/);
    const inputHash = (await technical.locator(".details-list code").last().textContent()).trim();
    const runModel = technical.getByRole("button", { name: "确认并运行" });
    await expect(runModel).toBeDisabled();

    await technical.getByLabel(/授权按本次预检运行/).check();
    await expect(runModel).toBeEnabled();
    await runModel.click();
    await expect(technical.getByTestId("job-status")).toContainText("作业状态：已成功", {
      timeout: 30_000,
    });
    expect(modelRequests).toHaveLength(1);
    expect(modelRequests[0].expected_input_hash).toBe(inputHash);

    expect(modelRequests[0].requirement_ids).toHaveLength(1);

    // Verify actor attribution on human-confirmed card and draft
    const confirmedCard = await browserApi(
      technical,
      "GET",
      `/cards/${pending.id}`,
    );
    expect(confirmedCard.status).toBe(200);
    expect(confirmedCard.payload.data.actor_kind).toBe("session");
    expect(confirmedCard.payload.data.origin).toBe("human");
    expect(confirmedCard.payload.data.confirmed_at).toBeTruthy();

    // Token and agent denial: tokens cannot mint human-only scopes or confirm/export
    const forbiddenToken = await browserApi(technical, "POST", "/tokens", {
      name: "illegal-token",
      scopes: ["evidence:confirm", "export"],
      expires_at: new Date(Date.now() + 86400000).toISOString(),
    });
    expect(forbiddenToken.status).toBe(403);
    expect(forbiddenToken.payload.data.error.code).toBe("forbidden_scopes");

    const tokenRes = await browserApi(technical, "POST", "/tokens", {
      name: "e2e-scoped-token",
      scopes: ["card:write", "job:read"],
      expires_at: new Date(Date.now() + 86400000).toISOString(),
    });
    expect(tokenRes.status).toBe(200);
    const tokenSecret = tokenRes.payload.data.token;
    expect(tokenSecret).toMatch(/^bid_/);

    const tokenConfirm = await technical.evaluate(async ({ cardId, secret }) => {
      const res = await fetch(`/cards/${cardId}/actions`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${secret}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          action: "confirm",
          expected_revision: 1,
          reviewed_evidence_ids: [],
          reviewed_warning_codes: [],
          reason: "Token unauthorized confirm attempt",
        }),
      });
      let json = null;
      try { json = await res.json(); } catch {}
      return { status: res.status, json };
    }, { cardId: pending.id, secret: tokenSecret });
    expect(tokenConfirm.status).toBe(403);

    const tokenExport = await technical.evaluate(async ({ tId, draftId, secret }) => {
      const res = await fetch(`/tasks/${tId}/export-runs`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${secret}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          draft_id: draftId,
          task_template_id: tId,
          binding_id: tId,
          mode: "review_copy",
          dry_run: true,
        }),
      });
      let json = null;
      try { json = await res.json(); } catch {}
      return { status: res.status, json };
    }, { tId: taskId, draftId: fixture.review.drafts.current.id, secret: tokenSecret });
    expect(tokenExport.status).toBe(403);

    const result = sanitized({
      passed: true,
      fixture: fixture.fixture,
      task_id: taskId,
      extraction_job_id: extractionJobId,
      gates: [
        "real-api-discovery",
        "exact-pdf-word-citations",
        "revision-conflict",
        "atomic-comply-only",
        "partial-stale-drafts",
        "generation-dry-run-model-unauthorized",
        "generation-model-bound-run",
        "token-agent-denial",
        "actor-attribution",
      ],
      rerun: {
        command: "cd web && npx playwright test e2e/org-console.spec.js",
        environment: [
          "E2E_BASE_URL",
          "E2E_ORG_FIXTURE",
          "E2E_ACCESS_KEY",
          "E2E_OUTPUT",
        ],
      },
    });
    writeFileSync(join(output, "org-console-result.json"), JSON.stringify(result, null, 2));
    await technical.close();
  });

  test("online previews of tender, certificate and export pages", async ({ browser }) => {
    const previews = fixture.previews;
    const pdfName = previews.documents.pdf.name;
    const wordName = previews.documents.word.name;
    const exportName = previews.export.file.name;
    const bidder = await browser.newPage();
    const consoleErrors = [];
    bidder.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    await login(bidder);
    const firstPage = async (title) => {
      const dialog = bidder.getByRole("dialog", { name: title });
      await expect(dialog).toBeVisible({ timeout: 30_000 });
      const image = dialog.getByRole("img", { name: `${title} 第 1 页` });
      await expect(image).toBeVisible({ timeout: 60_000 });
      expect(await image.evaluate((element) => element.naturalWidth)).toBeGreaterThan(300);
      return dialog;
    };
    const close = async (title) => {
      await bidder.keyboard.press("Escape");
      await expect(bidder.getByRole("dialog", { name: title })).toBeHidden();
    };

    await bidder.goto(`/app/org/tasks/${previews.task.id}`);
    await expect(bidder.getByText(/状态：已解析/)).toBeVisible();
    // The automatic extraction dry run disables the document select until it returns.
    await expect(bidder.getByText(/预检结果：/)).toBeVisible();
    await choose(bidder, "文档", new RegExp(pdfName));
    await bidder.getByRole("button", { name: `在线预览：${pdfName}` }).click();
    const pdfDialog = await firstPage(pdfName);
    await expect(pdfDialog).toContainText("/ 3 页");
    await close(pdfName);
    await expect(bidder.getByText(/预检结果：/)).toBeVisible();
    await choose(bidder, "文档", new RegExp(wordName));
    await bidder.getByRole("button", { name: `在线预览：${wordName}` }).click();
    const wordDialog = bidder.getByRole("dialog", { name: wordName });
    await expect(wordDialog.getByText(/合成条款 C-0001/)).toBeVisible();
    await close(wordName);

    const exportTable = bidder.getByRole("table", { name: "已发布的导出文件" });
    await exportTable.getByRole("button", { name: `在线预览：${exportName}` }).click();
    const exportDialog = await firstPage(exportName);
    await bidder.screenshot({ path: join(output, "preview-export.png") });
    await expect(exportDialog).toContainText(/\/ \d+ 页/);
    await close(exportName);

    await bidder.goto(
      `/app/org/tasks/${previews.task.id}/review?job=${previews.extraction_id}&requirement=${previews.card_requirement_id}`,
    );
    await bidder.getByRole("button", { name: `在线查看原文位置：${pdfName}` }).click();
    await firstPage(pdfName);
    await close(pdfName);
    await bidder.getByText(/证书原件与页来源/).click();
    const certificate = bidder.getByRole("region", { name: "固定材料" });
    await certificate.getByRole("button", { name: "在线预览" }).first().click();
    await firstPage(`${previews.certificate.name} · synthetic-certificate.pdf`);
    await bidder.screenshot({ path: join(output, "preview-certificate.png") });
    expect(consoleErrors).toEqual([]);
    await bidder.close();

  });
});
