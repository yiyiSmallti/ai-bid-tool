package main

import (
	"fmt"

	"github.com/egoist/mygo/ui"
)

func nativeProbability(value any) string {
	number, ok := value.(float64)
	if !ok || number < 0 || number > 1 || number != number {
		return "未确定"
	}
	return fmt.Sprintf("%.1f%%", number*100)
}
func bidPresenceLocation(c *ui.Context, location Object) {
	position := "投标页码未确定"
	if page := integer(location, "page"); page > 0 {
		position = fmt.Sprintf("投标第 %d 页", page)
	}
	status := "位置未解决"
	code := str(location, "status")
	if containsString([]string{"triage_present", "triage_absent", "triage_uncertain"}, code) {
		status = nativeLabel(code)
	}
	nativeText(c, position+" · "+status)
	if group := str(location, "group_id"); group != "" {
		nativeText(c, "骑缝组 "+group)
	}
	if location["probability_yes"] != nil {
		nativeText(c, "存在概率 "+nativeProbability(location["probability_yes"]))
	}
	for mark, value := range object(location["presence_probabilities"]) {
		nativeText(c, nativeLabel(mark)+"存在概率 "+nativeProbability(value))
	}
	nativeText(c, "需人工确认；单位归属、日期与指定位置仍未解决")
}

var nativeSignatureFields = []struct {
	key, label string
	status     bool
}{
	{"signature_index", "数字签名序号", false}, {"coverage_status", "字节范围覆盖", true}, {"crypto_status", "密码学完整性", true}, {"content_digest_status", "内容摘要", true}, {"signature_value_status", "签名值", true}, {"modified_after_signing", "签后有修改", false}, {"final_revision_covered", "覆盖最终修订", false}, {"certificate_validity_status", "证书有效性", true}, {"trust_status", "本地信任", true}, {"revocation_status", "吊销证明", true}, {"timestamp_status", "时间戳", true},
}
