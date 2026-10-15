package main

import (
	"errors"
	"fmt"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/egoist/mygo/ui"
)

var managementMemoryKey = regexp.MustCompile(`^[a-z0-9][a-z0-9_.\-]{0,99}$`)
var managementMemoryKinds = []string{"规则", "偏好"}
var managementMemoryKindCodes = map[string]string{"规则": "rule", "偏好": "preference"}
var managementMemoryStatuses = []string{"全部", "候选", "已生效", "已停用"}
var managementMemoryStatusCodes = map[string]string{"候选": "candidate", "已生效": "active", "已停用": "disabled"}
var managementMemoryExpiries = []string{"全部", "尚未过期", "已过期"}
var managementMemoryExpiryCodes = map[string]string{"全部": "all", "尚未过期": "unexpired", "已过期": "expired"}

type memoryPage struct {
	managementState
	reads                                                      managementReads
	id                                                         string
	list, history                                              managementListing
	detail, conflict, receipt                                  Object
	search, filterKind, filterStatus, filterExpiry, filterTags string
	kind, key, text, tags, expiry, reason                      string
	editing, reviewed, outcomeUnknown, outcomeChecked          bool
}

func newMemoryPage(route Route) *memoryPage {
	parts := strings.Split(strings.Trim(route.Path, "/"), "/")
	p := &memoryPage{
		managementState: managementState{route: route, title: "单位记忆库"},
		filterKind:      "全部", filterStatus: "全部", filterExpiry: "全部", kind: "规则",
	}
	if len(parts) == 3 {
		p.id = parts[2]
		p.title = "记忆精确修订与人工审核"
	}
	return p
}

func (p *memoryPage) Open(a *NativeApp) {
	if p.id == "" {
		p.loadList(a, "", false)
	} else {
		p.load(a, p.route.Query.Get("revision"))
	}
}

func (p *memoryPage) Close() {
	p.managementState.Close()
	p.reads.close()
	p.list = managementListing{}
	p.history = managementListing{}
	p.detail, p.conflict, p.receipt = nil, nil, nil
	p.key, p.text, p.tags, p.expiry, p.reason = "", "", "", "", ""
	p.search, p.filterTags = "", ""
	p.editing, p.reviewed, p.outcomeUnknown, p.outcomeChecked = false, false, false, false
}

func mgMemoryRevision(value Object, id string, number int) bool {
	if str(value, "id") == "" || str(value, "memory_id") != id || integer(value, "revision") < 1 || number > 0 && integer(value, "revision") != number || str(object(value["target"]), "scope") != "library" {
		return false
	}
	content := object(value["content"])
	kind := str(content, "kind")
	status := str(value, "status")
	if kind != "rule" && kind != "preference" || status != "candidate" && status != "active" && status != "disabled" || strings.TrimSpace(str(content, "text")) == "" || utf8.RuneCountInString(str(content, "text")) > 2000 || !managementMemoryKey.MatchString(str(content, "conflict_key")) {
		return false
	}
	if status == "active" && (str(value, "actor_kind") != "session" || str(value, "decision") != "approve" || str(value, "confirmed_at") == "") {
		return false
	}
	source := object(value["source"])
	if source == nil {
		return false
	}
	if mgBool(source["provenance_redacted"]) {
		for _, key := range []string{"task_id", "card_id", "card_revision_id", "feedback_event_id", "proposal_job_id", "proposal_run_id"} {
			if source[key] != nil {
				return false
			}
		}
	}
	_, err := mgMemoryTags(strings.Join(stringList(content["tags"]), ","))
	return err == nil
}

func mgMemory(value Object, id string) bool {
	actual := str(value, "id")
	if actual == "" || id != "" && actual != id || !mgMemoryRevision(object(value["current"]), actual, 0) {
		return false
	}
	status := str(value, "effective_status")
	switch status {
	case "candidate", "active", "disabled":
		return status == str(object(value["current"]), "status")
	case "expired":
		return str(object(value["current"]), "status") == "active" && str(object(value["current"]), "expires_at") != ""
	case "deleted":
		return str(value, "deleted_at") != ""
	}
	return false
}

