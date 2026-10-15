package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"sort"
	"strings"

	"github.com/egoist/mygo/ui"
)

type bidOutboundPanel struct {
	task, id, reason, bidders, staff, next string
	preview                                Object
	grants                                 []Object
	selected                               map[string]bool
	consent                                bool
	serial                                 uint64
}

func (p *bidOutboundPanel) Dirty() bool { return p.reason != "" || p.bidders != "" || p.staff != "" }
func (p *bidOutboundPanel) Close() {
	p.serial++
	p.preview = nil
	p.grants = nil
	p.selected = nil
	p.reason = ""
	p.bidders = ""
	p.staff = ""
	p.consent = false
}
func (p *bidOutboundPanel) Load(a *NativeApp, task, id string) {
	p.Close()
	p.task = task
	p.id = id
	p.selected = map[string]bool{}
	serial := p.serial
	a.Do(func(ctx context.Context) (func(), error) {
		grants, err := a.Client.Request(ctx, "GET", "/bid-submissions/"+id+"/outbound-authorizations?cursor=0&limit=100", nil)
		if err != nil {
			return nil, err
		}
		for _, grant := range grants.Items {
			if str(grant, "task_id") != task || str(grant, "submission_id") != id {
				return nil, errors.New("授权记录范围不匹配")
			}
		}
		cursor := 0
		var fixed Object
		pages := []Object{}
		seen := map[string]bool{}
		for {
			r, err := a.Client.Request(ctx, "GET", "/bid-submissions/"+id+"/redaction?cursor="+pageCursor(cursor)+"&limit=25", nil)
			if err != nil {
				return nil, err
			}
			data := r.Data
			if str(data, "submission_id") != id {
				return nil, errors.New("脱敏页面范围不匹配")
			}
			if fixed != nil {
				for _, key := range []string{"expected_redaction_manifest_sha256", "expected_revision", "provider_bindings_sha256"} {
					if str(fixed, key) != str(data, key) {
						return nil, errors.New("脱敏页面读取期间已变化，请刷新")
					}
				}
			}
			fixed = data
			for _, page := range objects(data["pages"]) {
				pid := str(page, "page_id")
				if pid == "" || seen[pid] {
					return nil, errors.New("脱敏页面存在重复标识")
				}
				seen[pid] = true
				pages = append(pages, page)
			}
			if data["next_cursor"] == nil {
				break
			}
			next := integer(data, "next_cursor")
			if next <= cursor {
				return nil, errors.New("脱敏页面游标无效")
			}
			cursor = next
		}
		fixed["pages"] = pages
		return func() {
			if serial != p.serial {
				return
			}
			p.preview = fixed
			p.grants = grants.Items
			p.next = pageCursor(grants.Data["next_cursor"])
		}, nil
	})
}
func (p *bidOutboundPanel) View(c *ui.Context, a *NativeApp, changed func()) {
	nativeHeading(c, "逐页文本外发授权")
	nativeText(c, "仅授权核对后的脱敏文本，固定本次准备、脱敏规则与模型配置。报价页、报价分类未确定页及图片不能通过此授权外发。")
	if p.preview != nil {
		fixed := p.preview
		for _, code := range stringList(fixed["blockers"]) {
			nativeText(c, code)
		}
		nativeField(c, "投标单位补充名单（每行一项）", &p.bidders, true)
		nativeField(c, "人员补充名单（每行一项）", &p.staff, true)
		nativeText(c, "保存将替换完整人工补充名单；未填写项会移除。当前修订 "+str(fixed, "human_name_revision"))
		nativeButton(c, "保存完整补充名单").Disabled(a.Busy).OnClick(func() {
			bidders, staff := uniqueLines(p.bidders), uniqueLines(p.staff)
			if len(bidders) > 500 || len(staff) > 500 {
				a.Error = "每类补充名单最多 500 项"
				return
			}
			for _, name := range append(append([]string{}, bidders...), staff...) {
				if len([]rune(name)) > 20000 {
					a.Error = "名单单项超过长度限制"
					return
				}
			}
			a.Request("POST", "/bid-submissions/"+p.id+"/redaction/names", Object{"request_id": requestID(), "expected_revision": fixed["human_name_revision"], "bidder_names": bidders, "staff_names": staff}, func(Result) { changed(); p.Load(a, p.task, p.id) })
		})
		chosen := []Object{}
		for _, page := range objects(fixed["pages"]) {
			id := str(page, "page_id")
			checked := p.selected[id]
			eligible := page["outbound_eligible"] == true
			if ui.Checkbox(c.Key(id), &checked, str(page, "role")+"第 "+str(page, "page")+" 页").Disabled(a.Busy || !eligible).Changed() {
				p.selected[id] = checked
				p.consent = false
			}
			nativeText(c, "报价分类："+str(page, "price_classification"))
			for _, note := range stringList(page["notes"]) {
				nativeText(c, note)
			}
			nativeText(c, str(page, "sanitized_text"))
			nativeText(c, "脱敏文本 SHA-256 "+str(page, "sanitized_text_sha256"))
			if checked && eligible {
				chosen = append(chosen, Object{"page_id": id, "sanitized_text_sha256": page["sanitized_text_sha256"]})
			}
		}
		if nativeField(c, "授权或撤销理由", &p.reason, true) {
			p.consent = false
		}
		ui.Checkbox(c, &p.consent, "已逐页核对所选脱敏文本，同意用于标书文本检验")
		nativePrimaryButton(c, "授权所选页面").Disabled(a.Busy || !p.consent || len(chosen) == 0 || strings.TrimSpace(p.reason) == "" || len(stringList(fixed["blockers"])) > 0).OnClick(func() {
			sort.Slice(chosen, func(i, j int) bool { return str(chosen[i], "page_id") < str(chosen[j], "page_id") })
			encoded, err := json.Marshal(chosen)
			if err != nil {
				a.Error = err.Error()
				return
			}
			hash := sha256.Sum256(encoded)
			body := Object{"request_id": requestID(), "authorized_sanitized_context_sha256": hex.EncodeToString(hash[:]), "pages": chosen, "purposes": []string{"bid_review_text"}, "allowed_capabilities": []string{"llm"}, "privacy_reviewed": true, "allow_external": true, "reason": strings.TrimSpace(p.reason)}
			for _, key := range []string{"expected_revision", "expected_authorization_id", "expected_submission_manifest_sha256", "expected_preparation_input_hash", "expected_redaction_manifest_sha256", "provider_bindings_sha256"} {
				body[key] = fixed[key]
			}
			p.consent = false
			a.Request("POST", "/bid-submissions/"+p.id+"/outbound-authorizations", body, func(Result) { changed(); p.Load(a, p.task, p.id) })
		})
		if len(p.grants) > 0 && p.grants[0]["allow_external"] == true {
			nativeDangerButton(c, "撤销外发授权").Disabled(a.Busy || strings.TrimSpace(p.reason) == "").OnClick(func() {
				current := p.grants[0]
				a.Request("POST", "/bid-submissions/"+p.id+"/outbound-authorizations/revoke", Object{"request_id": requestID(), "expected_revision": current["revision"], "expected_authorization_id": current["id"], "reason": strings.TrimSpace(p.reason)}, func(Result) { changed(); p.Load(a, p.task, p.id) })
			})
		}
	}
	for _, grant := range p.grants {
		nativeText(c, "授权修订 "+str(grant, "revision")+" / 允许外发 "+str(grant, "allow_external")+" / 当前有效 "+str(grant, "current")+" / "+str(grant, "authorized_at"))
	}
	if p.next != "" {
		nativeButton(c, "更多外发授权记录").Disabled(a.Busy).OnClick(func() {
			cursor := p.next
			a.Request("GET", "/bid-submissions/"+p.id+"/outbound-authorizations?cursor="+cursor+"&limit=100", nil, func(r Result) {
				for _, grant := range r.Items {
					if str(grant, "task_id") != p.task || str(grant, "submission_id") != p.id {
						a.Error = "授权记录范围不匹配"
						return
					}
				}
				p.grants = append(p.grants, r.Items...)
				p.next = pageCursor(r.Data["next_cursor"])
			})
		})
	}
	nativeButton(c, "刷新外发授权").Disabled(a.Busy || p.id == "").OnClick(func() { p.Load(a, p.task, p.id) })
}
func uniqueLines(text string) []string {
	out := []string{}
	seen := map[string]bool{}
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if line != "" && !seen[line] {
			seen[line] = true
			out = append(out, line)
		}
	}
	return out
}
