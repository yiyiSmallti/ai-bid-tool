// Failure scenarios specified before implementation: implicit extraction selection,
// unbounded filters/page fetches, invented progress, hidden-tab polling, unauthenticated
// SSE and duplicate event reload storms.
import { test, expect } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve, join, extname, basename } from "node:path";
import { openConsole } from "./access-key.js";
const output = resolve(process.env.E2E_OUTPUT ?? "../data/work/team-workflow-acceptance/browser");
const T = "00000000-0000-0000-0000-000000000001", U = "00000000-0000-0000-0000-000000000003", J = "00000000-0000-0000-0000-000000000004";
const K="00000000-0000-0000-0000-000000000005";
const session="synthetic-only-session";
const result = (command, data={}, items=[], ok=true) => ({ok,command,data,items,warnings:[],cost:{ calls: 0, llm_tokens: 0, ocr_pages: 0, search_requests: 0, browser_units: 0, unresolved_calls: 0 },duration_ms:0});
const workflow = () => ({task_id:T,state:"active",revision:1,last_event_cursor:"opaque-snapshot"});
const row = () => ({requirement_id:U,title:"合成验收要求",category:"technical",starred:true,bucket:"gap",card_id:null,card_revision:null,card_state:null,eligibility:null,flags:{human_needs_material:false,model_needs_material_hint:false,certificate_date_advisory:false,final_export_prototype_blocked:false},review_domain:"technical",blockers:["missing_card"],next_actions:[{code:"create_card",target:{kind:"requirement",id:U}}]});
test.beforeEach(async ({page}) => {
  if(process.env.E2E_STATIC_DIR) { const directory=resolve(process.env.E2E_STATIC_DIR); await page.route(url=>url.pathname.startsWith("/app/"),route=>{const pathname=new URL(route.request().url()).pathname;const file=pathname.startsWith("/app/assets/")?join(directory,"assets",basename(pathname)):join(directory,"index.html");return route.fulfill({contentType:{".html":"text/html",".js":"text/javascript",".css":"text/css"}[extname(file)]??"application/octet-stream",body:readFileSync(file)});}); }
});
async function fixture(page,{denial=false,streamFrames=null,paginated=false,boardFailures=0,boardRetryAfter=null,pollSnapshotFailure=false,uploadedDocument=false}={}) {
  let boards=0, streams=0, pollCalls=0;
  const state=workflow();
  const boardRow=row(), streamCheckpoints=[], boardQueries=[];
  await page.route(url=>!url.pathname.startsWith("/app/"),async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname;
    if(path==="/auth/session"&&request.method()==="POST"){expect(request.postDataJSON()).toEqual({access_key:process.env.E2E_ACCESS_KEY});return route.fulfill({json:result("auth session create",{session,expires_in:3600})});}
    expect(request.headers().authorization).toBe(`Bearer ${session}`);
    if(path==="/auth/session")return route.fulfill({json:result("auth session show",{actor_kind:"session",scopes:[],expires_at:null})});
    if(path===`/tasks/${T}`)return route.fulfill({json:result("task show",{id:T,name:"合成验收任务",model_redaction_enabled:true})});
    if(path===`/tasks/${T}/documents`)return route.fulfill({json:result("task document list",{task_id:T},uploadedDocument?[{id:K,task_id:T,name:"合成待解析招标文件.pdf",sha256:"a".repeat(64),media_type:"application/pdf",page_count:null,status:"uploaded",citation_mode:null,created_at:"2026-10-05T00:00:00Z"}]:[])});
    if(path===`/tasks/${T}/jobs`||path===`/tasks/${T}/exports`)return route.fulfill({json:result("task list",{},[])});
    if(uploadedDocument&&path===`/documents/${K}/extract`){expect(request.method()).toBe("POST");expect(request.postDataJSON()).toEqual({dry_run:true,retry:false});return route.fulfill({json:result("req extract",{dry_run:true,document_id:K,parsed:false,reasoning:null,reasoning_levels:[],input_blocker:"not_parsed"})});}
    if(path==="/confidential-fields"||path==="/confidential-values")return route.fulfill({json:result("confidential list",{},[])});
    if(path===`/tasks/${T}/extractions`)return route.fulfill({json:result("req history",{},uploadedDocument?[]:[{job_id:J,status:"succeeded"}])});
    if(path===`/tasks/${T}/events`){streams++;streamCheckpoints.push(request.headers()["last-event-id"]);expect(request.headers()["last-event-id"]).toBeTruthy();if(streamFrames)return route.fulfill({contentType:"text/event-stream",body:streamFrames(streams)});return route.fulfill({status:denial?403:503,json:result("task events",{error:{code:denial?"forbidden":"network_unavailable",message:"unavailable",exit_code:denial?4:3}},[],false)});}
    if(path===`/tasks/${T}/events/poll`){pollCalls++;const reset=pollSnapshotFailure&&pollCalls===1;if(reset){boardFailures=1;boardRetryAfter="8";}return route.fulfill({json:result("task events",{task_id:T,next_cursor:"opaque-next",head_cursor:"opaque-next",has_more:false,reset_required:reset?{type:"reset_required",reason:"retention_expired",head_cursor:"opaque-next"}:null})});}
    if(path===`/tasks/${T}/board`){boards++;boardQueries.push(Object.fromEntries(url.searchParams));expect(url.searchParams.get("extraction_job_id")).toBe(J);expect(Number(url.searchParams.get("limit"))).toBeLessThanOrEqual(100);if(boardFailures-->0)return route.fulfill({status:503,headers:boardRetryAfter?{"Retry-After":boardRetryAfter}:{},json:result("task board",{error:{code:"board_busy",message:"Snapshot unavailable",exit_code:3}},[],false)});const projection=result("task board",{task_id:T,extraction_job_id:J,counts:{total:1,gap:1,draft_card:0,pending_review:0,needs_material:0,confirmed:0,comply_only:0},matching:1,returned:1,next_cursor:paginated&&!url.searchParams.has("cursor")?"opaque-page2":null,event_cursor:"opaque-snapshot",jobs:[],activity:[],as_of:new Date().toISOString(),refresh_by:new Date(Date.now()+30000).toISOString()},[boardRow]);if(url.searchParams.get("view")==="requirement-review"){expect(url.pathname.startsWith("/tasks/")).toBe(true);const scope={task_id:T,extraction_job_id:J,document_id:K,origin:"model",revision:1,confirmation_sha256:"a".repeat(64),summary:{total:1,confirmed:1,unconfirmed:0,legacy_unconfirmed:0,invalidated:0,invalid_citations:0,rejected_reported:0,manual_added:0,extraction_completeness:"not_asserted"},last_event_cursor:"opaque-snapshot"};projection.data={board:projection.data,scope,buckets:{total:1,requirement_review:0,responses:projection.data.counts},requirement_activity:[]};projection.items=projection.items.map(response=>({response,requirement:{requirement_id:response.requirement_id,state:"confirmed",bucket:response.bucket,next_action:response.next_actions[0]?.code??"view",next_actor:null,blockers:[],counts_as_response_complete:response.bucket==="confirmed"||response.bucket==="comply_only"}}));}return route.fulfill({json:projection});}
    if(path===`/tasks/${T}/progress`)return route.fulfill({json:result("task progress",{task_id:T,event_cursor:"opaque-snapshot",next_cursor:null,as_of:new Date().toISOString(),returned:1},[{job_id:J,extraction_job_id:J,state:"running",run_id:null,attempts:1,progress:{completed:1,total:null},updated_at:new Date().toISOString()}])});
    return route.fulfill({status:404,json:result("task read",{error:{code:"not_found",message:"Not found",exit_code:4}},[],false)});
  });return {boards:()=>boards,streams:()=>streams,polls:()=>pollCalls,boardQueries,streamCheckpoints};
}
const frame = data => `data: ${JSON.stringify(data)}\n\n`;
test("explicit extraction, bounded filters and progress fallback",async({page})=>{
  const f=await fixture(page);await openConsole(page, `/org/tasks/${T}/board`);await expect(page.getByRole("heading",{name:"任务看板"})).toBeVisible();expect(f.boards()).toBe(0);
  await page.getByTestId("extraction-select").click();await page.getByRole("option").first().click();await expect(page.getByText("合成验收要求")).toBeVisible();await page.getByTestId("bucket-gap").click();await expect(page).toHaveURL(/bucket=gap/);await expect(page.getByText("创建响应卡")).toBeVisible();await expect(page.getByRole("button",{name:/会签|签署|审阅规则/})).toHaveCount(0);await expect(page.getByRole("button",{name:"分配与讨论",exact:true})).toHaveCount(0);
  await openConsole(page, `/org/tasks/${T}/progress`);await expect(page.getByText("已完成 1 项；总量未知")).toBeVisible();await expect(page.locator("[role=progressbar]")).toHaveCount(0);
  mkdirSync(output,{recursive:true});await page.screenshot({path:join(output,"progress-unknown.png"),fullPage:true});writeFileSync(join(output,"result.json"),JSON.stringify({passed:true,checks:["explicit-extraction","bounded-query","URL-filter","no-invented-progress","no-future-controls"]},null,2));
});
test("stream denial clears task data and stops retries",async({page})=>{const f=await fixture(page,{denial:true});await openConsole(page, `/org/tasks/${T}/board?job=${J}`);await expect(page.getByRole("alert")).toContainText("访问");await expect(page.getByText("合成验收要求")).toHaveCount(0);await page.waitForTimeout(1500);expect(f.streams()).toBe(1);});
test("duplicate event invalidation, reconnect, and expired cursor reset", async ({page}) => {
  const event={event_id:U,task_id:T,cursor:"opaque-next",payload:{type:"board_changed",invalidate_all:true},created_at:"2026-10-05T00:00:00Z"};
  const f=await fixture(page,{streamFrames:n=>n===1?frame(event)+frame(event):frame({type:"reset_required",reason:"retention_expired",head_cursor:"opaque-next"})});
  await openConsole(page, `/org/tasks/${T}/board?job=${J}`);await expect(page.getByText("合成验收要求")).toBeVisible();await expect.poll(f.streams).toBeGreaterThanOrEqual(2);await expect.poll(f.boards).toBeGreaterThanOrEqual(2);expect(f.boards()).toBeLessThan(6);
  await page.setViewportSize({width:390,height:844});await page.getByTestId("bucket-gap").focus();await page.keyboard.press("Enter");await expect(page).toHaveURL(/bucket=gap/);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  mkdirSync(output,{recursive:true});await page.screenshot({path:join(output,"board-mobile.png"),fullPage:true});
});
test("repeated transport failures enter bounded polling and session reset clears content",async({page})=>{
  const f=await fixture(page);await openConsole(page, `/org/tasks/${T}/board?job=${J}`);await expect(page.getByText("合成验收要求")).toBeVisible();await expect.poll(f.polls,{timeout:18000}).toBeGreaterThan(0);
  await page.evaluate(()=>window.dispatchEvent(new Event("bid:session-reset")));await expect(page.getByText("合成验收要求")).toHaveCount(0);const before=f.polls();await page.waitForTimeout(5500);expect(f.polls()).toBe(before);
});
test("server pagination resets its cursor after filters",async({page})=>{const f=await fixture(page,{paginated:true});await openConsole(page, `/org/tasks/${T}/board?job=${J}`);await expect(page.getByText("合成验收要求")).toBeVisible();await page.getByRole("button",{name:"下一页",exact:true}).click();await expect.poll(()=>f.boardQueries.at(-1).cursor).toBe("opaque-page2");await page.getByTestId("bucket-gap").click();await expect.poll(()=>f.boardQueries.at(-1).bucket).toBe("gap");expect(f.boardQueries.at(-1).cursor).toBeUndefined();});

