package main

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"strings"

	"github.com/egoist/mygo/ui"
)

type assessmentReportPage struct {
	task, id, kind, base, part, domain, state, severity, requirement, section, outcome, group, cursor, next string
	summary, meta, selected                                                                                 Object
	items, history, coverageCandidates, itemCandidates                                                      []Object
	rows                                                                                                    [][]string
	prior                                                                                                   []string
	reason, decision, selectedDomain, coverageAction, canonical, historyNext                                string
	selectedItems                                                                                           map[string]bool
	serial                                                                                                  uint64
	replacement                                                                                             rubricReplacement
	citation                                                                                                assessmentCitation
	itemNext, coverageNext, canonicalNext                                                                   string
	canonicalDetail, proposal                                                                               Object
	canonicalItems                                                                                          []Object
	itemReady, coverageReady                                                                                bool
}

func newAssessmentReportPage(r Route) NativePage {
	p := &assessmentReportPage{task: r.Params["taskId"], id: r.Params["reportId"], kind: "checks", part: "findings", requirement: r.Query.Get("requirement"), selectedItems: map[string]bool{}}
	if strings.Contains(r.Path, "/scores/") {
		p.kind = "scores"
		p.part = "items"
	}
	if r.Params["rubricId"] != "" {
		p.id = r.Params["rubricId"]
		p.kind = "score-rubrics"
		p.part = "sections"
	}
	p.base = "/tasks/" + p.task + "/" + p.kind + "/" + p.id
	if p.kind == "checks" {
		p.base = "/checks/" + p.id
	}
	return p
}
func (p *assessmentReportPage) Title() string {
	switch p.kind {
	case "scores":
		return "评分报告"
	case "score-rubrics":
		return "评分规则审阅"
	}
	return "检查报告"
}
func (p *assessmentReportPage) Dirty() bool { return p.reason != "" || p.replacement.Dirty() }
func (p *assessmentReportPage) Close() {
	p.serial++
	p.reason = ""
	p.replacement = rubricReplacement{}
	p.citation.Close()
}
func (p *assessmentReportPage) Open(a *NativeApp) { p.loadSummary(a) }
func (p *assessmentReportPage) loadSummary(a *NativeApp) {
	a.Request("GET", p.base+"?view=console&part=summary", nil, func(r Result) {
		report := object(r.Data["report"])
		if p.kind == "score-rubrics" {
			report = r.Data
		}
		if str(report, "task_id") != p.task || str(report, "id") != p.id {
			a.Error = "报告不属于当前任务"
			return
		}
		p.summary = r.Data
		p.load(a)
	})
}
func (p *assessmentReportPage) load(a *NativeApp) {
	p.serial++
	serial := p.serial
	p.selected = nil
	p.citation.Close()
	q := url.Values{"view": {"console"}, "part": {p.part}, "limit": {"50"}}
	filters := map[string]string{"cursor": p.cursor}
	switch p.kind {
	case "checks":
		if p.part == "findings" {
			filters["domain"] = p.domain
			filters["severity"] = p.severity
			filters["status"] = p.state
		}
		if p.part != "notices" {
			filters["requirement_id"] = p.requirement
		}
	case "score-rubrics":
		filters["requirement_id"] = p.requirement
		if p.part == "sections" || p.part == "items" {
			filters["domain"] = p.domain
			filters["state"] = p.state
		}
		if p.part == "blockers" {
			filters["group_id"] = p.group
		}
	case "scores":
		if p.part != "notices" {
			filters["section_key"] = p.section
		}
		if p.part == "items" {
			filters["requirement_id"] = p.requirement
			filters["outcome"] = p.outcome
		}
	}
	for key, value := range filters {
		if value != "" {
			q.Set(key, value)
		}
	}
	a.Request("GET", p.base+"?"+q.Encode(), nil, func(r Result) {
		if serial != p.serial {
			return
		}
		if str(r.Data, "task_id") != p.task || str(r.Data, "parent_id") != p.id || str(r.Data, "part") != p.part || integer(r.Data, "returned") != len(r.Items) {
			a.Error = "报告分页范围不符合契约"
			return
		}
		p.meta = r.Data
		p.items = r.Items
		p.next = str(r.Data, "next_cursor")
		p.rows = nil
		for _, item := range r.Items {
			title := str(item, "title")
			if title == "" {
				title = str(item, "message")
			}
			if title == "" {
				title = str(object(item["source"]), "quote")
			}
			p.rows = append(p.rows, []string{title, str(item, "severity"), str(item, "state") + str(item, "status"), str(item, "review_domain"), str(item, "outcome"), str(item, "estimated_score")})
		}
	})
}
func (p *assessmentReportPage) report() Object {
	if p.kind == "score-rubrics" {
		return p.summary
	}
	return object(p.summary["report"])
}
func (p *assessmentReportPage) allowed(row Object, action string) bool {
	if str(p.report(), "validity") != "current" || str(p.report(), "state") == "superseded" {
		return false
	}
	actions := objects(p.summary["actions"])
	if row != nil {
		actions = nil
		for _, subject := range objects(p.meta["subject_actions"]) {
			if str(subject, "subject_id") == str(row, "id") {
				actions = objects(subject["actions"])
			}
		}
	}
	for _, item := range actions {
		if str(item, "action") == action && item["allowed"] == true {
			return true
		}
	}
	return false
}
func (p *assessmentReportPage) decide(a *NativeApp, operation, action string) {
	if strings.TrimSpace(p.reason) == "" {
		a.Error = "请填写处理理由"
		return
	}
	row := p.selected
	revision := p.report()["revision"]
	if operation != "set" && row != nil {
		revision = row["revision"]
	}
	body := Object{"expected_revision": revision, "expected_input_hash": p.report()["input_hash"], "reason": strings.TrimSpace(p.reason)}
	path := p.base
	switch operation {
	case "set":
		path += "/decisions?view=console"
		body["action"] = action
	case "classify":
		path += "/" + p.part + "/" + str(row, "id") + "/classification"
		body["review_domain"] = p.selectedDomain
	case "coverage":
		path += "/coverage/" + str(row, "requirement_id") + "/decisions"
		body["action"] = p.coverageAction
		ids := []string{}
		if p.coverageAction == "mapped" {
			for _, item := range p.itemCandidates {
				if p.selectedItems[str(item, "id")] {
					ids = append(ids, str(item, "id"))
				}
			}
			if len(ids) == 0 {
				a.Error = "请核对并选择目标评分项"
				return
			}
		}
		body["rubric_item_ids"] = ids
		body["canonical_requirement_id"] = nil
		if p.coverageAction == "duplicate" {
			if p.canonical == "" {
				a.Error = "请选择并核对规范要求原文"
				return
			}
			body["canonical_requirement_id"] = p.canonical
		}
	default:
		path += "/" + p.part + "/" + str(row, "id") + "/decisions"
		body["action"] = action
	}
	a.Request("POST", path, body, func(Result) { p.reason = ""; p.selected = nil; p.loadSummary(a) })
}
func (p *assessmentReportPage) choose(a *NativeApp, row Object) {
	if p.reason != "" {
		a.Confirm("切换报告条目", "未保存处理理由将被丢弃，继续？", func() { p.reason = ""; p.choose(a, row) })
		return
	}
	p.serial++
	p.selected = row
	p.selectedDomain = str(row, "review_domain")
	p.coverageAction = "mapped"
	p.selectedItems = map[string]bool{}
	p.canonical = ""
	p.itemCandidates = nil
	p.coverageCandidates = nil
	p.itemNext, p.coverageNext, p.canonicalNext = "", "", ""
	p.itemReady, p.coverageReady = false, false
	p.canonicalDetail, p.canonicalItems = nil, nil
	if p.kind == "score-rubrics" && p.part == "coverage" {
		p.loadCoverageChoices(a, "items", false, false)
		p.loadCoverageChoices(a, "coverage", false, false)
	}
}
func (p *assessmentReportPage) loadCoverageChoices(a *NativeApp, part string, more, canonical bool) {
	serial, subject := p.serial, str(p.selected, "id")
	q := url.Values{"view": {"console"}, "part": {part}, "limit": {"50"}}
	cursor := p.coverageNext
	if part == "items" {
		q.Set("requirement_id", str(p.selected, "requirement_id"))
		cursor = p.itemNext
	}
	canonicalID := p.canonical
	if canonical {
		q.Set("requirement_id", canonicalID)
		cursor = p.canonicalNext
	}
	if more {
		q.Set("cursor", cursor)
	}
	a.Request("GET", p.base+"?"+q.Encode(), nil, func(r Result) {
		if serial != p.serial || subject != str(p.selected, "id") || canonical && canonicalID != p.canonical {
			return
		}
		if str(r.Data, "task_id") != p.task || str(r.Data, "parent_id") != p.id || r.Data["parent_revision"] != p.summary["revision"] || str(r.Data, "part") != part || len(r.Items) > 50 || integer(r.Data, "returned") != len(r.Items) {
			p.itemReady, p.coverageReady = false, false
			a.Error = "评分规则候选快照已变化，请重新读取报告"
			return
		}
		next := str(r.Data, "next_cursor")
		if canonical && part == "coverage" {
			if len(r.Items) != 1 || str(r.Items[0], "requirement_id") != canonicalID {
				a.Error = "规范要求范围不匹配"
				return
			}
			p.canonicalDetail = r.Items[0]
		} else if canonical {
			if more {
				p.canonicalItems = append(p.canonicalItems, r.Items...)
			} else {
				p.canonicalItems = r.Items
			}
			p.canonicalNext = next
		} else if part == "items" {
			if more {
				p.itemCandidates = append(p.itemCandidates, r.Items...)
			} else {
				p.itemCandidates = r.Items
			}
			p.itemNext, p.itemReady = next, true
		} else {
			if more {
				p.coverageCandidates = append(p.coverageCandidates, r.Items...)
			} else {
				p.coverageCandidates = r.Items
			}
			p.coverageNext, p.coverageReady = next, true
		}
	})
}
func (p *assessmentReportPage) readCanonical(a *NativeApp) {
	p.canonicalDetail, p.canonicalItems = nil, nil
	p.canonicalNext = ""
	if p.canonical == "" {
		return
	}
	p.loadCoverageChoices(a, "coverage", false, true)
	p.loadCoverageChoices(a, "items", false, true)
}
func (p *assessmentReportPage) loadProposal(a *NativeApp) {
	base, id, serial := p.base, p.id, p.serial
	p.proposal = nil
	a.Do(func(ctx context.Context) (func(), error) {
		r, err := a.Client.Request(ctx, "GET", base+"?view=console&part=replacement", nil)
		if err != nil {
			var api *APIError
			if errors.As(err, &api) && api.Status == 404 {
				current, accessErr := a.Client.Request(ctx, "GET", base+"?view=console&part=summary", nil)
				if accessErr != nil {
					return nil, accessErr
				}
				if str(current.Data, "id") == id {
					return func() {
						if serial == p.serial {
							a.Notice = "此评分规则没有已保存的修订提议"
						}
					}, nil
				}
			}
			return nil, err
		}
		return func() {
			if serial != p.serial {
				return
			}
			if str(r.Data, "rubric_id") != id || len(str(r.Data, "snapshot_sha256")) != 64 {
				a.Error = "已保存修订提议范围不匹配"
				return
			}
			p.proposal = r.Data
		}, nil
	})
}
func (p *assessmentReportPage) useProposal(a *NativeApp) {
	if !p.itemReady || !p.coverageReady || p.itemNext != "" {
		a.Error = "请先继续读取全部候选评分项，再恢复覆盖建议"
		return
	}
	for _, entry := range objects(object(p.proposal["replacement"])["coverage"]) {
		if str(entry, "requirement_id") != str(p.selected, "requirement_id") {
			continue
		}
		selected := map[string]bool{}
		for _, key := range stringList(entry["rubric_item_keys"]) {
			found := false
			for _, item := range p.itemCandidates {
				if str(item, "key") == key {
					selected[str(item, "id")] = true
					found = true
					break
				}
			}
			if !found {
				a.Error = "建议引用的评分项不在当前快照，请重新核对"
				return
			}
		}
		p.selectedItems = selected
		p.coverageAction = str(entry, "disposition")
		if p.coverageAction == "pending" {
			p.coverageAction = "mapped"
		}
		p.canonical, p.reason = str(entry, "canonical_requirement_id"), str(entry, "reason")
		if p.canonical != "" {
			p.readCanonical(a)
		}
		a.Notice = "已恢复覆盖建议，请核对原文后明确保存决定"
		return
	}
	a.Notice = "已保存提议没有此要求的覆盖建议"
}
func (p *assessmentReportPage) View(c *ui.Context, a *NativeApp) {
	if p.summary == nil {
		return
	}
	report := p.report()
	job := str(report, "extraction_job_id")

	nativePairs(c, []string{"记录", "有效状态", "完成情况", "总分状态", "总分估计", "可能范围", "已评估小计", "已评估项", "无法评估项"}, []string{p.id, str(report, "validity"), str(report, "completion"), str(p.summary, "total_status"), scoreValue(p.summary, "estimated_total"), scoreRangeLabel(object(p.summary["possible_range"])), scoreValue(p.summary, "assessed_subtotal"), str(p.summary, "assessed_items"), str(p.summary, "unassessable_items")})
	parts := []string{"findings", "coverage", "certificates", "notices"}
	if p.kind == "scores" {
		parts = []string{"items", "sections", "notices"}
	}
	if p.kind == "score-rubrics" {
		parts = []string{"sections", "items", "coverage", "blockers"}
	}
	ui.Row(c).FillWidth().Wrap().Gap(12).AlignItems(ui.End).Children(func() {
		ui.Column(c.Key("report-part")).Width(220).Children(func() {
			if nativeSelect(c, "报告分区", &p.part, parts) {
				p.cursor = ""
				p.prior = nil
				p.load(a)
			}
		})
		if p.part != "notices" && (p.kind != "scores" || p.part == "items") {
			ui.Column(c).Width(220).Children(func() { nativeField(c, "要求 ID 筛选", &p.requirement, false) })
		}
		if p.kind == "checks" && p.part == "findings" {
			ui.Column(c).Width(220).Children(func() {
				nativeSelect(c, "严重程度", &p.severity, []string{"", "disqualification_risk", "deduction_risk", "info"})
			})
			ui.Column(c).Width(220).Children(func() { nativeSelect(c, "处理状态", &p.state, []string{"", "open", "dismissed"}) })
		}
		if p.kind == "score-rubrics" && (p.part == "sections" || p.part == "items") {
			ui.Column(c).Width(220).Children(func() { nativeSelect(c, "审核状态", &p.state, []string{"", "candidate", "confirmed", "rejected"}) })
		}
		if p.kind == "checks" && p.part == "findings" || p.kind == "score-rubrics" && (p.part == "sections" || p.part == "items") {
			ui.Column(c).Width(220).Children(func() { nativeSelect(c, "职责", &p.domain, []string{"", "commercial", "technical", "unclassified"}) })
		}
		if p.kind == "scores" {
			if p.part != "notices" {
				ui.Column(c).Width(220).Children(func() { nativeField(c, "分节键", &p.section, false) })
			}
			if p.part == "items" {
				ui.Column(c).Width(220).Children(func() { nativeSelect(c, "评估结果", &p.outcome, []string{"", "assessed", "unassessable"}) })
			}
		}
		nativeButton(c, "应用报告筛选").OnClick(func() { p.cursor = ""; p.prior = nil; p.load(a) })
	})
	nativeTable(c, []string{"条目 / 原文", "严重程度", "状态", "职责", "评估结果", "估分"}, p.rows, func(i int) { p.choose(a, p.items[i]) })
	nativePager(c, len(p.prior)+1, p.next != "", false, "上一页条目", "下一页条目",
		func() { p.cursor = p.prior[len(p.prior)-1]; p.prior = p.prior[:len(p.prior)-1]; p.load(a) },
		func() { p.prior = append(p.prior, p.cursor); p.cursor = p.next; p.load(a) })
	if p.selected != nil {
		row := p.selected
		nativeHeading(c, str(row, "title"))
		for _, key := range []string{"message", "description", "rule_text", "rationale", "reason", "improvement", "ambiguity_reason", "assessment_mode", "aggregation", "aggregation_rule_text"} {
			if value := str(row, key); value != "" {
				nativeText(c, value)
			}
		}
		for _, key := range []string{"reasons", "improvements", "warnings", "normalization_errors", "deduction_reasons", "strengthening_actions"} {
			for _, value := range stringList(row[key]) {
				nativeText(c, value)
			}
		}
		if p.kind == "scores" {
			nativePairs(c, []string{"估分", "约定范围", "已评估小计", "可能范围", "汇总状态"}, []string{scoreValue(row, "estimated_score"), scoreRangeLabel(object(row["score_range"])), scoreValue(row, "assessed_subtotal"), scoreRangeLabel(object(row["possible_range"])), str(row, "total_status")})
		}
		if source := object(row["source"]); source != nil {
			nativeText(c, str(source, "quote"))
		}
		parent, part := "check", "finding"
		if p.part == "coverage" {
			part = "coverage"
		}
		if p.kind == "scores" {
			parent = "score"
			part = "score_item"
		}
		if p.kind == "score-rubrics" {
			parent = "rubric"
			part = map[string]string{"sections": "rubric_section", "items": "rubric_item", "coverage": "coverage"}[p.part]
		}
		if object(row["source"]) != nil {
			nativeButton(c, "查看固定引用原文").OnClick(func() { p.citation.Open(a, p.task, parent, p.id, part, str(row, "id"), "source", 0) })
		}
		origins := []string{"sources", "citations", "semantic_citations"}
		for _, originKey := range origins {
			for index, entry := range objects(row[originKey]) {
				origin := originKey
				if origin == "semantic_citations" {
					origin = "citations"
				}
				nativeText(c, str(entry, "quote")+str(object(entry["citation"]), "quote"))
				nativeButton(c.Key(originKey+fmt.Sprint(index)), fmt.Sprintf("查看%s引用 %d", origin, index+1)).OnClick(func() { p.citation.Open(a, p.task, parent, p.id, part, str(row, "id"), origin, index) })
			}
		}
		if str(row, "requirement_id") != "" {
			nativeButton(c, "跳转响应卡").OnClick(func() {
				a.Navigate("/org/tasks/" + p.task + "/review?job=" + job + "&requirement=" + str(row, "requirement_id"))
			})
		}
		if p.kind == "checks" && p.part == "findings" {
			nativeField(c, "处理理由", &p.reason, true)
			nativeButton(c, "忽略此风险").Disabled(a.Busy || !p.allowed(row, "finding_dismiss")).OnClick(func() { p.decide(a, "row", "dismiss") })
			nativeButton(c, "重新打开风险").Disabled(a.Busy || !p.allowed(row, "finding_reopen")).OnClick(func() { p.decide(a, "row", "reopen") })
			nativeButton(c, "读取风险决策历史").OnClick(func() {
				a.Request("GET", p.base+"/findings/"+str(row, "id")+"/decisions?limit=50", nil, func(r Result) { p.history = r.Items; p.historyNext = str(r.Data, "next_cursor") })
			})
		}
		if p.kind == "score-rubrics" {
			nativeField(c, "规则处理理由", &p.reason, true)
			if p.part == "coverage" {
				nativeSelect(c, "覆盖决定", &p.coverageAction, []string{"mapped", "duplicate", "excluded", "reopen"})
				nativeButton(c, "恢复已保存的覆盖建议").Disabled(a.Busy || p.proposal == nil || str(row, "disposition") != "pending").OnClick(func() { p.useProposal(a) })
				for _, item := range p.itemCandidates {
					id := str(item, "id")
					checked := p.selectedItems[id]
					if ui.Checkbox(c.Key(id), &checked, str(item, "title")).Changed() {
						p.selectedItems[id] = checked
					}
				}
				if p.itemNext != "" {
					nativeButton(c, "继续读取候选评分项").Disabled(a.Busy).OnClick(func() { p.loadCoverageChoices(a, "items", true, false) })
				}
				choices := []string{""}
				for _, item := range p.coverageCandidates {
					if str(item, "requirement_id") != str(row, "requirement_id") {
						choices = append(choices, str(item, "requirement_id"))
						if str(item, "requirement_id") == p.canonical {
							nativeText(c, str(object(item["source"]), "quote"))
						}
					}
				}
				if p.canonical != "" && !containsString(choices, p.canonical) {
					choices = append(choices, p.canonical)
				}
				if nativeSelect(c, "规范要求", &p.canonical, choices) {
					p.readCanonical(a)
				}
				if p.coverageNext != "" {
					nativeButton(c, "继续读取规范要求候选").Disabled(a.Busy).OnClick(func() { p.loadCoverageChoices(a, "coverage", true, false) })
				}
				if p.canonicalDetail != nil {
					nativeText(c, "规范要求原文："+str(object(p.canonicalDetail["source"]), "quote"))
					nativeButton(c, "展开规范要求固定原文").OnClick(func() {
						p.citation.Open(a, p.task, "rubric", p.id, "coverage", str(p.canonicalDetail, "id"), "source", 0)
					})
					for _, item := range p.canonicalItems {
						nativeText(c, str(item, "title")+" / "+str(item, "rule_text")+" / "+nativeValue(item["state"]))
					}
					if p.canonicalNext != "" {
						nativeButton(c, "继续读取规范要求评分项").Disabled(a.Busy).OnClick(func() { p.loadCoverageChoices(a, "items", true, true) })
					}
				}
				nativeButton(c, "保存覆盖决定").Disabled(a.Busy || !p.itemReady || !p.coverageReady || p.coverageAction == "duplicate" && p.canonicalDetail == nil || !p.allowed(row, "rubric_coverage_decide")).OnClick(func() { p.decide(a, "coverage", "") })
			} else if p.part == "sections" || p.part == "items" {
				nativeSelect(c, "职责分类", &p.selectedDomain, []string{"commercial", "technical"})
				capability := "rubric_section_decide"
				if p.part == "items" {
					capability = "rubric_item_decide"
				}
				nativeButton(c, "保存职责分类").Disabled(a.Busy || !p.allowed(row, "rubric_classify")).OnClick(func() { p.decide(a, "classify", "") })
				for _, action := range []string{"confirm", "reject", "reopen"} {
					target := map[string]string{"confirm": "confirmed", "reject": "rejected", "reopen": "candidate"}[action]
					if str(row, "state") != target {
						button := nativeButton
						if action == "reject" {
							button = nativeDangerButton
						} else if action == "confirm" {
							button = nativePrimaryButton
						}
						button(c, nativeLabel(action)).Disabled(a.Busy || !p.allowed(row, capability)).OnClick(func() { p.decide(a, "row", action) })
					}
				}
			}
		}
	}
	p.citation.View(c, a)
	if p.kind == "score-rubrics" {
		nativeHeading(c, "整套评分规则")
		nativeField(c, "整套规则处理理由", &p.reason, true)
		for _, action := range []string{"confirm", "reject", "reopen"} {
			button := nativeButton
			if action == "reject" {
				button = nativeDangerButton
			} else if action == "confirm" {
				button = nativePrimaryButton
			}
			button(c, "整套规则："+nativeLabel(action)).Disabled(a.Busy || !p.allowed(nil, "rubric_"+action)).OnClick(func() { p.decide(a, "set", action) })
		}
		nativeButton(c, "完整修订评分规则").Disabled(a.Busy || str(report, "validity") != "current" || str(report, "state") == "superseded").OnClick(func() { p.replacement.Load(a, p.task, p.id) })
		nativeButton(c, "读取已保存的修订提议").Disabled(a.Busy).OnClick(func() { p.loadProposal(a) })
		if p.proposal != nil {
			nativeText(c, "已保存提议 SHA-256 "+str(p.proposal, "snapshot_sha256")+"；可在待处理覆盖要求中恢复建议，再单独确认。")
		}
		nativeButton(c, "读取规则决策历史").OnClick(func() {
			a.Request("GET", p.base+"/history?limit=50", nil, func(r Result) { p.history = r.Items; p.historyNext = str(r.Data, "next_cursor") })
		})
		p.replacement.View(c, a, func(id string) { p.reason = ""; a.Navigate("/org/tasks/" + p.task + "/score-rubrics/" + id) })
	}
	for _, entry := range p.history {
		nativeText(c, str(entry, "action")+" / 修订 "+str(entry, "revision")+" / "+str(entry, "occurred_at"))
		nativeText(c, str(entry, "reason"))
	}
	if p.historyNext != "" {
		nativeButton(c, "继续读取决策历史").OnClick(func() {
			path := p.base + "/history"
			if p.kind == "checks" {
				path = p.base + "/findings/" + str(p.selected, "id") + "/decisions"
			}
			a.Request("GET", path+"?limit=50&cursor="+url.QueryEscape(p.historyNext), nil, func(r Result) { p.history = append(p.history, r.Items...); p.historyNext = str(r.Data, "next_cursor") })
		})
	}
	nativeText(c, fmt.Sprintf("当前页 %d 条；建议性估分不代表评标结论。", len(p.items)))
}

func scoreValue(value Object, key string) string {
	if value[key] == nil {
		return "未确定"
	}
	return str(value, key)
}
func scoreRangeLabel(value Object) string {
	if value == nil {
		return "未确定"
	}
	return scoreValue(value, "minimum") + " – " + scoreValue(value, "maximum")
}