func mgMemoryDetail(value Object, id, number string) bool {
	if !mgMemory(object(value["memory"]), id) {
		return false
	}
	revision := object(object(value["memory"])["current"])
	if integer(value, "current_revision") < integer(revision, "revision") {
		return false
	}
	if number != "" {
		parsed, err := strconv.Atoi(number)
		if err != nil || parsed < 1 || parsed != integer(revision, "revision") {
			return false
		}
	}
	return value["actions"] != nil
}

func mgMemoryTags(value string) ([]string, error) {
	parts := strings.FieldsFunc(value, func(r rune) bool { return r == ',' || r == '，' })
	if len(parts) > 20 {
		return nil, errors.New("标签最多 20 个")
	}
	result := make([]string, 0, len(parts))
	seen := make(map[string]bool, len(parts))
	for _, part := range parts {
		tag := strings.ToLower(strings.TrimSpace(part))
		if tag == "" {
			continue
		}
		if utf8.RuneCountInString(tag) > 30 || seen[tag] {
			return nil, errors.New("标签必须唯一，每个最多 30 字")
		}
		seen[tag] = true
		result = append(result, tag)
	}
	return result, nil
}

func (p *memoryPage) loadList(a *NativeApp, cursor string, outcome bool) {
	tags, err := mgMemoryTags(p.filterTags)
	if err != nil {
		a.Error = err.Error()
		return
	}
	p.list.Items, p.list.Rows, p.list.Meta = nil, nil, nil
	p.outcomeChecked = false
	body := Object{
		"scope": "library", "q": mgNull(p.search), "kind": mgNull(managementMemoryKindCodes[p.filterKind]),
		"status": mgNull(managementMemoryStatusCodes[p.filterStatus]), "tags": tags,
		"expiry": managementMemoryExpiryCodes[p.filterExpiry], "include_deleted": false,
		"cursor": mgNull(cursor), "limit": 25,
	}
	p.reads.call(a, "list", "POST", "/management/memories/query", body, func(r Result) {
		for _, row := range r.Items {
			if !mgMemory(row, "") {
				a.Error = "记忆列表范围不符合契约"
				return
			}
		}
		if len(r.Items) > 25 || !p.list.install(r, cursor, func(row Object) []string {
			revision := object(row["current"])
			content := object(revision["content"])
			return []string{str(content, "conflict_key"), str(content, "text"), strconv.Itoa(integer(revision, "revision")), mgStatus(str(revision, "status")), mgStatus(str(row, "effective_status")), strings.Join(stringList(content["tags"]), "、")}
		}) {
			a.Error = "记忆分页范围不符合契约"
			return
		}
		if outcome && p.outcomeUnknown {
			p.outcomeChecked = true
		}
	}, func(err error) {
		p.list.Previous = nil
		if mgDenied(err) {
			p.Close()
		}
	})
}

func (p *memoryPage) load(a *NativeApp, number string) {
	p.reviewed, p.reason = false, ""
	path := "/management/memories/" + url.PathEscape(p.id)
	if number != "" {
		parsed, err := strconv.Atoi(number)
		if err != nil || parsed < 1 {
			a.Error = "记忆修订必须是正整数"
			return
		}
		path += "?revision=" + url.QueryEscape(number)
	}
	p.reads.call(a, "detail", "GET", path, nil, func(r Result) {
		if !mgMemoryDetail(r.Data, p.id, number) {
			p.detail = nil
			a.Error = "记忆精确修订范围不符合契约"
			return
		}
		p.detail = r.Data
		p.loadHistory(a, "")
	}, func(err error) {
		p.detail = nil
		if mgDenied(err) {
			p.Close()
		}
	})
}

func (p *memoryPage) loadHistory(a *NativeApp, cursor string) {
	p.reads.call(a, "history", "POST", "/management/memories/"+url.PathEscape(p.id)+"/history/query", Object{"cursor": mgNull(cursor), "limit": 25}, func(r Result) {
		for _, item := range r.Items {
			if !mgMemoryRevision(item, p.id, 0) {
				a.Error = "记忆历史不属于当前记录"
				return
			}
		}
		if len(r.Items) > 25 || !p.history.install(r, cursor, func(row Object) []string {
			return []string{strconv.Itoa(integer(row, "revision")), mgStatus(str(row, "status")), str(row, "decision"), str(row, "created_at"), str(row, "actor_kind"), str(row, "decision_reason_sha256")}
		}) {
			a.Error = "记忆历史分页范围不符合契约"
		}
	}, func(err error) {
		p.history = managementListing{}
		if mgDenied(err) {
			p.Close()
		}
	})
}

