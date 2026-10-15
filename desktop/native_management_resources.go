package main

import (
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/egoist/mygo/ui"
)

type managementInput struct {
	Key, Label, Value string
	Multiline, Secret bool
}
type managementChapter struct {
	Title    string
	Children []*managementChapter
}
type resourcePage struct {
	managementState
	reads                                         managementReads
	kind, id                                      string
	search, state, status, productFilter          string
	list, history, lifeHistory                    managementListing
	detail, revision, original, conflict, receipt Object
	inputs                                        []managementInput
	baseData                                      Object
	chapters                                      []*managementChapter
	files                                         []managementFile
	productPicker, filterPicker                   managementProductPicker
	fields                                        []Object
	editing, uploading, pinning                   bool
	lot, reason, asOf, dateAdvice                 string
	preview                                       *ui.Bitmap
	previewPage, previewZoom                      int
}

var managementNames = map[string]string{"products": "产品", "features": "功能", "templates": "模板", "profiles": "单位资料", "certificates": "证照"}
var managementReasons = []string{"已过时", "重复记录", "不再提供", "其他"}
var managementReasonCodes = map[string]string{"已过时": "obsolete", "重复记录": "duplicate", "不再提供": "unavailable", "其他": "other"}

func newResourcePage(route Route, kind string) *resourcePage {
	parts := strings.Split(strings.Trim(route.Path, "/"), "/")
	page := &resourcePage{managementState: managementState{route: route, title: managementNames[kind] + "库"}, kind: kind, state: "可用", status: "全部", reason: managementReasons[0], previewPage: 1, previewZoom: 100}
	if len(parts) > 2 {
		page.id = parts[2]
		page.title = managementNames[kind] + "详情"
	}
	page.blank()
	return page
}
func (p *resourcePage) endpoint() string { return "/management/resources/" + p.kind }
func (p *resourcePage) blank() {
	p.baseData = Object{}
	p.inputs = nil
	p.chapters = nil
	p.files = nil
	p.fields = nil
	add := func(key, label string, multiline, secret bool) {
		p.inputs = append(p.inputs, managementInput{Key: key, Label: label, Multiline: multiline, Secret: secret})
	}
	switch p.kind {
	case "products":
		add("name", "产品名称", false, false)
		add("vendor", "厂商", false, false)
		add("model", "型号", false, false)
		add("model_version", "型号版本", false, false)
		add("official_url", "官网来源", false, false)
		add("whitepaper_url", "白皮书来源", false, false)
	case "features":
		add("product_id", "所属产品 ID", false, false)
		p.inputs[0].Value = p.route.Query.Get("product")
		add("name", "功能名称", false, false)
		add("description", "功能说明", true, false)
		add("status", "实现状态", false, false)
		p.inputs[3].Value = "planned"
	case "templates":
		add("name", "模板名称", false, false)
		add("project_types", "适用项目类型（每行一项）", true, false)
	case "profiles":
		add("name", "单位名称", false, false)
		add("registration_details", "登记信息", true, true)
		add("performance_summary", "履约业绩摘要", true, true)
		add("standard_wording", "标准表述", true, true)
	case "certificates":
		add("name", "证照名称", false, false)
		add("kind", "证照类别", false, false)
		p.inputs[1].Value = "qualification"
		add("number", "证号", false, false)
		add("valid_from", "有效期起（YYYY-MM-DD）", false, false)
		add("valid_until", "有效期止（YYYY-MM-DD，可留空）", false, false)
	}
}
func (p *resourcePage) Open(a *NativeApp) {
	if p.id == "" {
		p.loadList(a, "")
		if p.kind == "features" {
			p.filterPicker.load(a, &p.reads, "product-filter", "", true, func(error) { p.Close() })
		}
	} else {
		p.loadDetail(a, p.route.Query.Get("revision"), true)
	}
}
func (p *resourcePage) Close() {
	p.managementState.Close()
	p.reads.close()
	p.detail = nil
	p.revision = nil
	p.conflict = nil
	p.receipt = nil
	p.list = managementListing{}
	p.history = managementListing{}
	p.lifeHistory = managementListing{}
	p.preview = nil
	p.original = nil
	p.productPicker, p.filterPicker = managementProductPicker{}, managementProductPicker{}
	p.search, p.productFilter, p.lot, p.asOf, p.dateAdvice = "", "", "", "", ""
	p.blank()
}
func (p *resourcePage) failed(a *NativeApp, err error) {
	if mgDenied(err) {
		p.Close()
		return
	}
	if p.id == "" || !mgConflict(err) {
		return
	}
	p.reads.call(a, "head", "GET", p.endpoint()+"/"+url.PathEscape(p.id), nil, func(r Result) {
		if !p.validDetail(r.Data, "") {
			a.Error = "当前修订范围不符合契约"
			return
		}
		p.conflict = r.Data
		a.Error = "保存发生冲突或结果尚未确定。草稿已保留，请核对服务器当前修订并明确继续；不会自动重试。"
	}, func(readErr error) {
		if mgDenied(readErr) {
			p.Close()
		}
	})
}
func (p *resourcePage) listRow(row Object) []string {
	return []string{
		str(row, "name"),
		fmt.Sprintf("第 %d 版", integer(row, "revision")),
		mgStatus(str(object(row["lifecycle"]), "state")),
		mgStatus(str(row, "provenance")),
		str(row, "revised_at"),
	}
}
func (p *resourcePage) loadList(a *NativeApp, cursor string) {
	p.list.serial++
	run := p.list.serial
	p.list.Items = nil
	p.list.Rows = nil
	p.list.Meta = nil
	state := map[string]string{"可用": "active", "已停用": "inactive", "全部": "all"}[p.state]
	body := Object{"q": mgNull(p.search), "state": state, "cursor": mgNull(cursor), "limit": 25}
	if utf8.RuneCountInString(strings.TrimSpace(p.search)) > 200 {
		a.Error = "搜索文字不能超过 200 字"
		return
	}
	if p.kind == "features" {
		body["product_id"] = mgNull(p.productFilter)
		status := map[string]string{"已声明实现": "implemented", "开发中": "developing", "计划中": "planned"}[p.status]
		body["implementation_status"] = mgNull(status)
	}
	p.reads.call(a, "list", "POST", p.endpoint()+"/query", body, func(r Result) {
		if p.closed || run != p.list.serial {
			return
		}
		for _, row := range r.Items {
			if str(object(row["ref"]), "kind") != p.kind || str(object(row["ref"]), "resource_id") == "" || integer(row, "revision") < 1 {
				a.Error = "列表资源范围不符合契约"
				return
			}
		}
		if !p.list.install(r, cursor, p.listRow) {
			a.Error = "列表分页范围不符合契约"
		}
	}, func(err error) {
		if mgDenied(err) {
			p.Close()
		}
		p.list.Previous = nil
	})
}
func (p *resourcePage) validDetail(value Object, number string) bool {
	ref := object(value["ref"])
	revision := object(object(value["detail"])["revision"])
	if str(ref, "kind") != p.kind || str(ref, "resource_id") != p.id || str(revision, "id") == "" || integer(revision, "revision") < 1 || integer(value, "current_revision") < integer(revision, "revision") {
		return false
	}
	if number != "" {
		expected, err := strconv.Atoi(number)
		if err != nil || expected != integer(revision, "revision") {
			return false
		}
	}
	return revision["data"] != nil
}
func (p *resourcePage) loadDetail(a *NativeApp, number string, history bool) {
	if number != "" {
		value, err := strconv.Atoi(number)
		if err != nil || value < 1 {
			a.Error = "修订必须是正整数"
			return
		}
	}
	path := p.endpoint() + "/" + url.PathEscape(p.id)
	if number != "" {
		path += "?revision=" + url.QueryEscape(number)
	}
	p.preview = nil
	p.dateAdvice = ""
	p.reads.call(a, "detail", "GET", path, nil, func(r Result) {
		if !p.validDetail(r.Data, number) {
			p.detail, p.revision = nil, nil
			a.Error = "资源详情范围不符合契约"
			return
		}
		p.detail = r.Data
		p.revision = object(object(r.Data["detail"])["revision"])
		p.original = object(object(r.Data["detail"])["file"])
		if p.kind == "templates" {
			p.original = object(p.revision["file"])
		}
		if p.kind == "profiles" {
			p.reads.call(a, "fields", "GET", "/confidential-fields", nil, func(r Result) { p.fields = r.Items }, func(err error) {
				if mgDenied(err) {
					p.Close()
				}
			})
		}
		if history {
			p.loadHistory(a, false, "")
			p.loadHistory(a, true, "")
		}
	}, func(err error) {
		p.detail, p.revision = nil, nil
		if mgDenied(err) {
			p.Close()
		}
	})
}
func (p *resourcePage) loadHistory(a *NativeApp, life bool, cursor string) {
	listing := &p.history
	suffix := "history"
	if life {
		listing = &p.lifeHistory
		suffix = "lifecycle/history"
	}
	listing.serial++
	run := listing.serial
	p.reads.call(a, suffix, "POST", p.endpoint()+"/"+url.PathEscape(p.id)+"/"+suffix+"/query", Object{"cursor": mgNull(cursor), "limit": 25}, func(r Result) {
		if p.closed || listing.serial != run {
			return
		}
		for _, item := range r.Items {
			if str(object(item["ref"]), "resource_id") != p.id || str(object(item["ref"]), "kind") != p.kind {
				a.Error = "历史记录不属于当前资源"
				return
			}
		}
		if !listing.install(r, cursor, func(row Object) []string {
			if life {
				return []string{fmt.Sprintf("生命周期 %d · 内容 %d", integer(row, "revision"), integer(row, "resource_revision")), mgStatus(str(row, "before")) + " → " + mgStatus(str(row, "after")), str(row, "reason_code"), str(row, "created_at"), str(row, "actor_kind")}
			}
			current := "历史修订"
			if mgBool(row["current"]) {
				current = "当前修订"
			}
			return []string{strconv.Itoa(integer(row, "revision")), str(row, "name"), str(row, "created_at"), current}
		}) {
			a.Error = "历史分页范围不符合契约"
		}
	}, func(err error) {
		listing.Items = nil
		listing.Rows = nil
		listing.Meta = nil
		listing.Previous = nil
		if mgDenied(err) {
			p.Close()
		}
	})
}
func (p *resourcePage) startEdit(a *NativeApp, upload bool) {
	p.blank()
	p.editing = true
	p.uploading = upload
	p.conflict = nil
	p.receipt = nil
	p.dirty = false
	if p.revision != nil {
		p.baseData = mgCopy(object(p.revision["data"]))
		for i := range p.inputs {
			p.inputs[i].Value = str(p.baseData, p.inputs[i].Key)
		}
		if p.kind == "templates" {
			p.inputs[1].Value = strings.Join(stringList(p.baseData["project_types"]), "\n")
			p.chapters = mgChapters(p.baseData["chapters"])
		}
	}
	if p.kind == "profiles" {
		p.reads.call(a, "fields", "GET", "/confidential-fields", nil, func(r Result) { p.fields = r.Items }, func(err error) {
			if mgDenied(err) {
				p.Close()
			}
		})
	}
	if p.kind == "features" {
		p.productPicker.load(a, &p.reads, "product-editor", "", false, func(error) { p.Close() })
	}
}
func mgChapters(value any) []*managementChapter {
	rows := objects(value)
	result := make([]*managementChapter, 0, len(rows))
	for _, row := range rows {
		result = append(result, &managementChapter{Title: str(row, "title"), Children: mgChapters(row["children"])})
	}
	return result
}
func mgChapterData(chapters []*managementChapter) []Object {
	result := make([]Object, 0, len(chapters))
	for _, chapter := range chapters {
		result = append(result, Object{"title": chapter.Title, "children": mgChapterData(chapter.Children)})
	}
	return result
}
func mgChapterCount(chapters []*managementChapter) int {
	count := len(chapters)
	for _, chapter := range chapters {
		count += mgChapterCount(chapter.Children)
	}
	return count
}
func (p *resourcePage) formData() (Object, error) {
	data := mgCopy(p.baseData)
	for _, input := range p.inputs {
		value := strings.TrimSpace(input.Value)
		switch input.Key {
		case "official_url", "whitepaper_url", "model_version", "valid_from", "valid_until":
			data[input.Key] = mgNull(value)
		case "project_types":
			if value == "" {
				data[input.Key] = nil
			} else {
				data[input.Key] = strings.Split(value, "\n")
			}
		default:
			data[input.Key] = value
		}
	}
	if str(data, "name") == "" || utf8.RuneCountInString(str(data, "name")) > 200 {
		return nil, fmt.Errorf("请填写 200 字以内的名称")
	}
	if p.kind == "products" && (str(data, "vendor") == "" || str(data, "model") == "") {
		return nil, fmt.Errorf("请填写厂商和型号")
	}
	if p.kind == "features" && (str(data, "product_id") == "" || str(data, "description") == "") {
		return nil, fmt.Errorf("请填写所属产品和功能说明")
	}
	if p.kind == "certificates" {
		if str(data, "number") == "" {
			return nil, fmt.Errorf("请填写证号")
		}
		if from, until := str(data, "valid_from"), str(data, "valid_until"); from != "" && until != "" && from > until {
			return nil, fmt.Errorf("有效期起不能晚于有效期止")
		}
	}
	if p.kind == "templates" {
		if len(p.chapters) == 0 {
			data["chapters"] = nil
		} else {
			if mgChapterCount(p.chapters) > 200 {
				return nil, fmt.Errorf("声明章节最多 200 个节点")
			}
			data["chapters"] = mgChapterData(p.chapters)
		}
		var validate func([]*managementChapter, int) bool
		validate = func(chapters []*managementChapter, depth int) bool {
			if len(chapters) > 100 || depth > 6 && len(chapters) > 0 {
				return false
			}
			for _, chapter := range chapters {
				if strings.TrimSpace(chapter.Title) == "" || utf8.RuneCountInString(chapter.Title) > 200 || !validate(chapter.Children, depth+1) {
					return false
				}
			}
			return true
		}
		if !validate(p.chapters, 1) {
			return nil, fmt.Errorf("每个声明章节需有 200 字以内标题，层级最多六层")
		}
	}
	return data, nil
}
func (p *resourcePage) save(a *NativeApp) {
	if p.closed || p.conflict != nil {
		a.Error = "请先核对服务器当前修订并明确继续"
		return
	}
	data, err := p.formData()
	if err != nil {
		a.Error = err.Error()
		return
	}
	body := Object{"data": data}
	path := "/resources/" + p.kind
	if p.id != "" {
		if !mgAllowed(p.detail, "revise") || integer(p.revision, "revision") != integer(p.detail, "current_revision") {
			a.Error = "当前精确修订不能修改"
			return
		}
		path += "/" + url.PathEscape(p.id) + "/revisions"
		body["expected_revision"] = p.detail["current_revision"]
	}
	done := func(r Result) {
		if p.closed {
			return
		}
		revision := r.Data
		if p.uploading {
			revision = Object{"id": str(r.Data, "certificate_revision_id"), "revision": r.Data["revision"], "certificate_id": r.Data["certificate_id"], "data": r.Data["data"]}
		}
		if str(revision, "id") == "" || integer(revision, "revision") < 1 || p.id != "" && str(revision, map[string]string{"products": "product_id", "features": "feature_id", "templates": "template_id", "profiles": "profile_id", "certificates": "certificate_id"}[p.kind]) != p.id {
			a.Error = "保存回执不属于当前资源"
			return
		}
		p.receipt = r.Data
		p.editing = false
		p.uploading = false
		p.dirty = false
		p.conflict = nil
		p.blank()
		a.Notice = managementNames[p.kind] + "已保存为新的精确修订"
		if p.id == "" {
			p.list.Previous = nil
			p.loadList(a, "")
		} else {
			p.history.Previous = nil
			p.loadDetail(a, strconv.Itoa(integer(revision, "revision")), true)
		}
	}
	if p.kind == "templates" || p.uploading {
		if len(p.files) == 0 {
			a.Error = "请选择新的原件，元数据修改不能复用旧文件"
			return
		}
		if p.uploading {
			path = "/resources/certificates/" + url.PathEscape(p.id) + "/file-revisions"
			parts := make([]Object, len(p.files))
			for i, file := range p.files {
				parts[i] = Object{"rotation": file.Rotation}
			}
			body["parts"] = parts
		}
		mgUpload(a, path, body, "file", p.files, done, func(err error) { p.failed(a, err) })
	} else {
		mgCall(a, "POST", path, body, done, func(err error) { p.failed(a, err) })
	}
}
func (p *resourcePage) chooseFiles(a *NativeApp) {
	a.PickFiles(p.kind == "certificates", func(paths []string) {
		files := append([]managementFile(nil), p.files...)
		if p.kind == "templates" {
			files = nil
		}
		for _, path := range paths {
			stat, err := os.Stat(path)
			if err != nil {
				a.Error = "无法读取所选原件"
				return
			}
			name := filepath.Base(path)
			ext := strings.ToLower(filepath.Ext(name))
			allowed := ext == ".docx"
			if p.kind == "certificates" {
				allowed = ext == ".pdf" || ext == ".png" || ext == ".jpg" || ext == ".jpeg"
			}
			if !stat.Mode().IsRegular() || !allowed || stat.Size() == 0 || len(name) > 200 || strings.ContainsAny(name, "\x00\r\n") {
				a.Error = "请选择名称安全、非空的指定格式原件"
				return
			}
			files = append(files, managementFile{Path: path})
		}
		if len(files) > 20 || p.kind == "templates" && len(files) > 1 {
			a.Error = "一个证照最多 20 个文件；一个模板仅接受一个 DOCX"
			return
		}
		var total int64
		for _, file := range files {
			stat, err := os.Stat(file.Path)
			if err != nil {
				a.Error = "原件不可读取"
				return
			}
			total += stat.Size()
		}
		if total > 40*1024*1024 {
			a.Error = "原件合计不能超过 40 MiB"
			return
		}
		p.files = files
		p.dirty = true
	})
}
func (p *resourcePage) lifecycle(a *NativeApp) {
	state, action, text := "inactive", "deactivate", "停用"
	if str(object(p.detail["lifecycle"]), "state") == "inactive" {
		state, action, text = "active", "restore", "恢复"
	}
	if !mgAllowed(p.detail, action) {
		a.Error = "当前资源不能执行此操作"
		return
	}
	a.Confirm(text+managementNames[p.kind], text+"此记录？已有任务继续保留各自固定的修订；停用将阻止新的选择和版本替换。", func() {
		reason := managementReasonCodes[p.reason]
		if state == "active" {
			reason = "restored"
		}
		body := Object{"expected_revision": p.detail["current_revision"], "expected_lifecycle_revision": object(p.detail["lifecycle"])["revision"], "state": state, "reason_code": reason}
		mgCall(a, "POST", p.endpoint()+"/"+url.PathEscape(p.id)+"/lifecycle", body, func(r Result) {
			if str(object(r.Data["lifecycle"]), "state") != state || str(r.Data, "existing_selections") != "preserved" || str(object(object(r.Data["event"])["ref"]), "resource_id") != p.id {
				a.Error = "生命周期回执范围不符合契约"
				return
			}
			p.receipt = r.Data
			p.lifeHistory.Previous = nil
			p.loadDetail(a, strconv.Itoa(integer(p.revision, "revision")), true)
		}, func(err error) { p.failed(a, err) })
	})
}
func (p *resourcePage) pin(a *NativeApp) {
	task := p.route.Query.Get("task")
	if task == "" || p.revision == nil {
		a.Error = "请从明确任务打开资料库后固定精确修订"
		return
	}
	body := Object{"revision": p.revision["revision"], "lot": mgNull(p.lot)}
	idKey := map[string]string{"products": "product_id", "features": "feature_id", "templates": "template_id", "profiles": "profile_id", "certificates": "certificate_id"}[p.kind]
	body[idKey] = p.id
	revisionID, number := str(p.revision, "id"), integer(p.revision, "revision")
	mgCall(a, "POST", "/tasks/"+url.PathEscape(task)+"/"+p.kind, body, func(r Result) {
		value := r.Data
		key := strings.TrimSuffix(p.kind, "s") + "_revision_id"
		if p.kind == "features" {
			key = "feature_revision_id"
		}
		if str(value, "task_id") != task || str(value, key) != revisionID || integer(value, "revision") != number || str(value, "id") == "" {
			a.Error = "任务固定回执不属于所选精确修订"
			return
		}
		p.receipt = value
		p.pinning = false
		p.lot = ""
		a.Notice = "任务已固定所选精确修订；不会自动采用将来的版本"
	}, func(err error) {
		if mgDenied(err) {
			p.Close()
		}
		if mgConflict(err) {
			p.pinning = false
			a.Error = "任务选择结果尚未确定，请核对任务的固定版本后再主动决定；不会自动重试。"
		}
	})
}
func (p *resourcePage) previewCertificate(a *NativeApp) {
	if p.revision == nil || p.original == nil {
		return
	}
	revisionID := str(p.revision, "id")
	page, scale := p.previewPage, p.previewZoom
	zoom := 1
	if scale > 100 {
		zoom = 2
	}
	path := fmt.Sprintf("/resources/certificates/revisions/%s/file/pages/%d/preview?zoom=%d", url.PathEscape(revisionID), page, zoom)
	p.reads.image(a, "certificate-preview", "", path, nil, func(bitmap *ui.Bitmap) {
		if str(p.revision, "id") == revisionID && p.previewPage == page && p.previewZoom == scale {
			p.preview = bitmap
		}
	}, func(err error) {
		p.preview = nil
		if mgDenied(err) {
			p.Close()
		}
	})
}
func (p *resourcePage) viewForm(c *ui.Context, a *NativeApp) {
	nativeHeading(c, "编辑"+managementNames[p.kind])
	nativeHint(c, "保存会创建新修订，已固定到任务的资料保持原版本。")
	ui.Column(c.Key("resource-form")).FillWidth().MaxWidth(860).Height(420).Gap(10).Children(func() {
		ui.Scroll(c.Key("fields")).Grow(1).FillWidth().Padding(0, 12, 12, 0).Gap(16).Children(func() {
			ui.Row(c).Wrap().FillWidth().AlignItems(ui.Start).Gap(16).Children(func() {
				for i := range p.inputs {
					input := &p.inputs[i]
					field := ui.Column(c.Key(input.Key)).Width(340).Shrink(0).Gap(6)
					if input.Multiline || input.Secret || input.Key == "product_id" {
						field.FillWidth()
					}
					field.Children(func() {
						switch input.Key {
						case "product_id":
							p.productPicker.view(c, a, &p.reads, "product-editor", &input.Value, false, func() { p.dirty = true }, func(error) { p.Close() })
						case "status":
							value := mgStatus(input.Value)
							choices := []string{"已声明实现，仍需真实材料", "开发中", "计划中"}
							if mgChoice(c, input.Label, &value, choices) {
								input.Value = map[string]string{choices[0]: "implemented", choices[1]: "developing", choices[2]: "planned"}[value]
								p.dirty = true
							}
						case "kind":
							value := mgStatus(input.Value)
							if mgChoice(c, input.Label, &value, []string{"单位证照", "人员证书"}) {
								input.Value = map[string]string{"单位证照": "qualification", "人员证书": "personnel"}[value]
								p.dirty = true
							}
						default:
							if input.Secret {
								mgSecretEditor(c.Key(input.Key), input.Label, &input.Value, p.fields, &p.dirty)
							} else if nativeField(c, input.Label, &input.Value, input.Multiline) {
								p.dirty = true
							}
						}
					})
				}
			})
			if p.kind == "features" {
				nativeText(c, "实现状态只是一项声明。模拟或模型生成图片不会成为真实截图，也不会确认任何响应卡。")
			}
			if p.kind == "profiles" {
				nativeText(c, "保密字段以标签插入；真实值只在导出时解析。")
			}
			if p.kind == "templates" {
				nativeHeading(c, "声明章节")
				mgEditChapters(c, a, &p.chapters, 1, &p.dirty, mgChapterCount(p.chapters))
				nativeText(c, "声明章节不会替代 DOCX 原件中的实际章节。")
			}
			if p.kind == "templates" || p.uploading {
				nativeButton(c, "选择新的原件").Disabled(a.Busy).OnClick(func() { p.chooseFiles(a) })
				for i, file := range p.files {
					index := i
					ui.Row(c.Key(fmt.Sprintf("file-%d", i))).Wrap().Gap(8).Children(func() {
						nativeText(c, filepath.Base(file.Path))
						if p.uploading {
							nativeButton(c, "上移").Disabled(a.Busy || i == 0).OnClick(func() { p.files[index-1], p.files[index] = p.files[index], p.files[index-1]; p.dirty = true })
							nativeButton(c, "下移").Disabled(a.Busy || i+1 == len(p.files)).OnClick(func() { p.files[index+1], p.files[index] = p.files[index], p.files[index+1]; p.dirty = true })
							nativeButton(c, fmt.Sprintf("旋转 %d°", file.Rotation)).Disabled(a.Busy).OnClick(func() { p.files[index].Rotation = (p.files[index].Rotation + 90) % 360; p.dirty = true })
						}
						nativeDangerButton(c, "移除文件").Disabled(a.Busy).OnClick(func() { p.files = append(p.files[:index], p.files[index+1:]...); p.dirty = true })
					})
				}
			}
		})
		nativeActions(c).Children(func() {
			nativePrimaryButton(c, "保存"+managementNames[p.kind]).Disabled(a.Busy || p.closed).OnClick(func() { p.save(a) })
			nativeButton(c, "取消编辑").Disabled(a.Busy).OnClick(func() {
				cancel := func() { p.editing = false; p.uploading = false; p.dirty = false; p.blank(); p.conflict = nil }
				if p.dirty {
					a.Confirm("放弃未保存编辑", "放弃已填写的内容和所选文件？", cancel)
				} else {
					cancel()
				}
			})
		})
	})
}
func mgEditChapters(c *ui.Context, a *NativeApp, chapters *[]*managementChapter, depth int, dirty *bool, total int) {
	for index, chapter := range *chapters {
		i := index
		ui.Column(c.Key(fmt.Sprintf("chapter-%d-%d", depth, i))).Padding(8, 12).Gap(6).Children(func() {
			if nativeField(c, fmt.Sprintf("第 %d 层章节 %d 标题", depth, i+1), &chapter.Title, false) {
				*dirty = true
			}
			nativeDangerButton(c, "移除章节").Disabled(a.Busy).OnClick(func() { *chapters = append((*chapters)[:i], (*chapters)[i+1:]...); *dirty = true })
			if depth < 6 {
				mgEditChapters(c, a, &chapter.Children, depth+1, dirty, total)
			}
		})
	}
	nativeButton(c, "添加第 "+strconv.Itoa(depth)+" 层章节").Disabled(a.Busy || len(*chapters) >= 100 || total >= 200).OnClick(func() { *chapters = append(*chapters, &managementChapter{}); *dirty = true })
}
func (p *resourcePage) View(c *ui.Context, a *NativeApp) {
	if p.closed {
		nativeText(c, "页面权限或会话已变化，请重新打开。")
		return
	}
	if p.editing && p.conflict == nil {
		ui.Column(c.Key("resource-editor")).FillWidth().Disabled(a.Busy).Children(func() { p.viewForm(c, a) })
		return
	}
	if p.id == "" {
		p.viewList(c, a)
		return
	}
	if p.detail == nil {
		nativeText(c, "正在读取精确修订；不可访问的记录不会显示缓存内容。")
		return
	}
	data := object(p.revision["data"])
	nativeHeading(c, str(data, "name"))
	mgValue(c, "显示修订", fmt.Sprintf("第 %d 版 · 当前第 %d 版", integer(p.revision, "revision"), integer(p.detail, "current_revision")))
	mgValue(c, "资源状态", mgStatus(str(object(p.detail["lifecycle"]), "state")))
	if p.kind == "features" {
		product := object(p.detail["product"])
		mgValue(c, "所属产品", str(product, "name"))
		mgValue(c, "所属产品状态", mgStatus(str(object(product["lifecycle"]), "state")))
	}
	for _, input := range p.inputs {
		if input.Secret {
			mgSecretRead(c.Key(input.Key), input.Label, str(data, input.Key), p.fields)
			continue
		}
		value := str(data, input.Key)
		if input.Key == "status" || input.Key == "kind" {
			value = mgStatus(value)
		}
		if input.Key == "project_types" {
			value = strings.Join(stringList(data[input.Key]), "、")
		}
		mgValue(c, input.Label, value)
	}
	if p.kind == "features" {
		nativeText(c, "实现状态与来源链接不会自动确认响应卡或将模拟材料变为真实证据。")
		for _, proof := range objects(data["real_screenshots"]) {
			mgValue(c, "真实截图来源", str(proof, "url"))
			mgValue(c, "截图状态", mgStatus(str(proof, "status")))
		}
	}
	if p.kind == "templates" {
		mgReadChapters(c, objects(data["chapters"]), 1)
		if p.original != nil {
			mgValue(c, "DOCX 原件", str(p.original, "name"))
			nativeButton(c, "下载并核验精确修订 DOCX").Disabled(a.Busy).OnClick(func() {
				rid := str(p.revision, "id")
				mgDownload(a, "/resources/templates/revisions/"+rid+"/download-link", "/resources/templates/revisions/"+rid+"/download", str(p.original, "name"), p.original, nil)
			})
		}
		nativeButton(c, "导出绑定 · 审核此精确 DOCX 修订").Disabled(a.Busy).OnClick(func() {
			path := fmt.Sprintf("/org/templates/%s/revisions/%s/bindings", p.id, str(p.revision, "id"))
			a.Navigate(mgRevisionLink(p.route, path, integer(p.revision, "revision")))
		})
	}
	if p.kind == "certificates" {
		p.viewCertificate(c, a)
	}
	ui.Row(c).Wrap().Gap(8).Children(func() {
		current := integer(p.revision, "revision") == integer(p.detail, "current_revision")
		nativeButton(c, "编辑新修订").Disabled(a.Busy || p.editing || !current || !mgAllowed(p.detail, "revise")).OnClick(func() { p.startEdit(a, false) })
		if p.kind == "certificates" {
			nativeButton(c, "上传证照原件为新修订").Disabled(a.Busy || p.editing || !current || !mgAllowed(p.detail, "revise")).OnClick(func() { p.startEdit(a, true) })
		}
		if p.route.Query.Get("task") != "" {
			nativeButton(c, "固定此精确修订到任务").Disabled(a.Busy || p.editing).OnClick(func() { p.pinning = true })
		}
	})
	if p.editing {
		ui.Column(c.Key("resource-editor")).Disabled(a.Busy).Children(func() { p.viewForm(c, a) })
	}
	if p.conflict != nil {
		ui.Column(c.Key("resource-conflict")).FillWidth().Children(func() {
			nativeHeading(c, "服务器当前修订 · 请与草稿核对")
			head := object(object(p.conflict["detail"])["revision"])
			mgValue(c, "当前修订", strconv.Itoa(integer(head, "revision")))
			for _, input := range p.inputs {
				value := str(object(head["data"]), input.Key)
				if input.Secret {
					mgSecretRead(c.Key("conflict-"+input.Key), input.Label, value, p.fields)
				} else {
					mgValue(c, input.Label, value)
				}
			}
			nativeButton(c, "已核对当前修订，继续保留草稿").Disabled(a.Busy).OnClick(func() {
				p.detail, p.conflict = p.conflict, nil
				p.revision = object(object(p.detail["detail"])["revision"])
				p.original = object(object(p.detail["detail"])["file"])
				if p.kind == "templates" {
					p.original = object(p.revision["file"])
				}
				p.preview = nil
				a.Notice = "已采用核对后的当前修订作为保存基准；草稿保留，请主动保存。"
			})
		})
	}
	if integer(p.revision, "revision") != integer(p.detail, "current_revision") {
		nativeText(c, "历史修订只读，不代表当前库内内容。")
		nativeButton(c, "查看当前修订").Disabled(a.Busy).OnClick(func() {
			a.Navigate(mgRevisionLink(p.route, "/org/"+p.kind+"/"+p.id, integer(p.detail, "current_revision")))
		})
	}
	if p.pinning {
		nativeHeading(c, "固定到任务")
		mgValue(c, "任务", p.route.Query.Get("task"))
		mgValue(c, "精确修订", strconv.Itoa(integer(p.revision, "revision")))
		nativeField(c, "标段（可留空）", &p.lot, false)
		ui.Row(c).Wrap().Gap(8).Children(func() {
			nativePrimaryButton(c, "确认固定此修订").Disabled(a.Busy).OnClick(func() { p.pin(a) })
			nativeButton(c, "取消固定").OnClick(func() { p.pinning = false; p.lot = "" })
		})
	}
	ui.Divider(c)
	nativeHeading(c, "资源生命周期")
	mgChoice(c, "停用原因", &p.reason, managementReasons)
	text := "停用"
	action := "deactivate"
	if str(object(p.detail["lifecycle"]), "state") == "inactive" {
		text = "恢复"
		action = "restore"
	}
	button := nativeButton
	if action == "deactivate" {
		button = nativeDangerButton
	}
	button(c, text+managementNames[p.kind]).Disabled(a.Busy || p.editing || !mgAllowed(p.detail, action)).OnClick(func() { p.lifecycle(a) })
	ui.Divider(c)
	nativeHeading(c, "修订历史 · 只读")
	nativeTable(c, []string{"修订", "名称", "时间", "当前状态"}, p.history.Rows, func(i int) {
		a.Navigate(mgRevisionLink(p.route, "/org/"+p.kind+"/"+p.id, integer(p.history.Items[i], "revision")))
	})
	p.history.controls(c, a, "修订", func(cursor string) { p.loadHistory(a, false, cursor) })
	nativeHeading(c, "生命周期历史")
	nativeTable(c, []string{"修订", "状态", "原因", "时间", "操作身份"}, p.lifeHistory.Rows, nil)
	p.lifeHistory.controls(c, a, "生命周期", func(cursor string) { p.loadHistory(a, true, cursor) })
	p.showReceipt(c)
}
func mgReadChapters(c *ui.Context, chapters []Object, depth int) {
	for _, chapter := range chapters {
		nativeText(c, strings.Repeat("　", depth-1)+str(chapter, "title"))
		if depth < 6 {
			mgReadChapters(c, objects(chapter["children"]), depth+1)
		}
	}
}
func (p *resourcePage) viewList(c *ui.Context, a *NativeApp) {
	name := managementNames[p.kind]
	query := func() { p.list.Previous = nil; p.loadList(a, "") }
	searchWidth := float32(420)
	if p.kind == "features" {
		searchWidth = 300
	}
	ui.Row(c).FillWidth().Gap(12).AlignItems(ui.Center).Children(func() {
		ui.Column(c).Grow(1).MinWidth(0).Gap(2).Children(func() {
			ui.Text(c, name+"列表").FontSize(15.5).FontWeight(600)
			nativeHint(c, "维护可复用资料；任务始终使用人工选定的精确修订。")
		})
		nativeIconButton(c, "plus", "新建"+name, true).Disabled(a.Busy || p.editing).OnClick(func() { p.startEdit(a, false) })
	})
	ui.Row(c).FillWidth().Wrap().Gap(10).AlignItems(ui.Center).Children(func() {
		nativeSearch(c, &p.search, "搜索"+name, "搜索"+name+"名称，按 Enter 查询", query).Grow(1).MinWidth(220).MaxWidth(searchWidth)
		nativeChoice(c, &p.state, []string{"可用", "已停用", "全部"}, map[string]string{"可用": "状态：可用", "已停用": "状态：已停用", "全部": "状态：全部"}).Label("资源状态").Width(150)
		if p.kind == "features" {
			nativeChoice(c, &p.status, []string{"全部", "已声明实现", "开发中", "计划中"}, map[string]string{"全部": "实现：全部", "已声明实现": "实现：已声明", "开发中": "实现：开发中", "计划中": "实现：计划中"}).Label("实现状态").Width(150)
			p.filterPicker.filter(c, a, &p.reads, "product-filter", &p.productFilter, query, func(error) { p.Close() })
		}
		nativeIconButton(c, "search", "查询"+name, false).Disabled(a.Busy).OnClick(query)
	})
	headers := []string{"名称", "修订", "生命周期", "来源声明", "最近修订"}
	nativeTable(c, headers, p.list.Rows, func(i int) {
		row := p.list.Items[i]
		a.Navigate(mgRevisionLink(p.route, "/org/"+p.kind+"/"+str(object(row["ref"]), "resource_id"), integer(row, "revision")))
	})
	p.list.controls(c, a, managementNames[p.kind], func(cursor string) { p.loadList(a, cursor) })
	p.showReceipt(c)
}
func (p *resourcePage) showReceipt(c *ui.Context) {
	if p.receipt == nil {
		return
	}
	nativeHeading(c, "本次操作回执")
	for _, field := range []struct{ Key, Label string }{{"id", "回执 ID"}, {"revision", "精确修订"}, {"task_id", "任务"}, {"replaced_snapshot_id", "被替换的固定记录"}, {"duplicate", "是否已有相同固定版本"}, {"existing_selections", "已有任务选择"}} {
		if value, ok := p.receipt[field.Key]; ok {
			mgValue(c, field.Label, fmt.Sprint(value))
		}
	}
}
func (p *resourcePage) viewCertificate(c *ui.Context, a *NativeApp) {
	nativeField(c, "按此日期查看有效期（YYYY-MM-DD）", &p.asOf, false)
	nativeButton(c, "查看日期提示").Disabled(a.Busy || p.asOf == "").OnClick(func() {
		number, asOf := integer(p.revision, "revision"), p.asOf
		path := fmt.Sprintf("%s/%s?revision=%d&as_of=%s", p.endpoint(), url.PathEscape(p.id), number, url.QueryEscape(asOf))
		p.reads.call(a, "date-advisory", "GET", path, nil, func(r Result) {
			if !p.validDetail(r.Data, strconv.Itoa(number)) || str(object(r.Data["date_advisory"]), "as_of") != asOf {
				a.Error = "日期提示修订范围不符合契约"
				return
			}
			if integer(p.revision, "revision") != number || p.asOf != asOf {
				return
			}
			p.dateAdvice = managementDateLabels[str(object(r.Data["date_advisory"]), "state")]
		}, func(err error) {
			if mgDenied(err) {
				p.Close()
			}
		})
	})
	if p.dateAdvice != "" {
		nativeText(c, p.dateAdvice+"。提示不会自动确认资格或证据。")
	}
	if p.original == nil {
		nativeText(c, "此修订未保存证照原件，不能用元数据代替资格证据。")
		return
	}
	file := object(p.original["file"])
	mgValue(c, "原件", str(file, "name"))
	pages := integer(file, "page_count")
	ui.Row(c).Wrap().Gap(8).Children(func() {
		nativeButton(c, "上一原件页").Disabled(a.Busy || p.previewPage <= 1).OnClick(func() { p.previewPage--; p.previewCertificate(a) })
		nativeButton(c, "下一原件页").Disabled(a.Busy || pages == 0 || p.previewPage >= pages).OnClick(func() { p.previewPage++; p.previewCertificate(a) })
		nativeText(c, fmt.Sprintf("第 %d / %d 页 · %d%%", p.previewPage, pages, p.previewZoom))
		nativeButton(c, "缩小原件").Disabled(a.Busy || p.previewZoom <= 50).OnClick(func() { p.previewZoom -= 25; p.previewCertificate(a) })
		nativeButton(c, "放大原件").Disabled(a.Busy || p.previewZoom >= 200).OnClick(func() { p.previewZoom += 25; p.previewCertificate(a) })
		nativeButton(c, "读取原件预览").Disabled(a.Busy).OnClick(func() { p.previewCertificate(a) })
	})
	if p.preview != nil {
		ui.Image(c, p.preview).Width(820 * float32(p.previewZoom) / 100).Fit(ui.Contain).Label("证照原件第 " + strconv.Itoa(p.previewPage) + " 页")
	}
	nativeButton(c, "下载并核验精确修订原件").Disabled(a.Busy).OnClick(func() {
		rid := str(p.revision, "id")
		mgDownload(a, "/resources/certificates/revisions/"+rid+"/file/download-link", "/resources/certificates/revisions/"+rid+"/file/download", str(file, "name"), file, nil)
	})
}

