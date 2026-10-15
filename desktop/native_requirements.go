package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"strings"
	"time"

	"github.com/egoist/mygo/ui"
)

func requestID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	b[6] = (b[6] & 15) | 64
	b[8] = (b[8] & 63) | 128
	s := hex.EncodeToString(b[:])
	return s[:8] + "-" + s[8:12] + "-" + s[12:16] + "-" + s[16:20] + "-" + s[20:]
}

var requirementCategories = []string{"", "qualification", "technical", "scoring", "substantive"}

type requirementsPage struct {
	id, job, selected, state, category, origin, cursor                        string
	starred                                                                   bool
	history, documents, items, events, rejections                             []Object
	historyLabels                                                             map[string]string
	rows                                                                      [][]string
	scope, data, detail, repair                                               Object
	original, reason, batchReason, historyCursor, rejectedJob, rejectedCursor string
	rejectedFilterJob, rejectedFilterIndex, limit                             string
	reviewed, stale, reviewedEach                                             bool
	selectedBatch                                                             map[string]Object
	inspected                                                                 map[string]Object
	prior                                                                     []string
	manual                                                                    manualRequirement
	document                                                                  documentPanel
}

func newRequirementsPage(r Route) NativePage {
	limit := r.Query.Get("limit")
	if limit != "100" {
		limit = "50"
	}
	return &requirementsPage{id: r.Params["taskId"], job: r.Query.Get("job"), selected: r.Query.Get("requirement"), state: r.Query.Get("state"), category: r.Query.Get("category"), origin: r.Query.Get("origin"), starred: r.Query.Get("starred") == "1", rejectedFilterJob: r.Query.Get("rejected_job_id"), rejectedFilterIndex: r.Query.Get("rejected_index"), limit: limit, selectedBatch: map[string]Object{}, inspected: map[string]Object{}}
}
func (*requirementsPage) Title() string { return "要求确认与补录" }
func (p *requirementsPage) Dirty() bool {
	return p.reason != "" || p.batchReason != "" || p.manual.Dirty()
}
func (p *requirementsPage) Close() {
	p.reason = ""
	p.batchReason = ""
	p.original = ""
	p.manual = manualRequirement{}
	p.document.Close()
}
func (p *requirementsPage) Open(a *NativeApp) {
	a.Request("GET", "/tasks/"+p.id+"/extractions", nil, func(r Result) { p.history = r.Items; p.labelHistory() })
	a.Request("GET", "/tasks/"+p.id+"/documents", nil, func(r Result) { p.documents = r.Items; p.labelHistory() })
	if p.job != "" {
		p.load(a)
	}
	if p.selected != "" {
		p.openDetail(a, p.selected)
	}
}
func (p *requirementsPage) labelHistory() {
	p.historyLabels = map[string]string{"": "选择要求集合"}
	for _, row := range p.history {
		name := "要求集合"
		for _, document := range p.documents {
			if str(document, "id") == str(row, "document_id") {
				name = str(document, "name")
				break
			}
		}
		created := str(row, "created_at")
		if parsed, err := time.Parse(time.RFC3339Nano, created); err == nil {
			created = parsed.Local().Format("2006-01-02 15:04")
		}
		label := name
		if created != "" {
			label += " · " + created
		}
		if row["current_saved"] != nil {
			label += " · 已抽取 " + str(row, "current_saved") + " 条"
		}
		if row["manual_added"] != nil {
			label += " · 补录 " + str(row, "manual_added") + " 条"
		}
		p.historyLabels[str(row, "job_id")] = label
	}
}
func (p *requirementsPage) load(a *NativeApp) {
	if p.job == "" {
		return
	}
	limit := 50
	if p.limit == "100" {
		limit = 100
	}
	q := url.Values{"limit": {fmt.Sprint(limit)}}
	for key, value := range map[string]string{"state": p.state, "category": p.category, "origin": p.origin, "cursor": p.cursor, "rejected_job_id": p.rejectedFilterJob, "rejected_index": p.rejectedFilterIndex} {
		if value != "" {
			q.Set(key, value)
		}
	}
	if p.starred {
		q.Set("starred", "true")
	}
	a.Request("GET", "/tasks/"+p.id+"/extractions/"+p.job+"/requirement-reviews?"+q.Encode(), nil, func(r Result) {
		scope := object(r.Data["scope"])
		if str(scope, "task_id") != p.id || str(scope, "extraction_job_id") != p.job || len(r.Items) > limit {
			a.Error = "要求集合范围不符合契约"
			return
		}
		if p.scope != nil && (p.scope["revision"] != scope["revision"] || p.scope["confirmation_sha256"] != scope["confirmation_sha256"]) {
			p.reviewedEach = false
			p.inspected = map[string]Object{}
			p.selectedBatch = map[string]Object{}
			p.reviewed = false
			p.stale = true
			p.manual.invalidate()
		}
		p.scope = scope
		p.data = r.Data
		p.items = r.Items
		p.rows = nil
		for _, row := range p.items {
			content := object(row["content"])
			p.rows = append(p.rows, []string{str(content, "text"), nativeLabel(str(content, "category")), nativeValue(content["starred"]), nativeLabel(str(row, "state")), nativeLabel(str(row, "origin"))})
		}
	})
}
func (p *requirementsPage) openDetail(a *NativeApp, id string) {
	if p.reason != "" && id != p.selected {
		a.Confirm("切换要求", "尚有未保存审阅原因，是否丢弃？", func() { p.reason = ""; p.openDetail(a, id) })
		return
	}
	p.selected = id
	p.reviewed = false
	p.original = ""
	p.detail = nil
	p.events = nil
	p.document.Close()
	a.Do(func(ctx context.Context) (func(), error) {
		r, err := a.Client.Request(ctx, "GET", "/requirements/"+id+"/review", nil)
		if err != nil {
			return nil, err
		}
		scope, row := object(r.Data["scope"]), object(r.Data["requirement"])
		if str(scope, "task_id") != p.id || str(scope, "extraction_job_id") != p.job || str(row, "requirement_id") != id {
			return nil, errors.New("要求对象不符合契约")
		}
		source := object(object(row["content"])["source"])
		chunks, err := a.Client.Request(ctx, "GET", "/documents/"+str(source, "document_id")+"/chunks", nil)
		if err != nil {
			return nil, err
		}
		original := ""
		for _, chunk := range chunks.Items {
			if str(chunk, "id") != str(source, "chunk_id") || str(chunk, "document_id") != str(source, "document_id") {
				continue
			}
			if source["page"] != nil {
				original = str(chunk, "text")
			} else {
				for _, block := range objects(chunk["blocks"]) {
					if str(block, "block_id") == str(object(source["location"]), "block_id") {
						original = str(block, "text")
					}
				}
			}
		}
		history, err := a.Client.Request(ctx, "GET", "/requirements/"+id+"/review-history?limit=50", nil)
		return func() {
			if p.selected != id {
				return
			}
			p.detail = row
			p.original = original
			p.events = history.Items
			p.historyCursor = str(history.Data, "next_cursor")
			p.stale = false
			if original != "" && row["citation_valid"] == true {
				p.inspected[id] = Object{"requirement_id": id, "expected_revision": row["revision"], "expected_review_hash": row["review_hash"]}
			}
		}, err
	})
}
func (p *requirementsPage) decide(a *NativeApp, action string) {
	row := p.detail
	p.stale = true
	p.reviewed = false
	a.Request("POST", "/requirements/"+p.selected+"/review-decisions", Object{"request_id": requestID(), "action": action, "expected_revision": row["revision"], "expected_review_hash": row["review_hash"], "reason": p.reason}, func(Result) { p.reason = ""; p.load(a); p.openDetail(a, p.selected) })
}
func (p *requirementsPage) View(c *ui.Context, a *NativeApp) {

	if p.detail == nil {
		ui.Column(c.Key("requirement-list")).FillWidth().Gap(14).Children(func() {
			nativeText(c, "先核对要求，再分别审阅响应与证据。已确认不代表无遗漏。")
			options := []string{""}
			for _, h := range p.history {
				if str(h, "status") == "succeeded" {
					options = append(options, str(h, "job_id"))
				}
			}
			job := p.job
			if nativeChoice(c, &job, options, p.historyLabels).Label("明确选择要求集合").Changed() {
				a.Navigate("/org/tasks/" + p.id + "/requirements?job=" + job)
			}
			rejectedOptions := []string{""}
			for _, h := range p.history {
				if str(h, "origin") != "manual" {
					rejectedOptions = append(rejectedOptions, str(h, "job_id"))
				}
			}
			nativeButton(c, "补录遗漏要求").OnClick(func() {
				p.manual.opened = true
				p.manual.category = "technical"
				p.manual.target = "current"
				if p.scope == nil {
					p.manual.target = "new"
				}
			})
			nativeDisclosure(c, "拒绝回执与遗漏补录", false, func() {
				nativeSelect(c, "原抽取作业", &p.rejectedJob, rejectedOptions)
				nativeButton(c, "读取拒绝摘要").Disabled(p.rejectedJob == "" || a.Busy).OnClick(func() { p.loadRejected(a, false) })
				for _, rejected := range p.rejections {
					nativeText(c, str(rejected, "position")+" · "+str(rejected, "reason"))
					nativeText(c, str(rejected, "quote"))
					nativeButton(c.Key(str(rejected, "index")), "从真实原文补录").OnClick(func() {
						p.manual = manualRequirement{opened: true, target: "current", category: "technical", rejected: rejected}
					})
					nativeButton(c.Key("filter-"+str(rejected, "index")), "筛选该回执已补录要求").Disabled(p.job == "" || a.Busy).OnClick(func() {
						q := url.Values{"job": {p.job}, "state": {p.state}, "category": {p.category}, "origin": {p.origin}, "limit": {p.limit}, "rejected_job_id": {str(rejected, "job_id")}, "rejected_index": {str(rejected, "index")}}
						if p.starred {
							q.Set("starred", "1")
						}
						a.Navigate("/org/tasks/" + p.id + "/requirements?" + q.Encode())
					})
					for _, linked := range stringList(rejected["entered_requirement_ids"]) {
						nativeButton(c.Key(linked), "查看已补录要求").OnClick(func() {
							a.Request("GET", "/requirements/"+linked+"/review", nil, func(r Result) {
								scope := object(r.Data["scope"])
								if str(scope, "task_id") != p.id {
									a.Error = "补录要求范围不匹配"
									return
								}
								a.Navigate("/org/tasks/" + p.id + "/requirements?job=" + str(scope, "extraction_job_id") + "&requirement=" + linked)
							})
						})
					}
				}
				if p.rejectedCursor != "" {
					nativeButton(c, "继续读取拒绝摘要").OnClick(func() { p.loadRejected(a, true) })
				}
			})
			p.manual.View(c, a, p)
			if p.scope == nil {
				return
			}
			nativeText(c, "集合修订 "+str(p.scope, "revision"))
			ui.Row(c).FillWidth().Wrap().Gap(12).Children(func() {
				for _, field := range []struct {
					label   string
					value   *string
					options []string
				}{
					{"要求状态", &p.state, []string{"", "unconfirmed", "legacy_unconfirmed", "confirmed", "invalidated"}},
					{"要求类别", &p.category, requirementCategories},
					{"来源", &p.origin, []string{"", "model", "manual"}},
					{"每页条数", &p.limit, []string{"50", "100"}},
				} {
					ui.Column(c.Key(field.label)).Width(220).Children(func() { nativeSelect(c, field.label, field.value, field.options) })
				}
			})
			ui.Checkbox(c, &p.starred, "只看 ★ 条款")
			if p.rejectedFilterJob != "" {
				nativeText(c, "按拒绝回执关联筛选："+p.rejectedFilterJob+" / "+p.rejectedFilterIndex)
				nativeButton(c, "清除拒绝回执筛选").Disabled(a.Busy).OnClick(func() { p.rejectedFilterJob = ""; p.rejectedFilterIndex = ""; p.cursor = ""; p.prior = nil; p.load(a) })
			}
			nativeButton(c, "应用要求筛选").OnClick(func() { p.cursor = ""; p.prior = nil; p.load(a) })
			nativeTable(c, []string{"要求正文", "类别", "星标", "要求状态", "来源"}, p.rows, func(i int) { p.openDetail(a, str(p.items[i], "requirement_id")) })
			nativePager(c, len(p.prior)+1, str(p.data, "next_cursor") != "", false, "上一页", "下一页",
				func() { p.cursor = p.prior[len(p.prior)-1]; p.prior = p.prior[:len(p.prior)-1]; p.load(a) },
				func() { p.prior = append(p.prior, p.cursor); p.cursor = str(p.data, "next_cursor"); p.load(a) })
		})
	}
	if p.scope == nil {
		return
	}
	if p.detail != nil {
		nativeButton(c, "返回要求列表").Disabled(a.Busy).OnClick(func() {
			leave := func() { p.detail = nil; p.selected = ""; p.reason = ""; p.reviewed = false; p.document.Close() }
			if p.reason != "" {
				a.Confirm("返回要求列表", "尚有未提交的审阅原因，返回将丢弃此原因。", leave)
			} else {
				leave()
			}
		})
		nativeHint(c, "集合修订 "+str(p.scope, "revision"))
		row := p.detail
		content := object(row["content"])
		source := object(content["source"])
		ui.Row(c).FillWidth().Wrap().AlignItems(ui.Start).Gap(24).Children(func() {
			ui.Column(c.Key("requirement-source")).Width(540).MaxWidthPercent(100).Gap(12).Children(func() {
				nativeHeading(c, "要求正文")
				nativeText(c, str(content, "text"))
				nativePairs(c, []string{"状态", "类别", "星标", "修订"}, []string{nativeLabel(str(row, "state")), nativeLabel(str(content, "category")), nativeValue(content["starred"]), str(row, "revision")})
				nativeHeading(c, "来源原文")
				nativeText(c, p.original)
				nativeButton(c, "核对要求原件").OnClick(func() {
					p.document.Open(a, str(source, "document_id"), integer(source, "page"), str(object(source["location"]), "block_id"))
				})
				nativeButton(c, "重新读取要求修订").OnClick(func() { p.openDetail(a, p.selected) })
			})
			ui.Column(c.Key("requirement-decision")).Width(520).MaxWidthPercent(100).Gap(14).Children(func() {
				nativeHeading(c, "人工核对与确认")
				nativeField(c, "要求审阅原因", &p.reason, true)
				ui.Checkbox(c, &p.reviewed, "已核对正文、类别、星标、结构条件和逐字原文").Disabled(p.stale || p.original == "" || row["citation_valid"] != true)
				if str(row, "state") == "confirmed" {
					nativeButton(c, "重新打开要求").Disabled(a.Busy || p.stale || strings.TrimSpace(p.reason) == "").OnClick(func() { p.decide(a, "reopen") })
				} else {
					nativePrimaryButton(c, "确认要求").Disabled(a.Busy || p.stale || !p.reviewed || p.original == "" || row["citation_valid"] != true || strings.TrimSpace(p.reason) == "").OnClick(func() { p.decide(a, "confirm") })
					_, selected := p.selectedBatch[p.selected]
					if ui.Checkbox(c, &selected, "加入本次明确批量确认").Disabled(p.inspected[p.selected] == nil || len(p.selectedBatch) >= 100 && !selected).Changed() {
						p.reviewedEach = false
						if selected {
							p.selectedBatch[p.selected] = p.inspected[p.selected]
						} else {
							delete(p.selectedBatch, p.selected)
						}
					}
				}
				nativeButton(c, "进入响应与证据审阅").OnClick(func() { a.Navigate("/org/tasks/" + p.id + "/review?job=" + p.job + "&requirement=" + p.selected) })
			})
		})
		nativeHeading(c, "要求条件")
		nativeText(c, conditionText(content))
		pin := object(row["source_pin"])
		nativeText(c, "精确跨度 "+str(pin, "start")+" – "+str(pin, "end")+" / 校验器 "+str(pin, "verifier_version"))
		if str(row, "model_quote") != "" {
			nativeText(c, "原模型引文（仅供追溯）："+str(row, "model_quote"))
		}
		if row["citation_valid"] != true {
			nativeButton(c, "预检修复集合引用").OnClick(func() {
				a.Request("GET", "/tasks/"+p.id+"/requirements/repair?job="+p.job, nil, func(r Result) { p.repair = r.Data })
			})
		}
		if p.repair != nil {
			nativeText(c, "引用修复预检 "+str(p.repair, "preview_hash"))
			nativeButton(c, "按预检修复引用").Disabled(a.Busy || p.reason == "").OnClick(func() {
				a.Confirm("修复引用", "受影响的要求确认将失效，继续？", func() {
					preview := p.repair
					p.repair = nil
					a.Request("POST", "/tasks/"+p.id+"/requirements/repair", Object{"extraction_job_id": p.job, "expected_preview": preview["preview_hash"], "reason": p.reason}, func(Result) { p.load(a); p.openDetail(a, p.selected) })
				})
			})
		}
		nativeHeading(c, "审阅记录")
		if len(p.events) == 0 {
			nativeHint(c, "尚无审阅记录。")
		}
		for _, event := range p.events {
			ui.Column(c.Key(str(event, "id"))).Gap(8).Children(func() {
				content := object(event["content"])
				nativePairs(c, []string{"操作", "修订", "操作者类型", "时间", "决定后状态", "类别", "星标", "决定原因"}, []string{nativeLabel(str(event, "action")), str(event, "revision"), nativeLabel(str(event, "actor_kind")), str(event, "occurred_at"), nativeLabel(str(event, "state_after")), nativeLabel(str(content, "category")), nativeValue(content["starred"]), str(event, "reason")})
				nativeText(c, str(content, "text"))
				bidCitationText(c, object(content["source"]))
				nativeText(c, "原文分块 "+str(object(content["source"]), "chunk_id"))
				nativeText(c, conditionText(content))
			})
		}
		if p.historyCursor != "" {
			nativeButton(c, "更多审阅记录").OnClick(func() {
				a.Request("GET", "/requirements/"+p.selected+"/review-history?limit=50&cursor="+url.QueryEscape(p.historyCursor), nil, func(r Result) { p.events = append(p.events, r.Items...); p.historyCursor = str(r.Data, "next_cursor") })
			})
		}
	}
	p.document.View(c, a)
	nativeHeading(c, "批量确认要求")
	nativeText(c, fmt.Sprintf("明确选择 %d / 100 项；先逐项打开完整原文，无默认全选。", len(p.selectedBatch)))
	nativeField(c, "批次确认原因", &p.batchReason, true)
	ui.Checkbox(c, &p.reviewedEach, "已逐项核对全部明确选择的要求与来源").Disabled(len(p.selectedBatch) == 0 || p.stale)
	nativePrimaryButton(c, "确认所选要求").Disabled(a.Busy || p.stale || !p.reviewedEach || p.batchReason == "" || len(p.selectedBatch) == 0).OnClick(func() {
		items := make([]Object, 0, len(p.selectedBatch))
		for _, item := range p.selectedBatch {
			items = append(items, item)
		}
		p.reviewedEach = false
		p.stale = true
		a.Request("POST", "/tasks/"+p.id+"/extractions/"+p.job+"/requirement-confirmations", Object{"request_id": requestID(), "expected_set_revision": p.scope["revision"], "items": items, "reviewed_each": true, "reason": p.batchReason}, func(Result) {
			p.batchReason = ""
			p.selectedBatch = map[string]Object{}
			p.inspected = map[string]Object{}
			p.stale = false
			p.load(a)
			if p.selected != "" {
				p.openDetail(a, p.selected)
			}
		})
	})
}
func (p *requirementsPage) loadRejected(a *NativeApp, more bool) {
	q := "?limit=50"
	if more {
		q += "&cursor=" + url.QueryEscape(p.rejectedCursor)
	}
	a.Request("GET", "/tasks/"+p.id+"/extractions/"+p.rejectedJob+"/rejected-items"+q, nil, func(r Result) {
		if more {
			p.rejections = append(p.rejections, r.Items...)
		} else {
			p.rejections = r.Items
		}
		p.rejectedCursor = str(r.Data, "next_cursor")
	})
}