func (p *memoryPage) recover(a *NativeApp, err error) {
	p.reviewed, p.reason = false, ""
	if mgDenied(err) {
		p.Close()
		return
	}
	if !mgConflict(err) {
		return
	}
	p.reads.call(a, "head", "GET", "/management/memories/"+url.PathEscape(p.id), nil, func(r Result) {
		if !mgMemoryDetail(r.Data, p.id, "") {
			p.detail = nil
			a.Error = "服务器当前记忆范围不符合契约"
			return
		}
		p.conflict = r.Data
		a.Error = "修订、状态已变化或操作结果尚未确定。草稿已保留，审核勾选已清除；请核对服务器修订后主动决定。"
	}, func(readErr error) {
		p.detail = nil
		if mgDenied(readErr) {
			p.Close()
		}
	})
}

func (p *memoryPage) current() Object { return object(object(p.detail["memory"])["current"]) }
func (p *memoryPage) allowed(action string) bool {
	return p.detail != nil && p.conflict == nil && integer(p.current(), "revision") == integer(p.detail, "current_revision") && mgAllowed(p.detail, action)
}

func (p *memoryPage) startEdit() {
	p.kind, p.key, p.text, p.tags, p.expiry = "规则", "", "", "", ""
	if p.id != "" {
		revision := p.current()
		content := object(revision["content"])
		if str(content, "kind") == "preference" {
			p.kind = "偏好"
		}
		p.key, p.text = str(content, "conflict_key"), str(content, "text")
		p.tags, p.expiry = strings.Join(stringList(content["tags"]), ", "), str(revision, "expires_at")
	}
	p.editing, p.dirty, p.reviewed = true, false, false
	p.reason, p.receipt, p.conflict = "", nil, nil
}

func (p *memoryPage) save(a *NativeApp) {
	if p.outcomeUnknown || p.conflict != nil || p.id != "" && !p.allowed("revise") {
		return
	}
	tags, err := mgMemoryTags(p.tags)
	if err != nil {
		a.Error = err.Error()
		return
	}
	if !managementMemoryKey.MatchString(p.key) || strings.TrimSpace(p.text) == "" || utf8.RuneCountInString(p.text) > 2000 {
		a.Error = "请填写有效的冲突键和 2000 字以内的正文"
		return
	}
	if p.expiry != "" {
		expires, err := time.Parse(time.RFC3339, p.expiry)
		if err != nil || !expires.After(time.Now()) {
			a.Error = "到期时间需为含时区的未来时间"
			return
		}
	}
	body := Object{"content": Object{"kind": managementMemoryKindCodes[p.kind], "conflict_key": p.key, "text": p.text, "tags": tags}, "expires_at": mgNull(p.expiry)}
	method, path := "POST", "/memories"
	previous := 0
	if p.id == "" {
		body["target"] = Object{"scope": "library"}
	} else {
		method, path = "PUT", path+"/"+url.PathEscape(p.id)
		previous = integer(p.current(), "revision")
		body["expected_revision"] = previous
	}
	mgCall(a, method, path, body, func(r Result) {
		memory := object(r.Data["memory"])
		if !mgMemory(memory, p.id) || str(object(memory["current"]), "status") != "candidate" || integer(object(memory["current"]), "revision") != previous+1 {
			a.Error = "候选保存回执范围不符合契约"
			return
		}
		p.receipt = memory
		p.editing, p.dirty = false, false
		p.key, p.text, p.tags, p.expiry = "", "", "", ""
		a.Notice = "候选记忆已保存，仍需本人审核；不会确认任何证据。"
		if p.id == "" {
			p.loadList(a, "", false)
		} else {
			p.load(a, "")
		}
	}, func(err error) {
		if p.id != "" {
			p.recover(a, err)
			return
		}
		if mgDenied(err) {
			p.Close()
			return
		}
		if mgConflict(err) {
			p.outcomeUnknown, p.outcomeChecked = true, false
			a.Error = "保存结果尚未确定，草稿已保留。请主动读取并核对记忆库，在明确允许前不能再次保存。"
		}
	})
}

