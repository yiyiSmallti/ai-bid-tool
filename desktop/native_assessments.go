package main

import (
	"context"
	"errors"
	"net/url"
	"strings"
	"time"

	"github.com/egoist/mygo/ui"
)

type assessmentPage struct {
	task, extraction, kind, draft, rubric, mode, date, reasoning string
	inputs, preview, rubricDetail                                Object
	history, rubrics, jobs                                       []Object
	historyRows, jobRows                                         [][]string
	cursor, next                                                 string
	prior                                                        []string
	consent, retry                                               bool
	serial                                                       uint64
	queueBody                                                    Object
	job                                                          jobPanel
	jobCursor, jobNext, rubricCursor, rubricNext                 string
	jobPrior, rubricPrior                                        []string
	jobSerial, rubricSerial                                      uint64
	sourceNeedsRefresh                                           bool
	unassessableDetail                                           Object
	unassessableID                                               string
	unassessablePage                                             int
	reads                                                        managementReads
}

func newAssessmentPage(r Route) NativePage {
	kind := "checks"
	if strings.HasSuffix(r.Path, "/scores") {
		kind = "scores"
	}
	if strings.HasSuffix(r.Path, "/score-rubrics") {
		kind = "score-rubrics"
	}
	return &assessmentPage{task: r.Params["taskId"], extraction: r.Query.Get("job"), kind: kind, draft: r.Query.Get("draft"), rubric: r.Query.Get("rubric"), mode: "rules", date: time.Now().Format("2006-01-02")}
}
func (p *assessmentPage) Title() string {
	switch p.kind {
	case "scores":
		return "评分预估"
	case "score-rubrics":
		return "评分规则"
	}
	return "检查风险"
}
func (*assessmentPage) Dirty() bool    { return false }
func (p *assessmentPage) Close()       { p.serial++; p.job.Close(); p.reads.close() }
func (p *assessmentPage) base() string { return "/tasks/" + p.task + "/" + p.kind }
func (p *assessmentPage) invalidate() {
	p.serial++
	p.preview = nil
	p.consent = false
	p.queueBody = nil
	p.unassessableDetail = nil
	p.unassessableID = ""
	p.unassessablePage = 0
	p.reads.close()
}
func (p *assessmentPage) Open(a *NativeApp) {
	if p.extraction == "" {
		a.Error = "请明确选择成功的抽取集合"
		return
	}
	a.Request("GET", "/tasks/"+p.task+"/assessment-inputs?job="+p.extraction, nil, func(r Result) {
		if str(r.Data, "task_id") != p.task || str(r.Data, "extraction_job_id") != p.extraction {
			a.Error = "评估输入范围不匹配"
			return
		}
		p.inputs = r.Data
		if p.draft == "" {
			p.draft = str(object(r.Data["current_draft"]), "draft_id")
		}
		if p.draft != "" && p.selectedDraft() == nil {
			id := p.draft
			a.Request("GET", "/drafts/"+url.PathEscape(id), nil, func(saved Result) {
				if p.draft != id {
					return
				}
				if str(saved.Data, "id") != id || str(saved.Data, "task_id") != p.task || str(saved.Data, "extraction_job_id") != p.extraction {
					a.Error = "指定初稿不属于当前任务和抽取集合"
					return
				}
				p.inputs["selected_draft"] = Object{"draft_id": id, "validity": saved.Data["validity"], "completion": saved.Data["completion"]}
			})
		}
		p.load(a)
		if p.kind == "scores" {
			p.loadRubrics(a)
			if p.rubric != "" {
				p.readRubric(a)
			}
		}
	})
}
func (p *assessmentPage) load(a *NativeApp) {
	q := url.Values{"view": {"console"}, "extraction_job_id": {p.extraction}, "limit": {"50"}}
	if p.cursor != "" {
		q.Set("cursor", p.cursor)
	}
	a.Request("GET", p.base()+"?"+q.Encode(), nil, func(r Result) {
		p.history = r.Items
		p.next = str(r.Data, "next_cursor")
		p.historyRows = nil
		for _, item := range r.Items {
			report := object(item["report"])
			if report == nil {
				report = item
			}
			p.historyRows = append(p.historyRows, []string{str(report, "id"), str(report, "completion"), str(report, "validity"), str(item, "state"), str(item, "total_status"), str(report, "created_at")})
		}
	})
	p.loadJobs(a)
}
func (p *assessmentPage) loadJobs(a *NativeApp) {
	p.jobSerial++
	serial := p.jobSerial
	kind := map[string]string{"checks": "check", "scores": "score", "score-rubrics": "score_rubric"}[p.kind]
	q := url.Values{"kind": {kind}, "extraction_job_id": {p.extraction}, "limit": {"50"}}
	if p.jobCursor != "" {
		q.Set("cursor", p.jobCursor)
	}
	a.Request("GET", "/tasks/"+p.task+"/jobs?"+q.Encode(), nil, func(r Result) {
		if serial != p.jobSerial {
			return
		}
		if len(r.Items) > 50 {
			a.Error = "作业分页超过上限"
			return
		}
		p.jobNext = str(r.Data, "next_cursor")
		p.jobs = r.Items
		p.jobRows = tableRows(r.Items, "id", "status", "created_at")
		if p.job.id == "" {
			for _, job := range r.Items {
				if containsString([]string{"queued", "running"}, str(job, "status")) {
					p.job.Load(a, str(job, "id"), func() { p.load(a) })
					break
				}
			}
		}
	})
}
func (p *assessmentPage) loadRubrics(a *NativeApp) {
	p.rubricSerial++
	serial := p.rubricSerial
	q := url.Values{"view": {"console"}, "extraction_job_id": {p.extraction}, "limit": {"50"}}
	if p.rubricCursor != "" {
		q.Set("cursor", p.rubricCursor)
	}
	a.Request("GET", "/tasks/"+p.task+"/score-rubrics?"+q.Encode(), nil, func(r Result) {
		if serial != p.rubricSerial {
			return
		}
		if len(r.Items) > 50 {
			a.Error = "评分规则分页超过上限"
			return
		}
		p.rubrics, p.rubricNext = r.Items, str(r.Data, "next_cursor")
	})
}
func (p *assessmentPage) selectedDraft() Object {
	for _, key := range []string{"current_draft", "latest_draft", "selected_draft"} {
		row := object(p.inputs[key])
		if str(row, "draft_id") == p.draft && p.draft != "" {
			return row
		}
	}
	return nil
}
func (p *assessmentPage) sourceBlocker() string {
	if p.sourceNeedsRefresh {
		return "要求确认已变化，请先重新核对要求，再刷新评分规则来源"
	}
	switch str(object(p.rubricDetail["requirement_review"]), "state") {
	case "ready":
		return ""
	case "preparation":
		return "评分来源要求尚未全部确认，请先核对要求，再重新读取评分规则"
	case "stale":
		return "评分来源要求或确认已变化，请重新核对要求，再重新读取评分规则"
	default:
		return "评分来源确认状态尚未就绪，请重新读取评分规则并核对要求"
	}
}
func (p *assessmentPage) failed(a *NativeApp, err error) {
	var api *APIError
	if errors.As(err, &api) {
		if containsString([]string{"requirement_unconfirmed", "requirement_invalidated", "review_changed"}, api.Code) {
			p.sourceNeedsRefresh = true
		}
		if api.Status == 409 || p.sourceNeedsRefresh || strings.Contains(api.Code, "input_changed") || strings.Contains(api.Code, "stale") {
			p.invalidate()
		}
	}
	a.handleError(err)
}
func (p *assessmentPage) readUnassessable(a *NativeApp, id string) {
	p.unassessableID, p.unassessableDetail = id, nil
	serial := p.serial
	q := url.Values{"view": {"console"}, "part": {"items"}, "entry_id": {id}, "limit": {"1"}}
	p.reads.call(a, "unassessable", "GET", "/tasks/"+p.task+"/score-rubrics/"+p.rubric+"?"+q.Encode(), nil, func(r Result) {
		if serial != p.serial || id != p.unassessableID {
			return
		}
		if str(r.Data, "task_id") != p.task || str(r.Data, "parent_id") != p.rubric || r.Data["parent_revision"] != p.rubricDetail["revision"] || len(r.Items) != 1 || str(r.Items[0], "id") != id {
			a.Error = "无法评估评分项快照已变化"
			return
		}
		p.unassessableDetail = r.Items[0]
	}, func(err error) {
		if serial == p.serial {
			p.failed(a, err)
		}
	})
}
func (p *assessmentPage) readRubric(a *NativeApp) {
	p.invalidate()
	p.rubricDetail = nil
	id := p.rubric
	serial := p.serial
	if id == "" {
		return
	}
	a.Request("GET", "/tasks/"+p.task+"/score-rubrics/"+id+"?view=console&part=summary", nil, func(r Result) {
		if serial != p.serial || id != p.rubric {
			return
		}
		if str(r.Data, "task_id") != p.task || str(r.Data, "id") != id {
			a.Error = "评分规则范围不匹配"
			return
		}
		p.rubricDetail = r.Data
		p.sourceNeedsRefresh = false
	})
}
func (p *assessmentPage) allowed() bool {
	action := map[string]string{"checks": "check_run", "scores": "score_run", "score-rubrics": "rubric_generate"}[p.kind]
	for _, item := range objects(p.inputs["actions"]) {
		if str(item, "action") == action && item["allowed"] == true {
			return true
		}
	}
	return false
}
func (p *assessmentPage) prerequisite() string {
	if p.kind == "score-rubrics" {
		return ""
	}
	if p.draft == "" {
		return "请先完成人工响应审阅并组表"
	}
	if p.selectedDraft() == nil {
		return "指定初稿尚未核验，请重新读取并核对任务与抽取范围"
	}
	if str(p.selectedDraft(), "validity") != "current" {
		return "所选初稿已过期，请重新组表后评估"
	}
	if p.kind != "scores" {
		return ""
	}
	if p.rubricDetail == nil {
		return "请明确选择并读取评分规则"
	}
	if str(p.rubricDetail, "extraction_job_id") != p.extraction || str(p.rubricDetail, "document_id") != str(p.inputs, "document_id") {
		return "评分规则与当前抽取或招标文件不一致"
	}
	if blocker := p.sourceBlocker(); blocker != "" {
		return blocker
	}
	if str(p.rubricDetail, "validity") != "current" || str(p.rubricDetail, "state") != "confirmed" || object(p.rubricDetail["completeness"])["complete"] != true {
		return "请完成当前评分规则审核并确认整套规则"
	}
	return ""
}
func (p *assessmentPage) body(dry bool) Object {
	body := Object{"dry_run": dry}
	if p.kind == "score-rubrics" {
		body["extraction_job_id"] = p.extraction
	} else {
		body["draft_id"] = p.draft
		body["assessment_date"] = p.date
	}
	if p.kind == "checks" {
		body["mode"] = p.mode
	}
	if p.kind == "scores" {
		body["rubric_id"] = p.rubric
	}
	if p.reasoning != "" && (p.kind != "checks" || p.mode == "combined") {
		body["reasoning"] = p.reasoning
	}
	if !dry {
		body["expected_input_hash"] = object(p.preview["input"])["input_hash"]
		body["retry"] = p.retry
	}
	return body
}
func (p *assessmentPage) preflight(a *NativeApp) {
	if !p.allowed() || p.prerequisite() != "" {
		a.Error = p.prerequisite()
		return
	}
	p.invalidate()
	serial := p.serial
	path := p.base()
	if p.kind != "checks" {
		path += "/preview"
	}
	p.reads.call(a, "preflight", "POST", path, p.body(true), func(r Result) {
		if serial != p.serial {
			return
		}
		input, fixed := object(r.Data["input"]), object(r.Data["preflight"])
		if str(input, "task_id") != p.task || str(input, "extraction_job_id") != p.extraction || str(input, "document_id") != str(p.inputs, "document_id") || len(str(input, "input_hash")) != 64 || input["input_hash"] != fixed["input_hash"] {
			a.Error = "预检输入已变化，请重新核对"
			return
		}
		if p.kind != "score-rubrics" && (str(input, "draft_id") != p.draft || str(input, "assessment_date") != p.date) {
			a.Error = "预检初稿或评估日期不匹配"
			return
		}
		if p.kind == "scores" && (str(r.Data, "rubric_id") != p.rubric || r.Data["rubric_version"] != p.rubricDetail["version"] || r.Data["rubric_input_hash"] != p.rubricDetail["input_hash"]) {
			a.Error = "评分规则已变化，请重新核对"
			return
		}
		p.preview = r.Data
	}, func(err error) {
		if serial == p.serial {
			p.failed(a, err)
		}
	})
}
func (p *assessmentPage) submit(a *NativeApp, recover bool) {
	if !p.allowed() || p.prerequisite() != "" {
		a.Error = p.prerequisite()
		return
	}
	if !recover && p.preview == nil {
		return
	}
	body := p.queueBody
	if !recover {
		body = p.body(false)
	}
	if body == nil {
		return
	}
	p.consent = false
	serial, path := p.serial, p.base()
	a.Do(func(ctx context.Context) (func(), error) {
		r, err := a.Client.Request(ctx, "POST", path, body)
		if err != nil {
			var api *APIError
			if errors.As(err, &api) && api.Code == "queue_unavailable" && str(api.Result.Data, "job_id") != "" {
				return func() {
					if serial != p.serial {
						return
					}
					p.queueBody = body
					p.job.Load(a, str(api.Result.Data, "job_id"), func() { p.load(a) })
					a.Error = "作业已保存但派发未完成，可显式恢复同一请求"
				}, nil
			}
			return func() {
				if serial == p.serial {
					p.failed(a, err)
				}
			}, nil
		}
		return func() {
			if serial != p.serial {
				return
			}
			p.invalidate()
			p.jobCursor = ""
			p.jobPrior = nil
			p.job.Load(a, str(r.Data, "job_id"), func() { p.load(a) })
			p.load(a)
		}, nil
	})
}
func (p *assessmentPage) View(c *ui.Context, a *NativeApp) {

	nativeText(c, "固定抽取与初稿输入。先只读预检，再明确提交；历史报告和部分结果可以查看。")
	if p.inputs == nil {
		return
	}
	changed := false
	if p.kind != "score-rubrics" {
		choices := []string{""}
		for _, key := range []string{"current_draft", "latest_draft", "selected_draft"} {
			id := str(object(p.inputs[key]), "draft_id")
			if id != "" && !containsString(choices, id) {
				choices = append(choices, id)
			}
		}
		if p.draft != "" && !containsString(choices, p.draft) {
			choices = append(choices, p.draft)
		}
		ui.Row(c).FillWidth().Wrap().Gap(12).Children(func() {
			ui.Column(c.Key("assessment-draft")).Width(220).Children(func() {
				if nativeSelect(c, "明确选择初稿", &p.draft, choices) {
					changed = true
				}
			})
			ui.Column(c.Key("assessment-date")).Width(220).Children(func() {
				if nativeField(c, "评估日期 YYYY-MM-DD", &p.date, false) {
					changed = true
				}
			})
			if p.kind == "checks" {
				ui.Column(c.Key("assessment-mode")).Width(220).Children(func() {
					if nativeSelect(c, "规则检查 / 联合检查", &p.mode, []string{"rules", "combined"}) {
						changed = true
					}
				})
			}
		})
	}
	if p.kind != "score-rubrics" && p.prerequisite() != "" {
		nativeButton(c, "前往重新组表").OnClick(func() { a.Navigate("/org/tasks/" + p.task + "/drafts?job=" + p.extraction) })
	}
	if p.kind == "scores" {
		choices := []string{""}
		for _, item := range p.rubrics {
			choices = append(choices, str(item, "id"))
		}
		if p.rubric != "" && !containsString(choices, p.rubric) {
			choices = append(choices, p.rubric)
		}
		ui.Row(c).FillWidth().Wrap().Gap(12).Children(func() {
			ui.Column(c.Key("assessment-rubric")).Width(220).Children(func() {
				if nativeSelect(c, "明确选择评分规则", &p.rubric, choices) {
					p.readRubric(a)
				}
			})
		})
		if p.rubricDetail != nil {
			nativeText(c, "规则状态 "+str(p.rubricDetail, "state")+" / "+str(p.rubricDetail, "validity"))
			nativeButton(c, "打开评分规则审阅").OnClick(func() { a.Navigate("/org/tasks/" + p.task + "/score-rubrics/" + p.rubric + "?job=" + p.extraction) })
		}
		ui.Row(c).FillWidth().Wrap().Gap(8).Children(func() {
			nativeButton(c, "重新核对评分规则来源").Disabled(a.Busy || p.rubric == "").OnClick(func() { p.readRubric(a) })
			nativeButton(c, "核对评分来源要求").OnClick(func() { a.Navigate("/org/tasks/" + p.task + "/requirements?job=" + p.extraction) })
		})
		if p.rubricDetail != nil {
			review := object(p.rubricDetail["requirement_review"])
			nativeText(c, "固定评分来源 "+str(review, "fixed_count")+" 项 / 已确认 "+str(review, "confirmed_count")+" 项")
			nativeText(c, p.sourceBlocker())
		}
		nativePager(c, len(p.rubricPrior)+1, p.rubricNext != "", a.Busy, "上一页评分规则", "下一页评分规则",
			func() {
				p.rubricCursor = p.rubricPrior[len(p.rubricPrior)-1]
				p.rubricPrior = p.rubricPrior[:len(p.rubricPrior)-1]
				p.loadRubrics(a)
			},
			func() {
				p.rubricPrior = append(p.rubricPrior, p.rubricCursor)
				p.rubricCursor = p.rubricNext
				p.loadRubrics(a)
			})
	}
	if ui.TextInput(c, &p.reasoning).Label("推理档位（留空使用服务端默认）").Changed() {
		changed = true
	}
	if changed {
		p.invalidate()
	}
	nativeText(c, p.prerequisite())
	nativeButton(c, "运行只读预检").Disabled(a.Busy || !p.allowed() || p.prerequisite() != "").OnClick(func() { p.preflight(a) })
	if p.preview != nil {
		fixed := object(p.preview["preflight"])
		nativePairs(c, []string{"输入摘要", "计划调用", "阻塞原因", "模型"}, []string{str(object(p.preview["input"]), "input_hash"), str(fixed, "planned_calls"), str(fixed, "admission_blocker"), str(fixed, "model")})
		ids := stringList(p.preview["preflight_unassessable_item_ids"])
		start := min(p.unassessablePage*50, len(ids))
		for _, id := range ids[start:min(start+50, len(ids))] {
			nativeButton(c.Key(id), "核对无法评估评分项 "+id).Disabled(a.Busy).OnClick(func() { p.readUnassessable(a, id) })
		}
		if len(ids) > 0 {
			nativePager(c, p.unassessablePage+1, start+50 < len(ids), false, "上一页无法评估评分项", "下一页无法评估评分项",
				func() { p.unassessablePage-- },
				func() { p.unassessablePage++ })
		}
		if row := p.unassessableDetail; row != nil {
			nativeHeading(c, str(row, "title"))
			nativeText(c, "评估方式："+nativeValue(row["assessment_mode"])+" / 原因："+str(row, "ambiguity_reason"))
			nativeText(c, str(row, "rule_text"))
			bidCitationText(c, object(row["source"]))
		}
		ui.Checkbox(c, &p.consent, "已核对输入、模型与外发范围，授权本次运行")
		ui.Checkbox(c, &p.retry, "明确重试")
		model := p.kind != "checks" || p.mode == "combined"
		nativePrimaryButton(c, "提交作业").Disabled(a.Busy || !p.allowed() || p.prerequisite() != "" || str(fixed, "admission_blocker") != "" || model && !p.consent).OnClick(func() { p.submit(a, false) })
	}
	if p.queueBody != nil {
		nativeButton(c, "恢复已保存作业派发").Disabled(a.Busy).OnClick(func() { p.submit(a, true) })
	}
	p.job.View(c, a)
	nativeHeading(c, "历史报告")
	nativeTable(c, []string{"记录", "完成情况", "有效状态", "规则状态", "总分状态", "时间"}, p.historyRows, func(i int) {
		item := p.history[i]
		report := object(item["report"])
		if report == nil {
			report = item
		}
		a.Navigate("/org/tasks/" + p.task + "/" + p.kind + "/" + str(report, "id") + "?job=" + p.extraction)
	})
	nativePager(c.Key("history-pager"), len(p.prior)+1, p.next != "", false, "上一页历史", "下一页历史",
		func() { p.cursor = p.prior[len(p.prior)-1]; p.prior = p.prior[:len(p.prior)-1]; p.load(a) },
		func() { p.prior = append(p.prior, p.cursor); p.cursor = p.next; p.load(a) })
	nativeHeading(c, "已保存作业")
	nativeTable(c, []string{"作业", "状态", "时间"}, p.jobRows, func(i int) { p.job.Load(a, str(p.jobs[i], "id"), func() { p.load(a) }) })
	nativePager(c.Key("jobs-pager"), len(p.jobPrior)+1, p.jobNext != "", a.Busy, "上一页作业", "下一页作业",
		func() {
			p.jobCursor = p.jobPrior[len(p.jobPrior)-1]
			p.jobPrior = p.jobPrior[:len(p.jobPrior)-1]
			p.loadJobs(a)
		},
		func() { p.jobPrior = append(p.jobPrior, p.jobCursor); p.jobCursor = p.jobNext; p.loadJobs(a) })
}
