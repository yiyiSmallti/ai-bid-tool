package main

import (
	"net/url"
	"strings"
	"time"

	"github.com/egoist/mygo/ui"
)

type bidRunsPanel struct {
	task, id, date, request, next, obligationNext, signingNext, presenceID string
	preview, selected                                                      Object
	runs, obligations, signing                                             []Object
	rows                                                                   [][]string
	consent, retry, clef, presenceOpen                                     bool
	serial                                                                 uint64
	job                                                                    jobPanel
	presence                                                               bidPresencePanel
	findings                                                               bidFindingsPanel
}

func (p *bidRunsPanel) Dirty() bool { return p.presence.Dirty() || p.findings.Dirty() }
func (p *bidRunsPanel) Close() {
	p.serial++
	p.job.Close()
	p.presence.Close()
	p.findings.Close()
	p.preview = nil
	p.selected = nil
}
func (p *bidRunsPanel) invalidate() { p.preview = nil; p.consent = false; p.request = requestID() }
func (p *bidRunsPanel) authorizationChanged(a *NativeApp) {
	id := str(p.selected, "id")
	p.Close()
	p.invalidate()
	p.runs, p.rows, p.obligations, p.signing = nil, nil, nil, nil
	p.next, p.obligationNext, p.signingNext, p.presenceID = "", "", "", ""
	p.presenceOpen = false
	if id != "" {
		p.show(a, id)
	}
	p.list(a, false)
}
func (p *bidRunsPanel) Load(a *NativeApp, task, id string) {
	p.Close()
	p.task = task
	p.id = id
	p.date = time.Now().Format("2006-01-02")
	p.clef = true
	p.invalidate()
	p.list(a, false)
}
func (p *bidRunsPanel) list(a *NativeApp, more bool) {
	serial := p.serial
	query := url.Values{"limit": {"50"}}
	if more && p.next != "" {
		query.Set("cursor", p.next)
	}
	a.Request("GET", "/tasks/"+p.task+"/bid-reviews?"+query.Encode(), nil, func(r Result) {
		if serial != p.serial {
			return
		}
		if str(r.Data, "task_id") != p.task || len(r.Items) > 50 {
			a.Error = "检验运行列表范围不匹配"
			return
		}
		for _, row := range r.Items {
			if str(row, "task_id") != p.task {
				a.Error = "检验运行任务不匹配"
				return
			}
		}
		if more {
			p.runs = append(p.runs, r.Items...)
		} else {
			p.runs = r.Items
		}
		p.next = str(r.Data, "next_cursor")
		p.rows = nil
		for _, row := range p.runs {
			if str(row, "submission_id") == p.id {
				p.rows = append(p.rows, []string{str(row, "id"), str(row, "status"), str(row, "completion")})
			}
		}
	})
}
func (p *bidRunsPanel) section(a *NativeApp, id, name string, more bool) {
	serial := p.serial
	q := url.Values{"section": {name}, "limit": {"50"}}
	if more {
		cursor := p.obligationNext
		if name == "signing_requirements" {
			cursor = p.signingNext
		}
		if cursor != "" {
			q.Set("cursor", cursor)
		}
	}
	a.Request("GET", "/bid-reviews/"+id+"?"+q.Encode(), nil, func(r Result) {
		if serial != p.serial {
			return
		}
		run := object(r.Data["run"])
		if str(run, "id") != id || str(run, "task_id") != p.task || str(run, "submission_id") != p.id {
			a.Error = "检验结果范围不匹配"
			return
		}
		p.selected = run
		rows := objects(r.Data[name])
		if len(rows) > 50 {
			a.Error = "检验结果超过分页上限"
			return
		}
		if name == "obligations" {
			if more {
				p.obligations = append(p.obligations, rows...)
			} else {
				p.obligations = rows
			}
			p.obligationNext = pageCursor(r.Data["next_cursor"])
		} else {
			if more {
				p.signing = append(p.signing, rows...)
			} else {
				p.signing = rows
			}
			p.signingNext = pageCursor(r.Data["next_cursor"])
		}
		if p.findings.id != id {
			p.findings.Load(a, p.task, run)
		}
	})
}
func (p *bidRunsPanel) show(a *NativeApp, id string) {
	p.serial++
	p.selected = nil
	p.obligations = nil
	p.signing = nil
	p.presenceOpen = false
	p.presence.Close()
	p.findings.Close()
	p.section(a, id, "obligations", false)
	p.section(a, id, "signing_requirements", false)
}
func (p *bidRunsPanel) body(dry bool) Object {
	body := Object{"request_id": p.request, "submission_id": p.id, "assessment_date": p.date, "scope": "uploaded_bid", "review_slice": "compliance", "clef_enabled": p.clef, "presence_authorization_id": nil, "dry_run": dry}
	if p.presenceID != "" {
		body["presence_authorization_id"] = p.presenceID
	}
	if !dry {
		body["retry"] = p.retry
		body["expected_input_hash"] = object(p.preview["input"])["input_hash"]
		body["preflight_token"] = p.preview["preflight_token"]
	}
	return body
}
func (p *bidRunsPanel) View(c *ui.Context, a *NativeApp) {
	nativeHeading(c, "检验运行")
	if p.id == "" {
		nativeText(c, "完成本地准备后可预检检验运行")
		return
	}
	if nativeField(c, "检验基准日期", &p.date, false) {
		p.invalidate()
	}
	if ui.Checkbox(c, &p.clef, "使用 Clef 签章初筛").Changed() {
		p.invalidate()
	}
	if ui.Checkbox(c, &p.retry, "明确重试检验运行").Changed() {
		p.invalidate()
	}
	nativeButton(c, "预检检验运行").Disabled(a.Busy).OnClick(func() {
		p.invalidate()
		a.Request("POST", "/tasks/"+p.task+"/bid-reviews", p.body(true), func(r Result) {
			input := object(r.Data["input"])
			if str(input, "task_id") != p.task || str(input, "submission_id") != p.id || str(input, "input_hash") != str(object(r.Data["preflight"]), "input_hash") {
				a.Error = "检验预检范围不匹配"
				return
			}
			p.preview = r.Data
		})
	})
	if p.preview != nil {
		nativeText(c, "预检未创建作业或调用模型；有效至 "+str(p.preview, "expires_at"))
		input := object(p.preview["input"])
		preflight := object(p.preview["preflight"])
		nativeText(c, "输入 SHA-256 "+str(input, "input_hash"))
		nativeText(c, "计划调用 "+str(preflight, "planned_calls")+" 次 / 预计 tokens "+str(object(preflight["estimate"]), "llm_tokens"))
		clef := object(input["clef"])
		nativeText(c, "Clef启用 "+str(clef, "enabled")+" / 可用 "+str(clef, "available")+" / 计划图片调用 "+str(clef, "planned_calls"))
		for _, code := range stringList(clef["blockers"]) {
			nativeText(c, code)
		}
		for _, key := range []string{"admission_blockers", "uncovered_codes"} {
			for _, code := range stringList(p.preview[key]) {
				nativeText(c, code)
			}
		}
		ui.Checkbox(c, &p.consent, "确认已授权文本与配置，提交检验运行")
		nativePrimaryButton(c, "提交检验运行").Disabled(a.Busy || !p.consent || expiredPreview(p.preview) || len(stringList(p.preview["admission_blockers"])) > 0).OnClick(func() {
			body := p.body(false)
			p.invalidate()
			a.Request("POST", "/tasks/"+p.task+"/bid-reviews", body, func(r Result) {
				p.job.Load(a, str(r.Data, "job_id"), func() {
					p.list(a, false)
					if id := str(object(p.job.data["result"]), "review_id"); id != "" {
						p.show(a, id)
					}
				})
				p.list(a, false)
			})
		})
	}
	p.job.View(c, a)
	nativeTable(c, []string{"运行", "状态", "完成情况"}, p.rows, func(i int) { p.show(a, p.rows[i][0]) })
	if p.next != "" {
		nativeButton(c, "更多检验运行").OnClick(func() { p.list(a, true) })
	}
	if p.selected == nil {
		return
	}
	run := p.selected
	nativeText(c, "完成情况 "+nativeValue(run["completion"])+" / 有效状态 "+nativeValue(run["validity"]))
	for _, code := range stringList(run["uncovered_codes"]) {
		nativeText(c, "未覆盖："+code)
	}
	coverage := object(run["coverage"])
	nativePairs(c, []string{"招标总页数", "招标已评估页数", "招标已授权页数", "Clef覆盖", "已完成初筛调用", "尝试初筛调用", "待核对调用"}, []string{str(coverage, "tender_pages_total"), str(coverage, "tender_pages_assessed"), str(coverage, "tender_pages_authorized"), str(coverage, "clef"), str(coverage, "clef_completed_calls"), str(coverage, "clef_calls"), str(coverage, "clef_unresolved_calls")})
	if str(run, "status") == "succeeded" {
		nativeButton(c, "检验报告与 Word").OnClick(func() { a.Navigate("/org/tasks/" + p.task + "/bid-reviews/" + str(run, "id") + "/report") })
		nativeButton(c, "签章图片外发授权").OnClick(func() {
			p.presenceOpen = !p.presenceOpen
			if p.presenceOpen {
				p.presence.Load(a, p.id, str(run, "id"), func(id string) { p.presenceID = id; p.invalidate() })
			} else {
				p.presence.Close()
			}
		})
	}
	if p.presenceOpen {
		p.presence.View(c, a)
	}
	p.findings.View(c, a)
	nativeHeading(c, "招标义务与引用")
	for _, row := range p.obligations {
		nativeText(c, str(row, "text"))
		nativeText(c, "★条款 "+str(row, "starred")+" / 废标条件 "+str(row, "rejection_trigger"))
		bidCitationText(c, object(row["citation"]))
	}
	if p.obligationNext != "" {
		nativeButton(c, "更多招标义务").OnClick(func() { p.section(a, str(run, "id"), "obligations", true) })
	}
	nativeHeading(c, "签章检查清单与未解决位置")
	for _, row := range p.signing {
		nativeText(c, "适用性 "+nativeStatus(row["applicability"])+" / 类型 "+nativeMarkList(row["mark_types"])+" / 需日期 "+nativeValue(row["date_required"]))
		bidCitationText(c, object(row["citation"]))
		locations := objects(row["required_locations"])
		for _, location := range locations {
			bidPresenceLocation(c, location)
		}
		if len(locations) == 0 {
			if str(row, "applicability") == "not_applicable" {
				nativeText(c, "无适用签章位置")
			} else {
				nativeText(c, "目标位置未确定，不能判为存在")
			}
		}
	}
	if p.signingNext != "" {
		nativeButton(c, "更多签章要求").OnClick(func() { p.section(a, str(run, "id"), "signing_requirements", true) })
	}
}
func bidCitationText(c *ui.Context, citation Object) {
	if citation == nil {
		return
	}
	position := "原件第"
	if str(citation, "page_label") == "rendered_docx" {
		position = "固定转换第"
	}
	if integer(citation, "page") > 0 {
		nativeText(c, position+" "+str(citation, "page")+" 页 / 文档 "+str(citation, "document_id"))
	} else {
		nativeText(c, "页码未确定 / 文档 "+str(citation, "document_id"))
	}
	nativeText(c, str(citation, "quote"))
	location := object(citation["location"])
	if location != nil {
		nativeText(c, strings.Join(stringList(location["section_path"]), " / ")+" / "+str(location, "label")+" / 结构块 "+str(location, "block_id"))
	}
}
