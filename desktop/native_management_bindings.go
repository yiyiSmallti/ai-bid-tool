package main

import (
	"fmt"
	"math"
	"net/url"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/egoist/mygo/ui"
)

var managementBindingSections = []string{"substantive", "commercial", "technical", "comply_only", "gaps", "evidence_appendix"}
var managementBindingLabels = []string{"实质性响应", "商务响应", "技术响应", "仅符合项", "缺口", "证据附录"}
var managementBindingColumns = []string{"ordinal", "requirement", "response", "compliance"}
var managementBindingColumnLabels = []string{"序号", "招标要求", "投标响应", "偏离情况"}

type managementBindingSection struct {
	Heading, Table string
	Widths         [4]string
}

type bindingPage struct {
	managementState
	reads                                          managementReads
	id, revisionID                                 string
	template, revision, selected, preview, receipt Object
	list                                           managementListing
	sections                                       [6]managementBindingSection
	inspected, reviewed                            bool
	generation                                     uint64
}

func newBindingPage(route Route) *bindingPage {
	parts := strings.Split(strings.Trim(route.Path, "/"), "/")
	p := &bindingPage{managementState: managementState{route: route, title: "精确 DOCX 修订的导出绑定"}, id: parts[2], revisionID: parts[4]}
	p.resetSections()
	return p
}

