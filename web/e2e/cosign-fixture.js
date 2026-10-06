import { expect } from "@playwright/test";
import { fixture as assessmentFixture, ids, uuid, now, hash, source, result } from "./assessment-fixture.js";
export { ids };

export async function cosignFixture(page, options = {}) {
  const scope = { org_id: ids.org, task_id: ids.task };
  const state = { role: "technical", archived: false, conflict: false, signed: [], signatures: [], roundRevision: 1, policyRevision: 1, workflowRevision: 1, ruleRevision: 1, required: true, rule: false, writes: [], previews: 0, reason: "", ...options };
  state.signatures = state.signatures.map((item, index) => ({ ...scope, id: uuid(140 + index), card_id: ids.card, round_id: uuid(71), purpose: "response", signer_user_id: uuid(4), signer_org_role: item.domain === "commercial" ? "bidder" : "technical", reviewed_evidence_ids: [uuid(90), uuid(91)], reviewed_warning_codes: ["source_needs_review"], reason_sha256: hash, client_request_id: uuid(160 + index), created_at: now, ...item }));
  const evidence = [uuid(90), uuid(91)].map((id, index) => ({ id, input: { kind: "resource_field", field_path: `metadata.field${index}`, quote: `真实材料 ${index + 1}` }, material_kind: "declaration", quote_check: "exact_field_match", active_selection: true, selection_id: uuid(93 + index), resource_revision_id: uuid(95 + index), source_archive: null }));
  const card = { ...scope, id: ids.card, requirement_id: ids.requirement, extraction_job_id: ids.extract, revision: 2, revision_id: ids.revision, state: "pending_review", review_domain: "technical", disposition: "respond", suggested_disposition: null, eligibility: "unconfirmed", source: source(), warning_codes: ["source_needs_review"], content: { response_kind: "evidence", response_text: "提供已核对的真实材料。", deviation: "none", deviation_note: "材料参数与本项要求逐项对应。", evidence: evidence.map(item => item.input) }, evidence, confirmed_by: null, confirmed_at: null, reason: null };
  if (options.draft) card.state = "draft";
  const required = () => state.required || state.rule ? ["commercial", "technical"] : ["technical"];
  const workflow = () => ({ ...scope, owner_user_id: state.role === "bidder" || state.role === "admin" ? uuid(3) : uuid(4), state: state.archived ? "archived" : "active", revision: state.workflowRevision, access_epoch: 1, last_event_cursor: "opaque-snapshot", co_sign_starred: state.rule, rule_revision: state.ruleRevision, archived_at: state.archived ? now : null, archived_by_user_id: state.archived ? uuid(3) : null });
  const policy = () => ({ ...scope, extraction_job_id: ids.extract, requirement_id: ids.requirement, revision: state.policyRevision, primary_domain: "technical", co_sign_required: state.required, starred: true, co_sign_starred: state.rule, task_rule_revision: state.ruleRevision, required_domains: required() });
  const round = () => state.roundRevision ? ({ ...scope, id: uuid(70 + state.roundRevision), card_id: ids.card, extraction_job_id: ids.extract, requirement_id: ids.requirement, round_revision: state.roundRevision, card_revision: card.revision, card_revision_id: card.revision_id, policy_revision: state.policyRevision, task_rule_revision: state.ruleRevision, access_epoch: 1, evidence_sha256: hash, requirement_sha256: hash, citation_sha256: hash, content_sha256: hash, required_domains: required(), purpose: state.purpose ?? "response", intended_disposition: state.intended ?? "respond", prior_card_state: state.purpose === "disposition" ? "draft" : "pending_review", disposition_reason: state.purpose === "disposition" ? state.reason : null, state: state.invalidated ? "invalidated" : state.signed.length === required().length ? "complete" : "open", created_at: now }) : null;
  const summary = () => ({ status: state.invalidated ? "invalidated" : state.signed.length === required().length ? "complete" : state.signed.length ? "partial" : state.roundRevision ? "pending" : "not_required", round_revision: state.roundRevision, required_domains: required(), signed_domains: state.signed, pending_domains: required().filter(domain => !state.signed.includes(domain)) });
  const failure = code => ({ status: 409, payload: result("cosign", { error: { code, message: code, exit_code: 2 } }, [], { ok: false }) });
  const base = await assessmentFixture(page, { role: state.role, extra: async ({ method, path, url, body }) => {
    const response = data => ({ payload: result("cosign", data) });
    if (path === `/tasks/${ids.task}/workflow`) return response({ workflow: workflow() });
    if (path === `/tasks/${ids.task}/members`) return { payload: result("task member list", { ...scope, returned: 1, has_more: false, next_cursor: null }, [{ ...scope, user_id: uuid(3), display_label: "合成成员", active: true, role: ["admin", "bidder"].includes(state.role) ? "owner" : state.role === "viewer" ? "observer" : "reviewer", review_domains: state.role === "technical" ? ["technical"] : state.role === "bidder" ? ["commercial"] : [], revision: 1 }]) };
    if (path === `/tasks/${ids.task}/member-candidates`) return { payload: result("candidates", { ...scope, returned: 0, has_more: false, next_cursor: null }) };
    if (path === `/tasks/${ids.task}/simulated-resources`) return response({ selection_ids: [] });
    if (path === `/tasks/${ids.task}/requirements`) return { payload: result("req list", {}, [{ id: ids.requirement, job_id: ids.extract, category: "technical", text: "合成会签要求", starred: true, source: source() }]) };
    if (path === `/tasks/${ids.task}/cards`) return { payload: result("card list", { task_id: ids.task, extraction_job_id: ids.extract }, [{ requirement_id: ids.requirement, source: source(), status: card.state, card: structuredClone(card) }]) };
    if (path === `/cards/${ids.card}`) return response(structuredClone(card));
    if (/\/(?:products|features|certificates|profiles|org-profiles|selections|evidence-sources|certificate-files|resources)$/.test(path)) return { payload: result("material list", {}, []) };
    if (path === `/tasks/${ids.task}/review-rule`) {
      if (method === "PUT") {
        expect(body.expected_revision).toBe(state.workflowRevision); expect(body.reason.trim()).not.toBe("");
        if (body.dry_run) state.previews++; else { expect(state.previews).toBeGreaterThan(0); state.rule = body.co_sign_starred; state.ruleRevision++; state.workflowRevision++; state.writes.push({ path, body }); }
      }
      return response({ rule: { ...scope, workflow_revision: state.workflowRevision, rule_revision: state.ruleRevision, co_sign_starred: state.rule }, dry_run: body?.dry_run ?? false, affected_requirements: 1 });
    }
    if (path === `/tasks/${ids.task}/requirements/${ids.requirement}/review-policy`) {
      expect(url.searchParams.get("extraction_job_id")).toBe(ids.extract);
      if (method === "PUT") { expect(body.expected_policy_revision).toBe(state.policyRevision); state.required = body.co_sign_required; state.policyRevision++; state.signed = []; state.invalidated = true; state.writes.push({ path, body }); }
      return response({ policy: policy() });
    }
    if (path === `/cards/${ids.card}/signoffs` && method === "GET") return { payload: result("card signoff list", { ...scope, card_id: ids.card, thread_id: null, round: round(), summary: summary(), returned: state.signatures.length, next_cursor: null, has_more: false }, state.signatures) };
    if (path === `/cards/${ids.card}/review-rounds`) {
      expect(body.expected_revision).toBe(card.revision); expect(body.reason.trim()).not.toBe(""); expect(body.purpose).toBe("disposition");
      state.invalidated = false; state.purpose = "disposition"; state.intended = body.intended_disposition; state.reason = body.reason; state.roundRevision++; state.signed = []; state.writes.push({ path, body }); return response({ round: round() });
    }
    if (path === `/cards/${ids.card}/signoffs` && method === "POST") {
      state.writes.push({ path, body }); expect(state.archived).toBe(false); expect(["technical", "bidder"]).toContain(state.role); expect(body.selected_domain).toBe(state.role === "technical" ? "technical" : "commercial");
      expect(body.expected_revision).toBe(card.revision); expect(body.expected_round).toBe(state.roundRevision); expect(body.reviewed_warning_codes).toEqual(card.warning_codes); expect(body.reason.trim()).not.toBe("");
      if (body.purpose === "response") expect(body.reviewed_evidence_ids).toEqual(evidence.map(item => item.id)); else expect(body.reviewed_evidence_ids).toBeUndefined();
      if (state.conflict) { state.conflict = false; state.roundRevision++; state.signed = []; return failure("stale_round"); }
      const signature = { ...scope, id: uuid(100 + state.signatures.length), card_id: ids.card, round_id: round().id, purpose: body.purpose, domain: body.selected_domain, signer_user_id: uuid(3), signer_org_role: state.role, reviewed_evidence_ids: body.reviewed_evidence_ids ?? [], reviewed_warning_codes: body.reviewed_warning_codes, reason_sha256: hash, client_request_id: body.client_request_id, created_at: now };
      state.signatures.unshift(signature); state.signed.push(body.selected_domain);
      if (state.signed.length === required().length) { card.revision++; if (state.purpose === "disposition") { card.disposition = state.intended; card.eligibility = state.intended === "comply_only" ? "comply_only" : "unconfirmed"; } else { card.state = "confirmed"; card.eligibility = "eligible"; card.confirmed_by = uuid(3); card.confirmed_at = now; } }
      return response({ summary: summary(), signature, current_card_revision: card.revision, current_card_revision_id: card.revision_id });
    }
    return null;
  } });
  state.card = card; state.base = base; state.verify = base.verify; return state;
}
