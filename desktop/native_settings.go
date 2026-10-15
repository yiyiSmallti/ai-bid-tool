package main

import (
	"context"
	"errors"
	"strings"

	"github.com/egoist/mygo/ui"
)

func settingsPage(route Route) NativePage {
	switch route.Path {
	case "/platform/credentials":
		return newCredentialPage(route)
	case "/platform/clef":
		return newClefPage(route)
	case "/platform/trust-anchors":
		return newTrustAnchorPage(route)
	case "/org/settings/models":
		return newModelSettingsPage(route)
	case "/org/confidential":
		return newConfidentialPage(route)
	}
	return nil
}

type settingsChoices struct {
	codes                    []string
	labelByCode, codeByLabel map[string]string
}

func settingsOptions(pairs ...string) settingsChoices {
	choices := settingsChoices{labelByCode: make(map[string]string, len(pairs)/2), codeByLabel: make(map[string]string, len(pairs)/2)}
	for i := 0; i+1 < len(pairs); i += 2 {
		choices.codes = append(choices.codes, pairs[i])
		choices.labelByCode[pairs[i]] = pairs[i+1]
		choices.codeByLabel[pairs[i+1]] = pairs[i]
	}
	return choices
}
func settingsChoice(c *ui.Context, label string, value *string, choices settingsChoices) bool {
	changed := false
	ui.Column(c.Key(label)).Gap(6).FillWidth().MaxWidth(760).AlignSelf(ui.Start).Children(func() {
		ui.Text(c, label).FontSize(12.5).TextColor(c.Theme().TextMuted)
		changed = nativeChoice(c, value, choices.codes, choices.labelByCode).Label(label).FillWidth().Changed()
	})
	return changed
}

func init() {
	for code, label := range map[string]string{"anthropic": "Anthropic", "openai": "OpenAI 兼容", "json_schema": "JSON Schema", "json_object": "JSON 对象"} {
		nativeLabels[code] = label
	}
}

var settingsFailures = map[string]string{
	"invalid_input":                      "填写内容不符合要求，请核对非密钥字段并重新输入敏感值。",
	"invalid_response":                   "返回数据的范围或安全边界不符合契约，请重新读取。",
	"revision_conflict":                  "修订已变化，请核对最新内容后主动保存；密钥需要重新输入。",
	"credential_name_conflict":           "凭据名称已存在，已移除的名称也不能复用。",
	"credential_purpose_conflict":        "该服务已有凭据，请先处理现有凭据。",
	"credential_removed":                 "凭据已移除，不能替换、启用或测试。",
	"credential_unreadable":              "凭据无法解密，请检查加密根或重新提交密钥。",
	"credential_backend_unavailable":     "凭据数据库暂不可用，请稍后重试。",
	"credential_audit_unavailable":       "审计保存失败，操作未完成。",
	"credential_probe_endpoint_rejected": "凭据端点不符合出站访问规则。",
	"credential_probe_auth_failed":       "认证失败。", "credential_probe_unsupported": "不支持认证探针。",
	"credential_probe_timeout": "认证超时。", "credential_probe_rate_limited": "测试次数超限。",
	"credential_probe_unavailable": "认证服务不可用。", "credential_probe_interrupted": "测试中断，结果未知。",
	"clef_gateway_unchecked": "尚未完成网关安全检查。", "clef_gateway_not_checked": "尚未完成网关安全检查。",
	"clef_credential_unavailable": "Clef 凭据不可用。", "clef_credential_changed": "Clef 凭据已变化，请保存配置并重新检查。",
	"clef_configuration_unavailable": "Clef 配置暂不可用。", "clef_unconfigured": "尚未配置 Clef。", "clef_disabled": "已停用 Clef。",
	"clef_gateway_check_failed": "网关安全检查未通过。", "clef_gateway_unsafe": "网关未满足安全设置。",
	"clef_credentials_missing": "缺少有效的模型令牌或网关令牌。", "clef_revision_conflict": "配置修订已变化，请刷新后重新核对。",
	"invalid_trust_anchor": "证书不是有效的 PEM/DER CA 证书，请核对原件。", "trust_store_unavailable": "信任库暂时无法访问。",
	"trust_anchor_capacity": "信任根保留数量已达到上限。", "trust_anchor_invalid": "无法解析 CA 证书。",
	"trust_anchor_not_ca": "此证书没有 CA 属性，不能作为信任根。", "trust_anchor_limit": "证书超过 64 KiB 上限。",
	"confidential_value_conflict": "当前值已变化；输入已清空，请核对最新版本后重新输入。",
	"confidential_field_archived": "字段已归档，不能填写。", "confidential_task_required": "需要明确的任务上下文。",
	"confidential_task_not_allowed": "共用值不能附带任务。", "confidential_field_key_conflict": "字段键名已存在。",
	"provider_key_required": "首次设置或更换供应商、端点需要输入新密钥。", "provider_unavailable": "模型服务不可用。", "provider_content_blocked": "模型服务的内容审核拦截了请求。",
	"provider_save_failed":      "保存结果无法核验，请重新读取配置。",
	"management_cursor_invalid": "分页已失效，请从第一页读取。", "management_cursor_expired": "分页已过期，请从第一页读取。",
	"forbidden": "当前身份无权执行此操作。", "human_required": "需要本人登录后操作。",
	"not_found": "记录不存在或不可访问。", "session_required": "会话已失效，请重新输入访问密钥。",
	"invalid_session":            "会话已失效，请重新输入访问密钥。",
	"settings_operation_unknown": "操作结果尚未确定，请主动重新读取并核对；不会自动重试。",
}

