// Failure cases specified before UI implementation: legacy unbounded preloads; wrong
// org/root/exact revision/file; invalid cursor/oversize; lost draft or automatic CAS
// retry; stale authority, observer/nonmember admin/archived pin; silent latest pin;
// inherited old original after metadata save; reordered parts/rotation lost; upload
// failure or unknown outcome claimed successful; signed file bypass; date advice
// mistaken for authenticity; confidential values requested or stored; late reset data.
import { test, expect } from '@playwright/test';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { basename, dirname, extname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..'),work=join(root,'data/work');
// Each suite gets its own child directory during combined acceptance runs.
const artifactRoot=join(work,'management-pages-validation');
const outputBase=resolve(process.env.E2E_OUTPUT??join(artifactRoot,'browser'));
const output=join(outputBase,'qualifications');
function inside(base,path){const rel=relative(base,path);return rel!=='..'&&!rel.startsWith(`..${process.platform==='win32'?'\\':'/'}`)&&!rel.startsWith('/');}
if(!inside(work,outputBase)||!inside(artifactRoot,outputBase)||outputBase===artifactRoot)throw new Error('E2E_OUTPUT must be a run directory inside data/work/management-pages-validation');
let ancestor=output;while(!existsSync(ancestor))ancestor=dirname(ancestor);
if(!inside(realpathSync(root),realpathSync(ancestor)))throw new Error('Artifact ancestor escapes worktree');
const quote=value=>`'${value.replace(/'/g,`'\\''`)}'`;

const O='00000000-0000-0000-0000-000000000002',U='00000000-0000-0000-0000-000000000003',T='00000000-0000-0000-0000-000000000001',P='00000000-0000-0000-0000-000000000010',C='00000000-0000-0000-0000-000000000011';
const B='00000000-0000-0000-0000-000000000099';
const rid=(kind,n)=>`00000000-0000-0000-0000-${String((kind==='profiles'?100:200)+n).padStart(12,'0')}`;
const date='2026-10-06T00:00:00Z',cost={llm_tokens:0,ocr_pages:0,usd:0,basis:'zero',charge:'0',billing_currency:'USD',task_amount:'0',unpriced_calls:0,unresolved_calls:0};
const result=(command,data={},items=[],ok=true)=>({ok,command,data,items,warnings:[],cost,duration_ms:0});
const records=[],health=new WeakMap();
test.beforeEach(async({page})=>{
 const errors=[];health.set(page,errors);page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(['error','warning'].includes(m.type())&&!m.text().startsWith('Failed to load resource'))errors.push(m.text());});
 await page.addInitScript(({O,U})=>sessionStorage.setItem('bid.org.session',JSON.stringify({session:'synthetic-qualification-session',orgId:O,userId:U,orgName:'合成验收单位'})),{O,U});
 if(process.env.E2E_STATIC_DIR){const dir=resolve(process.env.E2E_STATIC_DIR);await page.route(url=>url.pathname.startsWith('/app/'),route=>{const path=new URL(route.request().url()).pathname,file=path.startsWith('/app/assets/')?join(dir,'assets',basename(path)):join(dir,'index.html');return route.fulfill({contentType:{'.html':'text/html','.js':'text/javascript','.css':'text/css'}[extname(file)]??'application/octet-stream',body:readFileSync(file)});});}
});
test.afterEach(async({page},info)=>{
 let ancestor=output;while(!existsSync(ancestor))ancestor=dirname(ancestor);
 if(!inside(realpathSync(root),realpathSync(ancestor)))throw new Error('Artifact ancestor escapes worktree');
 mkdirSync(output,{recursive:true});if(!inside(realpathSync(work),realpathSync(output))||!inside(realpathSync(artifactRoot),realpathSync(output)))throw new Error('Artifact symlink escapes management-pages-validation');
 const errors=health.get(page)??[],overlay=await page.locator('vite-error-overlay').count();
 if(info.status==='passed'&&!errors.length&&!overlay)await page.screenshot({path:join(output,`${info.title.replace(/[^a-z0-9]+/gi,'-').toLowerCase()}.png`),fullPage:true});
 records.push({scenario:info.title,status:info.status==='passed'&&(errors.length||overlay)?'failed':info.status,console_errors:errors.length,framework_overlay:overlay,measurements:info.annotations.filter(item=>item.type==='measurement').map(item=>JSON.parse(item.description))});
 const index=process.env.E2E_STATIC_DIR?join(resolve(process.env.E2E_STATIC_DIR),'index.html'):null;
 writeFileSync(join(output,'result.json'),JSON.stringify({mode:'mocked_api',fixture_version:'qualification-management-v1',schema_version:'4.0',browser:'chromium',browser_version:page.context().browser()?.version()??null,build_sha256:index?createHash('sha256').update(readFileSync(index)).digest('hex'):null,base_url:process.env.E2E_BASE_URL??null,artifact_directory:output,command:`cd web && E2E_BASE_URL=${quote(process.env.E2E_BASE_URL??'http://127.0.0.1:8000')} ${index?`E2E_STATIC_DIR=${quote(dirname(index))} `:''}E2E_OUTPUT=${quote(outputBase)} node_modules/.bin/playwright test e2e/qualification-management.spec.js`,scenarios:records,passed:records.every(r=>r.status==='passed')},null,2));
 expect(errors).toEqual([]);expect(overlay).toBe(0);
});
async function fixture(page,options={}){
 const state={role:'bidder',taskRole:'contributor',head:2,inactive:false,life:0,...options},writes=[],requests=[],queries=[],pins=[],data=new Map();let conflict=false,release;const reads={active:0,max:0},gates=new Map(),totals={pageBytes:0,pageResponses:0};
 const rootId=kind=>kind==='profiles'?P:C;
 const baseData=(kind,n)=>kind==='profiles'?{name:n===1?'资料旧版':'合成资料',registration_details:'注册声明 {{secret.bank_account}}',performance_summary:null,standard_wording:'标准声明'}:{kind:'qualification',name:n===1?'证照旧版':'合成证照',number:'SYN-001',valid_from:'2026-01-01',valid_until:'2026-10-05'};
 const content=(kind,n)=>data.get(`${kind}:${n}`)??baseData(kind,n);
 const revision=(kind,n)=>({id:rid(kind,n),org_id:O,[kind==='profiles'?'profile_id':'certificate_id']:rootId(kind),revision:n,data:content(kind,n)});
 const lifecycle=()=>({state:state.inactive?'inactive':'active',revision:state.life});
 const actions=()=>['revise','deactivate','restore','select'].map(action=>({action,allowed:['admin','bidder'].includes(state.role),...(['admin','bidder'].includes(state.role)?{}:{reason:'role_required'})}));
 const meta=(items,next=null)=>({org_id:O,as_of:date,returned:items.length,next_cursor:next,has_more:!!next});
 const files=new Map();
 const file=(n)=>({id:rid('certificates',n+500),org_id:O,certificate_id:C,certificate_revision_id:rid('certificates',n),revision:n,data:content('certificates',n),file:{name:'synthetic.pdf',sha256:'a'.repeat(64),size_bytes:123,page_count:2,media_type:'application/pdf'},parts:[]});
 files.set(1,file(1));if(options.currentFile)files.set(2,file(2));
 await page.route(url=>!url.pathname.startsWith('/app/'),async route=>{
  const req=route.request(),url=new URL(req.url()),path=url.pathname.replace(/^\/v4(?=\/)/,'');const requestOrg=req.headers()['x-org-id']??O;requests.push({path,method:req.method(),org_id:requestOrg,url:url.pathname+url.search});
  const read=req.method()==='GET'||req.method()==='POST'&&path.startsWith('/management/')&&path.endsWith('/query');
  if(read){reads.active++;reads.max=Math.max(reads.max,reads.active);}
  try{
  if(options.readGates?.includes(path))await new Promise(resolve=>gates.set(path,resolve));
  if(options.allowOrgSwitch&&path==='/auth/orgs')return route.fulfill({json:result('auth orgs',{},[{org_id:O,name:'合成验收单位',active:true},{org_id:B,name:'合成乙单位',active:true}])});
  if(options.allowOrgSwitch&&path==='/auth/login'){expect(req.postDataJSON().org_id).toBe(B);return route.fulfill({json:result('auth login',{session:'synthetic-org-b-session'})});}
  if(options.allowOrgSwitch&&requestOrg===B){
   expect(req.headers().authorization).toBe('Bearer synthetic-org-b-session');
   if(path==='/org/current')return route.fulfill({json:result('org current',{org_id:B,user_id:U,role:state.role})});
   if(path==='/tasks')return route.fulfill({json:result('task list',{org_id:B},[])});
   const match=path.match(/^\/management\/resources\/(profiles|certificates)\/query$/);
   if(match){const kind=match[1],items=[{org_id:B,ref:{kind,resource_id:rid(kind,8000)},name:kind==='profiles'?'乙单位专属资料':'乙单位专属证照',revision_id:rid(kind,8001),revision:1,lifecycle:{state:'active',revision:0},provenance:'declared',created_at:date,revised_at:date,revised_by:U,actions:actions()}];return route.fulfill({json:result('resource query',{org_id:B,as_of:date,returned:items.length,next_cursor:null,has_more:false},items)});}
   if(req.method()==="GET"&&path==="/health")return route.fulfill({json:{ok:true,command:"health",data:{status:"ok",version:"4.0",real_llm_configured:false,org_signup_enabled:false},items:[],warnings:[],cost:{llm_tokens:0,ocr_pages:0,usd:0},duration_ms:0}});
   throw new Error(`Unexpected org B request: ${req.method()} ${path}`);
  }
  const failure=(code,status=409)=>route.fulfill({status,json:result('resource write',{error:{code,message:code,exit_code:status===403||status===404?4:2}},[],false)});
  if(path==='/org/current')return route.fulfill({json:result('org current',{org_id:O,user_id:U,role:state.role})});
  if(path==='/confidential-fields')return route.fulfill({json:result('confidential field list',{},[{key:'bank_account',label:'开户账号',scope:'org'}])});
  if(path===`/tasks/${T}/workflow`)return route.fulfill({json:result('task workflow',{workflow:{org_id:O,task_id:T,state:state.archived?'archived':'active'}})});
  if(path===`/tasks/${T}/members`){const items=state.taskRole?[{org_id:O,task_id:T,user_id:U,role:state.taskRole,active:true,review_domains:[]}]:[];return route.fulfill({json:result('task members',{...meta(items),task_id:T},items)});}
  const match=path.match(/^\/management\/resources\/(profiles|certificates)(?:\/(.*))?$/);
  if(match){expect(url.pathname.startsWith('/v4/')).toBe(true);const kind=match[1],tail=match[2],id=rootId(kind);
   if(tail==='query'){const body=req.postDataJSON();queries.push({kind,...body});expect(url.search).toBe('');if(state.delay&&body.q==='迟到查询')await new Promise(r=>{release=r;});if(state.cursorError&&body.cursor)return failure('management_cursor_expired');let items=body.q==='空结果'?[]:[{org_id:state.foreign?'00000000-0000-0000-0000-000000000099':O,ref:{kind,resource_id:id},name:content(kind,state.head).name,revision_id:rid(kind,state.head),revision:state.head,lifecycle:lifecycle(),provenance:'declared',created_at:date,revised_at:date,revised_by:U,actions:actions()}];let next=state.paginated&&!body.cursor?'opaque-next':null;
    if(state.largePages&&kind==='profiles'){
     const index=body.cursor===null?0:Number(body.cursor.replace('opaque-page-',''));expect(body.limit).toBe(25);
     const names=['create','revise','deactivate','restore','select','upload','download','bind','configure','test','propose','approve','reject','disable','set_value','reveal'];
     items=Array.from({length:25},(_,i)=>({org_id:O,ref:{kind,resource_id:rid(kind,10000+index*25+i)},name:`分页${String(index).padStart(3,'0')}-${i} `+'声明'.repeat(93),revision_id:rid(kind,20000+index*25+i),revision:1,lifecycle:lifecycle(),provenance:'declared',created_at:date,revised_at:date,revised_by:U,actions:names.map(action=>({action,allowed:false,reason:'task_access_required'}))}));
     next=index<state.largePages-1?`opaque-page-${index+1}`:null;
    }
    if(state.oversize)while(items.length<=100)items.push(items[0]);const payload=result('resource query',meta(items,next),items),bytes=Buffer.byteLength(JSON.stringify(payload));if(!state.oversize)expect(bytes).toBeLessThanOrEqual(256*1024);totals.pageBytes+=bytes;totals.pageResponses++;return route.fulfill({json:payload});}
   if(tail===id){if(state.missing)return failure('not_found',404);const n=Number(url.searchParams.get('revision')??state.head),detail={kind,revision:revision(kind,n)};if(kind==='certificates')detail.file=files.get(n)??null;if(state.wrongFile&&detail.file)detail.file={...detail.file,certificate_revision_id:rid(kind,n+1)};return route.fulfill({json:result('resource show',{org_id:O,ref:{kind,resource_id:id},current_revision:state.head,revised_at:date,revised_by:U,lifecycle:lifecycle(),provenance:'declared',actions:actions(),detail,...(kind==='certificates'?{date_advisory:{as_of:url.searchParams.get('as_of'),state:url.searchParams.get('as_of')?'expired':'unknown'}}:{})})});}
   if(tail===`${id}/history/query`){const items=Array.from({length:state.head},(_,i)=>state.head-i).map(n=>({org_id:O,ref:{kind,resource_id:id},revision_id:rid(kind,n),revision:n,name:content(kind,n).name,created_at:date,created_by:U,current:n===state.head,has_file:kind==='certificates'&&files.has(n)}));return route.fulfill({json:result('resource history',meta(items),items)});}
   if(tail===`${id}/lifecycle/history/query`)return route.fulfill({json:result('resource lifecycle history',meta([]),[])});
   if(tail===`${id}/lifecycle`){const body=req.postDataJSON();writes.push({path,body});if(state.denied)return failure('forbidden',403);const before=state.inactive?'inactive':'active';expect(body.expected_lifecycle_revision).toBe(state.life);if(state.lifecycleConflict){state.lifecycleConflict=false;state.life++;state.inactive=true;return failure('lifecycle_conflict');}state.life++;state.inactive=body.state==='inactive';return route.fulfill({json:result('resource lifecycle set',{lifecycle:lifecycle(),existing_selections:'preserved',event:{id:rid(kind,600),org_id:O,ref:{kind,resource_id:id},revision:state.life,resource_revision:state.head,before,after:body.state,reason_code:body.reason_code,actor_user_id:U,actor_kind:'session',created_at:date}})});}
  }
  const mutation=path.match(/^\/resources\/(profiles|certificates)(?:\/([^/]+)\/(revisions|file-revisions))?$/);
  if(mutation&&req.method()==='POST'){
   const kind=mutation[1],operation=mutation[3];
   if(operation==='file-revisions'){const raw=req.postData();writes.push({path,multipart:raw});if(state.uploadError)return failure('invalid_document',400);const metadata=JSON.parse(raw.match(/name="metadata"\r\n\r\n([^\r]+)/)[1]);expect(metadata.expected_revision).toBe(state.head);state.head++;data.set(`${kind}:${state.head}`,metadata.data);const f=file(state.head);files.set(state.head,f);return route.fulfill({json:result('resource certificate file add',f)});}
   const body=req.postDataJSON();writes.push({path,body});if(state.denied)return failure('forbidden',403);if(state.unknown){state.unknown=false;state.head++;data.set(`${kind}:${state.head}`,body.data);return route.abort('failed');}if(state.conflict&&!conflict){conflict=true;state.head++;return failure('revision_conflict');}if(operation)expect(body.expected_revision).toBe(state.head);state.head=operation?state.head+1:1;if(!operation&&kind==='certificates')files.clear();data.set(`${kind}:${state.head}`,body.data);return route.fulfill({json:result('resource update',revision(kind,state.head))});
  }
  const pin=path.match(new RegExp(`^/tasks/${T}/(profiles|certificates)$`));
  if(pin&&req.method()==='POST'){const kind=pin[1],body=req.postDataJSON();writes.push({path,body});const prior=pins.find(p=>p.kind===kind&&p.revision===body.revision&&p.lot===(body.lot??null));if(state.inactive&&!prior)return failure('resource_inactive');const value={id:rid(kind,700),org_id:O,task_id:T,[kind==='profiles'?'profile_revision_id':'certificate_revision_id']:rid(kind,body.revision),revision:body.revision,lot:body.lot??null,data:content(kind,body.revision),duplicate:!!prior,replaced_snapshot_id:null};pins.push({kind,...value});return route.fulfill({json:result('task resource add',value)});}
  if(path.includes('/file/download-link'))return route.fulfill({json:result('resource certificate download link',{url:`/resources/certificates/revisions/${rid('certificates',1)}/file/download?signature=synthetic`,expires_in:300})});
  if(path.includes('/file/download')){expect(req.headers()['x-org-id']).toBe(O);expect(req.headers().authorization).toBeTruthy();return route.fulfill({contentType:'application/pdf',body:'%PDF-synthetic'});}
  if(path.includes('/file/pages/')){expect(req.headers().authorization).toBe('Bearer synthetic-qualification-session');expect(requestOrg).toBe(O);return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgaPj/HwAEggJ/59habAAAAABJRU5ErkJggg==','base64')});}
  if(req.method()==="GET"&&path==="/health")return route.fulfill({json:{ok:true,command:"health",data:{status:"ok",version:"4.0",real_llm_configured:false,org_signup_enabled:false},items:[],warnings:[],cost:{llm_tokens:0,ocr_pages:0,usd:0},duration_ms:0}});
  throw new Error(`Unexpected qualification request: ${req.method()} ${path}`);
  }finally{if(read)reads.active--;}
 });return {state,writes,queries,requests,pins,reads,totals,release:()=>release?.(),releaseRead:path=>gates.get(path)?.()};
}
for(const kind of ['profiles','certificates']){
 const id=kind==='profiles'?P:C,label=kind==='profiles'?'资料':'证照';
 test(`${kind} bounded search and page reset`,async({page})=>{const f=await fixture(page,{paginated:true});await page.goto('/app/org/profiles');await page.getByRole('button',{name:`下一页${label}`,exact:true}).click();await page.getByLabel(`搜索${label}`,{exact:true}).fill('空结果');await expect(page.getByText(`没有符合条件的${label}`,{exact:true})).toBeVisible();expect(f.queries.filter(q=>q.kind===kind).at(-1)).toMatchObject({q:'空结果',cursor:null,limit:25});expect(f.requests.some(r=>r.method==='GET'&&['/resources/profiles','/resources/certificates','/resources/certificates/files'].includes(r.path))).toBe(false);expect(page.url()).not.toContain('空结果');expect(await page.evaluate(()=>JSON.stringify({...sessionStorage,...localStorage}))).not.toContain('空结果');});
 test(`${kind} exact history immutable and explicit old pin`,async({page})=>{const f=await fixture(page);await page.goto(`/app/org/${kind}/${id}?revision=1&task=${T}`);await expect(page.getByRole('button',{name:`修订${label}`,exact:true})).toHaveCount(0);await page.getByRole('button',{name:'选择到任务',exact:true}).click();await page.getByLabel('分包（可选）').fill('包一');await page.getByRole('button',{name:'确认选择修订 1',exact:true}).click();await expect(page.getByRole('status').filter({hasText:'任务已固定修订 1'})).toBeVisible();expect(f.writes[0].body).toMatchObject({revision:1,lot:'包一'});await expect(page.getByRole('status').filter({hasText:'任务已固定修订 1'})).toBeVisible();});
 test(`${kind} conflict retains draft without automatic retry`,async({page})=>{const f=await fixture(page,{conflict:true});await page.goto(`/app/org/${kind}/${id}`);await page.getByRole('button',{name:`修订${label}`,exact:true}).click();await page.getByLabel(kind==='profiles'?'单位名称':'证照名称',{exact:true}).fill('仅内存草稿');await page.getByRole('button',{name:`保存${label}`,exact:true}).click();await expect(page.getByRole('alert').filter({hasText:'草稿已保留'}).first()).toBeVisible();expect(f.writes).toHaveLength(1);await page.getByRole('button',{name:`保存${label}`,exact:true}).click();await expect.poll(()=>f.writes.length).toBe(2);expect(f.writes[1].body.expected_revision).toBe(3);expect(f.requests.some(r=>/confidential-values|\/reveal/.test(r.path))).toBe(false);});
 test(`${kind} lifecycle CAS preserves content and task pins`,async({page})=>{const f=await fixture(page);await page.goto(`/app/org/${kind}/${id}`);await page.getByRole('button',{name:`停用${label}`,exact:true}).click();await page.getByRole('button',{name:'确认停用',exact:true}).click();await expect(page.getByRole('button',{name:`恢复${label}`,exact:true})).toBeVisible();expect(f.state.head).toBe(2);expect(f.writes[0].body).toMatchObject({expected_revision:2,expected_lifecycle_revision:0,state:'inactive'});});
}
for(const options of [{role:'viewer'},{role:'admin',taskRole:null},{taskRole:'observer'},{taskRole:'reviewer'},{archived:true}])test(`profile role/task denial ${JSON.stringify(options)}`,async({page})=>{const f=await fixture(page,options);await page.goto(`/app/org/profiles/${P}?task=${T}`);await expect(page.getByRole('heading',{name:'合成资料',exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'选择到任务',exact:true})).toHaveCount(0);expect(f.writes).toHaveLength(0);});
test('certificate metadata does not inherit original',async({page})=>{const f=await fixture(page,{currentFile:true});await page.goto(`/app/org/certificates/${C}`);await page.getByRole('button',{name:'修订证照',exact:true}).click();await page.getByLabel('证照名称',{exact:true}).fill('新元数据');await page.getByRole('button',{name:'保存证照',exact:true}).click();await expect(page.getByText('此修订没有原件', {exact:true})).toBeVisible();expect(f.writes).toHaveLength(1);});
test('certificate ordered multipart rotation exact receipt and subsequent pin',async({page})=>{const f=await fixture(page);await page.goto(`/app/org/certificates/${C}?task=${T}`);await page.getByRole('button',{name:'上传原件',exact:true}).click();await page.locator('input[type=file]').setInputFiles([{name:'one.png',mimeType:'image/png',buffer:Buffer.from('one')},{name:'two.jpg',mimeType:'image/jpeg',buffer:Buffer.from('two')}]);await page.getByRole('button',{name:'上移文件 2',exact:true}).click();await page.getByRole('button',{name:'旋转文件 1',exact:true}).click();await page.getByRole('button',{name:'保存原件为新修订',exact:true}).click();await expect(page.getByRole('status').filter({hasText:'原件已保存 · 内容修订 3'})).toBeVisible();expect(f.writes[0].multipart.indexOf('filename="two.jpg"')).toBeLessThan(f.writes[0].multipart.indexOf('filename="one.png"'));expect(f.writes[0].multipart).toContain('"rotation":90');expect(f.writes).toHaveLength(1);await page.getByRole('button',{name:'选择到任务',exact:true}).click();await page.getByRole('button',{name:'确认选择修订 3',exact:true}).click();await expect(page.getByRole('status').filter({hasText:'任务已固定修订 3'})).toBeVisible();expect(f.writes[1].body.revision).toBe(3);});
test('upload rejection preserves selected files and original revision',async({page})=>{const f=await fixture(page,{uploadError:true});await page.goto(`/app/org/certificates/${C}`);await page.getByRole('button',{name:'上传原件',exact:true}).click();await page.locator('input[type=file]').setInputFiles({name:'bad.pdf',mimeType:'application/pdf',buffer:Buffer.from('bad')});await page.getByRole('button',{name:'保存原件为新修订',exact:true}).click();await expect(page.getByRole('alert').filter({hasText:'invalid_document'})).toBeVisible();await expect(page.getByText('bad.pdf',{exact:false})).toBeVisible();expect(f.state.head).toBe(2);expect(f.writes).toHaveLength(1);});
test('exact old original authenticated download and explicit advisory date',async({page})=>{const f=await fixture(page);await page.goto(`/app/org/certificates/${C}?revision=1`);await page.getByLabel('核对日期（仅日期提示）').fill('2026-10-06');await page.getByLabel('核对日期（仅日期提示）').press('Tab');await expect(page.getByText(/按所填日期已过期/)).toBeVisible();const pending=page.waitForEvent('download');await page.getByRole('button',{name:'下载此修订原件',exact:true}).click();const download=await pending;expect(download.suggestedFilename()).toBe('synthetic.pdf');const stream=await download.createReadStream(),chunks=[];for await(const chunk of stream)chunks.push(chunk);expect(Buffer.concat(chunks).toString()).toBe('%PDF-synthetic');await expect.poll(()=>f.requests.some(r=>r.path.includes('/file/download')&&!r.path.includes('download-link'))).toBe(true);await expect(page.getByText(/真实性.*未经核验/)).toBeVisible();});
for(const options of [{foreign:true},{oversize:true}])test(`bounded projection rejected ${JSON.stringify(options)}`,async({page})=>{await fixture(page,options);await page.goto('/app/org/profiles');await expect(page.getByRole('alert').first()).toBeVisible();await expect(page.getByRole('link',{name:'合成资料',exact:true})).toHaveCount(0);});
test('wrong exact certificate original rejected',async({page})=>{await fixture(page,{wrongFile:true,currentFile:true});await page.goto(`/app/org/certificates/${C}`);await expect(page.getByRole('alert').filter({hasText:/原件.*范围/})).toBeVisible();await expect(page.getByRole('heading',{name:'合成证照',exact:true})).toHaveCount(0);});
test('authority loss clears draft',async({page})=>{const f=await fixture(page);await page.goto(`/app/org/profiles/${P}`);await page.getByRole('button',{name:'修订资料',exact:true}).click();await page.getByLabel('单位名称',{exact:true}).fill('撤权草稿');f.state.denied=true;await page.getByRole('button',{name:'保存资料',exact:true}).click();await expect(page.getByLabel('单位名称',{exact:true})).toHaveCount(0);await expect(page.getByRole('heading',{name:'合成资料',exact:true})).toHaveCount(0);});
test('narrow keyboard unsaved guard and exact query navigation',async({page})=>{await fixture(page);await page.setViewportSize({width:390,height:844});await page.goto(`/app/org/profiles/${P}?revision=2`);await page.getByRole('button',{name:'修订资料',exact:true}).click();await page.getByLabel('单位名称',{exact:true}).fill('未保存草稿');await page.getByRole('link',{name:'修订 1',exact:true}).click();await expect(page.getByRole('dialog').filter({hasText:'放弃未保存'})).toBeVisible();await page.getByRole('button',{name:'取消',exact:true}).last().click();await expect(page.getByLabel('单位名称',{exact:true})).toHaveValue('未保存草稿');await page.getByLabel('单位名称',{exact:true}).press('Tab');});
for(const role of ['technical','viewer'])test(`maintenance denial for ${role}`,async({page})=>{await fixture(page,{role});await page.goto(`/app/org/certificates/${C}`);await expect(page.getByRole('heading',{name:'合成证照',exact:true})).toBeVisible();for(const name of ['修订证照','停用证照','上传原件'])await expect(page.getByRole('button',{name,exact:true})).toHaveCount(0);});
test('technical task contributor can explicitly pin without maintenance',async({page})=>{const f=await fixture(page,{role:'technical'});await page.goto(`/app/org/profiles/${P}?task=${T}`);await page.getByRole('button',{name:'选择到任务',exact:true}).click();await page.getByRole('button',{name:'确认选择修订 2',exact:true}).click();await expect(page.getByRole('status').filter({hasText:'任务已固定修订 2'})).toBeVisible();expect(f.writes[0].body.revision).toBe(2);});
test('inactive new pin refused and exact duplicate remains usable',async({page})=>{const f=await fixture(page,{inactive:true,life:1});await page.goto(`/app/org/profiles/${P}?task=${T}`);await page.getByRole('button',{name:'核对已有固定版本',exact:true}).click();await page.getByRole('button',{name:'确认选择修订 2',exact:true}).click();await expect(page.getByRole('alert').filter({hasText:'resource_inactive'})).toBeVisible();expect(f.writes).toHaveLength(1);f.pins.push({kind:'profiles',revision:2,lot:null});await page.getByRole('button',{name:'核对已有固定版本',exact:true}).click();await page.getByRole('button',{name:'确认选择修订 2',exact:true}).click();await expect(page.getByRole('status').filter({hasText:'已有相同固定版本'})).toBeVisible();expect(f.writes).toHaveLength(2);expect(f.writes.every(w=>w.path===`/tasks/${T}/profiles`)).toBe(true);});
test('live downgrade cancels an open pin dialog',async({page})=>{const f=await fixture(page);await page.goto(`/app/org/certificates/${C}?task=${T}`);await page.getByRole('button',{name:'选择到任务',exact:true}).click();await expect(page.getByRole('dialog',{name:'固定精确证照修订',exact:true})).toBeVisible();f.state.taskRole='observer';await page.getByRole('button',{name:'确认选择修订 2',exact:true}).click();await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.getByText(/需要任务负责人或协作者/)).toBeVisible();expect(f.writes).toHaveLength(0);});
test('expired cursor visible and deliberate first-page restart',async({page})=>{const f=await fixture(page,{paginated:true,cursorError:true});await page.goto('/app/org/profiles');await page.getByRole('button',{name:'下一页资料',exact:true}).click();await expect(page.getByRole('alert').filter({hasText:'management_cursor_expired'})).toBeVisible();await expect(page.getByRole('button',{name:'上一页资料',exact:true})).toBeDisabled();await page.getByRole('button',{name:'重新读取资料',exact:true}).click();await expect(page.getByRole('link',{name:'合成资料',exact:true})).toBeVisible();expect(f.queries.filter(q=>q.kind==='profiles').at(-1).cursor).toBeNull();});
test('unknown metadata outcome rereads head and never automatically resubmits',async({page})=>{const f=await fixture(page,{unknown:true});await page.goto(`/app/org/profiles/${P}`);await page.getByRole('button',{name:'修订资料',exact:true}).click();await page.getByLabel('单位名称',{exact:true}).fill('结果未确定草稿');await page.getByRole('button',{name:'保存资料',exact:true}).click();await expect(page.getByRole('alert').filter({hasText:'保存结果尚未确定'})).toBeVisible();await expect(page.getByLabel('单位名称',{exact:true})).toHaveValue('结果未确定草稿');expect(f.writes).toHaveLength(1);expect(f.requests.filter(r=>r.path===`/management/resources/profiles/${P}`)).toHaveLength(2);});
test('missing foreign detail uses uniform inaccessible state',async({page})=>{await fixture(page,{missing:true});await page.goto(`/app/org/certificates/${C}`);await expect(page.getByRole('alert').filter({hasText:'不可访问'})).toBeVisible();await expect(page.getByRole('heading',{name:'合成证照',exact:true})).toHaveCount(0);});
test('lifecycle conflict rereads without retry or content changes',async({page})=>{const f=await fixture(page,{lifecycleConflict:true});await page.goto(`/app/org/profiles/${P}`);await page.getByRole('button',{name:'停用资料',exact:true}).click();await page.getByRole('button',{name:'确认停用',exact:true}).click();await expect(page.getByRole('alert').filter({hasText:'版本已变化'})).toBeVisible();await expect(page.getByRole('button',{name:'恢复资料',exact:true})).toBeVisible();expect(f.writes).toHaveLength(1);expect(f.state.head).toBe(2);});
test('org reset aborts obsolete search and clears forms and late content',async({page})=>{const f=await fixture(page,{delay:true});await page.goto('/app/org/profiles');await page.getByLabel('搜索资料',{exact:true}).fill('迟到查询');await expect.poll(()=>f.queries.some(q=>q.q==='迟到查询')).toBe(true);await page.evaluate(()=>window.dispatchEvent(new Event('bid:org-reset')));f.release();await expect(page.getByRole('link',{name:'合成资料',exact:true})).toHaveCount(0);await expect(page.getByRole('link',{name:'合成证照',exact:true})).toHaveCount(0);expect(await page.evaluate(()=>JSON.stringify({...sessionStorage,...localStorage}))).not.toContain('迟到查询');});
test('certificate metadata creation hands off exact detail without hidden file or pin writes',async({page})=>{const f=await fixture(page);await page.goto('/app/org/profiles');await page.getByRole('button',{name:'添加证照',exact:true}).click();await page.getByLabel('证照名称',{exact:true}).fill('新证照声明');await page.getByLabel('证号',{exact:true}).fill('SYN-NEW');await page.getByRole('button',{name:'保存证照',exact:true}).click();await expect(page.getByRole('link',{name:'查看精确修订并上传原件',exact:true})).toBeVisible();expect(f.writes).toHaveLength(1);expect(f.writes[0].path).toBe('/resources/certificates');expect(f.writes[0].body.data).toMatchObject({name:'新证照声明',valid_from:null,valid_until:null});});

test('fixed built lists actionable within 1500ms with at most 100 DOM rows',async({page},info)=>{
 await fixture(page);
 const started=performance.now();
 await page.goto('/app/org/profiles');
 await expect(page.getByRole('link',{name:'合成资料',exact:true})).toBeVisible();
 await expect(page.getByRole('link',{name:'合成证照',exact:true})).toBeVisible();
 const actionableMs=performance.now()-started;
 const renderedRows=await page.locator('[data-testid$="-list"] .el-table__body tbody tr').count();
 info.annotations.push({type:'measurement',description:JSON.stringify({first_actionable_ms:Math.round(actionableMs),limit_ms:1500,rendered_rows:renderedRows,row_limit:100})});
 expect(actionableMs).toBeLessThanOrEqual(1500);expect(renderedRows).toBeLessThanOrEqual(100);
});

test('shared reader queues a third read behind two occupied slots',async({page},info)=>{
 const profiles='/management/resources/profiles/query',certificates='/management/resources/certificates/query',fields='/confidential-fields';
 const f=await fixture(page,{readGates:[profiles,certificates,fields]});
 await page.goto('/app/org/profiles');
 await expect.poll(()=>f.reads.active).toBe(2);
 await page.getByRole('button',{name:'新建资料',exact:true}).click();
 await expect(page.getByLabel('单位名称',{exact:true})).toBeVisible();
 // Two animation frames let the click handler and fetch microtasks run; unlike
 // merely checking max after sequential reads, the metadata request is queued.
 await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 expect(f.requests.some(request=>request.path===fields)).toBe(false);
 expect(f.reads.active).toBe(2);
 f.releaseRead(profiles);
 await expect.poll(()=>f.requests.some(request=>request.path===fields)).toBe(true);
 await expect.poll(()=>f.reads.active).toBe(2);
 f.releaseRead(fields);f.releaseRead(certificates);
 await expect(page.getByRole('button',{name:'开户账号',exact:true}).first()).toBeVisible();
 await expect(page.getByRole('link',{name:'合成证照',exact:true})).toBeVisible();
 await expect.poll(()=>f.reads.active).toBe(0);
 expect(f.reads.max).toBe(2);
 info.annotations.push({type:'measurement',description:JSON.stringify({max_in_flight_reads:f.reads.max,limit:2,third_read_queued:true})});
});

test('many valid pages evict old rows and retain at most 2MiB parsed page data',async({page},info)=>{
 test.setTimeout(120_000);
 // WeakRefs observe which parsed page arrays/metadata remain strongly reachable
 // from the app. The observer retains byte counts, never result bodies or rows.
 await page.addInitScript(()=>{
  window.__qualificationPageRetention={transferred:0,refs:[],pages:0};
  const json=Response.prototype.json;
  Response.prototype.json=async function(...args){
   const payload=await json.apply(this,args);
   if(payload.command==='resource query'&&Array.isArray(payload.items)&&payload.data?.as_of){
    const tracker=window.__qualificationPageRetention,bytes=value=>new TextEncoder().encode(JSON.stringify(value)).length;
    tracker.transferred+=bytes(payload);tracker.pages++;
    tracker.refs.push({target:new WeakRef(payload.items),bytes:bytes(payload.items)},{target:new WeakRef(payload.data),bytes:bytes(payload.data)});
   }
   return payload;
  };
 });
 const pages=60,f=await fixture(page,{largePages:pages});
 await page.goto('/app/org/profiles');
 await expect(page.getByRole('link',{name:/^分页000-0 /})).toBeVisible();
 for(let index=1;index<pages;index++){
  await page.getByRole('button',{name:'下一页资料',exact:true}).click();
  await expect(page.getByRole('link',{name:new RegExp(`^分页${String(index).padStart(3,'0')}-0 `)})).toBeVisible();
  await expect(page.getByRole('link',{name:new RegExp(`^分页${String(index-1).padStart(3,'0')}-`)})).toHaveCount(0);
 }
 await expect(page.getByRole('button',{name:'下一页资料',exact:true})).toBeDisabled();
 const cdp=await page.context().newCDPSession(page);
 try{await cdp.send('HeapProfiler.collectGarbage');}finally{await cdp.detach();}
 const measured=await page.evaluate(()=>{
  const tracker=window.__qualificationPageRetention,live=tracker.refs.filter(entry=>entry.target.deref()!==undefined);
  return {transferred_page_bytes:tracker.transferred,retained_parsed_page_bytes:live.reduce((sum,entry)=>sum+entry.bytes,0),retained_page_arrays:live.filter(entry=>Array.isArray(entry.target.deref())).length,page_responses:tracker.pages};
 });
 expect(f.totals.pageBytes).toBeGreaterThan(2*1024*1024);
 expect(measured.transferred_page_bytes).toBeGreaterThan(2*1024*1024);
 expect(measured.page_responses).toBe(pages+1);
 expect(measured.retained_parsed_page_bytes).toBeGreaterThan(0);
 expect(measured.retained_parsed_page_bytes).toBeLessThanOrEqual(2*1024*1024);
 // One current profiles page plus one current certificates page survive, rather
 // than all 60 profiles pages or a hidden previous-page cache.
 expect(measured.retained_page_arrays).toBeLessThanOrEqual(2);
 expect(await page.locator('[data-testid$="-list"] .el-table__body tbody tr').count()).toBe(26);
 info.annotations.push({type:'measurement',description:JSON.stringify({...measured,fixture_pages_per_profiles:pages,rows_per_profiles_page:25,retained_limit_bytes:2*1024*1024,measurement:'WeakRef live parsed arrays and metadata after explicit GC'})});
});

test('actual logout and org B login discard a delayed org A library response',async({page},info)=>{
 const f=await fixture(page,{delay:true,allowOrgSwitch:true});
 await page.goto('/app/org/profiles');
 await expect(page.getByRole('link',{name:'合成证照',exact:true})).toBeVisible();
 await page.getByLabel('搜索资料',{exact:true}).fill('迟到查询');
 await expect.poll(()=>f.queries.some(query=>query.q==='迟到查询')).toBe(true);
 await page.getByRole('button',{name:'退出 / 切换单位',exact:true}).click();
 await expect(page).toHaveURL(/\/app\/org\/login$/);
 await expect.poll(()=>page.evaluate(()=>sessionStorage.getItem('bid.org.session'))).toBeNull();
 await page.locator('input[name=email]').fill('synthetic@example.test');
 await page.locator('input[name=password]').fill('synthetic-test-password');
 await page.getByRole('button',{name:'登录',exact:true}).click();
 await page.getByRole('button',{name:'合成乙单位',exact:true}).click();
 await expect(page).toHaveURL(/\/app\/org\/tasks$/);
 await page.getByRole('link',{name:'单位资料',exact:true}).click();
 await expect(page.getByRole('link',{name:'乙单位专属资料',exact:true})).toBeVisible();
 await expect(page.getByRole('link',{name:'乙单位专属证照',exact:true})).toBeVisible();
 f.release();
 await expect.poll(()=>f.reads.active).toBe(0);
 await expect(page.getByRole('link',{name:'合成资料',exact:true})).toHaveCount(0);
 await expect(page.getByRole('link',{name:'合成证照',exact:true})).toHaveCount(0);
 await expect(page.getByLabel('搜索资料',{exact:true})).toHaveValue('');
 const session=await page.evaluate(()=>JSON.parse(sessionStorage.getItem('bid.org.session')));
 expect(session.orgId).toBe(B);
 expect(f.requests.filter(request=>request.path.endsWith('/query')&&request.org_id===B)).toHaveLength(2);
 expect(await page.evaluate(()=>JSON.stringify({...sessionStorage,...localStorage}))).not.toContain('迟到查询');
 info.annotations.push({type:'measurement',description:JSON.stringify({switched_orgs:2,actual_logout:true,actual_org_login:true,old_reply_released_after_new_library:true})});
});

test('exact historical certificate PNG preview uses authenticated revision pages',async({page},info)=>{
 const f=await fixture(page);
 await page.goto(`/app/org/certificates/${C}?revision=1`);
 await page.getByRole('button',{name:'预览此修订原件',exact:true}).click();
 const viewer=page.getByRole('dialog',{name:'证照旧版 · 修订 1 · synthetic.pdf',exact:true});
 await expect(viewer).toBeVisible();
 const image=viewer.getByRole('img',{name:'证照旧版 · 修订 1 · synthetic.pdf 第 1 页',exact:true});
 await expect(image).toBeVisible();
 await expect.poll(()=>image.evaluate(node=>node.complete&&node.naturalWidth>0)).toBe(true);
 const signature=await image.evaluate(node=>Array.from(atob(node.src.split(',')[1]).slice(0,8),character=>character.charCodeAt(0)));
 expect(signature).toEqual([137,80,78,71,13,10,26,10]);
 expect(await image.getAttribute('src')).toMatch(/^data:image\/png;base64,/);
 const requests=f.requests.filter(request=>request.path.includes('/file/pages/'));
 expect(requests.length).toBeGreaterThan(0);
 expect(requests.every(request=>request.path.startsWith(`/resources/certificates/revisions/${rid('certificates',1)}/file/pages/`)&&request.org_id===O&&request.url.startsWith('/v4/'))).toBe(true);
 expect(requests.some(request=>request.path.includes(rid('certificates',2)))).toBe(false);
 expect(f.writes).toHaveLength(0);
 info.annotations.push({type:'measurement',description:JSON.stringify({preview_revision:1,decoded_png_signature_valid:true,authenticated_org_match:true,page_requests:requests.length})});
});
