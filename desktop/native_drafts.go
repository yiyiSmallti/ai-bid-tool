package main

import (
	"fmt"
	"strings"

	"github.com/egoist/mygo/ui"
)

type draftsPage struct {
	task, job, selected, section, query string
	history                             []Object
	rows, historyRows                   [][]string
	visible                             []Object
	detail, preview                     Object
	ready, retry                        bool
	page                                int
	generation                          uint64
	jobPanel                            jobPanel
}

func newDraftsPage(r Route) NativePage {
	return &draftsPage{task: r.Params["taskId"], job: r.Query.Get("job"), section: "substantive"}
}
func (*draftsPage) Title() string { return "响应表初稿" }
func (*draftsPage) Dirty() bool   { return false }
func (p *draftsPage) Close()      { p.generation++; p.jobPanel.Close() }
func (p *draftsPage) Open(a *NativeApp) {
	if p.job == "" {
		a.Error = "必须明确选择抽取集合，例如 ?job=J"
		return
	}
	a.Request("GET", "/tasks/"+p.task+"/extractions", nil, func(r Result) {
		for _, entry := range r.Items {
			if str(entry, "job_id") == p.job && str(entry, "status") == "succeeded" {
				p.ready = true
			}
		}
		if !p.ready {
			a.Error = "所选抽取不属于当前任务或尚未成功"
			return
		}
		p.load(a)
	})
}
func (p *draftsPage) load(a *NativeApp) {
	a.Request("GET", "/tasks/"+p.task+"/drafts?job="+p.job, nil, func(r Result) {
		p.history = r.Items
		p.historyRows = tableRows(r.Items, "id", "completion", "validity", "created_at")
		if p.selected == "" && len(r.Items) > 0 {
			p.show(a, str(r.Items[len(r.Items)-1], "id"))
		} else if p.selected != "" {
			p.show(a, p.selected)
		}
	})
}
func (p *draftsPage) show(a *NativeApp, id string) {
	p.selected = id
	p.generation++
	generation := p.generation
	p.detail = nil
	a.Request("GET", "/drafts/"+id, nil, func(r Result) {
		if generation != p.generation {
			return
		}
		if str(r.Data, "task_id") != p.task || str(r.Data, "extraction_job_id") != p.job {
			a.Error = "初稿不属于当前任务与抽取"
			return
		}
		p.detail = r.Data
		p.section = "substantive"
		p.page = 0
		p.query = ""
		p.filter()
	})
}
func (p *draftsPage) filter() {
	items := objects(object(p.detail["tables"])[p.section])
	if p.section == "comply_only" || p.section == "gaps" {
		items = objects(p.detail[p.section])
	}
	p.visible = nil
	p.rows = nil
	for _, row := range items {
		text := str(row, "requirement_id") + str(row, "category") + str(row, "location_label") + str(object(row["tender_clause"]), "quote") + str(row, "response_text") + str(row, "deviation") + str(row, "deviation_note") + strings.Join(stringList(row["reasons"]), " ")
		if p.query == "" || strings.Contains(strings.ToLower(text), strings.ToLower(p.query)) {
			p.visible = append(p.visible, row)
		}
	}
	if p.page*50 >= len(p.visible) {
		p.page = 0
	}
	for _, row := range p.visible[p.page*50 : min((p.page+1)*50, len(p.visible))] {
		evidence := []string{}
		for _, item := range objects(row["evidence"]) {
			input := object(item["input"])
			evidence = append(evidence, str(input, "kind")+" / "+str(input, "field_path")+str(input, "evidence_source_id")+" / "+str(input, "quote")+str(input, "visual_observation"))
		}
		p.rows = append(p.rows, []string{str(object(row["tender_clause"]), "quote"), str(row, "response_text"), nativeLabel(str(row, "deviation")), str(row, "deviation_note"), strings.Join(stringList(row["reasons"]), "；"), str(row, "location_label"), strings.Join(evidence, "\n"), str(row, "disposition_at")})
	}
}
func (p *draftsPage) View(c *ui.Context, a *NativeApp) {

	nativeText(c, "三表只纳入人工确认的响应与证据，须遵守项和缺口单列。负偏离如实保留。")
	nativeButton(c, "组表预检").Disabled(a.Busy || !p.ready).OnClick(func() {
		p.preview = nil
		a.Request("POST", "/tasks/"+p.task+"/drafts", Object{"extraction_job_id": p.job, "dry_run": true}, func(r Result) { p.preview = r.Data })
	})
	if p.preview != nil {
		nativeHeading(c, "组表预检")
		nativePairs(c, []string{"响应行", "须遵守", "缺口", "负偏离", "输入标识", "预计时长 ms"}, []string{str(p.preview, "response_requirements"), str(p.preview, "comply_only_requirements"), str(p.preview, "gap_requirements"), str(p.preview, "negative_deviations"), str(p.preview, "input_hash"), str(p.preview, "estimated_duration_ms")})
		for key, value := range object(p.preview["table_rows"]) {
			nativeText(c, key+"表行数："+fmt.Sprint(value))
		}
		for key, value := range object(p.preview["gap_reasons"]) {
			nativeText(c, "缺口 "+key+"："+fmt.Sprint(value))
		}
		ui.Checkbox(c, &p.retry, "明确重试组表")
		nativePrimaryButton(c, "提交组表作业").Disabled(a.Busy || !p.ready).OnClick(func() {
			p.preview = nil
			a.Request("POST", "/tasks/"+p.task+"/drafts", Object{"extraction_job_id": p.job, "dry_run": false, "retry": p.retry}, func(r Result) {
				id := str(r.Data, "job_id")
				if id == "" {
					id = str(r.Data, "generation_job_id")
				}
				p.jobPanel.Load(a, id, func() { p.load(a) })
			})
		})
	}
	p.jobPanel.View(c, a)
	nativeHeading(c, "初稿历史")
	nativeTable(c, []string{"初稿", "完成情况", "有效状态", "创建时间"}, p.historyRows, func(i int) { p.show(a, str(p.history[i], "id")) })
	if p.detail == nil {
		return
	}
	nativeText(c, "当前查看："+p.selected+" / "+nativeLabel(str(p.detail, "completion"))+" / "+nativeLabel(str(p.detail, "validity")))
	ui.Row(c).FillWidth().Wrap().Gap(12).Children(func() {
		ui.Column(c.Key("draft-section")).Width(220).Children(func() {
			if nativeSelect(c, "实质性 / 商务 / 技术 / 须遵守 / 缺口", &p.section, []string{"substantive", "commercial", "technical", "comply_only", "gaps"}) {
				p.page = 0
				p.filter()
			}
		})
		ui.Column(c.Key("draft-search")).Width(220).Gap(6).Children(func() {
			ui.Text(c, "搜索当前表").FontSize(12.5).TextColor(c.Theme().TextMuted)
			if ui.TextInput(c, &p.query).Label("搜索当前表").Placeholder("要求、响应、位置或缺口原因").Height(36).FillWidth().Radius(8).Background(nativePalette(c).Card).Changed() {
				p.page = 0
				p.filter()
			}
		})
	})
	nativeTable(c, []string{"招标条款", "响应正文", "偏离", "具体说明", "缺口原因", "引用位置", "已确认材料", "仅需遵守决定时间"}, p.rows, func(i int) {
		row := p.visible[p.page*50+i]
		suffix := "review"
		for _, reason := range stringList(row["reasons"]) {
			if strings.HasPrefix(reason, "requirement_") {
				suffix = "requirements"
			}
		}
		a.Navigate("/org/tasks/" + p.task + "/" + suffix + "?job=" + p.job + "&requirement=" + str(row, "requirement_id"))
	})
	nativePager(c, p.page+1, (p.page+1)*50 < len(p.visible), false, "上一页", "下一页",
		func() { p.page--; p.filter() },
		func() { p.page++; p.filter() })
}
