// Failure scenarios written before product UI implementation:
// - unbounded legacy preloads, search in URL/storage, cursor reuse across filters;
// - late org/search replies, wrong org/root/revision projections, oversized pages;
// - role escalation, admin nonmember, reviewer/observer, archived task writes;
// - implicit latest pin or upgrade, inactive replacement and duplicate semantics;
// - stale content/lifecycle CAS, automatic retry, discarded unsaved draft;
// - fabricated revision authors, merged lifecycle/content histories, inaccessible links;
// - missing keyboard/mobile actions, browser errors and irreproducible evidence.
import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from "node:fs";
import { basename, dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const work = join(root, "data/work");
const output = resolve(process.env.E2E_OUTPUT ?? join(work, "management-pages-validation/browser"));
function inside(base, path) { const rel = relative(base, path); return rel !== ".." && !rel.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`) && !rel.startsWith("/"); }
if (!inside(work, output) || !inside(join(work,"management-pages-validation"),output)) throw new Error("E2E_OUTPUT must be inside data/work/management-pages-validation");
let ancestor = output; while (!existsSync(ancestor)) ancestor = dirname(ancestor);
if (!inside(realpathSync(root), realpathSync(ancestor))) throw new Error("Artifact path resolves outside worktree");
const O="00000000-0000-0000-0000-000000000002", U="00000000-0000-0000-0000-000000000003", T="00000000-0000-0000-0000-000000000001", P="00000000-0000-0000-0000-000000000010", Q="00000000-0000-0000-0000-000000000011";
const rid=n=>`00000000-0000-0000-0000-${String(100+n).padStart(12,"0")}`;
const date="2026-10-06T00:00:00Z";
const cost={llm_tokens:0,ocr_pages:0,usd:0,basis:"zero",charge:"0",billing_currency:"USD",task_amount:"0",unpriced_calls:0,unresolved_calls:0};
const result=(command,data={},items=[],ok=true)=>({ok,command,data,items,warnings:[],cost,duration_ms:0});
const records=[],browserHealth=new WeakMap();
test.beforeEach(async({page})=>{
  const health=[];browserHealth.set(page,health);page.on("pageerror",error=>health.push(error.message));page.on("console",message=>{if(["error","warning"].includes(message.type())&&!message.text().startsWith("Failed to load resource"))health.push(message.text());});
  await page.addInitScript(({O,U})=>sessionStorage.setItem("bid.org.session",JSON.stringify({session:"synthetic-management-session",orgId:O,userId:U,orgName:"合成验收单位"})),{O,U});
  if(process.env.E2E_STATIC_DIR){const dir=resolve(process.env.E2E_STATIC_DIR);await page.route(url=>url.pathname.startsWith("/app/"),route=>{const path=new URL(route.request().url()).pathname;const file=path.startsWith("/app/assets/")?join(dir,"assets",basename(path)):join(dir,"index.html");return route.fulfill({contentType:{".html":"text/html",".js":"text/javascript",".css":"text/css"}[extname(file)]??"application/octet-stream",body:readFileSync(file)});});}
});
test.afterEach(async({page},info)=>{
  mkdirSync(output,{recursive:true});
  if(!inside(realpathSync(work),realpathSync(output)))throw new Error("Artifact symlink escapes data/work");
  const scenario=info.title.replace(/[^a-z0-9]+/gi,"-").toLowerCase();
  const health=browserHealth.get(page)??[];
  const overlay=await page.locator("vite-error-overlay").count();
  if(info.status==="passed"&&!health.length&&!overlay)await page.screenshot({path:join(output,`${scenario}.png`),fullPage:true});
  records.push({scenario:info.title,status:info.status==="passed"&&(health.length||overlay)?"failed":info.status,console_errors:health.length,framework_overlay:overlay,measurements:info.annotations.filter(item=>item.type==="measurement").map(item=>JSON.parse(item.description))});
  const index=process.env.E2E_STATIC_DIR?join(resolve(process.env.E2E_STATIC_DIR),"index.html"):null;
  writeFileSync(join(output,"result.json"),JSON.stringify({mode:"mocked_api",fixture_version:"product-management-v1",schema_version:"4.0",browser:"chromium",browser_version:page.context().browser()?.version()??null,build_sha256:index?createHash("sha256").update(readFileSync(index)).digest("hex"):null,base_url:process.env.E2E_BASE_URL??null,command:`cd web && E2E_BASE_URL=http://127.0.0.1:8000 ${index?'E2E_STATIC_DIR="$PWD/dist" ':''}E2E_OUTPUT=../data/work/management-pages-validation/browser node_modules/.bin/playwright test e2e/management-pages.spec.js`,scenarios:records,passed:records.every(r=>r.status==="passed")},null,2));
  expect(health,"Unexpected browser runtime or console error").toEqual([]);expect(overlay,"Framework error overlay").toBe(0);
});
async function fixture(page,options={}){
  const state={role:"technical",taskRole:"contributor",archived:false,inactive:false,head:2,lifecycleRevision:options.inactive?1:0,...options};
  const writes=[],queries=[],requests=[],pins=[];let conflictDone=false,delayedRelease;let queriesInFlight=0,maxQueries=0;
  const data=n=>({name:n===1?"产品旧版":"合成产品",vendor:"合成厂家",model:n===1?"M-1":"M-2",model_version:"2026",official_url:"https://vendor.example/spec",whitepaper_url:null});
  const revisions=new Map([[1,data(1)],[2,data(2)]]);
  const productData=n=>revisions.get(n)??data(n);
  const revision=n=>({id:rid(n),org_id:O,product_id:P,revision:n,data:productData(n)});
  const lifecycle=()=>({state:state.inactive?"inactive":"active",revision:state.lifecycleRevision});
  const actions=()=>["revise","deactivate","restore","select"].map(action=>({action,allowed:state.role==="admin"||state.role==="technical",...(["admin","technical"].includes(state.role)?{}:{reason:"role_required"})}));
  const meta=(items,next=null)=>({org_id:O,as_of:date,returned:items.length,next_cursor:next,has_more:!!next});
  const row=(id=P)=>({org_id:O,ref:{kind:"products",resource_id:id},name:id===P?productData(state.head).name:(state.created?.data.name??"后页产品"),revision_id:rid(state.head),revision:state.head,lifecycle:lifecycle(),provenance:state.simulated?"simulated":"declared",created_at:date,revised_at:date,revised_by:null,actions:actions()});
  await page.route(url=>!url.pathname.startsWith("/app/"),async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname.replace(/^\/v4(?=\/)/,"");requests.push({path,method:req.method(),url:url.pathname+url.search});
    if(path==="/org/current")return route.fulfill({json:result("org current",{org_id:O,user_id:U,role:state.role})});
    if(path===`/tasks/${T}/workflow`)return route.fulfill({json:result("task workflow",{workflow:{org_id:O,task_id:T,state:state.archived?"archived":"active",revision:1,last_event_cursor:"opaque",owner_user_id:U}})});
    if(path===`/tasks/${T}/members`){const items=state.taskRole?[{org_id:O,task_id:T,user_id:U,role:state.taskRole,active:true,review_domains:[],revision:1}]:[];return route.fulfill({json:result("task member list",{...meta(items),task_id:T},items)});}
    if(path==="/management/resources/products/query"){
      expect(url.pathname.startsWith("/v4/")).toBe(true);expect(req.method()).toBe("POST");expect(url.search).toBe("");const body=req.postDataJSON();queries.push(body);expect(body.limit).toBeLessThanOrEqual(100);queriesInFlight++;maxQueries=Math.max(maxQueries,queriesInFlight);
      if(state.delaySearch&&body.q==="旧查询"||state.delayEmptySearch&&body.q==="空结果")await new Promise(resolve=>{delayedRelease=resolve;});
      queriesInFlight--;if(state.queryError)return route.fulfill({status:413,json:result("resource query",{error:{code:"management_result_too_large",message:"Result too large",exit_code:2}},[],false)});
      const items=state.empty||body.q==="空结果"?[]:[row(body.cursor?Q:P),...(!body.cursor&&state.created?[{...row(Q),revision:1,revision_id:rid(20)}]:[])];if(state.foreign)items[0].org_id=Q;if(state.oversize)while(items.length<=100)items.push(row());
      return route.fulfill({json:result("resource query",meta(items,state.paginated&&!body.cursor?"opaque-next":null),items)});
    }
    if(path===`/management/resources/products/${P}`){const n=Number(url.searchParams.get("revision")??state.head);if(state.missing)return route.fulfill({status:404,json:result("resource show",{error:{code:"not_found",message:"Not found",exit_code:4}},[],false)});const detail={org_id:O,ref:{kind:"products",resource_id:P},current_revision:state.head,revised_at:date,revised_by:n===1?U:null,lifecycle:lifecycle(),provenance:state.simulated?"simulated":"declared",detail:{kind:"products",revision:revision(n)},actions:actions()};return route.fulfill({json:result("resource show",detail)});}
    if(path===`/management/resources/products/${P}/history/query`){const items=Array.from({length:state.head},(_,i)=>state.head-i).map(n=>({org_id:O,ref:{kind:"products",resource_id:P},revision_id:rid(n),revision:n,name:productData(n).name,created_at:date,created_by:n===1?U:null,current:n===state.head,has_file:false}));return route.fulfill({json:result("resource history",meta(items),items)});}
    if(path===`/management/resources/products/${P}/lifecycle/history/query`){const items=state.lifecycleRevision?[{id:Q,org_id:O,ref:{kind:"products",resource_id:P},revision:state.lifecycleRevision,resource_revision:state.head,before:state.inactive?"active":"inactive",after:state.inactive?"inactive":"active",reason_code:state.inactive?"obsolete":"restored",actor_user_id:U,actor_kind:"session",created_at:date}]:[];return route.fulfill({json:result("resource lifecycle history",meta(items),items)});}
    if(path===`/tasks/${T}/products`&&req.method()==="GET")return route.fulfill({json:result("task resource list",{org_id:O,task_id:T},pins)});
    if(req.method()==="POST"){
      const body=req.postDataJSON();writes.push({path,body});
      if(state.mutationDenied)return route.fulfill({status:403,json:result("resource write",{error:{code:"forbidden",message:"Forbidden",exit_code:4}},[],false)});
      if(path===`/resources/products/${P}/revisions`){if(state.conflict&&!conflictDone){conflictDone=true;state.head++;return route.fulfill({status:409,json:result("resource product update",{error:{code:"revision_conflict",message:"Changed",exit_code:2}},[],false)});}expect(body.expected_revision).toBe(state.head);state.head++;revisions.set(state.head,{...body.data});return route.fulfill({json:result("resource product update",revision(state.head))});}
      if(path==="/resources/products"){state.created={id:rid(20),org_id:O,product_id:Q,revision:1,data:{...body.data}};return route.fulfill({json:result("resource product add",state.created)});}
      if(path===`/management/resources/products/${P}/lifecycle`){if(state.lifecycleConflict){state.lifecycleConflict=false;state.lifecycleRevision++;state.inactive=true;return route.fulfill({status:409,json:result("resource lifecycle set",{error:{code:"lifecycle_conflict",message:"Changed",exit_code:2}},[],false)});}expect(body.expected_revision).toBe(state.head);expect(body.expected_lifecycle_revision).toBe(state.lifecycleRevision);state.lifecycleRevision++;state.inactive=body.state==="inactive";return route.fulfill({json:result("resource lifecycle set",{lifecycle:lifecycle(),existing_selections:"preserved",event:{id:Q,org_id:O,ref:{kind:"products",resource_id:P},revision:state.lifecycleRevision,resource_revision:state.head,before:state.inactive?"active":"inactive",after:body.state,reason_code:body.reason_code,actor_user_id:U,actor_kind:"session",created_at:date}})});}
      if(path===`/tasks/${T}/products`){expect(body.revision).toBeGreaterThan(0);const duplicate=pins.some(pin=>pin.product_revision_id===rid(body.revision)&&pin.lot===(body.lot??null));if(state.inactive&&!duplicate)return route.fulfill({status:409,json:result("task resource add",{error:{code:"resource_inactive",message:"Inactive",exit_code:2}},[],false)});const pin={id:Q,org_id:O,task_id:T,product_revision_id:rid(body.revision),revision:body.revision,lot:body.lot??null,data:productData(body.revision),duplicate,replaced_snapshot_id:duplicate?null:pins[0]?.id??null};if(!duplicate){pins.splice(0,pins.length,pin);}return route.fulfill({json:result("task resource add",pin)});}
    }
    throw new Error(`Unexpected product request: ${req.method()} ${path}`);
  });return {state,writes,queries,requests,pins,release:()=>delayedRelease?.(),maxQueries:()=>maxQueries};
}
test("bounded browse search paging and empty state",async({page})=>{
  const f=await fixture(page,{paginated:true,delayEmptySearch:true});
  await page.goto("/app/org/products");
  await expect(page.getByRole("heading",{name:"产品库",exact:true})).toBeVisible();
  await expect(page).toHaveTitle(/产品库.*单位后台/);
  await page.getByRole("button",{name:"下一页产品",exact:true}).click();
  await expect(page.getByText("后页产品",{exact:true})).toBeVisible();
  await page.getByLabel("搜索产品").fill("空结果");
  await expect.poll(()=>f.queries.at(-1)).toMatchObject({q:"空结果",cursor:null,limit:25});
  await expect(page.getByRole("status").filter({hasText:"正在读取产品"})).toBeVisible();
  await expect(page.getByText("没有符合条件的产品")).toHaveCount(0);
  await expect(page.getByRole("button",{name:"上一页产品",exact:true})).toBeDisabled();
  f.release();
  await expect(page.getByText("没有符合条件的产品")).toBeVisible();
  expect(f.queries.at(-1)).toMatchObject({q:"空结果",cursor:null,limit:25});
  expect(f.requests.some(r=>r.path==="/resources/products"&&r.method==="GET")).toBe(false);
  expect(await page.evaluate(()=>JSON.stringify({...sessionStorage,...localStorage}))).not.toContain("空结果");
  expect(page.url()).not.toContain("空结果");
});
test("create full declared product and server receipt",async({page})=>{const f=await fixture(page);await page.goto("/app/org/products");await page.getByRole("button",{name:"新增产品",exact:true}).click();await page.getByLabel("产品名称",{exact:true}).fill("新产品");await page.getByLabel("厂家",{exact:true}).fill("新厂家");await page.getByLabel("精确型号",{exact:true}).fill("M-3");await page.getByRole("button",{name:"保存产品",exact:true}).click();await expect(page.getByRole("status").filter({hasText:"已创建产品"})).toBeVisible();expect(f.writes[0].body.data).toMatchObject({name:"新产品",vendor:"新厂家",model:"M-3",official_url:null});});
test("exact historical revision provenance and separate histories",async({page})=>{await fixture(page,{simulated:true});await page.goto(`/app/org/products/${P}?revision=1`);await expect(page.getByRole("heading",{name:"产品旧版",exact:true})).toBeVisible();await expect(page.getByText("模拟拟投声明")).toBeVisible();await expect(page.getByText("内容修订 1 · 当前修订 2")).toBeVisible();await expect(page.getByRole("button",{name:"修订产品",exact:true})).toHaveCount(0);await page.getByRole("button",{name:"生命周期记录",exact:true}).click();await expect(page.getByText("暂无停用或恢复记录")).toBeVisible();});
test("revision conflict retains in-memory draft and deliberate retry",async({page})=>{const f=await fixture(page,{conflict:true});await page.goto(`/app/org/products/${P}`);await page.getByRole("button",{name:"修订产品",exact:true}).click();await page.getByLabel("产品名称",{exact:true}).fill("仅内存修订草稿");await page.getByRole("button",{name:"保存产品",exact:true}).click();await expect(page.getByRole("alert").filter({hasText:/草稿已保留/}).first()).toBeVisible();await expect(page.getByLabel("产品名称",{exact:true})).toHaveValue("仅内存修订草稿");expect(f.writes).toHaveLength(1);await page.getByRole("button",{name:"保存产品",exact:true}).click();await expect.poll(()=>f.writes.length).toBe(2);expect(f.writes[1].body.expected_revision).toBe(3);expect(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}))).not.toContain("仅内存修订草稿");});
test("lifecycle CAS preserves pins and content revisions",async({page})=>{const f=await fixture(page);await page.goto(`/app/org/products/${P}`);await page.getByRole("button",{name:"停用产品",exact:true}).click();await page.getByRole("button",{name:"确认停用",exact:true}).click();await expect(page.getByText("已停用",{exact:true}).first()).toBeVisible();expect(f.state.head).toBe(2);expect(f.writes[0].body).toMatchObject({expected_revision:2,expected_lifecycle_revision:0,state:"inactive"});await page.getByRole("button",{name:"恢复产品",exact:true}).click();await page.getByRole("button",{name:"确认恢复",exact:true}).click();await expect.poll(()=>f.writes.length).toBe(2);expect(f.writes[1].body.reason_code).toBe("restored");expect(f.writes[1].body.expected_lifecycle_revision).toBe(1);});
test("explicit old task pin remains old after library revision",async({page})=>{const f=await fixture(page);await page.goto(`/app/org/products/${P}?revision=1&task=${T}`);await page.getByRole("button",{name:"选择到任务",exact:true}).click();await page.getByLabel("分包（可选）").fill("包一");await page.getByRole("button",{name:"确认选择修订 1",exact:true}).click();await expect(page.getByRole("status").filter({hasText:"任务已固定修订 1"})).toBeVisible();expect(f.writes[0].body).toMatchObject({product_id:P,revision:1,lot:"包一"});expect(f.writes).toHaveLength(1);expect(f.pins[0].revision).toBe(1);});
for(const scenario of [{role:"technical",taskRole:"reviewer"},{role:"technical",taskRole:"observer"},{role:"admin",taskRole:null},{role:"technical",taskRole:"contributor",archived:true},{role:"viewer",taskRole:"contributor"}])test(`task pin denial ${scenario.role} ${scenario.taskRole} ${!!scenario.archived}`,async({page})=>{const f=await fixture(page,scenario);await page.goto(`/app/org/products/${P}?task=${T}`);await expect(page.getByRole("heading",{name:"合成产品",exact:true})).toBeVisible();await expect(page.getByText(scenario.archived?"任务已归档，只能读取已有记录。":"需要任务负责人或协作者并具备单位资源选择权限；审阅者、观察者和非成员管理员不能选择。")).toBeVisible();await expect(page.getByRole("button",{name:"选择到任务",exact:true})).toHaveCount(0);expect(f.writes).toHaveLength(0);});
test("inactive new selection blocked without a hidden restore",async({page})=>{const f=await fixture(page,{inactive:true});await page.goto(`/app/org/products/${P}?task=${T}`);await expect(page.getByText(/停用产品只允许核对已有的相同固定版本/)).toBeVisible();await page.getByRole("button",{name:"核对已有固定版本",exact:true}).click();await page.getByRole("button",{name:"确认选择修订 2",exact:true}).click();await expect(page.getByRole("alert").filter({hasText:/已停用/})).toBeVisible();expect(f.writes).toHaveLength(1);expect(f.writes[0].path).toBe(`/tasks/${T}/products`);});
test("inactive exact existing pin returns duplicate receipt",async({page})=>{const f=await fixture(page,{inactive:true});f.pins.push({id:Q,product_revision_id:rid(2),lot:null,revision:2});await page.goto(`/app/org/products/${P}?task=${T}`);await page.getByRole("button",{name:"核对已有固定版本",exact:true}).click();await page.getByRole("button",{name:"确认选择修订 2",exact:true}).click();await expect(page.getByRole("status").filter({hasText:"已有相同固定版本"})).toBeVisible();expect(f.writes).toHaveLength(1);});
test("uniform inaccessible view and foreign projection rejection",async({page})=>{await fixture(page,{foreign:true});await page.goto("/app/org/products");await expect(page.getByRole("alert").filter({hasText:/范围/})).toBeVisible();await expect(page.getByText("合成产品",{exact:true})).toHaveCount(0);});
test("oversized and failed pages have visible blockers",async({page})=>{await fixture(page,{queryError:true});await page.goto("/app/org/products");await expect(page.getByRole("alert").filter({hasText:/结果|Result/})).toBeVisible();await expect(page.getByRole("button",{name:"重新读取产品",exact:true})).toBeVisible();});
test("obsolete search cancelled and org reset clears content",async({page})=>{const f=await fixture(page,{delaySearch:true});await page.goto("/app/org/products");await page.getByLabel("搜索产品").fill("旧查询");await expect.poll(()=>f.queries.some(q=>q.q==="旧查询")).toBe(true);await page.getByLabel("搜索产品").fill("空结果");await expect(page.getByText("没有符合条件的产品")).toBeVisible();f.release();await page.evaluate(()=>window.dispatchEvent(new Event("bid:org-reset")));await expect(page.getByText("合成产品",{exact:true})).toHaveCount(0);expect(f.maxQueries()).toBeLessThanOrEqual(2);});
test("narrow screen keyboard and unsaved discard guard",async({page})=>{const f=await fixture(page);await page.setViewportSize({width:390,height:844});const errors=[];page.on("pageerror",error=>errors.push(error.message));await page.goto(`/app/org/products/${P}`);await page.getByRole("button",{name:"修订产品",exact:true}).click();await page.getByLabel("产品名称",{exact:true}).fill("尚未保存");await page.getByRole("button",{name:"取消编辑",exact:true}).click();await expect(page.getByRole("dialog").filter({hasText:"放弃未保存"})).toBeVisible();await page.getByRole("button",{name:"取消",exact:true}).last().click();await expect(page.getByLabel("产品名称",{exact:true})).toHaveValue("尚未保存");await page.getByLabel("产品名称",{exact:true}).press("Tab");expect(errors).toEqual([]);expect(f.writes).toHaveLength(0);});
test("oversized server projection is rejected rather than truncated",async({page})=>{await fixture(page,{oversize:true});await page.goto("/app/org/products");await expect(page.getByRole("alert").filter({hasText:/范围|边界/})).toBeVisible();await expect(page.getByText("合成产品",{exact:true})).toHaveCount(0);});
test("foreign or missing detail clears cached product identity",async({page})=>{await fixture(page,{missing:true});await page.goto(`/app/org/products/${P}`);await expect(page.getByRole("alert").filter({hasText:"不可访问"})).toBeVisible();await expect(page.getByRole("heading",{name:"合成产品",exact:true})).toHaveCount(0);await expect(page.getByRole("button",{name:"修订产品",exact:true})).toHaveCount(0);});
test("live membership downgrade blocks an open pin dialog",async({page})=>{
  const f=await fixture(page);
  await page.goto(`/app/org/products/${P}?task=${T}`);
  await page.getByRole("button",{name:"选择到任务",exact:true}).click();
  const dialog=page.getByRole("dialog",{name:"固定精确产品修订",exact:true});
  await expect(dialog).toBeVisible();
  f.state.taskRole="observer";
  await dialog.getByRole("button",{name:"确认选择修订 2",exact:true}).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByText(/需要任务负责人或协作者/)).toBeVisible();
  expect(f.writes).toHaveLength(0);
});
test("logout aborts a delayed query and discards old org text",async({page})=>{const f=await fixture(page,{delaySearch:true});await page.goto("/app/org/products");await page.getByLabel("搜索产品").fill("旧查询");await expect.poll(()=>f.queries.some(q=>q.q==="旧查询")).toBe(true);await page.getByRole("button",{name:"退出 / 切换单位",exact:true}).click();await expect(page).toHaveURL(/\/app\/org\/login/);f.release();await expect(page.getByText("合成产品",{exact:true})).toHaveCount(0);expect(await page.evaluate(()=>sessionStorage.getItem("bid.org.session"))).toBeNull();});
test("same product query navigation reloads exact revision and protects draft",async({page})=>{await fixture(page);await page.goto(`/app/org/products/${P}?revision=2`);await page.getByRole("button",{name:"修订产品",exact:true}).click();await page.getByLabel("产品名称",{exact:true}).fill("查询导航草稿");await page.getByRole("link",{name:"修订 1",exact:true}).click();await expect(page.getByRole("dialog").filter({hasText:"放弃未保存"})).toBeVisible();await page.getByRole("button",{name:"取消",exact:true}).last().click();await expect(page.getByLabel("产品名称",{exact:true})).toHaveValue("查询导航草稿");await page.getByRole("link",{name:"修订 1",exact:true}).click();await page.getByRole("button",{name:"放弃编辑",exact:true}).click();await expect(page).toHaveURL(/revision=1/);await expect(page.getByRole("heading",{name:"产品旧版",exact:true})).toBeVisible();await expect(page.getByText("内容修订 1 · 当前修订 2")).toBeVisible();});
test("authority denial during revision clears cached content and draft",async({page})=>{const f=await fixture(page);await page.goto(`/app/org/products/${P}`);await page.getByRole("button",{name:"修订产品",exact:true}).click();await page.getByLabel("产品名称",{exact:true}).fill("撤权草稿");f.state.mutationDenied=true;await page.getByRole("button",{name:"保存产品",exact:true}).click();await expect(page.getByRole("alert").filter({hasText:/无权/})).toBeVisible();await expect(page.getByLabel("产品名称",{exact:true})).toHaveCount(0);await expect(page.getByRole("heading",{name:"合成产品",exact:true})).toHaveCount(0);expect(f.writes).toHaveLength(1);});
test("lifecycle conflict refreshes state without automatic retry",async({page})=>{const f=await fixture(page,{lifecycleConflict:true});await page.goto(`/app/org/products/${P}`);await page.getByRole("button",{name:"停用产品",exact:true}).click();await page.getByRole("button",{name:"确认停用",exact:true}).click();await expect(page.getByRole("alert").filter({hasText:/版本已变化/})).toBeVisible();await expect(page.getByRole("button",{name:"恢复产品",exact:true})).toBeVisible();expect(f.writes).toHaveLength(1);});
test("fixed built list becomes actionable within browser budget",async({page},info)=>{
  await fixture(page);
  const started=performance.now();
  await page.goto("/app/org/products");
  await expect(page.getByRole("link",{name:"合成产品",exact:true})).toBeVisible();
  const actionableMs=performance.now()-started;
  info.annotations.push({type:"measurement",description:JSON.stringify({first_actionable_ms:Math.round(actionableMs),limit_ms:1500})});
  expect(actionableMs).toBeLessThanOrEqual(1500);
  expect(await page.locator(".el-table__body tbody tr").count()).toBeLessThanOrEqual(100);
});
