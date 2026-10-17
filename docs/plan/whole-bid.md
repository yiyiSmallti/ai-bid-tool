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
   split into its numbered forms (附件 N, 格式 N or a numbered list; a list of
   一、 groups holding 1、 2、 forms gives those forms, each group kept as a
   `cover` divider before them; a chapter split into 第N部分 parts numbers
   its 一、 forms within each part, and （1） sub-forms under a form make that
   form their `cover`). Unnumbered forms are found by their own title: an
   index or （格式） title, a title alone on a sparse page or right after a
   divider, a form title heading a page before the first numbered form, and,
   after it, a page-top title over a letter addressed 致：. The front table
   (须知前附表) before the first chapter is kept as an instructions section.
   Each form
   keeps its label, title, source pages or Word location, verbatim text and a
   kind: `cover`, `letter`, `price`, `declaration`, `copy`, `deviation`,
   `scoring_index`, `roster`, `solution`, or `form` for any other form. A tender without a format chapter
   has no forms in its outline; nothing is invented. The outline also lists
   the scoring chapter's items with their score ranges; the scoring index form
   lists them, except the price score, with the page column left for the
   final layout. The outline states where the items came from, the total the
   tender states, the sum of the items' top scores, and why the items may be
   incomplete: none found, no stated total, a sum that differs from the total,
   several packages, or a scoring table too long to read whole.
1a. **Scoring review.** When the rule table parser cannot read a scoring
   table, a model job reads the table as numbered source lines and lists each
   scored item with the line it starts on and its top score. An item is kept
   only when its name starts on that line (whole, wrapped, or assembled from
   the leading words of a PDF cell split between rule lines) and its top score
   appears between it and the next item; rows of one item scored separately on
   one line are added together. A model name that differs from the source
   only by cell splitting or a dropped character takes the source's name; an
   unnamed sub-row adds its score to the item above. Where several packages
   each have a table, the first package's items are listed, the last item's
   rule ending at the next package or total. An item is subjective when its
   rule is judged (优/良/中, 分析, 综合评审) rather than counted (每提供…得 N
   分) or met-or-not. The outline and solution drafting use the
   newest succeeded review of the same parsed text; a reparsed tender falls
   back to the parser until reviewed again. The review sends masked text and
   is a model job like drafting (requirement 7).
2. **Forms.** A PDF form is rebuilt from its page layout: paragraphs with
   their indents and alignment, drawn or typed blanks with the hint printed on
   them, and tables with merged cells. A Word form is rebuilt from its verbatim
   text, tab-separated rows becoming editable tables. Labels the
   installation can answer (bidder name, address, phone, legal representative,
   unified social credit code, project name and number) are filled from the task and the selected
   company profile, whether the label precedes the blank or a bracketed hint
   such as （招标人名称） follows it or stands alone in the text. Unknown values, prices, signatures and seals stay
   highlighted placeholders `【待填：…】`.
3. **Copies.** A form asking for a copy of a licence or certificate shows the
   confirmed certificate pages selected on the task, or a placeholder page
   `【待附：…】` naming what is required.
4. **Deviation table.** Uses the header row of the tender's deviation form.
   Rows are the clauses of the procurement-needs chapter in source order, each
   under its nearest heading; a list table in that chapter is one row naming
   its item count. A row whose quoted requirements all have confirmed response
   cards carries their response text and the worst of their deviations; until
   then it carries the drafted `完全响应` and `无偏离`, highlighted. The page column
   points, as a page-reference field, at the solution subsection whose heading
   the clause best covers; a clause no heading answers, such as a payment term,
   keeps an empty cell. Scoring rules and format-chapter text are never rows.
5. **Solution.** The scoring chapter's subjective items (方案, 措施, 人员管理,
   设施, 建议 and the like) each become a chapter. A model first plans the
   chapter's sections and their points from the scoring rule and the needs
   chapter: four to six sections, six to eight from 10 points, eight to ten
   from 20 points. Each section is then drafted in its own call as an
   introduction and one subsection per point, with a table where one fits.
   A section call has a bounded output and is retried, shorter, when cut off
   or malformed. Each chapter covers only its own item; other items' chapters
   are named to the model so it does not repeat them. Totals the tender does
   not state are not counted by the model.
   Service commitments (response times, frequencies, deadlines, the post
   doing each task) are concrete; a section that leaves one as a placeholder
   is redrafted. Headings carry no numbering; the file numbers them
   (`N、`, `N.M`, `N.M.K`). The needs text is bounded by
   `Settings.solution_needs_max_chars` in
   [config.py](../../server/app/core/config.py). Drafts describe methods and plans;
   company facts the model
   cannot know (staff, certificates, past projects, equipment counts) are
   placeholders. A human confirms each chapter; editing a chapter replaces its
   text and clears its confirmation. Both editing and confirmation require
   the expected current revision; model jobs never confirm a chapter.
6. **File.** One DOCX: cover, table of contents, then every form in order, each
   starting a new page with its tender label and title as a heading, and the
   solution immediately after the technical cover form, or after the solution
   form when there is no technical cover, or after the last form when neither
   exists. Missing subjective chapters remain visible as `【待起草：…】`.
   The contents page lists forms, chapters and their sections, and the scoring index's page
   column points at each solution chapter, both as page-reference fields.
   A4, 宋体 body, headings in 黑体, page numbers. The file is a
   draft (审阅稿) while any chapter is unconfirmed or any placeholder remains,
   and says so on its cover; otherwise it is a submission copy (提交稿).
7. **Access.** Outline and assembly are human or agent reads; drafting is a
   model job available to the human and actors holding `solution:draft`;
   chapter reads and exports containing chapter text require `solution:read`.
   The scoring review needs `solution:draft`.
   Editing and confirmation are
   human-only (`solution:edit` and `solution:confirm`), as is assembling a
   submission copy.

## Delivery slices

| Slice | Content | Status |
| --- | --- | --- |
| 1 | Outline service, API and CLI (`bid tender outline`) | Done |
| 2 | Assembly of forms, copies placeholders and deviation table; download (`bid tender whole-bid`) | Done |
| 3 | Solution planning and drafting job, chapter review and confirmation | Done |
| 4 | Structured company fields for form filling | Done |
| 5 | Desktop page for outline, drafting, chapter review and assembly | Done |
| 6 | Acceptance on real tenders against the winning-bid reference | Planned |

## Out of scope

Prices and pricing strategy, signatures and seals, platform upload, and any
generated certificate, contract, social-insurance record or vendor page.
