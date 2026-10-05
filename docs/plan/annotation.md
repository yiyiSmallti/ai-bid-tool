---
kind: plan
---

# Draft contract: local Rust annotation copies of fixed unconfirmed sources

Status: **pending approval, not implemented.** Covers B05 in the [roadmap](roadmap.md).

[agent.md](../../agent.md) requires Pydantic models, Provider interfaces, and CLI JSON before implementation, with confirmation before implementation begins. The [Pydantic/Provider draft](annotation/annotation_contracts.py) and [JSON Schema](annotation/annotation-schemas.json) remain documentation-only, unregistered in runtime API/CLI.

## Full goal and this minimal path

The [design](../design.md) defines Rust annotation; [coverage](roadmap.md) B05/B07/F03 supplies the wider scope. First deliver one independent path: read a real PNG by existing authorized source ID → verify → deterministic Rust crop/boxes/provenance watermark → new local PNG and traceable JSON receipt. Preserve the original and unconfirmed state, without claiming authenticity or parameter analysis.

This proposed implementation order does not reduce the full design. Server job `bid evidence stamp`, archived Evidence/Card, human confirmation (人工确认), and draft/export still need later contracts. Existing [Job](../../server/app/models/entities.py) requires task/document references for every kind except `provider_test`, with document_id referencing a same-org tender Document (招标文件); a source references a certificate (证照) original. Do not fabricate a Document to fit that job. This scope adds only local derived copies, not server stamp jobs or complete B05. Approval covers only the explicit scope below; retain the [full remaining goals](roadmap.md).

## New command, permissions, and data impact

`bid evidence source annotate --id UUID --input PLAN.json --output NEW.png --json`

Local mode reads through existing local PostgreSQL/RLS services; remote mode uses existing authorized preview links/download endpoints. Both use the same Pydantic/annotation Provider and results. Every identity requires evidence:source:read, task:read, certificate:read, certificate:file:read intersected with valid Membership/existing role. Four existing read roles may create local copies; old tokens gain no permissions. Authorized historical sources remain readable; receipts retain active_selection=false with an explicit warning.

**No new server routes, business tables, migrations, scopes, or write objects.** Only a local PNG and redacted CLI receipt; existing server originals/sources/audit are unchanged. API tokens/agents still cannot confirm/export. Copies are not draft/export business outputs and do not automatically become Evidence or confirmed inputs. Do not change real accounts, persistent grants, or security settings; no AI/OCR/web/search/cloud calls or outbound transmission. Downloads go only to the caller's machine; all test material is synthetic. Add one command without changing existing outputs.

## Exact input and pixel decisions (confirmed with approval)

- SourceAnnotationPlan={crop:PixelRect|null, boxes:PixelRect[]}; defaults null/[] mean provenance watermark only. At most 20 boxes, extra=forbid, input JSON<=128KiB. No arbitrary label text, overlay/watermark-removal, or confirm/export fields. PixelRect uses integer x/y>=0 and width/height>=1; each <=8192, right/bottom endpoints <=8192, within actual source bounds.
- Coordinates use original pixels of existing 150dpi RGB PNGs, respecting PDF rotation. crop and all boxes use original-image coordinates; with crop, every box must be fully inside. Reject out-of-bounds instead of clipping/scaling. Never overwrite original PNG/PDF. Draw only 2-pixel red inner borders, without fill, text redraw, or image completion.
- Content is original/cropped pixels at unchanged dimensions. Canvas width=max(content width,1024); center content horizontally with top=0. Add white provenance footer with 16px horizontal/vertical margins and fixed 16px-wide/20px-high ASCII monospace cells, using fixed small glyphs retained in the project without font downloads. Hard-wrap at floor((canvas width-32)/16) characters; footer height=32+20*line count. Users cannot change the watermark.
- Fixed content order: UNCONFIRMED USER-SUPPLIED PDF PAGE, SOURCE_ID, TASK_ID, PAGE, DPI, SOURCE_PROFILE, SOURCE_RENDERED_UTC, ORIGINAL_PDF_SHA256, SOURCE_PNG_SHA256, PLAN_SHA256. Each is KEY=VALUE. Use archived rendered_at in UTC ISO form, explicitly not certificate issue/vendor capture time. IDs/page/hashes trace originals; never present user-supplied PDF as vendor-authenticated material. Preserve Chinese source pixels; use ASCII watermark labels.
- Actual/planned output: each side<=8192px, total pixels<=20000000, encoded PNG<=40MiB and any lower configured limit. Reject excess watermark expansion without downsampling. RGB/no alpha; fixed PNG settings/profile=source-markup-v1 make identical source/plan/profile bytes deterministic.
- plan_sha256 is SHA256 of validated model_dump(mode="json") as UTF8 JSON with explicit defaults, sort_keys=true, separators=(",",":"), ensure_ascii=true, no trailing newline. Preserve box order. Receipt mapping includes original crop, content_offset_x, content_offset_y=0, footer_height_px. Map any output content coordinate back to the source; padding/watermark are not original-file regions.
- annotated_at is local operation UTC time, separate from trusted server rendered_at, without claiming server archival time. Models enforce status=unconfirmed_source, confirmed_by=null, eligible_for_draft_export=false; watermark explicitly says UNCONFIRMED.

## Provider, authorized reads, and local output

Review protocol: asynchronous AnnotationProvider.annotate(content:bytes, source:EvidenceSourceArchive, plan:SourceAnnotationPlan)->(bytes,SourceAnnotationRendering). Python handles authorization, source download/verification, subprocess boundaries, and receipts; the standalone Rust executable crops/boxes/watermarks/encodes. All source metadata comes from the unique complete Archive in existing authorized download Result.items; user plans cannot inject/rebind it. Retain original ID/page/profile/source PNG descriptors in results.

