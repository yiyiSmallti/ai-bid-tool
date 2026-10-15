package main

import (
	"context"
	"errors"
	"fmt"
	"net/url"
	"strings"

	"github.com/egoist/mygo/ui"
)

var credentialPurposes = settingsOptions("standalone_llm", "Standalone / Eval", "vendor_search", "厂商搜索", "clef_workers_ai", "Clef Workers AI 模型令牌", "clef_gateway", "Clef 网关令牌")
var credentialStates = settingsOptions("active", "已启用", "disabled", "已停用", "removed", "已移除")
var credentialReasons = settingsOptions("setup", "初始配置", "scheduled_rotation", "定期轮换", "vendor_revoked", "厂商已吊销", "incident", "事件处理", "retired", "停止使用", "migration", "迁移")
var credentialRemovalReasons = settingsOptions("vendor_revoked", "厂商已吊销", "incident", "事件处理", "retired", "停止使用")
var credentialOutcomes = map[string]string{"passed": "认证通过", "auth_failed": "认证失败", "unsupported": "不支持认证探针", "timeout": "认证超时", "rate_limited": "测试次数超限", "unavailable": "认证服务不可用", "interrupted": "测试中断，结果未知"}

type credentialPage struct {
	managementState
	reads                                                      managementReads
	rows                                                       []Object
	table                                                      [][]string
	selected, detail, base                                     Object
	probes                                                     map[string]Object
	state, purpose, cursor, next                               string
	previous                                                   []string
	action, name, formPurpose, provider, endpoint, key, reason string
	active                                                     bool
}

