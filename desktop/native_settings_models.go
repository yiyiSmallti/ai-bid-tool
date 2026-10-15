package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"strconv"
	"strings"

	"github.com/egoist/mygo/ui"
)

type modelReasoningEditor struct{ Name, Label, Options, Effort, Batch string }
type modelTestReceipt struct {
	Job, Config, Status, Provider, Model string
	Cost                                 map[string]int64
	Changed                              bool
}
type modelSettingsPage struct {
	managementState
	reads                                                                     managementReads
	settings, selectedRevision, testedRevision                                Object
	history                                                                   managementListing
	provider, model, endpoint, jsonMode, key, defaultReasoning, testReasoning string
	name, template, editSlot, testModel, expanded                             string
	editRevision                                                              any
	levels                                                                    []modelReasoningEditor
	testOptions, testModels                                                   settingsChoices
	editing, conflict                                                         bool
	preflight                                                                 Object
	preflightCost                                                             map[string]int64
	testReceipt                                                               *modelTestReceipt
	saveReceipt                                                               string
}

func newModelSettingsPage(route Route) *modelSettingsPage {
	return &modelSettingsPage{managementState: managementState{route: route, title: "模型与服务配置"}, provider: "openai", jsonMode: "json_schema"}
}
func (p *modelSettingsPage) Open(a *NativeApp) { p.load(a, nil); p.loadHistory(a, "") }
func (p *modelSettingsPage) Dirty() bool       { return p.editing && (p.dirty || p.key != "") }
func (p *modelSettingsPage) Close() {
	p.managementState.Close()
	p.reads.close()
	p.settings, p.selectedRevision, p.testedRevision, p.preflight = nil, nil, nil, nil
	p.history = managementListing{}
	p.key, p.model, p.endpoint, p.defaultReasoning, p.saveReceipt = "", "", "", "", ""
	p.name, p.template, p.editSlot, p.testModel, p.expanded, p.editRevision = "", "", "", "", "", nil
	p.levels = nil
	p.testReceipt = nil
	p.preflightCost = nil
	p.testOptions, p.testModels = settingsChoices{}, settingsChoices{}
	p.editing = false
}
func (p *modelSettingsPage) failed(a *NativeApp, err error) {
	p.key = ""
	if mgDenied(err) {
		p.Close()
	}
	a.Error = settingsErrorText(err)
}
func (p *modelSettingsPage) current() Object  { return object(p.settings["current"]) }
func (p *modelSettingsPage) models() []Object { return objects(p.settings["models"]) }
func (p *modelSettingsPage) saved(slot string) Object {
	for _, model := range p.models() {
		if str(model, "slot") == slot {
			return model
		}
	}
	return nil
}
func modelTitle(model Object) string {
	if name := str(model, "name"); name != "" {
		return name
	}
	return str(model, "model")
}

