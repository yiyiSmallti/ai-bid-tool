package main

import (
	"context"
	"fmt"
	"net/url"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/egoist/mygo/ui"
)

var confidentialKinds = settingsOptions("amount", "金额", "contact", "联系人 / 电话", "identity", "证件号", "bank_account", "银行账号", "other", "其他")
var confidentialScopes = settingsOptions("library", "所有任务共用", "task", "每个任务单独填写")

type confidentialPanel struct {
	managementState
	reads                                                           managementReads
	task                                                            string
	embedded                                                        bool
	mode, search                                                    string
	archived                                                        bool
	list, history                                                   managementListing
	fields, values, combined                                        []Object
	panelRows                                                       [][]string
	fieldsLoaded, valuesLoaded                                      bool
	missing                                                         int
	selected, editField, editValue, metadata, historyField          Object
	creating                                                        bool
	fieldKey, fieldLabel, fieldKind, fieldScope, labelDraft, secret string
	shown                                                           string
	revealSerial, detailSerial                                      uint64
	revealTimer                                                     *time.Timer
}

func newConfidentialPage(route Route) *confidentialPanel {
	p := newConfidential(route.Query.Get("task"), false)
	p.route = route
	return p
}
func NewConfidentialPanel(taskID string) *confidentialPanel { return newConfidential(taskID, true) }
func newConfidential(task string, embedded bool) *confidentialPanel {
	title := "保密字段"
	if embedded {
		title = "报价与保密信息"
	}
	return &confidentialPanel{managementState: managementState{title: title}, task: task, embedded: embedded, mode: "values", fieldKind: "amount", fieldScope: "task"}
}
func (p *confidentialPanel) Open(a *NativeApp) {
	if p.task != "" && !settingsUUID(p.task) {
		p.closed = true
		a.Error = "任务 ID 无效，请从任务入口重新打开保密信息。"
		return
	}
	p.load(a, "")
}
func (p *confidentialPanel) Dirty() bool { return p.dirty || p.secret != "" }
func (p *confidentialPanel) PrivacyBlur() {
	p.revealSerial++
	p.shown = ""
	if p.revealTimer != nil {
		p.revealTimer.Stop()
		p.revealTimer = nil
	}
	if slot := p.reads.slots["confidential-reveal"]; slot != nil {
		slot.serial++
		slot.cancel()
	}
}
func (p *confidentialPanel) Close() {
	p.PrivacyBlur()
	p.detailSerial++
	p.managementState.Close()
	p.reads.close()
	p.secret, p.labelDraft, p.fieldKey, p.fieldLabel = "", "", "", ""
	p.editField, p.editValue, p.metadata, p.historyField, p.selected = nil, nil, nil, nil, nil
	p.fields, p.values, p.combined = nil, nil, nil
	p.panelRows = nil
	p.list, p.history = managementListing{}, managementListing{}
	p.creating = false
	p.fieldsLoaded, p.valuesLoaded = false, false
}
func (p *confidentialPanel) failed(a *NativeApp, err error) {
	p.secret = ""
	p.PrivacyBlur()
	if mgDenied(err) {
		p.Close()
	}
	a.Error = settingsErrorText(err)
}
func confidentialFieldID(row Object) string {
	if id := str(row, "id"); id != "" {
		return id
	}
	return str(row, "field_id")
}
func (p *confidentialPanel) canSet(field Object) bool {
	return field != nil && !mgBool(field["archived"]) && (str(field, "scope") == "library" || settingsUUID(p.task))
}
func (p *confidentialPanel) load(a *NativeApp, cursor string) {
	p.PrivacyBlur()
	p.selected = nil
	if p.embedded {
		p.loadPanel(a)
		return
	}
	mode, task := p.mode, p.task
	p.list.Items, p.list.Rows, p.list.Meta = nil, nil, nil
	body := Object{"limit": 25, "cursor": mgNull(cursor), "q": mgNull(p.search), "task_id": mgNull(task), "archived": p.archived}
	path := "/management/confidential-values/query"
	if mode == "fields" {
		path = "/management/confidential-fields/query"
	}
	settingsRead(a, &p.reads, "confidential-list", func(ctx context.Context) (Result, error) {
		return a.Client.ConfidentialRequest(ctx, "POST", path, body)
	}, func(r Result) {
		if p.mode != mode {
			return
		}
		if !p.list.install(r, cursor, func(row Object) []string {
			state := "未填"
			if mode == "fields" {
				state = fmt.Sprintf("可用 · 字段修订 %d", integer(row, "revision"))
				if mgBool(row["archived"]) {
					state = fmt.Sprintf("已归档 · 字段修订 %d", integer(row, "revision"))
				}
			} else if str(row, "status") == "filled" {
				state = fmt.Sprintf("已填 · 值版本 %d", integer(row, "version"))
				if row["tail"] != nil {
					state += " · 末 4 位 " + str(row, "tail")
				}
			}
			return []string{str(row, "label"), str(row, "placeholder"), confidentialKinds.labelByCode[str(row, "kind")], confidentialScopes.labelByCode[str(row, "scope")], state, str(row, "set_at")}
		}) {
			a.Error = settingsFailures["invalid_response"]
		}
	}, func(err error) { p.failed(a, err) })
}
func (p *confidentialPanel) loadPanel(a *NativeApp) {
	p.fields, p.values, p.combined = nil, nil, nil
	p.panelRows = nil
	p.fieldsLoaded, p.valuesLoaded = false, false
	p.missing = 0
	task := p.task
	settingsRead(a, &p.reads, "confidential-panel-fields", func(ctx context.Context) (Result, error) {
		return a.Client.ConfidentialRequest(ctx, "GET", "/confidential-fields", nil)
	}, func(r Result) {
		p.fields = r.Items
		p.fieldsLoaded = true
		if !p.rebuildPanel() {
			p.failed(a, settingsInvalid())
		}
	}, func(err error) { p.failed(a, err) })
	path := "/confidential-values"
	if task != "" {
		path += "?task_id=" + url.QueryEscape(task)
	}
	settingsRead(a, &p.reads, "confidential-panel-values", func(ctx context.Context) (Result, error) { return a.Client.ConfidentialRequest(ctx, "GET", path, nil) }, func(r Result) {
		p.values = r.Items
		p.valuesLoaded = true
		p.missing = integer(r.Data, "missing")
		if !p.rebuildPanel() {
			p.failed(a, settingsInvalid())
		}
	}, func(err error) { p.failed(a, err) })
}
func (p *confidentialPanel) rebuildPanel() bool {
	if !p.fieldsLoaded || !p.valuesLoaded {
		return true
	}
	byField := make(map[string]Object, len(p.values))
	for _, value := range p.values {
		byField[str(value, "field_id")] = value
	}
	p.combined = make([]Object, 0, len(p.fields))
	p.panelRows = make([][]string, 0, len(p.fields))
	for _, field := range p.fields {
		value := byField[str(field, "id")]
		state := "按任务填写"
		if value != nil {
			if !settingsConfidentialValueForField(value, p.task, field) {
				p.panelRows = nil
				p.combined = nil
				return false
			}
			state = "未填"
			if str(value, "status") == "filled" {
				state = fmt.Sprintf("已填 · 值版本 %d", integer(value, "version"))
				if value["tail"] != nil {
					state += " · 末 4 位 " + str(value, "tail")
				}
			}
		}
		p.combined = append(p.combined, Object{"field": field, "value": value})
		p.panelRows = append(p.panelRows, []string{str(field, "label"), str(field, "placeholder"), confidentialKinds.labelByCode[str(field, "kind")], confidentialScopes.labelByCode[str(field, "scope")], state, str(value, "set_at")})
	}
	return true
}
func (p *confidentialPanel) exactField(a *NativeApp, id, key string, done func(Object)) {
	serial, task := p.detailSerial, p.task
	body := Object{"limit": 25, "cursor": nil, "q": nil, "field_id": id, "task_id": mgNull(task), "archived": true}
	settingsRead(a, &p.reads, "confidential-exact-field", func(ctx context.Context) (Result, error) {
		return a.Client.ConfidentialRequest(ctx, "POST", "/management/confidential-fields/query", body)
	}, func(r Result) {
		if serial != p.detailSerial {
			return
		}
		for _, field := range r.Items {
			if str(field, "id") == id && str(field, "key") == key {
				done(field)
				return
			}
		}
		p.failed(a, &APIError{Status: 404, Code: "not_found"})
	}, func(err error) { p.failed(a, err) })
}
func (p *confidentialPanel) exactValue(a *NativeApp, field Object, done func(Object)) {
	if str(field, "scope") == "task" && p.task == "" {
		done(nil)
		return
	}
	serial, task, id := p.detailSerial, p.task, str(field, "id")
	body := Object{"limit": 25, "cursor": nil, "q": nil, "field_id": id, "task_id": mgNull(task), "archived": true}
	settingsRead(a, &p.reads, "confidential-exact-value", func(ctx context.Context) (Result, error) {
		return a.Client.ConfidentialRequest(ctx, "POST", "/management/confidential-values/query", body)
	}, func(r Result) {
		if serial != p.detailSerial {
			return
		}
		for _, value := range r.Items {
			if str(value, "field_id") == id {
				if !settingsConfidentialValueForField(value, task, field) {
					p.failed(a, settingsInvalid())
					return
				}
				done(value)
				return
			}
		}
		p.failed(a, &APIError{Status: 404, Code: "not_found"})
	}, func(err error) { p.failed(a, err) })
}
func (p *confidentialPanel) openSet(a *NativeApp, row Object) {
	p.PrivacyBlur()
	p.secret = ""
	p.editField, p.editValue = nil, nil
	p.detailSerial++
	if p.embedded {
		field, value := object(row["field"]), object(row["value"])
		if p.canSet(field) {
			p.editField, p.editValue = field, value
		}
		return
	}
	id, key := confidentialFieldID(row), str(row, "key")
	p.exactField(a, id, key, func(field Object) {
		p.exactValue(a, field, func(value Object) {
			if !p.canSet(field) {
				a.Error = "字段必须未归档；任务值需要明确的任务上下文。"
				return
			}
			p.editField, p.editValue = field, value
			p.secret = ""
		})
	})
}
func (p *confidentialPanel) closeSecret() {
	p.detailSerial++
	p.secret = ""
	p.editField, p.editValue = nil, nil
	p.dirty = false
}
func (p *confidentialPanel) saveValue(a *NativeApp) {
	if a.Busy || !p.canSet(p.editField) || strings.TrimSpace(p.secret) == "" {
		return
	}
	if utf8.RuneCountInString(p.secret) > 2000 {
		p.secret = ""
		a.Error = "保密值最多 2000 字符，请重新输入。"
		return
	}
	field, value := p.editField, p.editValue
	id := str(field, "id")
	task := ""
	if str(field, "scope") == "task" {
		task = p.task
	}
	body := Object{"value": p.secret}
	path := "/confidential-fields/" + url.PathEscape(id) + "/values"
	if task != "" {
		body["task_id"] = task
	}
	if !p.embedded {
		path = "/management/confidential-fields/" + url.PathEscape(id) + "/values"
		body["task_id"] = mgNull(task)
		body["expected_field_revision"] = field["revision"]
		body["expected_value_id"] = value["value_id"]
	}
	p.secret = ""
	settingsWrite(a, func(ctx context.Context) (Result, error) {
		defer delete(body, "value")
		return a.Client.ConfidentialRequest(ctx, "POST", path, body)
	}, func(r Result) {
		if !settingsConfidentialValueForField(r.Data, task, field) || str(r.Data, "status") != "filled" || str(r.Data, "value_id") == str(value, "value_id") || !p.embedded && integer(r.Data, "version") != integer(value, "version")+1 {
			p.closeSecret()
			p.load(a, "")
			a.Error = "保密写入回执不符合契约；输入已清空，请重新读取当前状态。"
			return
		}
		p.closeSecret()
		a.Notice = fmt.Sprintf("已保存保密值版本 %d；回执仅包含脱敏元数据。", integer(r.Data, "version"))
		p.load(a, "")
	}, func(err error) {
		p.secret = ""
		p.failed(a, err)
		if p.closed {
			return
		}
		if !p.embedded && (settingsCode(err) == "revision_conflict" || settingsCode(err) == "confidential_value_conflict") {
			p.detailSerial++
			p.exactField(a, id, str(field, "key"), func(latest Object) {
				p.exactValue(a, latest, func(current Object) {
					p.editField, p.editValue = latest, current
					p.load(a, "")
					a.Error = "字段或当前值已变化；输入已清空。已重新读取，请核对后重新输入并主动保存。"
				})
			})
		} else {
			p.closeSecret()
			p.load(a, "")
			a.Error = "保存结果尚未确定或填写未完成；输入已清空，请核对当前状态后重新输入。不会自动重试。"
		}
	})
}
func (p *confidentialPanel) reveal(a *NativeApp, value Object) {
	if a.Busy || str(value, "status") != "filled" || str(value, "scope") == "task" && !settingsUUID(p.task) {
		return
	}
	p.PrivacyBlur()
	serial, epoch := p.revealSerial, a.epoch
	valueID, fieldID, key := str(value, "value_id"), str(value, "field_id"), str(value, "key")
	client := a.Client
	p.reads.run(a, "confidential-reveal", func(ctx context.Context) (func(), error) {
		plain, err := client.RevealConfidential(ctx, valueID, fieldID, key)
		if err != nil {
			return nil, settingsSafeError(err)
		}
		return func() {
			if p.closed || serial != p.revealSerial || epoch != a.epoch {
				return
			}
			p.shown = plain
			p.revealTimer = time.AfterFunc(30*time.Second, func() {
				a.update(func() {
					if !p.closed && serial == p.revealSerial && epoch == a.epoch {
						p.PrivacyBlur()
					}
				})
			})
		}, nil
	}, func(err error) { p.failed(a, err) })
}
func (p *confidentialPanel) historyPage(a *NativeApp, row Object, cursor string) {
	p.PrivacyBlur()
	id := confidentialFieldID(row)
	task := ""
	if str(row, "scope") == "task" {
		if !settingsUUID(p.task) {
			return
		}
		task = p.task
	}
	if confidentialFieldID(p.historyField) != id {
		p.history = managementListing{}
	}
	p.historyField = row
	p.history.Items, p.history.Rows, p.history.Meta = nil, nil, nil
	method, path := "POST", "/management/confidential-fields/"+url.PathEscape(id)+"/values/history/query"
	body := Object{"limit": 25, "cursor": mgNull(cursor), "task_id": mgNull(task)}
	if p.embedded {
		method = "GET"
		path = "/confidential-fields/" + url.PathEscape(id) + "/values"
		if task != "" {
			path += "?task_id=" + url.QueryEscape(task)
		}
		body = nil
	}
	settingsRead(a, &p.reads, "confidential-history", func(ctx context.Context) (Result, error) {
		return a.Client.ConfidentialRequest(ctx, method, path, body)
	}, func(r Result) {
		if confidentialFieldID(p.historyField) != id {
			return
		}
		for _, value := range r.Items {
			if !settingsConfidentialValue(value, task, id) || str(value, "status") != "filled" {
				a.Error = settingsFailures["invalid_response"]
				return
			}
		}
		if p.embedded {
			p.history.Items = r.Items
			p.history.Rows = make([][]string, len(r.Items))
			for i, value := range r.Items {
				p.history.Rows[i] = confidentialHistoryRow(value)
			}
		} else if !p.history.install(r, cursor, confidentialHistoryRow) {
			a.Error = settingsFailures["invalid_response"]
		}
	}, func(err error) { p.failed(a, err) })
}
func confidentialHistoryRow(value Object) []string {
	tail := str(value, "tail")
	if tail == "" {
		tail = "无尾号"
	}
	return []string{fmt.Sprint(integer(value, "version")), tail, str(value, "set_at")}
}
func (p *confidentialPanel) closeHistory() {
	p.PrivacyBlur()
	p.historyField = nil
	p.history = managementListing{}
	if slot := p.reads.slots["confidential-history"]; slot != nil {
		slot.serial++
		slot.cancel()
	}
}
func (p *confidentialPanel) openMetadata(a *NativeApp, row Object) {
	p.PrivacyBlur()
	p.detailSerial++
	p.exactField(a, str(row, "id"), str(row, "key"), func(field Object) { p.metadata = field; p.labelDraft = str(field, "label") })
}
func (p *confidentialPanel) saveMetadata(a *NativeApp, archive *bool) {
	if a.Busy || p.metadata == nil {
		return
	}
	target := p.metadata
	id := str(target, "id")
	body := Object{"expected_revision": target["revision"]}
	if archive == nil {
		if strings.TrimSpace(p.labelDraft) == "" {
			return
		}
		body["label"] = strings.TrimSpace(p.labelDraft)
	} else {
		body["archived"] = *archive
	}
	write := func() {
		settingsWrite(a, func(ctx context.Context) (Result, error) {
			return a.Client.ConfidentialRequest(ctx, "POST", "/confidential-fields/"+url.PathEscape(id)+"/revisions", body)
		}, func(r Result) {
			if str(r.Data, "id") != id {
				p.failed(a, settingsInvalid())
				return
			}
			p.metadata = nil
			p.labelDraft = ""
			p.dirty = false
			a.Notice = fmt.Sprintf("字段元数据已保存 · 并发修订 %d", integer(r.Data, "revision"))
			p.load(a, "")
		}, func(err error) {
			p.failed(a, err)
			if !p.closed && settingsCode(err) == "revision_conflict" {
				p.detailSerial++
				p.exactField(a, id, str(target, "key"), func(latest Object) {
					p.metadata = latest
					p.load(a, "")
					a.Error = "字段已变化；未保存名称已保留，请核对最新状态后主动保存。"
				})
			}
		})
	}
	if archive != nil && *archive {
		a.Confirm("归档保密字段", "归档后，该字段不能用于新的占位符；引用它的卡片确认、起草和导出可能被阻止。确定归档？", write)
	} else {
		write()
	}
}
func (p *confidentialPanel) create(a *NativeApp) {
	if a.Busy || !p.creating || !settingsFieldKeyPattern.MatchString(p.fieldKey) || strings.TrimSpace(p.fieldLabel) == "" {
		return
	}
	body := Object{"key": p.fieldKey, "label": strings.TrimSpace(p.fieldLabel), "kind": p.fieldKind, "scope": p.fieldScope}
	settingsWrite(a, func(ctx context.Context) (Result, error) {
		return a.Client.ConfidentialRequest(ctx, "POST", "/confidential-fields", body)
	}, func(Result) {
		p.creating, p.dirty = false, false
		p.fieldKey, p.fieldLabel = "", ""
		p.fieldKind, p.fieldScope = "amount", "task"
		p.mode = "fields"
		a.Notice = "已登记保密字段；键名、类别和范围固定。"
		p.load(a, "")
	}, func(err error) { p.failed(a, err) })
}
func (p *confidentialPanel) View(c *ui.Context, a *NativeApp) {
	if p.closed {
		nativeText(c, "保密信息权限或会话已变化，未保存内容和明文已清除。")
		return
	}
	if !p.embedded {
		nativeText(c, "模型只接收 {{secret.键名}} 占位符，导出时填入登记的值；值加密保存，每次填写保留历史。")
		if p.task != "" {
			mgValue(c, "明确任务范围", p.task)
		}
	}
	if !p.embedded {
		ui.Row(c).Wrap().Gap(8).Disabled(a.Busy).Children(func() {
			nativeButton(c, "重新读取保密字段").OnClick(func() { p.list.Previous = nil; p.load(a, "") })
			if !p.embedded {
				nativeButton(c, "登记字段").OnClick(func() { p.PrivacyBlur(); p.creating = true })
				nativeButton(c, "当前脱敏值").OnClick(func() { p.mode = "values"; p.list.Previous = nil; p.load(a, "") })
				nativeButton(c, "字段定义").OnClick(func() { p.mode = "fields"; p.list.Previous = nil; p.load(a, "") })
			}
		})
	}
	if p.embedded {
		if p.missing > 0 {
			nativeText(c, fmt.Sprintf("%d 项未填", p.missing))
		}
		if len(p.panelRows) == 0 && p.fieldsLoaded && p.valuesLoaded {
			nativeEmpty(c, "shield", "还没有保密字段。登记投标总价、联系人等字段后，即可在此填写本任务的保密值。", func() {
				nativeIconButton(c, "plus", "打开保密字段登记", true).AlignSelf(ui.Center).OnClick(func() { a.Navigate("/org/confidential?task=" + url.QueryEscape(p.task)) })
			})
		} else if len(p.panelRows) > 0 {
			nativeTable(c, []string{"名称", "占位符", "类别", "范围", "当前值", "填写时间"}, p.panelRows, func(i int) { p.PrivacyBlur(); p.selected = p.combined[i] })
		} else {
			nativeHint(c, "正在读取保密字段…")
		}
	} else {
		ui.Row(c).Wrap().Gap(8).Disabled(a.Busy).Children(func() {
			if nativeField(c, "搜索保密字段", &p.search, false) {
				p.list.Previous = nil
				p.load(a, "")
			}
			if ui.Checkbox(c, &p.archived, "包含归档字段").Changed() {
				p.list.Previous = nil
				p.load(a, "")
			}
		})
		nativeTable(c, []string{"名称", "占位符", "类别", "范围", "状态 / 版本", "填写时间"}, p.list.Rows, func(i int) { p.PrivacyBlur(); p.selected = p.list.Items[i] })
		p.list.controls(c, a, "保密字段", func(cursor string) { p.load(a, cursor) })
		if p.list.Meta != nil {
			nativeText(c, fmt.Sprintf("本页 %d 项 · %s；每页反映读取时的状态。", integer(p.list.Meta, "returned"), nativeDisplay(str(p.list.Meta, "as_of"))))
		}
	}
	if p.selected != nil {
		p.viewActions(c, a)
	}
	if p.creating && !p.embedded {
		p.viewCreate(c, a)
	}
	if p.metadata != nil {
		p.viewMetadata(c, a)
	}
	if p.editField != nil {
		p.viewSecret(c, a)
	}
	if p.shown != "" {
		nativeHeading(c, "查看保密值")
		nativeText(c, "此次查看已记入审计，30 秒后自动隐藏。关闭、窗口失焦、隐藏或离开页面也会清空。")
		ui.Text(c, p.shown).Label("已揭示的保密值")
		nativeButton(c, "关闭明文").OnClick(p.PrivacyBlur)
	}
	if p.historyField != nil {
		ui.Divider(c)
		nativeHeading(c, "脱敏值历史")
		mgValue(c, "字段与范围", str(p.historyField, "label")+" · "+confidentialScopes.labelByCode[str(p.historyField, "scope")])
		nativeText(c, "只显示值版本和脱敏信息；字段名称不是历史标签。")
		nativeTable(c, []string{"值版本", "脱敏尾号", "时间"}, p.history.Rows, nil)
		if !p.embedded {
			p.history.controls(c, a, "值历史", func(cursor string) { p.historyPage(a, p.historyField, cursor) })
		}
		nativeButton(c, "关闭值历史").OnClick(p.closeHistory)
	}
}
func (p *confidentialPanel) viewActions(c *ui.Context, a *NativeApp) {
	row := p.selected
	field, value := row, row
	if p.embedded {
		field, value = object(row["field"]), object(row["value"])
	}
	ui.Row(c.Key("confidential-row-actions")).Wrap().Gap(8).Disabled(a.Busy).Children(func() {
		canSet := p.canSet(field)
		if !p.embedded && p.mode == "values" {
			canSet = str(row, "scope") == "library" || settingsUUID(p.task)
		}
		if canSet {
			nativeButton(c, "填写新值").OnClick(func() { p.openSet(a, row) })
		}
		if (p.embedded || p.mode == "values") && str(value, "status") == "filled" {
			nativeButton(c, "查看明文").OnClick(func() { p.reveal(a, value) })
		}
		if str(field, "scope") == "library" || settingsUUID(p.task) {
			historyField := field
			if !p.embedded {
				historyField = row
			}
			nativeButton(c, "值历史").OnClick(func() { p.historyPage(a, historyField, "") })
		}
		if !p.embedded && p.mode == "fields" {
			nativeButton(c, "修改字段").OnClick(func() { p.openMetadata(a, row) })
		}
	})
}
func (p *confidentialPanel) viewCreate(c *ui.Context, a *NativeApp) {
	nativeHeading(c, "登记保密字段")
	ui.Column(c.Key("confidential-create")).Gap(8).Disabled(a.Busy).Children(func() {
		settingsDirtyField(c, "字段名称", &p.fieldLabel, false, &p.dirty)
		settingsDirtyField(c, "字段键名", &p.fieldKey, false, &p.dirty)
		if settingsChoice(c, "字段类别", &p.fieldKind, confidentialKinds) {
			p.dirty = true
		}
		if settingsChoice(c, "字段范围", &p.fieldScope, confidentialScopes) {
			p.dirty = true
		}
		nativeText(c, "键名、类别和范围登记后固定；值通过独立的填写窗口输入。")
		nativePrimaryButton(c, "保存字段定义").Disabled(strings.TrimSpace(p.fieldLabel) == "" || !settingsFieldKeyPattern.MatchString(p.fieldKey)).OnClick(func() { p.create(a) })
		nativeButton(c, "取消登记").OnClick(func() {
			p.creating, p.dirty = false, false
			p.fieldKey, p.fieldLabel = "", ""
			p.fieldKind, p.fieldScope = "amount", "task"
		})
	})
}
func (p *confidentialPanel) viewMetadata(c *ui.Context, a *NativeApp) {
	nativeHeading(c, "修改保密字段")
	mgValue(c, "固定引用", str(p.metadata, "placeholder")+" · "+confidentialKinds.labelByCode[str(p.metadata, "kind")]+" · "+confidentialScopes.labelByCode[str(p.metadata, "scope")])
	nativeText(c, fmt.Sprintf("元数据并发修订 %d；该计数器不是可恢复的历史版本。", integer(p.metadata, "revision")))
	ui.Column(c.Key("confidential-metadata")).Gap(8).Disabled(a.Busy).Children(func() {
		settingsDirtyField(c, "修改字段名称", &p.labelDraft, false, &p.dirty)
		nativeText(c, "归档会阻止新占位符使用，并可能阻止引用该字段的卡片确认、起草和导出。")
		nativeButton(c, "保存字段名称").Disabled(strings.TrimSpace(p.labelDraft) == "").OnClick(func() { p.saveMetadata(a, nil) })
		label := "归档字段"
		if mgBool(p.metadata["archived"]) {
			label = "取消归档字段"
		}
		button := nativeButton
		if !mgBool(p.metadata["archived"]) {
			button = nativeDangerButton
		}
		button(c, label).OnClick(func() { archive := !mgBool(p.metadata["archived"]); p.saveMetadata(a, &archive) })
		nativeButton(c, "关闭字段编辑").OnClick(func() { p.metadata = nil; p.labelDraft = ""; p.dirty = false })
	})
}
func (p *confidentialPanel) viewSecret(c *ui.Context, a *NativeApp) {
	nativeHeading(c, "填写新的保密值")
	mgValue(c, "字段与范围", str(p.editField, "label")+" · "+confidentialScopes.labelByCode[str(p.editField, "scope")])
	state := "该范围尚无值"
	if str(p.editValue, "value_id") != "" {
		state = fmt.Sprintf("当前值版本 %d", integer(p.editValue, "version"))
	}
	nativeText(c, fmt.Sprintf("字段修订 %d · %s；输入不会预填现有明文。", integer(p.editField, "revision"), state))
	ui.Column(c.Key("confidential-secret-input")).Gap(8).Disabled(a.Busy).Children(func() {
		ui.Text(c, "新的保密值")
		if ui.TextInput(c, &p.secret).Password().Label("新的保密值").Changed() {
			p.dirty = true
		}
		nativeText(c, "提交后立即清空输入；冲突需要核对最新版本并重新填写。")
		nativePrimaryButton(c, "保存新值").Disabled(strings.TrimSpace(p.secret) == "" || !p.canSet(p.editField)).OnClick(func() { p.saveValue(a) })
		nativeButton(c, "取消填写").OnClick(p.closeSecret)
	})
}
