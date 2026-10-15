package main

import (
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/egoist/mygo/ui"
)

var managementAttachmentKinds = []string{"营业执照", "资格扫描件", "合同", "业绩材料"}
var managementAttachmentKindCodes = map[string]string{"营业执照": "business_licence", "资格扫描件": "qualification_scan", "合同": "contract", "业绩材料": "performance_record"}
var managementAttachmentKindLabels = map[string]string{"business_licence": "营业执照", "qualification_scan": "资格扫描件", "contract": "合同", "performance_record": "业绩材料"}
var managementDeclarationFields = []string{"登记信息", "业绩声明", "标准表述"}
var managementDeclarationCodes = map[string]string{"登记信息": "registration_details", "业绩声明": "performance_summary", "标准表述": "standard_wording"}
var managementDeclarationLabels = map[string]string{"registration_details": "登记信息", "performance_summary": "业绩声明", "standard_wording": "标准表述"}
var managementAttachmentNext = map[string]string{
	"review_uploaded_file": "审阅上传原件", "upload_corrected_revision": "上传更正修订", "link_declaration": "关联精确声明",
	"select_for_task": "选择到任务", "archive_page": "归档原件页", "review_page_privacy": "审阅此页隐私",
	"redact_page": "等待遮挡流程", "replace_source": "替换来源", "annotate_page": "准备标注",
	"wait_for_annotation_enablement": "等待标注接入", "inspect_history": "核对历史",
}
var managementAttachmentBlockers = map[string]string{
	"archive_inactive": "附件已停用", "archive_review_pending": "原件待审阅", "archive_review_rejected": "原件已驳回", "archive_review_revoked": "原件接受已撤回",
	"profile_link_missing": "尚未关联声明", "profile_link_inactive": "声明关联已停用", "profile_selection_changed": "任务资料选择已变化",
	"attachment_selection_inactive": "任务附件选择已停用", "task_archived": "任务已归档", "source_not_archived": "此页尚未归档",
	"source_integrity_failure": "来源完整性检查失败", "privacy_pending": "此页隐私待审阅", "needs_redaction": "此页需要遮挡，当前流程未启用遮挡",
	"privacy_withdrawn": "隐私审阅已撤回", "extraction_required": "需要先抽取要求", "annotation_adapter_not_enabled": "附件标注接入尚未启用",
}

func mgAttachmentReadiness(value Object) string {
	parts := []string{managementAttachmentNext[str(value, "next_action")]}
	for _, code := range stringList(value["blockers"]) {
		label := managementAttachmentBlockers[code]
		if label == "" {
			label = code
		}
		parts = append(parts, label)
	}
	return strings.Join(parts, "；")
}
func mgAttachmentPDF(path string) error {
	info, err := os.Stat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() < 1 || info.Size() > 40*1024*1024 || !strings.EqualFold(filepath.Ext(path), ".pdf") {
		return fmt.Errorf("请选择非空且不超过 40 MiB 的单个未旋转 PDF")
	}
	return nil
}

type attachmentPage struct {
	managementState
	reads                                   managementReads
	id                                      string
	list, history, reviews, profiles, links managementListing
	root, revision, profile, selectedLink   Object
	search, filterKind                      string
	includeHistory                          bool
	editing                                 bool
	file, label, kind, reason, requestID    string
	profileID, profileNumber, declaration   string
	page                                    int
	preview                                 *ui.Bitmap
	inspected, accepted                     bool
	receipt                                 string
}