func (p *memoryPage) decide(a *NativeApp, action string) {
	if !p.allowed(action) || p.editing || strings.TrimSpace(p.reason) == "" || action == "approve" && !p.reviewed {
		return
	}
	number := integer(p.current(), "revision")
	a.Confirm("记忆人工决定", fmt.Sprintf("对精确修订 %d 作出此决定？原因仅保留哈希；决定不会确认任何证据。", number), func() {
		if !p.allowed(action) || integer(p.current(), "revision") != number {
			return
		}
		path := "/memories/" + url.PathEscape(p.id) + "/decisions"
		body := Object{"expected_revision": number, "reason": strings.TrimSpace(p.reason)}
		if action == "disable" {
			path = "/memories/" + url.PathEscape(p.id) + "/disable"
		} else {
			body["action"] = action
		}
		p.reason, p.reviewed = "", false
		mgCall(a, "POST", path, body, func(r Result) {
			memory := object(r.Data["memory"])
			if !mgMemory(memory, p.id) || integer(object(memory["current"]), "revision") != number+1 {
				a.Error = "人工决定回执范围不符合契约"
				return
			}
			p.receipt = memory
			p.history.Previous = nil
			p.load(a, "")
		}, func(err error) { p.recover(a, err) })
	})
}

func (p *memoryPage) viewEditor(c *ui.Context, a *NativeApp) {
	ui.Column(c.Key("memory-editor")).Disabled(a.Busy || p.outcomeUnknown || p.conflict != nil).Gap(9).Children(func() {
		if mgChoice(c, "记忆类型", &p.kind, managementMemoryKinds) {
			p.dirty = true
		}
		for _, input := range []struct {
			label     string
			value     *string
			multiline bool
		}{{"冲突键", &p.key, false}, {"记忆正文", &p.text, true}, {"标签（逗号分隔）", &p.tags, false}, {"到期时间（含时区的 ISO 时间，可留空）", &p.expiry, false}} {
			if nativeField(c, input.label, input.value, input.multiline) {
				p.dirty = true
			}
		}
		nativeText(c, "同类记忆使用同一冲突键表示互斥规则。不要录入报价、身份、银行、凭据或保密字段内容。")
		nativePrimaryButton(c, "保存候选记忆").OnClick(func() { p.save(a) })
	})
	nativeButton(c, "取消编辑").Disabled(a.Busy).OnClick(func() {
		cancel := func() {
			p.editing, p.dirty = false, false
			p.key, p.text, p.tags, p.expiry = "", "", "", ""
			p.conflict = nil
		}
		if p.dirty {
			a.Confirm("放弃未保存记忆", "放弃当前候选草稿？", cancel)
		} else {
			cancel()
		}
	})
}

