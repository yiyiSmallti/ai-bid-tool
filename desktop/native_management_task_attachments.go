package main

import (
	"fmt"
	"net/url"
	"strconv"
	"strings"

	"github.com/egoist/mygo/ui"
)

type taskAttachmentPage struct {
	managementState
	reads                                                                              managementReads
	task, requestID                                                                    string
	profiles, links, selections, sources                                               managementListing
	selectedProfile, selectedLink, selectedAttachment, selectedSource, source, privacy Object
	extractions                                                                        []Object
	extractionOffset                                                                   int
	extraction, expected, pageText                                                     string
	includeHistory, reviewed                                                           bool
	image                                                                              *ui.Bitmap
	imageGeneration                                                                    uint64
	receipt                                                                            string
}

func newTaskAttachmentPage(route Route) *taskAttachmentPage {
	parts := strings.Split(strings.Trim(route.Path, "/"), "/")
	return &taskAttachmentPage{managementState: managementState{route: route, title: "任务附件与逐页隐私"}, task: parts[2], requestID: requestID(), pageText: "1"}
}
func (p *taskAttachmentPage) Open(a *NativeApp) { p.load(a) }
func (p *taskAttachmentPage) Close() {
	p.managementState.Close()
	p.reads.close()
	p.profiles, p.links, p.selections, p.sources = managementListing{}, managementListing{}, managementListing{}, managementListing{}
	p.selectedProfile, p.selectedLink, p.selectedAttachment, p.selectedSource, p.source, p.privacy = nil, nil, nil, nil, nil, nil
	p.extractions = nil
	p.extraction, p.expected, p.receipt = "", "", ""
	p.image = nil
	p.reviewed = false
	p.imageGeneration++
}
func (p *taskAttachmentPage) fail(err error) {
	p.reviewed = false
	if mgDenied(err) {
		p.Close()
	}
}
func (p *taskAttachmentPage) load(a *NativeApp) {
	p.loadSelections(a, "")
	p.loadSources(a, "")
	p.loadProfiles(a, "")
	p.loadExtractions(a)
}
func (p *taskAttachmentPage) query(cursor string, history bool) string {
	query := url.Values{"limit": {"25"}, "history": {strconv.FormatBool(history)}}
	if cursor != "" {
		query.Set("cursor", cursor)
	}
	return query.Encode()
}
func (p *taskAttachmentPage) validTaskRows(a *NativeApp, rows []Object) bool {
	for _, row := range rows {
		if str(row, "task_id") != p.task || str(row, "id") == "" {
			a.Error = "附件响应不属于当前任务"
			return false
		}
	}
	return true
}
func (p *taskAttachmentPage) loadProfiles(a *NativeApp, cursor string) {
	p.reads.call(a, "task-profiles", "GET", "/tasks/"+url.PathEscape(p.task)+"/profiles?"+p.query(cursor, false), nil, func(r Result) {
		if !p.validTaskRows(a, r.Items) {
			return
		}
		if !p.profiles.install(r, cursor, func(row Object) []string {
			return []string{str(object(row["data"]), "name"), strconv.Itoa(integer(row, "revision")), str(row, "lot"), str(row, "id")}
		}) {
			a.Error = "任务资料分页范围不符合契约"
		}
	}, p.fail)
}
func (p *taskAttachmentPage) loadLinks(a *NativeApp, cursor string) {
	if p.selectedProfile == nil {
		return
	}
	profileID, rid := str(p.selectedProfile, "id"), str(p.selectedProfile, "profile_revision_id")
	p.selectedLink = nil
	p.links.Items, p.links.Rows, p.links.Meta = nil, nil, nil
	path := "/resources/profiles/revisions/" + url.PathEscape(rid) + "/attachments?limit=25"
	if cursor != "" {
		path += "&cursor=" + url.QueryEscape(cursor)
	}
	p.reads.call(a, "task-declaration-links", "GET", path, nil, func(r Result) {
		if str(p.selectedProfile, "id") != profileID {
			return
		}
		for _, row := range r.Items {
			if str(row, "profile_revision_id") != rid {
				a.Error = "声明附件关联不属于所选资料修订"
				return
			}
		}
		if !p.links.install(r, cursor, func(row Object) []string {
			state := "已停用"
			if mgBool(row["active"]) {
				state = "可用"
			}
			return []string{managementDeclarationLabels[str(row, "field")], str(row, "attachment_revision_id"), state, str(row, "id")}
		}) {
			a.Error = "声明关联分页范围不符合契约"
		}
	}, p.fail)
}
func (p *taskAttachmentPage) loadSelections(a *NativeApp, cursor string) {
	p.selectedAttachment = nil
	p.reads.call(a, "task-attachments", "GET", "/tasks/"+url.PathEscape(p.task)+"/attachments?"+p.query(cursor, p.includeHistory), nil, func(r Result) {
		if !p.validTaskRows(a, r.Items) {
			return
		}
		if !p.selections.install(r, cursor, func(row Object) []string {
			state := "已停用"
			if mgBool(row["active"]) {
				state = "可用"
			}
			updated := ""
			if mgBool(row["library_updated"]) {
				updated = fmt.Sprintf("库当前第 %d 版；任务仍固定原版本", integer(row, "current_library_revision"))
			}
			return []string{str(row, "attachment_revision_id"), managementDeclarationLabels[str(row, "field")], str(row, "lot"), state, updated, mgAttachmentReadiness(object(row["readiness"]))}
		}) {
			a.Error = "任务附件分页范围不符合契约"
		}
	}, p.fail)
}
func (p *taskAttachmentPage) loadSources(a *NativeApp, cursor string) {
	p.selectedSource = nil
	p.reads.call(a, "task-attachment-sources", "GET", "/tasks/"+url.PathEscape(p.task)+"/attachment-sources?"+p.query(cursor, p.includeHistory), nil, func(r Result) {
		if !p.validTaskRows(a, r.Items) {
			return
		}
		if !p.sources.install(r, cursor, func(row Object) []string {
			return []string{strconv.Itoa(integer(row, "page")), str(row, "attachment_revision_id"), str(row, "source_png_sha256"), mgAttachmentReadiness(object(row["readiness"]))}
		}) {
			a.Error = "归档来源分页范围不符合契约"
		}
	}, p.fail)
}
func (p *taskAttachmentPage) loadExtractions(a *NativeApp) {
	p.reads.call(a, "privacy-extractions", "GET", "/tasks/"+url.PathEscape(p.task)+"/extractions", nil, func(r Result) {
		p.extractions = nil
		p.extractionOffset = 0
		for _, row := range r.Items {
			if task := str(row, "task_id"); task != "" && task != p.task {
				a.Error = "抽取作业不属于当前任务"
				p.extractions = nil
				return
			}
			if str(row, "status") == "succeeded" {
				p.extractions = append(p.extractions, row)
			}
		}
	}, p.fail)
}
func (p *taskAttachmentPage) selectAttachment(a *NativeApp) {
	if p.selectedProfile == nil || p.selectedLink == nil || !mgBool(p.selectedLink["active"]) {
		return
	}
	profileID, linkID := str(p.selectedProfile, "id"), str(p.selectedLink, "id")
	body := Object{"request_id": p.requestID, "task_org_profile_id": profileID, "profile_attachment_link_id": linkID, "expected_selection_id": mgNull(p.expected)}
	mgCall(a, "POST", "/tasks/"+url.PathEscape(p.task)+"/attachments", body, func(r Result) {
		if str(r.Data, "task_id") != p.task || str(r.Data, "task_org_profile_id") != profileID || str(r.Data, "profile_attachment_link_id") != linkID {
			a.Error = "任务固定附件回执范围不符合契约"
			return
		}
		p.requestID = requestID()
		p.receipt = "任务已固定原件修订 " + str(r.Data, "attachment_revision_id") + " · 选择 " + str(r.Data, "id")
		p.expected = ""
		p.loadSelections(a, "")
	}, p.fail)
}
func (p *taskAttachmentPage) deactivate(a *NativeApp, row Object) {
	id := str(row, "id")
	mgCall(a, "POST", "/task-attachments/"+url.PathEscape(id)+"/deactivate", Object{"request_id": p.requestID, "expected_state_version": row["state_version"], "reason": "selection_replaced"}, func(r Result) {
		if str(r.Data, "task_id") != p.task || str(r.Data, "id") != id {
			a.Error = "停用任务附件回执范围不符合契约"
			return
		}
		p.requestID = requestID()
		p.image, p.source, p.privacy = nil, nil, nil
		p.reviewed = false
		p.imageGeneration++
		p.loadSelections(a, "")
		p.loadSources(a, "")
	}, p.fail)
}
func (p *taskAttachmentPage) archive(a *NativeApp, row Object) {
	page, err := strconv.Atoi(p.pageText)
	if err != nil || page < 1 || page > 200 {
		a.Error = "归档页码须在 1 至 200 之间，且属于此原件"
		return
	}
	selectionID := str(row, "id")
	mgCall(a, "POST", "/tasks/"+url.PathEscape(p.task)+"/attachment-sources", Object{"request_id": p.requestID, "task_attachment_id": selectionID, "page": page}, func(r Result) {
		source := object(r.Data["source"])
		if str(source, "task_id") != p.task || str(source, "task_attachment_id") != selectionID || integer(source, "page") != page || str(source, "status") != "unconfirmed_source" {
			a.Error = "归档回执范围不符合契约"
			return
		}
		p.requestID = requestID()
		p.receipt = fmt.Sprintf("已归档第 %d 页，固定 150 dpi；仍未隐私放行或确认。", page)
		p.loadSources(a, "")
	}, p.fail)
}
func (p *taskAttachmentPage) inspect(a *NativeApp, row Object) {
	id := str(row, "id")
	p.image, p.source, p.privacy = nil, nil, nil
	p.reviewed = false
	p.imageGeneration++
	generation := p.imageGeneration
	p.reads.call(a, "attachment-source-detail", "GET", "/attachment-sources/"+url.PathEscape(id), nil, func(r Result) {
		if generation != p.imageGeneration {
			return
		}
		if str(r.Data, "task_id") != p.task || str(r.Data, "id") != id || str(r.Data, "source_png_sha256") != str(object(r.Data["preview"]), "sha256") {
			a.Error = "原页详情范围或 PNG 哈希不符合契约"
			return
		}
		p.source = r.Data
		metadata := mgCopy(object(r.Data["preview"]))
		metadata["media_type"] = "image/png"
		link := "/attachment-sources/" + url.PathEscape(id) + "/preview/download-link"
		if p.includeHistory {
			link += "?history=true"
		}
		p.reads.image(a, "task-source-png", link, "/attachment-sources/"+url.PathEscape(id)+"/preview/download", metadata, func(bitmap *ui.Bitmap) {
			if generation == p.imageGeneration && str(p.source, "id") == id {
				p.image = bitmap
			}
		}, p.fail)
	}, p.fail)
}
func (p *taskAttachmentPage) privacyReview(a *NativeApp, mode string) {
	if p.source == nil || p.image == nil || !p.reviewed {
		return
	}
	if mode == "clear" && (p.extraction == "" || p.source["latest_privacy_hold_id"] != nil) {
		return
	}
	id, hash := str(p.source, "id"), str(p.source, "source_png_sha256")
	body := Object{"mode": mode, "request_id": p.requestID, "reviewed_source_png_sha256": hash, "expected_hold_id": p.source["latest_privacy_hold_id"]}
	if mode == "clear" {
		body["clearance"] = "safe_for_task_team"
		body["extraction_job_id"] = p.extraction
	} else {
		body["reason"] = "sensitive_content"
	}
	p.reviewed = false
	mgCall(a, "POST", "/attachment-sources/"+url.PathEscape(id)+"/privacy", body, func(r Result) {
		if str(r.Data, "task_id") != p.task || str(r.Data, "evidence_source_id") != id || str(r.Data, "source_png_sha256") != hash || r.Data["confirmed_at"] != nil || mgBool(r.Data["eligible_for_draft_export"]) {
			a.Error = "隐私回执范围不符合契约"
			return
		}
		p.requestID = requestID()
		p.privacy = r.Data
		if mode == "clear" {
			p.receipt = "此精确页已隐私放行，仍未确认材料或响应证据。"
		} else {
			p.receipt = "已记录此页含敏感内容；遮挡尚未启用，不会放行原始像素。"
		}
		p.reads.call(a, "attachment-source-detail", "GET", "/attachment-sources/"+url.PathEscape(id), nil, func(next Result) {
			if str(next.Data, "task_id") != p.task || str(next.Data, "id") != id {
				a.Error = "隐私决定后来源范围不符合契约"
				return
			}
			if str(p.source, "id") == id {
				p.source = next.Data
			}
		}, p.fail)
		p.loadSources(a, "")
	}, p.fail)
}
func (p *taskAttachmentPage) teamView(a *NativeApp, row Object) {
	id := str(row, "id")
	p.reads.call(a, "team-source-detail", "GET", "/attachment-sources/"+url.PathEscape(id), nil, func(r Result) {
		if str(r.Data, "task_id") != p.task || str(r.Data, "id") != id || str(r.Data, "cleared_rendition_id") == "" || str(r.Data, "cleared_asset_id") == "" {
			a.Error = "此精确页没有当前有效的隐私放行图"
			return
		}
		p.openRendition(a, Object{"id": r.Data["cleared_rendition_id"], "asset_id": r.Data["cleared_asset_id"]})
	}, p.fail)
}
func (p *taskAttachmentPage) openRendition(a *NativeApp, rendition Object) {
	id := str(rendition, "id")
	p.imageGeneration++
	generation := p.imageGeneration
	p.image = nil
	p.reviewed = false
	metadata := mgCopy(object(rendition["image"]))
	metadata["media_type"] = "image/png"
	p.reads.imageMethod(a, "team-rendition", "POST", "/screenshot-renditions/"+url.PathEscape(id)+"/preview-link", "/screenshot-renditions/"+url.PathEscape(id)+"/content", metadata, func(bitmap *ui.Bitmap) {
		if generation != p.imageGeneration {
			return
		}
		p.image, p.source = bitmap, nil
	}, p.fail)
}
func (p *taskAttachmentPage) View(c *ui.Context, a *NativeApp) {
	if p.closed {
		nativeText(c, "任务附件权限或会话已变化，请重新打开。")
		return
	}

	nativeHeading(c, "选择精确声明关联")
	nativeTable(c, []string{"任务固定资料", "修订", "标段", "任务资料选择"}, p.profiles.Rows, func(i int) {
		p.selectedProfile = p.profiles.Items[i]
		p.links = managementListing{}
		p.loadLinks(a, "")
	})
	p.profiles.controls(c, a, "任务资料", func(cursor string) { p.loadProfiles(a, cursor) })
	if p.selectedProfile != nil {
		mgValue(c, "所选任务资料", str(p.selectedProfile, "id"))
		nativeTable(c, []string{"声明字段", "精确原件修订", "状态", "声明关联"}, p.links.Rows, func(i int) {
			if mgBool(p.links.Items[i]["active"]) {
				p.selectedLink = p.links.Items[i]
			}
		})
		p.links.controls(c, a, "任务声明关联", func(cursor string) { p.loadLinks(a, cursor) })
		if p.selectedLink != nil {
			mgValue(c, "所选声明关联", str(p.selectedLink, "id"))
		}
		nativeField(c, "被替换的任务附件选择 ID（空值仅创建空位）", &p.expected, false)
		nativePrimaryButton(c, "固定此关联到任务").Disabled(a.Busy || p.selectedLink == nil).OnClick(func() { p.selectAttachment(a) })
	}
	ui.Divider(c)
	nativeHeading(c, "任务固定附件")
	if ui.Checkbox(c, &p.includeHistory, "包含失效历史选择与页").Changed() {
		p.load(a)
	}
	nativeButton(c, "重新读取任务附件").Disabled(a.Busy).OnClick(func() { p.load(a) })
	nativeTable(c, []string{"精确原件修订", "声明", "标段", "选择状态", "库内更新", "下一步"}, p.selections.Rows, func(i int) { p.selectedAttachment = p.selections.Items[i] })
	p.selections.controls(c, a, "任务附件", func(cursor string) { p.loadSelections(a, cursor) })
	nativeField(c, "归档页码", &p.pageText, false)
	if p.selectedAttachment != nil {
		row := p.selectedAttachment
		mgValue(c, "所选附件选择 ID", str(row, "id"))
		nativeButton(c, "查看所选原件精确修订").OnClick(func() {
			a.Navigate("/org/attachments/" + str(row, "attachment_id") + "?revision=" + url.QueryEscape(str(row, "attachment_revision_id")) + "&task=" + url.QueryEscape(p.task))
		})
		ui.Row(c).Wrap().Gap(8).Children(func() {
			nativeButton(c, "归档所选原件指定页").Disabled(a.Busy || !mgBool(row["active"])).OnClick(func() { p.archive(a, row) })
			nativeDangerButton(c, "停用所选任务附件").Disabled(a.Busy || !mgBool(row["active"])).OnClick(func() { p.deactivate(a, row) })
			nativeButton(c, "以此选择作为替换基准").Disabled(a.Busy).OnClick(func() { p.expected = str(row, "id") })
		})
	}
	nativeHeading(c, "归档原件页与逐页隐私")
	nativeTable(c, []string{"原件页", "精确原件修订", "原始 PNG 哈希", "下一步"}, p.sources.Rows, func(i int) { p.selectedSource = p.sources.Items[i] })
	p.sources.controls(c, a, "归档来源", func(cursor string) { p.loadSources(a, cursor) })
	if p.selectedSource != nil {
		nativeButton(c, "核对所选原页隐私").Disabled(a.Busy).OnClick(func() { p.inspect(a, p.selectedSource) })
		nativeButton(c, "查看当前已放行团队图").Disabled(a.Busy || str(p.selectedSource, "cleared_rendition_id") == "").OnClick(func() { p.teamView(a, p.selectedSource) })
		nativeButton(c, "附件页标注尚未启用").Disabled(true)
	}
	if p.image != nil {
		label := "隐私放行团队图"
		if p.source != nil {
			label = "限制级原始附件页"
		}
		ui.Image(c, p.image).Width(820).Fit(ui.Contain).Label(label)
	}
	if p.source != nil {
		mgValue(c, "原始 PNG SHA-256", str(p.source, "source_png_sha256"))
		mgValue(c, "精确页", strconv.Itoa(integer(p.source, "page")))
		nativeText(c, mgAttachmentReadiness(object(p.source["readiness"])))
		nativeText(c, "只审阅此页原始像素；其他页仍需独立审阅。需要遮挡时不会放行原始像素。")
		start := p.extractionOffset
		end := min(start+25, len(p.extractions))
		for _, job := range p.extractions[start:end] {
			id := str(job, "job_id")
			nativeButton(c, "隐私审阅绑定要求抽取 "+id).Disabled(a.Busy).OnClick(func() { p.extraction = id; p.reviewed = false })
		}
		nativePager(c, start/25+1, end < len(p.extractions), a.Busy, "上一页成功抽取", "下一页成功抽取",
			func() { p.extractionOffset = max(0, start-25) },
			func() { p.extractionOffset = end })
		mgValue(c, "所选成功抽取", p.extraction)
		ui.Checkbox(c, &p.reviewed, "已逐像素核对此页，按此确切 PNG 哈希记录").Disabled(a.Busy || p.image == nil)
		ui.Row(c).Wrap().Gap(8).Children(func() {
			nativePrimaryButton(c, "原页安全，允许查看").Disabled(a.Busy || !p.reviewed || p.extraction == "" || p.source["latest_privacy_hold_id"] != nil).OnClick(func() { p.privacyReview(a, "clear") })
			nativeButton(c, "含敏感内容，需要遮挡").Disabled(a.Busy || !p.reviewed).OnClick(func() { p.privacyReview(a, "needs_redaction") })
		})
	}
	if rendition := object(p.privacy["rendition"]); rendition != nil {
		nativeButton(c, "查看此隐私回执的团队图").Disabled(a.Busy).OnClick(func() { p.openRendition(a, rendition) })
	}
	if p.receipt != "" {
		nativeText(c, p.receipt)
	}
}
