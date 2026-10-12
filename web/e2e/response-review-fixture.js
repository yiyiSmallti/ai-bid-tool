import { expect } from "@playwright/test";
import { fixture as assessmentFixture, ids, uuid, now, hash, source, result } from "./assessment-fixture.js";
export { ids };

export async function responseReviewFixture(page, options = {}) {
  const scope = { org_id: ids.org, task_id: ids.task };
  const state = { role: "technical", archived: false, writes: [], ...options };
  const evidence = [uuid(90), uuid(91)].map((id, index) => ({ id, input: { kind: "resource_field", field_path: `metadata.field${index}`, quote: `真实材料 ${index + 1}` }, material_kind: "declaration", quote_check: "exact_field_match", active_selection: true, selection_id: uuid(93 + index), resource_revision_id: uuid(95 + index), source_archive: null }));
  const card = { ...scope, id: ids.card, requirement_id: ids.requirement, extraction_job_id: ids.extract, revision: 2, revision_id: ids.revision, state: "pending_review", review_domain: "technical", disposition: "respond", suggested_disposition: null, eligibility: "unconfirmed", source: source(), warning_codes: ["source_needs_review"], content: { response_kind: "evidence", response_text: "提供已核对的真实材料。", deviation: "none", deviation_note: "材料参数与本项要求逐项对应。", evidence: evidence.map(item => item.input) }, evidence, confirmed_by: null, confirmed_at: null, reason: null };
  if (options.draft) card.state = "draft";
  const workflow = () => ({ ...scope, owner_user_id: uuid(3), state: state.archived ? "archived" : "active", revision: 1, access_epoch: 1, last_event_cursor: "opaque-snapshot", archived_at: state.archived ? now : null, archived_by_user_id: state.archived ? uuid(3) : null });
  const base = await assessmentFixture(page, { role: state.role, extra: async ({ method, path, url, body }) => {
    const response = data => ({ payload: result("card review", data) });
    if (path === `/tasks/${ids.task}/progress`) return response({ ...scope, event_cursor: "opaque-snapshot", workflow: workflow(), access: { role: ["admin", "bidder"].includes(state.role) ? "owner" : state.role === "viewer" ? "observer" : "reviewer", review_domains: state.role === "technical" ? ["technical"] : state.role === "bidder" ? ["commercial"] : [] } });
    if (path === `/tasks/${ids.task}/simulated-resources`) return response({ selection_ids: [] });
    if (method === "POST" && path === `/documents/${ids.document}/extract`) {
      // GenerationPanel reads reasoning choices on mount; this is not a model submission.
      expect(body).toEqual({ dry_run: true });
      return response({ dry_run: true, document_id: ids.document, parsed: true, reasoning: null, reasoning_levels: [] });
    }
    if (path === `/requirements/${ids.requirement}/review`) return response({ requirement: { ...scope, extraction_job_id: ids.extract, requirement_id: ids.requirement, origin: "extracted", content: { category: "technical", starred: true, text: "合成响应要求", condition: {}, source: source() }, revision: 1, review_hash: hash, state: "confirmed", citation_valid: true, source_pin: { source: source(), document_sha256: hash, chunk_sha256: hash, location_sha256: hash, quote_sha256: hash, start: 0, end: source().quote.length, verifier_version: "literal-v1", binding_sha256: hash }, confirmed_by_user_id: uuid(3), confirmed_at: now, rejected_item: null, next_actor: null }, scope: { ...scope, extraction_job_id: ids.extract, document_id: ids.document, origin: "model", revision: 1, membership_sha256: hash, confirmation_sha256: hash, summary: { total: 1, confirmed: 1, unconfirmed: 0, legacy_unconfirmed: 0, invalidated: 0, invalid_citations: 0, manual_added: 0, rejected_reported: 0, extraction_completeness: "not_asserted" }, last_event_cursor: "opaque-snapshot" } });
    if (path === `/tasks/${ids.task}/requirements`) return { payload: result("req list", {}, [{ id: ids.requirement, job_id: ids.extract, category: "technical", text: "合成响应要求", starred: true, source: source() }]) };
    if (path === `/tasks/${ids.task}/cards`) return { payload: result("card list", { task_id: ids.task, extraction_job_id: ids.extract }, [{ requirement_id: ids.requirement, source: source(), status: card.state, card: structuredClone(card) }]) };
    if (path === `/cards/${ids.card}`) return response(structuredClone(card));
    if (/\/(?:products|features|certificates|profiles|org-profiles|selections|evidence-sources|certificate-files|resources)$/.test(path)) return { payload: result("material list", {}, []) };
    return null;
  } });
  state.card = card; state.base = base; state.verify = base.verify; return state;
}