func newCredentialPage(route Route) *credentialPage {
	return &credentialPage{managementState: managementState{route: route, title: "服务凭据"}, probes: make(map[string]Object), formPurpose: "standalone_llm", provider: "anthropic", endpoint: "https://api.anthropic.com", reason: "setup"}
}
func (p *credentialPage) Open(a *NativeApp) { p.load(a, "") }
func (p *credentialPage) Dirty() bool       { return p.dirty || p.key != "" }
func (p *credentialPage) Close() {
	p.managementState.Close()
	p.reads.close()
	p.rows, p.table = nil, nil
	p.selected, p.detail, p.base = nil, nil, nil
	p.probes = nil
	p.key, p.action, p.name, p.endpoint = "", "", "", ""
	p.previous = nil
}
func (p *credentialPage) failed(a *NativeApp, err error) {
	p.key = ""
	if mgDenied(err) {
		p.Close()
	}
	a.Error = settingsErrorText(err)
}
func (p *credentialPage) load(a *NativeApp, cursor string) {
	p.rows, p.table = nil, nil
	p.selected = nil
	p.next = ""
	p.cursor = cursor
	query := url.Values{"limit": {"100"}}
	if p.state != "" {
		query.Set("state", p.state)
	}
	if p.purpose != "" {
		query.Set("purpose", p.purpose)
	}
	if cursor != "" {
		query.Set("after_name", cursor)
	}
	settingsRead(a, &p.reads, "credentials", func(ctx context.Context) (Result, error) {
		return a.Client.CredentialRequest(ctx, "GET", "/platform/credentials?"+query.Encode(), nil)
	}, func(r Result) {
		for _, row := range r.Items {
			if p.state != "" && str(row, "state") != p.state || p.purpose != "" && str(row, "purpose") != p.purpose {
				a.Error = settingsFailures["invalid_response"]
				return
			}
		}
		p.rows = r.Items
		p.next = str(r.Data, "next_after_name")
		p.table = make([][]string, len(p.rows))
		for i, row := range p.rows {
			p.table[i] = []string{str(row, "name"), credentialPurposes.labelByCode[str(row, "purpose")], str(row, "provider") + " · " + str(row, "endpoint"), str(row, "fingerprint") + " · …" + str(row, "last_four"), credentialStates.labelByCode[str(row, "state")], fmt.Sprintf("修订 %d · 密钥 %d", integer(row, "revision"), integer(row, "secret_version"))}
		}
	}, func(err error) { p.failed(a, err) })
}
func (p *credentialPage) cancel() { p.key = ""; p.action = ""; p.base = nil; p.dirty = false }
func (p *credentialPage) create() {
	p.cancel()
	p.action = "create"
	p.name = ""
	p.formPurpose = "standalone_llm"
	p.provider = "anthropic"
	p.endpoint = "https://api.anthropic.com"
	p.active = false
	p.reason = "setup"
}
func (p *credentialPage) choosePurpose() {
	switch p.formPurpose {
	case "clef_workers_ai":
		p.provider = "cloudflare"
		p.endpoint = "https://gateway.ai.cloudflare.com"
	case "clef_gateway":
		p.provider = "cloudflare"
		p.endpoint = "https://api.cloudflare.com/client/v4"
	case "vendor_search":
		p.provider = "perplexity"
		p.endpoint = "https://api.perplexity.ai"
	default:
		if p.provider == "cloudflare" || p.provider == "perplexity" {
			p.provider = "anthropic"
			p.endpoint = "https://api.anthropic.com"
		}
	}
}
func (p *credentialPage) show(a *NativeApp, row Object, action string) {
	id := str(row, "id")
	p.key = ""
	settingsRead(a, &p.reads, "credential-detail", func(ctx context.Context) (Result, error) {
		return a.Client.CredentialRequest(ctx, "GET", "/platform/credentials/"+url.PathEscape(id), nil)
	}, func(r Result) {
		value := object(r.Data["credential"])
		if str(value, "id") != id {
			a.Error = settingsFailures["invalid_response"]
			return
		}
		p.detail = value
		if action != "" {
			p.cancel()
			p.base = value
			p.action = action
			p.active = str(value, "state") != "active"
			p.reason = "setup"
			if action == "replace" {
				p.reason = "scheduled_rotation"
			}
			if action == "remove" {
				p.reason = "retired"
			}
		}
	}, func(err error) { p.failed(a, err) })
}
func credentialValidKey(value string) bool {
	if len(value) < 16 || len(value) > 4096 {
		return false
	}
	for _, b := range []byte(value) {
		if b < 33 || b > 126 {
			return false
		}
	}
	return true
}
func (p *credentialPage) save(a *NativeApp) {
	if p.action == "" || a.Busy {
		return
	}
	if (p.action == "create" || p.action == "replace") && !credentialValidKey(p.key) {
		p.key = ""
		a.Error = "密钥须为 16 到 4096 个无空白的可打印 ASCII 字符，请重新输入。"
		return
	}
	path := "/platform/credentials"
	body := Object{"reason": p.reason}
	action := p.action
	if action == "create" {
		if !settingsCredentialNamePattern.MatchString(p.name) || !settingsHTTPS(p.endpoint) || strings.TrimSpace(p.endpoint) == "" {
			p.key = ""
			a.Error = settingsFailures["invalid_input"]
			return
		}
		body["name"], body["purpose"], body["provider"], body["endpoint"], body["active"], body["api_key"] = p.name, p.formPurpose, p.provider, p.endpoint, p.active, p.key
	} else {
		if p.base == nil || str(p.base, "state") == "removed" {
			p.key = ""
			return
		}
		suffix := action
		if suffix == "set-active" {
			suffix = "active"
		}
		path += "/" + url.PathEscape(str(p.base, "id")) + "/" + suffix
		body["expected_revision"] = p.base["revision"]
		if action == "replace" {
			body["api_key"] = p.key
		}
		if action == "set-active" {
			body["active"] = p.active
		}
	}
	p.key = ""
	settingsWrite(a, func(ctx context.Context) (Result, error) {
		defer delete(body, "api_key")
		return a.Client.CredentialRequest(ctx, "POST", path, body)
	}, func(r Result) {
		value := object(r.Data["credential"])
		if action != "create" && str(value, "id") != str(p.base, "id") {
			a.Error = settingsFailures["invalid_response"]
			return
		}
		p.detail = value
		p.cancel()
		a.Notice = "凭据已保存。"
		p.load(a, p.cursor)
	}, func(err error) {
		p.failed(a, err)
		if !p.closed && (settingsCode(err) == "revision_conflict" || mgConflict(err)) {
			p.load(a, "")
			a.Error = "凭据操作结果尚未确定或修订已变化；密钥已清空。请核对列表后重新打开操作，不会自动重试。"
		}
	})
}
func (p *credentialPage) probe(a *NativeApp, row Object) {
	id, revision := str(row, "id"), integer(row, "revision")
	install := func(probe Object) bool {
		if !settingsCredentialProbe(probe) || str(probe, "credential_id") != id || integer(probe, "tested_revision") != revision {
			return false
		}
		p.probes[id] = probe
		return true
	}
	settingsWrite(a, func(ctx context.Context) (Result, error) {
		return a.Client.CredentialRequest(ctx, "POST", "/platform/credentials/"+url.PathEscape(id)+"/test", Object{"expected_revision": revision, "mode": "authentication"})
	}, func(r Result) {
		if !install(object(r.Data["probe"])) {
			a.Error = settingsFailures["invalid_response"]
		}
	}, func(err error) {
		var api *APIError
		if errors.As(err, &api) {
			install(object(api.Result.Data["probe"]))
		}
		p.failed(a, err)
		if settingsCode(err) == "revision_conflict" && !p.closed {
			p.load(a, p.cursor)
		}
	})
}
func credentialConsumers(c *ui.Context, value Object) {
	nativeHeading(c, "消费对象")
	if len(objects(value["consumers"])) == 0 {
		nativeText(c, "当前没有消费对象。")
	}
	for _, consumer := range objects(value["consumers"]) {
		selected := "未选用"
		if mgBool(consumer["selected"]) {
			selected = "已选用"
		}
		nativeText(c, credentialPurposes.labelByCode[str(consumer, "service")]+" · "+selected)
	}
}
func (p *credentialPage) View(c *ui.Context, a *NativeApp) {
	if p.closed {
		nativeText(c, "凭据权限或会话已变化，请重新打开。")
		return
	}
	nativeText(c, "管理出站服务凭据。密钥只在提交时传送，保存后仅显示指纹与末四位。")
	ui.Row(c).Wrap().Gap(8).Children(func() {
		nativeButton(c, "刷新凭据").Disabled(a.Busy).OnClick(func() { p.load(a, p.cursor) })
		nativePrimaryButton(c, "添加凭据").Disabled(a.Busy).OnClick(p.create)
	})
	if p.action != "" {
		p.viewForm(c, a)
	}
	ui.Row(c).Gap(12).Disabled(a.Busy).Children(func() {
		state := credentialStates.labelByCode[p.state]
		if state == "" {
			state = "全部状态"
		}
		if mgChoice(c, "状态筛选", &state, []string{"全部状态", "已启用", "已停用", "已移除"}) {
			p.state = credentialStates.codeByLabel[state]
			p.previous = nil
			p.load(a, "")
		}
		purpose := credentialPurposes.labelByCode[p.purpose]
		if purpose == "" {
			purpose = "全部用途"
		}
		if mgChoice(c, "用途筛选", &purpose, []string{"全部用途", "Standalone / Eval", "厂商搜索", "Clef Workers AI 模型令牌", "Clef 网关令牌"}) {
			p.purpose = credentialPurposes.codeByLabel[purpose]
			p.previous = nil
			p.load(a, "")
		}
	})
	nativeTable(c, []string{"名称", "用途", "服务商 / 端点", "指纹 / 末四位", "状态", "修订"}, p.table, func(i int) { p.selected = p.rows[i] })
	if p.selected != nil {
		row := p.selected
		ui.Row(c.Key("credential-actions")).Wrap().Gap(8).Disabled(a.Busy).Children(func() {
			nativeButton(c, "查看凭据详情").OnClick(func() { p.show(a, row, "") })
			if str(row, "state") != "removed" {
				nativeButton(c, "替换密钥").OnClick(func() { p.show(a, row, "replace") })
				title := "启用"
				if str(row, "state") == "active" {
					title = "停用"
				}
				button := nativeButton
				if str(row, "state") == "active" {
					button = nativeDangerButton
				}
				button(c, title+"凭据").OnClick(func() { p.show(a, row, "set-active") })
				nativeButton(c, "测试认证").OnClick(func() { p.probe(a, row) })
				nativeDangerButton(c, "移除凭据").OnClick(func() { p.show(a, row, "remove") })
			}
		})
		if probe := p.probes[str(row, "id")]; probe != nil {
			nativeText(c, fmt.Sprintf("%s · 测试第 %d 版 · %d ms", credentialOutcomes[str(probe, "outcome")], integer(probe, "tested_revision"), integer(probe, "duration_ms")))
			if integer(probe, "retry_after_seconds") > 0 {
				nativeText(c, fmt.Sprintf("%d 秒后可重试", integer(probe, "retry_after_seconds")))
			}
			nativeText(c, "仅验证认证元数据，不证明模型授权、额度或推理能力。")
		}
	}
	nativePager(c, len(p.previous)+1, p.next != "", a.Busy, "上一页凭据", "下一页凭据",
		func() {
			last := len(p.previous) - 1
			cursor := p.previous[last]
			p.previous = p.previous[:last]
			p.load(a, cursor)
		},
		func() {
			if len(p.previous) >= 50 {
				p.previous = p.previous[1:]
			}
			p.previous = append(p.previous, p.cursor)
			p.load(a, p.next)
		})
	if p.detail != nil {
		ui.Divider(c)
		nativeHeading(c, str(p.detail, "name")+" · 凭据详情")
		mgValue(c, "凭据 ID", str(p.detail, "id"))
		mgValue(c, "更新时间", str(p.detail, "updated_at"))
		mgValue(c, "版本", fmt.Sprintf("修订 %d · 密钥 %d · %s", integer(p.detail, "revision"), integer(p.detail, "secret_version"), credentialStates.labelByCode[str(p.detail, "state")]))
		credentialConsumers(c, p.detail)
		nativeButton(c, "关闭凭据详情").OnClick(func() { p.detail = nil })
	}
	nativeText(c, "一次性环境迁移使用 bid platform credential import-env 的受保护文件入口。")
}
func (p *credentialPage) viewForm(c *ui.Context, a *NativeApp) {
	nativeHeading(c, map[string]string{"create": "添加凭据", "replace": "替换密钥", "set-active": "修改启用状态", "remove": "移除凭据"}[p.action])
	ui.Column(c.Key("credential-form")).Gap(8).Disabled(a.Busy).Children(func() {
		if p.action == "create" {
			settingsDirtyField(c, "凭据名称", &p.name, false, &p.dirty)
			if settingsChoice(c, "用途", &p.formPurpose, credentialPurposes) {
				p.choosePurpose()
				p.dirty = true
			}
			fixed := p.formPurpose != "standalone_llm"
			ui.Column(c.Key("credential-routing")).Disabled(fixed).Children(func() {
				if mgChoice(c, "服务商", &p.provider, []string{"anthropic", "openai"}) {
					p.dirty = true
				}
				settingsDirtyField(c, "HTTPS Base URL", &p.endpoint, false, &p.dirty)
			})
			if fixed {
				mgValue(c, "固定服务商与端点", p.provider+" · "+p.endpoint)
			}
			nativeText(c, "名称、用途、服务商和端点保存后不可更改；更换端点需要创建新凭据。")
			if ui.Checkbox(c, &p.active, "创建时启用（默认停用）").Changed() {
				p.dirty = true
			}
		}
		if p.action == "create" || p.action == "replace" {
			ui.Text(c, "API 密钥（只写）")
			if ui.TextInput(c, &p.key).Password().Label("API 密钥（只写）").Changed() {
				p.dirty = true
			}
		}
		if p.action == "replace" {
			nativeText(c, "替换后下一次外发请求使用新密钥；已开始的请求仍可能使用旧值。原密钥无法恢复。")
		}
		if p.action == "set-active" || p.action == "remove" {
			impact := "停用后，引用它的下一次请求将失败。"
			if p.action == "remove" {
				impact = "移除会清空在线密钥，名称不能复用；引用它的后续调用将失败。"
			} else if p.active {
				impact = "启用后，引用它的消费对象可发起新请求。"
			}
			nativeText(c, impact)
			credentialConsumers(c, p.base)
		}
		reasons := credentialReasons
		if p.action == "remove" {
			reasons = credentialRemovalReasons
		}
		if settingsChoice(c, "原因", &p.reason, reasons) {
			p.dirty = true
		}
		label := "保存凭据"
		if p.action == "remove" {
			label = "确认移除"
		}
		nativePrimaryButton(c, label).OnClick(func() { p.save(a) })
		nativeButton(c, "取消凭据编辑").OnClick(p.cancel)
	})
}