type manualRequirement struct {
	opened, starred                                                      bool
	document, position, target, category, text, quote, reason, condition string
	verificationRequest                                                  string
	sources                                                              []Object
	preview, rejected                                                    Object
	verifiedBody                                                         Object
}

func (m *manualRequirement) Dirty() bool {
	return m.text != "" || m.quote != "" || m.reason != "" || strings.TrimSpace(m.condition) != "" && strings.TrimSpace(m.condition) != "{}"
}
func (m *manualRequirement) invalidate() {
	m.preview = nil
	m.verifiedBody = nil
	m.verificationRequest = ""
}
func (m *manualRequirement) body(p *requirementsPage) (Object, error) {
	var source Object
	for _, s := range m.sources {
		if str(s, "key") == m.position {
			source = object(s["source"])
		}
	}
	if source == nil || strings.TrimSpace(m.quote) == "" || strings.TrimSpace(m.text) == "" || strings.TrimSpace(m.reason) == "" {
		return nil, errors.New("请选择真实原文位置，并填写逐字引文、要求和原因")
	}
	copySource := Object{}
	for k, v := range source {
		copySource[k] = v
	}
	copySource["quote"] = m.quote
	var job, revision any
	if m.target == "current" {
		if p.scope == nil || str(p.scope, "document_id") != m.document {
			return nil, errors.New("追加集合必须使用该集合的招标文件")
		}
		job = p.job
		revision = p.scope["revision"]
	}
	condition := Object{}
	if strings.TrimSpace(m.condition) != "" {
		decoder := json.NewDecoder(strings.NewReader(m.condition))
		decoder.UseNumber()
		if err := decoder.Decode(&condition); err != nil || condition == nil {
			return nil, errors.New("结构条件必须是 JSON 对象")
		}
		var extra any
		if err := decoder.Decode(&extra); err != io.EOF {
			return nil, errors.New("结构条件必须是单个 JSON 对象")
		}
	}
	var rejected any
	if m.rejected != nil {
		rejected = Object{"job_id": m.rejected["job_id"], "index": m.rejected["index"], "summary_sha256": m.rejected["summary_sha256"]}
	}
	return Object{"extraction_job_id": job, "expected_set_revision": revision, "content": Object{"category": m.category, "starred": m.starred, "text": m.text, "condition": condition, "source": copySource}, "rejected_item": rejected, "reason": m.reason}, nil
}
func (m *manualRequirement) View(c *ui.Context, a *NativeApp, p *requirementsPage) {
	if !m.opened {
		return
	}
	nativeHeading(c, "从真实原文补录遗漏要求")
	nativeButton(c, "关闭补录").OnClick(func() {
		if m.Dirty() {
			a.Confirm("关闭补录", "尚有未保存输入，是否丢弃？", func() { *m = manualRequirement{} })
		} else {
			*m = manualRequirement{}
		}
	})
	if nativeChoice(c, &m.target, []string{"current", "new"}).Label("追加当前集合 / 新建人工补录集").Changed() {
		m.invalidate()
	}
	docs := []string{""}
	for _, doc := range p.documents {
		if str(doc, "status") == "parsed" {
			docs = append(docs, str(doc, "id"))
		}
	}
	if nativeChoice(c, &m.document, docs).Label("已解析的招标文件").Changed() {
		m.invalidate()
		m.position = ""
		m.sources = nil
		doc := m.document
		a.Request("GET", "/documents/"+doc+"/chunks", nil, func(r Result) {
			if m.document != doc {
				return
			}
			for _, chunk := range r.Items {
				if chunk["citation_verified"] == false {
					continue
				}
				blocks := objects(chunk["blocks"])
				if len(blocks) > 0 {
					for _, block := range blocks {
						location := Object{}
						for key, value := range block {
							if key != "text" {
								location[key] = value
							}
						}
						m.sources = append(m.sources, Object{"key": str(chunk, "id") + ":" + str(block, "block_id"), "text": block["text"], "source": Object{"document_id": doc, "chunk_id": chunk["id"], "page": nil, "location": location}})
					}
				} else {
					m.sources = append(m.sources, Object{"key": str(chunk, "id"), "text": chunk["text"], "source": Object{"document_id": doc, "chunk_id": chunk["id"], "page": chunk["page"], "location": nil}})
				}
			}
		})
	}
	positions := []string{""}
	for _, s := range m.sources {
		positions = append(positions, str(s, "key"))
	}
	if nativeChoice(c, &m.position, positions).Label("原文页或完整 Word 位置").Changed() {
		m.invalidate()
	}
	for _, s := range m.sources {
		if str(s, "key") == m.position {
			nativeText(c, str(s, "text"))
		}
	}
	changed := false
	for _, field := range []struct {
		label string
		value *string
	}{{"逐字引文", &m.quote}, {"要求正文", &m.text}, {"补录原因", &m.reason}} {
		ui.Text(c, field.label)
		if ui.TextArea(c, field.value).Label(field.label).Height(80).Changed() {
			changed = true
		}
	}
	if nativeChoice(c, &m.category, requirementCategories[1:]).Label("类别").Changed() {
		changed = true
	}
	if ui.Checkbox(c, &m.starred, "明确标记 ★ 条款").Changed() {
		changed = true
	}
	ui.Text(c, "结构条件（JSON 对象）")
	if ui.TextArea(c, &m.condition).Label("结构条件（JSON 对象）").Height(120).Placeholder("{}").Changed() {
		changed = true
	}
	if changed {
		m.invalidate()
	}
	nativeButton(c, "核验补录来源").Disabled(a.Busy).OnClick(func() {
		body, err := m.body(p)
		if err != nil {
			a.Error = err.Error()
			return
		}
		m.invalidate()
		request := requestID()
		m.verificationRequest = request
		a.Request("POST", "/tasks/"+p.id+"/requirements/manual-preview", body, func(r Result) {
			if m.verificationRequest != request {
				return
			}
			m.preview = r.Data
			m.verifiedBody = body
		})
	})
	if m.preview != nil {
		nativeText(c, "原文已核验；重复要求："+str(m.preview, "duplicate_requirement_id"))
		nativePrimaryButton(c, "保存待确认要求").Disabled(a.Busy || str(m.preview, "duplicate_requirement_id") != "").OnClick(func() {
			body := m.verifiedBody
			body["expected_preview_hash"] = m.preview["preview_hash"]
			body["request_id"] = requestID()
			m.preview = nil
			a.Request("POST", "/tasks/"+p.id+"/requirements/manual", body, func(r Result) {
				*m = manualRequirement{}
				p.reason = ""
				p.batchReason = ""
				a.Navigate("/org/tasks/" + p.id + "/requirements?job=" + str(object(r.Data["scope"]), "extraction_job_id") + "&requirement=" + str(object(r.Data["requirement"]), "requirement_id"))
			})
		})
	}
	nativeText(c, "保存后仍待人工确认，不会确认响应、证据或无遗漏。")
}

func conditionText(content Object) string {
	if cached, ok := content["native_condition_text"].(string); ok {
		return cached
	}
	if content["condition"] == nil {
		return "未提供结构化条件"
	}
	encoded, err := json.MarshalIndent(content["condition"], "", "  ")
	if err != nil {
		return "结构条件无法读取"
	}
	text := string(encoded)
	if content != nil {
		content["native_condition_text"] = text
	}
	return text
}
