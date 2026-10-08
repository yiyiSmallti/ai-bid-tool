import { expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { basename, extname, join, resolve } from "node:path";

export const seed = "console-assessments-stateful-v1";
export const uuid = (number) => `00000000-0000-4000-8000-${String(number).padStart(12, "0")}`;
export const ids = { org: uuid(1), orgB: uuid(2), task: uuid(10), extract: uuid(11), document: uuid(12), draft: uuid(13), job: uuid(14), report: uuid(15), finding: uuid(16), requirement: uuid(17), card: uuid(18), revision: uuid(19), item: uuid(20), rubric: uuid(21), score: uuid(22) };
export const now = "2026-10-05T00:00:00Z";
export const hash = "a".repeat(64);
export const zeroCost = () => ({ llm_tokens: 0, ocr_pages: 0, usd: 0, basis: "zero", charge: "0", billing_currency: "USD", task_amount: "0", unpriced_calls: 0, unresolved_calls: 0 });
export function result(command, data = {}, items = [], { ok = true, cost = zeroCost(), warnings = [] } = {}) {
  const value = { ok, command, data, items, warnings, cost, duration_ms: 0 };
  expect(Object.keys(value).sort()).toEqual(["command", "cost", "data", "duration_ms", "items", "ok", "warnings"].sort());
  expect(typeof value.cost.usd === "number" || value.cost.usd === null).toBe(true);
  expect(Object.keys(value.cost).sort()).toEqual(Object.keys(zeroCost()).sort());
  return value;
}
export const source = (word = false) => ({ document_id: ids.document, chunk_id: uuid(30), page: word ? null : 3, location: word ? { block_id: "p-17", kind: "paragraph", section_path: ["第二章", "技术要求"], paragraph: 17, table: null, row: null, column: null, label: "第二章 / 技术要求 / 第 17 段" } : null, quote: "必须提供真实材料，响应应无负偏离。" });
export const action = (name, allowed, domain = null) => ({ action: name, allowed, required_role: domain === "technical" ? "technical" : domain === "commercial" ? "bidder" : null, review_domain: domain, blocker_codes: allowed ? [] : ["forbidden"] });
export const budget = () => ({ org_id: ids.org, task_id: ids.task, revision: 1, limit: "10", currency: "USD", state: "active", spent: "2", reserved: "1", available: "7", unpriced_calls: 0, unresolved_calls: 0, history_complete: true, as_of: now });
export const header = (state) => ({ id: ids.report, org_id: ids.org, task_id: ids.task, job_id: ids.job, extraction_job_id: ids.extract, document_id: ids.document, draft_id: ids.draft, input_hash: hash, assessment_date: "2026-10-05", created_at: now, completion: state.partial ? "partial" : "complete", validity: state.stale ? "stale" : "current", invalidation_codes: state.stale ? ["card_revision_changed"] : [], notice_count: 0, advisory_only: true });
export const makeFinding = (index = 0) => ({ id: index ? uuid(100 + index) : ids.finding, org_id: ids.org, task_id: ids.task, report_id: ids.report, check_item_id: ids.item, requirement_id: ids.requirement, method: "deterministic", code: "negative_deviation", severity: index === 1 ? "disqualification_risk" : index === 2 ? "info" : "deduction_risk", review_domain: index === 1 ? "commercial" : "technical", reason: `合成风险 ${index + 1}：响应存在负偏离。`, source: source(index === 1), citations: [{ id: uuid(50 + index), org_id: ids.org, task_id: ids.task, report_id: ids.report, finding_id: index ? uuid(100 + index) : ids.finding, citation: { kind: "draft", draft_id: ids.draft, response_item_id: ids.item, card_revision_id: ids.revision, field: "response_text", quote: "存在负偏离" } }], status: "open", revision: 1, latest_decision_id: null, advisory_only: true });

export async function fixture(page, options = {}) {
  if (!process.env.E2E_STATIC_DIR) throw new Error("E2E_STATIC_DIR is required; this suite starts no services");
  const directory = resolve(process.env.E2E_STATIC_DIR);
  const state = { role: "technical", org: ids.org, published: true, findings: [makeFinding(), makeFinding(1), makeFinding(2)], decisions: [], requests: [], unexpected: [], violations: [], consoleErrors: [], writes: 0, previews: 0, submissions: 0, polls: 0, partial: false, stale: false, noDraft: false, error: null, conflict: false, blocker: null, unknownCost: false, queueError: false, networkError: false, delayedCitation: null, ...options };
  page.on?.("pageerror", error => state.consoleErrors.push(error.message));
  page.on?.("console", message => { if (message.type() === "error" && !(state.networkError && message.text().includes("net::ERR_CONNECTION_RESET")) && !/Failed to load resource: the server responded with a status of (404|409|422|503)/.test(message.text())) state.consoleErrors.push(message.text()); });
  state.coverage = Array.from({ length: 1200 }, (_, index) => ({ id: index ? uuid(1000 + index) : ids.item, org_id: ids.org, task_id: ids.task, report_id: ids.report, requirement_id: index ? uuid(3000 + index) : ids.requirement, response_item_id: index ? uuid(5000 + index) : ids.item, card_revision_id: index > 1197 ? null : ids.revision, partition: index === 1199 ? "gap" : index === 1198 ? "comply_only" : "response", source: source(index % 2 === 1), rules: [{ code: "negative_deviation", outcome: index === 0 ? "risk" : "clear", reason_code: index === 0 ? "negative_deviation" : "no_negative_deviation", task_certificate_id: null, certificate_revision_id: null }], semantic_status: "not_requested", semantic_reason_code: null, semantic_outcome: null, semantic_citations: [], finding_ids: index === 0 ? [ids.finding] : [] }));
  await page.addInitScript(({ org, role }) => sessionStorage.setItem("bid.org.session", JSON.stringify({ session: "synthetic-session", orgId: org, orgName: "合成单位 A", email: `${role}@example.test` })), { org: state.org, role: state.role });
  const summary = () => ({ report: header(state), mode: state.mode ?? "rules", item_count: 1200, finding_count: state.findings.length, unassessed_count: state.partial ? 2 : 0, certificate_count: 1, groups: Object.values(state.findings.reduce((groups, row) => { const key = `${row.severity}:${row.review_domain}:${row.status}`; groups[key] ??= { severity: row.severity, review_domain: row.review_domain, status: row.status, count: 0 }; groups[key].count++; return groups; }, {})) });
  const permitted = (domain) => !state.stale && ((state.role === "technical" && domain === "technical") || (state.role === "bidder" && domain === "commercial"));
  const pageData = (part, rows, filteredTotal, next = null) => ({ task_id: ids.task, parent_id: ids.report, part, snapshot: `snapshot-${state.decisions.length}`, total: part === "findings" ? state.findings.length : filteredTotal, filtered_total: filteredTotal, returned: rows.length, next_cursor: next, validity: state.stale ? "stale" : "current", parent_revision: null, subject_actions: part === "findings" ? rows.map((row) => ({ subject_id: row.id, actions: [action(row.status === "open" ? "finding_dismiss" : "finding_reopen", permitted(row.review_domain), row.review_domain)] })) : [] });
  const fail = (code, status = 409) => ({ status, payload: result("assessment error", { error: { code, message: code, exit_code: 2 } }, [], { ok: false }) });
  const preview = (body) => {
    const unavailable = state.unknownCost || state.blocker === "redaction_required";
    const estimated = unavailable ? { ...zeroCost(), usd: null, charge: null, task_amount: null, basis: "unknown", unpriced_calls: 1 } : body.mode === "rules" ? zeroCost() : { ...zeroCost(), usd: 0.02, charge: "0.06", task_amount: "0.06", basis: "first_pass_upper_bound", llm_tokens: 400 };
    return { dry_run: true, input: { org_id: ids.org, task_id: ids.task, draft_id: ids.draft, extraction_job_id: ids.extract, document_id: ids.document, input_hash: hash, draft_input_hash: hash, assessment_date: body.assessment_date, scope: "confirmed_draft" }, selected_item_ids: [ids.requirement], estimated_cost: estimated, estimated_charge: estimated.charge, billing_currency: "USD", cost_basis: unavailable ? "unknown" : "known", cost_basis_reason: state.blocker === "redaction_required" ? "redaction_required" : body.mode === "rules" ? "no_model_calls" : "approved_model_prices", admission_blocker: state.blocker, estimated_duration_ms: null, provider_config_id: body.mode === "rules" ? null : uuid(31), provider_source: body.mode === "rules" ? null : "platform", platform_model_id: body.mode === "rules" ? null : "synthetic-model", model_revision: body.mode === "rules" ? null : 1, model: body.mode === "rules" ? null : "synthetic-model", reasoning: body.reasoning ?? null, redaction_revision: 1, redaction_rule_version: "redaction-v1", redacted_counts: { bank_account: 1 }, max_charge: body.max_charge ?? null, mode: body.mode, rule_version: "rules-v1", prompt_version: body.mode === "rules" ? null : "semantic-v1", schema_version: "check-v1", rules_applicable: 1200, semantic_items: body.mode === "rules" ? 0 : 1198, gap_requirements: 2, limitations: ["仅检查已保存初稿，不能代替人工审核"], budget_preflight: { dry_run: true, command: "check run", task_id: ids.task, input_hash: hash, as_of: now, task_budget: budget(), planned_calls: state.blocker === "redaction_required" ? null : body.mode === "rules" ? 0 : 1, maximum_calls: state.blocker === "redaction_required" ? null : body.mode === "rules" ? 0 : 8, estimate: estimated, next_call: null, admission_blocker: state.blocker === "redaction_required" ? "provider_unavailable" : state.blocker, first_pass_fits: state.blocker === "redaction_required" ? null : !state.blocker, full_run_guaranteed: false, cached_job_id: null, cached_result_cost: null, estimated_duration_ms: null, duration_basis: "unknown", uncertainty: body.mode === "rules" ? [] : ["concurrent_spending"] } };
  };
  await page.route("**/*", async (route) => {
    const request = route.request(), url = new URL(request.url()), versionedPath = url.pathname, path = versionedPath.replace(/^\/v4(?=\/)/, ""), method = request.method();
    if (url.origin !== new URL(process.env.E2E_BASE_URL ?? "http://console.test").origin) { state.unexpected.push(`${method} external-origin`); return route.abort(); }
    if (path.startsWith("/app/")) {
      const file = path.startsWith("/app/assets/") ? join(directory, "assets", basename(path)) : join(directory, "index.html");
      return route.fulfill({ contentType: { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" }[extname(file)] ?? "application/octet-stream", body: readFileSync(file) });
    }
    if (method === "GET" && path === "/health") return route.fulfill({ json:{ok:true,command:"health",data:{status:"ok",version:"4.0",real_llm_configured:false,org_signup_enabled:false},items:[],warnings:[],cost:{llm_tokens:0,ocr_pages:0,usd:0},duration_ms:0} });
    const body = request.postData() ? request.postDataJSON() : undefined;
    state.requests.push({ method, path, versioned_path: versionedPath, query: Object.fromEntries(url.searchParams), ...(body ? { body } : {}) });
    try {
      expect(request.headers()["x-org-id"]).toBe(state.org);
      const requirementPath = /^\/(?:tasks\/[^/]+\/(?:extractions\/[^/]+\/(?:requirement-reviews|rejected-items|requirement-confirmations)|requirements\/(?:manual-preview|manual|repair))|requirements\/[^/]+\/(?:review|review-history|review-decisions))$/.test(path) || ["requirement-review"].includes(url.searchParams.get("view"));
      const assessmentPath = /\/(?:assessment-inputs|assessment-citation|checks|scores|score-rubrics)(?:\/|$)/.test(path) || /^\/jobs\//.test(path) || (path.endsWith("/jobs") && ["check", "score_rubric", "score"].includes(url.searchParams.get("kind")));
      expect(versionedPath.startsWith("/v4/")).toBe(assessmentPath || requirementPath);
      if (state.org === ids.orgB && path !== "/org/current") { const failure = fail("not_found", 404); return route.fulfill({ status: failure.status, json: failure.payload }); }
      let response;
      const extra = await state.extra?.({ method, path, url, body });
      if (extra) return route.fulfill({ status: extra.status ?? 200, json: extra.payload });
      if (method === "GET" && path === "/org/current") response = result("org current", { org_id: state.org, role: state.role, user_id: uuid(3) });
      else if (method === "GET" && path === `/tasks/${ids.task}`) response = result("task get", { id: ids.task, org_id: state.org, name: "合成评估任务", tender_number: "SYN-001", deadline: null, budget_usd: 10, created_by: uuid(3), created_at: now, model_redaction_enabled: true, model_redaction_revision: 1, model_redaction_by: uuid(3) });
      // Task pages also load the team workflow header and its live-progress stream; the
      // assessment flows only need an active task and a stream that sends one heartbeat.
      else if (method === "GET" && path === `/tasks/${ids.task}/workflow`) response = result("task workflow", { workflow: { org_id: state.org, task_id: ids.task, owner_user_id: uuid(3), state: "active", revision: 1, access_epoch: 1, last_event_cursor: "opaque-snapshot", co_sign_starred: false, rule_revision: 1, archived_at: null, archived_by_user_id: null } });
      else if (method === "GET" && path === `/tasks/${ids.task}/members`) {
        // The signed-in user's task membership mirrors the org role used by each scenario.
        const [role, review_domains] = { bidder: ["owner", ["commercial"]], technical: ["contributor", ["technical"]] }[state.role] ?? ["observer", []];
        response = result("task member list", { org_id: state.org, task_id: ids.task, next_cursor: null, returned: 1, has_more: false }, [{ org_id: state.org, task_id: ids.task, user_id: uuid(3), display_label: "合成成员", role, active: true, review_domains, revision: 1 }]);
      }
      else if (method === "GET" && path === `/tasks/${ids.task}/events`) return route.fulfill({ contentType: "text/event-stream", body: `data: ${JSON.stringify({ type: "heartbeat", cursor: "opaque-snapshot", as_of: now })}\n\n` });
      else if (method === "GET" && path === `/tasks/${ids.task}/review-rule`) response = result("task review-rule show", { rule: { org_id: state.org, task_id: ids.task, workflow_revision: 1, rule_revision: 1, co_sign_starred: false }, dry_run: false, affected_requirements: 0 });
      else if (method === "GET" && path === `/tasks/${ids.task}/requirements/${ids.requirement}/review-policy`) {
        expect(url.searchParams.get("extraction_job_id")).toBe(ids.extract);
        response = result("card policy show", { policy: { org_id: state.org, task_id: ids.task, extraction_job_id: ids.extract, requirement_id: ids.requirement, revision: 0, primary_domain: "technical", co_sign_required: false, starred: false, co_sign_starred: false, task_rule_revision: 1, required_domains: ["technical"] } });
      }
      else if (method === "GET" && path === `/cards/${ids.card}/signoffs`) response = result("card signoff list", { org_id: state.org, task_id: ids.task, card_id: ids.card, thread_id: null, returned: 0, has_more: false, next_cursor: null, round: null, summary: { status: "not_required", round_revision: 0, required_domains: ["technical"], signed_domains: [], pending_domains: ["technical"] } });
      else if (method === "GET" && path === `/cards/${ids.card}/threads`) {
        expect(Number(url.searchParams.get("limit"))).toBeLessThanOrEqual(100);
        response = result("card thread list", { org_id: state.org, task_id: ids.task, card_id: ids.card, thread_id: null, next_cursor: null, returned: 0, has_more: false });
      }
      else if (method === "GET" && path.startsWith(`/cards/${ids.card}/threads/`) && path.endsWith("/comments")) {
        expect(Number(url.searchParams.get("limit"))).toBeLessThanOrEqual(100);
        response = result("card comment list", { org_id: state.org, task_id: ids.task, card_id: ids.card, thread_id: path.split("/")[4], next_cursor: null, returned: 0, has_more: false });
      }
      else if (method === "GET" && path === `/tasks/${ids.task}/documents`) response = result("task document list", { task_id: ids.task }, [{ id: ids.document, task_id: ids.task, name: "合成招标文件.pdf", sha256: hash, media_type: "application/pdf", page_count: 5, status: "parsed", citation_mode: "page", created_at: now }]);
      else if (method === "GET" && path === `/tasks/${ids.task}/extractions`) response = result("req history", {}, [{ job_id: ids.extract, document_id: ids.document, reasoning: null, model: "synthetic-model", status: "succeeded", created_at: now, finished_at: now, saved: 1200, rejected: 0, tokens: 0, error: null, latest: true }]);
      else if (method === "GET" && ["/confidential-fields", "/confidential-values", `/tasks/${ids.task}/exports`].includes(path)) response = result(path.endsWith("exports") ? "export list" : "confidential list");
      else if (method === "GET" && path === `/tasks/${ids.task}/assessment-inputs`) {
        expect(url.searchParams.get("job")).toBe(ids.extract);
        const draft = { draft_id: ids.draft, extraction_job_id: ids.extract, created_at: now, validity: state.stale ? "stale" : "current", completion: "partial", input_hash: hash };
        response = result("assessment inputs", { org_id: state.org, task_id: ids.task, extraction_job_id: ids.extract, document_id: ids.document, latest_draft: state.noDraft ? null : draft, current_draft: state.noDraft || state.stale ? null : draft, redaction_enabled: true, redaction_revision: 1, task_budget: budget(), actions: [action("check_run", state.role !== "viewer"), action("rubric_generate", state.role !== "viewer"), action("score_run", state.role !== "viewer")] });
      } else if (method === "GET" && path === `/tasks/${ids.task}/jobs`) {
        const terminal = state.cancelled || (!state.holdJob && state.polls > 1), rows = state.submissions ? [{ id: ids.job, task_id: ids.task, extraction_job_id: ids.extract, kind: "check", status: state.cancelled ? "cancelled" : terminal ? "succeeded" : "running", created_at: now, finished_at: terminal ? now : null, attempts: 1, result_id: terminal && !state.cancelled ? ids.report : null, completion: terminal && !state.cancelled ? state.partial ? "partial" : "complete" : null, progress: null, error_code: null, stop_reason: null, cancel: action("job_cancel", !terminal && state.role !== "viewer") }] : [];
        response = result("task job list", { task_id: ids.task, kind: url.searchParams.get("kind"), total: rows.length, next_cursor: null }, rows);
      } else if (method === "GET" && path === `/tasks/${ids.task}/checks`) {
        expect(url.searchParams.get("view")).toBe("console");
        response = result("check list", { task_id: ids.task, total: state.published ? 1 : 0, next_cursor: null }, state.published ? [summary()] : []);
      } else if (method === "POST" && path === `/tasks/${ids.task}/checks`) {
        expect(body.draft_id).toBe(ids.draft);
        expect(body.assessment_date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
        expect(["rules", "combined"]).toContain(body.mode);
        if (body.mode === "rules") { expect(body.reasoning == null).toBe(true); expect(body.max_charge == null).toBe(true); }
        if (body.dry_run) { state.previews++; response = preview(body); response = result("check run", response); }
        else {
          state.submissions++; expect(state.role).not.toBe("viewer"); expect(body.expected_input_hash).toBe(hash);
          if (state.blocker) throw new Error("blocked preview submitted");
          if (state.networkError) return route.abort("connectionreset");
          state.mode = body.mode; if (state.submissions === 1) state.writes++; state.published = true;
          if (state.queueError) { const failure = fail("queue_unavailable", 503); failure.payload.data.job_id = ids.job; return route.fulfill({ status: failure.status, json: failure.payload }); }
          response = result("check run", { job_id: ids.job, status: "queued", cached: state.submissions > 1 });
        }
      } else if (method === "GET" && path === `/jobs/${ids.job}`) {
        state.polls++;
        const running = !state.cancelled && (state.holdJob || (state.polls === 1 && !state.queueError));
        response = result("job status", { id: ids.job, kind: "check", status: state.cancelled ? "cancelled" : running ? "running" : "succeeded", attempts: 1, reasoning: null, error: null, result: running || state.cancelled ? {} : { report_id: ids.report, job_id: ids.job, completion: state.partial ? "partial" : "complete", usage_record_ids: [], charge: "0", billing_currency: "USD", stop_reason: null, checked_requirements: 1200, finding_count: state.findings.length, unassessed_requirements: state.partial ? 2 : 0 } }, [], { ok: !state.cancelled && !state.partial });
      } else if (method === "POST" && path === `/jobs/${ids.job}/cancel`) {
        expect(state.role).not.toBe("viewer"); state.cancelled = true; state.writes++;
        response = result("job cancel", { id: ids.job, status: "cancelled" });
      } else if (method === "GET" && path === `/checks/${ids.report}`) {
        expect(url.searchParams.get("view")).toBe("console");
        if (state.error) { const failure = fail(state.error, state.error === "assessment_entry_too_large" ? 422 : 404); return route.fulfill({ status: failure.status, json: failure.payload }); }
        const part = url.searchParams.get("part") ?? "summary";
        if (part === "summary") response = result("check show", summary(), [], { ok: !state.partial });
        else {
          let rows = [];
          if (part === "findings") {
            rows = state.findings.filter((row) => ["severity", "status", "domain", "entry_id"].every((key) => !url.searchParams.get(key) || url.searchParams.get(key) === row[{ domain: "review_domain", entry_id: "id" }[key] ?? key]));
          } else if (part === "coverage") rows = state.coverage.map((row) => ({ ...row, semantic_status: state.mode === "combined" ? row.partition === "response" ? "assessed" : "unassessed" : "not_requested", semantic_reason_code: state.mode === "combined" && row.partition !== "response" ? "no_confirmed_response" : null, semantic_outcome: state.mode === "combined" && row.partition === "response" ? "no_risk_found" : null, semantic_citations: state.mode === "combined" && row.partition === "response" ? [{ kind: "tender", source: row.source }, { kind: "draft", draft_id: ids.draft, response_item_id: row.response_item_id, card_revision_id: row.card_revision_id, field: "response_text", quote: "存在负偏离" }] : [] }));
          else if (part === "certificates") rows = [{ id: uuid(32), org_id: ids.org, task_id: ids.task, report_id: ids.report, task_certificate_id: uuid(33), certificate_revision_id: uuid(34), requirement_ids: [ids.requirement], assessment_date: "2026-10-05", date_status: "unknown" }];
          else expect(part).toBe("notices");
          const total = rows.length;
          let next = part === "findings" && !url.searchParams.get("cursor") && rows.length > 1 ? "second-page" : null;
          if (part === "findings") rows = url.searchParams.get("cursor") ? rows.slice(1) : rows.slice(0, 1);
          if (part === "coverage") { const offset = Number(url.searchParams.get("cursor") ?? 0), limit = Number(url.searchParams.get("limit") ?? 50); rows = rows.slice(offset, offset + limit); next = offset + rows.length < total ? String(offset + rows.length) : null; }
          response = result("check show", pageData(part, rows, total, next), rows, { ok: !state.partial });
        }
      } else if (path === `/checks/${ids.report}/findings/${ids.finding}/decisions`) {
        if (method === "GET") response = result("check history", { task_id: ids.task, total: state.decisions.length, next_cursor: null }, state.decisions);
        else {
          expect(method).toBe("POST"); expect(permitted(state.findings[0].review_domain)).toBe(true); expect(body.reason.trim().length).toBeGreaterThan(0); expect(body.expected_input_hash).toBe(hash);
          const finding = state.findings[0];
          if (state.conflict) { state.conflict = false; finding.revision++; const failure = fail("revision_conflict"); return route.fulfill({ status: failure.status, json: failure.payload }); }
          expect(body.expected_revision).toBe(finding.revision); expect(body.action).toBe(finding.status === "open" ? "dismiss" : "reopen");
          state.writes++; finding.revision++; finding.status = body.action === "dismiss" ? "dismissed" : "open";
          const decision = { id: uuid(200 + finding.revision), org_id: ids.org, task_id: ids.task, report_id: ids.report, finding_id: ids.finding, revision: finding.revision, action: body.action, reason: body.reason, reason_sha256: hash, decided_by: uuid(3), decided_at: now, actor_kind: "session" }; finding.latest_decision_id = decision.id; state.decisions.push(decision);
          response = result("check decide", { finding, decision });
        }
      } else if (method === "GET" && path === `/tasks/${ids.task}/assessment-citation`) {
        if (state.delayedCitation) await state.delayedCitation;
        expect(url.searchParams.get("parent_id")).toBe(ids.report);
        const entryId = url.searchParams.get("entry_id"), entry = state.findings.find(row => row.id === entryId) ?? state.coverage.find(row => row.id === entryId), origin = url.searchParams.get("origin") ?? "source", offset = Number(url.searchParams.get("offset") ?? 0), text = origin === "source" ? `${entry?.source.quote ?? source().quote}<img src=x onerror=window.__unsafe=1>还有原文未展开` : "固定修订响应：存在负偏离，尚须补齐材料。", quote = origin === "source" ? entry?.source.quote ?? source().quote : "存在负偏离", start = text.indexOf(quote);
        const windowText = offset ? text.slice(offset) : text.slice(0, 18);
        response = result("assessment citation", { parent_id: ids.report, entry_id: entryId, kind: origin === "source" ? "tender" : "draft", verified: true, document_id: origin === "source" ? ids.document : null, page: origin === "source" ? entry?.source.page ?? null : null, location: origin === "source" ? entry?.source.location ?? null : null, draft_id: origin === "source" ? null : ids.draft, response_item_id: origin === "source" ? null : ids.item, card_revision_id: origin === "source" ? null : ids.revision, evidence_id: null, field: origin === "source" ? null : "response_text", text_kind: "context", window: { text: windowText, offset, total_characters: text.length, next_offset: offset + windowText.length < text.length ? offset + windowText.length : null }, quote_start: start, quote_end: start + quote.length, fix: { task_id: ids.task, extraction_job_id: ids.extract, requirement_id: entry?.requirement_id ?? ids.requirement, current_card_id: ids.card, historical_card_revision_id: ids.revision } });
      } else { state.unexpected.push(`${method} ${path}`); return route.abort(); }
      return route.fulfill({ json: response });
    } catch (error) { state.violations.push(error.message); return route.fulfill({ status: 500, json: result("fixture error", { error: { code: "fixture_violation", message: "Synthetic contract assertion failed", exit_code: 4 } }, [], { ok: false }) }); }
  });
  state.verify = () => { expect(state.unexpected).toEqual([]); expect(state.violations).toEqual([]); expect(state.consoleErrors).toEqual([]); };
  return state;
}
