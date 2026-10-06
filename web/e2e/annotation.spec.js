// Failure scenarios specified before implementation: no implicit submission/attachment,
// 409 preserves geometry but clears preview, missing role/B02/co-sign stays separate,
// cancel/failed job never exposes a candidate, source/org race cannot repaint,
// image requests and cursor pages are bounded, signed links are never persisted.
import { expect, test } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { annotationFixture, ids } from "./annotation-fixture.js";
test.use({trace:"on"});
const output=resolve("../data/work/annotation-acceptance");
const path=`/app/org/tasks/${ids.task}/cards/${ids.card}/annotation?job=${ids.extract}`;
async function select(page){await page.getByRole("button",{name:"选择证书页：合成证书.pdf 第 1 页",exact:true}).click();await expect(page.getByAltText("证书来源原页")).toBeVisible();}
async function artifact(page,state,name){state.verify();mkdirSync(output,{recursive:true});await page.screenshot({path:join(output,`${name}.png`),fullPage:true});writeFileSync(join(output,`${name}.json`),JSON.stringify({seed:"annotation-mocked-v1",scenario:name,requests:state.requests,max_image_requests:state.maxImages,checks:["bounded-pages","explicit-reviewed-hashes","no-worker-card-write","no-paid-requests"],replay:"E2E_BASE_URL=http://127.0.0.1:8000 E2E_STATIC_DIR=$PWD/web/dist npm --prefix web run e2e -- annotation.spec.js --output=../data/work/annotation-acceptance/playwright"},null,2));}
test("explicit preview creates actual candidate then human saves exact content-region evidence", async ({ page }) => {
  const state = await annotationFixture(page);
  await page.goto(path);
  await select(page);
  await page.getByLabel("裁剪横坐标", { exact: true }).fill("7");
  await page.getByLabel("裁剪纵坐标", { exact: true }).fill("9");
  await page.getByLabel("裁剪宽度", { exact: true }).fill("80");
  await page.getByLabel("裁剪高度", { exact: true }).fill("70");
  await expect(page.getByRole("button", { name: "生成标注材料", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "预览标注", exact: true }).click();
  await expect(page.getByTestId("annotation-plan-preview")).toBeVisible();
  expect(state.submits).toBe(0);
  await page.getByRole("checkbox", { name: "已核对原页与标注范围", exact: true }).check();
  await page.getByRole("button", { name: "生成标注材料", exact: true }).click();
  await expect(page.getByAltText("实际标注候选图")).toBeVisible();
  expect(state.cardWrites).toBe(0);
  await page.getByRole("button", { name: "用于此响应卡", exact: true }).click();
  const editor = page.getByRole("dialog", { name: "关联标注材料与响应审阅", exact: true });
  await expect(editor.getByRole("textbox", { name: "视觉观察说明", exact: true })).toBeVisible();
  expect(state.cardWrites).toBe(0);
  const response = "提供所选证书原件页，核对名称及有效范围后响应本要求。";
  const deviation = "证书页的名称及有效范围与本要求对应，标注框用于定位核对内容。";
  const observation = "人工核对证书名称和原件页一致";
  await editor.getByRole("textbox", { name: "响应正文", exact: true }).fill(response);
  await editor.getByRole("textbox", { name: "对应关系或具体偏离说明", exact: true }).fill(deviation);
  await editor.getByRole("textbox", { name: "视觉观察说明", exact: true }).fill(observation);
  await editor.getByRole("button", { name: "加入本次响应编辑", exact: true }).click();
  await expect(editor.getByText("image_region", { exact: false }).first()).toBeVisible();
  expect(state.cardWrites).toBe(0);
  await expect(editor.getByRole("textbox", { name: "响应正文", exact: true })).toHaveText(response);
  await expect(editor.getByRole("textbox", { name: "对应关系或具体偏离说明", exact: true })).toHaveText(deviation);
  await editor.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect.poll(() => state.cardWrites).toBe(1);
  await expect(editor.getByText("操作成功", { exact: true })).toBeVisible();
  const writes = state.requests.filter(item => item.method === "PUT" && item.path === `/cards/${ids.card}`);
  expect(writes).toHaveLength(1);
  expect(writes[0].body.expected_revision).toBe(1);
  expect(writes[0].body.content).toMatchObject({ response_kind: "evidence", response_text: response, deviation: "none", deviation_note: deviation });
  expect(writes[0].body.content.evidence).toHaveLength(1);
  expect(writes[0].body.content.evidence[0]).toMatchObject({
    kind: "image_region", asset_id: ids.asset, rendition_id: ids.rendition,
    region: { x: 0, y: 0, width: 80, height: 70 },
    claim_scope: "document_excerpt", visual_observation: observation,
  });
  expect(state.requests.filter(item => item.method === "POST" && /actions|signoffs/.test(item.path))).toEqual([]);
  await expect(editor.getByRole("textbox", { name: "响应正文", exact: true })).toHaveText(response);
  await expect(editor.getByRole("textbox", { name: "对应关系或具体偏离说明", exact: true })).toHaveText(deviation);
  await artifact(page, state, "explicit-candidate-save");
});
test("conflict keeps numeric geometry and requires fresh preview",async({page})=>{const state=await annotationFixture(page,{conflict:true});await page.goto(path);await select(page);await page.getByLabel("裁剪宽度",{exact:true}).fill("80");await page.getByRole("button",{name:"预览标注",exact:true}).click();await page.getByRole("checkbox",{name:"已核对原页与标注范围",exact:true}).check();await page.getByRole("button",{name:"生成标注材料",exact:true}).click();await expect(page.getByRole("alert").first()).toContainText("未自动重试");await expect(page.getByLabel("裁剪宽度",{exact:true})).toHaveValue("80");await expect(page.getByRole("button",{name:"生成标注材料",exact:true})).toBeDisabled();expect(state.submits).toBe(1);await artifact(page,state,"conflict-retains-geometry");});
for(const taskRole of ["reviewer","observer"])test(`${taskRole} reads without annotation writes`,async({page})=>{const state=await annotationFixture(page,{taskRole});await page.goto(path);await select(page);await expect(page.getByRole("button",{name:"预览标注",exact:true})).toBeDisabled();expect(state.submits).toBe(0);await artifact(page,state,`readonly-${taskRole}`);});
test("B02 and partial co-sign remain distinct from rendering and release",async({page})=>{const state=await annotationFixture(page,{requirementState:"unconfirmed",cosign:"partial"});await page.goto(path);await expect(page.getByTestId("annotation-requirement-state")).toContainText("要求尚未确认");await expect(page.getByTestId("annotation-cosign-state")).toContainText("待签署：commercial");await expect(page.getByTestId("annotation-release-state")).toContainText("未生成确认图");await artifact(page,state,"independent-gates");});
for(const status of ["failed","cancelled"])test(`${status} job exposes no attachable material`,async({page})=>{const state=await annotationFixture(page,{jobStatus:status});await page.goto(path);await select(page);await page.getByRole("button",{name:"预览标注",exact:true}).click();await page.getByRole("checkbox",{name:"已核对原页与标注范围",exact:true}).check();await page.getByRole("button",{name:"生成标注材料",exact:true}).click();await expect(page.getByTestId("annotation-job-state")).toContainText(status);await expect(page.getByRole("button",{name:"用于此响应卡",exact:true})).toHaveCount(0);await artifact(page,state,`job-${status}`);});
test("org reset cancels source reads and clears active editor input",async({page})=>{const state=await annotationFixture(page);await page.goto(path);await select(page);await page.evaluate(()=>window.dispatchEvent(new Event("bid:org-reset")));await expect(page.getByAltText("证书来源原页")).toHaveCount(0);await expect(page.getByLabel("裁剪宽度",{exact:true})).toHaveCount(0);expect(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}))).not.toContain("visual_observation");await artifact(page,state,"org-reset");});
test("initiator explicitly cancels running annotation without material publication",async({page})=>{
  const state=await annotationFixture(page,{jobStatus:"running"});
  await page.goto(path);
  await select(page);
  await page.getByRole("button",{name:"预览标注",exact:true}).click();
  await page.getByRole("checkbox",{name:"已核对原页与标注范围",exact:true}).check();
  await page.getByRole("button",{name:"生成标注材料",exact:true}).click();
  await expect(page.getByTestId("annotation-job-state")).toContainText("running");
  await expect(page.getByAltText("实际标注候选图")).toHaveCount(0);
  await page.getByRole("button",{name:"取消标注作业",exact:true}).click();
  await expect(page.getByTestId("annotation-job-state")).toContainText("cancelled");
  await expect(page.getByRole("button",{name:"取消标注作业",exact:true})).toBeDisabled();
  await expect(page.getByAltText("实际标注候选图")).toHaveCount(0);
  await expect(page.getByRole("button",{name:"用于此响应卡",exact:true})).toHaveCount(0);
  expect(state.submits).toBe(1);
  expect(state.cancels).toBe(1);
  expect(state.cardWrites).toBe(0);
  expect(state.requests.filter(row=>row.path.startsWith("/annotations/")||row.path.startsWith("/annotation-releases/"))).toEqual([]);
  await artifact(page,state,"explicit-cancel");
});
test("keyboard geometry edit invalidates reviewed preview",async({page})=>{const state=await annotationFixture(page);await page.goto(path);await select(page);await page.getByLabel("裁剪宽度",{exact:true}).fill("80");await page.getByRole("button",{name:"预览标注",exact:true}).click();await page.getByRole("checkbox",{name:"已核对原页与标注范围",exact:true}).check();const width=page.getByLabel("裁剪宽度",{exact:true});await width.focus();await width.press("ArrowUp");await expect(width).toHaveValue("81");await expect(page.getByRole("button",{name:"生成标注材料",exact:true})).toBeDisabled();expect(state.submits).toBe(0);await artifact(page,state,"keyboard-invalidates-preview");});
test("failed release retry uses complete exact approval pin without new confirmation",async({page})=>{const state=await annotationFixture(page,{existingCandidate:true,failedRelease:true});await page.goto(path);await page.getByRole("button",{name:`核对标注图 ${ids.annotation}`,exact:true}).click();await expect(page.getByAltText("实际标注候选图")).toBeVisible();await page.getByRole("button",{name:"重试确认图生成",exact:true}).click();await expect.poll(()=>state.releaseRetries).toBe(1);expect(state.cardWrites).toBe(0);expect(state.requests.filter(row=>/actions|signoffs/.test(row.path)&&row.method==="POST")).toEqual([]);await artifact(page,state,"exact-release-retry");});
test("stale confirmed release stays historical without preview or export fallback",async({page})=>{
  const state=await annotationFixture(page,{existingCandidate:true,staleRelease:true});
  await page.goto(path);
  await page.getByRole("button",{name:`核对标注图 ${ids.annotation}`,exact:true}).click();
  await expect(page.getByText("历史确认图不可用于导出",{exact:false})).toBeVisible();
  await expect(page.getByRole("button",{name:"核对确认图：已确认标注图",exact:true})).toHaveCount(0);
  await expect(page.getByRole("button",{name:"重新核对确认图",exact:true})).toBeVisible();
  expect(state.requests.filter(row=>row.path.startsWith("/annotation-releases"))).toEqual([]);
  await artifact(page,state,"stale-release");
});
test("delayed source response cannot repaint after org reset",async({page})=>{const state=await annotationFixture(page,{sourceDelay:true});await page.goto(path);await page.getByRole("button",{name:"选择证书页：合成证书.pdf 第 1 页",exact:true}).click();await expect.poll(()=>state.images).toBe(1);await page.evaluate(()=>window.dispatchEvent(new Event("bid:org-reset")));await expect(page.getByAltText("证书来源原页")).toHaveCount(0);await expect.poll(()=>state.images).toBe(0);await expect(page.getByAltText("证书来源原页")).toHaveCount(0);await artifact(page,state,"delayed-source-reset");});

test("release status outage preserves the inspected candidate and explicit attachment",async({page})=>{const state=await annotationFixture(page,{existingCandidate:true,releaseJobsUnavailable:true});await page.goto(path);await page.getByRole("button",{name:`核对标注图 ${ids.annotation}`,exact:true}).click();await expect(page.getByAltText("实际标注候选图")).toBeVisible();await expect(page.getByRole("alert").first()).toContainText("确认图状态暂时无法读取");await expect(page.getByRole("button",{name:"用于此响应卡",exact:true})).toBeEnabled();expect(state.cardWrites).toBe(0);await artifact(page,state,"auxiliary-job-read-failure");});
