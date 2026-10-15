package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/egoist/mygo/ui"
)

type bidUploadRow struct{ path, role, kind string }
type bidSubmissionsPage struct {
	task, request, next string
	files               []bidUploadRow
	items               []Object
	rows                [][]string
	preview             Object
	consent             bool
	serial              uint64
	page                int
}

func newBidSubmissionsPage(r Route) NativePage {
	return &bidSubmissionsPage{task: r.Params["taskId"], request: requestID()}
}
func (*bidSubmissionsPage) Title() string       { return "标书检验" }
func (p *bidSubmissionsPage) Dirty() bool       { return len(p.files) > 0 }
func (p *bidSubmissionsPage) Close()            { p.files = nil; p.preview = nil; p.serial++ }
func (p *bidSubmissionsPage) Open(a *NativeApp) { p.load(a, "") }
func (p *bidSubmissionsPage) load(a *NativeApp, cursor string) {
	path := "/tasks/" + p.task + "/bid-submissions?limit=50"
	if cursor != "" {
		path += "&cursor=" + cursor
	}
	a.Request("GET", path, nil, func(r Result) {
		if str(r.Data, "task_id") != p.task {
			a.Error = "提交列表任务范围不匹配"
			return
		}
		p.items = r.Items
		p.rows = tableRows(r.Items, "id", "revision", "state", "created_at")
		p.next = str(r.Data, "next_cursor")
		if cursor == "" {
			p.page = 1
		} else {
			p.page++
		}
	})
}
func (p *bidSubmissionsPage) invalidate() {
	p.serial++
	p.preview = nil
	p.consent = false
	p.request = requestID()
}
func bidUploadMetadata(rows []bidUploadRow, id string, dry bool) (Object, error) {
	if len(rows) < 2 || len(rows) > 20 {
		return nil, errors.New("招标与投标合计需 2 至 20 份文件")
	}
	files := []Object{}
	var total int64
	tender, bid := false, false
	for _, row := range rows {
		if row.role == "tender" {
			tender = true
		} else {
			bid = true
		}
		media := "application/pdf"
		ext := strings.ToLower(filepath.Ext(row.path))
		if ext == ".docx" {
			media = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
		} else if ext != ".pdf" {
			return nil, errors.New("仅支持 PDF 或 DOCX")
		}
		file, err := os.Open(row.path)
		if err != nil {
			return nil, err
		}
		stat, err := file.Stat()
		if err != nil {
			file.Close()
			return nil, err
		}
		if stat.Size() <= 0 || stat.Size() > 100<<20 {
			file.Close()
			return nil, errors.New("文件为空或超过 100 MiB")
		}
		total += stat.Size()
		if total > 500<<20 {
			file.Close()
			return nil, errors.New("提交总大小超过 500 MiB")
		}
		hash := sha256.New()
		_, err = io.Copy(hash, file)
		file.Close()
		if err != nil {
			return nil, err
		}
		files = append(files, Object{"role": row.role, "kind": row.kind, "media_type": media, "sha256": hex.EncodeToString(hash.Sum(nil)), "size_bytes": stat.Size()})
	}
	if !tender || !bid {
		return nil, errors.New("必须同时选择招标文件和投标文件")
	}
	return Object{"request_id": id, "files": files, "dry_run": dry}, nil
}
func (p *bidSubmissionsPage) upload(a *NativeApp, dry bool) {
	rows := append([]bidUploadRow(nil), p.files...)
	id := p.request
	serial := p.serial
	a.Do(func(ctx context.Context) (func(), error) {
		metadata, err := bidUploadMetadata(rows, id, dry)
		if err != nil {
			return nil, err
		}
		data, err := json.Marshal(metadata)
		if err != nil {
			return nil, err
		}
		files := make([]UploadFile, len(rows))
		for i, row := range rows {
			files[i] = UploadFile{Field: "files", Path: row.path}
		}
		r, err := a.Client.Upload(ctx, "/tasks/"+p.task+"/bid-submissions", map[string]string{"metadata": string(data)}, files)
		return func() {
			if serial != p.serial {
				return
			}
			if dry {
				p.preview = r.Data
				p.consent = false
			} else {
				p.files = nil
				p.invalidate()
				a.Navigate("/org/tasks/" + p.task + "/bid-submissions/" + str(r.Data, "id"))
			}
		}, err
	})
}
func (p *bidSubmissionsPage) View(c *ui.Context, a *NativeApp) {

	nativeText(c, "固定招标文件和已编写的投标文件后，明确开始本地准备。上传和本地准备不调用模型。")
	for _, role := range []string{"tender", "bid"} {
		label := "选择招标文件"
		if role == "bid" {
			label = "选择投标文件"
		}
		nativeButton(c, label).Disabled(a.Busy).OnClick(func() {
			a.PickFiles(true, func(paths []string) {
				p.invalidate()
				for _, path := range paths {
					kind := "tender"
					if role == "bid" {
						kind = "commercial_technical"
					}
					p.files = append(p.files, bidUploadRow{path: path, role: role, kind: kind})
				}
			})
		})
	}
	for i := range p.files {
		row := &p.files[i]
		nativeText(c, filepath.Base(row.path)+" / "+row.role)
		if row.role == "bid" && nativeChoice(c.Key(fmt.Sprint(i)), &row.kind, []string{"qualification", "commercial_technical", "price", "declaration", "other"}).Label("投标材料类型").Changed() {
			p.invalidate()
		}
		nativeDangerButton(c.Key("remove"+fmt.Sprint(i)), "移除文件").Disabled(a.Busy).OnClick(func() { p.files = append(p.files[:i], p.files[i+1:]...); p.invalidate() })
	}
	nativeButton(c, "预览上传限制").Disabled(a.Busy || len(p.files) < 2 || len(p.files) > 20).OnClick(func() { p.preview = nil; p.consent = false; p.upload(a, true) })
	if p.preview != nil {
		limits := object(p.preview["limits"])
		nativePairs(c, []string{"文件数上限", "单文件字节上限", "提交字节上限"}, []string{str(limits, "files"), str(limits, "file_bytes"), str(limits, "submission_bytes")})
		ui.Checkbox(c, &p.consent, "已确认文件角色与材料类型，上传此固定版本")
		nativePrimaryButton(c, "上传固定版本").Disabled(a.Busy || !p.consent).OnClick(func() { p.upload(a, false) })
	}
	nativeTable(c, []string{"提交", "版本", "状态", "提交时间"}, p.rows, func(i int) { a.Navigate("/org/tasks/" + p.task + "/bid-submissions/" + str(p.items[i], "id")) })
	nativePager(c, max(1, p.page), p.next != "", false, "上一页提交", "下一页提交",
		nil, func() { p.load(a, p.next) })
}

