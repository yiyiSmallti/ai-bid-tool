package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/egoist/mygo/ui"
)

type responsesPage struct {
	id, job, selected                           string
	task                                        Object
	items, filtered                             []Object
	rows                                        [][]string
	query, category, state, domain, disposition string
	starred, mine, gaps                         bool
	page                                        int
	selection                                   map[string]bool
	batch                                       []responseDisposition
	editor                                      responseEditor
	generation                                  generationPanel
}
type responseDisposition struct {
	ID                        string
	Revision                  any
	Text, Disposition, Reason string
}

func newResponsesPage(r Route) NativePage {
	return &responsesPage{id: r.Params["taskId"], job: r.Query.Get("job"), selected: r.Query.Get("requirement"), category: r.Query.Get("category"), selection: map[string]bool{}}
}
func (*responsesPage) Title() string { return "要求与响应审阅" }
func (p *responsesPage) Dirty() bool {
	if p.editor.Dirty() {
		return true
	}
	for _, item := range p.batch {
		if item.Reason != "" {
			return true
		}
	}
	return false
}
func (p *responsesPage) Close() { p.editor.Close(); p.generation.job.Close() }
func (p *responsesPage) Open(a *NativeApp) {
	if p.job == "" {
		a.Error = "请从任务页明确选择成功的抽取集合"
		return
	}
	a.Request("GET", "/tasks/"+p.id, nil, func(r Result) { p.task = r.Data })
	p.load(a)
}
func (p *responsesPage) load(a *NativeApp) {
	a.Do(func(ctx context.Context) (func(), error) {
		history, err := a.Client.Request(ctx, "GET", "/tasks/"+p.id+"/extractions", nil)
		if err != nil {
			return nil, err
		}
		var extraction Object
		for _, item := range history.Items {
			if str(item, "job_id") == p.job && str(item, "status") == "succeeded" {
				extraction = item
			}
		}
		if extraction == nil {
			return nil, errors.New("所选抽取不可访问或尚未成功")
		}
		requirements, err := a.Client.Request(ctx, "GET", "/tasks/"+p.id+"/requirements?job="+p.job, nil)
		if err != nil {
			return nil, err
		}
		slots, err := a.Client.Request(ctx, "GET", "/tasks/"+p.id+"/cards?job="+p.job, nil)
		if err != nil {
			return nil, err
		}
		if str(slots.Data, "task_id") != p.id || str(slots.Data, "extraction_job_id") != p.job || len(slots.Items) != len(requirements.Items) {
			return nil, errors.New("要求与卡片集合范围不匹配")
		}
		byID := make(map[string]Object, len(slots.Items))
		for _, slot := range slots.Items {
			id := str(slot, "requirement_id")
			if byID[id] != nil {
				return nil, errors.New("卡片集合含重复要求")
			}
			byID[id] = slot
		}
		for _, row := range requirements.Items {
			slot := byID[str(row, "id")]
			source, slotSource := object(row["source"]), object(slot["source"])
			card := object(slot["card"])
			if slot == nil || str(row, "job_id") != p.job || str(source, "document_id") != str(extraction, "document_id") || str(source, "document_id") != str(slotSource, "document_id") || str(source, "chunk_id") != str(slotSource, "chunk_id") || str(source, "quote") != str(slotSource, "quote") {
				return nil, errors.New("要求、引文或卡片关联不一致")
			}
			if card != nil && (str(card, "task_id") != p.id || str(card, "extraction_job_id") != p.job || str(card, "requirement_id") != str(row, "id")) {
				return nil, errors.New("响应卡不属于当前要求")
			}
			for key, value := range slot {
				row[key] = value
			}
		}
		return func() {
			p.items = requirements.Items
			p.filter()
			p.generation.init(a, p.id, p.job, str(extraction, "document_id"))
			if p.selected != "" && p.editor.row == nil {
				for _, row := range p.items {
					if str(row, "id") == p.selected {
						p.open(a, row)
					}
				}
			}
		}, nil
	})
}
func (p *responsesPage) filter() {
	p.filtered = nil
	p.rows = nil
	query := strings.ToLower(p.query)
	for _, row := range p.items {
		card := object(row["card"])
		if p.category != "" && str(row, "category") != p.category || p.state != "" && str(row, "status") != p.state || p.domain != "" && str(card, "review_domain") != p.domain || p.disposition != "" && str(card, "disposition") != p.disposition || p.starred && row["starred"] != true || p.mine && str(row, "status") != "pending_review" || p.gaps && containsString([]string{"eligible", "comply_only"}, str(card, "eligibility")) {
			continue
		}
		if query != "" && !strings.Contains(strings.ToLower(str(row, "text")+"\n"+str(object(row["source"]), "quote")), query) {
			continue
		}
		p.filtered = append(p.filtered, row)
	}
	if p.page*50 >= len(p.filtered) {
		p.page = 0
	}
	end := min((p.page+1)*50, len(p.filtered))
	for _, row := range p.filtered[p.page*50 : end] {
		p.rows = append(p.rows, []string{str(row, "text"), str(row, "category"), str(row, "status"), str(object(row["card"]), "eligibility"), str(object(row["source"]), "quote")})
	}
}
func (p *responsesPage) open(a *NativeApp, row Object) {
	if p.editor.Dirty() {
		a.Confirm("切换要求", "有未保存的响应编辑或评论，是否丢弃？", func() { p.editor.Close(); p.open(a, row) })
		return
	}
	p.selected = str(row, "id")
	p.editor.Open(a, p.id, p.job, row, func() { p.load(a) })
}
func (p *responsesPage) View(c *ui.Context, a *NativeApp) {
	if p.editor.row != nil {
		nativeButton(c, "返回响应列表").Disabled(a.Busy).OnClick(func() {
			if p.editor.Dirty() {
				a.Confirm("返回响应列表", "尚有未保存的响应或审阅原因，返回将丢弃更改。", p.editor.Close)
			} else {
				p.editor.Close()
			}
		})
		p.editor.View(c, a)
		if p.generation.job.id != "" && !p.generation.job.completed {
			p.generation.job.View(c, a)
		}
		return
	}
	nativeHint(c, "打开一条要求，核对来源后编辑响应。保存草稿与提交人工审阅是独立动作。")
	changed := false
	ui.Row(c).FillWidth().Wrap().Gap(12).Children(func() {
		ui.Column(c.Key("response-search")).Width(220).Children(func() {
			changed = nativeField(c, "搜索要求或引文", &p.query, false)
		})
		for _, field := range []struct {
			label   string
			value   *string
			options []string
		}{
			{"类别", &p.category, requirementCategories},
			{"状态", &p.state, []string{"", "missing_card", "draft", "pending_review", "confirmed", "rejected", "needs_material"}},
			{"职责", &p.domain, []string{"", "commercial", "technical", "unclassified"}},
			{"处置", &p.disposition, []string{"", "respond", "comply_only", "undecided"}},
		} {
			ui.Column(c.Key(field.label)).Width(220).Children(func() {
				if nativeSelect(c, field.label, field.value, field.options) {
					changed = true
				}
			})
		}
	})
	ui.Row(c).Wrap().Gap(18).Children(func() {
		if ui.Checkbox(c, &p.starred, "只看星标").Changed() {
			changed = true
		}
		if ui.Checkbox(c, &p.mine, "待审阅").Changed() {
			changed = true
		}
		if ui.Checkbox(c, &p.gaps, "只看缺口").Changed() {
			changed = true
		}
	})
	if changed {
		p.page = 0
		p.selection = map[string]bool{}
		p.generation.invalidate()
		p.filter()
	}
	nativeText(c, fmt.Sprintf("匹配 %d / 全集 %d；所选 %d", len(p.filtered), len(p.items), len(p.selection)))
	nativeTable(c, []string{"要求", "类别", "状态", "资格", "逐字引文"}, p.rows, func(i int) { p.open(a, p.filtered[p.page*50+i]) })
	nativePager(c, p.page+1, (p.page+1)*50 < len(p.filtered), false, "上一页", "下一页",
		func() { p.page--; p.filter() },
		func() { p.page++; p.filter() })
	ui.Row(c).Wrap().Gap(8).Children(func() {
		nativeButton(c, "选择本页用于起草预检").OnClick(func() { p.selectRange(false, false) })
		nativeButton(c, "选择全部匹配用于起草预检").OnClick(func() {
			a.Confirm("固定起草范围", fmt.Sprintf("固定当前匹配的 %d 项要求用于预检？", len(p.filtered)), func() { p.selectRange(true, false) })
		})
		nativeButton(c, "清除选择").OnClick(func() { p.selection = map[string]bool{}; p.generation.ids = nil; p.generation.invalidate() })
	})
	ui.Row(c).Wrap().Gap(8).Children(func() {
		nativeButton(c, "选择本页可处置项").OnClick(func() { p.selectRange(false, true) })
		nativeButton(c, "选择全部匹配可处置项").OnClick(func() {
			a.Confirm("固定处置范围", "待审和已确认条目不纳入处置；固定当前匹配要求？", func() { p.selectRange(true, true) })
		})
		nativeButton(c, "准备批量处置").Disabled(len(p.selection) == 0 || p.editor.Dirty()).OnClick(func() { p.prepareBatch(a) })
	})
	for i := range p.batch {
		item := &p.batch[i]
		nativeText(c, item.Text)
		nativeSelect(c, "处置 "+item.ID, &item.Disposition, []string{"comply_only", "respond"})
		nativeField(c, "逐项理由 "+item.ID, &item.Reason, true)
	}
	if len(p.batch) > 0 {
		nativeButton(c, "提交整批处置").Disabled(a.Busy).OnClick(func() {
			items := make([]Object, 0, len(p.batch))
			for _, item := range p.batch {
				if strings.TrimSpace(item.Reason) == "" {
					a.Error = "每项均需填写处置理由"
					return
				}
				items = append(items, Object{"requirement_id": item.ID, "expected_revision": item.Revision, "disposition": item.Disposition, "reason": item.Reason})
			}
			a.Request("POST", "/tasks/"+p.id+"/cards/dispositions", Object{"extraction_job_id": p.job, "items": items}, func(Result) { p.batch = nil; p.selection = map[string]bool{}; p.load(a) })
		})
	}
	nativeHeading(c, "模型起草与预检")
	p.generation.View(c, a, p.task, func(task Object) { p.task = task }, func() { p.load(a) })
}
func (p *responsesPage) selectRange(all, disposition bool) {
	rows := p.filtered
	if !all {
		rows = rows[p.page*50 : min((p.page+1)*50, len(rows))]
	}
	if len(rows) > 1000 {
		return
	}
	p.selection = map[string]bool{}
	p.generation.ids = nil
	for _, row := range rows {
		card := object(row["card"])
		if disposition && (containsString([]string{"pending_review", "confirmed"}, str(row, "status")) || containsString([]string{"requirement_unconfirmed", "requirement_invalidated"}, str(card, "eligibility"))) {
			continue
		}
		id := str(row, "id")
		p.selection[id] = true
		p.generation.ids = append(p.generation.ids, id)
	}
	p.generation.invalidate()
}
func (p *responsesPage) prepareBatch(a *NativeApp) {
	p.batch = nil
	for _, row := range p.items {
		if !p.selection[str(row, "id")] {
			continue
		}
		card := object(row["card"])
		if containsString([]string{"pending_review", "confirmed"}, str(row, "status")) || containsString([]string{"requirement_unconfirmed", "requirement_invalidated"}, str(card, "eligibility")) {
			a.Error = "所选条目包含不可处置范围"
			p.batch = nil
			return
		}
		p.batch = append(p.batch, responseDisposition{ID: str(row, "id"), Revision: card["revision"], Text: str(row, "text"), Disposition: "comply_only"})
	}
}