func (p *memoryPage) View(c *ui.Context, a *NativeApp) {
	if p.closed {
		nativeText(c, "记忆权限或会话已变化，请重新打开。")
		return
	}
	nativeText(c, "候选经本人审核后才生效。记忆用于指导工作，不能作为证据或替代人工确认。")
	if p.id == "" {
		p.viewList(c, a)
		return
	}
	if p.detail == nil {
		nativeText(c, "正在读取可访问的精确修订。")
		return
	}
	revision := p.current()
	content := object(revision["content"])
	nativeHeading(c, str(content, "conflict_key"))
	mgValue(c, "记忆正文", str(content, "text"))
	mgValue(c, "显示 / 当前修订", fmt.Sprintf("%d / %d", integer(revision, "revision"), integer(p.detail, "current_revision")))
	mgValue(c, "存储状态", mgStatus(str(revision, "status")))
	mgValue(c, "服务器生效状态", mgStatus(str(p.detail, "current_effective_status")))
	mgValue(c, "服务器读取时间", str(p.detail, "as_of"))
	mgValue(c, "标签", strings.Join(stringList(content["tags"]), "、"))
	mgValue(c, "到期时间", str(revision, "expires_at"))
	if integer(revision, "revision") != integer(p.detail, "current_revision") {
		nativeText(c, "此历史修订已被取代，不代表当前生效内容。")
		nativeButton(c, "查看当前修订").OnClick(func() {
			a.Navigate(fmt.Sprintf("/org/memory/%s?revision=%d", p.id, integer(p.detail, "current_revision")))
		})
	}
	if str(p.detail, "current_effective_status") == "expired" || str(revision, "status") == "disabled" {
		nativeText(c, "重新启用必须先编辑为候选，再经本人审核通过。")
	}
	if p.allowed("revise") && !p.editing {
		nativeButton(c, "编辑为候选").Disabled(a.Busy).OnClick(p.startEdit)
	}
	if p.editing {
		if str(revision, "status") == "active" {
			nativeText(c, "保存会立即撤下原生效内容；新候选审核通过前没有生效内容。")
		}
		p.viewEditor(c, a)
	}
	if p.conflict != nil {
		nativeHeading(c, "服务器当前修订 · 草稿保留")
		mgValue(c, "当前修订", strconv.Itoa(integer(p.conflict, "current_revision")))
		mgValue(c, "当前正文", str(object(object(object(p.conflict["memory"])["current"])["content"]), "text"))
		nativeButton(c, "已核对服务器修订，继续保留草稿").Disabled(a.Busy).OnClick(func() { p.detail, p.conflict = p.conflict, nil; p.reviewed, p.reason = false, "" })
	}
	ui.Divider(c)
	nativeHeading(c, "来源与责任")
	source := object(revision["source"])
	if mgBool(source["provenance_redacted"]) {
		nativeText(c, "任务来源不可访问，来源标识已隐藏。")
	} else {
		origin := "人工提案"
		if str(source, "origin") == "system" {
			origin = "已记录的人工反馈形成的系统候选"
		}
		mgValue(c, "来源", origin)
		for _, field := range []struct{ key, label string }{{"card_revision_id", "来源卡片精确修订"}, {"feedback_event_id", "反馈事件"}, {"proposal_job_id", "候选作业"}, {"proposal_run_id", "候选运行"}} {
			if value := str(source, field.key); value != "" {
				mgValue(c, field.label, value)
			}
		}
		if task := str(source, "task_id"); task != "" {
			nativeButton(c, "查看来源任务反馈队列").OnClick(func() { a.Navigate("/org/tasks/" + task + "/memory-feedback") })
		}
	}
	if !p.editing && p.conflict == nil && (p.allowed("approve") || p.allowed("reject") || p.allowed("disable")) {
		nativeHeading(c, "人工决定 · 当前精确修订")
		ui.Column(c).Disabled(a.Busy).Gap(8).Children(func() {
			if p.allowed("approve") {
				ui.Checkbox(c, &p.reviewed, "我已核对本精确修订的内容、冲突键、来源与有效期")
			}
			nativeField(c, "操作原因（必填，仅保留哈希）", &p.reason, true)
			ui.Row(c).Wrap().Gap(8).Children(func() {
				if p.allowed("approve") {
					nativePrimaryButton(c, "审核通过此精确修订").Disabled(!p.reviewed || strings.TrimSpace(p.reason) == "").OnClick(func() { p.decide(a, "approve") })
				}
				if p.allowed("reject") {
					nativeDangerButton(c, "驳回并停用候选").Disabled(strings.TrimSpace(p.reason) == "").OnClick(func() { p.decide(a, "reject") })
				}
				if p.allowed("disable") {
					nativeDangerButton(c, "停用记忆").Disabled(strings.TrimSpace(p.reason) == "").OnClick(func() { p.decide(a, "disable") })
				}
			})
		})
	}
	nativeHeading(c, "不可变修订历史")
	nativeTable(c, []string{"精确修订", "存储状态", "决定", "时间", "操作身份", "原因 SHA-256"}, p.history.Rows, func(i int) {
		a.Navigate(fmt.Sprintf("/org/memory/%s?revision=%d", p.id, integer(p.history.Items[i], "revision")))
	})
	p.history.controls(c, a, "记忆历史", func(cursor string) { p.loadHistory(a, cursor) })
	p.viewReceipt(c, a)
}