type bidSubmissionPage struct {
	task, id, request string
	detail, preview   Object
	consent, retry    bool
	job               jobPanel
	outbound          bidOutboundPanel
	runs              bidRunsPanel
}

func newBidSubmissionPage(r Route) NativePage {
	return &bidSubmissionPage{task: r.Params["taskId"], id: r.Params["submissionId"], request: requestID()}
}
func (*bidSubmissionPage) Title() string       { return "标书提交与本地准备" }
func (p *bidSubmissionPage) Dirty() bool       { return p.outbound.Dirty() || p.runs.Dirty() }
func (p *bidSubmissionPage) Close()            { p.job.Close(); p.outbound.Close(); p.runs.Close() }
func (p *bidSubmissionPage) Open(a *NativeApp) { p.load(a) }
func (p *bidSubmissionPage) load(a *NativeApp) {
	a.Request("GET", "/bid-submissions/"+p.id, nil, func(r Result) {
		submission := object(r.Data["submission"])
		if str(submission, "task_id") != p.task || str(submission, "id") != p.id {
			a.Error = "固定提交范围不匹配"
			return
		}
		p.detail = r.Data
		preparation := object(r.Data["preparation"])
		if containsString([]string{"queued", "running"}, str(preparation, "status")) {
			p.job.Load(a, str(preparation, "job_id"), func() { p.preview = nil; p.load(a) })
		}
		if str(submission, "state") == "prepared" {
			p.outbound.Load(a, p.task, p.id)
			p.runs.Load(a, p.task, p.id)
		}
	})
}
func expiredPreview(preview Object) bool {
	expiry, err := time.Parse(time.RFC3339Nano, str(preview, "expires_at"))
	return err != nil || !time.Now().Before(expiry)
}
func (p *bidSubmissionPage) View(c *ui.Context, a *NativeApp) {

	if p.detail == nil {
		return
	}
	submission := object(p.detail["submission"])
	documents := objects(submission["documents"])
	if documents == nil {
		documents = objects(submission["files"])
	}
	nativePairs(c, []string{"固定提交", "版本", "状态", "清单哈希"}, []string{p.id, str(submission, "revision"), str(submission, "state"), str(submission, "manifest_sha256")})
	for _, doc := range documents {
		nativeText(c, str(doc, "role")+" / "+str(doc, "kind")+" / "+str(doc, "id")+" / "+str(doc, "size_bytes")+" 字节")
	}
	preparation := object(p.detail["preparation"])
	if str(submission, "state") == "uploaded" && !containsString([]string{"queued", "running"}, str(preparation, "status")) {
		nativeHeading(c, "本地准备")
		nativeText(c, "固定解析、分页渲染、本地数字签名验证与签章条款扫描；不运行 OCR 或模型。")
		nativeButton(c, "预检本地准备").Disabled(a.Busy).OnClick(func() {
			p.preview = nil
			p.consent = false
			p.request = requestID()
			a.Request("POST", "/tasks/"+p.task+"/bid-submissions/"+p.id+"/prepare", Object{"request_id": p.request, "submission_id": p.id, "dry_run": true}, func(r Result) { p.preview = r.Data })
		})
		if p.preview != nil {
			nativeText(c, "预检有效至 "+str(p.preview, "expires_at"))
			for _, blocker := range stringList(p.preview["admission_blockers"]) {
				nativeText(c, blocker)
			}
			ui.Checkbox(c, &p.retry, "明确重试本地准备")
			ui.Checkbox(c, &p.consent, "确认此固定版本，开始本地准备")
			nativePrimaryButton(c, "提交本地准备").Disabled(a.Busy || !p.consent || expiredPreview(p.preview) || len(stringList(p.preview["admission_blockers"])) > 0).OnClick(func() {
				fixed := p.preview
				p.preview = nil
				p.consent = false
				a.Request("POST", "/tasks/"+p.task+"/bid-submissions/"+p.id+"/prepare", Object{"request_id": p.request, "submission_id": p.id, "dry_run": false, "retry": p.retry, "expected_input_hash": fixed["input_hash"], "preflight_token": fixed["preflight_token"]}, func(r Result) { p.job.Load(a, str(r.Data, "job_id"), func() { p.load(a) }) })
			})
		}
	}
	p.job.View(c, a)
	nativeHeading(c, "本地数字签名校验")
	for _, validation := range objects(p.detail["signature_validations"]) {
		final := object(validation["final_revision"])
		nativePairs(c, []string{"文档", "状态", "原件哈希", "信任快照", "验证器", "验证时间", "最终修订状态", "最后签名后修改", "覆盖最终修订签名"}, []string{str(validation, "document_id"), str(validation, "status"), str(validation, "original_sha256"), str(validation, "trust_store_sha256"), str(validation, "validator_identity"), str(validation, "validation_time"), str(final, "status"), str(final, "modified_after_last_signature"), str(final, "covered_by_signature_indices")})
		for _, signature := range objects(validation["signatures"]) {
			labels := []string{"签名序号", "签署修订", "签名字段哈希", "ByteRange", "字节范围覆盖", "密码学完整性", "内容摘要", "签名值", "签后修改", "后续修改类型", "覆盖最终修订", "证书有效性", "本地信任链", "吊销证明", "可信时间戳", "可信时间戳时间", "声明签署时间", "摘要算法", "签名算法", "验证方式", "验证限制"}
			keys := []string{"signature_index", "signed_revision", "field_name_sha256", "byte_range", "coverage_status", "crypto_status", "content_digest_status", "signature_value_status", "modified_after_signing", "post_signing_changes", "final_revision_covered", "certificate_validity_status", "trust_status", "revocation_status", "timestamp_status", "trusted_timestamp_time", "claimed_signing_time", "digest_algorithm", "signature_algorithm", "verification_variant", "reason_codes"}
			values := make([]string, len(keys))
			for i, key := range keys {
				values[i] = str(signature, key)
			}
			nativePairs(c, labels, values)
			certificate := object(signature["certificate"])
			if certificate != nil {
				nativePairs(c, []string{"证书指纹", "有效期起", "有效期止", "证书主体", "颁发者"}, []string{str(certificate, "fingerprint_sha256"), str(certificate, "not_before"), str(certificate, "not_after"), str(certificate, "subject"), str(certificate, "issuer")})
			}
		}
	}
	nativeText(c, "离线验证不判定可见签章存在；单位、日期、指定位置和吊销证明仍需人工核对。")
	nativeText(c, "签章候选共 "+str(p.detail, "signing_candidate_count")+" 条；适用性未知，尚未确认为要求。图片页未做 OCR。")
	for _, candidate := range objects(p.detail["signing_candidates"]) {
		nativePairs(c, []string{"候选序号", "文档", "页码", "页面", "原文", "起始字符", "结束字符", "位置提示", "标记候选", "主体候选", "日期候选"}, []string{str(candidate, "ordinal"), str(candidate, "document_id"), str(candidate, "page"), str(candidate, "page_id"), str(candidate, "quote"), str(candidate, "start_offset"), str(candidate, "end_offset"), str(candidate, "location_hint"), str(candidate, "mark_types"), str(candidate, "owner_roles"), str(candidate, "date_required")})
	}
	if p.detail["signing_candidates_next_cursor"] != nil {
		nativeButton(c, "更多签章候选").OnClick(func() {
			cursor := integer(p.detail, "signing_candidates_next_cursor")
			a.Request("GET", "/bid-submissions/"+p.id+"/signing-candidates?cursor="+strconv.Itoa(cursor)+"&limit=20", nil, func(r Result) {
				items := objects(r.Data["items"])
				if len(items) > 20 || integer(r.Data, "total") != integer(p.detail, "signing_candidate_count") || r.Data["next_cursor"] != nil && integer(r.Data, "next_cursor") <= cursor {
					a.Error = "签章候选分页不符合契约"
					return
				}
				known := map[string]bool{}
				for _, item := range objects(p.detail["signing_candidates"]) {
					known[str(item, "id")] = true
				}
				docIDs := map[string]bool{}
				for _, doc := range documents {
					docIDs[str(doc, "id")] = true
				}
				for _, item := range items {
					if known[str(item, "id")] || !docIDs[str(item, "document_id")] {
						a.Error = "签章候选文档范围不匹配"
						return
					}
					known[str(item, "id")] = true
				}
				p.detail["signing_candidates"] = append(objects(p.detail["signing_candidates"]), items...)
				p.detail["signing_candidates_next_cursor"] = r.Data["next_cursor"]
			})
		})
	}
	nativeHeading(c, "固定页面清单")
	for _, inventory := range objects(p.detail["inventory"]) {
		nativeText(c, str(inventory, "document_id")+" / 总页数 "+str(inventory, "page_count")+" / 文本页 "+str(inventory, "text_pages")+" / 图片页 "+str(inventory, "image_pages")+" / 签名字段 "+str(inventory, "signature_fields"))
		for _, warning := range stringList(inventory["parsing_warnings"]) {
			nativeText(c, warning)
		}
	}
	p.outbound.View(c, a, func() { p.runs.authorizationChanged(a) })
	p.runs.View(c, a)
}

func pageCursor(value any) string {
	switch v := value.(type) {
	case float64:
		return strconv.Itoa(int(v))
	case int:
		return strconv.Itoa(v)
	case string:
		return v
	}
	return ""
}