type responseEditor struct {
	task, job                                   string
	row, card, conflict                         Object
	kind, text, deviation, note, reason, domain string
	baseline                                    string
	candidates, history, secrets                []Object
	reviewed, warnings                          map[string]bool
	ready, contentDirty                         bool
	document                                    documentPanel
	image                                       signedImagePanel
	materials                                   materialPanel
	changed                                     func()
}

func (e *responseEditor) content() Object {
	return Object{"response_kind": e.kind, "response_text": nullable(e.text), "deviation": e.deviation, "deviation_note": nullable(e.note), "evidence": e.candidates}
}
func (e *responseEditor) Dirty() bool  { return e.ready && (e.contentDirty || e.reason != "") }
func (e *responseEditor) edited() bool { return e.ready && e.contentDirty }
func (e *responseEditor) markEdited() {
	data, _ := json.Marshal(e.content())
	e.contentDirty = string(data) != e.baseline
	e.clearReview()
}
func (e *responseEditor) clearReview() {
	e.reviewed = map[string]bool{}
	e.warnings = map[string]bool{}
}
func (e *responseEditor) Close() {
	e.document.Close()
	e.image.Close()
	e.materials.Close()
	*e = responseEditor{}
}
func (e *responseEditor) Open(a *NativeApp, task, job string, row Object, changed func()) {
	e.Close()
	e.task = task
	e.job = job
	e.row = row
	e.changed = changed
	if card := object(row["card"]); card != nil {
		a.Request("GET", "/cards/"+str(card, "id"), nil, func(r Result) { e.install(a, r.Data) })
	} else {
		e.install(a, nil)
	}
	a.Request("GET", "/confidential-fields", nil, func(r Result) { e.secrets = r.Items })
	e.materials.Load(a, task)
}
func (e *responseEditor) install(a *NativeApp, card Object) {
	if card != nil && (str(card, "task_id") != e.task || str(card, "extraction_job_id") != e.job || str(card, "requirement_id") != str(e.row, "id")) {
		a.Error = "卡片与当前任务、抽取或要求不匹配"
		return
	}
	e.card = card
	e.conflict = nil
	e.history = nil
	e.reason = ""
	e.kind = "commitment"
	e.deviation = "none"
	e.text = ""
	e.note = ""
	e.candidates = []Object{}
	if card != nil {
		content := object(card["content"])
		e.kind = str(content, "response_kind")
		e.deviation = str(content, "deviation")
		e.text = str(content, "response_text")
		e.note = str(content, "deviation_note")
		e.candidates = append(e.candidates, objects(content["evidence"])...)
	}
	data, _ := json.Marshal(e.content())
	e.baseline = string(data)
	e.ready = true
	e.contentDirty = false
	e.clearReview()
}
func (e *responseEditor) editable() bool {
	return e.ready && e.conflict == nil && (e.card == nil || (containsString([]string{"draft", "rejected", "needs_material"}, str(e.card, "state")) && str(e.card, "disposition") != "comply_only"))
}
func (e *responseEditor) blocker() string {
	if e.edited() {
		return "有未保存的编辑，请先保存并重新提交审阅"
	}
	if e.conflict != nil {
		return "修订或材料已变化，请处理冲突"
	}
	if containsString([]string{"requirement_unconfirmed", "requirement_invalidated", "stale_material", "invalid_citation", "needs_reconfirmation"}, str(e.card, "eligibility")) {
		return "要求、引用或材料未确认或已失效"
	}
	if strings.TrimSpace(e.kind) == "" || strings.TrimSpace(e.text) == "" || strings.TrimSpace(e.deviation) == "" || strings.TrimSpace(e.note) == "" || strings.TrimSpace(e.note) == "满足" {
		return "响应种类、正文、偏离和具体说明都需要填写"
	}
	if strings.Contains(e.text, "[REDACTED_") || strings.Contains(e.note, "[REDACTED_") {
		return "请使用保密字段占位符，不能保存外发遮挡标记"
	}
	evidence := objects(e.card["evidence"])
	if e.kind == "evidence" && len(evidence) == 0 {
		return "证据响应至少需要关联一项真实材料"
	}
	for _, item := range evidence {
		if !e.reviewed[str(item, "id")] {
			return "请逐项核对全部关联材料"
		}
	}
	codes, _ := e.card["warning_codes"].([]any)
	for _, code := range codes {
		if !e.warnings[fmt.Sprint(code)] {
			return "请逐项核对全部警示"
		}
	}
	if len(codes) > 0 && strings.TrimSpace(e.reason) == "" {
		return "有警示时需要填写处理理由"
	}
	return ""
}
func (e *responseEditor) write(a *NativeApp, method, path string, body Object) {
	e.clearReview()
	a.Do(func(ctx context.Context) (func(), error) {
		r, err := a.Client.Request(ctx, method, path, body)
		if err != nil {
			var api *APIError
			if errors.As(err, &api) && api.Status == 409 && e.card != nil {
				fresh, readErr := a.Client.Request(ctx, "GET", "/cards/"+str(e.card, "id"), nil)
				if readErr == nil {
					return func() {
						e.conflict = fresh.Data
						e.clearReview()
						a.Error = "修订或材料已改变，编辑已保留。请核对服务器修订后重新处理。"
					}, nil
				}
			}
			return nil, err
		}
		return func() {
			e.install(a, r.Data)
			if e.changed != nil {
				e.changed()
			}
		}, nil
	})
}
func (e *responseEditor) action(a *NativeApp, action string) {
	if e.card == nil || e.edited() || e.conflict != nil {
		return
	}
	if containsString([]string{"withdraw", "reject", "needs_material", "reopen"}, action) && strings.TrimSpace(e.reason) == "" {
		a.Error = "请填写非空操作原因"
		return
	}
	if action == "confirm" && e.blocker() != "" {
		a.Error = e.blocker()
		return
	}
	body := Object{"expected_revision": e.card["revision"], "action": action, "reason": nullable(strings.TrimSpace(e.reason))}
	if action == "confirm" {
		ids := []string{}
		codes := []string{}
		for _, item := range objects(e.card["evidence"]) {
			ids = append(ids, str(item, "id"))
		}
		for _, code := range stringList(e.card["warning_codes"]) {
			codes = append(codes, code)
		}
		body["reviewed_evidence_ids"] = ids
		body["reviewed_warning_codes"] = codes
	}
	a.Confirm("响应人工操作", "仅作用于当前修订 "+str(e.card, "revision")+"："+action, func() { e.write(a, "POST", "/cards/"+str(e.card, "id")+"/actions", body) })
}
func (e *responseEditor) refresh(a *NativeApp) {
	if e.card == nil {
		return
	}
	a.Request("GET", "/cards/"+str(e.card, "id"), nil, func(r Result) {
		oldWarnings, _ := json.Marshal(e.card["warning_codes"])
		newWarnings, _ := json.Marshal(r.Data["warning_codes"])
		if r.Data["revision"] != e.card["revision"] || r.Data["eligibility"] != e.card["eligibility"] || string(oldWarnings) != string(newWarnings) {
			e.conflict = r.Data
			a.Notice = "修订或材料已变化，编辑已保留"
		} else {
			e.card = r.Data
			a.Notice = "已核对当前修订，请重新勾选审阅项"
		}
		e.clearReview()
	})
}
func (e *responseEditor) View(c *ui.Context, a *NativeApp) {
	if e.row == nil {
		return
	}
	source := object(e.row["source"])
	if e.card != nil {
		source = object(e.card["source"])
	}
	ui.Column(c.Key("response-source")).FillWidth().Padding(16).Gap(12).Background(c.Theme().Surface).Radius(5).Children(func() {
		nativeHeading(c, "所选要求与招标来源")
		nativeText(c, str(e.row, "text"))
		nativeHint(c, "逐字引文")
		nativeText(c, str(source, "quote"))
	})
	ui.Row(c).Wrap().Gap(8).Children(func() {
		nativeButton(c, "核对要求确认状态").OnClick(func() {
			a.Navigate("/org/tasks/" + e.task + "/requirements?job=" + e.job + "&requirement=" + str(e.row, "id"))
		})
		nativeButton(c, "在线查看原文位置").OnClick(func() {
			e.document.Open(a, str(source, "document_id"), integer(source, "page"), str(object(source["location"]), "block_id"))
		})
		nativeButton(c, "下载招标原件").OnClick(func() { signedDownload(a, "/documents/"+str(source, "document_id")+"/download-link", "tender") })
		nativeButton(c, "核对当前修订").OnClick(func() { e.refresh(a) })
	})
	e.document.View(c, a)
	if !e.ready {
		nativeText(c, "正在读取当前修订")
		return
	}
	nativePairs(c, []string{"状态", "资格", "修订", "审阅职责", "处置"}, []string{str(e.card, "state"), str(e.card, "eligibility"), str(e.card, "revision"), str(e.card, "review_domain"), str(e.card, "disposition")})
	if e.conflict != nil {
		nativeHeading(c, "服务器当前修订（只读）")
		nativeText(c, str(object(e.conflict["content"]), "response_text"))
		nativeText(c, str(object(e.conflict["content"]), "deviation_note"))
		nativeButton(c, "放弃本地编辑，使用服务器修订").OnClick(func() {
			a.Confirm("使用服务器修订", "将丢弃本地未保存编辑，继续？", func() { e.install(a, e.conflict) })
		})
	}
	editable := e.editable()
	kind := e.kind
	if nativeChoice(c, &kind, []string{"commitment", "evidence"}).Label("响应种类").Disabled(!editable).Changed() {
		apply := func() {
			e.kind = kind
			if kind == "commitment" {
				e.candidates = []Object{}
			}
			e.markEdited()
		}
		if kind == "commitment" && len(e.candidates) > 0 {
			a.Confirm("改为承诺", "将移除全部候选材料，继续？", apply)
		} else {
			apply()
		}
	}
	ui.Text(c, "响应正文")
	if ui.TextArea(c, &e.text).Label("响应正文").Height(140).ReadOnly(!editable).Changed() {
		e.markEdited()
	}
	if nativeChoice(c, &e.deviation, []string{"none", "positive", "negative"}).Label("偏离").Disabled(!editable).Changed() {
		e.markEdited()
	}
	ui.Text(c, "对应关系或具体偏离说明")
	if ui.TextArea(c, &e.note).Label("对应关系或具体偏离说明").Height(90).ReadOnly(!editable).Changed() {
		e.markEdited()
	}
	for _, secret := range e.secrets {
		if secret["archived"] == true {
			continue
		}
		nativeButton(c.Key(str(secret, "id")), "插入保密字段："+str(secret, "label")).Disabled(!editable).OnClick(func() { e.text += str(secret, "placeholder"); e.markEdited() })
	}
	for i, input := range e.candidates {
		nativeText(c, str(input, "kind")+" / "+str(input, "quote")+str(input, "visual_observation"))
		nativeDangerButton(c.Key(fmt.Sprint(i)), "移除候选材料").Disabled(!editable).OnClick(func() { e.candidates = append(e.candidates[:i], e.candidates[i+1:]...); e.markEdited() })
	}
	nativePrimaryButton(c, "保存草稿").Disabled(a.Busy || !editable).OnClick(func() {
		if e.card == nil {
			e.write(a, "POST", "/tasks/"+e.task+"/cards", Object{"extraction_job_id": e.job, "requirement_id": e.row["id"], "content": e.content()})
		} else {
			e.write(a, "PUT", "/cards/"+str(e.card, "id"), Object{"expected_revision": e.card["revision"], "content": e.content()})
		}
	})
	if e.card != nil && editable && !e.edited() {
		nativeButton(c, "标注真实证书页").OnClick(func() {
			a.Navigate("/org/tasks/" + e.task + "/cards/" + str(e.card, "id") + "/annotation?job=" + e.job)
		})
	}
	e.materials.View(c, a, editable && e.kind == "evidence", func(input Object) {
		if len(e.candidates) >= 100 {
			a.Error = "一张卡片最多关联 100 项材料"
			return
		}
		e.candidates = append(e.candidates, input)
		e.markEdited()
	}, func() { e.clearReview(); e.refresh(a) })
	nativeHeading(c, "本修订实际链接材料")
	for _, evidence := range objects(e.card["evidence"]) {
		id := str(evidence, "id")
		input := object(evidence["input"])
		nativeText(c, str(evidence, "material_kind")+" / "+str(evidence, "quote_check")+" / 有效选择："+str(evidence, "active_selection"))
		nativeText(c, str(input, "quote")+str(input, "visual_observation"))
		nativeText(c, "选择 "+str(evidence, "selection_id")+" / 资源修订 "+str(evidence, "resource_revision_id"))
		if str(input, "kind") == "image_region" {
			nativeButton(c.Key("image"+id), "核对关联图片").OnClick(func() {
				rid := str(input, "rendition_id")
				e.image.Load(a, "响应卡实际材料图", "POST", "/screenshot-renditions/"+rid+"/preview-link", "/screenshot-renditions/"+rid+"/content", Object{"sha256": input["expected_image_sha256"]})
			})
		}
		if archive := object(evidence["source_archive"]); archive != nil {
			nativeButton(c.Key("source"+id), "核对固定证书来源页").OnClick(func() {
				sourceID := str(archive, "id")
				e.image.Load(a, "固定证书来源页", "GET", "/evidence-sources/"+sourceID+"/preview/download-link", "/evidence-sources/"+sourceID+"/preview/download", object(archive["preview"]))
			})
		}
		reviewed := e.reviewed[id]
		if ui.Checkbox(c.Key(id), &reviewed, "已核对材料 "+id).Disabled(e.edited() || e.conflict != nil || str(e.card, "state") != "pending_review").Changed() {
			e.reviewed[id] = reviewed
		}
	}
	e.image.View(c, a)
	nativeHeading(c, "警示与人工操作")
	for _, code := range stringList(e.card["warning_codes"]) {
		checked := e.warnings[code]
		if ui.Checkbox(c.Key(code), &checked, code).Disabled(e.edited() || e.conflict != nil).Changed() {
			e.warnings[code] = checked
		}
	}
	nativeField(c, "人工操作原因", &e.reason, true)
	if e.card != nil {
		state := str(e.card, "state")
		actions := []string{}
		switch state {
		case "draft", "rejected", "needs_material":
			actions = []string{"submit"}
		case "pending_review":
			actions = []string{"confirm", "reject", "needs_material", "withdraw"}
		case "confirmed":
			actions = []string{"reopen"}
		}
		ui.Row(c).Wrap().Gap(8).Children(func() {
			for _, action := range actions {
				labels := map[string]string{"submit": "提交审阅", "confirm": "确认响应", "reject": "驳回", "needs_material": "需补材料", "withdraw": "撤回", "reopen": "重开"}
				button := nativeButton
				if action == "reject" || action == "withdraw" {
					button = nativeDangerButton
				} else if action == "confirm" || action == "submit" {
					button = nativePrimaryButton
				}
				button(c, labels[action]).Disabled(a.Busy || e.edited() || e.conflict != nil || (action == "confirm" && e.blocker() != "")).OnClick(func() { e.action(a, action) })
			}
		})
		if state == "pending_review" {
			nativeText(c, e.blocker())
		}
		nativeSelect(c, "审阅职责分类", &e.domain, []string{"commercial", "technical"})
		nativeButton(c, "保存职责分类").Disabled(a.Busy || e.reason == "" || e.edited() || e.conflict != nil).OnClick(func() {
			e.write(a, "POST", "/cards/"+str(e.card, "id")+"/classification", Object{"expected_revision": e.card["revision"], "review_domain": e.domain, "reason": e.reason})
		})
		nativeButton(c, "读取不可变历史").OnClick(func() {
			a.Request("GET", "/cards/"+str(e.card, "id")+"?history=true", nil, func(r Result) { e.history = objects(r.Data["history"]) })
		})
	}
	for _, entry := range e.history {
		nativeText(c, "修订 "+str(entry, "revision")+" / "+str(entry, "action")+" / "+str(entry, "actor_kind"))
		nativeText(c, str(object(entry["content"]), "response_text"))
	}
}
func stringList(v any) []string {
	if values, ok := v.([]string); ok {
		return values
	}
	values, _ := v.([]any)
	out := make([]string, 0, len(values))
	for _, value := range values {
		if text, ok := value.(string); ok {
			out = append(out, text)
		}
	}
	return out
}

