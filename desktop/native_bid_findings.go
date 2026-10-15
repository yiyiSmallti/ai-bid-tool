package main

import (
	"context"
	"net/url"
	"strings"

	"github.com/egoist/mygo/ui"
)

type bidFindingsPanel struct {
	task, id, severity, state, outcome, next, reason, domain, action, historyPart, historyNext string
	run, meta, selected                                                                        Object
	items, history                                                                             []Object
	rows                                                                                       [][]string
	serial                                                                                     uint64
	conflicted                                                                                 bool
}

func (p *bidFindingsPanel) Dirty() bool { return p.reason != "" }
func (p *bidFindingsPanel) Close() {
	p.serial++
	p.id = ""
	p.meta = nil
	p.selected = nil
	p.items = nil
	p.rows = nil
	p.history = nil
	p.reason = ""
	p.next, p.historyNext = "", ""
	p.conflicted = false
}
func (p *bidFindingsPanel) Load(a *NativeApp, task string, run Object) {
	p.Close()
	p.task = task
	p.run = run
	p.id = str(run, "id")
	p.load(a, false)
}
func (p *bidFindingsPanel) valid(r Result) bool {
	return str(r.Data, "review_id") == p.id && str(r.Data, "input_hash") == str(p.run, "input_hash") && containsString([]string{"current", "stale"}, str(r.Data, "validity")) && len(r.Items) <= 50
}
func (p *bidFindingsPanel) load(a *NativeApp, more bool) {
	serial := p.serial
	q := url.Values{"limit": {"50"}}
	for k, v := range map[string]string{"severity": p.severity, "state": p.state, "outcome": p.outcome} {
		if v != "" {
			q.Set(k, v)
		}
	}
	if more {
		q.Set("cursor", p.next)
	} else {
		p.items = nil
		p.rows = nil
		p.selected = nil
		p.history = nil
		p.reason = ""
		p.next, p.historyNext = "", ""
		p.meta = nil
	}
	a.Request("GET", "/bid-reviews/"+p.id+"/findings?"+q.Encode(), nil, func(r Result) {
		if serial != p.serial {
			return
		}
		if !p.valid(r) {
			a.Error = "发现项范围无法核验"
			return
		}
		known := map[string]bool{}
		for _, item := range p.items {
			known[str(item, "id")] = true
		}
		for _, row := range r.Items {
			id := str(row, "id")
			if known[id] || str(row, "review_id") != p.id || str(row, "task_id") != p.task || !containsString([]string{"fatal", "high", "medium"}, str(row, "severity")) || !containsString([]string{"open", "dismissed", "confirmed"}, str(row, "state")) || integer(row, "revision") < 1 {
				a.Error = "发现项数据无法核验"
				return
			}
			known[id] = true
		}
		p.items = append(p.items, r.Items...)
		p.meta = r.Data
		p.conflicted = false
		p.next = str(r.Data, "next_cursor")
		p.rows = tableRows(p.items, "title", "severity", "state", "outcome", "review_domain")
	})
}
func (p *bidFindingsPanel) readHistory(a *NativeApp, part string, more bool) {
	if p.selected == nil {
		return
	}
	p.historyPart = part
	id := str(p.selected, "id")
	serial := p.serial
	q := url.Values{"limit": {"50"}}
	if more {
		q.Set("cursor", p.historyNext)
	} else {
		p.history = nil
	}
	a.Request("GET", "/bid-reviews/"+p.id+"/findings/"+id+"/"+part+"?"+q.Encode(), nil, func(r Result) {
		if serial != p.serial || str(p.selected, "id") != id || p.historyPart != part {
			return
		}
		if !p.valid(r) {
			a.Error = "发现项历史范围无法核验"
			return
		}
		for _, row := range r.Items {
			if str(row, "finding_id") != id || str(row, "review_id") != p.id {
				a.Error = "发现项历史记录不匹配"
				return
			}
		}
		p.history = append(p.history, r.Items...)
		p.historyNext = str(r.Data, "next_cursor")
	})
}
func (p *bidFindingsPanel) mutate(a *NativeApp) {
	row := p.selected
	if row == nil || p.reason == "" || str(p.meta, "validity") != "current" || str(p.run, "validity") != "current" {
		return
	}
	body := Object{"request_id": requestID(), "reason": strings.TrimSpace(p.reason), "expected_revision": row["revision"], "expected_input_hash": p.meta["input_hash"], "expected_decision_id": row["latest_decision_id"]}
	part := "decisions"
	if p.action == "classify" {
		part = "classification"
		body["review_domain"] = p.domain
	} else {
		body["action"] = p.action
	}
	serial := p.serial
	path := "/bid-reviews/" + p.id + "/findings/" + str(row, "id") + "/" + part
	a.Do(func(ctx context.Context) (func(), error) {
		_, err := a.Client.Request(ctx, "POST", path, body)
		return func() {
			if serial != p.serial {
				return
			}
			if err != nil {
				if mgConflict(err) {
					p.serial++
					p.meta, p.selected = nil, nil
					p.items, p.rows, p.history = nil, nil, nil
					p.next, p.historyNext = "", ""
					p.conflicted = true
				}
				a.handleError(err)
				return
			}
			p.serial++
			p.reason = ""
			p.load(a, false)
		}, nil
	})
}
func (p *bidFindingsPanel) View(c *ui.Context, a *NativeApp) {
	if p.id == "" {
		return
	}
	nativeHeading(c, "发现项与人工审查")
	nativeText(c, "评标委员会决定最终结果；人工决定保留机器原始结论，不确认材料证据，也不改变评分。")
	oldSeverity, oldState, oldOutcome := p.severity, p.state, p.outcome
	changed := nativeSelect(c, "发现项风险筛选", &p.severity, []string{"", "fatal", "high", "medium"})
	changed = nativeSelect(c, "发现项状态筛选", &p.state, []string{"", "open", "dismissed", "confirmed"}) || changed
	changed = nativeSelect(c, "响应筛选", &p.outcome, []string{"", "responded", "deviation", "missing", "unknown"}) || changed
	if changed {
		severity, state, outcome := p.severity, p.state, p.outcome
		p.severity, p.state, p.outcome = oldSeverity, oldState, oldOutcome
		reload := func() { p.severity, p.state, p.outcome = severity, state, outcome; p.serial++; p.load(a, false) }
		if p.Dirty() {
			a.Confirm("切换发现项筛选", "未保存理由将被丢弃，继续？", reload)
		} else {
			reload()
		}
	}
	if p.conflicted {
		nativeText(c, "当前发现项已变化或写入结果未确定。理由已保留；请刷新并重新核对，不会自动重试。")
	}
	nativeButton(c, "刷新发现项").Disabled(a.Busy).OnClick(func() { p.serial++; p.load(a, false) })
	nativeTable(c, []string{"标题", "风险", "状态", "响应", "职责"}, p.rows, func(i int) {
		selectRow := func() {
			p.selected = p.items[i]
			p.domain = str(p.selected, "review_domain")
			if p.domain == "" {
				p.domain = "commercial"
			}
			p.action = "classify"
			p.reason = ""
			p.history = nil
		}
		if p.reason != "" {
			a.Confirm("切换发现项", "未保存理由将被丢弃，继续？", selectRow)
		} else {
			selectRow()
		}
	})
	if p.next != "" {
		nativeButton(c, "更多发现项").OnClick(func() { p.load(a, true) })
	}
	if p.selected == nil {
		return
	}
	row := p.selected
	nativeHeading(c, str(row, "title"))
	nativeText(c, "机器原始结论 "+nativeStatus(row["outcome"])+" / 影响 "+nativeStatus(row["impact"]))
	basis := object(row["basis"])
	nativeText(c, "依据 "+nativeValue(basis["kind"])+" / 规则或提示词版本 "+str(basis, "rule_or_prompt_version")+" / 模型 "+str(basis, "model")+" / 模型置信度 "+nativeValue(basis["confidence"])+"（不代表准确率）")
	nativeText(c, str(row, "explanation"))
	for _, kind := range []string{"tender_support", "bid_support"} {
		nativeHeading(c, kind)
		for _, citation := range objects(row[kind]) {
			bidCitationText(c, citation)
		}
	}
	absence := object(row["absence_search"])
	if absence != nil {
		nativeHeading(c, "缺失检索范围")
		nativeText(c, str(absence, "coverage"))
		for _, page := range objects(absence["searched_pages"]) {
			nativeText(c, "投标第 "+str(page, "page")+" 页 / "+str(page, "document_id"))
		}
		nativeText(c, str(absence, "required_document_description"))
		nativeText(c, "提交清单哈希 "+str(absence, "submission_manifest_sha256"))
		for _, id := range stringList(absence["inspected_bid_document_ids"]) {
			nativeText(c, "已核对文件 "+id)
		}
		for _, code := range stringList(absence["limitation_codes"]) {
			nativeText(c, code)
		}
	}
	rule := object(row["rule_evidence"])
	if str(rule, "kind") == "pdf_signature_validation" {
		nativeText(c, "本地签名校验文件 "+str(rule, "document_ids"))
		nativeText(c, "本地签名校验记录 "+str(rule, "validation_ids"))
	}
	nativeText(c, "补救建议 "+str(row, "remediation"))
	for _, code := range stringList(row["limitation_codes"]) {
		nativeText(c, code)
	}
	nativeText(c, "当前修订 "+str(row, "revision")+" / 决定 "+str(row, "latest_decision_id"))
	if reviewed := object(row["reviewed_basis"]); reviewed != nil {
		nativeText(c, "人工核对决定 "+str(reviewed, "human_decision_id"))
	}
	current := str(p.run, "validity") == "current" && str(p.meta, "validity") == "current"
	if current {
		actions := []string{"reopen"}
		if str(row, "state") == "open" {
			actions = []string{"classify", "dismiss", "confirm"}
		}
		nativeSelect(c, "发现项操作", &p.action, actions)
		if p.action == "classify" {
			nativeSelect(c, "发现项专业职责", &p.domain, []string{"commercial", "technical"})
		}
		nativeField(c, "发现项审查理由", &p.reason, true)
		nativeButton(c, "保存发现项决定").Disabled(a.Busy || strings.TrimSpace(p.reason) == "").OnClick(func() { p.mutate(a) })
		nativeButton(c, "发现项决定历史").OnClick(func() { p.readHistory(a, "decisions", false) })
		nativeButton(c, "发现项分类历史").OnClick(func() { p.readHistory(a, "classification", false) })
	} else {
		nativeText(c, "输入或授权已变化，当前结果仅供历史查阅，不能作出人工决定。")
	}
	for _, entry := range p.history {
		nativePairs(c, []string{"动作", "职责", "理由", "审查时间", "审查人", "修订"}, []string{nativeValue(entry["action"]), nativeValue(entry["review_domain"]), str(entry, "reason"), str(entry, "decided_at"), str(entry, "decided_by"), str(entry, "revision")})
	}
	if p.historyNext != "" {
		nativeButton(c, "更多发现项历史").OnClick(func() { p.readHistory(a, p.historyPart, true) })
	}
}
