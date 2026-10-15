package main

import (
	"fmt"
	"net/url"
	"strings"

	"github.com/egoist/mygo/ui"
)

var bidReportSections = []string{"overall", "basic_information", "compliance", "signatures", "risks", "scores", "evidence", "remediation", "methodology"}
var bidReportTitles = []string{"一、总体结论", "二、基本信息", "三、废标判定", "签章校验", "四、高风险缺陷", "五、得分预估", "六、证据核对", "七、补救清单", "八、检验说明"}

type bidReportPage struct {
	task, id, snapshot, section, cursor, next, historyNext, request string
	meta, preview                                                   Object
	items, history                                                  []Object
	trail                                                           []string
	consent, retry                                                  bool
	serial                                                          uint64
	job                                                             jobPanel
}

func newBidReportPage(r Route) NativePage {
	return &bidReportPage{task: r.Params["taskId"], id: r.Params["reviewId"], section: "overall", request: requestID()}
}
func (*bidReportPage) Title() string { return "标书检验报告" }
func (*bidReportPage) Dirty() bool   { return false }
func (p *bidReportPage) Close() {
	p.serial++
	p.job.Close()
	p.meta = nil
	p.preview = nil
	p.items = nil
}
func (p *bidReportPage) Open(a *NativeApp) { p.read(a); p.readHistory(a, false) }
func (p *bidReportPage) invalidate() {
	p.preview = nil
	p.consent = false
	p.retry = false
	p.request = requestID()
}
func (p *bidReportPage) read(a *NativeApp) {
	serial := p.serial
	q := url.Values{"section": {p.section}, "limit": {"50"}}
	if p.snapshot != "" {
		q.Set("snapshot_id", p.snapshot)
	}
	if p.cursor != "" {
		q.Set("cursor", p.cursor)
	}
	a.Request("GET", "/bid-reviews/"+p.id+"/report?"+q.Encode(), nil, func(r Result) {
		if serial != p.serial {
			return
		}
		if !p.valid(r, p.section, p.snapshot) {
			a.Error = "报告响应范围无法核验"
			p.meta = nil
			p.items = nil
			return
		}
		if p.meta != nil {
			for _, key := range []string{"input_hash", "report_input_hash", "decisions_snapshot_sha256", "projection"} {
				if str(p.meta, key) != str(r.Data, key) {
					p.meta = nil
					p.items = nil
					p.invalidate()
					a.Error = "报告快照已变化，请刷新"
					return
				}
			}
		}
		p.meta = r.Data
		p.items = r.Items
		p.next = str(r.Data, "next_cursor")
	})
}
func (p *bidReportPage) valid(r Result, section, snapshot string) bool {
	d := r.Data
	if str(d, "task_id") != p.task || str(d, "review_id") != p.id || str(d, "section") != section || str(d, "snapshot_id") != snapshot || len(r.Items) > 50 || !containsString([]string{"protected", "cleared", "safe"}, str(d, "projection")) || !containsString([]string{"complete", "partial"}, str(d, "completion")) || !strings.Contains(str(d, "advisory_statement"), "评标委员会") {
		return false
	}
	for _, key := range []string{"input_hash", "report_input_hash", "decisions_snapshot_sha256", "current_decisions_snapshot_sha256"} {
		if !sha256Text(str(d, key)) {
			return false
		}
	}
	sections := objects(d["sections"])
	if len(sections) != len(bidReportSections) {
		return false
	}
	for i, row := range sections {
		if str(row, "key") != bidReportSections[i] || str(row, "title") != bidReportTitles[i] {
			return false
		}
	}
	for _, row := range r.Items {
		if !containsString([]string{"notice", "basic_information", "obligation", "pdf_validation", "signing_requirement", "usage", "cost", "finding"}, str(row, "kind")) {
			return false
		}
		if finding := object(row["finding"]); finding != nil {
			if str(finding, "task_id") != p.task || str(finding, "review_id") != p.id {
				return false
			}
			if decision := object(row["decision"]); decision != nil && str(decision, "finding_id") != str(finding, "id") {
				return false
			}
		}
	}
	artifacts := objects(d["artifacts"])
	if len(artifacts) > 2 || str(d, "projection") != "protected" && len(artifacts) > 0 {
		return false
	}
	for _, artifact := range artifacts {
		if str(artifact, "task_id") != p.task || str(artifact, "report_id") != p.id || snapshot != "" && str(artifact, "snapshot_id") != snapshot || !sha256Text(str(artifact, "sha256")) || integer(artifact, "size_bytes") <= 0 {
			return false
		}
	}
	return true
}
func sha256Text(text string) bool {
	if len(text) != 64 {
		return false
	}
	for _, ch := range text {
		if !(ch >= '0' && ch <= '9' || ch >= 'a' && ch <= 'f') {
			return false
		}
	}
	return true
}
func (p *bidReportPage) readHistory(a *NativeApp, more bool) {
	q := url.Values{"limit": {"50"}}
	if more {
		q.Set("cursor", p.historyNext)
	}
	a.Request("GET", "/bid-reviews/"+p.id+"/reports?"+q.Encode(), nil, func(r Result) {
		if str(r.Data, "task_id") != p.task || str(r.Data, "review_id") != p.id || len(r.Items) > 50 {
			a.Error = "报告历史范围不匹配"
			return
		}
		if more {
			p.history = append(p.history, r.Items...)
		} else {
			p.history = r.Items
		}
		p.historyNext = str(r.Data, "next_cursor")
	})
}
func (p *bidReportPage) inspect(a *NativeApp) {
	p.invalidate()
	a.Request("GET", "/bid-reviews/"+p.id+"/report?section=overall&limit=50", nil, func(current Result) {
		if !p.valid(current, "overall", "") {
			a.Error = "当前报告快照无法核验"
			return
		}
		p.readHistory(a, false)
		a.Request("POST", "/bid-reviews/"+p.id+"/artifacts", Object{"request_id": p.request, "report_id": p.id, "dry_run": true, "expected_decisions_snapshot_sha256": current.Data["decisions_snapshot_sha256"]}, func(r Result) {
			data := r.Data
			preflight := object(data["preflight"])
			if str(data, "report_id") != p.id || str(data, "decisions_snapshot_sha256") != str(current.Data, "decisions_snapshot_sha256") || str(data, "report_input_hash") != str(current.Data, "report_input_hash") || str(preflight, "input_hash") != str(data, "input_hash") || integer(preflight, "planned_calls") != 0 {
				a.Error = "报告预检回执无法核验"
				return
			}
			p.preview = data
		})
	})
}
func (p *bidReportPage) View(c *ui.Context, a *NativeApp) {

	nativeButton(c, "刷新报告").Disabled(a.Busy).OnClick(func() { p.serial++; p.meta = nil; p.items = nil; p.invalidate(); p.Open(a) })
	choices := []string{""}
	for _, row := range p.history {
		if str(row, "artifact_status") == "ready" {
			choices = append(choices, str(row, "snapshot_id"))
		}
		nativeText(c, nativeLabel(str(row, "artifact_status"))+" / "+nativeDisplay(str(row, "created_at"))+" / "+nativeShortID(str(row, "snapshot_id")))
	}
	if nativeSelect(c, "报告快照（空为当前决定）", &p.snapshot, choices) {
		p.serial++
		p.cursor = ""
		p.trail = nil
		p.meta = nil
		p.items = nil
		p.invalidate()
		p.read(a)
	}
	if p.historyNext != "" {
		nativeButton(c, "更多历史快照").OnClick(func() { p.readHistory(a, true) })
	}
	if p.meta == nil {
		return
	}
	nativeText(c, str(p.meta, "advisory_statement"))
	nativeText(c, "完成情况 "+str(p.meta, "completion"))
	nativeText(c, "决定快照 SHA-256 "+str(p.meta, "decisions_snapshot_sha256"))
	nativeText(c, "报告数据 SHA-256 "+str(p.meta, "report_input_hash"))
	if p.snapshot != "" && str(p.meta, "decisions_snapshot_sha256") != str(p.meta, "current_decisions_snapshot_sha256") {
		nativeText(c, "此固定快照之后已有新人工决定；重新预检并渲染可生成新快照。")
	}
	if nativeSelect(c, "报告章节", &p.section, bidReportSections) {
		p.serial++
		p.cursor = ""
		p.trail = nil
		p.items = nil
		p.read(a)
	}
	for i, key := range bidReportSections {
		if key == p.section {
			nativeHeading(c, bidReportTitles[i])
		}
	}
	for index, row := range p.items {
		ui.Column(c.Key(fmt.Sprintf("report-row-%d", index))).FillWidth().Gap(12).Padding(16, 0).BorderWidth(0, 0, 1, 0).BorderColor(c.Theme().Border).Children(func() { bidReportRow(c, row) })
	}
	if len(p.items) == 0 {
		nativeText(c, "本节没有逐项记录；不代表检验通过。")
	}
	nativePager(c, len(p.trail)+1, p.next != "", a.Busy, "上一页报告", "下一页报告",
		func() { p.cursor = p.trail[len(p.trail)-1]; p.trail = p.trail[:len(p.trail)-1]; p.read(a) },
		func() { p.trail = append(p.trail, p.cursor); p.cursor = p.next; p.read(a) })
	if str(p.meta, "projection") != "protected" {
		nativeText(c, "当前显示隐私清理后的报告；受保护报告与 Word 需要本人登录操作。")
		return
	}
	nativeHeading(c, "Word 报告")
	nativeText(c, "本地渲染不调用模型；明确提交后建立不可变快照与 Word 作业。")
	nativeButton(c, "预检 Word 报告").Disabled(a.Busy).OnClick(func() { p.inspect(a) })
	if p.preview != nil {
		fixed := p.preview
		retryNeeded := false
		for _, row := range p.history {
			if row["can_retry"] == true && str(row, "input_hash") == str(fixed, "input_hash") && containsString([]string{"failed", "cancelled"}, str(row, "artifact_status")) {
				retryNeeded = true
			}
		}
		nativeText(c, "模型调用 0 / 有效至 "+str(fixed, "expires_at"))
		nativeText(c, "输入哈希 "+str(fixed, "input_hash"))
		nativeText(c, "固定决定哈希 "+str(fixed, "decisions_snapshot_sha256")+" / 渲染器 "+str(fixed, "renderer_identity"))
		for _, code := range stringList(fixed["admission_blockers"]) {
			nativeText(c, code)
		}
		if retryNeeded {
			ui.Checkbox(c, &p.retry, "明确重试此决定快照的失败或取消作业")
		}
		ui.Checkbox(c, &p.consent, "确认此决定快照，提交 Word 渲染")
		nativePrimaryButton(c, "提交 Word 渲染").Disabled(a.Busy || !p.consent || expiredPreview(fixed) || len(stringList(fixed["admission_blockers"])) > 0 || retryNeeded && !p.retry).OnClick(func() {
			body := Object{"request_id": p.request, "report_id": p.id, "dry_run": false, "retry": retryNeeded && p.retry, "expected_input_hash": fixed["input_hash"], "preflight_token": fixed["preflight_token"], "expected_decisions_snapshot_sha256": fixed["decisions_snapshot_sha256"]}
			p.invalidate()
			a.Request("POST", "/bid-reviews/"+p.id+"/artifacts", body, func(r Result) {
				p.job.Load(a, str(r.Data, "job_id"), func() {
					p.invalidate()
					if id := str(object(p.job.data["result"]), "snapshot_id"); str(p.job.data, "status") == "succeeded" && id != "" {
						p.snapshot = id
						p.meta = nil
						p.read(a)
					}
					p.readHistory(a, false)
				})
			})
		})
	}
	p.job.View(c, a)
	for _, artifact := range objects(p.meta["artifacts"]) {
		if str(artifact, "format") != "docx" {
			continue
		}
		nativeText(c, "固定 Word "+str(artifact, "id")+" / "+str(artifact, "size_bytes")+" 字节 / SHA-256 "+str(artifact, "sha256"))
		nativeButton(c.Key(str(artifact, "id")), "下载 Word 报告").Disabled(a.Busy).OnClick(func() {
			id := str(artifact, "id")
			a.DownloadVerified("/bid-review-artifacts/"+id+"/download-link", "/bid-review-artifacts/"+id+"/download", "bid-review-report.docx", Object{"sha256": artifact["sha256"], "size_bytes": artifact["size_bytes"], "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}, nil)
		})
	}
}
func bidReportRow(c *ui.Context, row Object) {
	if finding := object(row["finding"]); finding != nil {
		nativeHeading(c, str(finding, "title")+" / "+nativeStatus(finding["severity"]))
		nativeText(c, "机器原始结论 "+nativeStatus(finding["outcome"])+" / 影响 "+nativeStatus(finding["impact"]))
		basis := object(finding["basis"])
		nativeText(c, "依据 "+nativeValue(basis["kind"])+" / "+str(basis, "rule_or_prompt_version")+" / "+str(basis, "model"))
		nativeText(c, str(finding, "explanation"))
		nativeText(c, str(row, "summary"))
		for _, key := range []string{"tender_support", "bid_support"} {
			for _, citation := range objects(finding[key]) {
				bidCitationText(c, citation)
			}
		}
		absence := object(finding["absence_search"])
		if absence != nil {
			nativeText(c, "缺失检索范围 "+nativeValue(absence["coverage"]))
			for _, page := range objects(absence["searched_pages"]) {
				nativeText(c, str(page, "document_id")+" 第 "+nativeValue(page["page"])+" 页")
			}
		}
		nativeText(c, "快照人工决定 "+nativeStatus(finding["state"])+" / 修订 "+nativeValue(finding["revision"]))
		decision := object(row["decision"])
		nativeText(c, nativeValue(decision["action"])+" / "+str(decision, "decided_at")+" / "+str(decision, "reason"))
		nativeText(c, str(finding, "remediation"))
		for _, code := range stringList(finding["limitation_codes"]) {
			nativeText(c, code)
		}
		return
	}
	if requirement := object(row["requirement"]); requirement != nil {
		nativeHeading(c, "签章要求 / "+nativeStatus(requirement["applicability"]))
		nativeText(c, nativeMarkList(requirement["mark_types"])+" / 需日期 "+nativeValue(requirement["date_required"]))
		bidCitationText(c, object(requirement["citation"]))
		locations := objects(requirement["required_locations"])
		for _, location := range locations {
			bidPresenceLocation(c, location)
		}
		if len(locations) == 0 {
			if str(requirement, "applicability") == "not_applicable" {
				nativeText(c, "无适用签章位置")
			} else {
				nativeText(c, "目标位置未确定，不能判为存在")
			}
		}
		return
	}
	if validation := object(row["validation"]); validation != nil {
		nativeHeading(c, "PDF 本地数字签名校验 / "+nativePDFStatus(validation["status"]))
		nativePairs(c, []string{"文件", "验证器", "验证时间", "原件 SHA-256", "固定信任快照"}, []string{nativeValue(validation["document_id"]), nativeValue(validation["validator_identity"]), nativeValue(validation["validation_time"]), nativeValue(validation["original_sha256"]), nativeValue(validation["trust_store_sha256"])})
		final := object(validation["final_revision"])
		if final != nil {
			nativeText(c, "最终修订 "+nativePDFStatus(final["status"])+" / 最后签名后修改 "+nativeValue(final["modified_after_last_signature"]))
		}
		if validation["signatures"] == nil {
			nativeText(c, "详细数字签名记录需要本人登录后读取。")
		} else if len(objects(validation["signatures"])) == 0 {
			if str(validation, "status") == "not_applicable" {
				nativeText(c, "数字签名校验不适用")
			} else {
				nativeText(c, "未发现可验证数字签名")
			}
		}
		for _, signature := range objects(validation["signatures"]) {
			for _, field := range nativeSignatureFields {
				value := nativeValue(signature[field.key])
				if field.status {
					value = nativePDFStatus(signature[field.key])
				}
				nativeText(c, field.label+"："+value)
			}
		}
		nativeText(c, "离线验证不判定可见签章存在；吊销证明未知。")
		return
	}
	switch str(row, "kind") {
	case "basic_information":
		nativePairs(c, []string{"任务", "招标编号", "评估日期", "固定提交", "本地准备版本", "检验运行", "检验时间", "检验作业", "覆盖状态"}, []string{str(row, "task_name"), str(row, "tender_number"), str(row, "assessment_date"), str(row, "submission_id"), str(row, "preparation_id"), str(row, "review_id"), str(row, "review_created_at"), str(row, "review_job_id"), str(row, "completion")})
		for _, key := range []string{"tender_documents", "bid_documents"} {
			for _, doc := range objects(row[key]) {
				nativeText(c, str(doc, "name")+" / "+str(doc, "id")+" / "+str(doc, "size_bytes")+" 字节 / "+str(doc, "sha256"))
			}
		}
	case "obligation":
		obligation := object(row["obligation"])
		nativeText(c, str(obligation, "text"))
		nativeText(c, "★条款 "+str(obligation, "starred")+" / ▲条款 "+str(obligation, "triangle"))
		bidCitationText(c, object(obligation["citation"]))
		nativeText(c, "投标响应尚未确定，没有已核验的投标原文引句；未覆盖 "+str(row, "reason_code"))
	case "usage":
		usage := object(row["usage"])
		nativeText(c, "模型 "+str(usage, "provider")+" / "+str(usage, "model")+" / 输入 tokens "+str(usage, "input_tokens")+" / 输出 tokens "+str(usage, "output_tokens"))
	case "cost":
		cost := object(row["cost"])
		for _, key := range []string{"calls", "llm_tokens", "ocr_pages", "search_requests", "browser_units", "unresolved_calls"} {
			nativeText(c, key+"："+str(cost, key))
		}
	default:
		nativeHeading(c, str(row, "label"))
		nativeText(c, str(row, "text"))
		if value, ok := row["value"]; ok {
			nativeText(c, nativeValue(value))
		}
		for _, value := range stringList(row["values"]) {
			nativeText(c, nativeValue(value))
		}
	}
}