func settingsSafeError(err error) error {
	if err == nil {
		return nil
	}
	if errors.Is(err, context.Canceled) {
		return context.Canceled
	}
	var api *APIError
	if !errors.As(err, &api) {
		return &APIError{Status: 0, Code: "settings_operation_unknown", Message: settingsFailures["settings_operation_unknown"]}
	}
	code := api.Code
	if _, known := settingsFailures[code]; !known {
		code = "settings_operation_unknown"
	}
	message := settingsFailures[code]
	if api.Status == 401 {
		code = "session_required"
		message = settingsFailures[code]
	}
	if api.Status == 403 {
		code = "forbidden"
		message = settingsFailures[code]
	}
	if api.Status == 404 {
		code = "not_found"
		message = settingsFailures[code]
	}
	clean := &APIError{Status: api.Status, Code: code, Message: message}
	if probe := object(api.Result.Data["probe"]); settingsCredentialProbe(probe) {
		clean.Result = Result{Data: Object{"probe": probe}}
	}
	return clean
}
func settingsErrorText(err error) string {
	if err == nil {
		return ""
	}
	return settingsSafeError(err).Error()
}
func settingsRead(a *NativeApp, reads *managementReads, channel string, work func(context.Context) (Result, error), done func(Result), failed func(error)) {
	reads.run(a, channel, func(ctx context.Context) (func(), error) {
		result, err := work(ctx)
		if err != nil {
			return nil, settingsSafeError(err)
		}
		return func() {
			if done != nil {
				done(result)
			}
		}, nil
	}, func(err error) {
		a.Error = settingsErrorText(err)
		if failed != nil {
			failed(err)
		}
	})
}
func settingsWrite(a *NativeApp, work func(context.Context) (Result, error), done func(Result), failed func(error)) {
	a.Do(func(ctx context.Context) (func(), error) {
		result, err := work(ctx)
		err = settingsSafeError(err)
		if mgDenied(err) {
			return nil, err
		}
		return func() {
			if err != nil {
				a.Error = settingsErrorText(err)
				if failed != nil {
					failed(err)
				}
				return
			}
			if done != nil {
				done(result)
			}
		}, nil
	})
}
func settingsDirtyField(c *ui.Context, label string, value *string, multiline bool, dirty *bool) {
	if nativeField(c, label, value, multiline) {
		*dirty = true
	}
}
func settingsCode(err error) string {
	var api *APIError
	if errors.As(err, &api) {
		return api.Code
	}
	return ""
}
func settingsEndpoint(value string) string { return strings.TrimSuffix(strings.TrimSpace(value), "/") }
