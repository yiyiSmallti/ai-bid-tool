---
kind: adr
---

# 0005 Response cards (响应卡) separate model proposals from human confirmation (人工确认)

Status: accepted.

## Context

Extracted requirements, material declarations, model responses (响应), and human decisions have different trust levels and lifecycles. Writing model text directly into deviation (偏离) tables treats missing, unreviewed, or invalid material as completed responses. Requiring responses for every procedural clause produces commitments with no business meaning. Re-extraction, resource changes, and material replacement must not rewrite existing confirmations.

## Decision and rationale

- Bind cards to an explicitly selected successful extraction job and its requirements within one task. Do not implicitly select the latest job or merge jobs. Revisions append only; the current pointer uses expected-version checks to keep the human-reviewed object traceable.
- Separate response kind from clause disposition. `evidence` needs real material; `commitment` expresses a deliverable obligation and has no Evidence. Humans decide `respond` versus `comply_only` (comply-only, 须遵守) within their review domain (职责). Models write suggestions only, so suggestions cannot close gaps (缺口) themselves.
- Use one professional human reviewer per required domain: technical leads handle technical review, bid specialists handle commercial (商务) review. The approved [team workflow co-sign contract](../plan/team-workflow.md#co-sign-policy-rounds-and-consumption-gates) supersedes the one-reviewer-per-card restriction: explicit requirement policy or the starred-task rule may require both domains, with distinct humans, exact input-bound rounds, invalidation and complete-round database/service/draft/export gates. This approval does not activate co-sign; slice 3 must implement those gates together. Single-domain decisions retain their existing runtime behavior until that cutover. Administrators assign a review domain only for undecided categories and do not automatically gain cross-domain confirmation authority. Tokens, internal/external agents, and workers cannot confirm, reject, decide to supply material, reopen, classify, or determine disposition. Batch disposition commits atomically to avoid ambiguous recovery from half-completed human decisions.
- Declarations do not become proof. Metadata, URLs, feature implementation status, and user-supplied certificate (证照) pages retain their source nature. Matching page text cannot replace a human viewing the original; human confirmation does not certify an original's authenticity. Humans record decisions on material obligations. Commitments cannot falsely claim existing certificates, reports, screenshots, or attachments.
- Models generate only unconfirmed drafts (初稿) and cannot overwrite pending-review, confirmed, or comply-only cards. Fix inputs first, then recheck at actual calls and writes. Models cannot change recorded negative deviation (负偏离) to no deviation (无偏离) or positive deviation (正偏离). Retain valid partial candidates and explicitly report incomplete items so a paid call does not discard all reviewable progress.
- Outbound inputs use the task-selected allowlist. Redact quotes/amounts, contacts/phone numbers, identity numbers, and bank accounts by default. Only human org administrators may disable redaction (遮挡), with an audit entry; individual model commands cannot override it. Initially, certificates send only locally extractable text from selected pages; explicitly report pages without text, without implicit OCR or image transmission.
- Quotes must locate continuous verbatim text in both the actual outbound text and fixed original. The server maps local refs to real material. Never restore redacted values or infer repairs to invalid quotes. Material responses without valid quotes remain drafts needing material and do not automatically become commitments. This preserves failure information and prevents models from choosing an easier response kind to evade material gates.
- Model drafting reuses platform-default models, official reasoning levels, and prepaid admission. Caches bind model catalog, input, and rule versions. Charge each call immediately; cancellation or refusal cannot erase incurred charges. Org BYOK remains defined by the [provider configuration contract](../plan/provider-config.md) and is not a prerequisite.
- Drafts perform table assembly (组表) only and copy valid human-confirmed text verbatim without model rewriting. Each requirement enters exactly one main-table row, comply-only record, or gap. Substantive clauses (实质性条款) take precedence; others enter commercial or technical tables by review domain. Negative deviations cannot be hidden. Older drafts retain snapshots and recalculate invalidation when read; a draft is not export authorization.

## Implementation of the co-sign amendment

The approved amendment is implemented by the input-bound rounds and complete-round
gates described in [Team workflow](../notes/team-workflow.md#how-it-works).
[Migration 0044](../../server/migrations/versions/0044_team_workflow_cosign.py)
extends the revision, signature, Evidence and response-item gates together with
service, draft, export and assessment consumption checks. Existing historical
single-domain decisions keep their actual human records; they receive no synthetic
signatures. Tokens, agents, workers and administrators gain no professional signing
authority. The original decision and its approval history above remain the basis
for this implementation.

## Tradeoffs and scope

Automatic redaction uses versioned text rules with false negatives and over-redaction. Enabling it does not replace source selection and human review. Even uncited material read by the model may affect a response, so all input material conservatively becomes a dependency. This can require more re-review but prevents changes to uncited inputs from being ignored.

Exclude web/white-paper evidence collection, fabricated materials, Rust annotation, prototype (原型) images, full-text semantic checks, scoring, template export, manual requirement additions, merging extraction jobs, dashboards, and automatic memory. Completeness covers only requirements saved by the selected extraction job and does not claim that tender documents (招标文件) contain no missed extraction.

Maintain rules and state machines in [Response cards](../notes/response-cards.md), outbound and citation boundaries in [Model drafting and redaction](../notes/model-drafting-redaction.md), and steps in the [CLI guide](../guides/cli.md#review-responses-and-assemble-a-draft).
