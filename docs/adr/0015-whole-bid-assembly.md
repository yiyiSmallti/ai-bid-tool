---
kind: adr
---

# 0015 Whole bids assembled from the tender's own format chapter

Status: accepted. Date: 2026-10-16.

## Context

The [section export](../plan/export.md) fills six fixed anchors of an uploaded
template with three generic response tables, comply-only clauses, gaps and an
evidence index. Its plan put whole-bid assembly, bid letters and solution
drafting out of scope.

Run on a real public tender, that output cannot be submitted. The tender's
format chapter (投标文件格式) asks for 22 numbered forms: a bid letter, an
opening price sheet, qualification declarations, one procurement deviation
table with its own columns, staff and past-performance tables and a scoring
cross-reference. None of them appear in the export. Eighty of the hundred
points are awarded to the service solution (服务方案) and related measures,
which the tool does not draft at all. The generic tables also differ from the
deviation table the tender prescribes.

Options considered:

- **Improve the generic tables only.** Cleaner rows still leave every form,
  the solution and the tender's own table layout to manual work. Rejected.
- **A fixed national bid template.** Tenders prescribe different forms, order
  and columns; a fixed template would be wrong for most of them. Rejected.
- **Follow the tender's format chapter.** Each required form is located in the
  parsed tender, reproduced in order, and filled from task and company data;
  the deviation table uses the tender's columns; solution chapters are drafted
  against the scoring items. Chosen.

## Decision

A whole bid (整本标书) is assembled from the outline of the tender's format
chapter. Forms keep the tender's wording and are filled only from declared
task and company values; a value the installation does not hold stays a
visible placeholder. Solution chapters are model drafts that a human confirms
chapter by chapter. Confirmed responses fill the tender's deviation table;
unconfirmed evidence never enters the file.

The section export stays for templates that need it.

## Consequences

- The design's goals gain whole-bid drafting; fabricating materials, prices,
  signatures and platform submission stay non-goals. Prices remain
  placeholders that the human fills in.
- Drafted solution text is a proposal like a response card's: a file that
  still holds unconfirmed chapters or placeholders is a draft (审阅稿), not a
  submission copy.
- Delivery follows the [whole-bid plan](../plan/whole-bid.md).