test("initial snapshot failure retries before admitting an SSE connection",async({page})=>{
  const f=await fixture(page,{boardFailures:1});await openConsole(page, `/org/tasks/${T}/board?job=${J}`);await expect(page.getByRole("alert")).toContainText("board_busy");expect(f.streams()).toBe(0);await expect(page.getByRole("heading",{level:3,name:"★ 合成验收要求",exact:true})).toBeVisible();expect(f.boards()).toBeGreaterThan(1);await expect(page.getByRole("alert")).toHaveCount(0);await expect.poll(f.streams).toBeGreaterThan(0);expect(f.streamCheckpoints[0]).toBe("opaque-snapshot");expect(f.boards()).toBeLessThan(5);
});
test("session reset stops retrying an unavailable first board snapshot",async({page})=>{
  const f=await fixture(page,{boardFailures:99});await openConsole(page, `/org/tasks/${T}/board?job=${J}`);await expect(page.getByRole("alert")).toContainText("board_busy");await page.evaluate(()=>window.dispatchEvent(new Event("bid:session-reset")));const reads=f.boards();await page.waitForTimeout(1500);expect(f.boards()).toBe(reads);expect(f.streams()).toBe(0);
});

test("failed snapshot admission honors Retry-After before its next read",async({page})=>{
  const f=await fixture(page,{boardFailures:1,boardRetryAfter:"2"});await openConsole(page, `/org/tasks/${T}/board?job=${J}`);await expect(page.getByRole("alert")).toContainText("board_busy");await page.waitForTimeout(1100);expect(f.boards()).toBe(1);expect(f.streams()).toBe(0);await expect(page.getByRole("heading",{level:3,name:"★ 合成验收要求",exact:true})).toBeVisible();expect(f.boards()).toBeGreaterThan(1);await expect(page.getByRole("alert")).toHaveCount(0);await expect.poll(f.streams).toBeGreaterThan(0);expect(f.streamCheckpoints[0]).toBe("opaque-snapshot");
});

