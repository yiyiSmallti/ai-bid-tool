import { expect } from "@playwright/test";
import { action, budget, fixture, hash, header, ids, now, result, source, uuid, zeroCost } from "./assessment-fixture.js";

export async function rubricFixture(page, options = {}) {
  const state = await fixture(page, { role: "bidder", ...options });
  state.requirementReview = options.requirementReview === undefined ? { state: "ready", fixed_count: 2, confirmed_count: 2, invalidation_codes: [] } : options.requirementReview;
  state.kind = "score_rubric"; state.rubricRevision = 1; state.rubricState = "candidate"; state.generated = false; state.progressReads = 0; state.replacements = []; state.currentRubricId = ids.rubric;
  state.sections = Array.from({ length: 2 }, (_, index) => ({ id: uuid(600 + index), org_id: ids.org, task_id: ids.task, rubric_id: ids.rubric, requirement_id: uuid(700 + index), key: `section-${index + 1}`, title: `合成分节 ${index + 1}`, order: index + 1, aggregation: index === 1 ? "formula" : "sum", aggregation_assessable: index !== 1, aggregation_rule_text: index === 1 ? "按外部排名计算，系统无法自动合计" : null, score_range: { minimum: "0", maximum: "10" }, weight: null, cap: null, included_in_overall_total: index === 0, ambiguity_reason: index === 1 ? "外部排名无法自动评估" : null, review_domain: "commercial", source: source(index === 1), state: "candidate", revision: 1, confirmed_by: null, confirmed_at: null }));
  state.rubricItems = Array.from({ length: 2 }, (_, index) => ({ id: uuid(800 + index), org_id: ids.org, task_id: ids.task, rubric_id: ids.rubric, section_id: uuid(600 + index), requirement_id: uuid(700 + index), category: "scoring", key: `item-${index + 1}`, title: `合成评分条目 ${index + 1}`, rule_text: `提供真实可核验材料得 ${index + 1} 分，缺少材料不得分。`, order: index + 1, assessment_mode: index === 1 ? "unsupported_formula" : "model_assessable", score_range: { minimum: "0", maximum: "10" }, weight: null, ambiguity_reason: index === 1 ? "外部排名无法自动评估" : null, source: source(index === 1), fingerprint: String(index + 1).repeat(64), review_domain: "commercial", state: "candidate", revision: 1, confirmed_by: null, confirmed_at: null }));
  state.rubricCoverage = Array.from({ length: 2 }, (_, index) => ({ id: uuid(900 + index), org_id: ids.org, task_id: ids.task, rubric_id: ids.rubric, requirement_id: uuid(700 + index), source: source(index === 1), disposition: "pending", rubric_item_ids: [], canonical_requirement_id: null, reason: null, decided_by: null, decided_at: null, revision: 1 }));
  state.events = [];
  const completeness = () => ({ scoring_requirement_count: 2, covered_requirement_count: state.rubricCoverage.filter(r => r.disposition !== "pending").length, pending_requirements: state.rubricCoverage.filter(r => r.disposition === "pending").length, duplicate_groups: 0, unconfirmed_sections: state.sections.filter(r => r.state !== "confirmed").length, unconfirmed_items: state.rubricItems.filter(r => r.state !== "confirmed").length, normalization_errors: 0, section_aggregation_rules_confirmed: state.sections.every(r => r.state === "confirmed"), overall_aggregation_rule_confirmed: true, complete: state.sections.every(r => r.state === "confirmed") && state.rubricItems.every(r => r.state === "confirmed") && state.rubricCoverage.every(r => r.disposition !== "pending") });
  const allowed = (name) => action(name, name === "rubric_classify" ? state.role === "admin" : ["bidder", "technical"].includes(state.role) && !state.stale);
  state.rubricSummary = () => ({ id: state.currentRubricId, org_id: ids.org, task_id: ids.task, extraction_job_id: ids.extract, document_id: ids.document, input_hash: hash, prior_rubric_id: state.replacements.length ? ids.rubric : null, version: state.replacements.length ? 2 : 1, revision: state.rubricRevision, state: state.rubricState, validity: state.stale ? "stale" : "current", created_at: now, section_count: state.sections.length, item_count: state.rubricItems.length, completeness: completeness(), requirement_review: state.requirementReview, overall_aggregation: "sum", overall_aggregation_assessable: true, overall_rule_text: null, overall_score_range: { minimum: "0", maximum: "10" }, overall_cap: null, actions: [allowed("rubric_revise"), { ...allowed("rubric_confirm"), allowed: completeness().complete && state.role === "bidder", blocker_codes: completeness().complete && state.role === "bidder" ? [] : ["rubric_incomplete"] }, allowed("rubric_reopen")] });
  const endpoint = `/tasks/${ids.task}/score-rubrics`, detail = `${endpoint}/${ids.rubric}`;
  const summaryData = () => state.rubricSummary();
  const response = (payload, status = 200) => ({ payload, status });
  state.extra = async ({ method, path, url, body }) => {
    if (method === "GET" && path === `/tasks/${ids.task}/assessment-citation` && url.searchParams.get("parent_kind") === "rubric") {
      const entryId = url.searchParams.get("entry_id"), entry = [...state.sections, ...state.rubricItems, ...state.rubricCoverage].find(row => row.id === entryId);
      expect(entry).toBeTruthy(); expect(url.searchParams.get("parent_id")).toBe(state.currentRubricId);
      return response(result("assessment citation", { parent_id: state.currentRubricId, entry_id: entryId, kind: "tender", verified: true, document_id: ids.document, page: entry.source.page, location: entry.source.location, draft_id: null, response_item_id: null, card_revision_id: null, evidence_id: null, field: null, text_kind: "context", window: { text: entry.source.quote, offset: 0, total_characters: entry.source.quote.length, next_offset: null }, quote_start: 0, quote_end: entry.source.quote.length, fix: { task_id: ids.task, extraction_job_id: ids.extract, requirement_id: entry.requirement_id, current_card_id: ids.card, historical_card_revision_id: null } }));
    }
    if (path === `/tasks/${ids.task}/jobs` && url.searchParams.get("kind") === "score_rubric") {
      return response(result("task job list", { task_id: ids.task, kind: "score_rubric", total: state.generated ? 1 : 0, next_cursor: null }, state.generated ? [{ id: ids.job, task_id: ids.task, extraction_job_id: ids.extract, kind: "score_rubric", status: "running", created_at: now, finished_at: null, attempts: 1, result_id: null, completion: null, progress: null, error_code: null, stop_reason: null, cancel: action("job_cancel", true) }] : []));
    }
    if (path === `/jobs/${ids.job}` && state.generated) {
      const stages = state.progress ?? [null, { scheme: "single_pass", strategy_version: "synthetic-v1", stage: "whole_table", completed_batches: null, total_batches: null, sections_sha256: null }, { scheme: "two_stage", strategy_version: "synthetic-v2", stage: "sections", completed_batches: null, total_batches: null, sections_sha256: null }, { scheme: "two_stage", strategy_version: "synthetic-v2", stage: "items", completed_batches: 1, total_batches: 3, sections_sha256: hash }, { scheme: "two_stage", strategy_version: "synthetic-v2", stage: "validation", completed_batches: null, total_batches: null, sections_sha256: hash }, { scheme: "two_stage", strategy_version: "synthetic-v2", stage: "publication", completed_batches: null, total_batches: null, sections_sha256: hash }];
      // Progress scenarios hold each observed server stage until the test advances
      // it; background polling must not consume a stage before its UI is asserted.
      const step = Math.min(state.progressStep ?? state.progressReads, stages.length), terminal = step >= stages.length;
      state.progressReads++;
      const payload = result("job status", { id: ids.job, kind: "score_rubric", status: terminal ? "succeeded" : "running", attempts: 1, reasoning: null, error: null, result: terminal ? { rubric_id: ids.rubric, job_id: ids.job, completion: state.partial ? "partial" : "complete", version: 1, scoring_requirements: 2, candidate_items: 2, unresolved_requirements: state.partial ? 1 : 0, usage_record_ids: [], charge: "0.06", billing_currency: "USD", stop_reason: state.partial ? "provider_unavailable" : null } : { ...(stages[step] ? { progress: stages[step] } : {}) } }, [], { ok: !(terminal && state.partial), cost: terminal ? { ...zeroCost(), basis: "actual", usd: 0.02, charge: "0.06", task_amount: "0.06" } : zeroCost() });
      return response(payload);
    }
    if (path === endpoint && method === "GET") { expect(url.searchParams.get("view")).toBe("console"); return response(result("score rubric list", { task_id: ids.task, total: 1, next_cursor: null }, [summaryData()])); }
    if ((path === `${endpoint}/preview` || path === endpoint) && method === "POST") {
      expect(body.extraction_job_id).toBe(ids.extract);
      if (body.dry_run) {
        state.previews++;
        const estimate = { ...zeroCost(), usd: 0.02, basis: "first_pass_upper_bound", charge: "0.06", task_amount: "0.06", llm_tokens: 400 };
        return response(result("score rubric generate", { dry_run: true, input: { org_id: ids.org, task_id: ids.task, extraction_job_id: ids.extract, document_id: ids.document, input_hash: hash, scope: "scoring_requirements" }, selected_item_ids: state.rubricCoverage.map(r => r.requirement_id), scoring_requirement_ids: state.rubricCoverage.map(r => r.requirement_id), estimated_cost: estimate, estimated_charge: "0.06", billing_currency: "USD", cost_basis: "known", cost_basis_reason: "approved_model_prices", estimate_kind: "first_pass_upper_bound", admission_blocker: state.blocker, estimated_duration_ms: null, provider_config_id: uuid(31), provider_source: "platform", platform_model_id: "synthetic-model", model_revision: 1, model: "synthetic-model", reasoning: null, redaction_revision: 1, redaction_rule_version: "redaction-v1", redacted_counts: {}, max_charge: body.max_charge ?? null, prompt_version: "rubric-v1", schema_version: "rubric-v1", normalization_rule_version: "normalization-v1", budget_preflight: { dry_run: true, command: "score rubric generate", task_id: ids.task, input_hash: hash, as_of: now, task_budget: budget(), planned_calls: 1, maximum_calls: 8, estimate, next_call: null, admission_blocker: state.blocker, first_pass_fits: !state.blocker, full_run_guaranteed: false, cached_job_id: null, cached_result_cost: null, estimated_duration_ms: null, duration_basis: "unknown", uncertainty: ["concurrent_spending"] } }));
      }
      expect(body.expected_input_hash).toBe(hash); expect(Number(body.max_charge)).toBeGreaterThan(0); expect(state.blocker).toBeNull(); state.submissions++; state.writes++; state.generated = true;
      return response(result("score rubric generate", { job_id: ids.job, status: "queued", cached: false }));
    }
    if ([detail, `${endpoint}/${uuid(23)}`].includes(path) && method === "GET") {
      expect(url.searchParams.get("view")).toBe("console");
      const part = url.searchParams.get("part");
      if (part === "summary") return response(result("score rubric show", summaryData()));
      if (part === "replacement") return state.replacements.length ? response(result("score rubric show", { rubric_id: state.currentRubricId, prior_rubric_id: ids.rubric, snapshot_sha256: hash, replacement: state.replacements.at(-1) })) : response(result("score rubric show", { error: { code: "not_found", message: "not_found", exit_code: 4 } }, [], { ok: false }), 404);
      let rows = part === "sections" ? state.sections : part === "items" ? state.rubricItems : part === "coverage" ? state.rubricCoverage : [];
      const total = rows.length;
      rows = rows.filter(row => ["entry_id", "requirement_id", "domain", "state"].every(key => !url.searchParams.get(key) || url.searchParams.get(key) === row[{ entry_id: "id", domain: "review_domain" }[key] ?? key]));
      const filteredTotal = rows.length, offset = url.searchParams.get("cursor") ? 1 : 0;
      rows = rows.slice(offset, offset + 1);
      const subjects = rows.map(row => ({ subject_id: row.id, actions: [allowed("rubric_classify"), action(part === "sections" ? "rubric_section_decide" : part === "items" ? "rubric_item_decide" : "rubric_coverage_decide", state.role === "bidder" && !state.stale && (part === "coverage" || row.review_domain === "commercial"), part === "coverage" ? "commercial" : row.review_domain)] }));
      return response(result("score rubric show", { task_id: ids.task, parent_id: state.currentRubricId, part, snapshot: state.mixedSnapshot && offset ? "conflicting-snapshot" : `rubric-snapshot-${state.rubricRevision}`, total, filtered_total: filteredTotal, returned: rows.length, next_cursor: state.missingPage ? null : offset + rows.length < filteredTotal ? "second-page" : null, validity: state.stale ? "stale" : "current", parent_revision: state.rubricRevision, subject_actions: subjects }, rows));
    }
    if (path === `${detail}/history`) return response(result("score rubric history", { task_id: ids.task, total: state.events.length, next_cursor: null }, state.events));
    if (path === `${detail}/revisions` && method === "POST") {
      expect(body.expected_revision).toBe(state.rubricRevision); expect(body.expected_input_hash).toBe(hash); expect(body.reason.trim()).not.toBe("");
      expect(body.sections).toHaveLength(state.sections.length); expect(body.items).toHaveLength(state.rubricItems.length); expect(body.coverage).toHaveLength(state.rubricCoverage.length);
      expect(body.items.map(r => r.section_key).every(key => body.sections.some(s => s.key === key))).toBe(true);
      expect(body.coverage.flatMap(r => r.rubric_item_keys).every(key => body.items.some(i => i.key === key))).toBe(true);
      state.replacements.push(body); state.writes++; state.rubricRevision = 1; state.rubricState = "candidate"; state.currentRubricId = uuid(23);
      state.sections = state.sections.map((row, index) => { const { source_section_id, ...changed } = body.sections[index]; return { ...row, ...changed, id: uuid(1600 + index), rubric_id: uuid(23), revision: 1, state: "candidate", review_domain: null, confirmed_by: null, confirmed_at: null }; });
      state.rubricItems = state.rubricItems.map((row, index) => { const { source_item_id, section_key, ...changed } = body.items[index]; return { ...row, ...changed, id: uuid(1700 + index), section_id: state.sections.find(section => section.key === section_key).id, rubric_id: uuid(23), revision: 1, state: "candidate", review_domain: null, confirmed_by: null, confirmed_at: null }; });
      state.rubricCoverage = state.rubricCoverage.map((row, index) => ({ ...row, id: uuid(1900 + index), rubric_id: uuid(23), revision: 1, disposition: "pending", rubric_item_ids: [], canonical_requirement_id: null, reason: null, decided_by: null, decided_at: null }));
      return response(result("score rubric revise", summaryData()));
    }
    if (path === `${detail}/decisions` && method === "POST") {
      expect(state.role).toBe("bidder"); if(body.action === "confirm") expect(state.requirementReview?.state).toBe("ready"); expect(completeness().complete).toBe(true); expect(body.expected_revision).toBe(state.rubricRevision); expect(body.expected_input_hash).toBe(hash); expect(body.reason.trim()).not.toBe(""); state.writes++; state.rubricRevision++; state.rubricState = body.action === "confirm" ? "confirmed" : "candidate";
      return response(result("score rubric decide", summaryData()));
    }
    const child = path.match(new RegExp(`^${endpoint}/${state.currentRubricId}/(sections|items|coverage)/([^/]+)/(decisions|classification)$`));
    if (child && method === "POST") {
      const [, part, childId, operation] = child, rows = part === "sections" ? state.sections : part === "items" ? state.rubricItems : state.rubricCoverage;
      const row = rows.find(r => (part === "coverage" ? r.requirement_id : r.id) === childId); expect(row).toBeTruthy(); expect(body.expected_revision).toBe(row.revision); expect(body.expected_input_hash).toBe(hash); expect(body.reason.trim()).not.toBe("");
      if (operation === "classification") { expect(state.role).toBe("admin"); row.review_domain = body.review_domain; }
      else {
        expect(state.role).toBe("bidder");
        if (part === "coverage") {
          if (body.action === "mapped") expect(body.rubric_item_ids.every(id => state.rubricItems.some(item => item.id === id && item.requirement_id === row.requirement_id && item.rubric_id === state.currentRubricId))).toBe(true);
          row.disposition = body.action === "reopen" ? "pending" : body.action; row.rubric_item_ids = body.rubric_item_ids ?? []; row.canonical_requirement_id = body.canonical_requirement_id ?? null; row.reason = body.reason; row.decided_by = body.action === "reopen" ? null : uuid(3); row.decided_at = body.action === "reopen" ? null : now;
        }
        else { row.state = body.action === "confirm" ? "confirmed" : body.action === "reject" ? "rejected" : "candidate"; row.confirmed_by = body.action === "confirm" ? uuid(3) : null; row.confirmed_at = body.action === "confirm" ? now : null; }
      }
      state.writes++; row.revision++; state.rubricRevision++;
      const event = { kind: part === "coverage" ? "coverage_decision" : operation === "classification" ? "classification" : "decision", id: uuid(1500 + state.writes), org_id: ids.org, task_id: ids.task, rubric_id: state.currentRubricId, revision: row.revision, reason: body.reason, decided_by: uuid(3), decided_at: now, actor_kind: "session", ...(part === "coverage" ? { requirement_id: row.requirement_id, action: body.action, rubric_item_ids: body.rubric_item_ids ?? [], canonical_requirement_id: body.canonical_requirement_id ?? null } : { section_id: part === "sections" ? row.id : null, item_id: part === "items" ? row.id : null, ...(operation === "classification" ? { review_domain: body.review_domain } : { action: body.action }) }) };
      state.events.push(event); return response(result(`score rubric ${part === "coverage" ? "coverage" : operation}`, event));
    }
    return null;
  };
  return state;
}

