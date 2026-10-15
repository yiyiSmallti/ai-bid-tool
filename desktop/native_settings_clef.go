package main

import (
	"context"
	"errors"
	"fmt"
	"regexp"

	"github.com/egoist/mygo/ui"
)

var clefAccountPattern = regexp.MustCompile(`^[a-f0-9]{32}$`)
var clefGatewayPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9_-]{0,63}$`)

type clefPage struct {
	managementState
	reads                                 managementReads
	config                                Object
	blockers                              []string
	workers, gateways                     []Object
	workerOptions, gatewayOptions         settingsChoices
	account, gateway, workerID, gatewayID string
	enabled                               bool
}

func newClefPage(route Route) *clefPage {
	return &clefPage{managementState: managementState{route: route, title: "Clef 初筛配置"}, enabled: true}
}
func (p *clefPage) Open(a *NativeApp) { p.load(a) }
func (p *clefPage) Close() {
	p.managementState.Close()
	p.reads.close()
	p.config = nil
	p.blockers = nil
	p.workers, p.gateways = nil, nil
	p.workerOptions, p.gatewayOptions = settingsChoices{}, settingsChoices{}
	p.account, p.gateway, p.workerID, p.gatewayID = "", "", "", ""
}
func (p *clefPage) failed(a *NativeApp, err error) {
	if mgDenied(err) {
		p.Close()
	}
	a.Error = settingsErrorText(err)
}
func settingsClefData(value Object) bool {
	if !settingsKeys(value, "config", "blockers") || !isArray(value["blockers"]) {
		return false
	}
	list, _ := value["blockers"].([]any)
	if len(list) > 20 {
		return false
	}
	for _, code := range list {
		if !settingsString(code, 1, 100) {
			return false
		}
	}
	if value["config"] == nil {
		return true
	}
	config := object(value["config"])
	if !settingsKeys(config, "expected_revision", "account_id", "gateway_id", "enabled", "workers_credential_id", "gateway_credential_id", "revision", "workers_credential_revision", "gateway_credential_revision", "gateway_check", "updated_at") || config["expected_revision"] != nil || !clefAccountPattern.MatchString(str(config, "account_id")) || !clefGatewayPattern.MatchString(str(config, "gateway_id")) || !settingsBoolean(config["enabled"]) || !settingsUUID(str(config, "workers_credential_id")) || !settingsUUID(str(config, "gateway_credential_id")) || str(config, "workers_credential_id") == str(config, "gateway_credential_id") || !settingsInteger(config["revision"], 1, 1<<30) || !settingsInteger(config["workers_credential_revision"], 1, 1<<30) || !settingsInteger(config["gateway_credential_revision"], 1, 1<<30) || !settingsTime(config["updated_at"]) {
		return false
	}
	if config["gateway_check"] != nil {
		check := object(config["gateway_check"])
		if !settingsKeys(check, "gateway_id", "authentication", "collect_logs", "logpush", "cache_ttl", "gateway_retries", "rate_limit_requests", "rate_limit_seconds", "checked_at") || str(check, "gateway_id") != str(config, "gateway_id") || check["authentication"] != true || check["collect_logs"] != false || check["logpush"] != false || check["gateway_retries"] != false || !settingsInteger(check["cache_ttl"], 0, 0) || !settingsInteger(check["rate_limit_requests"], 1, 1<<30) || !settingsInteger(check["rate_limit_seconds"], 1, 1<<30) || !settingsTime(check["checked_at"]) {
			return false
		}
	}
	return true
}
func (p *clefPage) apply(a *NativeApp, data Object) bool {
	if !settingsClefData(data) {
		a.Error = settingsFailures["invalid_response"]
		return false
	}
	p.config = object(data["config"])
	p.blockers = stringList(data["blockers"])
	if p.config != nil {
		p.account, p.gateway, p.workerID, p.gatewayID = str(p.config, "account_id"), str(p.config, "gateway_id"), str(p.config, "workers_credential_id"), str(p.config, "gateway_credential_id")
		p.enabled = mgBool(p.config["enabled"])
	}
	p.dirty = false
	return true
}
func (p *clefPage) load(a *NativeApp) {
	p.workers, p.gateways = nil, nil
	p.workerOptions, p.gatewayOptions = settingsChoices{}, settingsChoices{}
	settingsRead(a, &p.reads, "clef-config", func(ctx context.Context) (Result, error) { return a.Client.Request(ctx, "GET", "/platform/clef", nil) }, func(r Result) { p.apply(a, r.Data) }, func(err error) { p.failed(a, err) })
	for _, purpose := range []string{"clef_workers_ai", "clef_gateway"} {
		purpose := purpose
		settingsRead(a, &p.reads, purpose, func(ctx context.Context) (Result, error) {
			return a.Client.CredentialRequest(ctx, "GET", "/platform/credentials?limit=100&purpose="+purpose+"&state=active", nil)
		}, func(r Result) {
			if r.Data["next_after_name"] != nil {
				a.Error = settingsFailures["invalid_response"]
				return
			}
			pairs := make([]string, 0, len(r.Items)*2)
			for _, row := range r.Items {
				if str(row, "purpose") != purpose || str(row, "provider") != "cloudflare" || str(row, "state") != "active" {
					a.Error = settingsFailures["invalid_response"]
					return
				}
				pairs = append(pairs, str(row, "id"), fmt.Sprintf("%s · 第 %d 版", str(row, "name"), integer(row, "revision")))
			}
			if purpose == "clef_workers_ai" {
				p.workers = r.Items
				p.workerOptions = settingsOptions(pairs...)
			} else {
				p.gateways = r.Items
				p.gatewayOptions = settingsOptions(pairs...)
			}
		}, func(err error) { p.failed(a, err) })
	}
}
func (p *clefPage) ready() bool {
	if !clefAccountPattern.MatchString(p.account) || !clefGatewayPattern.MatchString(p.gateway) || p.workerID == p.gatewayID {
		return false
	}
	return p.workerOptions.labelByCode[p.workerID] != "" && p.gatewayOptions.labelByCode[p.gatewayID] != ""
}
func (p *clefPage) save(a *NativeApp) {
	if !p.ready() || a.Busy {
		return
	}
	var revision any
	if p.config != nil {
		revision = p.config["revision"]
	}
	body := Object{"account_id": p.account, "gateway_id": p.gateway, "enabled": p.enabled, "workers_credential_id": p.workerID, "gateway_credential_id": p.gatewayID, "expected_revision": revision}
	settingsWrite(a, func(ctx context.Context) (Result, error) { return a.Client.Request(ctx, "PUT", "/platform/clef", body) }, func(r Result) {
		if p.apply(a, r.Data) {
			a.Notice = "Clef 配置已保存，请运行网关安全检查。"
		}
	}, func(err error) { p.failed(a, err) })
}
func (p *clefPage) check(a *NativeApp) {
	if p.config == nil || a.Busy {
		return
	}
	revision := integer(p.config, "revision")
	a.Do(func(ctx context.Context) (func(), error) {
		result, err := a.Client.Request(ctx, "POST", "/platform/clef/check", Object{"expected_revision": revision})
		if err != nil {
			var api *APIError
			if errors.As(err, &api) && settingsClefData(api.Result.Data) {
				data := api.Result.Data
				safe := settingsSafeError(err)
				return func() { p.apply(a, data); p.failed(a, safe) }, nil
			}
			safe := settingsSafeError(err)
			if mgDenied(safe) {
				return nil, safe
			}
			return func() { p.failed(a, safe) }, nil
		}
		return func() {
			if p.apply(a, result.Data) {
				if len(p.blockers) > 0 {
					a.Notice = "网关检查未通过，Clef 调用被阻止。"
				} else {
					a.Notice = "网关安全检查通过。"
				}
			}
		}, nil
	})
}
func (p *clefPage) View(c *ui.Context, a *NativeApp) {
	if p.closed {
		nativeText(c, "Clef 配置不可访问，请重新打开。")
		return
	}
	nativeText(c, "配置签章存在性初筛；记录调用次数和返回的 token 用量。需要独立的 Workers AI 模型令牌与网关令牌。")
	ui.Row(c).Wrap().Gap(8).Children(func() {
		nativeButton(c, "刷新 Clef 配置").Disabled(a.Busy).OnClick(func() { p.load(a) })
		nativeButton(c, "管理服务凭据").OnClick(func() { a.Navigate("/platform/credentials") })
	})
	ui.Column(c.Key("clef-form")).Gap(8).Disabled(a.Busy).Children(func() {
		settingsDirtyField(c, "Cloudflare Account ID", &p.account, false, &p.dirty)
		settingsDirtyField(c, "AI Gateway ID", &p.gateway, false, &p.dirty)
		if settingsChoice(c, "Workers AI 模型凭据", &p.workerID, p.workerOptions) {
			p.dirty = true
		}
		if settingsChoice(c, "网关凭据", &p.gatewayID, p.gatewayOptions) {
			p.dirty = true
		}
		if ui.Checkbox(c, &p.enabled, "启用 Clef 初筛").Changed() {
			p.dirty = true
		}
		nativeText(c, "每个目标页一次调用，同时询问公章与签字存在性。每次检验默认开启，提交人可在预检中关闭。")
		nativePrimaryButton(c, "保存 Clef 配置").Disabled(!p.ready()).OnClick(func() { p.save(a) })
	})
	ui.Divider(c)
	nativeHeading(c, "网关安全检查")
	nativeText(c, "检查认证开启、日志与 Logpush 关闭、缓存 TTL 为 0、网关自动重试关闭且已设置限流。任一不满足或凭据缺失均阻止初筛调用。")
	nativeButton(c, "检查 Clef 网关").Disabled(a.Busy || p.config == nil).OnClick(func() { p.check(a) })
	for _, code := range p.blockers {
		message := settingsFailures[code]
		if message == "" {
			message = "Clef 配置未就绪，请重新读取。"
		}
		nativeText(c, "阻止调用："+message)
	}
	if p.config != nil {
		mgValue(c, "配置修订", fmt.Sprint(integer(p.config, "revision")))
		mgValue(c, "更新时间", str(p.config, "updated_at"))
		if check := object(p.config["gateway_check"]); check != nil {
			mgValue(c, "检查时间", str(check, "checked_at"))
			mgValue(c, "认证", "开启")
			mgValue(c, "日志与 Logpush", "关闭")
			mgValue(c, "缓存 TTL", "0")
			mgValue(c, "网关重试", "关闭")
			mgValue(c, "限流", fmt.Sprintf("%d 次 / %d 秒", integer(check, "rate_limit_requests"), integer(check, "rate_limit_seconds")))
		}
	}
}