// Test reasoning levels belong to the chosen model, not to the default one.
func (p *modelSettingsPage) refreshTestOptions() {
	pairs := []string{"", "使用默认等级"}
	reasoning := p.settings["reasoning"]
	if chosen := p.saved(p.testModel); chosen != nil {
		reasoning = chosen["reasoning"]
	}
	for _, level := range objects(reasoning) {
		label := str(level, "label")
		if label == "" {
			label = str(level, "name")
		}
		pairs = append(pairs, str(level, "name"), label)
	}
	p.testOptions = settingsOptions(pairs...)
	if _, ok := p.testOptions.labelByCode[p.testReasoning]; !ok {
		p.testReasoning = ""
	}
}
func (p *modelSettingsPage) load(a *NativeApp, after func(bool)) {
	settingsRead(a, &p.reads, "provider-settings", func(ctx context.Context) (Result, error) {
		return a.Client.Request(ctx, "GET", "/management/providers", nil)
	}, func(r Result) {
		if !settingsProviderSettings(r.Data) {
			a.Error = settingsFailures["invalid_response"]
			if after != nil {
				after(false)
			}
			return
		}
		if p.editing && mgAllowed(p.settings, "configure") && !mgAllowed(r.Data, "configure") {
			p.Close()
			a.Error = "维护权限已改变，未保存内容已清除。请重新打开配置页面。"
			if after != nil {
				after(false)
			}
			return
		}
		p.settings = r.Data
		p.preflight = nil
		p.preflightCost = nil
		pairs := []string{"", "默认模型"}
		for _, model := range p.models() {
			pairs = append(pairs, str(model, "slot"), modelTitle(model)+" · "+str(model, "model"))
		}
		p.testModels = settingsOptions(pairs...)
		if p.saved(p.testModel) == nil {
			p.testModel = ""
		}
		p.refreshTestOptions()
		if after != nil {
			after(true)
		}
	}, func(err error) {
		p.failed(a, err)
		if after != nil {
			after(false)
		}
	})
}
func (p *modelSettingsPage) loadHistory(a *NativeApp, cursor string) {
	p.history.Items, p.history.Rows, p.history.Meta = nil, nil, nil
	body := Object{"cursor": mgNull(cursor), "limit": 25}
	settingsRead(a, &p.reads, "provider-history", func(ctx context.Context) (Result, error) {
		return a.Client.Request(ctx, "POST", "/management/providers/history/query", body)
	}, func(r Result) {
		if !settingsPageMeta(r.Data, len(r.Items), 25) {
			a.Error = settingsFailures["invalid_response"]
			return
		}
		for _, row := range r.Items {
			if !settingsProviderMetadata(row, "") {
				a.Error = settingsFailures["invalid_response"]
				return
			}
		}
		p.history.install(r, cursor, func(row Object) []string {
			return []string{fmt.Sprint(integer(row, "revision")), nativeDisplay(str(row, "name")), nativeLabel(str(row, "provider")), str(row, "model"), nativeDisplay(str(row, "revised_at")), nativeShortID(str(row, "id"))}
		})
	}, func(err error) { p.failed(a, err) })
}
func (p *modelSettingsPage) revision(a *NativeApp, id string, tested bool) {
	channel := "provider-revision"
	if tested {
		channel = "provider-tested-revision"
	}
	settingsRead(a, &p.reads, channel, func(ctx context.Context) (Result, error) {
		return a.Client.Request(ctx, "GET", "/management/providers/revisions/"+url.PathEscape(id), nil)
	}, func(r Result) {
		if !settingsProviderMetadata(r.Data, id) {
			a.Error = settingsFailures["invalid_response"]
			return
		}
		if tested {
			p.testedRevision = r.Data
		} else {
			p.selectedRevision = r.Data
		}
	}, func(err error) { p.failed(a, err) })
}

// startEdit opens the form for one saved model, or for a new one when model is nil.
func (p *modelSettingsPage) startEdit(model Object) {
	if !mgAllowed(p.settings, "configure") || model == nil && len(p.models()) >= 20 {
		return
	}
	p.key, p.template, p.editSlot, p.editRevision, p.name = "", "", "", nil, ""
	p.provider, p.model, p.endpoint, p.jsonMode, p.defaultReasoning = "openai", "", "", "json_schema", ""
	p.levels = nil
	if model != nil {
		configuration := object(model["configuration"])
		p.editSlot, p.editRevision, p.name = str(model, "slot"), model["revision"], str(model, "name")
		p.provider, p.model, p.endpoint, p.jsonMode, p.defaultReasoning = str(configuration, "provider"), str(configuration, "model"), str(configuration, "base_url"), str(configuration, "json_mode"), str(configuration, "default_reasoning")
		for _, level := range objects(configuration["reasoning"]) {
			p.levels = append(p.levels, modelReasoningEditor{Name: str(level, "name"), Label: str(level, "label"), Options: mgJSON(level["request_options"]), Effort: str(level, "effort"), Batch: fmt.Sprint(integer(level, "batch_chars"))})
		}
	}
	p.editing = true
	p.conflict = false
	p.dirty = false
	p.preflight = nil
	p.preflightCost = nil
}
func (p *modelSettingsPage) applyTemplate(key string) {
	template, ok := findModelTemplate(key)
	if !ok {
		return
	}
	p.key = ""
	if p.name == "" || p.editSlot == "" {
		p.name = template.Name
	}
	p.provider, p.endpoint, p.model, p.jsonMode, p.defaultReasoning = template.Provider, template.Endpoint, template.Model, template.JSONMode, template.Default
	p.levels = append([]modelReasoningEditor(nil), template.Levels...)
	p.dirty = true
}
func (p *modelSettingsPage) cancel(a *NativeApp) {
	cancel := func() {
		p.key = ""
		p.editing, p.dirty, p.conflict = false, false, false
		p.levels = nil
		p.model, p.endpoint, p.defaultReasoning, p.name, p.template, p.editSlot, p.editRevision = "", "", "", "", "", "", nil
		p.preflight = nil
		p.preflightCost = nil
	}
	if p.Dirty() {
		a.Confirm("放弃模型配置编辑", "放弃未保存的非密钥字段和新密钥？", cancel)
	} else {
		cancel()
	}
}

