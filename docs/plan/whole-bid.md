---
kind: plan
status: "Approved"
---

# Whole-bid assembly

Implements [ADR 0015](../adr/0015-whole-bid-assembly.md). The quality bar is a
real winning bid: every form the tender asks for, in its order, filled, with a
solution that answers each scoring item.

## Requirements

1. **Outline.** The tender's format chapter is found in the parsed document and
   split into its numbered forms (附件 N, 格式 N or a numbered list). Each form
   keeps its label, title, source pages or Word location, verbatim text and a
   kind: `cover`, `letter`, `price`, `declaration`, `copy`, `deviation`,
   `scoring_index`, `roster`, `solution`, or `form` for any other form. A tender without a format chapter
   has no forms in its outline; nothing is invented.
2. **Forms.** A PDF form is rebuilt from its page layout: paragraphs with
   their indents and alignment, drawn or typed blanks with the hint printed on
   them, and tables with merged cells. A Word form is rebuilt from its verbatim
   text, tab-separated rows becoming editable tables. Labels the
   installation can answer (bidder name, address, legal representative,
   project name and number, date) are filled from the task and the selected
   company profile. Unknown values, prices, signatures and seals stay
   highlighted placeholders `【待填：…】`.
3. **Copies.** A form asking for a copy of a licence or certificate shows the
   confirmed certificate pages selected on the task, or a placeholder page
   `【待附：…】` naming what is required.
4. **Deviation table.** Uses the header row of the tender's deviation form.
   Rows are the clauses of the procurement-needs chapter in source order, each
   under its nearest heading; a list table in that chapter is one row naming
   its item count. Until a response is confirmed the row carries the drafted
   `完全响应` and `无偏离`, highlighted. Scoring rules and format-chapter text
   are never rows.
5. **Solution.** The scoring chapter's subjective items (方案, 措施, 人员管理,
   设施, 建议 and the like) each become a chapter. A model first plans the
   chapter headings from the scoring text and the needs chapter, then drafts
   each chapter. Drafts describe methods and plans; company facts the model
   cannot know (staff, certificates, past projects, equipment counts) are
   placeholders. A human confirms each chapter; editing a chapter replaces its
   text and clears its confirmation.
6. **File.** One DOCX: cover, table of contents, then every form in order, each
   starting a new page with its tender label and title as a heading, and the
   solution in the tender's solution form or, without one, after the last
   technical form. A4, 宋体 body, headings in 黑体, page numbers. The file is a
   draft (审阅稿) while any chapter is unconfirmed or any placeholder remains,
   and says so on its cover; otherwise it is a submission copy (提交稿).
7. **Access.** Outline and assembly are human or agent reads; drafting is a
   model job; chapter confirmation and assembling a submission copy are
   human-only, like other confirmations.

## Delivery slices

| Slice | Content | Status |
| --- | --- | --- |
| 1 | Outline service, API and CLI (`bid tender outline`) | Done |
| 2 | Assembly of forms, copies placeholders and deviation table; download (`bid tender whole-bid`) | Done |
| 3 | Solution planning and drafting job, chapter review and confirmation | Planned |
| 4 | Structured company fields for form filling | Planned |
| 5 | Desktop page for outline, drafting, chapter review and assembly | Planned |
| 6 | Acceptance on real tenders against the winning-bid reference | Planned |

## Out of scope

Prices and pricing strategy, signatures and seals, platform upload, and any
generated certificate, contract, social-insurance record or vendor page.