func (p *memoryPage) viewList(c *ui.Context, a *NativeApp) {
	ui.Row(c).FillWidth().Wrap().Gap(12).AlignItems(ui.End).Children(func() {
		ui.Column(c).Width(220).Children(func() { nativeField(c, "搜索记忆", &p.search, false) })
		ui.Column(c).Width(220).Children(func() { mgChoice(c, "类型", &p.filterKind, []string{"全部", "规则", "偏好"}) })
		ui.Column(c).Width(220).Children(func() { mgChoice(c, "存储状态", &p.filterStatus, managementMemoryStatuses) })
		ui.Column(c).Width(220).Children(func() { mgChoice(c, "有效期", &p.filterExpiry, managementMemoryExpiries) })
		ui.Column(c).Width(220).Children(func() { nativeField(c, "按标签筛选（逗号分隔）", &p.filterTags, false) })
	})
	ui.Row(c).Wrap().Gap(8).Children(func() {
		nativeButton(c, "查询记忆").Disabled(a.Busy).OnClick(func() { p.list.Previous = nil; p.loadList(a, "", false) })
		nativePrimaryButton(c, "新增候选记忆").Disabled(a.Busy || p.editing || p.outcomeUnknown).OnClick(p.startEdit)
	})
	if p.outcomeUnknown {
		nativeText(c, "前次保存结果尚未确定，不能自动或直接再次提交。")
		nativeButton(c, "重新读取并核对未确定保存").Disabled(a.Busy).OnClick(func() {
			p.search, p.filterKind, p.filterStatus, p.filterExpiry, p.filterTags = p.key, p.kind, "候选", "全部", ""
			p.list.Previous = nil
			p.loadList(a, "", true)
		})
		nativeButton(c, "已核对记录，允许再次提交").Disabled(a.Busy || !p.outcomeChecked).OnClick(func() {
			a.Confirm("核对未确定结果", "前次请求可能已经创建候选。已核对服务器记录，仍允许再次主动保存保留草稿？", func() {
				p.outcomeUnknown, p.outcomeChecked = false, false
				a.Notice = "已允许再次主动保存；不会自动重发。"
			})
		})
	}
	if p.editing {
		p.viewEditor(c, a)
	}
	nativeTable(c, []string{"冲突键", "正文", "精确修订", "存储状态", "生效状态", "标签"}, p.list.Rows, func(i int) {
		row := p.list.Items[i]
		a.Navigate(fmt.Sprintf("/org/memory/%s?revision=%d", str(row, "id"), integer(object(row["current"]), "revision")))
	})
	p.list.controls(c, a, "记忆", func(cursor string) { p.loadList(a, cursor, false) })
	if p.list.Meta != nil {
		mgValue(c, "服务器读取时间", str(p.list.Meta, "as_of"))
	}
	p.viewReceipt(c, a)
}

func (p *memoryPage) viewReceipt(c *ui.Context, a *NativeApp) {
	if p.receipt == nil {
		return
	}
	mgValue(c, "本次回执修订", strconv.Itoa(integer(object(p.receipt["current"]), "revision")))
	mgValue(c, "回执生效状态", mgStatus(str(p.receipt, "effective_status")))
	nativeButton(c, "查看回执的候选与审核下一步").OnClick(func() {
		a.Navigate(fmt.Sprintf("/org/memory/%s?revision=%d", str(p.receipt, "id"), integer(object(p.receipt["current"]), "revision")))
	})
}

type memoryFeedbackPage struct {
	managementState
	reads         managementReads
	task          string
	list          managementListing
	selected, job Object
	timer         *time.Timer
	failures      int
}

