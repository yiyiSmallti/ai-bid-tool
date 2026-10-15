package main

import (
	"fmt"
	"regexp"
	"strings"
)

// Wire values stay unchanged; this is the shared product-language projection.
var nativeLabels = map[string]string{
	"uploaded": "已上传", "parsing": "解析中", "parsed": "已解析", "page": "按页码", "block": "按段落 / 单元格",
	"fatal": "废标风险", "high": "高风险", "medium": "中风险", "low": "低风险", "info": "提示", "open": "待审查", "dismissed": "已驳回", "confirmed": "已确认", "unconfirmed": "待确认", "legacy_unconfirmed": "历史未确认", "invalidated": "已失效", "responded": "已响应", "deviation": "偏离", "missing": "缺失", "unknown": "未确定", "rejection": "废标风险", "lost_points": "扣分风险", "both": "废标与扣分风险", "uncertain": "影响未确定", "rule": "本地规则", "rules": "规则检查", "combined": "联合检查", "model": "模型", "manual": "人工", "classify": "分类", "dismiss": "驳回", "reopen": "重新打开", "confirm": "确认", "commercial": "商务", "technical": "技术", "qualification": "资格", "scoring": "评分", "substantive": "实质性", "comply_only": "仅需遵守", "gaps": "缺口", "commitment": "承诺", "evidence": "证据", "none": "无偏离", "positive": "正偏离", "negative": "负偏离", "applies": "适用", "not_applicable": "不适用", "alternative": "有替代签章方式", "valid": "有效", "invalid": "无效", "unsigned": "未发现数字签名", "trusted": "链到本地信任根", "untrusted": "未链到本地信任根", "expired": "已过期", "not_for_signing": "证书用途不含签名",
	"triage_present": "初筛存在", "triage_absent": "初筛未发现", "triage_uncertain": "初筛未确定", "company_seal": "单位公章", "signature": "签字", "legal_representative_signature": "法定代表人签字", "authorized_agent_signature": "授权代理人签字", "personal_seal": "个人印章", "date": "日期", "seam_seal": "骑缝章", "every_page_electronic_seal": "逐页电子章", "pdf_digital_signature": "PDF 数字签名",
	"unsupported": "不支持", "whole_revision": "覆盖完整签署修订", "not_yet_valid": "尚未生效", "absent": "未提供", "unmodified": "无后续修改", "incremental_or_appended_bytes": "存在增量更新或追加字节", "additional_signatures": "后续新增签名", "permitted_changes": "存在允许的认证修改",
	"queued": "排队中", "running": "处理中", "succeeded": "已完成", "failed": "失败", "cancelled": "已取消", "pending": "待处理", "ready": "已就绪", "current": "当前有效", "stale": "已过期", "complete": "完整", "partial": "部分完成", "candidate": "候选", "rejected": "已拒绝", "draft": "草稿", "submitted": "已提交", "approved": "已批准", "archived": "已归档", "new": "新建", "final": "正式件", "preview": "预览件", "commercial_technical": "商务技术文件", "price": "报价文件", "declaration": "声明文件", "other": "其他材料", "tender": "招标文件", "bid": "投标文件",
	"overall": "总体", "summary": "概要", "sections": "章节", "items": "条目", "coverage": "覆盖情况", "blockers": "阻塞事项", "findings": "检查问题", "notices": "提示事项", "basic_information": "基本信息", "compliance": "废标判定", "signatures": "签章校验", "risks": "高风险缺陷", "scores": "得分预估", "remediation": "补救清单", "methodology": "检验说明", "disqualification_risk": "废标风险", "deduction_risk": "扣分风险", "sum": "求和", "weighted_sum": "加权求和", "capped_sum": "封顶求和", "formula": "公式", "non_additive": "非加和",
	"product": "产品", "feature": "功能", "certificate": "证照", "org_profile": "单位资料", "image_region": "图片区域", "prototype": "原型", "real": "真实材料", "simulated": "模拟材料", "library": "资源库", "name": "名称", "description": "说明", "vendor": "厂家", "model_name": "型号", "scope": "适用范围", "legal_name": "单位名称", "business_scope": "经营范围", "true": "是", "false": "否",
}