func newAttachmentPage(route Route) *attachmentPage {
	parts := strings.Split(strings.Trim(route.Path, "/"), "/")
	p := &attachmentPage{
		managementState: managementState{route: route, title: "附件档案"}, filterKind: "全部类别", kind: "营业执照",
		reason: "错误原件", declaration: "登记信息", requestID: requestID(), page: 1,
	}
	if len(parts) == 3 {
		p.id = parts[2]
		p.title = "附件精确修订与原件审阅"
	}
	return p
}
func (p *attachmentPage) Open(a *NativeApp) {
	if p.id == "" {
		p.loadList(a, "")
	} else {
		p.load(a, p.route.Query.Get("revision"))
		p.loadProfiles(a, "")
	}
}
func (p *attachmentPage) Close() {
	p.managementState.Close()
	p.reads.close()
	p.list, p.history, p.reviews, p.profiles, p.links = managementListing{}, managementListing{}, managementListing{}, managementListing{}, managementListing{}
	p.root, p.revision, p.profile, p.selectedLink = nil, nil, nil, nil
	p.preview = nil
	p.file, p.label, p.profileID, p.profileNumber, p.receipt = "", "", "", "", ""
	p.inspected, p.accepted, p.editing = false, false, false
}
func (p *attachmentPage) fail(a *NativeApp, err error) {
	p.accepted, p.inspected = false, false
	if mgDenied(err) {
		p.Close()
		return
	}
}
func (p *attachmentPage) loadList(a *NativeApp, cursor string) {
	p.list.Items, p.list.Rows, p.list.Meta = nil, nil, nil
	body := Object{"limit": 25, "history": p.includeHistory, "kind": mgNull(managementAttachmentKindCodes[p.filterKind]), "q": mgNull(p.search), "cursor": mgNull(cursor)}
	p.reads.call(a, "archives", "POST", "/management/resources/attachments/query", body, func(r Result) {
		for _, row := range r.Items {
			if str(row, "id") == "" || integer(row, "current_revision") < 1 {
				a.Error = "附件列表范围不符合契约"
				return
			}
		}
		if !p.list.install(r, cursor, func(row Object) []string {
			active := "已停用"
			if mgBool(row["active"]) {
				active = "可用"
			}
			return []string{managementAttachmentKindLabels[str(row, "kind")], str(row, "id"), strconv.Itoa(integer(row, "current_revision")), mgStatus(str(row, "review_state")) + " · " + active, mgAttachmentReadiness(object(row["readiness"]))}
		}) {
			a.Error = "附件分页范围不符合契约"
		}
	}, func(err error) { p.fail(a, err) })
}
func (p *attachmentPage) load(a *NativeApp, revisionID string) {
	p.inspected, p.accepted = false, false
	p.preview = nil
	p.reads.call(a, "archive-root", "GET", "/resources/attachments/"+url.PathEscape(p.id), nil, func(r Result) {
		root := object(r.Data["attachment"])
		if str(root, "id") != p.id || integer(root, "state_version") < 1 {
			a.Error = "附件根记录范围不符合契约"
			return
		}
		p.root = root
		p.loadHistory(a, "")
		if !mgBool(root["active"]) {
			p.revision = nil
			p.profile = nil
			p.preview = nil
			return
		}
		exact := revisionID
		if exact == "" {
			exact = str(root, "current_revision_id")
		}
		p.reads.call(a, "archive-revision", "GET", "/resources/attachments/revisions/"+url.PathEscape(exact), nil, func(r Result) {
			value := r.Data
			if str(value, "attachment_id") != p.id || str(value, "id") != exact || str(object(value["original"]), "sha256") != str(value, "original_sha256") || integer(object(value["original"]), "size_bytes") != integer(value, "size_bytes") {
				a.Error = "原件修订不属于当前档案或完整性元数据不一致"
				return
			}
			p.revision = value
			p.page = 1
			if !p.editing {
				p.label = str(object(value["metadata"]), "label")
				p.kind = managementAttachmentKindLabels[str(value, "kind")]
			}
			p.loadReviews(a, "")
		}, func(err error) { p.fail(a, err) })
	}, func(err error) { p.fail(a, err) })
}
func (p *attachmentPage) loadHistory(a *NativeApp, cursor string) {
	query := url.Values{"limit": {"25"}}
	if cursor != "" {
		query.Set("cursor", cursor)
	}
	p.reads.call(a, "archive-history", "GET", "/resources/attachments/"+url.PathEscape(p.id)+"/revisions?"+query.Encode(), nil, func(r Result) {
		for _, row := range r.Items {
			if str(row, "attachment_id") != p.id {
				a.Error = "内容历史不属于当前档案"
				return
			}
		}
		if !p.history.install(r, cursor, func(row Object) []string {
			return []string{strconv.Itoa(integer(row, "revision")), mgStatus(str(row, "review_state")), str(row, "created_at"), str(row, "original_sha256")}
		}) {
			a.Error = "内容历史分页范围不符合契约"
		}
	}, func(err error) { p.fail(a, err) })
}
func (p *attachmentPage) loadReviews(a *NativeApp, cursor string) {
	rid := str(p.revision, "id")
	query := url.Values{"limit": {"25"}}
	if cursor != "" {
		query.Set("cursor", cursor)
	}
	p.reads.call(a, "archive-reviews", "GET", "/resources/attachments/revisions/"+url.PathEscape(rid)+"/reviews?"+query.Encode(), nil, func(r Result) {
		if str(p.revision, "id") != rid {
			return
		}
		for _, row := range r.Items {
			if str(row, "attachment_id") != p.id || str(row, "attachment_revision_id") != rid {
				a.Error = "审阅记录不属于此精确原件修订"
				return
			}
		}
		if !p.reviews.install(r, cursor, func(row Object) []string {
			return []string{str(row, "decision"), str(row, "reason"), str(row, "reviewed_at"), str(row, "id")}
		}) {
			a.Error = "审阅历史分页范围不符合契约"
		}
	}, func(err error) { p.fail(a, err) })
}
func (p *attachmentPage) loadProfiles(a *NativeApp, cursor string) {
	p.reads.call(a, "profile-options", "POST", "/management/resources/profiles/query", Object{"limit": 25, "cursor": mgNull(cursor), "q": nil, "state": "active"}, func(r Result) {
		for _, row := range r.Items {
			if str(object(row["ref"]), "kind") != "profiles" {
				a.Error = "资料候选范围不符合契约"
				return
			}
		}
		if !p.profiles.install(r, cursor, func(row Object) []string {
			return []string{str(row, "name"), strconv.Itoa(integer(row, "revision")), str(object(row["ref"]), "resource_id")}
		}) {
			a.Error = "资料分页范围不符合契约"
		}
	}, func(err error) { p.fail(a, err) })
}
func (p *attachmentPage) openProfile(a *NativeApp) {
	if p.profileID == "" {
		return
	}
	id, number := p.profileID, p.profileNumber
	path := "/management/resources/profiles/" + url.PathEscape(id)
	if number != "" {
		path += "?revision=" + url.QueryEscape(number)
	}
	p.profile, p.selectedLink = nil, nil
	p.links = managementListing{}
	p.reads.call(a, "profile-detail", "GET", path, nil, func(r Result) {
		revision := object(object(r.Data["detail"])["revision"])
		if str(object(r.Data["ref"]), "resource_id") != id || str(revision, "profile_id") != id || number != "" && strconv.Itoa(integer(revision, "revision")) != number {
			a.Error = "声明资料不属于所选精确修订"
			return
		}
		if p.profileID != id || p.profileNumber != number {
			return
		}
		p.profile = r.Data
		p.loadLinks(a, "")
	}, func(err error) { p.fail(a, err) })
}
func (p *attachmentPage) loadLinks(a *NativeApp, cursor string) {
	rid := str(object(object(p.profile["detail"])["revision"]), "id")
	query := url.Values{"limit": {"25"}, "history": {"true"}}
	if cursor != "" {
		query.Set("cursor", cursor)
	}
	p.reads.call(a, "declaration-links", "GET", "/resources/profiles/revisions/"+url.PathEscape(rid)+"/attachments?"+query.Encode(), nil, func(r Result) {
		if str(object(object(p.profile["detail"])["revision"]), "id") != rid {
			return
		}
		for _, row := range r.Items {
			if str(row, "profile_revision_id") != rid {
				a.Error = "关联不属于当前精确声明"
				return
			}
		}
		if !p.links.install(r, cursor, func(row Object) []string {
			active := "已停用"
			if mgBool(row["active"]) {
				active = "可用"
			}
			return []string{managementDeclarationLabels[str(row, "field")], str(row, "attachment_revision_id"), active, str(row, "id")}
		}) {
			a.Error = "声明关联分页范围不符合契约"
		}
	}, func(err error) { p.fail(a, err) })
}
func (p *attachmentPage) choose(a *NativeApp) {
	a.PickFiles(false, func(paths []string) {
		if err := mgAttachmentPDF(paths[0]); err != nil {
			a.Error = err.Error()
			return
		}
		p.file = paths[0]
		p.dirty = true
	})
}
func (p *attachmentPage) save(a *NativeApp) {
	if err := mgAttachmentPDF(p.file); err != nil {
		a.Error = err.Error()
		return
	}
	if strings.TrimSpace(p.label) == "" {
		a.Error = "请填写原件显示名称"
		return
	}
	metadata := Object{"request_id": p.requestID, "metadata": Object{"kind": managementAttachmentKindCodes[p.kind], "label": strings.TrimSpace(p.label)}, "parts": []Object{}, "dry_run": false}
	path := "/resources/attachments"
	if p.id != "" {
		path += "/" + url.PathEscape(p.id) + "/revisions"
		metadata["expected_state_version"] = p.root["state_version"]
	}
	mgUpload(a, path, metadata, "files", []managementFile{{Path: p.file}}, func(r Result) {
		if str(r.Data, "attachment_id") == "" || str(r.Data, "revision_id") == "" || p.id != "" && str(r.Data, "attachment_id") != p.id {
			a.Error = "附件保存回执范围不符合契约"
			return
		}
		p.requestID = requestID()
		p.editing, p.dirty = false, false
		p.file = ""
		if p.id == "" {
			a.Navigate(mgTaskLink(p.route, "/org/attachments/"+str(r.Data, "attachment_id")))
		} else {
			p.load(a, str(r.Data, "revision_id"))
		}
	}, func(err error) { p.fail(a, err) })
}
func (p *attachmentPage) original(a *NativeApp) {
	rid := str(p.revision, "id")
	p.inspected, p.accepted = false, false
	file := object(p.revision["original"])
	mgDownload(a, "/resources/attachments/revisions/"+url.PathEscape(rid)+"/file/download-link", "/resources/attachments/revisions/"+url.PathEscape(rid)+"/file/download", str(file, "name"), file, func() {
		if !p.closed && str(p.revision, "id") == rid {
			p.inspected = true
		}
	})
}
func (p *attachmentPage) previewOriginal(a *NativeApp) {
	rid, page := str(p.revision, "id"), p.page
	p.preview = nil
	p.inspected, p.accepted = false, false
	base := fmt.Sprintf("/resources/attachments/revisions/%s/pages/%d/preview", url.PathEscape(rid), page)
	p.reads.image(a, "original-page", base+"-link?zoom=1", base, Object{"media_type": "image/png"}, func(bitmap *ui.Bitmap) {
		if str(p.revision, "id") == rid && p.page == page {
			p.preview = bitmap
			p.inspected = true
		}
	}, func(err error) { p.fail(a, err) })
}
func (p *attachmentPage) decision(a *NativeApp, action string) {
	if !mgBool(p.root["active"]) || p.revision == nil || action == "approve" && (!p.inspected || !p.accepted) || action == "reject" && !p.inspected {
		return
	}
	value := p.revision
	rid := str(value, "id")
	reason := map[string]string{"错误原件": "wrong_document", "原件无法辨读": "unreadable_document", "元数据不符": "metadata_mismatch", "隐私问题": "privacy_concern"}[p.reason]
	if action == "approve" {
		reason = "accepted_for_internal_use"
	} else if action == "revoke" {
		reason = "withdrawn_by_org"
	}
	body := Object{"request_id": p.requestID, "expected_state_version": p.root["state_version"], "expected_review_id": value["latest_review_id"], "file_id": value["file_id"], "original_sha256": value["original_sha256"], "metadata_sha256": value["metadata_sha256"], "decision": action, "reason": reason}
	p.accepted = false
	mgCall(a, "POST", "/resources/attachments/revisions/"+url.PathEscape(rid)+"/reviews", body, func(r Result) {
		if str(r.Data, "attachment_revision_id") != rid || str(r.Data, "file_id") != str(value, "file_id") || str(r.Data, "original_sha256") != str(value, "original_sha256") || str(r.Data, "decision") != action {
			a.Error = "原件审阅回执不属于此精确修订"
			return
		}
		p.requestID = requestID()
		p.receipt = "原件审阅已记录 · " + str(r.Data, "id") + "；不会确认响应证据。"
		p.load(a, rid)
	}, func(err error) { p.fail(a, err) })
}
func (p *attachmentPage) deactivate(a *NativeApp) {
	a.Confirm("停用附件档案", "停用后不能恢复本档案。原件和历史保留，现有选择不再可用于新的材料。", func() {
		mgCall(a, "POST", "/resources/attachments/"+url.PathEscape(p.id)+"/deactivate", Object{"request_id": p.requestID, "expected_state_version": p.root["state_version"], "reason": "withdrawn_by_org"}, func(r Result) {
			p.requestID = requestID()
			p.load(a, "")
		}, func(err error) { p.fail(a, err) })
	})
}
func (p *attachmentPage) link(a *NativeApp) {
	rid := str(object(object(p.profile["detail"])["revision"]), "id")
	field := managementDeclarationCodes[p.declaration]
	if rid == "" || str(p.revision, "review_state") != "approved" || !mgBool(p.root["active"]) || strings.TrimSpace(str(object(object(object(p.profile["detail"])["revision"])["data"]), field)) == "" {
		return
	}
	attachmentRevision, approval := str(p.revision, "id"), str(p.revision, "latest_review_id")
	mgCall(a, "POST", "/resources/profiles/revisions/"+url.PathEscape(rid)+"/attachments", Object{"request_id": p.requestID, "field": field, "attachment_revision_id": attachmentRevision, "approval_id": approval}, func(r Result) {
		if str(r.Data, "profile_revision_id") != rid || str(r.Data, "attachment_revision_id") != attachmentRevision || str(r.Data, "approval_id") != approval || str(r.Data, "field") != field {
			a.Error = "声明关联回执范围不符合契约"
			return
		}
		p.requestID = requestID()
		p.receipt = "已关联精确声明与原件；任务仍需显式选择。"
		p.loadLinks(a, "")
	}, func(err error) { p.fail(a, err) })
}
func (p *attachmentPage) withdrawLink(a *NativeApp, row Object) {
	id, version := str(row, "id"), integer(row, "state_version")
	mgCall(a, "POST", "/profile-attachment-links/"+url.PathEscape(id)+"/deactivate", Object{"request_id": p.requestID, "expected_state_version": version, "reason": "declaration_changed"}, func(r Result) {
		if str(r.Data, "id") != id {
			a.Error = "停用关联回执范围不符合契约"
			return
		}
		p.requestID = requestID()
		p.selectedLink = nil
		p.loadLinks(a, "")
	}, func(err error) { p.fail(a, err) })
}
func (p *attachmentPage) viewUpload(c *ui.Context, a *NativeApp) {
	ui.Column(c.Key("attachment-upload")).Disabled(a.Busy).Gap(8).Children(func() {
		if mgChoice(c, "附件类别", &p.kind, managementAttachmentKinds) {
			p.dirty = true
		}
		if nativeField(c, "显示名称（仅原件读者可读）", &p.label, false) {
			p.dirty = true
		}
		nativeButton(c, "选择一个未旋转 PDF").OnClick(func() { p.choose(a) })
		if p.file != "" {
			mgValue(c, "所选原件", filepath.Base(p.file))
		}
		nativeText(c, "最多 40 MiB、200 页。当前附件档案只接受一个未旋转 PDF；多文件和图片组合尚未启用。")
		nativePrimaryButton(c, "保存完整 PDF 原件修订").Disabled(p.file == "" || strings.TrimSpace(p.label) == "").OnClick(func() { p.save(a) })
	})
	nativeButton(c, "取消上传").Disabled(a.Busy).OnClick(func() {
		cancel := func() { p.editing, p.dirty = false, false; p.file = "" }
		if p.dirty {
			a.Confirm("放弃所选原件和未保存元数据", "放弃此附件草稿？", cancel)
		} else {
			cancel()
		}
	})
}
func (p *attachmentPage) View(c *ui.Context, a *NativeApp) {
	if p.closed {
		nativeText(c, "附件权限或会话已变化，请重新打开。")
		return
	}
	if p.id == "" {
		p.viewList(c, a)
		return
	}
	if p.root == nil {
		nativeText(c, "正在读取附件档案。")
		return
	}
	mgValue(c, "档案", p.id)
	mgValue(c, "当前修订与状态版本", fmt.Sprintf("第 %d 版 · 状态版本 %d", integer(p.root, "current_revision"), integer(p.root, "state_version")))
	mgValue(c, "就绪状态与下一步", mgAttachmentReadiness(object(p.root["readiness"])))
	if !mgBool(p.root["active"]) {
		nativeText(c, "此档案已停用且不能恢复；保留历史，不读取受限原件。")
		p.viewHistory(c, a)
		return
	}
	if p.revision == nil {
		nativeText(c, "正在读取精确原件修订。")
		return
	}
	mgValue(c, "精确修订", fmt.Sprintf("第 %d 版 · %s", integer(p.revision, "revision"), str(p.revision, "id")))
	mgValue(c, "原件名称", str(object(p.revision["metadata"]), "label"))
	mgValue(c, "原件 ID", str(p.revision, "file_id"))
	mgValue(c, "类别、页数与大小", fmt.Sprintf("%s · %d 页 · %d 字节", managementAttachmentKindLabels[str(p.revision, "kind")], integer(p.revision, "page_count"), integer(p.revision, "size_bytes")))
	revisedAt := str(p.revision, "revised_at")
	if revisedAt == "" {
		revisedAt = str(p.revision, "created_at")
	}
	mgValue(c, "修订时间", revisedAt)
	if str(p.revision, "id") != str(p.root, "current_revision_id") {
		nativeText(c, "资料库有较新修订；此处仍为精确历史修订，不自动替换关联与任务选择。")
	}
	mgValue(c, "审阅状态", mgStatus(str(p.revision, "review_state")))
	mgValue(c, "原件 SHA-256", str(p.revision, "original_sha256"))
	mgValue(c, "元数据 SHA-256", str(p.revision, "metadata_sha256"))
	nativeText(c, "接受内部使用与逐页隐私审阅分别记录；任何操作都不确认响应证据。")
	ui.Row(c).Wrap().Gap(8).Children(func() {
		nativeButton(c, "上传更正修订").Disabled(a.Busy || p.editing).OnClick(func() { p.editing = true; p.dirty = false; p.file = "" })
		nativeDangerButton(c, "停用档案").Disabled(a.Busy).OnClick(func() { p.deactivate(a) })
		nativeButton(c, "下载并核验此精确修订原件").Disabled(a.Busy).OnClick(func() { p.original(a) })
	})
	if p.editing {
		p.viewUpload(c, a)
	}
	pageText := strconv.Itoa(p.page)
	if nativeField(c, "核对原件页码", &pageText, false) {
		if page, err := strconv.Atoi(pageText); err == nil && page > 0 && page <= integer(p.revision, "page_count") {
			p.page = page
			p.preview = nil
			p.inspected, p.accepted = false, false
		}
	}
	nativeButton(c, "核对此页原件").Disabled(a.Busy).OnClick(func() { p.previewOriginal(a) })
	if p.preview != nil {
		ui.Image(c, p.preview).Width(820).Fit(ui.Contain).Label("附件精确原件页")
	}
	ui.Checkbox(c, &p.accepted, "已核对此精确修订原件，接受内部使用").Disabled(a.Busy || !p.inspected)
	state := str(p.revision, "review_state")
	if state != "approved" {
		nativePrimaryButton(c, "接受内部使用").Disabled(a.Busy || !p.accepted || !p.inspected).OnClick(func() { p.decision(a, "approve") })
	}
	if state == "pending" {
		mgChoice(c, "驳回原因", &p.reason, []string{"错误原件", "原件无法辨读", "元数据不符", "隐私问题"})
		nativeDangerButton(c, "驳回此修订").Disabled(a.Busy || !p.inspected).OnClick(func() { p.decision(a, "reject") })
	}
	if state == "approved" {
		nativeDangerButton(c, "撤回此修订接受").Disabled(a.Busy).OnClick(func() { p.decision(a, "revoke") })
	}
	ui.Divider(c)
	nativeHeading(c, "关联到精确单位声明")
	nativeTable(c, []string{"单位资料", "修订", "资料 ID"}, p.profiles.Rows, func(i int) {
		row := p.profiles.Items[i]
		p.profileID, p.profileNumber = str(object(row["ref"]), "resource_id"), strconv.Itoa(integer(row, "revision"))
		p.openProfile(a)
	})
	p.profiles.controls(c, a, "资料候选", func(cursor string) { p.loadProfiles(a, cursor) })
	nativeField(c, "资料 ID", &p.profileID, false)
	nativeField(c, "资料修订号（留空读取当前）", &p.profileNumber, false)
	nativeButton(c, "打开资料精确修订").Disabled(a.Busy || p.profileID == "").OnClick(func() { p.openProfile(a) })
	if p.profile != nil {
		mgChoice(c, "声明字段", &p.declaration, managementDeclarationFields)
		declaration := str(object(object(object(p.profile["detail"])["revision"])["data"]), managementDeclarationCodes[p.declaration])
		mgSecretRead(c.Key("attachment-declaration"), "精确声明正文", declaration, nil)
		nativePrimaryButton(c, "关联此精确原件修订与声明").Disabled(a.Busy || state != "approved" || strings.TrimSpace(declaration) == "").OnClick(func() { p.link(a) })
		nativeTable(c, []string{"声明字段", "原件精确修订", "状态", "关联 ID"}, p.links.Rows, func(i int) { p.selectedLink = p.links.Items[i] })
		p.links.controls(c, a, "声明关联", func(cursor string) { p.loadLinks(a, cursor) })
		if p.selectedLink != nil {
			nativeDangerButton(c, "停用所选声明关联").Disabled(a.Busy || !mgBool(p.selectedLink["active"])).OnClick(func() { p.withdrawLink(a, p.selectedLink) })
		}
	}
	p.viewHistory(c, a)
	if p.receipt != "" {
		nativeText(c, p.receipt)
	}
}
func (p *attachmentPage) viewHistory(c *ui.Context, a *NativeApp) {
	nativeHeading(c, "内容修订历史")
	nativeTable(c, []string{"修订", "原件审阅", "时间", "SHA-256"}, p.history.Rows, func(i int) {
		if mgBool(p.root["active"]) {
			p.load(a, str(p.history.Items[i], "id"))
		}
	})
	p.history.controls(c, a, "附件修订", func(cursor string) { p.loadHistory(a, cursor) })
	nativeHeading(c, "此精确原件审阅历史")
	nativeTable(c, []string{"决定", "原因", "时间", "回执"}, p.reviews.Rows, nil)
	p.reviews.controls(c, a, "原件审阅", func(cursor string) { p.loadReviews(a, cursor) })
}
func (p *attachmentPage) viewList(c *ui.Context, a *NativeApp) {
	nativeText(c, "上传后先接受内部使用，再关联声明。原件接受与逐页隐私审阅分别记录。")
	ui.Row(c).FillWidth().Wrap().Gap(12).AlignItems(ui.End).Children(func() {
		ui.Column(c).Width(260).Children(func() { nativeField(c, "搜索类别或附件 ID 前缀", &p.search, false) })
		ui.Column(c).Width(260).Children(func() {
			mgChoice(c, "附件类别", &p.filterKind, append([]string{"全部类别"}, managementAttachmentKinds...))
		})
	})
	ui.Checkbox(c, &p.includeHistory, "包含已停用档案")
	ui.Row(c).Wrap().Gap(8).Children(func() {
		nativeButton(c, "查询附件档案").Disabled(a.Busy).OnClick(func() { p.list.Previous = nil; p.loadList(a, "") })
		nativePrimaryButton(c, "上传附件").Disabled(a.Busy || p.editing).OnClick(func() { p.editing = true; p.dirty = false; p.file, p.label = "", "" })
	})
	if p.editing {
		p.viewUpload(c, a)
	}
	nativeTable(c, []string{"类别", "附件", "当前修订", "原件审阅", "下一步"}, p.list.Rows, func(i int) { a.Navigate(mgTaskLink(p.route, "/org/attachments/"+str(p.list.Items[i], "id"))) })
	p.list.controls(c, a, "附件档案", func(cursor string) { p.loadList(a, cursor) })
}