type generationPanel struct {
	task, extraction, document, reasoning string
	levels, ids                           []string
	preview                               Object
	authorized                            bool
	job                                   jobPanel
	models                                modelPicker
}

func (g *generationPanel) invalidate() { g.preview = nil; g.authorized = false }
func (g *generationPanel) init(a *NativeApp, task, extraction, document string) {
	if g.document == document && g.extraction == extraction {
		return
	}
	g.task = task
	g.extraction = extraction
	g.document = document
	g.invalidate()
	g.models.load(a)
	g.loadLevels(a)
}

// loadLevels reads the chosen model's reasoning levels from an extraction preflight, which calls no model.
func (g *generationPanel) loadLevels(a *NativeApp) {
	body := Object{"dry_run": true}
	g.models.set(body)
	a.Request("POST", "/documents/"+g.document+"/extract", body, func(r Result) {
		g.levels = []string{""}
		for _, level := range objects(r.Data["reasoning_levels"]) {
			g.levels = append(g.levels, str(level, "name"))
		}
		g.reasoning = str(r.Data, "reasoning")
	})
}
func (g *generationPanel) View(c *ui.Context, a *NativeApp, task Object, changed func(Object), finished func()) {
	if g.task == "" {
		return
	}
	nativeHeading(c, "模型起草预览")
	enabled := task["model_redaction_enabled"] != false
	nativeText(c, fmt.Sprintf("模型外发遮挡：%t。先预检、核对外发范围，再明确提交。", enabled))
	nativeButton(c, "切换外发遮挡").Disabled(a.Busy).OnClick(func() {
		apply := func() {
			g.invalidate()
			a.Request("PUT", "/tasks/"+g.task+"/model-redaction", Object{"expected_revision": task["model_redaction_revision"], "model_redaction_enabled": !enabled}, func(r Result) { changed(r.Data) })
		}
		if enabled {
			a.Confirm("关闭外发遮挡", "报价、联系人、身份和银行信息可能原文外发。确认关闭？", apply)
		} else {
			apply()
		}
	})
	if g.models.View(c) {
		g.invalidate()
		g.loadLevels(a)
	}
	if nativeChoice(c, &g.reasoning, g.levels).Label("官方推理档位").Changed() {
		g.invalidate()
	}
	nativeButton(c, "起草预检").Disabled(a.Busy).OnClick(func() {
		g.invalidate()
		body := Object{"extraction_job_id": g.extraction, "dry_run": true}
		g.models.set(body)
		if g.ids != nil {
			body["requirement_ids"] = g.ids
		}
		if g.reasoning != "" {
			body["reasoning"] = g.reasoning
		}
		a.Request("POST", "/tasks/"+g.task+"/cards/generations", body, func(r Result) { g.preview = r.Data })
	})
	if g.preview != nil {
		nativePairs(c, []string{"模型", "档位", "阻塞原因", "外发遮挡"}, []string{str(g.preview, "model"), str(g.preview, "reasoning"), str(g.preview, "admission_blocker"), str(g.preview, "model_redaction_enabled")})
		nativeText(c, fmt.Sprintf("本次目标要求 %d 项", len(stringList(g.preview["selected_requirements"]))))
		nativeText(c, "输入引用："+strings.Join(stringList(g.preview["input_refs"]), "、"))
		nativeText(c, "输入标识："+str(g.preview, "input_hash"))
		nativeText(c, "遮挡规则版本："+str(g.preview, "redaction_rule_version"))
		nativeText(c, "模型修订："+str(g.preview, "model_revision"))
		nativeText(c, "估算类型："+str(g.preview, "estimate_kind")+" / 预计时长 ms："+str(g.preview, "estimated_duration_ms")+" / tokens："+str(object(object(g.preview["preflight"])["estimate"]), "llm_tokens"))
		for key, value := range object(g.preview["skipped"]) {
			nativeText(c, "跳过 "+key+"："+fmt.Sprint(value))
		}
		for key, value := range object(g.preview["redacted_counts"]) {
			nativeText(c, "遮挡 "+key+"："+fmt.Sprint(value))
		}
		ui.Checkbox(c, &g.authorized, "已核对本次模型、材料、要求范围与外发内容，授权起草")
		nativePrimaryButton(c, "开始模型起草").Disabled(a.Busy || !g.authorized || len(stringList(g.preview["selected_requirements"])) == 0 || str(g.preview, "admission_blocker") != "").OnClick(func() {
			body := Object{"extraction_job_id": g.extraction, "expected_input_hash": g.preview["input_hash"]}
			g.models.set(body)
			if g.ids != nil {
				body["requirement_ids"] = g.ids
			}
			if g.reasoning != "" {
				body["reasoning"] = g.reasoning
			}
			g.invalidate()
			a.Request("POST", "/tasks/"+g.task+"/cards/generations", body, func(r Result) { g.job.Load(a, str(r.Data, "job_id"), finished) })
		})
	}
	g.job.View(c, a)
}