test("poll reset waits for a rate-limited fresh snapshot before polling again",async({page})=>{
  const f=await fixture(page,{pollSnapshotFailure:true});await openConsole(page, `/org/tasks/${T}/board?job=${J}`);await expect.poll(f.polls,{timeout:18000}).toBeGreaterThan(0);await expect(page.getByRole("alert")).toContainText("board_busy");await page.waitForTimeout(5200);expect(f.boards()).toBe(2);expect(f.polls()).toBe(1);await expect(page.getByRole("heading",{level:3,name:"★ 合成验收要求",exact:true})).toBeVisible({timeout:12000});expect(f.boards()).toBeGreaterThan(2);await expect(page.getByRole("alert")).toHaveCount(0);await expect.poll(f.polls).toBeGreaterThan(1);expect(f.streamCheckpoints.every(Boolean)).toBe(true);
});
test("task entry exposes upload and parse work to the single human",async({page})=>{await fixture(page,{uploadedDocument:true});await openConsole(page, `/org/tasks/${T}`);await expect(page.getByRole("heading",{name:"合成验收任务",exact:true})).toBeVisible();await expect(page.locator("form.upload")).toBeVisible();await expect(page.getByRole("button",{name:"上传文件",exact:true})).toBeVisible();await expect(page.getByRole("button",{name:"开始解析",exact:true})).toBeVisible();await expect(page.getByRole("button",{name:"开始解析",exact:true})).toBeEnabled();await expect(page.getByText("预检结果：文档尚未解析",{exact:false})).toBeVisible();await expect(page.getByRole("button",{name:"开始抽取",exact:true})).toBeDisabled();await expect(page.getByRole("alert")).toHaveCount(0);});