Reuse exact existing service download paths with short signatures and required identity; no redirects/external hosts. Check actual PNG length/SHA/format/dimensions after download, not merely link success. Validate input/new output path first; failure must not start Rust or produce a final file. Execute only explicitly configured trusted local binaries without shell. No tokens/service keys in argv/logs/subprocess environment. Subprocess receives only source PNG/public provenance metadata/plan, using task-private 0700 temporary directories and 0600 files; clean task-generated temporary files afterward.

Proposed internal CLI: `bid-stamp render --request PRIVATE.json --output PRIVATE.png`. Request includes fixed private input_path, authorized Archive, and plan. stdout contains only bounded Rendering JSON, never PNG/keys/source text. Provider chooses new output paths; plan cannot choose arbitrary Rust paths. Deadline: 20 seconds; terminate/reap the owned subprocess on expiry and fail without final files. Bound declared dimensions/input bytes/decoded memory before PNG decoding, not only afterward. Enable PNG codec only, with no other image formats/network. If fixed ASCII glyphs use third-party data, verify licensing and retain attribution first.

Python rechecks actual output PNG/length/hash/dimensions/profile/plan/source binding rather than trusting Rust receipts. Publish validated new files atomically as 0600 PNGs, rejecting existing files/symlinks/path-replacement races. Failure cleans only task temporary files, preserving originals, old downloads, and real directory contents. Do not persist local paths on the server.

## JSON and failure contract

Result1.0 retains ok, command, data, items, warnings, cost, duration_ms. Success command="evidence source annotate", data=SourceAnnotationReceipt, items=[]; cost.llm_tokens=0, ocr_pages=0, usd=0, actual measured duration_ms. Receipt fields: source (complete original Archive), plan, plan_sha256, annotation_profile, annotated_at, output_path, file (PNG name/SHA/bytes/width/height), mapping, status, confirmed_by, eligible_for_draft_export. No images/base64 in JSON; add only one CLI schema entry after approval.

| Exit | Exact cases |
| --- | --- |
| 0 | New PNG actually written and fully verified |
| 2 | Missing arguments; invalid UUID/JSON/coordinates/limits; existing/invalid output paths |
| 3 | Missing trusted executable; temporary network/storage failure; subprocess deadline |
| 4 | Identity/permission/resource failure; source/output integrity failure; non-retryable Rust failure |

No partial-success case or exit5 claim. Reuse 401/403/404 semantics; cross-org/unauthorized resources return 404. Redact errors without source pages/arbitrary inputs/tokens. Failures cannot report success or retain partial final PNGs.

## Compiler precheck and ordinary dependency preparation

The proposal's read-only local precheck found no cargo/rustc in PATH and no corresponding exact paths in ~/.cargo/bin or /opt/homebrew/bin. This was not an exhaustive filesystem search. No installation/download/configuration change occurred. If approved, prepare an isolated tool directory from official sources using task-specific CARGO_HOME/RUSTUP_HOME and per-command PATH, without shell startup or persistent PATH changes. Keep tools/logs local. Official [rustup installation](https://rust-lang.github.io/rustup/installation/index.html) and [environment-variable guidance](https://rust-lang.github.io/rustup/environment-variables.html) support isolation; record actual versions/checks/platform compatibility during implementation rather than guessing versions here.

Rust dependencies are limited to clap, serde, image, sha2 and small JSON codec serde_json. After approval, verify official/crates.io sources/licenses, lock Cargo.lock/actual versions, PNG-only. No large new dependency, paid service, account/key, or persistent grant. Download ordinary tools/dependency code only, never upload project material. If network/permissions block preparation, record the exact blocker promptly without prolonged retries or false cargo passes.

## Acceptance and phased plan after approval

1. Tools/lockfiles: isolated Rust environment, dependencies/license sources; cargo fmt/check/clippy/test and release build. Missing tools cannot justify Python annotation or labeling unrun tests passed.
2. Real Rust pixels: two visibly different synthetic pages; no crop/crop/multiple boxes; original unchanged, outside-box pixels identical, reversible coordinates, visible fixed watermark consistent with receipt; deterministic inputs and multi-page binding. Malformed PNG/dimension/memory/encoding limits, out-of-bounds/excess boxes/watermark-removal fields, timeout/cancel produce no false success.
3. Authorization/files: two orgs/no context, role/scope intersection, old tokens unchanged, invalid membership/historical sources; source/output tampering, redirect/broken stream, missing/nonexecutable binary, path injection, symlink/concurrent overwrite/atomic output/0600/temp cleanup. Only task output is writable; original archives/tables/permissions stay unchanged.
4. Both real CLI modes and Result/schema: one new snapshot; existing outputs/schema unchanged. Native PG/API/existing real worker and isolated Compose/MinIO regression; all applicable pytest/ruff/pyright/Python packages/Rust builds. Test Providers are fake, not claimed real AI; all samples synthetic.
5. Mac support tools verify existing source read/download/errors/repetition/back/cancel and actual local PNG with visible watermark/crop/boxes. This validates the local annotation path, without a new Vue form or claiming product UI. Retain local pixel/file evidence, English mechanism note/report; stop owned services and retain history/data/volumes.

These acceptance goals have not run. Other remaining scope lives in the [roadmap](roadmap.md): complete server stamp jobs/persisted derived images/Evidence/Card/human confirmation, other sources, response checks/scoring/export, Vue, memory, service configuration, budget agents/production, and more.