func (p *bindingPage) resetSections() {
	p.sections = [6]managementBindingSection{}
	for i := 0; i < 3; i++ {
		p.sections[i].Widths = [4]string{"25", "25", "25", "25"}
	}
}
func (p *bindingPage) invalidate() {
	p.generation++
	p.preview, p.receipt = nil, nil
	p.reviewed, p.inspected = false, false
	p.dirty = true
}
func (p *bindingPage) Close() {
	p.managementState.Close()
	p.reads.close()
	p.generation++
	p.template, p.revision, p.selected, p.preview, p.receipt = nil, nil, nil, nil, nil
	p.inspected, p.reviewed = false, false
	p.list = managementListing{}
	p.resetSections()
}
func (p *bindingPage) fail(err error) {
	if mgDenied(err) {
		p.Close()
	}
}
func (p *bindingPage) Open(a *NativeApp) {
	path := "/management/resources/templates/" + url.PathEscape(p.id) + "?revision_id=" + url.QueryEscape(p.revisionID)
	p.reads.call(a, "template", "GET", path, nil, func(r Result) {
		detail := object(r.Data["detail"])
		revision := object(detail["revision"])
		if str(object(r.Data["ref"]), "kind") != "templates" || str(object(r.Data["ref"]), "resource_id") != p.id || str(revision, "template_id") != p.id || str(revision, "id") != p.revisionID || integer(revision, "revision") < 1 {
			a.Error = "模板绑定精确修订范围不符合契约"
			return
		}
		if expected := p.route.Query.Get("revision"); expected != "" && expected != strconv.Itoa(integer(revision, "revision")) {
			a.Error = "绑定地址中的修订号与原件不一致"
			return
		}
		file := object(revision["file"])
		if len(str(file, "sha256")) != 64 || integer(file, "size_bytes") < 1 || str(file, "media_type") != "application/vnd.openxmlformats-officedocument.wordprocessingml.document" {
			a.Error = "精确 DOCX 原件元数据不完整"
			return
		}
		p.template, p.revision = r.Data, revision
		p.load(a, "")
	}, p.fail)
}
func (p *bindingPage) validBinding(value Object, id string) bool {
	if str(value, "id") == "" || id != "" && str(value, "id") != id || str(value, "template_revision_id") != p.revisionID || str(value, "template_sha256") != str(object(p.revision["file"]), "sha256") || len(str(value, "binding_hash")) != 64 || len(str(value, "static_content_hash")) != 64 {
		return false
	}
	sections := objects(value["sections"])
	if len(sections) != 6 {
		return false
	}
	for index, section := range sections {
		if str(section, "section") != managementBindingSections[index] {
			return false
		}
	}
	return true
}
func (p *bindingPage) load(a *NativeApp, cursor string) {
	p.selected = nil
	p.list.Items, p.list.Rows, p.list.Meta = nil, nil, nil
	p.reads.call(a, "binding-list", "POST", "/management/export-bindings/query", Object{"template_revision_id": p.revisionID, "cursor": mgNull(cursor), "limit": 25}, func(r Result) {
		for _, row := range r.Items {
			if !p.validBinding(row, "") {
				a.Error = "绑定列表不属于当前 DOCX 精确修订"
				return
			}
		}
		if !p.list.install(r, cursor, func(row Object) []string {
			applicability := "旧版绑定不能用于导出"
			if mgBool(row["current"]) {
				applicability = "当前四列表格绑定"
			}
			return []string{str(row, "id"), applicability, str(row, "reviewed_at")}
		}) {
			a.Error = "绑定分页范围不符合契约"
		}
	}, p.fail)
}
func (p *bindingPage) show(a *NativeApp, row Object) {
	id := str(row, "id")
	p.selected = nil
	p.reads.call(a, "binding-detail", "GET", "/management/export-bindings/"+url.PathEscape(id)+"?template_revision_id="+url.QueryEscape(p.revisionID), nil, func(r Result) {
		if !p.validBinding(r.Data, id) {
			a.Error = "绑定详情不属于所选精确修订"
			return
		}
		p.selected = r.Data
	}, p.fail)
}
func (p *bindingPage) input() ([]Object, error) {
	sections := make([]Object, 6)
	for i, section := range p.sections {
		if strings.TrimSpace(section.Heading) == "" || strings.TrimSpace(section.Table) == "" || utf8.RuneCountInString(section.Heading) > 200 || utf8.RuneCountInString(section.Table) > 200 {
			return nil, fmt.Errorf("每节需填写原件中真实存在的标题和表格样式 ID（最多 200 字）")
		}
		columns := make([]Object, 0, 4)
		if i < 3 {
			total := 0.0
			for j, key := range managementBindingColumns {
				width, err := strconv.ParseFloat(section.Widths[j], 64)
				if err != nil || math.IsNaN(width) || math.IsInf(width, 0) || width <= 0 || width > 100 {
					return nil, fmt.Errorf("前三节的每列宽度须大于 0 且不超过 100%%")
				}
				total += width
				columns = append(columns, Object{"key": key, "width_percent": width})
			}
			if math.Abs(total-100) > 1e-8 {
				return nil, fmt.Errorf("前三节的四列宽度合计必须为 100%%")
			}
		}
		sections[i] = Object{"section": managementBindingSections[i], "heading_style_id": section.Heading, "table_style_id": section.Table, "columns": columns}
	}
	return sections, nil
}
func (p *bindingPage) previewBinding(a *NativeApp) {
	sections, err := p.input()
	if err != nil {
		a.Error = err.Error()
		return
	}
	p.invalidate()
	generation := p.generation
	hash := str(object(p.revision["file"]), "sha256")
	body := Object{"template_revision_id": p.revisionID, "expected_template_sha256": hash, "sections": sections, "dry_run": true}
	p.reads.call(a, "binding-preview", "POST", "/export-template-bindings", body, func(r Result) {
		if generation != p.generation {
			return
		}
		value := r.Data
		if !mgBool(value["dry_run"]) || str(value, "template_revision_id") != p.revisionID || str(value, "template_sha256") != hash || len(str(value, "static_content_hash")) != 64 || len(objects(value["anchors"])) > 6 || len(objects(value["issues"])) > 100 {
			a.Error = "绑定预览范围不符合契约"
			return
		}
		previous := -1
		for _, anchor := range objects(value["anchors"]) {
			index := -1
			for i, key := range managementBindingSections {
				if str(anchor, "section") == key {
					index = i
				}
			}
			if index <= previous || integer(anchor, "paragraph") < 1 || integer(anchor, "section_index") < 0 {
				a.Error = "绑定锚点顺序或位置不符合契约"
				return
			}
			previous = index
		}
		for _, issue := range objects(value["issues"]) {
			severity := str(issue, "severity")
			if severity != "block" && severity != "acknowledge" {
				a.Error = "绑定警示范围不符合契约"
				return
			}
		}
		p.preview = value
	}, p.fail)
}
func (p *bindingPage) canCreate() bool {
	if p.closed || p.preview == nil || !p.inspected || !p.reviewed || len(objects(p.preview["anchors"])) != 6 {
		return false
	}
	for _, issue := range objects(p.preview["issues"]) {
		if str(issue, "severity") == "block" {
			return false
		}
	}
	return true
}
func (p *bindingPage) original(a *NativeApp) {
	generation := p.generation
	p.inspected, p.reviewed = false, false
	file := object(p.revision["file"])
	mgDownload(a, "/resources/templates/revisions/"+url.PathEscape(p.revisionID)+"/download-link", "/resources/templates/revisions/"+url.PathEscape(p.revisionID)+"/download", str(file, "name"), file, func() {
		if !p.closed && p.generation == generation && str(p.revision, "id") == p.revisionID {
			p.inspected = true
		}
	})
}
func (p *bindingPage) create(a *NativeApp) {
	if !p.canCreate() {
		return
	}
	sections, err := p.input()
	if err != nil {
		a.Error = err.Error()
		return
	}
	generation := p.generation
	preview := p.preview
	body := Object{"template_revision_id": p.revisionID, "expected_template_sha256": preview["template_sha256"], "expected_static_content_hash": preview["static_content_hash"], "sections": sections, "dry_run": false}
	mgCall(a, "POST", "/export-template-bindings", body, func(r Result) {
		if generation != p.generation {
			return
		}
		if !p.validBinding(r.Data, "") || str(r.Data, "static_content_hash") != str(preview, "static_content_hash") {
			p.preview = nil
			p.inspected, p.reviewed = false, false
			a.Error = "绑定回执的静态内容范围不符合契约"
			return
		}
		p.receipt = r.Data
		p.preview = nil
		p.inspected, p.reviewed, p.dirty = false, false, false
		p.resetSections()
		p.list.Previous = nil
		p.load(a, "")
	}, func(err error) {
		p.preview = nil
		p.inspected, p.reviewed = false, false
		p.fail(err)
		a.Error = "创建结果尚未确定或原件哈希已变化。请主动重新读取绑定记录并核对；不会自动重试。"
	})
}
func (p *bindingPage) View(c *ui.Context, a *NativeApp) {
	if p.closed {
		nativeText(c, "模板权限或会话已变化，请重新打开绑定。")
		return
	}
	if p.revision == nil {
		nativeText(c, "正在读取精确 DOCX 修订。")
		return
	}
	nativeButton(c, "返回模板精确修订详情").OnClick(func() { a.Navigate(mgRevisionLink(p.route, "/org/templates/"+p.id, integer(p.revision, "revision"))) })
	nativeText(c, "绑定只适用于此精确 DOCX 原件。新内容修订需要独立审阅；绑定不会确认材料或批准任务导出。")
	mgValue(c, "模板", str(object(p.revision["data"]), "name"))
	mgValue(c, "原件修订", fmt.Sprintf("第 %d 版 · %s", integer(p.revision, "revision"), p.revisionID))
	mgValue(c, "DOCX 原件", str(object(p.revision["file"]), "name"))
	nativeHeading(c, "已有不可变绑定与审阅记录")
	nativeTable(c, []string{"绑定", "适用性", "审阅时间"}, p.list.Rows, func(i int) { p.show(a, p.list.Items[i]) })
	p.list.controls(c, a, "绑定", func(cursor string) { p.load(a, cursor) })
	if p.selected != nil {
		p.viewBinding(c, p.selected)
	}
	ui.Divider(c)
	nativeHeading(c, "新建六节绑定")
	nativeText(c, "原件需包含以下顺序的六个独立锚点段落；声明章节需对应实际标题。前三节固定四列且宽度合计 100%，其余节不配置列。")
	ui.Column(c.Key("binding-form")).FillWidth().Height(430).Disabled(a.Busy).Gap(12).Children(func() {
		ui.Scroll(c.Key("binding-sections")).Grow(1).FillWidth().Gap(18).Padding(0, 12, 12, 0).Children(func() {
			for i := range p.sections {
				section := &p.sections[i]
				ui.Column(c.Key(managementBindingSections[i])).Gap(6).Children(func() {
					nativeHeading(c, managementBindingLabels[i])
					mgValue(c, "原件独立段落锚点", "{{bid."+managementBindingSections[i]+"}}")
					if nativeField(c, "标题样式 ID", &section.Heading, false) {
						p.invalidate()
					}
					if nativeField(c, "表格样式 ID", &section.Table, false) {
						p.invalidate()
					}
					if i < 3 {
						for j := range managementBindingColumns {
							if nativeField(c, managementBindingColumnLabels[j]+"列宽（%）", &section.Widths[j], false) {
								p.invalidate()
							}
						}
					}
				})
			}
		})
		nativeActions(c).Children(func() {
			nativePrimaryButton(c, "只读预览绑定与静态内容").OnClick(func() { p.previewBinding(a) })
		})
	})
	if p.preview != nil {
		nativeHeading(c, "核对预览与原件静态内容")
		mgValue(c, "模板 SHA-256", str(p.preview, "template_sha256"))
		mgValue(c, "静态内容哈希", str(p.preview, "static_content_hash"))
		mgValue(c, "适配器版本", str(p.preview, "adapter_version"))
		for _, anchor := range objects(p.preview["anchors"]) {
			mgValue(c, str(anchor, "section"), fmt.Sprintf("原件段落 %d · 文档节 %d", integer(anchor, "paragraph"), integer(anchor, "section_index")))
		}
		for _, issue := range objects(p.preview["issues"]) {
			nativeText(c, mgStatus(str(issue, "severity"))+" · "+str(issue, "code"))
		}
		nativeText(c, "下载并打开本修订原件，核对正文、页眉页脚、已有表格及其他静态内容。修改配置后必须重新预览、读取原件并审阅。")
		nativeButton(c, "下载并核验本精确修订原件").Disabled(a.Busy).OnClick(func() { p.original(a) })
		ui.Checkbox(c, &p.reviewed, "我已打开并核对原件中全部静态内容、样式与六节锚点").Disabled(a.Busy || !p.inspected)
		nativePrimaryButton(c, "创建此精确修订的不可变绑定").Disabled(a.Busy || !p.canCreate()).OnClick(func() { p.create(a) })
	}
	if p.receipt != nil {
		nativeHeading(c, "绑定创建回执")
		p.viewBinding(c, p.receipt)
	}
}
func (p *bindingPage) viewBinding(c *ui.Context, value Object) {
	mgValue(c, "绑定 ID", str(value, "id"))
	mgValue(c, "绑定哈希", str(value, "binding_hash"))
	mgValue(c, "静态内容哈希", str(value, "static_content_hash"))
	mgValue(c, "审阅时间", str(value, "reviewed_at"))
	for _, section := range objects(value["sections"]) {
		mgValue(c, str(section, "section")+" 标题样式", str(section, "heading_style_id"))
		mgValue(c, str(section, "section")+" 表格样式", str(section, "table_style_id"))
		for _, column := range objects(section["columns"]) {
			mgValue(c, str(column, "key")+" 列宽", fmt.Sprint(column["width_percent"])+"%")
		}
	}
}