func newMemoryFeedbackPage(route Route) *memoryFeedbackPage {
	parts := strings.Split(strings.Trim(route.Path, "/"), "/")
	return &memoryFeedbackPage{managementState: managementState{route: route, title: "任务记忆反馈队列"}, task: parts[2]}
}
func (p *memoryFeedbackPage) Open(a *NativeApp) { p.load(a, "") }
func (p *memoryFeedbackPage) Close() {
	p.managementState.Close()
	p.reads.close()
	if p.timer != nil {
		p.timer.Stop()
	}
	p.list, p.selected, p.job = managementListing{}, nil, nil
}
func (p *memoryFeedbackPage) fail(err error) {
	if mgDenied(err) {
		p.Close()
	}
}
func (p *memoryFeedbackPage) load(a *NativeApp, cursor string) {
	query := url.Values{"management": {"true"}, "limit": {"25"}}
	if cursor != "" {
		query.Set("cursor", cursor)
	}
	p.list.Items, p.list.Rows, p.list.Meta = nil, nil, nil
	p.reads.call(a, "feedback-list", "GET", "/tasks/"+url.PathEscape(p.task)+"/memory-feedback?"+query.Encode(), nil, func(r Result) {
		for _, row := range r.Items {
			if str(row, "task_id") != p.task || str(row, "id") == "" || str(row, "card_id") == "" || str(row, "before_revision_id") == "" || str(row, "after_revision_id") == "" {
				a.Error = "反馈记录不属于当前任务或缺少精确修订"
				return
			}
			if str(row, "candidate_job_id") != "" {
				events := stringList(row["candidate_event_ids"])
				found := false
				for _, id := range events {
					if id == str(row, "id") {
						found = true
					}
				}
				if !found || len(events) > 100 || str(row, "kind") == "card_confirmed" {
					a.Error = "候选作业事件范围不符合契约"
					return
				}
			}
		}
		if len(r.Items) > 25 || !p.list.install(r, cursor, func(row Object) []string {
			kind := map[string]string{"card_edited": "人工修改", "card_rejected": "人工驳回", "card_confirmed": "人工确认（仅评价样本）"}[str(row, "kind")]
			return []string{kind, str(row, "created_at"), str(row, "card_id"), str(row, "before_revision_id"), str(row, "after_revision_id"), str(row, "candidate_job_id")}
		}) {
			a.Error = "反馈分页范围不符合契约"
			return
		}
		if requested := p.route.Query.Get("job"); requested != "" && p.selected == nil {
			for _, row := range r.Items {
				if str(row, "candidate_job_id") == requested {
					p.openJob(a, row)
					return
				}
			}
			a.Notice = "指定作业不在当前页，请翻页核对对应反馈记录。"
		}
	}, p.fail)
}
func (p *memoryFeedbackPage) openJob(a *NativeApp, row Object) {
	if str(row, "candidate_job_id") == "" {
		return
	}
	if p.timer != nil {
		p.timer.Stop()
	}
	p.selected, p.job, p.failures = row, nil, 0
	p.readJob(a)
}
func (p *memoryFeedbackPage) schedule(a *NativeApp, delay time.Duration) {
	if p.timer != nil {
		p.timer.Stop()
	}
	p.timer = time.AfterFunc(delay, func() {
		a.Window.Update(func() {
			if p.closed || p.selected == nil {
				return
			}
			if !a.Window.IsVisible() || a.Window.IsMinimized() {
				p.schedule(a, 2*time.Second)
				return
			}
			p.readJob(a)
		})
	})
}
func (p *memoryFeedbackPage) readJob(a *NativeApp) {
	if p.selected == nil || p.closed {
		return
	}
	jobID := str(p.selected, "candidate_job_id")
	events := stringList(p.selected["candidate_event_ids"])
	p.reads.call(a, "candidate-job", "GET", "/jobs/"+url.PathEscape(jobID), nil, func(r Result) {
		if p.selected == nil || str(p.selected, "candidate_job_id") != jobID {
			return
		}
		job := r.Data
		if str(job, "id") != jobID || str(job, "kind") != "memory_candidate" {
			a.Error = "候选作业范围不符合契约"
			return
		}
		for _, item := range objects(object(job["result"])["items"]) {
			found := false
			for _, event := range events {
				if str(item, "event_id") == event {
					found = true
				}
			}
			if !found {
				a.Error = "候选结果包含未记录的反馈事件"
				return
			}
		}
		p.job, p.failures = job, 0
		status := str(job, "status")
		if status == "running" || status == "queued" {
			p.schedule(a, 2*time.Second)
		}
	}, func(err error) {
		if mgDenied(err) {
			p.Close()
			return
		}
		p.failures++
		var api *APIError
		retriable := !errors.As(err, &api) || api.Status == 429 || api.Status >= 500
		if p.failures <= 5 && retriable {
			delay := min(30*time.Second, 2*time.Second*time.Duration(1<<p.failures))
			if api != nil {
				if seconds, parseErr := strconv.Atoi(api.RetryAfter); parseErr == nil {
					delay = max(delay, time.Duration(seconds)*time.Second)
				}
			}
			p.schedule(a, delay)
		}
	})
}
func (p *memoryFeedbackPage) canRetry() bool {
	if p.selected == nil || p.job == nil || str(p.job, "id") != str(p.selected, "candidate_job_id") {
		return false
	}
	status := str(p.job, "status")
	return status == "queued" || status == "failed" || status == "cancelled"
}