export async function scoreFixture(page, options = {}) {
  const state = await rubricFixture(page, { partial: true, totalStatus: "unavailable", ...options });
  const rubricRoutes = state.extra;
  if (state.totalStatus === "estimated") {
    state.sections[1].aggregation = "sum"; state.sections[1].aggregation_assessable = true; state.sections[1].aggregation_rule_text = null; state.sections[1].ambiguity_reason = null;
    state.rubricItems[1].assessment_mode = "model_assessable"; state.rubricItems[1].ambiguity_reason = null;
  }
  if (!state.unconfirmedRubric) {
    state.sections.forEach(row => { row.state = "confirmed"; row.confirmed_by = uuid(3); row.confirmed_at = now; });
    state.rubricItems.forEach(row => { row.state = "confirmed"; row.confirmed_by = uuid(3); row.confirmed_at = now; });
    state.rubricCoverage.forEach((row, index) => { row.disposition = "mapped"; row.rubric_item_ids = [state.rubricItems[index].id]; row.decided_by = uuid(3); row.decided_at = now; });
    state.rubricState = "confirmed";
  }
  state.scoreItems = Array.from({ length: 2 }, (_, index) => ({ id: uuid(1800 + index), org_id: ids.org, task_id: ids.task, report_id: ids.score, rubric_item_id: state.rubricItems[index].id, requirement_id: state.rubricCoverage[index].requirement_id, anchor_response_item_id: ids.item, response_item_ids: index === 1 && state.totalStatus !== "estimated" ? [] : [ids.item], section_key: state.sections[index].key, anchor_partition: "response", outcome: index === 1 && state.totalStatus !== "estimated" ? "unassessable" : "assessed", score_range: { minimum: "0", maximum: "10" }, estimated_score: index === 1 && state.totalStatus !== "estimated" ? null : "0", reason_code: index === 1 ? "external_comparison" : "missing_support", reason: index === 1 ? "该分节不进入总分，但其外部排名仍无法评估。" : "已评估的真实零分，缺少条款规定的真实证明。", deduction_reasons: index === 1 && state.totalStatus !== "estimated" ? [] : ["缺少可核验真实材料"], strengthening_actions: index === 1 && state.totalStatus !== "estimated" ? [] : ["补充真实材料并由职责负责人确认"], citations: index === 1 && state.totalStatus !== "estimated" ? [] : [{ kind: "tender", source: source() }, { kind: "draft", draft_id: ids.draft, response_item_id: ids.item, card_revision_id: ids.revision, field: "response_text", quote: "存在负偏离" }], advisory_only: true }));
  state.scoreSections = state.sections.map((row, index) => ({ section_key: row.key, title: row.title, aggregation: row.aggregation, aggregation_assessable: row.aggregation_assessable, aggregation_rule_text: row.aggregation_rule_text, cap: row.cap, configured_range: { minimum: "0", maximum: "10" }, assessed_items: index === 1 && state.totalStatus !== "estimated" ? 0 : 1, unassessable_items: index === 1 && state.totalStatus !== "estimated" ? 1 : 0, assessed_subtotal: "0", possible_range: { minimum: "0", maximum: "10" }, status: index === 0 ? "estimated" : state.totalStatus, estimated_score: index === 0 || state.totalStatus === "estimated" ? "0" : null }));
  if (state.noEligible) {
    state.rubricItems.forEach(row => { row.assessment_mode = "manual_only"; row.ambiguity_reason = "需要人工核对外部排名，模型不能评估"; });
    state.scoreItems.forEach(row => { row.outcome = "unassessable"; row.estimated_score = null; row.response_item_ids = []; row.citations = []; row.deduction_reasons = []; row.strengthening_actions = []; row.reason_code = "manual_only"; row.reason = "需要人工核对外部排名，模型不能评估"; });
    state.scoreSections.forEach(row => { row.status = "unavailable"; row.estimated_score = null; row.assessed_items = 0; row.unassessable_items = 1; });
  }
  state.scoreSummary = () => ({ report: { ...header(state), id: ids.score, completion: state.totalStatus === "estimated" ? "complete" : "partial" }, rubric_id: ids.rubric, rubric_version: 1, assessed_items: state.scoreItems.filter(row => row.outcome === "assessed").length, unassessable_items: state.scoreItems.filter(row => row.outcome === "unassessable").length, section_count: 2, overall_aggregation: "sum", overall_aggregation_assessable: true, overall_rule_text: null, overall_cap: null, assessed_subtotal: "0", total_status: state.totalStatus, possible_range: { minimum: "0", maximum: "10" }, estimated_total: state.totalStatus === "estimated" ? "0" : null });
  const base = `/tasks/${ids.task}/scores`, detail = `${base}/${ids.score}`;
  state.extra = async ({ method, path, url, body }) => {
    const response = (payload, status = 200) => ({ payload, status });
    if (method === "GET" && path === `/tasks/${ids.task}/assessment-citation` && url.searchParams.get("parent_kind") === "score") {
      expect(url.searchParams.get("parent_id")).toBe(ids.score); expect(url.searchParams.get("origin")).toBe("citations");
      const entryId = url.searchParams.get("entry_id"), entry = state.scoreItems.find(row => row.id === entryId), cited = entry?.citations[Number(url.searchParams.get("citation_index") ?? 0)];
      expect(cited).toBeTruthy();
      const tender = cited.kind === "tender", text = tender ? cited.source.quote : `固定修订响应：${cited.quote}，尚须补齐材料。`, quote = tender ? cited.source.quote : cited.quote;
      return response(result("assessment citation", { parent_id: ids.score, entry_id: entryId, kind: tender ? "tender" : "draft", verified: true, document_id: tender ? ids.document : null, page: tender ? cited.source.page : null, location: tender ? cited.source.location : null, draft_id: tender ? null : ids.draft, response_item_id: tender ? null : ids.item, card_revision_id: tender ? null : ids.revision, evidence_id: null, field: tender ? null : "response_text", text_kind: "context", window: { text, offset: 0, total_characters: text.length, next_offset: null }, quote_start: text.indexOf(quote), quote_end: text.indexOf(quote) + quote.length, fix: { task_id: ids.task, extraction_job_id: ids.extract, requirement_id: entry.requirement_id, current_card_id: ids.card, historical_card_revision_id: tender ? null : ids.revision } }));
    }
    if (path === `/tasks/${ids.task}/jobs` && url.searchParams.get("kind") === "score") return response(result("task job list", { task_id: ids.task, kind: "score", total: 0, next_cursor: null }));
    if (path === base && method === "GET") return response(result("score list", { task_id: ids.task, total: 1, next_cursor: null }, [state.scoreSummary()]));
    if (path === detail && method === "GET") {
      expect(url.searchParams.get("view")).toBe("console");
      const part = url.searchParams.get("part");
      if (part === "summary") return response(result("score show", state.scoreSummary(), [], { ok: state.totalStatus === "estimated" }));
      let rows = part === "items" ? state.scoreItems : part === "sections" ? state.scoreSections : [];
      const total = rows.length;
      rows = rows.filter(row => ["outcome", "section_key", "requirement_id", "entry_id"].every(key => !url.searchParams.get(key) || row[key === "entry_id" ? "id" : key] === url.searchParams.get(key)));
      const filteredTotal = rows.length, offset = url.searchParams.get("cursor") ? 1 : 0;
      rows = rows.slice(offset, offset + 1);
      return response(result("score show", { task_id: ids.task, parent_id: ids.score, part, snapshot: "score-snapshot", total, filtered_total: filteredTotal, returned: rows.length, next_cursor: offset + rows.length < filteredTotal ? "second-page" : null, validity: state.stale ? "stale" : "current", parent_revision: null, subject_actions: [] }, rows, { ok: state.totalStatus === "estimated" }));
    }
    if ((path === `${base}/preview` || path === base) && method === "POST") {
      expect(body.draft_id).toBe(ids.draft); expect(body.rubric_id).toBe(ids.rubric);
      if (state.scoreSourceError) return response(result("score run", { error: { code: state.scoreSourceError, message: state.scoreSourceError, exit_code: 2 } }, [], { ok: false }), 409);
      expect(state.requirementReview?.state).toBe("ready");
      if (state.unconfirmedRubric) return response(result("score run", { error: { code: "score_rubric_unconfirmed", message: "score_rubric_unconfirmed", exit_code: 2 } }, [], { ok: false }), 409);
      if (body.dry_run) {
        state.previews++;
        const estimated = state.noEligible ? zeroCost() : { ...zeroCost(), usd: 0.02, charge: "0.06", task_amount: "0.06", basis: "first_pass_upper_bound" };
        return response(result("score run", { dry_run: true, input: { org_id: ids.org, task_id: ids.task, draft_id: ids.draft, extraction_job_id: ids.extract, document_id: ids.document, input_hash: hash, draft_input_hash: hash, assessment_date: body.assessment_date, scope: "confirmed_draft" }, rubric_id: ids.rubric, rubric_version: 1, rubric_input_hash: hash, selected_item_ids: state.rubricItems.map(row => row.id), preflight_unassessable_item_ids: state.noEligible ? state.rubricItems.map(row => row.id) : state.totalStatus === "estimated" ? [] : [state.rubricItems[1].id], estimated_cost: estimated, estimated_charge: estimated.charge, billing_currency: "USD", cost_basis: "known", cost_basis_reason: state.noEligible ? "no_assessable_items" : "approved_model_prices", estimate_kind: "first_pass_upper_bound", admission_blocker: state.blocker, estimated_duration_ms: null, provider_config_id: uuid(31), provider_source: "platform", platform_model_id: "synthetic-model", model_revision: 1, model: "synthetic-model", reasoning: null, redaction_revision: 1, redaction_rule_version: "redaction-v1", redacted_counts: {}, max_charge: body.max_charge ?? null, prompt_version: "score-v1", schema_version: "score-v1", scoring_rule_version: "score-v1", limitations: ["仅评估已保存初稿；评分结果仅供参考"], budget_preflight: { dry_run: true, command: "score run", task_id: ids.task, input_hash: hash, as_of: now, task_budget: budget(), planned_calls: state.noEligible ? 0 : 1, maximum_calls: 8, estimate: estimated, next_call: null, admission_blocker: state.blocker, first_pass_fits: !state.blocker, full_run_guaranteed: false, cached_job_id: null, cached_result_cost: null, estimated_duration_ms: null, duration_basis: "unknown", uncertainty: ["concurrent_spending"] } }));
      }
      expect(body.expected_input_hash).toBe(hash); expect(Number(body.max_charge)).toBeGreaterThan(0); state.writes++; state.submissions++;
      return response(result("score run", { job_id: ids.job, status: "queued", cached: false }));
    }
    if (path === `/jobs/${ids.job}` && state.submissions) {
      state.polls++;
      return response(result("job status", { id: ids.job, kind: "score", status: "succeeded", attempts: 1, reasoning: null, error: null, result: { report_id: ids.score, job_id: ids.job, completion: state.totalStatus === "estimated" ? "complete" : "partial", usage_record_ids: [], charge: state.noEligible ? "0" : "0.06", billing_currency: "USD", stop_reason: null, assessed_items: state.scoreSummary().assessed_items, unassessable_items: state.scoreSummary().unassessable_items, assessed_subtotal: "0", total_status: state.totalStatus, estimated_total: state.totalStatus === "estimated" ? "0" : null } }, [], { ok: state.totalStatus === "estimated", cost: state.noEligible ? zeroCost() : { ...zeroCost(), usd: 0.02, basis: "actual", charge: "0.06", task_amount: "0.06" } }));
    }
    return rubricRoutes({ method, path, url, body });
  };
  return state;
}