func nativeLabel(code string) string {
	if label, ok := nativeLabels[code]; ok {
		return label
	}
	return code
}
func nativeWarning(code string) string {
	switch {
	case code == "Product metadata is a declaration; it is not evidence of parameter compliance":
		return "产品资料仅为声明，不能证明参数符合招标要求。"
	case code == "Implementation status is a declaration; evidence has not been verified":
		return "功能实现状态仅为声明，支持证据尚未经核验。"
	case code == "Certificate metadata and dates are declarations; authenticity, legality and compliance have not been verified":
		return "证照资料与日期仅为声明，真实性、合法性与合规性尚未经核验。"
	case code == "Certificate file is user-supplied; authenticity, eligibility and metadata matching have not been verified":
		return "证照文件由本人提供，真实性、资格适用性与资料匹配情况尚未经核验。"
	case code == "Company metadata is a declaration; authenticity, performance and qualification have not been verified":
		return "单位资料仅为声明，真实性、履约能力与资格尚未经核验。"
	case code == "Template metadata is a declaration; upload validation does not establish export approval":
		return "模板资料仅为声明，上传校验通过不代表已批准导出。"
	case code == "Template metadata is a declaration; chapter matching and export adaptation have not been verified":
		return "模板资料仅为声明，章节匹配与导出适配尚未经核验。"
	case code == "User-supplied file; archive review does not confirm evidence":
		return "此文件由本人提供；档案审阅不等同于证据确认。"
	case code == "Memory is guidance, never evidence or human confirmation":
		return "记忆仅供参考，不能替代证据或人工确认。"
	case code == "historical_extraction" || strings.HasPrefix(code, "historical_extraction:"):
		message := "正在查看历史抽取集合，已有更新的抽取结果"
		if _, id, ok := strings.Cut(code, ":"); ok {
			message += "（" + id + "）"
		}
		return message
	case code == "manual_scope:extraction_completeness_not_asserted":
		return "当前集合来自人工补录，不声明已完整覆盖招标文件"
	case strings.HasPrefix(code, "content_blocked:"):
		return "模型服务的内容审核拦截了 " + strings.TrimPrefix(code, "content_blocked:") + " 段原文，这些段落未抽取，请在拒绝回执中查看位置并人工补录"
	case strings.HasPrefix(code, "extraction_rejected:"):
		return "抽取有 " + strings.TrimPrefix(code, "extraction_rejected:") + " 项被拒绝，请核对拒绝回执与遗漏要求"
	default:
		return code
	}
}

var nativeHTTPStatusPattern = regexp.MustCompile(`(?i)\bHTTP(?:\s+status)?\s*[:=]?\s*([1-5][0-9]{2})\b`)

// Keep provider diagnostics useful without exposing the raw upstream message.
func nativeJobError(problem Object) string {
	code := str(problem, "code")
	if code == "" && str(problem, "message") == "" {
		return "—"
	}
	label := "作业执行失败"
	if known, ok := settingsFailures[code]; ok {
		label = strings.TrimSuffix(known, "。")
	}
	match := nativeHTTPStatusPattern.FindStringSubmatch(str(problem, "message"))
	if code == "provider_unavailable" {
		switch {
		case len(match) == 2 && match[1] == "429":
			label = "模型服务限流"
		case len(match) == 2 && match[1][0] == '4':
			label = "模型服务拒绝了请求"
		default:
			label = "模型服务暂不可用"
		}
	}
	if len(match) == 2 {
		label += "（HTTP " + match[1] + "）"
	}
	return label
}
func nativeWarnings(codes []string) string {
	labels := make([]string, len(codes))
	for i, code := range codes {
		labels[i] = nativeWarning(code)
	}
	return strings.Join(labels, "\n")
}
func nativeValue(value any) string {
	if value == nil {
		return "未知"
	}
	switch v := value.(type) {
	case bool:
		if v {
			return "是"
		}
		return "否"
	case string:
		if v == "" {
			return "未知"
		}
		if local, ok := nativeTime(v); ok {
			return local
		}
		return nativeLabel(v)
	default:
		return fmt.Sprint(value)
	}
}
func nativeStatus(value any) string {
	if value == nil {
		return "未知"
	}
	label, ok := nativeLabels[fmt.Sprint(value)]
	if !ok {
		return "未确定"
	}
	return label
}
func nativePDFStatus(value any) string {
	if value == "none" {
		return "无后续修改"
	}
	return nativeStatus(value)
}
func nativeMarkList(value any) string {
	marks := stringList(value)
	labels := make([]string, len(marks))
	for i, mark := range marks {
		labels[i] = nativeLabel(mark)
	}
	if len(labels) == 0 {
		return "未确定"
	}
	return strings.Join(labels, "、")
}

var nativeReportLabels = func() map[string]string {
	labels := make(map[string]string, len(bidReportSections))
	for i, key := range bidReportSections {
		labels[key] = bidReportTitles[i]
	}
	return labels
}()