// The server reuses a saved key from any model with the same provider and endpoint.
func (p *modelSettingsPage) reusable() bool {
	for _, model := range p.models() {
		if p.provider == str(model, "provider") && settingsEndpoint(p.endpoint) == str(object(model["configuration"]), "base_url") {
			return true
		}
	}
	return false
}
func (p *modelSettingsPage) input() (Object, error) {
	if strings.TrimSpace(p.model) == "" || len(p.model) > 100 || len([]rune(strings.TrimSpace(p.name))) > 60 || !settingsHTTPS(mgNull(p.endpoint)) || len(p.levels) > 8 {
		return nil, fmt.Errorf("请填写模型 ID（不超过 100 字符）、显示名称（不超过 60 字）与有效的 HTTPS Base URL。")
	}
	levels := make([]Object, 0, len(p.levels))
	seen := make(map[string]bool, len(p.levels))
	for _, level := range p.levels {
		batch, err := strconv.Atoi(level.Batch)
		var options Object
		optionErr := json.Unmarshal([]byte(level.Options), &options)
		if !settingsLevelNamePattern.MatchString(level.Name) || seen[level.Name] || len([]rune(level.Label)) > 60 || level.Effort != "" && !settingsEffortPattern.MatchString(level.Effort) || err != nil || batch < 1000 || batch > 200000 || optionErr != nil || options == nil {
			return nil, fmt.Errorf("推理等级名称需唯一；请求选项需 JSON 对象，每批字符数为 1000–200000。")
		}
		seen[level.Name] = true
		levels = append(levels, Object{"name": level.Name, "label": mgNull(level.Label), "request_options": options, "effort": mgNull(level.Effort), "batch_chars": batch})
	}
	if len(levels) > 0 && !seen[p.defaultReasoning] || len(levels) == 0 && p.defaultReasoning != "" {
		return nil, fmt.Errorf("请选择已配置的默认推理等级；未配置等级时应留空。")
	}
	body := Object{"capability": "llm_extract", "name": mgNull(strings.TrimSpace(p.name)), "provider": p.provider, "model": strings.TrimSpace(p.model), "base_url": mgNull(settingsEndpoint(p.endpoint)), "json_mode": p.jsonMode, "reasoning": levels, "default_reasoning": mgNull(p.defaultReasoning)}
	if p.editSlot == "" {
		body["new"] = true
	} else {
		body["slot"], body["expected_revision"] = p.editSlot, p.editRevision
	}
	return body, nil
}
func (p *modelSettingsPage) save(a *NativeApp) {
	if !p.editing || !mgAllowed(p.settings, "configure") || a.Busy {
		return
	}
	body, err := p.input()
	if err != nil {
		p.key = ""
		a.Error = err.Error()
		return
	}
	if p.key == "" && !p.reusable() {
		a.Error = settingsFailures["provider_key_required"]
		return
	}
	if p.key != "" {
		body["api_key"] = p.key
	}
	p.key = ""
	settingsWrite(a, func(ctx context.Context) (Result, error) {
		defer delete(body, "api_key")
		return a.Client.ProviderWrite(ctx, body)
	}, func(r Result) {
		id, revision := str(r.Data, "id"), integer(r.Data, "revision")
		if !settingsUUID(id) || revision < 1 {
			p.failed(a, settingsInvalid())
			return
		}
		p.load(a, func(ok bool) {
			saved := false
			for _, model := range p.models() {
				saved = saved || str(model, "id") == id && integer(model, "revision") == revision
			}
			if !ok || !saved {
				if !p.closed {
					p.conflict = true
					a.Error = settingsFailures["provider_save_failed"]
				}
				return
			}
			p.key = ""
			p.editing, p.dirty, p.conflict = false, false, false
			p.levels = nil
			p.model, p.endpoint, p.defaultReasoning, p.name, p.template, p.editSlot, p.editRevision = "", "", "", "", "", "", nil
			p.saveReceipt = id
			p.history.Previous = nil
			p.loadHistory(a, "")
			a.Notice = "模型已保存，密钥不会回显。"
		})
	}, func(err error) {
		p.failed(a, err)
		if !p.closed && (settingsCode(err) == "revision_conflict" || mgConflict(err) || settingsCode(err) == "provider_save_failed" || settingsCode(err) == "invalid_response") {
			p.conflict = true
			p.load(a, func(bool) {
				if !p.closed {
					a.Error = "保存结果尚未确定或修订已变化；非密钥草稿已保留，密钥已清空。核对当前服务器修订后再主动保存，不会自动重试。"
				}
			})
		}
	})
}
func modelTestResult(result Result, displayed Object) (*modelTestReceipt, error) {
	data := result.Data
	if !settingsUUID(str(data, "job_id")) || data["provider_config_id"] != nil && !settingsUUID(str(data, "provider_config_id")) || !containsString([]string{"succeeded", "failed", "cancelled", "queued", "running"}, str(data, "status")) {
		return nil, settingsInvalid()
	}
	usage := object(data["usage"])
	model, provider := "", ""
	if usage["model"] != nil {
		if !settingsString(usage["model"], 1, 100) {
			return nil, settingsInvalid()
		}
		model = str(usage, "model")
	}
	if usage["provider"] != nil {
		if !containsString([]string{"anthropic", "openai"}, str(usage, "provider")) {
			return nil, settingsInvalid()
		}
		provider = str(usage, "provider")
	}
	id := str(data, "provider_config_id")
	return &modelTestReceipt{Job: str(data, "job_id"), Config: id, Status: str(data, "status"), Model: model, Provider: provider, Cost: result.Cost, Changed: id != str(displayed, "id") || model != "" && model != str(displayed, "model") || provider != "" && provider != str(displayed, "provider")}, nil
}
func (p *modelSettingsPage) test(a *NativeApp, dry bool) {
	if a.Busy || p.editing || !mgAllowed(p.settings, "test") || !dry && p.preflight == nil {
		return
	}
	reasoning, chosen := mgNull(p.testReasoning), mgNull(p.testModel)
	displayed := p.current()
	if model := p.saved(p.testModel); model != nil {
		displayed = model
	}
	p.key = ""
	if dry {
		settingsWrite(a, func(ctx context.Context) (Result, error) {
			return a.Client.Request(ctx, "POST", "/providers/test", Object{"capability": "llm_extract", "model": chosen, "reasoning": reasoning, "dry_run": true})
		}, func(r Result) {
			if r.Data["dry_run"] != true || object(r.Data["preflight"]) == nil {
				a.Error = settingsFailures["invalid_response"]
				return
			}
			preflight := object(r.Data["preflight"])
			blocker := str(preflight, "admission_blocker")
			if blocker != "" && len(blocker) > 100 {
				a.Error = settingsFailures["invalid_response"]
				return
			}
			p.preflight = Object{"blocked": preflight["admission_blocker"] != nil}
			p.preflightCost = r.Cost
			p.testReceipt = nil
			p.testedRevision = nil
		}, func(err error) { p.preflight = nil; p.preflightCost = nil; p.failed(a, err) })
		return
	}
	p.preflight, p.preflightCost = nil, nil
	a.Do(func(ctx context.Context) (func(), error) {
		result, err := a.Client.Request(ctx, "POST", "/providers/test", Object{"capability": "llm_extract", "model": chosen, "reasoning": reasoning, "dry_run": false})
		if err != nil {
			var api *APIError
			if errors.As(err, &api) && api.Result.Command == "provider test" {
				result = api.Result
			} else {
				safe := settingsSafeError(err)
				if mgDenied(safe) {
					return nil, safe
				}
				return func() {
					p.failed(a, safe)
					a.Error = "连接测试结果无法核验，可能已发出调用。不会自动重试；请核对用量或作业后再明确开始新的测试。"
				}, nil
			}
		}
		receipt, validation := modelTestResult(result, displayed)
		if validation != nil {
			return func() {
				p.failed(a, validation)
				a.Error = "连接测试结果无法核验，可能已发出调用。不会自动重试。"
			}, nil
		}
		failed := err != nil
		return func() {
			p.testReceipt = receipt
			p.testedRevision = nil
			if receipt.Config != "" {
				p.revision(a, receipt.Config, true)
			}
			if failed {
				a.Error = "连接测试未成功，实际用量与配置回执已保留。请核对后重新预检，不会自动重试。"
			}
		}, nil
	})
}
func modelMetadataView(c *ui.Context, value Object) {
	ui.Column(c).FillWidth().Gap(14).Children(func() {
		configuration := object(value["configuration"])
		endpoint := str(configuration, "base_url")
		if endpoint == "" {
			endpoint = "使用供应商默认端点"
		}
		nativePairs(c,
			[]string{"配置 ID", "修订", "供应商与模型", "HTTPS Base URL", "结构化输出模式", "更新时间", "修订时间"},
			[]string{str(value, "id"), fmt.Sprint(integer(value, "revision")), str(value, "provider") + " · " + str(value, "model"), endpoint, str(configuration, "json_mode"), str(value, "updated_at"), str(value, "revised_at")})
		nativeHint(c, "密钥已配置，仅写入，不回显。")
		levels := objects(configuration["reasoning"])
		if len(levels) == 0 {
			return
		}
		nativeHeading(c, "推理等级")
		palette := nativePalette(c)
		ui.Column(c.Key("reasoning-metadata")).FillWidth().Gap(0).Children(func() {
			ui.Row(c).FillWidth().Gap(16).Padding(8, 12).Background(palette.NeutralSoft).Radius(6).Children(func() {
				ui.Text(c, "等级").Width(90).Shrink(0).FontSize(12).TextColor(palette.Muted)
				ui.Text(c, "显示名称").Grow(1).MinWidth(0).FontSize(12).TextColor(palette.Muted)
				ui.Text(c, "每批字符数").Width(110).Shrink(0).FontSize(12).TextColor(palette.Muted)
				ui.Text(c, "推理强度").Width(90).Shrink(0).FontSize(12).TextColor(palette.Muted)
			})
			for i, level := range levels {
				ui.Column(c.Key(fmt.Sprintf("reasoning-metadata-%d", i))).FillWidth().Gap(7).Padding(12).BorderWidth(0, 0, 1, 0).BorderColor(palette.Border).Children(func() {
					ui.Row(c).FillWidth().AlignItems(ui.Start).Gap(16).Children(func() {
						ui.Text(c, str(level, "name")).Width(90).Shrink(0).Font("monospace").FontSize(13)
						ui.Text(c, nativeDisplay(str(level, "label"))).Grow(1).MinWidth(0).FontSize(13)
						ui.Text(c, fmt.Sprint(integer(level, "batch_chars"))).Width(110).Shrink(0).FontSize(13)
						ui.Text(c, nativeDisplay(str(level, "effort"))).Width(90).Shrink(0).FontSize(13)
					})
					ui.Row(c).FillWidth().AlignItems(ui.Start).Gap(16).Children(func() {
						ui.Text(c, "请求选项").Width(90).Shrink(0).FontSize(12).TextColor(palette.Muted)
						ui.Text(c, mgJSON(level["request_options"])).Grow(1).MinWidth(0).Font("monospace").FontSize(12).LineHeight(1.45).TextColor(palette.Muted)
					})
				})
			}
		})
	})
}
func (p *modelSettingsPage) setDefault(a *NativeApp, model Object) {
	if a.Busy || !mgAllowed(p.settings, "configure") {
		return
	}
	slot := str(model, "slot")
	settingsWrite(a, func(ctx context.Context) (Result, error) {
		return a.Client.Request(ctx, "POST", "/providers/models/"+url.PathEscape(slot)+"/default", nil)
	}, func(Result) {
		p.load(a, nil)
		a.Notice = "已将「" + modelTitle(model) + "」设为默认模型。"
	}, func(error) { p.load(a, nil) })
}
func (p *modelSettingsPage) remove(a *NativeApp, model Object) {
	if a.Busy || !mgAllowed(p.settings, "configure") {
		return
	}
	slot := str(model, "slot")
	a.Confirm("删除模型", "删除「"+modelTitle(model)+"」？已排队的作业仍按原配置运行，之后不能再选择该模型。", func() {
		settingsWrite(a, func(ctx context.Context) (Result, error) {
			return a.Client.Request(ctx, "DELETE", "/providers/models/"+url.PathEscape(slot), nil)
		}, func(Result) {
			if p.testModel == slot {
				p.testModel = ""
			}
			p.load(a, nil)
			p.loadHistory(a, "")
			a.Notice = "模型已删除。"
		}, func(error) { p.load(a, nil) })
	})
}
func (p *modelSettingsPage) modelRow(c *ui.Context, a *NativeApp, model Object) {
	palette := nativePalette(c)
	slot := str(model, "slot")
	isDefault := slot == str(p.settings, "default_slot")
	configuration := object(model["configuration"])
	endpoint := str(configuration, "base_url")
	if endpoint == "" {
		endpoint = "供应商默认端点"
	}
	ui.Column(c.Key("model-"+slot)).FillWidth().Gap(10).Padding(14, 16).Radius(10).Border(1, palette.Border).Background(palette.Card).Children(func() {
		ui.Row(c).FillWidth().Wrap().Gap(12).AlignItems(ui.Center).Children(func() {
			ui.Icon(c, nativeIcon("cpu")).FontSize(18).TextColor(palette.Muted)
			ui.Column(c).Grow(1).MinWidth(220).Gap(3).Children(func() {
				ui.Row(c).Gap(8).AlignItems(ui.Center).Children(func() {
					ui.Text(c, modelTitle(model)).FontSize(14.5).FontWeight(600).SingleLine()
					if isDefault {
						nativeBadge(c, "默认", toneAccent)
					}
				})
				ui.Text(c, nativeLabel(str(model, "provider"))+" · "+str(model, "model")+" · 密钥 ••••").FontSize(12.5).TextColor(palette.Muted).SingleLine()
				ui.Text(c, endpoint).FontSize(12).Font("monospace").TextColor(palette.Subtle).SingleLine()
			})
			ui.Row(c).Gap(6).AlignItems(ui.Center).Children(func() {
				configure := !a.Busy && !p.editing && mgAllowed(p.settings, "configure")
				if !isDefault {
					nativeGhostButton(c, "star", "设为默认", !configure).OnClick(func() { p.setDefault(a, model) })
				}
				nativeGhostButton(c, "pencil", "编辑", !configure).OnClick(func() { p.startEdit(model) })
				detail := "详情"
				if p.expanded == slot {
					detail = "收起"
				}
				nativeGhostButton(c, "chevron-down", detail, false).OnClick(func() {
					if p.expanded == slot {
						p.expanded = ""
					} else {
						p.expanded = slot
					}
				})
				nativeGhostButton(c, "trash-2", "删除", !configure).OnClick(func() { p.remove(a, model) })
			})
		})
		if p.expanded == slot {
			modelMetadataView(c.Key("model-detail-"+slot), model)
		}
	})
}
func (p *modelSettingsPage) View(c *ui.Context, a *NativeApp) {
	if p.closed {
		nativeText(c, "配置不可访问，未保存内容已清除。请重新打开。")
		return
	}
	if p.editing && !p.conflict {
		p.viewForm(c, a)
		return
	}
	nativeText(c, "可保存多个模型，各自使用独立的供应商、端点与密钥；抽取和起草时可以选择本次使用的模型，未选择时使用默认模型。")
	ui.Row(c).Wrap().Gap(8).Children(func() {
		nativeIconButton(c, "plus", "添加模型", true).Disabled(a.Busy || p.editing || !mgAllowed(p.settings, "configure") || len(p.models()) >= 20).OnClick(func() { p.startEdit(nil) })
		nativeGhostButton(c, "refresh-cw", "重新读取", a.Busy).OnClick(func() { p.load(a, nil) })
	})
	if p.settings == nil {
		nativeText(c, "正在读取模型元数据。")
	} else if len(p.models()) == 0 {
		nativeEmpty(c, "cpu", "尚未添加模型。点击「添加模型」，选择厂商模板后填入密钥即可。", nil)
	} else {
		ui.Column(c.Key("model-list")).FillWidth().Gap(10).Children(func() {
			for _, model := range p.models() {
				p.modelRow(c, a, model)
			}
		})
	}
	if p.saveReceipt != "" {
		mgValue(c, "最近保存回执", p.saveReceipt)
	}
	if p.editing {
		p.viewForm(c, a)
	}
	ui.Divider(c)
	nativeHeading(c, "连接测试")
	if settingsChoice(c, "测试模型", &p.testModel, p.testModels) {
		p.refreshTestOptions()
		p.preflight, p.preflightCost = nil, nil
	}
	if settingsChoice(c, "测试推理等级", &p.testReasoning, p.testOptions) {
		p.preflight, p.preflightCost = nil, nil
	}
	nativeButton(c, "预检连接测试").Disabled(a.Busy || p.editing || !mgAllowed(p.settings, "test")).OnClick(func() { p.test(a, true) })
	if p.preflight != nil {
		mgValue(c, "预计调用", fmt.Sprintf("%d 次 · %d tokens", p.preflightCost["calls"], p.preflightCost["llm_tokens"]))
		if mgBool(p.preflight["blocked"]) {
			nativeText(c, "本次预检存在阻塞，无法开始测试。")
		}
		nativePrimaryButton(c, "明确开始连接测试").Disabled(a.Busy || mgBool(p.preflight["blocked"])).OnClick(func() { p.test(a, false) })
	}
	if receipt := p.testReceipt; receipt != nil {
		mgValue(c, "测试作业", receipt.Job)
		mgValue(c, "实际配置修订 ID", receipt.Config)
		mgValue(c, "作业状态", nativeLabel(receipt.Status))
		mgValue(c, "实际供应商与模型", receipt.Provider+" · "+receipt.Model)
		mgValue(c, "实际调用与用量", fmt.Sprintf("%d 次 · %d tokens", receipt.Cost["calls"], receipt.Cost["llm_tokens"]))
		if receipt.Changed {
			nativeText(c, "测试实际使用的配置与开始时显示的配置不同，请核对精确配置回执。")
		}
		if p.testedRevision != nil {
			modelMetadataView(c.Key("tested-provider"), p.testedRevision)
		}
	}
	ui.Divider(c)
	nativeHeading(c, "配置修订历史")
	nativeTable(c, []string{"修订", "名称", "供应商", "模型", "修订时间", "配置 ID"}, p.history.Rows, func(i int) { p.revision(a, str(p.history.Items[i], "id"), false) })
	p.history.controls(c, a, "模型配置历史", func(cursor string) { p.loadHistory(a, cursor) })
	if p.selectedRevision != nil {
		nativeHeading(c, "精确历史配置（只读）")
		modelMetadataView(c.Key("historical-provider"), p.selectedRevision)
		nativeButton(c, "关闭历史配置").OnClick(func() { p.selectedRevision = nil })
	}
}
func (p *modelSettingsPage) viewForm(c *ui.Context, a *NativeApp) {
	if p.editSlot == "" {
		nativeHeading(c, "添加模型")
	} else {
		nativeHeading(c, "编辑模型："+modelTitle(p.saved(p.editSlot)))
	}
	if p.conflict {
		nativeText(c, "请对照当前服务器修订核对保留的非密钥草稿；新密钥需要重新输入。")
	}
	ui.Column(c.Key("model-config-form")).FillWidth().MaxWidth(860).Height(520).Gap(10).Disabled(a.Busy).Children(func() {
		ui.Scroll(c.Key("model-fields")).Grow(1).FillWidth().Padding(0, 12, 12, 0).Gap(14).Children(func() {
			if settingsChoice(c, "厂商模板（自动填写端点、模型与推理等级，只需再填密钥）", &p.template, modelTemplateOptions()) {
				p.applyTemplate(p.template)
			}
			if template, ok := findModelTemplate(p.template); ok && template.Note != "" {
				nativeHint(c, template.Note)
			}
			settingsDirtyField(c, "显示名称（可选，如「智谱 快速」）", &p.name, false, &p.dirty)
			if mgChoice(c, "供应商协议", &p.provider, []string{"openai", "anthropic"}) {
				p.key = ""
				p.dirty = true
			}
			settingsDirtyField(c, "模型 ID", &p.model, false, &p.dirty)
			if nativeField(c, "HTTPS Base URL（留空使用默认端点；暂不支持 http 本地服务）", &p.endpoint, false) {
				p.key = ""
				p.dirty = true
			}
			if mgChoice(c, "结构化输出模式", &p.jsonMode, []string{"json_schema", "json_object"}) {
				p.dirty = true
			}
			keyHint := "API 密钥（只写，不回显）"
			if p.reusable() {
				keyHint = "API 密钥（可留空：将复用已保存的同供应商、同端点密钥）"
			}
			ui.Text(c, keyHint).FontSize(12.5).TextColor(c.Theme().TextMuted)
			if ui.TextInput(c, &p.key).Password().Label("模型 API 密钥（只写）").Height(36).FillWidth().MaxWidth(760).Radius(8).Background(nativePalette(c).Card).Changed() {
				p.dirty = true
			}
			for i := range p.levels {
				index := i
				level := &p.levels[index]
				ui.Column(c.Key(fmt.Sprintf("reasoning-%d", i))).Gap(6).Padding(10).Children(func() {
					nativeHeading(c, fmt.Sprintf("推理等级 %d", index+1))
					settingsDirtyField(c, "等级名称", &level.Name, false, &p.dirty)
					settingsDirtyField(c, "显示名称（可选）", &level.Label, false, &p.dirty)
					settingsDirtyField(c, "请求选项（JSON 对象）", &level.Options, true, &p.dirty)
					settingsDirtyField(c, "推理强度（可选）", &level.Effort, false, &p.dirty)
					settingsDirtyField(c, "每批字符数", &level.Batch, false, &p.dirty)
					nativeDangerButton(c, "移除此推理等级").OnClick(func() {
						removed := p.levels[index].Name
						p.levels = append(p.levels[:index], p.levels[index+1:]...)
						if p.defaultReasoning == removed {
							p.defaultReasoning = ""
						}
						p.dirty = true
					})
				})
			}
			nativeButton(c, "添加推理等级").Disabled(len(p.levels) >= 8).OnClick(func() {
				p.levels = append(p.levels, modelReasoningEditor{Options: "{}", Batch: "4000"})
				p.dirty = true
			})
			settingsDirtyField(c, "默认推理等级名称（未配置等级时留空）", &p.defaultReasoning, false, &p.dirty)
		})
		nativeActions(c).Children(func() {
			nativePrimaryButton(c, "保存模型配置").OnClick(func() { p.save(a) })
			nativeButton(c, "取消模型编辑").OnClick(func() { p.cancel(a) })
		})
	})
}