var managementDateLabels = map[string]string{
	"unknown": "日期不足，无法判断有效期", "expired": "按所填日期已过期",
	"not_yet_valid": "按所填日期尚未生效", "valid": "按所填日期处于声明有效期内",
}

type qualificationHub struct {
	managementState
	profiles, certificates *resourcePage
	selected               int
}

func newQualificationHub(route Route) *qualificationHub {
	return &qualificationHub{managementState: managementState{route: route, title: "单位资料与证照"}, profiles: newResourcePage(route, "profiles"), certificates: newResourcePage(route, "certificates")}
}
func (p *qualificationHub) Open(a *NativeApp) { p.profiles.Open(a); p.certificates.Open(a) }
func (p *qualificationHub) Dirty() bool       { return p.profiles.Dirty() || p.certificates.Dirty() }
func (p *qualificationHub) Close() {
	p.managementState.Close()
	p.profiles.Close()
	p.certificates.Close()
}
func (p *qualificationHub) View(c *ui.Context, a *NativeApp) {
	ui.Segmented(c, &p.selected, "单位资料", "证照与人员证书").Label("资料类别").AlignSelf(ui.Start).Disabled(a.Busy || p.Dirty())
	if p.selected == 0 {
		ui.Column(c.Key("profiles")).FillWidth().Gap(14).Children(func() { p.profiles.View(c, a) })
	} else {
		ui.Column(c.Key("certificates")).FillWidth().Gap(14).Children(func() { p.certificates.View(c, a) })
	}
}