func (p *memoryFeedbackPage) retry(a *NativeApp) {
	if !p.canRetry() {
		return
	}
	jobID := str(p.selected, "candidate_job_id")
	events := append([]string(nil), stringList(p.selected["candidate_event_ids"])...)
	a.Confirm("恢复已保存的候选作业", fmt.Sprintf("恢复作业 %s？只使用已记录的 %d 个反馈事件，不改变卡片人工决定。", jobID, len(events)), func() {
		if !p.canRetry() || str(p.job, "id") != jobID {
			return
		}
		mgCall(a, "POST", "/tasks/"+url.PathEscape(p.task)+"/memory-candidates", Object{"event_ids": events, "action": Object{"retry": true}}, func(r Result) {
			if str(r.Data, "job_id") != jobID || str(r.Data, "task_id") != p.task || !mgBool(r.Data["reused"]) {
				a.Error = "恢复回执未引用原已保存作业"
				return
			}
			a.Notice = "已恢复原候选作业；候选仍需本人审核，卡片决定保留。"
			p.readJob(a)
		}, func(err error) {
			if mgDenied(err) {
				p.Close()
				return
			}
			var api *APIError
			if errors.As(err, &api) && api.Code == "queue_unavailable" && str(api.Result.Data, "job_id") == jobID {
				a.Notice = "人工决定与原候选作业已保存，派发暂未完成；可主动恢复原作业。"
			}
			p.readJob(a)
		})
	})
}
func (p *memoryFeedbackPage) View(c *ui.Context, a *NativeApp) {
	if p.closed {
		nativeText(c, "任务不可访问，请重新打开反馈队列。")
		return
	}
	nativeText(c, "这里只读取已记录的人工卡片反馈。人工确认仅形成评价样本，不生成记忆候选。")
	nativeTable(c, []string{"反馈类型", "时间", "响应卡", "原修订", "新修订", "候选作业"}, p.list.Rows, func(i int) { p.openJob(a, p.list.Items[i]) })
	p.list.controls(c, a, "反馈", func(cursor string) { p.load(a, cursor) })
	if p.selected == nil {
		return
	}
	nativeHeading(c, "原已保存候选作业")
	mgValue(c, "作业", str(p.selected, "candidate_job_id"))
	mgValue(c, "已记录事件", strconv.Itoa(len(stringList(p.selected["candidate_event_ids"]))))
	if p.job != nil {
		mgValue(c, "状态", mgStatus(str(p.job, "status")))
		result := object(p.job["result"])
		if str(result, "completion") == "partial" {
			nativeText(c, "部分完成，请逐项核对已保存候选、跳过项和失败原因。")
		}
		if code := str(object(p.job["error"]), "code"); code != "" {
			mgValue(c, "作业失败代码", code)
		}
		for index, item := range objects(result["items"]) {
			ui.Column(c.Key(fmt.Sprintf("candidate-result-%d", index))).Gap(5).Children(func() {
				mgValue(c, "反馈事件", str(item, "event_id"))
				mgValue(c, "处理结果", str(item, "outcome"))
				if code := str(item, "error_code"); code != "" {
					mgValue(c, "跳过或失败代码", code)
				}
				if memory := str(item, "memory_id"); memory != "" {
					nativeText(c, "候选已保存，仍需本人核对与审核。")
					nativeButton(c, "打开候选记忆审核").OnClick(func() { a.Navigate("/org/memory/" + memory) })
				}
			})
		}
	}
	nativeButton(c, "主动读取原作业状态").Disabled(a.Busy).OnClick(func() { p.readJob(a) })
	nativeButton(c, "恢复原已保存作业").Disabled(a.Busy || !p.canRetry()).OnClick(func() { p.retry(a) })
}
