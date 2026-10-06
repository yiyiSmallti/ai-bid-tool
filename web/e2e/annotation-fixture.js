import { expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { basename, extname, join, resolve } from "node:path";
const id=n=>`00000000-0000-4000-8000-${String(n).padStart(12,"0")}`;
export const ids={org:id(1),task:id(2),user:id(3),card:id(4),extract:id(5),requirement:id(6),source:id(7),annotation:id(8),job:id(9),asset:id(10),rendition:id(11),document:id(12),releaseJob:id(13),release:id(14)};
const png=Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=","base64"),hash=createHash("sha256").update(png).digest("hex"),now="2026-10-06T12:00:00Z";
const cost={llm_tokens:0,ocr_pages:0,usd:0,basis:"zero",charge:"0",billing_currency:"CNY",task_amount:"0",unpriced_calls:0,unresolved_calls:0};
const result=(command,data={},items=[],ok=true)=>({ok,command,data,items,warnings:[],cost,duration_ms:0});
export async function annotationFixture(page,options={}){
  if(!process.env.E2E_STATIC_DIR)throw new Error("E2E_STATIC_DIR required; fixture starts no services");
  const directory=resolve(process.env.E2E_STATIC_DIR),state={requests:[],unexpected:[],submits:0,cardWrites:0,cancels:0,releaseRetries:0,images:0,maxImages:0,...options};
  const boundary={org_id:ids.org,task_id:ids.task},source={id:ids.source,...boundary,task_certificate_id:id(20),certificate_id:id(21),certificate_revision_id:id(22),certificate_file_id:id(23),source_kind:"user_supplied_certificate_pdf",original:{name:"合成证书.pdf",sha256:hash,size_bytes:100,media_type:"application/pdf",page_count:1},page:1,render_profile:"pdf-page-preview-v1",dpi:150,preview:{name:"page.png",sha256:hash,size_bytes:png.length,media_type:"image/png",width_px:100,height_px:100},rendered_at:now,created_by:ids.user,active_selection:true,status:"unconfirmed_source",confirmed_by:null,eligible_for_draft_export:false};
  const citation={document_id:ids.document,chunk_id:id(24),page:1,quote:"合成证书要求"};
  const card={id:ids.card,...boundary,extraction_job_id:ids.extract,requirement_id:ids.requirement,revision:1,revision_id:id(25),state:"draft",review_domain:"technical",disposition:null,origin:"human",actor_kind:"session",source:citation,content:{response_kind:"evidence",response_text:"",deviation:"none",deviation_note:"",evidence:[]},evidence:[],confirmed_by:null,confirmed_at:null,warning_codes:[],eligibility:options.requirementState==="unconfirmed"?"requirement_unconfirmed":"unconfirmed"};
  let target=null;
  const candidate=()=>({id:ids.annotation,...boundary,job_id:ids.job,input_hash:hash,manifest:{org_id:ids.org,task_id:ids.task,target:target??{extraction_job_id:ids.extract,card_id:ids.card,expected_card_revision:1,source:{kind:"certificate_page",evidence_source_id:ids.source},plan:{crop:null,boxes:[]}}},asset_id:ids.asset,rendition_id:ids.rendition,rendering:{image:{sha256:hash,size_bytes:png.length,width_px:1024,height_px:(target?.plan.crop.height??100)+40},canvas:{width_px:1024,height_px:(target?.plan.crop.height??100)+40,mapping:{crop:target?.plan.crop??{x:0,y:0,width:100,height:100},content_offset_x:Math.floor((1024-(target?.plan.crop.width??100))/2),content_offset_y:0,content_width:target?.plan.crop.width??100,content_height:target?.plan.crop.height??100,footer_height:40}}},created_at:now,status:"unconfirmed_material",confirmed_by:null,eligible_for_draft_export:false});
  await page.addInitScript(value=>sessionStorage.setItem("bid.org.session",JSON.stringify({orgId:value.org,session:"synthetic-session"})),ids);
  await page.route("**/*",async route=>{
    const req=route.request(),url=new URL(req.url()),path=url.pathname.replace(/^\/v4(?=\/)/,""),method=req.method();
    if(url.origin!==new URL(process.env.E2E_BASE_URL??"http://127.0.0.1:8000").origin){state.unexpected.push("external-origin");return route.abort();}
    if(path.startsWith("/app/")){const file=path.startsWith("/app/assets/")?join(directory,"assets",basename(path)):join(directory,"index.html");return route.fulfill({contentType:{".html":"text/html",".js":"text/javascript",".css":"text/css"}[extname(file)]??"application/octet-stream",body:readFileSync(file)});}
    const body=req.postData()?req.postDataJSON():null;
    state.requests.push({method,path,query:Object.fromEntries(url.searchParams),...(body?{body}: {})});
    let response;
    try{
      expect(req.headers()["x-org-id"]).toBe(ids.org);
      if(/\/annotations|\/annotation-releases/.test(path))expect(url.pathname.startsWith("/v4/")).toBe(true);
      if(method==="GET"&&path==="/org/current")response=result("org current",{org_id:ids.org,user_id:ids.user,role:options.role??"technical"});
      else if(path===`/tasks/${ids.task}/workflow`)response=result("task workflow",{workflow:{...boundary,owner_user_id:ids.user,state:"active",revision:1,access_epoch:1,last_event_cursor:"opaque-seed"}});
      else if(path===`/tasks/${ids.task}/members`)response=result("task member list",{...boundary,next_cursor:null},[{...boundary,user_id:ids.user,role:options.taskRole??"contributor",active:true,review_domains:["technical"]}]);
      else if(path===`/tasks/${ids.task}/events`)return route.fulfill({contentType:"text/event-stream",body:`data: ${JSON.stringify({type:"heartbeat",cursor:"opaque-seed"})}\n\n`});
      else if(path===`/tasks/${ids.task}/events/poll`)response=result("task events",{...boundary,next_cursor:"opaque-seed"});
      else if(path===`/cards/${ids.card}`&&method==="GET")response=result("card show",card);
      else if(path===`/cards/${ids.card}`&&method==="PUT"){
        expect(Object.keys(body).sort()).toEqual(["content","expected_revision"]);
        expect(body.expected_revision).toBe(card.revision);
        expect(body.content.evidence).toHaveLength(1);
        const input=body.content.evidence[0];
        expect(input.kind).toBe("image_region");
        expect(input.asset_id).toBe(ids.asset);expect(input.rendition_id).toBe(ids.rendition);
        expect(input.expected_image_sha256).toBe(hash);
        expect(input.region).toEqual({x:0,y:0,width:target?.plan.crop.width??100,height:target?.plan.crop.height??100});
        expect(input.claim_scope).toBe("document_excerpt");expect(input.visual_observation.trim()).not.toBe("");
        state.cardWrites++;
        Object.assign(card,{content:structuredClone(body.content),revision:card.revision+1,revision_id:id(26),evidence:[{
          id:id(31),...boundary,card_id:ids.card,requirement_id:ids.requirement,input:structuredClone(input),
          material_kind:"certificate_image",selection_id:source.task_certificate_id,resource_revision_id:source.certificate_revision_id,
          quote_check:"unreviewed_image",source_archive:source,active_selection:true,confirmed_by:null,confirmed_at:null,
          screenshot_asset_id:input.asset_id,screenshot_rendition_id:input.rendition_id,image_sha256:input.expected_image_sha256,
          region:input.region,claim_scope:input.claim_scope,visual_observation:input.visual_observation,
        }]});
        response=result("card update",card);
      }
      else if(path===`/requirements/${ids.requirement}/review`)response=result("req show",{requirement:{...boundary,extraction_job_id:ids.extract,requirement_id:ids.requirement,content:{text:"合成要求",source:citation,category:"qualification",starred:false},state:options.requirementState??"confirmed"}});
      else if(path===`/cards/${ids.card}/signoffs`)response=result("card signoff list",{...boundary,card_id:ids.card,round:null,next_cursor:null,summary:{status:options.cosign??"not_required",round_revision:0,required_domains:["technical","commercial"],signed_domains:options.cosign==="partial"?["technical"]:[],pending_domains:options.cosign==="partial"?["commercial"]:["technical","commercial"]}});
      else if(path===`/tasks/${ids.task}/requirements/${ids.requirement}/review-policy`)response=result("card policy show",{policy:{...boundary,extraction_job_id:ids.extract,requirement_id:ids.requirement,revision:0,primary_domain:"technical",co_sign_required:false,starred:false,co_sign_starred:false,task_rule_revision:1,required_domains:["technical"]}});
      else if(path===`/tasks/${ids.task}/evidence-sources`){if(url.searchParams.has("limit"))expect(Number(url.searchParams.get("limit"))).toBe(50);response=result("evidence source list",{history:true,active_source_ids:[ids.source],...(url.searchParams.has("limit")?{next_cursor:null,returned:1,has_more:false}:{})},[source]);}
      else if(path===`/evidence-sources/${ids.source}/preview/download-link`)response=result("evidence source preview",{url:`/evidence-sources/${ids.source}/preview/download?signature=synthetic`,expires_in:300});
      else if((path.endsWith("/preview/download")||path.endsWith("/content"))){state.images++;state.maxImages=Math.max(state.maxImages,state.images);try{if(options.sourceDelay&&path.startsWith("/evidence-sources/"))await new Promise(resolve=>setTimeout(resolve,500));return await route.fulfill({contentType:"image/png",body:png});}finally{state.images--;}}
      else if(path===`/tasks/${ids.task}/annotations`&&method==="POST"){
        expect(["owner","contributor"]).toContain(options.taskRole??"contributor");target=body.input;expect(target.source).toEqual({kind:"certificate_page",evidence_source_id:ids.source});expect(target.card_id).toBe(ids.card);
        if(body.dry_run)response=result("evidence stamp",{dry_run:true,input_hash:hash,manifest:{target,source:{source_png:{sha256:hash}}},predicted_output:{width_px:1024,height_px:target.plan.crop.height+40},blocker_codes:[]});
        else{state.submits++;expect(body.expected_input_hash).toBe(hash);expect(body.reviewed_source_png_sha256).toBe(hash);expect(body.request_id).toMatch(/^[a-f0-9-]{36}$/);if(options.conflict)return route.fulfill({status:409,json:result("evidence stamp",{error:{code:"annotation_input_changed",message:"Source changed",exit_code:2}},[],false)});response=result("evidence stamp",{job_id:ids.job,task_id:ids.task,card_id:ids.card,kind:"annotation_render",status:"queued",duplicate:false});}
      }
      else if(path===`/tasks/${ids.task}/annotations`){expect(url.searchParams.get("card_id")).toBe(ids.card);expect(url.searchParams.get("limit")).toBe("50");response=result("evidence annotation list",{task_id:ids.task,next_cursor:null},(state.submits&&!options.conflict||options.existingCandidate)?[candidate()]:[]);}
      else if(path===`/jobs/${ids.job}`)response=result("job status",{id:ids.job,kind:"annotation_render",status:state.cancels?"cancelled":options.jobStatus??"succeeded",result:{annotation_id:ids.annotation},error:options.jobStatus==="failed"?{code:"renderer_unavailable",message:"Renderer unavailable",exit_code:4}:null},[],!options.jobStatus&&!state.cancels);
      else if(path===`/jobs/${ids.job}/cancel`){state.cancels++;response=result("job cancel",{id:ids.job,status:"cancelled"});}
      else if(path===`/jobs/${ids.releaseJob}`)response=result("job status",{id:ids.releaseJob,kind:"annotation_release",status:"failed",error:{code:"renderer_timeout",message:"Retry explicitly",exit_code:3},result:{annotation_id:ids.annotation,evidence_id:id(30),card_id:ids.card,expected_card_revision:1,expected_approval_binding_hash:hash}},[],false);
      else if(path===`/annotations/${ids.annotation}`)response=result("evidence annotation show",{candidate:candidate(),current:{validity:"current",active_selection:true,source_withdrawn:false,warning_codes:[]}});
      else if(path===`/annotations/${ids.annotation}/preview`)response=result("evidence annotation preview",{annotation_id:ids.annotation,rendition_id:ids.rendition,kind:"candidate",url:`/annotations/${ids.annotation}/content?signature=synthetic`,expires_in:300,image:{sha256:hash,size_bytes:png.length}});
      else if(path===`/annotations/${ids.annotation}/releases`&&method==="POST"){state.releaseRetries++;expect(body.retry).toBe(true);expect(body.expected_approval_binding_hash).toBe(hash);expect(body.expected_card_revision).toBe(1);expect(body.job_id).toBe(ids.releaseJob);response=result("evidence annotation release retry",{job_id:ids.releaseJob,status:"queued"});}
      else if(path===`/annotations/${ids.annotation}/releases`)response=result("evidence annotation releases",{task_id:ids.task,annotation_id:ids.annotation,next_cursor:null},options.staleRelease?[{id:ids.release,annotation_id:ids.annotation,decision_validity:"stale",releasable:false,blocker_codes:["approval_stale"]}]:[]);
      else if(path===`/tasks/${ids.task}/jobs`){expect(url.searchParams.get("limit")).toBe("20");expect(url.searchParams.get("kind")).toBe("annotation_release");expect(url.searchParams.get("annotation_id")).toBe(ids.annotation);if(options.releaseJobsUnavailable)return route.fulfill({status:503,json:result("task job list",{error:{code:"temporary_failure",message:"Retry status read",exit_code:3}},[],false)});response=result("task job list",{task_id:ids.task,kind:"annotation_release",next_cursor:null,returned:options.failedRelease?1:0,has_more:false},options.failedRelease?[{id:ids.releaseJob,kind:"annotation_release",status:"failed"}]:[]);}
      else if(path===`/tasks/${ids.task}/simulated-resources`)response=result("task simulated-resources",{selection_ids:[]});
      else if(path==="/confidential-fields")response=result("confidential list");
      else if(["products","features","certificates","profiles","certificate-files"].some(kind=>path===`/tasks/${ids.task}/${kind}`))response=result("task resources",{active_snapshot_ids:[]});
      else{state.unexpected.push(`${method} ${path}`);return route.fulfill({status:404,json:result("error",{error:{code:"unexpected_request",message:"Unexpected route",exit_code:4}},[],false)});}
      return route.fulfill({json:response});
    }catch(exc){state.unexpected.push(String(exc));return route.fulfill({status:500,json:result("error",{error:{code:"fixture_assertion",message:String(exc),exit_code:4}},[],false)});}
  });
  state.verify=()=>{expect(state.unexpected).toEqual([]);expect(state.maxImages).toBeLessThanOrEqual(2);expect(state.requests.filter(row=>/analyze|generate|search|mock/.test(row.path))).toEqual([]);};
  return state;
}
